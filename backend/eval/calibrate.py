"""Refit the confidence calibrator on eval results.

The judge panel turns agreement + source quality into a probability that its verdict is right
(`app/verify/calibration.py`). The shipped weights are hand-set priors. After a real `full` run,
`python -m eval.run --report-only --out <run> --fit-calibrator` refits them on what actually
happened and writes `app/verify/calibration_params.json`, which the app loads at startup.

Only items whose `full` prediction has exactly one calibrated claim are used: the dataset label
is for the whole message, so with several claims we can't tell which decision it belongs to.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.verify.calibration import Calibrator, Features, expected_calibration_error
from eval.datasets import EvalItem
from eval.systems import Prediction

MIN_POINTS = 30  # fewer than this and the hand-set priors are the better guess


@dataclass
class CalibrationFit:
    n: int
    accuracy: float  # share of calibrated verdicts that were right
    ece_before: float
    ece_after: float
    calibrator: Calibrator | None  # None when there were too few points


def training_points(
    items: Sequence[EvalItem], preds: dict[tuple[str, str], Prediction], system: str = "full"
) -> tuple[list[Features], list[int]]:
    xs: list[Features] = []
    ys: list[int] = []
    for item in items:
        pred = preds.get((system, item.id))
        if pred is None or pred.error or len(pred.calibration) != 1:
            continue
        point = pred.calibration[0]
        f = point.features
        xs.append(
            Features(
                agreement=float(f["agreement"]),
                best_tier=int(f["best_tier"]),
                n_sources=int(f["n_sources"]),
                factcheck_consensus=int(f["factcheck_consensus"]),
                judge_confidence=float(f["judge_confidence"]),
            )
        )
        ys.append(1 if item.correct(point.verdict) else 0)
    return xs, ys


def fit(
    items: Sequence[EvalItem],
    preds: dict[tuple[str, str], Prediction],
    base: Calibrator | None = None,
) -> CalibrationFit:
    xs, ys = training_points(items, preds)
    base = base or Calibrator.load()
    before = [base.predict(x) for x in xs]
    ece_before = expected_calibration_error(before, ys)
    acc = sum(ys) / len(ys) if ys else 0.0
    if len(xs) < MIN_POINTS:
        return CalibrationFit(len(xs), acc, ece_before, ece_before, None)
    # Start from the priors so a small sample nudges them rather than replacing them.
    fitted = Calibrator(weights=list(base.weights), bias=base.bias).fit(xs, ys)
    ece_after = expected_calibration_error([fitted.predict(x) for x in xs], ys)
    return CalibrationFit(len(xs), acc, ece_before, ece_after, fitted)
