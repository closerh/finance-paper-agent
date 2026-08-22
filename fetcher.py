import json
import logging
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser

from config import Config

ARXIV_API_URL = "https://export.arxiv.org/api/query"

logger = logging.getLogger(__name__)

ARXIV_NS = "http://www.w3.org/2005/Atom"


@dataclass
class Paper:
    title: str
    authors: list[str]
    abstract: str
    url: str
    published: datetime
    source: str
    categories: list[str]
    pdf_url: Optional[str] = None
    score: int = 0


def _cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "FinancePaperAgent/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ---------------------------------------------------------------------------
# Source 1: Semantic Scholar API (reliable, no key needed)
# ---------------------------------------------------------------------------

SS_FIELDS = "title,authors,abstract,publicationDate,url,openAccessPdf,externalIds,fieldsOfStudy"

def fetch_semantic_scholar(config: Config) -> list[Paper]:
    cutoff = _cutoff(config.lookback_days)
    from_date = cutoff.strftime("%Y-%m-%d")
    to_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    papers: list[Paper] = []
    seen_titles: set[str] = set()

    queries = [
        "financial economics asset pricing portfolio",
        "macroeconomics monetary policy corporate finance",
    ]

    for i, query in enumerate(queries):
        if i > 0:
            time.sleep(5)
        encoded_query = urllib.parse.quote(query)
        url = (
            "https://api.semanticscholar.org/graph/v1/paper/search"
            f"?query={encoded_query}"
            f"&fields={SS_FIELDS}"
            f"&limit=25"
            f"&publicationDateOrYear={from_date}:{to_date}"
        )
        data = None
        for attempt in range(3):
            try:
                data = json.loads(_get(url, timeout=20))
                break
            except Exception as e:
                if "429" in str(e) and attempt < 2:
                    wait = 30 * (attempt + 1)
                    logger.warning(f"Semantic Scholar 429, retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    logger.warning(f"Semantic Scholar query '{query}' failed: {e}")
                    break
        if data is None:
            continue

        for item in data.get("data", []):
            title = (item.get("title") or "").strip()
            if not title or title.lower() in seen_titles:
                continue

            pub_date_str = item.get("publicationDate") or ""
            try:
                published = datetime.fromisoformat(pub_date_str).replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if published < cutoff:
                continue

            authors = [a.get("name", "") for a in (item.get("authors") or [])]
            abstract = (item.get("abstract") or "").strip()
            paper_url = item.get("url") or ""
            pdf_url = None
            oap = item.get("openAccessPdf")
            if oap and isinstance(oap, dict):
                pdf_url = oap.get("url")

            ext_ids = item.get("externalIds") or {}
            if ext_ids.get("ArXiv"):
                paper_url = f"https://arxiv.org/abs/{ext_ids['ArXiv']}"
                if not pdf_url:
                    pdf_url = f"https://arxiv.org/pdf/{ext_ids['ArXiv']}"

            categories = [f for f in (item.get("fieldsOfStudy") or []) if f]

            seen_titles.add(title.lower())
            papers.append(Paper(
                title=title,
                authors=authors,
                abstract=abstract,
                url=paper_url,
                published=published,
                source="Semantic Scholar",
                categories=categories or ["Economics"],
                pdf_url=pdf_url,
                score=len(categories),
            ))

    logger.info(f"Semantic Scholar: found {len(papers)} papers in the last {config.lookback_days} days")
    return papers


# ---------------------------------------------------------------------------
# Source 2: arXiv API (replaces RSS — works on weekends, uses subcategories)
# ---------------------------------------------------------------------------

_ATOM_NS = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# econ subcategories alongside q-fin for breadth
_ECON_SUBCATS = ["econ.EM", "econ.GN", "econ.TH"]


def fetch_arxiv_api(config: Config) -> list[Paper]:
    cutoff = _cutoff(config.lookback_days)
    cats = list(config.arxiv_categories) + _ECON_SUBCATS
    cat_query = " OR ".join(f"cat:{c}" for c in cats)
    query = urllib.parse.quote(cat_query)

    url = (
        f"{ARXIV_API_URL}?search_query={query}"
        "&sortBy=submittedDate&sortOrder=descending&max_results=100"
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FinancePaperAgent/1.0"})
        raw = urllib.request.urlopen(req, timeout=30).read()
        root = ET.fromstring(raw)
    except Exception as e:
        logger.warning("arXiv API fetch failed: %s", e)
        return []

    papers: list[Paper] = []
    for entry in root.findall("a:entry", _ATOM_NS):
        title = (entry.findtext("a:title", "", _ATOM_NS) or "").strip().replace("\n", " ")
        if not title:
            continue

        pub_str = entry.findtext("a:published", "", _ATOM_NS) or ""
        try:
            published = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
        except Exception:
            continue
        if published < cutoff:
            continue

        abstract = (entry.findtext("a:summary", "", _ATOM_NS) or "").strip().replace("\n", " ")

        link = ""
        pdf_url = None
        for lnk in entry.findall("a:link", _ATOM_NS):
            href = lnk.get("href", "")
            if lnk.get("rel") == "alternate":
                link = href
            elif lnk.get("type") == "application/pdf":
                pdf_url = href
        if not link:
            link = entry.findtext("a:id", "", _ATOM_NS) or ""
        if not pdf_url and "/abs/" in link:
            pdf_url = link.replace("/abs/", "/pdf/")

        authors = [
            (a.findtext("a:name", "", _ATOM_NS) or "").strip()
            for a in entry.findall("a:author", _ATOM_NS)
        ]

        categories: list[str] = []
        pc = entry.find("arxiv:primary_category", _ATOM_NS)
        if pc is not None and pc.get("term"):
            categories.append(pc.get("term"))
        for cat_el in entry.findall("a:category", _ATOM_NS):
            term = cat_el.get("term", "")
            if term and term not in categories:
                categories.append(term)

        papers.append(Paper(
            title=title,
            authors=authors,
            abstract=abstract,
            url=link,
            published=published,
            source="arXiv",
            categories=categories or ["q-fin"],
            pdf_url=pdf_url,
        ))

    logger.info("arXiv API: found %d papers in the last %d days", len(papers), config.lookback_days)
    return papers


# ---------------------------------------------------------------------------
# Source 3: NBER via Semantic Scholar (NBER RSS is defunct)
# ---------------------------------------------------------------------------

_NBER_SS_QUERIES = [
    "NBER working paper macroeconomics finance",
    "NBER working paper asset pricing monetary policy",
]


def fetch_nber_papers(config: Config) -> list[Paper]:
    """Fetch NBER working papers via Semantic Scholar (NBER's own RSS is defunct)."""
    cutoff = _cutoff(max(config.lookback_days, 14))
    from_date = cutoff.strftime("%Y-%m-%d")
    to_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    papers: list[Paper] = []
    seen_titles: set[str] = set()

    for i, query in enumerate(_NBER_SS_QUERIES):
        if i > 0:
            time.sleep(5)
        encoded = urllib.parse.quote(query)
        url = (
            "https://api.semanticscholar.org/graph/v1/paper/search"
            f"?query={encoded}"
            f"&fields={SS_FIELDS}"
            "&limit=20"
            f"&publicationDateOrYear={from_date}:{to_date}"
        )
        data = None
        for attempt in range(3):
            try:
                data = json.loads(_get(url, timeout=20))
                break
            except Exception as e:
                if "429" in str(e) and attempt < 2:
                    wait = 30 * (attempt + 1)
                    logger.warning("NBER/SS 429, retrying in %ds...", wait)
                    time.sleep(wait)
                else:
                    logger.warning("NBER/SS query '%s' failed: %s", query[:40], e)
                    break
        if not data:
            continue

        for item in data.get("data", []):
            title = (item.get("title") or "").strip()
            if not title or title.lower() in seen_titles:
                continue
            # Only keep papers that mention NBER or look like working papers
            abstract = (item.get("abstract") or "").lower()
            if "nber" not in abstract and "national bureau" not in abstract:
                continue

            pub_date_str = item.get("publicationDate") or ""
            try:
                published = datetime.fromisoformat(pub_date_str).replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if published < cutoff:
                continue

            authors = [a.get("name", "") for a in (item.get("authors") or [])]
            paper_url = item.get("url") or ""
            pdf_url = None
            oap = item.get("openAccessPdf")
            if oap and isinstance(oap, dict):
                pdf_url = oap.get("url")

            seen_titles.add(title.lower())
            papers.append(Paper(
                title=title,
                authors=authors,
                abstract=item.get("abstract") or "",
                url=paper_url,
                published=published,
                source="NBER",
                categories=["Economics"],
                pdf_url=pdf_url,
                score=2,
            ))

    logger.info("NBER (via SS): found %d papers in the last %d days", len(papers), config.lookback_days)
    return papers


# ---------------------------------------------------------------------------
# Selection: merge, deduplicate, diversity filter
# ---------------------------------------------------------------------------

def _similar(a: str, b: str) -> bool:
    a_words = set(a.lower().split())
    b_words = set(b.lower().split())
    if not a_words or not b_words:
        return False
    overlap = len(a_words & b_words) / min(len(a_words), len(b_words))
    return overlap > 0.7


def select_top_papers(all_papers: list[Paper], n: int) -> list[Paper]:
    sorted_papers = sorted(all_papers, key=lambda p: (p.score, p.published), reverse=True)
    selected: list[Paper] = []
    used_sources: dict[str, int] = {}

    for paper in sorted_papers:
        if len(selected) >= n:
            break
        if not paper.abstract:
            continue
        if any(_similar(paper.title, s.title) for s in selected):
            continue
        if used_sources.get(paper.source, 0) >= 3:
            continue
        selected.append(paper)
        used_sources[paper.source] = used_sources.get(paper.source, 0) + 1

    if len(selected) < n:
        for paper in sorted_papers:
            if len(selected) >= n:
                break
            if paper not in selected and paper.abstract and not any(_similar(paper.title, s.title) for s in selected):
                selected.append(paper)

    return selected[:n]


def fetch_top_papers(config: Config) -> tuple[list[Paper], list[Paper], dict]:
    """Returns (selected_papers, all_papers, source_counts)."""
    ss_papers = fetch_semantic_scholar(config)
    arxiv_papers = fetch_arxiv_api(config)
    nber_papers = fetch_nber_papers(config)

    all_papers = ss_papers + arxiv_papers + nber_papers
    selected = select_top_papers(all_papers, config.top_n)

    source_counts = {
        "Semantic Scholar": len(ss_papers),
        "arXiv": len(arxiv_papers),
        "NBER": len(nber_papers),
        "total": len(all_papers),
        "selected": len(selected),
    }

    logger.info(f"Selected {len(selected)} of {len(all_papers)} papers for this week's report")
    return selected, all_papers, source_counts
