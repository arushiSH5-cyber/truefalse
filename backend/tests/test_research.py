from app.core.models import EvidenceDraft, SourceKind
from app.research.agent import EvidenceStore, build_tools
from app.research.tiers import tier_for_url
from tests.fakes import FakeSource


def _draft(url: str) -> EvidenceDraft:
    return EvidenceDraft(source=SourceKind.WEB, tier=3, title="t", url=url, quote="q")


def test_store_assigns_ids_and_dedupes() -> None:
    store = EvidenceStore()
    a = store.add([_draft("https://a"), _draft("https://b")])
    b = store.add([_draft("https://a"), _draft("https://c")])
    assert [e.id for e in a] == ["E1", "E2"]
    assert [e.id for e in b] == ["E1", "E3"]
    assert len(store.all()) == 3


async def test_tool_queries_are_pii_redacted() -> None:
    source = FakeSource(SourceKind.WEB, [_draft("https://a")])
    (tool,) = build_tools([source], EvidenceStore())
    out = await tool.handler(
        {"query": "call +1 (408) 555-0199 or mail a@b.com about 5G 2020", "language": None}
    )
    query = source.queries[0][0]
    assert "555" not in query and "a@b.com" not in query
    assert "2020" in query and "5G" in query
    assert '"id": "E1"' in out


def test_tiers() -> None:
    assert tier_for_url("https://www.who.int/news") == 1
    assert tier_for_url("https://data.cdc.gov/x") == 1
    assert tier_for_url("https://www.mohfw.gov.in/") == 1
    assert tier_for_url("https://www.reuters.com/fact-check/x") == 2
    assert tier_for_url("https://randomblog.example.com") == 3
    assert tier_for_url("https://notwho.int.evil.com") == 3
