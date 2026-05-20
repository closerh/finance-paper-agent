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
            time.sleep(2)
        encoded_query = urllib.parse.quote(query)
        url = (
            "https://api.semanticscholar.org/graph/v1/paper/search"
            f"?query={encoded_query}"
            f"&fields={SS_FIELDS}"
            f"&limit=25"
            f"&publicationDateOrYear={from_date}:{to_date}"
        )
        try:
            data = json.loads(_get(url, timeout=20))
        except Exception as e:
            logger.warning(f"Semantic Scholar query '{query}' failed: {e}")
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
# Source 2: arXiv RSS feeds
# ---------------------------------------------------------------------------

ARXIV_RSS_CATEGORIES = ["q-fin", "econ"]


def fetch_arxiv_rss(config: Config) -> list[Paper]:
    cutoff = _cutoff(config.lookback_days)
    papers: list[Paper] = []

    for cat in ARXIV_RSS_CATEGORIES:
        url = f"https://rss.arxiv.org/rss/{cat}"
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            logger.warning(f"arXiv RSS {cat} failed: {e}")
            continue

        for entry in feed.entries:
            title = getattr(entry, "title", "").strip()
            if not title:
                continue

            published = None
            for attr in ("published_parsed", "updated_parsed"):
                parsed = getattr(entry, attr, None)
                if parsed:
                    published = datetime(*parsed[:6], tzinfo=timezone.utc)
                    break
            if published is None or published < cutoff:
                continue

            abstract = getattr(entry, "summary", "").strip()
            link = getattr(entry, "link", "")
            authors_raw = getattr(entry, "author", "")
            authors = [a.strip() for a in authors_raw.split(",") if a.strip()]
            tags = [t.get("term", "") for t in getattr(entry, "tags", [])]
            pdf_url = link.replace("/abs/", "/pdf/") if "/abs/" in link else None

            papers.append(Paper(
                title=title,
                authors=authors,
                abstract=abstract,
                url=link,
                published=published,
                source="arXiv",
                categories=tags or [cat],
                pdf_url=pdf_url,
                score=len(tags),
            ))

    logger.info(f"arXiv RSS: found {len(papers)} papers in the last {config.lookback_days} days")
    return papers


# ---------------------------------------------------------------------------
# Source 3: NBER Working Papers RSS
# ---------------------------------------------------------------------------

def fetch_nber_papers(config: Config) -> list[Paper]:
    cutoff = _cutoff(max(config.lookback_days, 14))
    try:
        feed = feedparser.parse(config.nber_rss_url)
    except Exception as e:
        logger.warning(f"NBER fetch failed: {e}")
        return []

    papers: list[Paper] = []
    for entry in feed.entries:
        published = None
        for attr in ("published_parsed", "updated_parsed"):
            parsed = getattr(entry, attr, None)
            if parsed:
                published = datetime(*parsed[:6], tzinfo=timezone.utc)
                break
        if published is None or published < cutoff:
            continue

        title = getattr(entry, "title", "Untitled").strip()
        url = getattr(entry, "link", "")
        abstract = getattr(entry, "summary", "").strip()
        authors_raw = getattr(entry, "author", "")
        authors = [a.strip() for a in authors_raw.split(",") if a.strip()]

        papers.append(Paper(
            title=title,
            authors=authors,
            abstract=abstract,
            url=url,
            published=published,
            source="NBER",
            categories=["Economics"],
            score=2,
        ))

    logger.info(f"NBER: found {len(papers)} papers in the last 14 days")
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
    arxiv_papers = fetch_arxiv_rss(config)
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
