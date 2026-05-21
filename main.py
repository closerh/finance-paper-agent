"""
Finance & Economics Weekly Paper Agent
Fetches top 5 papers, generates English summaries + scores via Claude,
and emails an HTML digest with PDF attachments every Monday.
"""

import json
import logging
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup, escape

import database
from config import load_config
from emailer import send_email
from fetcher import fetch_top_papers
from fetcher import Paper
from summarizer import analyze_papers_directly, build_summaries

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "agent.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def week_start_date() -> datetime:
    """Return the Monday of the current week."""
    now = datetime.now()
    return now - timedelta(days=now.weekday())


def week_label() -> str:
    monday = week_start_date()
    sunday = monday + timedelta(days=6)
    return f"{monday.strftime('%b %d')} – {sunday.strftime('%b %d, %Y')}"


def nl2br(value: str) -> Markup:
    """Jinja2 filter: escape text then convert newlines to <br> tags."""
    return Markup(str(escape(value)).replace("\n", "<br>\n"))


def render_email(
    summaries, issue_number: int, run_stats: dict, db_stats: dict,
    is_special_issue: bool = False,
) -> str:
    template_dir = Path(__file__).parent
    env = Environment(loader=FileSystemLoader(str(template_dir)), autoescape=True)
    env.filters["nl2br"] = nl2br
    template = env.get_template("template.html")
    return template.render(
        week_label=week_label(),
        issue_number=issue_number,
        items=summaries,
        run_stats=run_stats,
        db_stats=db_stats,
        is_special_issue=is_special_issue,
    )


MAX_PDF_BYTES = 8 * 1024 * 1024   # 8 MB per PDF
MAX_TOTAL_ATTACH_BYTES = 15 * 1024 * 1024  # 15 MB total (base64 overhead ~+33% → ~20 MB on wire)


def download_pdf(url: str, title: str) -> tuple[str, bytes] | None:
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "FinancePaperAgent/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read(MAX_PDF_BYTES + 1)
        if len(data) > MAX_PDF_BYTES:
            logger.warning("PDF too large (>10 MB), skipping: %s", title[:50])
            return None
        safe = re.sub(r"[^\w\s-]", "", title[:50]).strip().replace(" ", "_")
        return f"{safe}.pdf", data
    except Exception as exc:
        logger.warning("PDF download failed for '%s': %s", title[:50], exc)
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(dry_run: bool = False) -> None:
    logger.info("=== Finance Paper Agent starting ===")
    config = load_config()

    # Init DB
    database.init_db()
    ws = week_start_date().strftime("%Y-%m-%d")
    issue_number = database.get_or_create_issue(ws)

    # 1. Fetch papers — build_summaries selects top-N via Claude scoring
    _selected_unused, all_papers, source_counts = fetch_top_papers(config)
    if not all_papers:
        logger.error("No papers fetched — aborting.")
        sys.exit(1)

    # Persist all fetched papers to DB first (scores updated after step 2)
    database.upsert_papers(all_papers, set())

    # 2. Two-pass scoring + analysis: screen all → analyze top-N
    summaries = build_summaries(all_papers, config)
    if not summaries:
        logger.error("No papers survived scoring — aborting.")
        sys.exit(1)

    # Build run_stats with per-source breakdown for template
    run_stats = {
        "total": source_counts["total"],
        "selected": len(summaries),
        "by_source": [
            {"source": src, "count": cnt}
            for src, cnt in source_counts.items()
            if src not in ("total", "selected") and cnt > 0
        ],
    }

    # Mark selected papers in DB
    selected_urls = {s.paper.url for s in summaries}
    database.upsert_papers([s.paper for s in summaries], selected_urls)

    # Persist scores to DB
    for s in summaries:
        database.update_paper_scores(s.paper.url, s.keywords, {
            "relevance": round(s.relevance),
            "source_quality": round(s.source_quality),
            "final_score": s.final_score,
        })

    db_stats = database.cumulative_stats()

    # 3. Render HTML
    html_body = render_email(summaries, issue_number, run_stats, db_stats)

    preview_path = LOG_DIR / f"preview_{datetime.now().strftime('%Y%m%d')}.html"
    preview_path.write_text(html_body, encoding="utf-8")
    logger.info("HTML preview saved: %s", preview_path)

    # 4. Download PDFs (respect Gmail 25 MB wire limit)
    attachments: list[tuple[str, bytes]] = []
    total_attach = 0
    for s in summaries:
        if total_attach >= MAX_TOTAL_ATTACH_BYTES:
            logger.info("Attachment budget reached — skipping remaining PDFs.")
            break
        result = download_pdf(s.paper.pdf_url, s.paper.title)
        if result:
            filename, data = result
            if total_attach + len(data) > MAX_TOTAL_ATTACH_BYTES:
                logger.warning("Skipping PDF (would exceed budget): %s", filename)
                continue
            attachments.append(result)
            total_attach += len(data)
            logger.info("PDF ready: %s (%.1f MB)", filename, len(data) / 1e6)

    # 5. Send email (or skip in dry-run mode)
    monday = week_start_date()
    subject = f"Weekly QIS Papers | {monday.strftime('%b %d, %Y')}  Issue #{issue_number}"
    if dry_run:
        logger.info("[DRY RUN] Would send: '%s' with %d PDF(s) — email skipped.", subject, len(attachments))
    else:
        send_email(subject, html_body, config, attachments)

    logger.info("=== Agent finished successfully ===")


# ---------------------------------------------------------------------------
# Special Issue: QIS Classics
# ---------------------------------------------------------------------------

def load_classics() -> list[Paper]:
    """Load the curated classics list from classics.json."""
    path = Path(__file__).parent / "classics.json"
    items = json.loads(path.read_text(encoding="utf-8"))
    papers = []
    for item in items:
        published = datetime.fromisoformat(item["published"]).replace(tzinfo=timezone.utc)
        papers.append(Paper(
            title=item["title"],
            authors=item["authors"],
            abstract=item["abstract"],
            url=item["url"],
            published=published,
            source=item["source"],
            categories=item["categories"],
            pdf_url=item.get("pdf_url"),
        ))
    return papers


def run_special_issue(dry_run: bool = False) -> None:
    logger.info("=== Finance Paper Agent — Special Issue starting ===")
    config = load_config()

    database.init_db()
    issue_number = database.get_or_create_issue("special-classics-v1")

    papers = load_classics()
    logger.info("Loaded %d classics", len(papers))

    summaries = analyze_papers_directly(papers, config)
    if not summaries:
        logger.error("No summaries generated — aborting.")
        sys.exit(1)

    selected_urls = {s.paper.url for s in summaries}
    database.upsert_papers(papers, selected_urls)
    for s in summaries:
        database.update_paper_scores(s.paper.url, s.keywords, {
            "relevance": round(s.relevance),
            "source_quality": round(s.source_quality),
            "final_score": s.final_score,
        })

    run_stats = {
        "total": len(papers),
        "selected": len(summaries),
        "by_source": [],
    }
    db_stats = database.cumulative_stats()

    html_body = render_email(summaries, issue_number, run_stats, db_stats, is_special_issue=True)

    preview_path = LOG_DIR / f"preview_special_{datetime.now().strftime('%Y%m%d')}.html"
    preview_path.write_text(html_body, encoding="utf-8")
    logger.info("HTML preview saved: %s", preview_path)

    attachments: list[tuple[str, bytes]] = []
    total_attach = 0
    for s in summaries:
        if total_attach >= MAX_TOTAL_ATTACH_BYTES:
            break
        result = download_pdf(s.paper.pdf_url, s.paper.title)
        if result:
            filename, data = result
            if total_attach + len(data) > MAX_TOTAL_ATTACH_BYTES:
                logger.warning("Skipping PDF (would exceed budget): %s", filename)
                continue
            attachments.append(result)
            total_attach += len(data)
            logger.info("PDF ready: %s (%.1f MB)", filename, len(data) / 1e6)

    subject = f"Special Issue: QIS Classics | {len(summaries)} Must-Read Papers"
    if dry_run:
        logger.info("[DRY RUN] Would send: '%s' with %d PDF(s) — email skipped.", subject, len(attachments))
    else:
        send_email(subject, html_body, config, attachments)

    logger.info("=== Special Issue finished successfully ===")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    special_issue = "--special-issue" in sys.argv
    if special_issue:
        run_special_issue(dry_run=dry_run)
    else:
        run(dry_run=dry_run)
