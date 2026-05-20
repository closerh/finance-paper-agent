"""
SQLite persistence layer for papers, scores, and issue tracking.
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "data" / "papers.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS papers (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            title               TEXT NOT NULL,
            authors             TEXT,
            abstract            TEXT,
            url                 TEXT UNIQUE,
            pdf_url             TEXT,
            published           TEXT,
            source              TEXT,
            categories          TEXT,
            keywords            TEXT,
            score_relevance     REAL,
            score_source_quality REAL,
            score_final         REAL,
            fetch_date          TEXT NOT NULL,
            selected            INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS issues (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_number INTEGER UNIQUE NOT NULL,
            week_start   TEXT NOT NULL,
            run_date     TEXT NOT NULL
        );
        """)
    # Migrate old schema if needed
    existing = {row[1] for row in conn.execute("PRAGMA table_info(papers)")}
    for col, typedef in [
        ("score_source_quality", "REAL"),
        ("score_final",          "REAL"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {col} {typedef}")
    # Remove obsolete columns (SQLite can't DROP, so just leave them — they'll be empty)
    logger.info("Database ready: %s", DB_PATH)


def get_or_create_issue(week_start: str) -> int:
    """Return the issue number for this week, creating a new one if it doesn't exist."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT issue_number FROM issues WHERE week_start = ?", (week_start,)
        ).fetchone()
        if row:
            return row["issue_number"]
        max_row = conn.execute(
            "SELECT MAX(issue_number) AS n FROM issues"
        ).fetchone()
        next_num = (max_row["n"] or 0) + 1
        conn.execute(
            "INSERT INTO issues (issue_number, week_start, run_date) VALUES (?, ?, ?)",
            (next_num, week_start, datetime.utcnow().isoformat()),
        )
        return next_num


def upsert_papers(papers: list, selected_urls: set) -> None:
    fetch_date = datetime.utcnow().isoformat()
    with _connect() as conn:
        for p in papers:
            conn.execute(
                """
                INSERT INTO papers
                    (title, authors, abstract, url, pdf_url, published,
                     source, categories, fetch_date, selected)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    fetch_date = excluded.fetch_date,
                    selected   = excluded.selected
                """,
                (
                    p.title,
                    json.dumps(p.authors),
                    p.abstract,
                    p.url,
                    p.pdf_url,
                    p.published.isoformat() if p.published else None,
                    p.source,
                    json.dumps(p.categories),
                    fetch_date,
                    1 if p.url in selected_urls else 0,
                ),
            )


def update_paper_scores(url: str, keywords: list, scores: dict) -> None:
    with _connect() as conn:
        conn.execute(
            """
            UPDATE papers SET
                keywords             = ?,
                score_relevance      = ?,
                score_source_quality = ?,
                score_final          = ?
            WHERE url = ?
            """,
            (
                json.dumps(keywords),
                scores.get("relevance"),
                scores.get("source_quality"),
                scores.get("final_score"),
                url,
            ),
        )


def cumulative_stats() -> dict:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT source, COUNT(*) AS cnt FROM papers GROUP BY source ORDER BY cnt DESC"
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) AS n FROM papers").fetchone()["n"]
    return {
        "total": total,
        "by_source": [{"source": r["source"], "count": r["cnt"]} for r in rows],
    }
