"""
Database access layer for the crawl4ai crawler.

Backed by PostgreSQL. Connection URL is read from the DATABASE_URL environment
variable (e.g. "postgresql://user:password@localhost:5432/crawl4ai").

All public functions open their own connection, commit on success, and roll
back + close on any exception. Callers use the context manager pattern:

    with get_connection() as conn:
        rows = conn.execute("SELECT ...", (...)).fetchall()

Row dictionaries are standard Python dicts (via RealDictCursor), so field
access is identical to the previous sqlite3.Row style: row["field_name"].
"""

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import psycopg2
import psycopg2.extras

from src.utils.util import item_hashcode


# ---------------------------------------------------------------------------
# Directory constants (database is now remote — no DATA_DIR or DB_PATH)
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR  = PROJECT_ROOT / ".output" / "sources"
LOGS_DIR     = PROJECT_ROOT / ".logs"
RUN_RESULTS_DIR = PROJECT_ROOT / ".output" / "runs"
CONFIG_DIR   = PROJECT_ROOT / "src" / "config"

# Config file used to declare which X profiles should be treated as sources.
X_TARGETS_CONFIG_PATH = CONFIG_DIR / "x_targets.json"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceRecord:
    id: int
    name: str
    link: str
    folder_name: str
    scraper_key: str
    enabled: bool
    created_at_utc: str
    updated_at_utc: str


# ---------------------------------------------------------------------------
# Internal connection wrapper
# ---------------------------------------------------------------------------

class _Conn:
    """
    Thin wrapper around a psycopg2 connection that exposes the same
    .execute(sql, params) interface used by sqlite3.Connection.

    It automatically converts sqlite3-style "?" placeholders to the
    psycopg2-style "%s" placeholders so the SQL strings in this module
    do not need to change.
    """

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Any = ()) -> Any:
        # Replace sqlite3 "?" placeholders with psycopg2 "%s" placeholders.
        pg_sql = sql.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(pg_sql, params)
        return cur


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------

@contextmanager
def get_connection():
    """
    Yield a _Conn wrapping a psycopg2 connection.

    Commits on clean exit, rolls back + re-raises on any exception,
    and always closes the underlying connection.

    DATABASE_URL must be set in the environment, for example:
        postgresql://user:password@localhost:5432/crawl4ai
    """
    raw_conn = psycopg2.connect(
        os.environ["DATABASE_URL"],
        # RealDictCursor returns rows as plain Python dicts, so row["field"]
        # works exactly like it did with sqlite3.Row.
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    conn = _Conn(raw_conn)
    try:
        yield conn
        raw_conn.commit()
    except Exception:
        raw_conn.rollback()
        raise
    finally:
        raw_conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def camel_case_name(value: str) -> str:
    parts = ["".join(ch for ch in chunk if ch.isalnum()) for chunk in value.split()]
    return "".join(part[:1].upper() + part[1:] for part in parts if part) or "Source"


def short_source_name(value: str) -> str:
    words = [word for word in value.split() if word]
    filtered = [word for word in words if word.lower() not in {"news", "newsroom"}]
    chosen = filtered if filtered else words
    return " ".join(chosen) or value


def build_source_folder_name(source_id: int, source_name: str) -> str:
    return f"{source_id}_{camel_case_name(short_source_name(source_name))}"


def _row_to_source(row: dict) -> SourceRecord:
    return SourceRecord(
        id=row["id"],
        name=row["name"],
        link=row["link"],
        folder_name=row["folder_name"],
        scraper_key=row["scraper_key"],
        # PostgreSQL returns INTEGER columns as Python int; cast to bool for clarity.
        enabled=bool(row["enabled"]),
        created_at_utc=row["created_at_utc"],
        updated_at_utc=row["updated_at_utc"],
    )


# ---------------------------------------------------------------------------
# Source management
# ---------------------------------------------------------------------------

def ensure_source(name: str, link: str, scraper_key: str, enabled: bool = True) -> SourceRecord:
    """Insert or update a source row and return it."""
    now = utc_now_iso()
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT * FROM sources WHERE name = ? OR link = ?",
            (name, link),
        ).fetchone()

        if existing:
            source_id  = int(existing["id"])
            folder_name = existing["folder_name"] or build_source_folder_name(source_id, name)
            connection.execute(
                """
                UPDATE sources
                SET name = ?, link = ?, folder_name = ?, scraper_key = ?,
                    enabled = ?, updated_at_utc = ?
                WHERE id = ?
                """,
                (name, link, folder_name, scraper_key, int(enabled), now, source_id),
            )
        else:
            # INSERT and get the new auto-generated id in one round-trip via RETURNING.
            cursor = connection.execute(
                """
                INSERT INTO sources
                    (name, link, folder_name, scraper_key, enabled, created_at_utc, updated_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (name, link, "", scraper_key, int(enabled), now, now),
            )
            source_id = int(cursor.fetchone()["id"])
            folder_name = build_source_folder_name(source_id, name)
            connection.execute(
                "UPDATE sources SET folder_name = ? WHERE id = ?",
                (folder_name, source_id),
            )

        row = connection.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        return _row_to_source(row)


def normalize_x_source_link(value: str) -> str:
    # Normalise X profile URLs so https://x.com/OpenAI and
    # https://x.com/OpenAI/with_replies map to the same source row.
    parsed = urlparse(value.strip())
    path_parts = [part for part in parsed.path.split("/") if part]
    if not path_parts:
        raise ValueError(f"Invalid X account URL: {value}")
    handle = path_parts[0].lstrip("@")
    if not handle:
        raise ValueError(f"Invalid X account URL: {value}")
    return f"https://x.com/{handle}"


def load_x_targets_config() -> dict[str, Any]:
    if not X_TARGETS_CONFIG_PATH.exists():
        return {"defaults": {}, "auth": {}, "accounts": []}

    payload = json.loads(X_TARGETS_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{X_TARGETS_CONFIG_PATH} must contain a JSON object.")
    payload.setdefault("defaults", {})
    payload.setdefault("auth", {})
    payload.setdefault("accounts", [])
    return payload


def sync_configured_x_sources() -> None:
    payload  = load_x_targets_config()
    accounts = payload.get("accounts", [])
    if not isinstance(accounts, list):
        raise ValueError(f"{X_TARGETS_CONFIG_PATH} field 'accounts' must be a JSON array.")

    for account in accounts:
        if not isinstance(account, dict):
            raise ValueError(f"{X_TARGETS_CONFIG_PATH} entries in 'accounts' must be JSON objects.")
        raw_url = str(account.get("url", "")).strip()
        if not raw_url:
            raise ValueError("Each configured X account must include a non-empty 'url'.")
        normalized_url = normalize_x_source_link(raw_url)
        handle = normalized_url.rsplit("/", 1)[-1]
        source_name = str(account.get("name") or f"X {handle}").strip()
        ensure_source(
            name=source_name,
            link=normalized_url,
            scraper_key="x_latest_posts",
            enabled=bool(account.get("enabled", True)),
        )


def get_enabled_sources() -> list[SourceRecord]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM sources WHERE enabled = 1 ORDER BY id ASC"
        ).fetchall()
        return [_row_to_source(row) for row in rows]


def get_all_sources() -> list[SourceRecord]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM sources ORDER BY id ASC"
        ).fetchall()
        return [_row_to_source(row) for row in rows]


def set_source_enabled(source_id: int, enabled: bool) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE sources SET enabled = ?, updated_at_utc = ? WHERE id = ?",
            (int(enabled), utc_now_iso(), source_id),
        )


# ---------------------------------------------------------------------------
# Run tracking
# ---------------------------------------------------------------------------

def create_run(log_dir: Path, total_sources: int) -> int:
    with get_connection() as connection:
        # RETURNING id avoids a separate SELECT to get the auto-generated PK.
        cursor = connection.execute(
            """
            INSERT INTO runs (started_at_utc, status, log_dir, total_sources)
            VALUES (?, ?, ?, ?)
            RETURNING id
            """,
            (utc_now_iso(), "running", str(log_dir), total_sources),
        )
        return int(cursor.fetchone()["id"])


def finalize_run(
    run_id: int,
    status: str,
    succeeded_sources: int,
    failed_sources: int,
    results_path: Path | None,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE runs
            SET finished_at_utc = ?, status = ?,
                succeeded_sources = ?, failed_sources = ?, results_path = ?
            WHERE id = ?
            """,
            (
                utc_now_iso(),
                status,
                succeeded_sources,
                failed_sources,
                str(results_path) if results_path else None,
                run_id,
            ),
        )


def create_run_source(run_id: int, source_id: int, log_path: Path) -> int:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO run_sources (run_id, source_id, started_at_utc, status, log_path)
            VALUES (?, ?, ?, ?, ?)
            RETURNING id
            """,
            (run_id, source_id, utc_now_iso(), "running", str(log_path)),
        )
        return int(cursor.fetchone()["id"])


def finalize_run_source(
    run_source_id: int,
    status: str,
    output_path: Path | None,
    total_current_count: int,
    returned_count: int,
    error_text: str | None,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE run_sources
            SET finished_at_utc = ?, status = ?, output_path = ?,
                total_current_count = ?, returned_count = ?, error_text = ?
            WHERE id = ?
            """,
            (
                utc_now_iso(),
                status,
                str(output_path) if output_path else None,
                total_current_count,
                returned_count,
                error_text,
                run_source_id,
            ),
        )


# ---------------------------------------------------------------------------
# Runtime directories (database is remote; only local filesystem paths here)
# ---------------------------------------------------------------------------

def ensure_runtime_directories() -> None:
    for path in (SOURCES_DIR, LOGS_DIR, RUN_RESULTS_DIR, CONFIG_DIR):
        path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Schema migrations
# ---------------------------------------------------------------------------

def migrate_db() -> None:
    """
    Add any columns that are missing from the live schema.

    Uses information_schema (standard SQL) instead of the SQLite-specific
    sqlite_master table and PRAGMA table_info().
    """
    with get_connection() as connection:
        # Check whether the scraped_items table exists at all.
        tables = {
            row["table_name"]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                """
            ).fetchall()
        }
        if "scraped_items" not in tables:
            return

        # Fetch existing column names so we can add only the missing ones.
        cols = {
            row["column_name"]
            for row in connection.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'scraped_items'
                  AND table_schema = 'public'
                """
            ).fetchall()
        }

        if "scrapper_type" not in cols:
            connection.execute(
                "ALTER TABLE scraped_items ADD COLUMN scrapper_type TEXT NOT NULL DEFAULT ''"
            )
        if "hashcode" not in cols:
            connection.execute(
                "ALTER TABLE scraped_items ADD COLUMN hashcode TEXT NOT NULL DEFAULT ''"
            )

        # CREATE INDEX IF NOT EXISTS is standard SQL and works in PostgreSQL.
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_scraped_items_scrapper_type_hashcode
            ON scraped_items (scrapper_type, hashcode)
            """
        )


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def filter_unseen_items(scrapper_type: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only items whose URL hash has not been stored before."""
    if not items:
        return []

    hashes = [item_hashcode(item) for item in items]

    with get_connection() as connection:
        # ANY(%s) with a Python list is idiomatic psycopg2 and avoids building a
        # dynamic "IN (?, ?, ...)" placeholder string.
        rows = connection.execute(
            """
            SELECT hashcode
            FROM scraped_items
            WHERE scrapper_type = ? AND hashcode = ANY(?)
            """,
            (scrapper_type, hashes),
        ).fetchall()

    seen = {row["hashcode"] for row in rows}
    return [item for item, h in zip(items, hashes) if h not in seen]


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def upsert_scraped_items(
    source_id: int,
    run_source_id: int,
    scraper_key: str,
    items: list[dict[str, Any]],
) -> dict[str, int]:
    """
    Insert new scraped items; update mutable fields if an item was seen before.

    Returns {"inserted": N, "updated": M}.
    """
    if not items:
        return {"inserted": 0, "updated": 0}

    item_ids = [str(item["id"]) for item in items]
    now = utc_now_iso()

    with get_connection() as connection:
        # Fetch all item_ids that already exist so we can report insert vs update.
        existing_rows = connection.execute(
            """
            SELECT item_id
            FROM scraped_items
            WHERE source_id = ? AND item_id = ANY(?)
            """,
            (source_id, item_ids),
        ).fetchall()
        existing_ids = {row["item_id"] for row in existing_rows}

        for item in items:
            raw_json = json.dumps(item, ensure_ascii=False, sort_keys=True)
            hashcode  = item_hashcode(item)

            # ON CONFLICT ... DO UPDATE is standard SQL (PostgreSQL 9.5+).
            # "excluded" is the special alias for the row that failed to insert.
            connection.execute(
                """
                INSERT INTO scraped_items (
                    source_id, run_source_id, item_id, section, item_type,
                    scrapper_type, title, description, published_date, url,
                    image, read_time, button_text, external, source_page,
                    raw_json, hashcode, first_seen_at_utc, last_seen_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (source_id, item_id) DO UPDATE SET
                    run_source_id    = excluded.run_source_id,
                    section          = excluded.section,
                    item_type        = excluded.item_type,
                    scrapper_type    = excluded.scrapper_type,
                    title            = excluded.title,
                    description      = excluded.description,
                    published_date   = excluded.published_date,
                    url              = excluded.url,
                    image            = excluded.image,
                    read_time        = excluded.read_time,
                    button_text      = excluded.button_text,
                    external         = excluded.external,
                    source_page      = excluded.source_page,
                    raw_json         = excluded.raw_json,
                    hashcode         = excluded.hashcode,
                    last_seen_at_utc = excluded.last_seen_at_utc
                """,
                (
                    source_id,
                    run_source_id,
                    item["id"],
                    item.get("section", ""),
                    item.get("type", ""),
                    scraper_key,
                    item.get("title", ""),
                    item.get("description", ""),
                    item.get("published_date", ""),
                    item.get("url", ""),
                    item.get("image", ""),
                    item.get("read_time", ""),
                    item.get("button_text", ""),
                    int(bool(item.get("external", False))),
                    item.get("source_page", ""),
                    raw_json,
                    hashcode,
                    now,
                    now,
                ),
            )

    inserted = sum(1 for iid in item_ids if iid not in existing_ids)
    updated  = len(item_ids) - inserted
    return {"inserted": inserted, "updated": updated}
