"""Evaluation harness: loaders, systems, metrics, runner and the AI-detection scorer (offline)."""

import csv
import json
from pathlib import Path

from PIL import Image

from app.ai_detect.signals import AISignal, Strength
from app.core.models import (
    ClaimJudgement,
    Confidence,
    Evidence,
    EvidenceDraft,
    JudgePanelOutput,
    SourceKind,
    Verdict,
)
from app.language.accuracy import LanguageStats, is_lower_accuracy
from app.pipeline import Deps
from eval import ai_detect, run
from eval.datasets import (
    EvalItem,
    load,
    load_multiclaim,
    load_xfact,
    multiclaim_verdict,
    stratified_sample,
)
from eval.metrics import score
from eval.systems import (
    FullSystem,
    LookupOnlySystem,
    Prediction,
    SingleJudgeOutput,
    SingleJudgeSystem,
    lookup_verdict,
)
from tests.fakes import FakeLLM, FakeSource

XFACT_COLUMNS = [
    "language", "site", *(f"evidence_{i}" for i in range(1, 6)), *(f"link_{i}" for i in range(1, 6)),
    "claimDate", "reviewDate", "claimant", "claim", "label",
]  # fmt: skip


def _xfact(tmp_path: Path) -> Path:
    rows = [
        ("pt", "aosfatos.org", "2019-01-02T00:00:00Z", "Vacina causa autismo", "false"),
        (
            "hi",
            "boomlive.in",
            "2020-05-01T00:00:00Z",
            "नमक का पानी कोरोना ठीक करता है",
            "mostly false",
        ),
        ("ka", "factcheck.ge", "", "claim ka", "partly true/misleading"),
        ("id", "turnbackhoax.id", "", "Klaim benar", "mostly true"),
        ("pl", "demagog.org.pl", "", "Hard one", "complicated/hard to categorise"),
        ("it", "pagellapolitica.it", "", "Other one", "other"),
    ]
    path = tmp_path / "test.all.tsv"
    with path.open("w", encoding="utf-8") as f:
        f.write("\t".join(XFACT_COLUMNS) + "\n")
        for lang, site, date, claim, label in rows:
            values = dict.fromkeys(XFACT_COLUMNS, "")
            values |= {"language": lang, "site": site, "claimDate": date, "claim": claim}
            values["label"] = label
            f.write("\t".join(values[c] for c in XFACT_COLUMNS) + "\n")
    return path


def test_xfact_loader_maps_labels_and_skips_unclear(tmp_path) -> None:
    items = load_xfact(_xfact(tmp_path))
    assert [(i.language, i.expected) for i in items] == [
        ("pt", Verdict.FALSE),
        ("hi", Verdict.FALSE),
        ("ka", Verdict.MISLEADING),
        ("id", Verdict.TRUE),
    ]
    assert items[0].site == "aosfatos.org" and items[0].claim_date == "2019-01-02"


def test_multiclaim_loader_reads_python_literals(tmp_path) -> None:
    path = tmp_path / "posts.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["post_id", "instances", "ocr", "verdicts", "text"])
        w.writerow([
            1, "[]", "[('Texto en la\nimagen', 'Text in the image', [('spa', 1.0)])]",
            "['False information']", "('El limón cura', 'Lemon cures', [('spa', 0.98)])",
        ])  # fmt: skip
        w.writerow(
            [2, "[]", "[]", "['Missing context']", "('Post two', 'Post two', [('eng', 1.0)])"]
        )
        w.writerow([3, "[]", "[]", "[]", "('No verdict', 'No verdict', [('eng', 1.0)])"])
    items = load_multiclaim(tmp_path)
    assert [i.expected for i in items] == [Verdict.FALSE, Verdict.MISLEADING]
    assert items[0].text == "El limón cura\nTexto en la\nimagen" and items[0].language == "spa"
    assert multiclaim_verdict(["Partly false information"]) is Verdict.MISLEADING
    assert multiclaim_verdict(["Altered photo"]) is Verdict.FALSE
    assert multiclaim_verdict(["Satire"]) is None


def test_builtin_and_own_forwards_load(tmp_path) -> None:
    builtin = load("builtin")
    assert len(builtin) == 68 and all(i.text for i in builtin)
    own = tmp_path / "own"
    own.mkdir()
    template = Path("eval/datasets/own_forwards/template.jsonl")
    (own / "template.jsonl").write_text(template.read_text())  # the template is ignored
    (own / "mine.jsonl").write_text(template.read_text())
    items = load(f"own:{own}")
    assert len(items) == 2 and items[0].base_language == "hi" and items[0].site == "factly.in"


def test_stratified_sample_spreads_languages() -> None:
    items = [
        EvalItem(id=f"{lang}{n}", dataset="d", language=lang, text="t", expected=Verdict.FALSE)
        for lang, count in (("pt", 50), ("ka", 3), ("hi", 10))
        for n in range(count)
    ]
    picked = stratified_sample(items, 9)
    assert sorted(i.language for i in picked) == ["hi"] * 3 + ["ka"] * 3 + ["pt"] * 3
    assert stratified_sample(items, 9) == picked  # reproducible


def _item(expected: Verdict, lang: str = "en", acceptable: set[Verdict] | None = None) -> EvalItem:
    return EvalItem(
        id=f"{expected}{lang}{id(acceptable)}",
        dataset="d",
        language=lang,
        text="t",
        expected=expected,
        acceptable=acceptable or set(),
    )


def _pred(v: Verdict, **kw) -> Prediction:
    return Prediction(system="s", item_id="x", verdict=v, **kw)


def test_metrics() -> None:
    T, M, F, U = Verdict.TRUE, Verdict.MISLEADING, Verdict.FALSE, Verdict.UNVERIFIED
    pairs = [
        (_item(T), _pred(T)),
        (_item(T), _pred(F)),  # false alarm
        (_item(T), _pred(U)),
        (_item(F), _pred(F)),
        (_item(F, acceptable={F, M}), _pred(M)),  # acceptable, but not an F1 hit
        (_item(M), _pred(U, error="boom")),
    ]
    s = score(pairs)
    assert s.n == 6 and s.errors == 1
    assert s.accuracy == round(3 / 6, 4)
    assert s.false_alarm_rate == round(1 / 3, 4)
    assert s.unverified_rate == round(2 / 6, 4)
    # true: tp1 fp0 fn2 -> 0.5; false: tp1 fp1 fn1 -> 0.5; misleading: tp0 -> 0
    assert s.per_class_f1 == {"true": 0.5, "misleading": 0.0, "false": 0.5}
    assert s.macro_f1 == round(1 / 3, 4)
    assert score([(_item(F), _pred(F))]).false_alarm_rate is None  # no true items


def test_lookup_verdict_majority_and_tie() -> None:
    def ev(rating: str, n: int) -> Evidence:
        return Evidence(
            id=f"E{n}", source=SourceKind.FACTCHECK, tier=2, title="t", url="u", quote="q",
            rating=rating,
        )  # fmt: skip

    assert lookup_verdict([]) is Verdict.UNVERIFIED
    assert lookup_verdict([ev("Mostly true", 1), ev("False", 2), ev("Pants on fire", 3)]) is (
        Verdict.FALSE
    )
    assert lookup_verdict([ev("Misleading", 1), ev("False", 2)]) is Verdict.MISLEADING  # first


async def test_full_system_hides_the_answer_key(spanish_health_extraction, factcheck_draft) -> None:
    def deps(llm: FakeLLM) -> Deps:
        llm.queue("extract", spanish_health_extraction)
        llm.queue_panel(
            JudgePanelOutput(
                judgements=[
                    ClaimJudgement(
                        claim_index=0,
                        verdict=Verdict.FALSE,
                        confidence=Confidence.HIGH,
                        explanation="Fact-checkers say lemon water does not cure cancer.",
                        cited_evidence_ids=["E1"],
                    )
                ]
            )
        )
        llm.tool_script = [("search_factchecks", {"query": "lemon cancer", "language": "es"})]
        return Deps(llm, [FakeSource(SourceKind.FACTCHECK, [factcheck_draft])])

    item = _item(Verdict.FALSE, "es")
    seen = await FullSystem(deps(FakeLLM()), (4, 20)).predict(item)
    assert seen.verdict is Verdict.FALSE and seen.cost_usd > 0
    hidden = item.model_copy(update={"site": "afp.com"})  # matches factcheck.afp.com
    blind = await FullSystem(deps(FakeLLM()), (4, 20)).predict(hidden)
    assert blind.verdict is Verdict.UNVERIFIED  # the only source was the answer key


async def test_single_judge_and_lookup_only(factcheck_draft) -> None:
    llm = FakeLLM().queue("single_judge", SingleJudgeOutput(verdict=Verdict.FALSE, reason="r"))
    pred = await SingleJudgeSystem(llm, (4, 20)).predict(_item(Verdict.FALSE))
    assert pred.verdict is Verdict.FALSE and llm.calls[0]["stage"] == "single_judge"

    web = FakeSource(SourceKind.WEB, [factcheck_draft])
    fc = FakeSource(SourceKind.FACTCHECK, [factcheck_draft])
    lookup = LookupOnlySystem([web, fc], None, FakeLLM(), (4, 20))
    assert (await lookup.predict(_item(Verdict.FALSE, "es"))).verdict is Verdict.FALSE
    assert not web.queries and [lang for _, lang in fc.queries] == ["es", None]
    hidden = _item(Verdict.FALSE, "es").model_copy(update={"site": "factcheck.afp.com"})
    assert (await lookup.predict(hidden)).verdict is Verdict.UNVERIFIED
    assert not LookupOnlySystem([web], None, FakeLLM(), (4, 20)).available


def test_runner_dry_run_resumes_and_reports(tmp_path, capsys) -> None:
    out = tmp_path / "run"
    args = ["--dataset", "builtin", "--limit", "20", "--dry-run", "--out", str(out)]
    assert run.main(args) == 0
    preds = (out / "predictions.jsonl").read_text().splitlines()
    assert len(preds) == 60  # 20 items x 3 systems
    assert run.main(args) == 0  # resume: nothing new to do
    assert len((out / "predictions.jsonl").read_text().splitlines()) == 60
    report = json.loads((out / "report.json").read_text())
    assert set(report["systems"]) == set(run.SYSTEMS) and report["n_items"] == 20
    md = (out / "report.md").read_text()
    assert "| full | 20 |" in md and "## Macro-F1 by language" in md
    assert run.main(["--report-only", "--out", str(out)]) == 0


def test_language_accuracy_is_relative_to_median() -> None:
    hard = {lang: LanguageStats(n=40, macro_f1=0.42) for lang in ("en", "es", "pt", "hi")}
    assert not is_lower_accuracy("en", hard)  # a hard benchmark doesn't flag everyone
    hard["sw"] = LanguageStats(n=40, macro_f1=0.25)
    assert is_lower_accuracy("sw", hard) and not is_lower_accuracy("es", hard)
    report = run.Report(
        n_items=1,
        systems={
            "full": run.SystemReport(
                overall=score([]),
                by_dataset={},
                by_language={"hi": score([(_item(Verdict.TRUE), _pred(Verdict.TRUE))])},
            )
        },
    )
    assert run.language_accuracy(report) == {"hi": LanguageStats(n=1, macro_f1=1.0)}


class FakeDetector:
    def __init__(self, flagged: set[str]) -> None:
        self.flagged = flagged

    def _signals(self, path: Path) -> list[AISignal]:
        if path.name not in self.flagged:
            return []
        return [
            AISignal(
                detector="c2pa", media="image", finding="ai_generated", strength=Strength.STRONG
            )
        ]

    async def check_image(self, path: Path) -> tuple[list[AISignal], list[EvidenceDraft]]:
        return self._signals(path), []

    async def check_audio(self, path: Path) -> list[AISignal]:
        return self._signals(path)


async def test_ai_detect_scorer(tmp_path) -> None:
    for label, names in (("ai", ["a1.png", "a2.png"]), ("real", ["r1.png", "r2.jpg", "r3.png"])):
        (tmp_path / label).mkdir()
        for name in names:
            Image.new("RGB", (8, 8)).save(tmp_path / label / name)
    (tmp_path / "real" / "notes.txt").write_text("ignored")
    items = ai_detect.find_media(tmp_path)
    assert len(items) == 5 and {i.media for i in items} == {"image"}
    results = await ai_detect.run(FakeDetector({"a1.png", "r1.png"}), items, 2)  # type: ignore[arg-type]
    rep = ai_detect.report(results)["overall"]
    assert rep["recall"] == 0.5 and rep["precision"] == 0.5
    assert rep["false_alarm_rate"] == 1 / 3
    assert rep["detectors"]["c2pa"]["on_ai"] == 1 and rep["detectors"]["c2pa"]["on_real"] == 1
