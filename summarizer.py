import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import anthropic

from config import Config
from fetcher import Paper

logger = logging.getLogger(__name__)

KNOWLEDGE_PATH = Path(__file__).parent / "knowledge.md"


def _load_knowledge() -> str:
    if KNOWLEDGE_PATH.exists():
        return KNOWLEDGE_PATH.read_text(encoding="utf-8")
    return ""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PaperScore:
    paper: Paper
    relevance: float       # 1–5 from Claude
    source_quality: float  # 1–5 from source tier
    final_score: float     # 0.5 * relevance + 0.5 * source_quality
    relevance_reason: str = ""


@dataclass
class PaperSummary:
    paper: Paper
    summary: str           # 1–2 sentences
    key_idea: str          # 1–2 sentences
    application: str       # 1–2 sentences
    relevance: float
    source_quality: float
    final_score: float
    relevance_reason: str = ""
    keywords: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Source quality scoring
# ---------------------------------------------------------------------------

_SOURCE_QUALITY: dict[str, float] = {
    # Tier 5 — top journals (we rarely see these via RSS, but handle if they appear)
    "journal of finance": 5.0,
    "review of financial studies": 5.0,
    "journal of financial economics": 5.0,
    "jfqa": 5.0,
    "journal of financial and quantitative analysis": 5.0,
    "review of asset pricing studies": 5.0,
    "journal of derivatives": 5.0,
    # Tier 4 — NBER + practitioner journals
    "nber": 4.0,
    "journal of portfolio management": 4.0,
    "mathematical finance": 4.0,
    "quantitative finance": 4.0,
    "management science": 4.0,
    # Tier 3 — arXiv q-fin
    "arxiv": 3.0,
    # Tier 2 — Semantic Scholar / other
    "semantic scholar": 2.0,
    # Practitioner journals
    "risk": 4.0,
}

_QFIN_CATEGORIES = {
    "q-fin.pr", "q-fin.tr", "q-fin.rm", "q-fin.pm",
    "q-fin.mf", "q-fin.st", "q-fin.gn", "q-fin.ec",
}


def source_quality_score(paper: Paper) -> float:
    src = paper.source.lower()

    # NBER always Tier 4
    if "nber" in src:
        return 4.0

    # arXiv: bump to 3 only for q-fin categories, else 2
    if "arxiv" in src:
        cats_lower = {c.lower() for c in paper.categories}
        if cats_lower & _QFIN_CATEGORIES:
            return 3.0
        return 2.0

    # Semantic Scholar: check if categories suggest a known journal
    if "semantic scholar" in src:
        for name, score in _SOURCE_QUALITY.items():
            if name in " ".join(paper.categories).lower():
                return score
        return 2.0

    # Fallback: try matching source string
    for name, score in _SOURCE_QUALITY.items():
        if name in src:
            return score

    return 2.0


# ---------------------------------------------------------------------------
# Stage 1: relevance screening (fast, Haiku, batched)
# ---------------------------------------------------------------------------

_SCREEN_SYSTEM = """You are a relevance filter for a quantitative investment strategies (QIS) research team.
Your only job is to score how relevant each paper is to the team's work."""

_BATCH_SIZE = 10


def _batch_screen_prompt(batch: list[Paper], knowledge: str) -> str:
    papers_text = ""
    for i, paper in enumerate(batch, 1):
        papers_text += (
            f"\n[{i}] Title: {paper.title}\n"
            f"    Source: {paper.source}\n"
            f"    Categories: {', '.join(paper.categories)}\n"
            f"    Abstract: {paper.abstract[:600]}\n"
        )

    return f"""Score the relevance of each paper below for the QIS team described in the knowledge base.

{knowledge}

---
Papers to score:
{papers_text}
Return EXACTLY one line per paper, numbered, in this format:
N: SCORE | reason (one short phrase)

N is the paper number, SCORE is 1–5 using the Relevance Scoring Guide above.
Example:
1: 4 | directly models implied vol surface dynamics
2: 1 | cryptocurrency paper, excluded topic"""


def _parse_batch_response(text: str, n: int) -> list[tuple[float, str]]:
    """Parse batch response into (relevance, reason) pairs. Falls back to (1.0, '') on parse failure."""
    results: list[tuple[float, str]] = [(1.0, "")] * n
    for line in text.splitlines():
        m = re.match(r"(\d+):\s*([\d.]+)\s*\|?\s*(.*)", line.strip())
        if not m:
            continue
        idx = int(m.group(1)) - 1
        if 0 <= idx < n:
            try:
                rel = float(m.group(2))
                rel = max(1.0, min(5.0, rel))
                reason = m.group(3).strip()
                results[idx] = (rel, reason)
            except Exception:
                pass
    return results


def screen_papers(papers: list[Paper], client: anthropic.Anthropic, config: Config) -> list[PaperScore]:
    """Score all candidate papers on relevance using batched Haiku calls."""
    knowledge = _load_knowledge()
    sq_scores = [source_quality_score(p) for p in papers]
    scored_pairs: list[tuple[float, str]] = []

    batches = [papers[i:i + _BATCH_SIZE] for i in range(0, len(papers), _BATCH_SIZE)]
    logger.info("Screening %d papers in %d batch(es) of up to %d", len(papers), len(batches), _BATCH_SIZE)

    for batch_idx, batch in enumerate(batches):
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=_BATCH_SIZE * 30,
                system=[{"type": "text", "text": _SCREEN_SYSTEM, "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": _batch_screen_prompt(batch, knowledge)}],
            )
            pairs = _parse_batch_response(resp.content[0].text.strip(), len(batch))
        except Exception as exc:
            logger.warning("Batch %d screening failed: %s", batch_idx + 1, exc)
            pairs = [(1.0, "")] * len(batch)
        scored_pairs.extend(pairs)

    results: list[PaperScore] = []
    for paper, sq, (rel, reason) in zip(papers, sq_scores, scored_pairs):
        final = 0.5 * rel + 0.5 * sq
        results.append(PaperScore(
            paper=paper,
            relevance=rel,
            source_quality=sq,
            final_score=final,
            relevance_reason=reason,
        ))
        logger.debug("Scored '%s': rel=%.1f sq=%.1f final=%.2f", paper.title[:50], rel, sq, final)

    return results


# ---------------------------------------------------------------------------
# Stage 2: full analysis (Sonnet, top-5 only)
# ---------------------------------------------------------------------------

_ANALYSIS_SYSTEM = """You are a research analyst at a quantitative investment strategies (QIS) firm.
Write concise, practitioner-focused analyses of academic papers."""


def _analysis_prompt(paper: Paper, knowledge: str) -> str:
    authors_str = ", ".join(paper.authors[:4])
    if len(paper.authors) > 4:
        authors_str += " et al."
    return f"""Analyze this paper for a QIS practitioner audience. Use the context below.

{knowledge}

---
Title: {paper.title}
Authors: {authors_str}
Source: {paper.source}
Categories: {", ".join(paper.categories)}
Abstract:
{paper.abstract}

Return EXACTLY these four sections (plain text, no markdown, no asterisks):

SUMMARY
[1–2 sentences: what the paper does and what it finds]

KEY IDEA
[1–2 sentences: the core methodological or conceptual contribution]

APPLICATION
[1–2 sentences: how a systematic/derivatives trading desk could use this]

KEYWORDS
[5–7 comma-separated tags]"""


def _parse_analysis(text: str) -> tuple[str, str, str, list[str]]:
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        s = line.strip()
        if s in ("SUMMARY", "KEY IDEA", "APPLICATION", "KEYWORDS"):
            current = s
            sections[current] = []
        elif current and s:
            sections[current].append(s)

    def get(key: str) -> str:
        return " ".join(sections.get(key, [])).strip()

    kw_raw = get("KEYWORDS")
    keywords = [k.strip() for k in kw_raw.split(",") if k.strip()]
    return get("SUMMARY"), get("KEY IDEA"), get("APPLICATION"), keywords


def analyze_paper(score: PaperScore, client: anthropic.Anthropic, config: Config) -> PaperSummary:
    paper = score.paper
    logger.info("Analyzing: %s", paper.title[:60])
    knowledge = _load_knowledge()

    resp = client.messages.create(
        model=config.claude_model,
        max_tokens=400,
        system=[{"type": "text", "text": _ANALYSIS_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": _analysis_prompt(paper, knowledge)}],
    )
    summary, key_idea, application, keywords = _parse_analysis(resp.content[0].text.strip())

    return PaperSummary(
        paper=paper,
        summary=summary,
        key_idea=key_idea,
        application=application,
        relevance=score.relevance,
        source_quality=score.source_quality,
        final_score=score.final_score,
        relevance_reason=score.relevance_reason,
        keywords=keywords,
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def analyze_papers_directly(papers: list[Paper], config: Config) -> list[PaperSummary]:
    """Analyze all papers directly without a relevance screening pass.

    Used for pre-curated lists (e.g. classics special issue) where every paper
    should be included regardless of automated relevance score.
    Relevance is set to 5.0 for all papers; source quality is computed normally.
    """
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    summaries: list[PaperSummary] = []
    for paper in papers:
        sq = source_quality_score(paper)
        score = PaperScore(
            paper=paper,
            relevance=5.0,
            source_quality=sq,
            final_score=0.5 * 5.0 + 0.5 * sq,
            relevance_reason="curated classic",
        )
        try:
            summary = analyze_paper(score, client, config)
            summaries.append(summary)
        except Exception as exc:
            logger.warning("Analysis failed for '%s': %s", paper.title[:50], exc)
    return summaries


def build_summaries(papers: list[Paper], config: Config) -> list[PaperSummary]:
    """
    Two-pass pipeline:
      1. Screen all papers with Haiku → rank by final_score
      2. Full analysis of top-N with Sonnet
    Returns top-N PaperSummary objects sorted by final_score descending.
    """
    client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    logger.info("Stage 1: screening %d papers...", len(papers))
    scored = screen_papers(papers, client, config)
    scored.sort(key=lambda s: s.final_score, reverse=True)

    top = scored[: config.top_n]
    logger.info(
        "Stage 1 complete. Top %d scores: %s",
        len(top),
        [f"{s.paper.title[:30]}…({s.final_score:.2f})" for s in top],
    )

    logger.info("Stage 2: full analysis of top %d papers...", len(top))
    summaries = [analyze_paper(s, client, config) for s in top]

    return summaries
