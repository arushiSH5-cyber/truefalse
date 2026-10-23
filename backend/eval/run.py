"""Compare the full pipeline, a single LLM judge, and fact-check lookup on labeled claims.

Usage (from backend/):
  uv run python -m eval.run --dataset builtin --dry-run              # harness check, no API calls
  uv run python -m eval.run --dataset builtin --dataset own --limit 50
  uv run python -m eval.run --dataset xfact:~/x-fact/data/x-fact/test.all.tsv --limit 300 \
      --systems full,single_judge,lookup_only --write-language-accuracy
  uv run python -m eval.run --report-only --out eval/results/my-run

Results go to `--out` (default eval/results/<timestamp>): `items.jsonl`, `predictions.jsonl`
(appended as it runs, so an interrupted run resumes where it stopped), `report.json` and
`report.md`. A rough cost estimate is printed first and the run asks before spending money
(skip the question with --yes).
"""

import argparse
import asyncio
import json
import random
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from app.core.models import Verdict
from app.language.accuracy import ACCURACY_PATH, MIN_EXAMPLES, LanguageStats
from eval.datasets import EvalItem, dedupe, load, stratified_sample
from eval.metrics import Scores, grouped, score
from eval.systems import Prediction, System

RESULTS_DIR = Path(__file__).with_name("results")
SYSTEMS = ("full", "single_judge", "lookup_only")
# Rough cost per item with the default models (Opus 5.5 main model + Opus/Sonnet/Haiku judges).
# The real cost per item is printed as the run goes.
EST_COST_USD = {"full": 0.30, "single_judge": 0.01, "lookup_only": 0.001}


class DryRunSystem:
    """No API calls: a seeded random verdict, to check datasets, resume and reports."""

    def __init__(self, name: str) -> None:
        self.name = name

    async def predict(self, item: EvalItem) -> Prediction:
        rng = random.Random(f"{self.name}:{item.id}")
        return Prediction(system=self.name, item_id=item.id, verdict=rng.choice(list(Verdict)))


def build_systems(names: Sequence[str], dry_run: bool) -> list[System]:
    if dry_run:
        return [DryRunSystem(n) for n in names]
    from app.core.config import get_settings
    from app.core.container import build_deps, build_http_client
    from app.core.llm import AnthropicLLM
    from eval.systems import FullSystem, LookupOnlySystem, SingleJudgeSystem

    settings = get_settings()
    http = build_http_client(settings)
    deps = build_deps(settings, http)
    prices = (settings.llm_input_price_per_mtok, settings.llm_output_price_per_mtok)
    out: list[System] = []
    for name in names:
        if name == "full":
            out.append(FullSystem(deps, prices))
        elif name == "single_judge":
            out.append(SingleJudgeSystem(deps.llm, prices))
        elif name == "lookup_only":
            llm = deps.llm
            fast = llm.with_model(settings.fast_model) if isinstance(llm, AnthropicLLM) else llm
            lookup = LookupOnlySystem(deps.sources, deps.index, fast, prices)
            if not lookup.available:
                print(
                    "lookup_only: no GOOGLE_FACTCHECK_API_KEY and no index, so it will "
                    "answer Unverified for everything.",
                    file=sys.stderr,
                )
            out.append(lookup)
    return out


def read_predictions(path: Path) -> dict[tuple[str, str], Prediction]:
    if not path.exists():
        return {}
    preds = (Prediction.model_validate_json(x) for x in path.read_text().splitlines() if x)
    return {(p.system, p.item_id): p for p in preds}


async def run_all(
    systems: Sequence[System],
    items: Sequence[EvalItem],
    out: Path,
    concurrency: int,
) -> dict[tuple[str, str], Prediction]:
    path = out / "predictions.jsonl"
    done = read_predictions(path)
    todo = [(s, i) for s in systems for i in items if (s.name, i.id) not in done]
    if len(done):
        print(f"Resuming: {len(done)} predictions already saved, {len(todo)} to go.")
    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    spent = 0.0
    finished = 0

    async def one(system: System, item: EvalItem) -> None:
        nonlocal spent, finished
        async with sem:
            try:
                pred = await system.predict(item)
            except Exception as exc:  # a crash is an Unverified answer, and is reported
                pred = Prediction(
                    system=system.name,
                    item_id=item.id,
                    verdict=Verdict.UNVERIFIED,
                    error=f"{type(exc).__name__}: {exc}"[:300],
                )
        async with lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(pred.model_dump_json() + "\n")
            done[(pred.system, pred.item_id)] = pred
            spent += pred.cost_usd
            finished += 1
            if finished % max(1, len(todo) // 10) == 0 or finished == len(todo):
                print(f"  {finished}/{len(todo)} done, ${spent:.2f} spent", flush=True)

    await asyncio.gather(*(one(s, i) for s, i in todo))
    return done


class SystemReport(BaseModel):
    overall: Scores
    by_dataset: dict[str, Scores]
    by_language: dict[str, Scores]


class Report(BaseModel):
    n_items: int
    systems: dict[str, SystemReport]


def build_report(
    items: Sequence[EvalItem], preds: dict[tuple[str, str], Prediction], systems: Sequence[str]
) -> Report:
    out: dict[str, SystemReport] = {}
    for name in systems:
        pairs = [(i, preds[(name, i.id)]) for i in items if (name, i.id) in preds]
        out[name] = SystemReport(
            overall=score(pairs),
            by_dataset={k: score(v) for k, v in grouped(pairs, "dataset").items()},
            by_language={k: score(v) for k, v in grouped(pairs, "base_language").items()},
        )
    return Report(n_items=len(items), systems=out)


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{x:.0%}"


def _row(name: str, s: Scores) -> str:
    return (
        f"| {name} | {s.n} | {_pct(s.accuracy)} | {s.macro_f1:.2f} | {_pct(s.false_alarm_rate)} "
        f"| {_pct(s.unverified_rate)} | {s.errors} | ${s.mean_cost_usd:.4f} |"
    )


HEADER = (
    "| System | n | Accuracy | Macro-F1 | False alarms | Unverified | Errors | Cost/item |\n"
    "|---|---|---|---|---|---|---|---|"
)


def markdown(report: Report) -> str:
    systems = report.systems
    lines = ["# TrueFalse evaluation", "", f"Items: {report.n_items}", "", "## Overall", ""]
    lines.append(HEADER)
    lines += [_row(name, r.overall) for name, r in systems.items()]
    for d in sorted({d for r in systems.values() for d in r.by_dataset}):
        lines += ["", f"## {d}", "", HEADER]
        lines += [_row(name, r.by_dataset[d]) for name, r in systems.items() if d in r.by_dataset]
    lines += ["", "## Macro-F1 by language", "", "| Language | " + " | ".join(systems) + " |"]
    lines.append("|---" * (len(systems) + 1) + "|")
    for lang in sorted({lang for r in systems.values() for lang in r.by_language}):
        cells = []
        for r in systems.values():
            s = r.by_language.get(lang)
            cells.append(f"{s.macro_f1:.2f} (n={s.n})" if s else "–")
        lines.append(f"| {lang} | " + " | ".join(cells) + " |")
    lines += [
        "",
        "False alarms: share of true claims called False or Misleading (– when the data has no "
        "true claims). Errors count as Unverified.",
    ]
    return "\n".join(lines) + "\n"


def language_accuracy(report: Report, system: str = "full") -> dict[str, LanguageStats]:
    return {
        lang: LanguageStats(n=s.n, macro_f1=s.macro_f1)
        for lang, s in report.systems[system].by_language.items()
        if lang != "und"
    }


def _read_items(path: Path) -> list[EvalItem]:
    return [EvalItem.model_validate_json(x) for x in path.read_text().splitlines() if x]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument(
        "--dataset",
        action="append",
        default=[],
        help="builtin | own[:DIR] | jsonl:PATH | xfact:PATH.tsv | multiclaim:DIR (repeatable)",
    )
    ap.add_argument("--systems", default=",".join(SYSTEMS))
    ap.add_argument(
        "--limit", type=int, default=300, help="stratified by dataset+language; 0 = all"
    )
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true", help="no API calls (random verdicts)")
    ap.add_argument("--yes", action="store_true", help="don't ask before spending money")
    ap.add_argument("--report-only", action="store_true", help="rebuild the report from --out")
    ap.add_argument(
        "--write-language-accuracy",
        action="store_true",
        help=f"write per-language macro-F1 of `full` to {ACCURACY_PATH.name}",
    )
    args = ap.parse_args(argv)

    names = [s.strip() for s in args.systems.split(",") if s.strip()]
    if unknown := set(names) - set(SYSTEMS):
        ap.error(f"unknown systems: {sorted(unknown)}")
    out: Path = args.out or RESULTS_DIR / datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    items_path = out / "items.jsonl"

    if args.report_only:
        if not items_path.exists():
            ap.error(f"no items.jsonl in {out}")
        items = _read_items(items_path)
        names = sorted({s for s, _ in read_predictions(out / "predictions.jsonl")})
    else:
        if not args.dataset:
            ap.error("give at least one --dataset")
        if items_path.exists():  # resuming: keep the same sample
            items = _read_items(items_path)
        else:
            items = stratified_sample(
                dedupe(i for spec in args.dataset for i in load(spec)), args.limit, args.seed
            )
            out.mkdir(parents=True, exist_ok=True)
            items_path.write_text("".join(i.model_dump_json() + "\n" for i in items))
        est = sum(EST_COST_USD[n] for n in names) * len(items)
        print(
            f"{len(items)} items x {len(names)} systems ({', '.join(names)}); "
            f"rough cost ${0 if args.dry_run else est:.2f}. Results: {out}"
        )
        if not args.dry_run and not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
            return 1
        systems = build_systems(names, args.dry_run)
        asyncio.run(run_all(systems, items, out, args.concurrency))

    report = build_report(items, read_predictions(out / "predictions.jsonl"), names)
    (out / "report.json").write_text(report.model_dump_json(indent=1))
    md = markdown(report)
    (out / "report.md").write_text(md)
    print(md)
    if args.write_language_accuracy and "full" in names and not args.dry_run:
        stats = language_accuracy(report)
        data = {k: v.model_dump() for k, v in sorted(stats.items())}
        ACCURACY_PATH.write_text(json.dumps(data, indent=1) + "\n")
        enough = sum(1 for s in stats.values() if s.n >= MIN_EXAMPLES)
        print(f"Wrote {ACCURACY_PATH} ({enough} languages with >= {MIN_EXAMPLES} examples).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
