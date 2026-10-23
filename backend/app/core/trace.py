"""Per-request stage latency and token accounting (anonymized metrics only)."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import BaseModel, Field


class StageStats(BaseModel):
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    # Tokens whose model price is known (e.g. a Haiku judge), already converted to USD.
    priced_input_tokens: int = 0
    priced_output_tokens: int = 0
    priced_cost_usd: float = 0.0


class RunTrace(BaseModel):
    stages: dict[str, StageStats] = Field(default_factory=dict)

    def stage(self, name: str) -> StageStats:
        return self.stages.setdefault(name, StageStats())

    def add_tokens(
        self,
        stage: str,
        input_tokens: int,
        output_tokens: int,
        price_per_mtok: tuple[float, float] | None = None,
    ) -> None:
        s = self.stage(stage)
        s.input_tokens += input_tokens
        s.output_tokens += output_tokens
        if price_per_mtok is not None:
            s.priced_input_tokens += input_tokens
            s.priced_output_tokens += output_tokens
            s.priced_cost_usd += (
                input_tokens / 1e6 * price_per_mtok[0] + output_tokens / 1e6 * price_per_mtok[1]
            )

    def total_tokens(self) -> tuple[int, int]:
        return (
            sum(s.input_tokens for s in self.stages.values()),
            sum(s.output_tokens for s in self.stages.values()),
        )

    def cost_usd(self, input_per_mtok: float, output_per_mtok: float) -> float:
        """Known per-model prices where recorded; the given default price for the rest."""
        i, o = self.total_tokens()
        i -= sum(s.priced_input_tokens for s in self.stages.values())
        o -= sum(s.priced_output_tokens for s in self.stages.values())
        known = sum(s.priced_cost_usd for s in self.stages.values())
        return round(known + i / 1e6 * input_per_mtok + o / 1e6 * output_per_mtok, 5)

    @contextmanager
    def timed(self, stage: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            self.stage(stage).latency_ms += round((time.perf_counter() - start) * 1000, 1)


current_trace: ContextVar[RunTrace | None] = ContextVar("current_trace", default=None)


def record_tokens(
    stage: str,
    input_tokens: int,
    output_tokens: int,
    price_per_mtok: tuple[float, float] | None = None,
) -> None:
    trace = current_trace.get()
    if trace is not None:
        trace.add_tokens(stage, input_tokens, output_tokens, price_per_mtok)
