"""PubMed via NCBI E-utilities (esearch -> efetch abstracts).

Docs: https://www.ncbi.nlm.nih.gov/books/NBK25499/
"""

import xml.etree.ElementTree as ET

import httpx

from app.core.http import get_json, get_text
from app.core.models import EvidenceDraft, SourceKind
from app.research.sources.base import clip

BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


class PubMedSource:
    kind = SourceKind.PUBMED

    def __init__(
        self, client: httpx.AsyncClient, api_key: str | None = None, max_results: int = 5
    ) -> None:
        self._client = client
        self._common = {"tool": "truefalse"} | ({"api_key": api_key} if api_key else {})
        self._max = max_results

    async def search(self, query: str, language: str | None = None) -> list[EvidenceDraft]:
        found = await get_json(
            self._client,
            f"{BASE}/esearch.fcgi",
            params=self._common
            | {
                "db": "pubmed",
                "term": query,
                "retmode": "json",
                "retmax": self._max,
                "sort": "relevance",
            },
        )
        ids = found.get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        xml = await get_text(
            self._client,
            f"{BASE}/efetch.fcgi",
            params=self._common | {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
        )
        return parse_efetch(xml)


def parse_efetch(xml: str) -> list[EvidenceDraft]:
    out: list[EvidenceDraft] = []
    root = ET.fromstring(xml)
    for article in root.iter("PubmedArticle"):
        pmid = article.findtext(".//PMID")
        title_node = article.find(".//ArticleTitle")
        title = "".join(title_node.itertext()) if title_node is not None else ""
        abstract = " ".join("".join(node.itertext()) for node in article.iter("AbstractText"))
        if not pmid or not abstract:
            continue
        year = article.findtext(".//PubDate/Year") or article.findtext(".//PubDate/MedlineDate")
        journal = article.findtext(".//Journal/Title")
        types = {t.text or "" for t in article.iter("PublicationType")}
        if "Retracted Publication" in types:
            continue
        out.append(
            EvidenceDraft(
                source=SourceKind.PUBMED,
                tier=1,
                title=title or f"PubMed {pmid}",
                url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                quote=clip(abstract),
                published=year,
                publisher=journal,
            )
        )
    return out
