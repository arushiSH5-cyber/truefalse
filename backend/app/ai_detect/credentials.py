"""C2PA Content Credentials: a signed record of how a file was made (camera, editor, AI model).

Needs the `ai_detect` extra (c2pa-python). Most messaging apps strip credentials when they
recompress media, so their absence says nothing; they survive in web uploads, files sent as
documents, and many links. No C2PA trust list is configured, so a valid signature means "this
record is intact and signed by <issuer>", not "the issuer is trusted".
"""

import asyncio
import json
from pathlib import Path
from typing import Any

from app.ai_detect.signals import AISignal, MediaKind, Strength
from app.core.logging import get_logger

log = get_logger(__name__)

IPTC = "http://cv.iptc.org/newscodes/digitalsourcetype/"
# IPTC digital source types that mean generative AI made all or part of the content.
AI_SOURCE_TYPES = {
    "trainedAlgorithmicMedia",
    "compositeWithTrainedAlgorithmicMedia",
    "trainedAlgorithmicData",
}
CAPTURE_SOURCE_TYPES = {"digitalCapture", "computationalCapture"}


def _source_types(manifest_store: dict[str, Any]) -> list[tuple[str, str | None]]:
    """(source type, tool name) from c2pa.actions* assertions in every manifest (incl. history)."""
    out: list[tuple[str, str | None]] = []
    for manifest in (manifest_store.get("manifests") or {}).values():
        info: list[dict[str, Any]] = manifest.get("claim_generator_info") or []
        generator = info[0].get("name") if info else None
        for assertion in manifest.get("assertions") or []:
            if not str(assertion.get("label", "")).startswith("c2pa.actions"):
                continue
            for action in (assertion.get("data") or {}).get("actions") or []:
                kind = str(action.get("digitalSourceType") or "").removeprefix(IPTC)
                if kind:
                    agent = action.get("softwareAgent")
                    tool = agent.get("name") if isinstance(agent, dict) else agent
                    out.append((kind, tool or generator))
    return out


def interpret(manifest_store: dict[str, Any], state: str | None, media: MediaKind) -> AISignal:
    """Turn a manifest store (Reader.json()) into one signal."""
    active = (manifest_store.get("manifests") or {}).get(
        manifest_store.get("active_manifest") or "", {}
    )
    issuer = (active.get("signature_info") or {}).get("issuer") or "an unknown signer"
    if state == "Invalid":
        return AISignal(
            detector="c2pa",
            media=media,
            finding="edited",
            strength=Strength.MODERATE,
            detail=f"This {media}'s Content Credentials no longer match the file, so it was "
            "changed after it was signed.",
        )
    kinds = _source_types(manifest_store)
    ai = [(k, tool) for k, tool in kinds if k in AI_SOURCE_TYPES]
    if ai:
        tool = next((t for _, t in ai if t), None)
        made_with = f" ({tool})" if tool else ""
        return AISignal(
            detector="c2pa",
            media=media,
            finding="ai_generated",
            strength=Strength.STRONG,
            detail=f"This {media} carries Content Credentials saying it was made with AI"
            f"{made_with}, signed by {issuer}.",
        )
    if any(k in CAPTURE_SOURCE_TYPES for k, _ in kinds):
        return AISignal(
            detector="c2pa",
            media=media,
            finding="camera_original",
            strength=Strength.MODERATE,
            detail=f"Content Credentials signed by {issuer} say a camera captured this {media}.",
        )
    return AISignal(
        detector="c2pa",
        media=media,
        finding="no_finding",
        strength=Strength.WEAK,
        detail=f"Has Content Credentials signed by {issuer}, with no statement about AI.",
    )


def _read(path: Path) -> tuple[dict[str, Any], str | None] | None:
    import c2pa

    reader = c2pa.Reader.try_create(str(path))
    if reader is None:
        return None
    with reader:
        return json.loads(reader.json()), reader.get_validation_state()


class ContentCredentialsCheck:
    async def check(self, path: Path, media: MediaKind) -> list[AISignal]:
        try:
            found = await asyncio.to_thread(_read, path)
        except Exception as exc:  # unsupported format, corrupt file: no information
            log.info("c2pa_unreadable", error=type(exc).__name__)
            return []
        if found is None:
            return []
        store, state = found
        return [interpret(store, str(state) if state is not None else None, media)]
