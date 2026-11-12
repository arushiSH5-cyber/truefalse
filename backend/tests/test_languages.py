"""Phase 6: scripts, romanization, language switch, accuracy tiers, welcome, voice replies."""

import json
from pathlib import Path

import pytest

from app.core.llm import LLMError
from app.core.models import (
    Claim,
    ComposedReply,
    Extraction,
    Part,
    PartKind,
    Platform,
    Submission,
)
from app.core.prefs import InMemoryPrefsStore, UserPrefs
from app.ingestion.common import CHECKING, WELCOME
from app.ingestion.handler import handle_inbound
from app.ingestion.telegram import parse_update
from app.language.accuracy import LanguageStats, is_lower_accuracy
from app.language.names import autonym, parse_language_name
from app.language.scripts import dominant_script, is_mixed_script, looks_romanized
from app.pipeline import Deps, run_check
from app.respond.translate import Translation, Translator
from app.worker import Extras, process
from tests.fakes import FakeLLM, FakeMessenger, FakeQueue

DATA = Path(__file__).parents[1] / "eval" / "datasets"


def _load(name: str) -> list[dict]:
    return [json.loads(line) for line in (DATA / name).read_text(encoding="utf-8").splitlines()]


# ---- datasets ------------------------------------------------------------------------------


def test_datasets_are_well_formed() -> None:
    for name in ("health_cures.jsonl", "multilingual_claims.jsonl"):
        rows = _load(name)
        assert len({r["id"] for r in rows}) == len(rows)
        for r in rows:
            assert r["expected"] in r["acceptable"]
            assert set(r["acceptable"]) <= {"true", "false", "misleading", "unverified"}
    health = _load("health_cures.jsonl")
    assert {r["topic"] for r in health} == {"health", "superstition"}
    assert sum(r["harmful"] for r in health) >= 15
    langs = {r["language"] for r in _load("multilingual_claims.jsonl")}
    assert len(langs) >= 12


@pytest.mark.parametrize("row", _load("multilingual_claims.jsonl"), ids=lambda r: r["id"])
def test_script_and_romanization_detection(row) -> None:
    assert dominant_script(row["text"]) == row["script"]
    if row["romanized_or_mixed"] and row["language"] in ("hi", "ta"):
        assert looks_romanized(row["language"], row["text"])
    if not row["romanized_or_mixed"]:
        assert not looks_romanized(row["language"], row["text"])


def test_mixed_script() -> None:
    assert is_mixed_script("यह खबर fake news है or not, please check karo")
    assert not is_mixed_script("यह खबर गलत है 5G")


# ---- language names ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Español", "es"),
        ("español!", "es"),
        ("ESPAÑOL", "es"),
        ("Spanish", "es"),
        ("العربية", "ar"),
        ("हिंदी", "hi"),
        ("Hindi", "hi"),
        ("reply in hindi", "hi"),
        ("Kiswahili", "sw"),
        ("中文", "zh"),
        ("Bahasa Indonesia", "id"),
        ("தமிழ்", "ta"),
        ("inglés", "en"),
        ("Português", "pt"),
        ("Русский", "ru"),
        ("isiZulu", "zu"),
        ("Hello there", None),
        ("5G causes covid", None),
        ("es", "es"),
    ],
)
def test_parse_language_name(text, code) -> None:
    assert parse_language_name(text) == code


def test_autonym() -> None:
    assert autonym("hi") == "हिंदी" and autonym("es") == "Español" and autonym("xx") == "xx"


def test_accuracy_tiers() -> None:
    assert not is_lower_accuracy("es", {}) and not is_lower_accuracy("pt-BR", {})
    assert is_lower_accuracy("sw", {}) and is_lower_accuracy("ta", {})
    measured = {"sw": LanguageStats(n=50, macro_f1=0.8), "es": LanguageStats(n=50, macro_f1=0.4)}
    assert not is_lower_accuracy("sw", measured) and is_lower_accuracy("es", measured)
    assert is_lower_accuracy("sw", {"sw": LanguageStats(n=5, macro_f1=0.9)})  # too few examples


# ---- handler: language switch & voice toggle ----------------------------------------------


def _update(text: str, lang: str = "en") -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": 5, "type": "private"},
            "from": {"id": 9, "language_code": lang},
            "text": text,
        },
    }


async def test_language_switch_and_ack_language() -> None:
    store, m, q = InMemoryPrefsStore(), FakeMessenger(), FakeQueue()
    inbound = parse_update(_update("Español"), "salt")
    assert await handle_inbound(inbound, m, q, store) == "language_set"
    prefs = await store.get(inbound.submission.user_hash)
    assert prefs is not None and prefs.reply_language == "es"
    assert "Español" in m.sent[-1][1] and not q.jobs
    # Next message: acknowledgment in the chosen language even though Telegram UI is English.
    await handle_inbound(parse_update(_update("Garlic cures covid"), "salt"), m, q, store)
    assert m.sent[-1][1] == CHECKING["es"] and len(q.jobs) == 1


async def test_voice_toggle_and_reset_language() -> None:
    store, m, q = InMemoryPrefsStore(), FakeMessenger(), FakeQueue()
    assert (
        await handle_inbound(parse_update(_update("/voice"), "s"), m, q, store) == "voice_toggled"
    )
    user = parse_update(_update("/voice"), "s").submission.user_hash
    assert (await store.get(user)).voice_replies is True
    await handle_inbound(parse_update(_update("/voice"), "s"), m, q, store)
    assert (await store.get(user)).voice_replies is False
    await store.save(UserPrefs(user_hash=user, reply_language="hi"))
    assert (
        await handle_inbound(parse_update(_update("/language"), "s"), m, q, store)
        == "language_auto"
    )
    assert (await store.get(user)).reply_language is None


# ---- pipeline language behaviour -----------------------------------------------------------


def _extraction(lang: str, name: str, claims: list[Claim] | None = None, **kw) -> Extraction:
    base = dict(
        language=lang,
        language_name=name,
        script="Latin",
        romanized_or_mixed=False,
        looks_like_satire_or_opinion=False,
        claims=claims or [],
    )
    return Extraction(**(base | kw))


def _sub(text: str, kind: PartKind = PartKind.TEXT, hint: str | None = "es") -> Submission:
    part = Part(kind=kind, text=text) if kind is PartKind.TEXT else Part(kind=kind, media_ref="x")
    return Submission(platform=Platform.TELEGRAM, user_hash="u", parts=[part], language_hint=hint)


async def test_lower_accuracy_warning_and_reply_language_override() -> None:
    llm = FakeLLM().queue("extract", _extraction("sw", "Swahili"))
    llm.queue("compose", LLMError("x"))
    report = await run_check(_sub("Habari za leo"), Deps(llm, []), reply_language="es")
    assert any("less tested in Swahili" in w for w in report.warnings)
    compose = next(c for c in llm.calls if c["stage"] == "compose")
    assert "Target language: es (Spanish)" in compose["user"]
    assert report.reply_language == "es"


async def test_romanized_fallback_when_model_misses_it() -> None:
    llm = FakeLLM().queue("extract", _extraction("hi", "Hindi")).queue("compose", LLMError("x"))
    report = await run_check(_sub("garam nimbu pani cancer theek karta hai"), Deps(llm, []))
    assert any("mixes languages or scripts" in w for w in report.warnings)


async def test_lid_hint_passed_to_extractor() -> None:
    class Lid:
        async def detect(self, text):  # type: ignore[no-untyped-def]
            from app.language.detect import LanguageGuess

            return LanguageGuess(language="hi", confidence=0.91, script="Devanagari")

    llm = FakeLLM().queue("extract", _extraction("hi", "Hindi")).queue("compose", LLMError("x"))
    await run_check(_sub("यह सच है?"), Deps(llm, [], lid=Lid()))
    extract = next(c for c in llm.calls if c["stage"] == "extract")
    assert "<language_id_hint>hi (confidence 0.91)</language_id_hint>" in extract["user"]


# ---- translator, welcome, voice ------------------------------------------------------------


async def test_translator_caches_and_falls_back() -> None:
    llm = FakeLLM().queue("translate", Translation(text="¡Hola!"))
    t = Translator(llm)
    assert await t.translate("Hi!", "es") == "¡Hola!"
    assert await t.translate("Hi!", "es") == "¡Hola!"  # cached: no second LLM call
    assert await t.translate("Hi!", "en") == "Hi!"
    assert await t.translate("Bye", "fr") == "Bye"  # nothing queued -> English fallback
    assert sum(c["stage"] == "translate" for c in llm.calls) == 2


class FakeTTS:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        self.calls: list[tuple[str, str]] = []

    async def synthesize(self, text: str, language: str) -> str:
        self.calls.append((text, language))
        path = self.tmp / f"v{len(self.calls)}.ogg"
        path.write_bytes(b"OggS")
        return str(path)


def _payload(sub: Submission) -> dict:
    return {
        "submission": sub.model_dump(mode="json"),
        "target": {"platform": "telegram", "chat_id": "1", "reply_to_message_id": None},
    }


def _composed() -> ComposedReply:
    return ComposedReply(
        verdict_label="Sin verificar",
        explanation="No hay fuentes.",
        confidence_label="Baja",
        warnings=[],
        safety_note=None,
        sources_heading="Fuentes",
    )


async def test_first_time_welcome_then_not_again(tmp_path) -> None:
    store = InMemoryPrefsStore()
    llm = FakeLLM().queue("extract", _extraction("es", "Spanish")).queue("compose", _composed())
    llm.queue("translate", Translation(text="¡Hola! Soy TrueFalse."))
    m = FakeMessenger()
    extras = Extras(prefs=store, translator=Translator(llm))
    await process(_payload(_sub("hola")), Deps(llm, []), m, (4, 20), extras)
    assert [t for _, t in m.sent][0] == "¡Hola! Soy TrueFalse."
    assert m.sent[1][1].startswith("⚪ SIN VERIFICAR")
    llm.queue("extract", _extraction("es", "Spanish")).queue("compose", _composed())
    await process(_payload(_sub("otra")), Deps(llm, []), m, (4, 20), extras)
    assert len(m.sent) == 3 and WELCOME not in [t for _, t in m.sent]


async def test_voice_in_voice_out(tmp_path) -> None:
    llm = FakeLLM().queue("extract", _extraction("es", "Spanish")).queue("compose", _composed())
    tts, m = FakeTTS(tmp_path), FakeMessenger()
    extras = Extras(tts=tts, tts_supports=lambda lang: lang == "es")
    await process(_payload(_sub("", PartKind.AUDIO)), Deps(llm, []), m, (4, 20), extras)
    assert tts.calls == [("Sin verificar. No hay fuentes.", "es")]
    assert len(m.voices) == 1 and not Path(m.voices[0][1]).exists()  # temp audio deleted


async def test_no_voice_for_unsupported_language_or_text_input(tmp_path) -> None:
    tts = FakeTTS(tmp_path)
    for sub, supports in ((_sub("", PartKind.AUDIO), lambda lang: False), (_sub("hola"), None)):
        llm = FakeLLM().queue("extract", _extraction("es", "Spanish")).queue("compose", _composed())
        m = FakeMessenger()
        await process(
            _payload(sub), Deps(llm, []), m, (4, 20), Extras(tts=tts, tts_supports=supports)
        )
        assert m.voices == []
    assert tts.calls == []


# ---- status messages in any language ------------------------------------------------------


class SlowLLM(FakeLLM):
    async def structured(self, **kw):  # type: ignore[no-untyped-def, override]
        import asyncio

        await asyncio.sleep(10)
        return await super().structured(**kw)


async def test_status_messages_use_table_then_translator() -> None:
    from app.ingestion.common import FAILURE, say

    llm = FakeLLM().queue("translate", Translation(text="🔎 Mo n ṣàyẹ̀wò…"))
    tr = Translator(llm)
    assert await say(CHECKING, "es-MX", tr) == CHECKING["es"]  # hand-written, no model call
    assert await say(CHECKING, "yo", tr) == "🔎 Mo n ṣàyẹ̀wò…"
    assert await say(CHECKING, "yo", tr) == "🔎 Mo n ṣàyẹ̀wò…"  # cached
    assert len(llm.calls) == 1 and CHECKING["en"] in llm.calls[0]["user"]
    assert await say(FAILURE, None, tr) == FAILURE
    assert await say(FAILURE, "yo", None) == FAILURE


async def test_slow_translation_falls_back_to_english(monkeypatch) -> None:
    from app.ingestion import common

    monkeypatch.setattr(common, "STATUS_TRANSLATE_TIMEOUT_S", 0.05)
    assert await common.say(common.TEXT_ONLY, "yo", Translator(SlowLLM())) == common.TEXT_ONLY


async def test_ack_translated_for_language_without_table() -> None:
    m, q = FakeMessenger(), FakeQueue()
    tr = Translator(FakeLLM().queue("translate", Translation(text="Mo n ṣàyẹ̀wò")))
    inbound = parse_update(_update("Ata ń wo àrùn jẹjẹrẹ sàn", lang="yo"), "salt")
    assert await handle_inbound(inbound, m, q, None, tr) == "queued"
    assert m.sent[-1][1] == "Mo n ṣàyẹ̀wò"


async def test_failure_message_in_users_language() -> None:
    llm = FakeLLM().queue("extract", LLMError("down"))
    tr = Translator(FakeLLM().queue("translate", Translation(text="Ma binu, nkan kan ṣẹlẹ̀.")))
    m = FakeMessenger()
    sub = _sub("Ata ń wo àrùn jẹjẹrẹ sàn").model_copy(update={"language_hint": "yo"})
    assert await process(_payload(sub), Deps(llm, []), m, (4, 20), Extras(translator=tr)) == (
        "failed"
    )
    assert m.sent[-1][1] == "Ma binu, nkan kan ṣẹlẹ̀."
