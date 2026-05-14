"""
Database layer for the Scout → Forge → Sentinel agent pipeline.

Two tables are managed here (defined in schema.sql):
  discovery_targets  — one row per organisation discovered by Scout;
                       updated by Forge (scraper_file, status) and
                       by Sentinel (status after review).
  scraper_reviews    — one row per Sentinel review; multiple rows can
                       exist for the same target across revision cycles.

All functions reuse the same get_connection() context manager as the
rest of the project, so the DATABASE_URL env var must be set before
any function here is called.
"""

import json
from datetime import datetime, timezone
from typing import Any

from src.database import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# discovery_targets helpers
# ---------------------------------------------------------------------------

def insert_discovery_target(
    *,
    org_name: str,
    website: str,
    newsroom_url: str,
    url_pattern: str,
    scraper_key: str,
    page_hints: dict | None = None,
) -> int:
    """
    Insert a new discovery target row and return its id.

    If a row with the same scraper_key already exists (e.g. Scout is
    re-run), the non-status fields are updated in place so Scout is
    safe to run repeatedly without creating duplicate rows.
    """
    now = _now()
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO discovery_targets
                (org_name, website, newsroom_url, url_pattern, page_hints,
                 scraper_key, scraper_file, status, discovered_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, '', 'discovered', ?, ?)
            ON CONFLICT (scraper_key) DO UPDATE SET
                org_name     = excluded.org_name,
                website      = excluded.website,
                newsroom_url = excluded.newsroom_url,
                url_pattern  = excluded.url_pattern,
                page_hints   = excluded.page_hints,
                updated_at   = excluded.updated_at
            RETURNING id
            """,
            (
                org_name,
                website,
                newsroom_url,
                url_pattern,
                json.dumps(page_hints or {}),
                scraper_key,
                now,
                now,
            ),
        )
        return int(cursor.fetchone()["id"])


def get_targets_by_status(status: str) -> list[dict[str, Any]]:
    """Return all discovery_targets rows with the given status, oldest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM discovery_targets WHERE status = ? ORDER BY id ASC",
            (status,),
        ).fetchall()
        return [dict(row) for row in rows]


def update_target(target_id: int, **fields: Any) -> None:
    """
    Update arbitrary columns on a discovery_targets row.
    updated_at is always refreshed automatically.

    Usage:
        update_target(42, status="code_ready", scraper_file="src/scrapers/foo.py")
    """
    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [target_id]
    with get_connection() as conn:
        conn.execute(
            f"UPDATE discovery_targets SET {set_clause} WHERE id = ?",
            values,
        )


def get_all_targets() -> list[dict[str, Any]]:
    """Return every row in discovery_targets, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM discovery_targets ORDER BY id DESC"
        ).fetchall()
        return [dict(row) for row in rows]


def get_existing_source_links() -> set[str]:
    """
    Return the set of normalised source link URLs already in the sources table.

    Normalised means: lowercase, trailing slash stripped. Used by Forge to avoid
    regenerating scrapers for newsroom URLs that are already covered by a source.
    """
    with get_connection() as conn:
        rows = conn.execute("SELECT link FROM sources").fetchall()
        return {row["link"].rstrip("/").lower() for row in rows}


# ---------------------------------------------------------------------------
# scraper_reviews helpers
# ---------------------------------------------------------------------------

def insert_scraper_review(
    *,
    target_id: int,
    scraper_file: str,
    verdict: str,
    issues: list[str],
    suggestions: list[str],
) -> int:
    """
    Record a Sentinel review for a discovery_targets row.
    Returns the new review row id.

    verdict must be "approved" or "needs_revision".
    issues and suggestions are stored as JSON arrays.
    """
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO scraper_reviews
                (target_id, scraper_file, verdict, issues, suggestions, reviewed_at)
            VALUES (?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                target_id,
                scraper_file,
                verdict,
                json.dumps(issues),
                json.dumps(suggestions),
                _now(),
            ),
        )
        return int(cursor.fetchone()["id"])


def get_reviews_for_target(target_id: int) -> list[dict[str, Any]]:
    """Return all Sentinel reviews for a target, newest first."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM scraper_reviews
            WHERE target_id = ?
            ORDER BY reviewed_at DESC
            """,
            (target_id,),
        ).fetchall()
        return [dict(row) for row in rows]
