"""Evaluation datasets, all mapped to TrueFalse's four verdicts.

- JSONL (`health_cures.jsonl`, `multilingual_claims.jsonl`, your own forwards): one object per
  line with `id`, `language`, `text`, `expected`, optional `acceptable` (every verdict counted
  as correct) and optional `site` (a fact-check domain to hide from the systems).
- X-FACT (Gupta & Srikumar 2021): the TSV files from github.com/utahnlp/x-fact
  (`data/x-fact/*.tsv`). Labels: true/mostly true -> true, partly true/misleading -> misleading,
  mostly false/false -> false; "complicated/hard to categorise" and "other" are skipped.
  Claims come from fact-check sites, so the item's `site` is hidden from evidence (see run.py).
- MultiClaim (Pikuliak et al. 2023): the folder with `posts.csv` from Zenodo (access on
  request). Posts carry Facebook's misinformation labels, so there are **no true items**:
  it measures how often false/misleading posts are caught, not false alarms.
"""

import ast
import csv
import json
import random
from collections import defaultdict
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.models import Verdict

DATASETS_DIR = Path(__file__).with_name("datasets")
OWN_FORWARDS_DIR = DATASETS_DIR / "own_forwards"

XFACT_LABELS = {
    "true": Verdict.TRUE,
    "mostly true": Verdict.TRUE,
    "partly true/misleading": Verdict.MISLEADING,
    "mostly false": Verdict.FALSE,
    "false": Verdict.FALSE,
}


class EvalItem(BaseModel):
    id: str
    dataset: str
    language: str
    text: str
    expected: Verdict
    acceptable: set[Verdict] = Field(default_factory=set)  # empty = only `expected`
    site: str | None = None  # fact-check domain the label came from (hidden from evidence)
    claim_date: str | None = None

    @property
    def base_language(self) -> str:
        """ "hi-Latn" -> "hi": what the "less tested language" warning is keyed by."""
        return self.language.split("-")[0].lower()

    def correct(self, verdict: Verdict) -> bool:
        return verdict in (self.acceptable or {self.expected})


def load_jsonl(path: Path, dataset: str | None = None) -> list[EvalItem]:
    name = dataset or path.stem
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        out.append(
            EvalItem(
                id=f"{name}:{row.get('id', n)}",
                dataset=name,
                language=row.get("language") or "und",
                text=row["text"],
                expected=Verdict(row["expected"]),
                acceptable={Verdict(v) for v in row.get("acceptable") or []},
                site=row.get("site"),
                claim_date=row.get("claim_date"),
            )
        )
    return out


def load_own_forwards(folder: Path = OWN_FORWARDS_DIR) -> list[EvalItem]:
    """Every `*.jsonl` in the folder except the template."""
    items: list[EvalItem] = []
    for path in sorted(folder.glob("*.jsonl")):
        if path.name != "template.jsonl":
            items += load_jsonl(path, dataset="own_forwards")
    return items


def load_xfact(path: Path) -> list[EvalItem]:
    csv.field_size_limit(1 << 30)
    out = []
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        for n, row in enumerate(reader):
            verdict = XFACT_LABELS.get((row.get("label") or "").strip().lower())
            claim = (row.get("claim") or "").strip()
            if verdict is None or not claim:
                continue
            out.append(
                EvalItem(
                    id=f"xfact:{path.stem}:{n}",
                    dataset="xfact",
                    language=row["language"],
                    text=claim,
                    expected=verdict,
                    site=(row.get("site") or "").strip() or None,
                    claim_date=(row.get("claimDate") or "")[:10] or None,
                )
            )
    return out


def multiclaim_verdict(labels: Sequence[str]) -> Verdict | None:
    """Facebook third-party fact-check labels -> verdict ("Partly false", "Missing context"...)."""
    text = " ".join(labels).lower()
    if "partly" in text or "missing context" in text:
        return Verdict.MISLEADING
    if "false" in text or "altered" in text:
        return Verdict.FALSE
    return None


Text = tuple[str, str, list[tuple[str, float]]]  # (original, English, [(language, prob)])


def _literal(value: str) -> Any:
    # The CSVs store Python literals; newlines inside strings must be escaped first.
    return ast.literal_eval(value.replace("\n", "\\n")) if value else None


def load_multiclaim(folder: Path) -> list[EvalItem]:
    csv.field_size_limit(1 << 30)
    out = []
    with (folder / "posts.csv").open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            verdicts: list[str] = _literal(row.get("verdicts") or "") or []
            verdict = multiclaim_verdict([str(v) for v in verdicts])
            text: Text | None = _literal(row.get("text") or "")
            ocr: list[Text] = _literal(row.get("ocr") or "") or []
            parts = ([text] if text else []) + ocr
            original = "\n".join(p[0] for p in parts if p and p[0]).strip()
            if verdict is None or not original:
                continue
            langs = [lang for p in parts for lang, _ in (p[2] or [])]
            out.append(
                EvalItem(
                    id=f"multiclaim:{row['post_id']}",
                    dataset="multiclaim",
                    language=langs[0] if langs else "und",
                    text=original,
                    expected=verdict,
                )
            )
    return out


def load(spec: str) -> list[EvalItem]:
    """`builtin`, `own`, `jsonl:PATH`, `xfact:PATH.tsv`, `multiclaim:FOLDER`."""
    kind, _, arg = spec.partition(":")
    if kind == "builtin":
        return load_jsonl(DATASETS_DIR / "health_cures.jsonl") + load_jsonl(
            DATASETS_DIR / "multilingual_claims.jsonl"
        )
    if kind == "own":
        return load_own_forwards(Path(arg) if arg else OWN_FORWARDS_DIR)
    if kind == "jsonl":
        return load_jsonl(Path(arg).expanduser())
    if kind == "xfact":
        return load_xfact(Path(arg).expanduser())
    if kind == "multiclaim":
        return load_multiclaim(Path(arg).expanduser())
    raise ValueError(f"unknown dataset {spec!r}")


def stratified_sample(items: Sequence[EvalItem], limit: int, seed: int = 0) -> list[EvalItem]:
    """Up to `limit` items, spread evenly over (dataset, language) groups, reproducibly."""
    if limit <= 0 or len(items) <= limit:
        return list(items)
    rng = random.Random(seed)
    groups: dict[tuple[str, str], list[EvalItem]] = defaultdict(list)
    for item in items:
        groups[(item.dataset, item.language)].append(item)
    for g in groups.values():
        rng.shuffle(g)
    picked: list[EvalItem] = []
    pools = [iter(groups[k]) for k in sorted(groups)]
    while len(picked) < limit and pools:
        alive: list[Iterator[EvalItem]] = []
        for pool in pools:
            nxt = next(pool, None)
            if nxt is not None and len(picked) < limit:
                picked.append(nxt)
                alive.append(pool)
        pools = alive
    return picked


def dedupe(items: Iterable[EvalItem]) -> list[EvalItem]:
    return list({i.id: i for i in items}.values())
