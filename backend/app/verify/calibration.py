"""Turn panel agreement + source quality into a calibrated probability that a verdict is right.

A small logistic model. The default weights are hand-set priors; Phase 8's eval harness refits
them on labeled data (`Calibrator.fit`) and saves them to calibration_params.json.
"""

import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from app.core.models import Confidence

PARAMS_PATH = Path(__file__).with_name("calibration_params.json")

CONFIDENCE_VALUE = {Confidence.LOW: 1 / 3, Confidence.MEDIUM: 2 / 3, Confidence.HIGH: 1.0}

# Probability cut-offs for user-facing confidence; below MIN_VERDICT -> "Unverified".
MIN_VERDICT = 0.5
MEDIUM_AT = 0.65
HIGH_AT = 0.85


@dataclass(frozen=True)
class Features:
    agreement: float  # fraction of valid judges voting the majority verdict (0..1)
    best_tier: int  # 1 = peer-reviewed/official, 3 = general web
    n_sources: int  # distinct cited evidence items
    factcheck_consensus: int  # +1 fact-check ratings agree with the verdict, -1 contradict, 0 none
    judge_confidence: float  # mean self-reported confidence of majority judges (0..1)

    def vector(self) -> list[float]:
        return [
            self.agreement,
            1.0 if self.best_tier == 1 else 0.0,
            1.0 if self.best_tier == 2 else 0.0,
            min(self.n_sources, 4) / 4,
            float(self.factcheck_consensus),
            self.judge_confidence,
        ]


FEATURE_NAMES = ["agreement", "tier1", "tier2", "sources", "factcheck_consensus", "judge_conf"]


@dataclass
class Calibrator:
    weights: list[float] = field(default_factory=lambda: [2.5, 1.0, 0.6, 1.2, 1.0, 1.5])
    bias: float = -2.6

    def predict(self, features: Features) -> float:
        z = self.bias + sum(w * x for w, x in zip(self.weights, features.vector(), strict=True))
        return 1 / (1 + math.exp(-z))

    def fit(
        self,
        xs: Sequence[Features],
        ys: Sequence[int],
        *,
        lr: float = 0.5,
        epochs: int = 2000,
        l2: float = 0.01,
    ) -> "Calibrator":
        """Logistic regression by gradient descent (y = 1 if the verdict was correct)."""
        x = np.asarray([f.vector() for f in xs], dtype=float)
        y = np.asarray(ys, dtype=float)
        w = np.asarray(self.weights, dtype=float)
        b = self.bias
        for _ in range(epochs):
            p = 1 / (1 + np.exp(-(x @ w + b)))
            grad = p - y
            w -= lr * (x.T @ grad / len(y) + l2 * w)
            b -= lr * float(grad.mean())
        self.weights, self.bias = [float(v) for v in w], float(b)
        return self

    def save(self, path: Path = PARAMS_PATH) -> None:
        path.write_text(json.dumps(asdict(self) | {"features": FEATURE_NAMES}, indent=2))

    @classmethod
    def load(cls, path: Path = PARAMS_PATH) -> "Calibrator":
        if not path.exists():
            return cls()
        data = json.loads(path.read_text())
        return cls(weights=list(data["weights"]), bias=float(data["bias"]))


def confidence_for(probability: float) -> Confidence:
    if probability >= HIGH_AT:
        return Confidence.HIGH
    if probability >= MEDIUM_AT:
        return Confidence.MEDIUM
    return Confidence.LOW


def expected_calibration_error(
    probs: Sequence[float], correct: Sequence[int], bins: int = 10
) -> float:
    p = np.asarray(probs, dtype=float)
    c = np.asarray(correct, dtype=float)
    if len(p) == 0:
        return 0.0
    edges = np.linspace(0, 1, bins + 1)
    ece = 0.0
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        mask = (p > lo) & (p <= hi) if lo > 0 else (p >= lo) & (p <= hi)
        if mask.any():
            ece += mask.mean() * abs(p[mask].mean() - c[mask].mean())
    return float(ece)
