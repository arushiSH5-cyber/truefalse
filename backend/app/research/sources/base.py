from typing import Protocol

from app.core.models import EvidenceDraft, SourceKind


class Source(Protocol):
    kind: SourceKind

    async def search(self, query: str, language: str | None = None) -> list[EvidenceDraft]: ...


def clip(text: str | None, limit: int = 600) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
