"""
Finance & Economics Weekly Paper Agent

Two-phase operation:
  --prepare   Fetch, score, render, download PDFs → save to data/queue/{week}/
  --send      Load the prepared package and email it

Legacy single-shot:
  (no flag)   Prepare + send in one go (original behaviour)
"""

import json
import logging
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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

QUEUE_DIR = Path(__file__).parent / "data" / "queue"

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


MAX_PDF_BYTES = 8 * 1024 * 1024
MAX_TOTAL_ATTACH_BYTES = 15 * 1024 * 1024


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
# Shared helper: download PDFs up to budget, return list of (filename, bytes)
# ---------------------------------------------------------------------------

def _collect_pdfs(summaries) -> list[tuple[str, bytes]]:
    attachments: list[tuple[str, bytes]] = []
    total = 0
    for s in summaries:
        if total >= MAX_TOTAL_ATTACH_BYTES:
            logger.info("Attachment budget reached — skipping remaining PDFs.")
            break
        result = download_pdf(s.paper.pdf_url, s.paper.title)
        if result:
            filename, data = result
            if total + len(data) > MAX_TOTAL_ATTACH_BYTES:
                logger.warning("Skipping PDF (would exceed budget): %s", filename)
                continue
            attachments.append(result)
            total += len(data)
            logger.info("PDF ready: %s (%.1f MB)", filename, len(data) / 1e6)
    return attachments


# ---------------------------------------------------------------------------
# Phase 1: prepare — fetch, score, render, save to queue
# ---------------------------------------------------------------------------

def prepare(dry_run: bool = False) -> None:
    logger.info("=== Finance Paper Agent — PREPARE starting ===")
    config = load_config()

    database.init_db()
    ws = week_start_date().strftime("%Y-%m-%d")
    package_dir = QUEUE_DIR / ws
    meta_path = package_dir / "meta.json"

    # Idempotency: skip if already prepared (or already sent) this week
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        status = meta.get("status", "")
        logger.info("Week %s already has status=%s — skipping prepare.", ws, status)
        return

    issue_number = database.get_or_create_issue(ws)

    _selected_unused, all_papers, source_counts = fetch_top_papers(config)
    if not all_papers:
        logger.error("No papers fetched — aborting.")
        sys.exit(1)

    database.upsert_papers(all_papers, set())

    summaries = build_summaries(all_papers, config)
    if not summaries:
        logger.error("No papers survived scoring — aborting.")
        sys.exit(1)

    run_stats = {
        "total": source_counts["total"],
        "selected": len(summaries),
        "by_source": [
            {"source": src, "count": cnt}
            for src, cnt in source_counts.items()
            if src not in ("total", "selected") and cnt > 0
        ],
    }

    selected_urls = {s.paper.url for s in summaries}
    database.upsert_papers([s.paper for s in summaries], selected_urls)
    for s in summaries:
        database.update_paper_scores(s.paper.url, s.keywords, {
            "relevance": round(s.relevance),
            "source_quality": round(s.source_quality),
            "final_score": s.final_score,
        })

    db_stats = database.cumulative_stats()
    html_body = render_email(summaries, issue_number, run_stats, db_stats)

    monday = week_start_date()
    subject = f"Weekly QIS Papers | {monday.strftime('%b %d, %Y')}  Issue #{issue_number}"

    if dry_run:
        preview_path = LOG_DIR / f"preview_{datetime.now().strftime('%Y%m%d')}.html"
        preview_path.write_text(html_body, encoding="utf-8")
        logger.info("[DRY RUN] Package not saved. HTML preview: %s", preview_path)
        return

    # Save package
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "email_body.html").write_text(html_body, encoding="utf-8")

    attach_dir = package_dir / "attachments"
    attach_dir.mkdir(exist_ok=True)
    for filename, data in _collect_pdfs(summaries):
        (attach_dir / filename).write_bytes(data)

    meta = {
        "status": "ready",
        "week_start": ws,
        "subject": subject,
        "issue_number": issue_number,
        "prepared_at": datetime.now().isoformat(),
        "sent_at": None,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    # Also save HTML preview to logs/
    preview_path = LOG_DIR / f"preview_{datetime.now().strftime('%Y%m%d')}.html"
    preview_path.write_text(html_body, encoding="utf-8")

    logger.info("Package saved to: %s", package_dir)
    logger.info("=== PREPARE finished successfully ===")


# ---------------------------------------------------------------------------
# Phase 2: send — load from queue and email
# ---------------------------------------------------------------------------

def send_prepared(dry_run: bool = False) -> None:
    logger.info("=== Finance Paper Agent — SEND starting ===")

    if not QUEUE_DIR.exists():
        logger.error("Queue directory not found (%s). Run --prepare first.", QUEUE_DIR)
        sys.exit(1)

    # Find most recent "ready" package
    candidates: list[tuple[Path, dict]] = []
    for folder in QUEUE_DIR.iterdir():
        if not folder.is_dir():
            continue
        meta_path = folder / "meta.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("status") == "ready":
            candidates.append((folder, meta))

    if not candidates:
        logger.error("No prepared (unsent) packages found in queue. Run --prepare first.")
        sys.exit(1)

    candidates.sort(key=lambda x: x[1]["week_start"], reverse=True)
    package_dir, meta = candidates[0]
    logger.info("Sending package: %s (prepared %s)", meta["week_start"], meta.get("prepared_at", "?"))

    html_body = (package_dir / "email_body.html").read_text(encoding="utf-8")

    attachments: list[tuple[str, bytes]] = []
    attach_dir = package_dir / "attachments"
    if attach_dir.exists():
        for pdf_path in sorted(attach_dir.glob("*.pdf")):
            attachments.append((pdf_path.name, pdf_path.read_bytes()))
            logger.info("Loaded PDF: %s (%.1f MB)", pdf_path.name, pdf_path.stat().st_size / 1e6)

    subject = meta["subject"]
    config = load_config()

    if dry_run:
        logger.info("[DRY RUN] Would send: '%s' with %d PDF(s) — skipped.", subject, len(attachments))
        return

    send_email(subject, html_body, config, attachments)

    meta["status"] = "sent"
    meta["sent_at"] = datetime.now().isoformat()
    (package_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info("Email sent. Package marked as sent.")
    logger.info("=== SEND finished successfully ===")


# ---------------------------------------------------------------------------
# Legacy: prepare + send in one shot
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

    # 4. Download PDFs
    attachments = _collect_pdfs(summaries)

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

    attachments = _collect_pdfs(summaries)

    subject = f"Special Issue: QIS Classics | {len(summaries)} Must-Read Papers"
    if dry_run:
        logger.info("[DRY RUN] Would send: '%s' with %d PDF(s) — email skipped.", subject, len(attachments))
    else:
        send_email(subject, html_body, config, attachments)

    logger.info("=== Special Issue finished successfully ===")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    if "--special-issue" in sys.argv:
        run_special_issue(dry_run=dry_run)
    elif "--prepare" in sys.argv:
        prepare(dry_run=dry_run)
    elif "--send" in sys.argv:
        send_prepared(dry_run=dry_run)
    else:
        run(dry_run=dry_run)
