"""Fake implementations of every external dependency, so tests run offline."""

from collections import defaultdict, deque
from collections.abc import Sequence
from typing import Any, TypeVar

from pydantic import BaseModel

from app.core.llm import Effort, LLMError, Tool
from app.core.models import EvidenceDraft, ReplyTarget, SourceKind
from app.core.trace import record_tokens

T = TypeVar("T", bound=BaseModel)


class FakeLLM:
    """Returns scripted outputs per stage and replays scripted tool calls."""

    def __init__(self) -> None:
        self.outputs: dict[str, deque[BaseModel | Exception]] = defaultdict(deque)
        self.tool_script: list[tuple[str, dict[str, Any]]] = []
        self.calls: list[dict[str, Any]] = []
        self.auto_ground = True  # grounding check marks every sentence supported unless queued

    @staticmethod
    def _all_supported(user: str, output: type[T]) -> T:
        import json

        items = json.loads(user)
        return output.model_validate(
            {
                "explanations": [
                    {
                        "claim_index": it["claim_index"],
                        "sentences": [
                            {
                                "index": int(i),
                                "supported": True,
                                "evidence_ids": [e["id"] for e in it["evidence"]][:1],
                            }
                            for i in it["sentences"]
                        ],
                    }
                    for it in items
                ]
            }
        )

    def queue(self, stage: str, value: BaseModel | Exception) -> "FakeLLM":
        self.outputs[stage].append(value)
        return self

    def queue_panel(self, *values: BaseModel | Exception) -> "FakeLLM":
        """Queue one output per panel judge (one value = all judges agree)."""
        from app.verify.judges import PANEL

        for spec, value in zip(
            PANEL, values * len(PANEL) if len(values) == 1 else values, strict=False
        ):
            self.queue(f"verify.{spec.name}", value)
        return self

    async def structured(
        self,
        *,
        stage: str,
        system: str,
        user: str,
        output: type[T],
        effort: Effort = "medium",
        max_tokens: int = 8000,
        images: Sequence[Any] = (),
    ) -> T:
        self.calls.append({"stage": stage, "system": system, "user": user, "images": len(images)})
        record_tokens(stage, 100, 50)
        if stage == "ground" and not self.outputs[stage] and self.auto_ground:
            return self._all_supported(user, output)
        if not self.outputs[stage]:
            raise LLMError(f"FakeLLM: nothing queued for {stage}")
        value = self.outputs[stage].popleft()
        if isinstance(value, Exception):
            raise value
        assert isinstance(value, output), f"{stage}: queued {type(value)}, wanted {output}"
        return value

    async def run_tools(
        self,
        *,
        stage: str,
        system: str,
        user: str,
        tools: Sequence[Tool],
        max_tool_calls: int,
        time_budget_s: float,
        effort: Effort = "medium",
    ) -> int:
        self.calls.append({"stage": stage, "system": system, "user": user})
        by_name = {t.name: t for t in tools}
        done = 0
        for name, args in self.tool_script:
            if done >= max_tool_calls:
                break
            if name in by_name:
                await by_name[name].handler(args)
                done += 1
        return done


class FakeSource:
    def __init__(
        self,
        kind: SourceKind,
        results: list[EvidenceDraft] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.kind = kind
        self.results = results or []
        self.error = error
        self.queries: list[tuple[str, str | None]] = []

    async def search(self, query: str, language: str | None = None) -> list[EvidenceDraft]:
        self.queries.append((query, language))
        if self.error:
            raise self.error
        return list(self.results)


class FakeMessenger:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[tuple[ReplyTarget, str]] = []
        self.fail = fail

    async def send_text(self, target: ReplyTarget, text: str) -> None:
        if self.fail:
            raise RuntimeError("send failed")
        self.sent.append((target, text))

    async def send_typing(self, target: ReplyTarget) -> None:
        pass


class FakeQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict[str, Any]]] = []

    async def enqueue(self, function: str, payload: dict[str, Any]) -> None:
        self.jobs.append((function, payload))


# A tiny multilingual "concept" lexicon: words that mean the same thing share a dimension.
# It stands in for a real multilingual embedding model so cross-language logic is testable
# offline and deterministically.
CONCEPTS: dict[str, int] = {}
for i, words in enumerate(
    [
        ["lemon", "limón", "नींबू", "柠檬", "limao", "limau", "ndimu"],
        ["cancer", "cáncer", "कैंसर", "癌", "cancro", "saratani"],
        ["cure", "cures", "cura", "ठीक", "治", "huponya"],
        ["5g"],
        ["coronavirus", "corona", "कोरोना", "新冠"],
        ["vaccine", "vaccines", "vacuna", "vacunas", "टीका", "疫苗", "chanjo"],
        ["microchip", "microchips", "chip", "chips", "चिप", "芯片"],
        ["garlic", "ajo", "लहसुन", "大蒜", "kitunguu"],
        ["tower", "towers", "antenas", "antenna", "टावर", "基站"],
        ["spread", "propagan", "फैलाते", "传播"],
        ["water", "agua", "पानी", "水", "maji"],
    ]
):
    for w in words:
        CONCEPTS[w] = i


class ConceptEmbedder:
    def __init__(self, dim: int = 16) -> None:
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        from app.matching.bm25 import tokenize

        v = [0.0] * self.dim
        lowered = text.casefold()
        for tok in tokenize(text):
            if tok in CONCEPTS:
                v[CONCEPTS[tok]] += 1.0
        for word, idx in CONCEPTS.items():  # substring hits for unspaced scripts (e.g. 癌)
            if not word.isascii() and word in lowered and v[idx] == 0:
                v[idx] += 1.0
        return v

    async def embed_queries(self, texts):  # type: ignore[no-untyped-def]
        import numpy as np

        return np.asarray([self._vec(t) for t in texts], dtype=np.float32)

    embed_documents = embed_queries
