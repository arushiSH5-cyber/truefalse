from app.core.llm import LLMError
from app.ingestion.common import FAILURE
from app.pipeline import Deps
from app.worker import process
from tests.fakes import FakeLLM, FakeMessenger


def _payload(submission) -> dict:
    return {
        "submission": submission.model_dump(mode="json"),
        "target": {"platform": "telegram", "chat_id": "42", "reply_to_message_id": "7"},
    }


async def test_failure_sends_polite_message(submission) -> None:
    llm = FakeLLM().queue("extract", LLMError("overloaded"))
    messenger = FakeMessenger()
    status = await process(_payload(submission), Deps(llm, []), messenger, (4.0, 20.0))
    assert status == "failed"
    assert messenger.sent[0][1] == FAILURE


async def test_success_replies(submission, spanish_health_extraction) -> None:
    llm = FakeLLM().queue("extract", spanish_health_extraction).queue("compose", LLMError("x"))
    messenger = FakeMessenger()
    status = await process(_payload(submission), Deps(llm, []), messenger, (4.0, 20.0))
    assert status == "ok"
    assert messenger.sent[0][1].startswith("⚪")
