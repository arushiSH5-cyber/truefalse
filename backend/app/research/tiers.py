"""Source tiering by domain.

Tier 1: peer-reviewed research and official bodies (health agencies, governments, election bodies)
Tier 2: news wires and IFCN-signatory fact-checkers
Tier 3: everything else
"""

from urllib.parse import urlparse

TIER1_DOMAINS = {
    "who.int",
    "cdc.gov",
    "nih.gov",
    "ncbi.nlm.nih.gov",
    "pubmed.ncbi.nlm.nih.gov",
    "cochranelibrary.com",
    "cochrane.org",
    "ecdc.europa.eu",
    "ema.europa.eu",
    "fda.gov",
    "nhs.uk",
    "gov.uk",
    "europa.eu",
    "un.org",
    "unicef.org",
    "paho.org",
    "mohfw.gov.in",
    "icmr.gov.in",
    "eci.gov.in",
    "semanticscholar.org",
    "nature.com",
    "thelancet.com",
    "nejm.org",
    "bmj.com",
    "science.org",
    "cell.com",
    "jamanetwork.com",
    "plos.org",
}
TIER2_DOMAINS = {
    "reuters.com",
    "apnews.com",
    "afp.com",
    "factcheck.afp.com",
    "bbc.com",
    "bbc.co.uk",
    "snopes.com",
    "politifact.com",
    "factcheck.org",
    "fullfact.org",
    "boomlive.in",
    "altnews.in",
    "maldita.es",
    "newtral.es",
    "chequeado.com",
    "africacheck.org",
    "aosfatos.org",
    "lupa.uol.com.br",
    "factly.in",
    "vishvasnews.com",
    "teyit.org",
    "correctiv.org",
    "dpa.com",
    "efe.com",
    "verificat.cat",
    "pesacheck.org",
    "rappler.com",
}
OFFICIAL_SUFFIXES = (
    ".gov",
    ".gov.in",
    ".gov.uk",
    ".gob.mx",
    ".gob.es",
    ".gov.br",
    ".go.id",
    ".go.ke",
    ".gov.za",
    ".gc.ca",
    ".gov.au",
    ".mil",
    ".int",
)


def domain_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host.removeprefix("www.")


def _matches(host: str, domains: set[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def tier_for_url(url: str) -> int:
    host = domain_of(url)
    if _matches(host, TIER1_DOMAINS) or host.endswith(OFFICIAL_SUFFIXES):
        return 1
    if _matches(host, TIER2_DOMAINS):
        return 2
    return 3
