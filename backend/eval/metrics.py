"""Scores for verdict predictions.

- accuracy: share of items whose verdict is in the item's acceptable set.
- macro_f1: F1 per expected class (true, misleading, false), averaged. Unverified is never a
  gold label here, so it only counts as a miss; classes absent from the gold labels are skipped.
- false_alarm_rate: share of TRUE items the system called False or Misleading. This is the
  costly error for a fact-checker (telling someone a true thing is false). Undefined (None)
  when the data has no true items, e.g. MultiClaim.
- unverified_rate: how often the system declined to decide.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence

from pydantic import BaseModel

from app.core.models import Verdict
from eval.datasets import EvalItem
from eval.systems import Prediction

CLASSES = (Verdict.TRUE, Verdict.MISLEADING, Verdict.FALSE)


class Scores(BaseModel):
    n: int
    errors: int  # predictions that crashed (counted as Unverified)
    accuracy: float
    macro_f1: float
    false_alarm_rate: float | None
    unverified_rate: float
    needs_review_rate: float
    mean_cost_usd: float
    mean_latency_s: float
    per_class_f1: dict[str, float]


def _f1(tp: int, fp: int, fn: int) -> float:
    return 0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn)


def score(pairs: Sequence[tuple[EvalItem, Prediction]]) -> Scores:
    n = len(pairs)
    if n == 0:
        return Scores(
            n=0,
            errors=0,
            accuracy=0.0,
            macro_f1=0.0,
            false_alarm_rate=None,
            unverified_rate=0.0,
            needs_review_rate=0.0,
            mean_cost_usd=0.0,
            mean_latency_s=0.0,
            per_class_f1={},
        )
    per_class: dict[str, float] = {}
    for c in CLASSES:
        if not any(i.expected is c for i, _ in pairs):
            continue
        tp = sum(1 for i, p in pairs if i.expected is c and p.verdict is c)
        fp = sum(1 for i, p in pairs if i.expected is not c and p.verdict is c)
        fn = sum(1 for i, p in pairs if i.expected is c and p.verdict is not c)
        per_class[c.value] = round(_f1(tp, fp, fn), 4)
    true_items = [p for i, p in pairs if i.expected is Verdict.TRUE]
    alarms = sum(1 for p in true_items if p.verdict in (Verdict.FALSE, Verdict.MISLEADING))
    return Scores(
        n=n,
        errors=sum(1 for _, p in pairs if p.error),
        accuracy=round(sum(1 for i, p in pairs if i.correct(p.verdict)) / n, 4),
        macro_f1=round(sum(per_class.values()) / len(per_class), 4) if per_class else 0.0,
        false_alarm_rate=round(alarms / len(true_items), 4) if true_items else None,
        unverified_rate=round(sum(1 for _, p in pairs if p.verdict is Verdict.UNVERIFIED) / n, 4),
        needs_review_rate=round(sum(1 for _, p in pairs if p.needs_human_review) / n, 4),
        mean_cost_usd=round(sum(p.cost_usd for _, p in pairs) / n, 5),
        mean_latency_s=round(sum(p.latency_s for _, p in pairs) / n, 2),
        per_class_f1=per_class,
    )


def grouped(
    pairs: Iterable[tuple[EvalItem, Prediction]], key: str
) -> dict[str, list[tuple[EvalItem, Prediction]]]:
    out: dict[str, list[tuple[EvalItem, Prediction]]] = defaultdict(list)
    for item, pred in pairs:
        out[str(getattr(item, key))].append((item, pred))
    return dict(sorted(out.items()))
