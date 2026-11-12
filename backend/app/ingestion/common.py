"""Helpers shared by all platform adapters."""

import asyncio
import hashlib
import hmac
from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.respond.translate import Translator

log = get_logger(__name__)

# A status message never waits longer than this for a translation; English is sent instead
# (the translation is still cached for next time).
STATUS_TRANSLATE_TIMEOUT_S = 3.0

# Short status strings shown before language detection runs, keyed by the platform's UI
# language hint. These are a fast path: other languages are translated by `say`. The final
# answer is always in the language the message was written in.
CHECKING = {
    "en": "🔎 Checking… this can take up to a minute.",
    "es": "🔎 Verificando… puede tardar hasta un minuto.",
    "pt": "🔎 Verificando… pode levar até um minuto.",
    "fr": "🔎 Vérification… cela peut prendre une minute.",
    "de": "🔎 Wird geprüft… das kann bis zu einer Minute dauern.",
    "it": "🔎 Verifica in corso… può richiedere fino a un minuto.",
    "hi": "🔎 जाँच हो रही है… इसमें एक मिनट तक लग सकता है।",
    "ta": "🔎 சரிபார்க்கிறது… ஒரு நிமிடம் வரை ஆகலாம்.",
    "ar": "🔎 جارٍ التحقق… قد يستغرق ذلك دقيقة.",
    "zh": "🔎 正在核查…最多可能需要一分钟。",
    "ru": "🔎 Проверяем… это может занять до минуты.",
    "id": "🔎 Sedang memeriksa… ini bisa memakan waktu hingga satu menit.",
    "sw": "🔎 Inakagua… inaweza kuchukua hadi dakika moja.",
    "tr": "🔎 Kontrol ediliyor… bu bir dakika kadar sürebilir.",
    "bn": "🔎 যাচাই করা হচ্ছে… এক মিনিট পর্যন্ত লাগতে পারে।",
    "ur": "🔎 جانچ ہو رہی ہے… اس میں ایک منٹ تک لگ سکتا ہے۔",
}

WELCOME = (
    "👋 Hi! I'm TrueFalse.\n\n"
    "Forward me any message, rumor, or claim — in any language — and I'll check it against "
    "fact-checkers and trusted sources like WHO, PubMed, and news agencies.\n\n"
    "You'll get: 🟢 True · 🟠 Misleading · 🔴 False · ⚪ Unverified, a short explanation, and "
    "links to the sources.\n\n"
    "I never save your messages."
)

TEXT_ONLY = (
    "I can't check this type of file. Send me text, a photo or screenshot, a voice note, "
    "a video, or a link."
)

# Sent when a check involves media or links, which take longer.
WORKING_ON_MEDIA = {
    "en": "🎧 Looking at your media and links… this can take a few minutes.",
    "es": "🎧 Revisando tus archivos y enlaces… puede tardar unos minutos.",
    "pt": "🎧 Analisando sua mídia e links… pode levar alguns minutos.",
    "fr": "🎧 J'examine vos médias et liens… cela peut prendre quelques minutes.",
    "hi": "🎧 आपकी मीडिया और लिंक देख रहे हैं… इसमें कुछ मिनट लग सकते हैं।",
    "ar": "🎧 نراجع الوسائط والروابط… قد يستغرق ذلك بضع دقائق.",
    "id": "🎧 Memeriksa media dan tautan Anda… ini bisa memakan beberapa menit.",
    "sw": "🎧 Tunakagua picha, sauti na viungo… inaweza kuchukua dakika chache.",
    "ru": "🎧 Изучаем ваши медиа и ссылки… это может занять несколько минут.",
    "zh": "🎧 正在查看您的媒体和链接……可能需要几分钟。",
}

FAILURE = "Sorry, something went wrong while checking this. Please try again in a few minutes."


def hash_user(salt: str, platform: str, user_id: str) -> str:
    """Pseudonymous, non-reversible user id. Raw ids/phone numbers are never stored."""
    return hmac.new(salt.encode(), f"{platform}:{user_id}".encode(), hashlib.sha256).hexdigest()[
        :32
    ]


async def say(
    message: str | dict[str, str], language: str | None, translator: "Translator | None" = None
) -> str:
    """A fixed message in the user's language: a hand-written table entry if there is one,
    else the cached translator (bounded wait), else English."""
    base = (language or "en").split("-")[0].lower()
    if isinstance(message, dict):
        if base in message:
            return message[base]
        message = message["en"]
    if translator is None or base == "en" or not language:
        return message
    task = asyncio.ensure_future(translator.translate(message, language))
    try:
        # shield: a timeout leaves the translation running, so it is cached for next time
        return await asyncio.wait_for(asyncio.shield(task), STATUS_TRANSLATE_TIMEOUT_S)
    except TimeoutError:
        log.info("status_translate_slow", language=language)
        return message
