"""Source tiering by domain.

Tier 1: peer-reviewed research and official bodies (health agencies, governments, election bodies)
Tier 2: news wires, public broadcasters and fact-checkers
Tier 3: everything else

The lists live in tiers.json so they can grow without code changes. Official domains are
recognized by pattern in every country (gov.*, gob.*, gouv.*, govt.*, ...), not by a fixed list.
"""

import json
import re
from pathlib import Path
from urllib.parse import urlparse

_DATA = json.loads(Path(__file__).with_name("tiers.json").read_text(encoding="utf-8"))
TIER1_DOMAINS: frozenset[str] = frozenset(_DATA["tier1_domains"])
TIER2_DOMAINS: frozenset[str] = frozenset(_DATA["tier2_domains"])
OFFICIAL_PATTERNS = [re.compile(p) for p in _DATA["official_patterns"]]


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host.removeprefix("www.")


def _matches(host: str, domains: frozenset[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def is_official(host: str) -> bool:
    return any(p.search(host) for p in OFFICIAL_PATTERNS)


def tier_for_url(url: str) -> int:
    host = domain_of(url)
    if _matches(host, TIER1_DOMAINS) or is_official(host):
        return 1
    if _matches(host, TIER2_DOMAINS):
        return 2
    return 3
