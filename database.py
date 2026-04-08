import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
SOURCES_DIR = PROJECT_ROOT / "sources"
LOGS_DIR = PROJECT_ROOT / ".logs"
RUN_RESULTS_DIR = DATA_DIR / "runs"
DB_PATH = DATA_DIR / "crawler.sqlite3"
# Centralized config directory for runtime-controlled scrapers such as X.
CONFIG_DIR = PROJECT_ROOT / "config"
# Config file used to declare which X profiles should be treated as sources.
X_TARGETS_CONFIG_PATH = CONFIG_DIR / "x_targets.json"


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


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db() -> None:
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                link TEXT NOT NULL UNIQUE,
                folder_name TEXT NOT NULL UNIQUE,
                scraper_key TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at_utc TEXT NOT NULL,
                updated_at_utc TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at_utc TEXT NOT NULL,
                finished_at_utc TEXT,
                status TEXT NOT NULL,
                log_dir TEXT NOT NULL,
                results_path TEXT,
                total_sources INTEGER NOT NULL DEFAULT 0,
                succeeded_sources INTEGER NOT NULL DEFAULT 0,
                failed_sources INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS run_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                started_at_utc TEXT NOT NULL,
                finished_at_utc TEXT,
                status TEXT NOT NULL,
                log_path TEXT NOT NULL,
                output_path TEXT,
                total_current_count INTEGER NOT NULL DEFAULT 0,
                returned_count INTEGER NOT NULL DEFAULT 0,
                error_text TEXT,
                UNIQUE (run_id, source_id)
            );

            CREATE TABLE IF NOT EXISTS scraped_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
                -- The run_source row that most recently observed this item.
                run_source_id INTEGER REFERENCES run_sources(id) ON DELETE SET NULL,
                -- Stable scraper-level item id, e.g. tweet:https://x.com/<handle>/status/<id>.
                item_id TEXT NOT NULL,
                section TEXT NOT NULL,
                item_type TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                published_date TEXT NOT NULL,
                url TEXT NOT NULL,
                image TEXT NOT NULL,
                read_time TEXT NOT NULL,
                button_text TEXT NOT NULL,
                external INTEGER NOT NULL DEFAULT 0,
                source_page TEXT NOT NULL,
                raw_json TEXT NOT NULL,
                -- first_seen_at_utc never changes after the first insert.
                first_seen_at_utc TEXT NOT NULL,
                -- last_seen_at_utc is refreshed every time the item is observed again.
                last_seen_at_utc TEXT NOT NULL,
                -- Prevent duplicate storage for the same logical item under one source.
                UNIQUE (source_id, item_id)
            );
            """
        )


def _row_to_source(row: sqlite3.Row) -> SourceRecord:
    return SourceRecord(
        id=row["id"],
        name=row["name"],
        link=row["link"],
        folder_name=row["folder_name"],
        scraper_key=row["scraper_key"],
        enabled=bool(row["enabled"]),
        created_at_utc=row["created_at_utc"],
        updated_at_utc=row["updated_at_utc"],
    )


def ensure_source(name: str, link: str, scraper_key: str, enabled: bool = True) -> SourceRecord:
    now = utc_now_iso()
    with get_connection() as connection:
        existing = connection.execute(
            "SELECT * FROM sources WHERE name = ? OR link = ?",
            (name, link),
        ).fetchone()

        if existing:
            source_id = int(existing["id"])
            folder_name = existing["folder_name"] or build_source_folder_name(source_id, name)
            connection.execute(
                """
                UPDATE sources
                SET name = ?, link = ?, folder_name = ?, scraper_key = ?, enabled = ?, updated_at_utc = ?
                WHERE id = ?
                """,
                (name, link, folder_name, scraper_key, int(enabled), now, source_id),
            )
        else:
            cursor = connection.execute(
                """
                INSERT INTO sources (name, link, folder_name, scraper_key, enabled, created_at_utc, updated_at_utc)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (name, link, "", scraper_key, int(enabled), now, now),
            )
            source_id = int(cursor.lastrowid)
            folder_name = build_source_folder_name(source_id, name)
            connection.execute(
                "UPDATE sources SET folder_name = ? WHERE id = ?",
                (folder_name, source_id),
            )

        row = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return _row_to_source(row)


def normalize_x_source_link(value: str) -> str:
    # Source rows for X should normalize to the profile root so duplicates like
    # https://x.com/OpenAI and https://x.com/OpenAI/with_replies map to one source.
    parsed = urlparse(value.strip())
    path_parts = [part for part in parsed.path.split("/") if part]
    if not path_parts:
        raise ValueError(f"Invalid X account URL: {value}")
    handle = path_parts[0].lstrip("@")
    if not handle:
        raise ValueError(f"Invalid X account URL: {value}")
    return f"https://x.com/{handle}"


def load_x_targets_config() -> dict[str, Any]:
    # Missing config is treated as "no X accounts configured" so the rest of the
    # app keeps working without forcing every user to adopt the X scraper.
    if not X_TARGETS_CONFIG_PATH.exists():
        return {"defaults": {}, "auth": {}, "accounts": []}

    payload = json.loads(X_TARGETS_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{X_TARGETS_CONFIG_PATH} must contain a JSON object.")
    # These defaults keep downstream code simple by avoiding repeated existence checks.
    payload.setdefault("defaults", {})
    payload.setdefault("auth", {})
    payload.setdefault("accounts", [])
    return payload


def sync_configured_x_sources() -> None:
    # The X scraper is config-driven: each configured account becomes one source row.
    # This keeps the existing multi-source runner unchanged while allowing many X
    # handles to share a single scraper implementation.
    payload = load_x_targets_config()
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
        # If the user does not supply a name, derive a predictable source name from the handle.
        source_name = str(account.get("name") or f"X {handle}").strip()
        ensure_source(
            name=source_name,
            link=normalized_url,
            scraper_key="x_latest_posts",
            enabled=bool(account.get("enabled", True)),
        )


def seed_default_sources() -> None:
    ensure_source(
        name="Bellatrix Aerospace Updates",
        link="https://bellatrix.aero/updates",
        scraper_key="bellatrix_updates",
        enabled=True,
    )
    ensure_source(
        name="Digantara Newsroom",
        link="https://www.digantara.co.in/newsroom",
        scraper_key="digantara_newsroom",
        enabled=True,
    )
    ensure_source(
        name="Skyroot Newsroom",
        link="https://www.skyroot.in/newsroom",
        scraper_key="skyroot_newsroom",
        enabled=True,
    )
    ensure_source(
        name="NSIL News",
        link="https://www.nsilindia.co.in/news",
        scraper_key="nsil_news",
        enabled=True,
    )
    ensure_source(
        name="Pixxel Newsroom",
        link="https://www.pixxel.space/newsroom",
        scraper_key="pixxel_newsroom",
        enabled=True,
    )
    sync_configured_x_sources()


def get_enabled_sources() -> list[SourceRecord]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM sources WHERE enabled = 1 ORDER BY id ASC"
        ).fetchall()
        return [_row_to_source(row) for row in rows]


def get_all_sources() -> list[SourceRecord]:
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM sources ORDER BY id ASC").fetchall()
        return [_row_to_source(row) for row in rows]


def set_source_enabled(source_id: int, enabled: bool) -> None:
    with get_connection() as connection:
        connection.execute(
            "UPDATE sources SET enabled = ?, updated_at_utc = ? WHERE id = ?",
            (int(enabled), utc_now_iso(), source_id),
        )


def create_run(log_dir: Path, total_sources: int) -> int:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO runs (started_at_utc, status, log_dir, total_sources)
            VALUES (?, ?, ?, ?)
            """,
            (utc_now_iso(), "running", str(log_dir), total_sources),
        )
        return int(cursor.lastrowid)


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
            SET finished_at_utc = ?, status = ?, succeeded_sources = ?, failed_sources = ?, results_path = ?
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
            """,
            (run_id, source_id, utc_now_iso(), "running", str(log_path)),
        )
        return int(cursor.lastrowid)


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
            SET finished_at_utc = ?, status = ?, output_path = ?, total_current_count = ?, returned_count = ?, error_text = ?
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


def ensure_runtime_directories() -> None:
    # config/ is created alongside the existing runtime directories so the sample
    # X config can live inside the project without manual setup.
    for path in (DATA_DIR, SOURCES_DIR, LOGS_DIR, RUN_RESULTS_DIR, CONFIG_DIR):
        path.mkdir(parents=True, exist_ok=True)


def upsert_scraped_items(source_id: int, run_source_id: int, items: list[dict[str, Any]]) -> dict[str, int]:
    # The scraper state file prevents repeat returns across normal runs, but we also
    # persist items into SQLite. This helper makes SQLite idempotent by inserting new
    # items once and refreshing existing rows when the same item is seen again.
    if not items:
        return {"inserted": 0, "updated": 0}

    item_ids = [str(item["id"]) for item in items]
    placeholders = ", ".join("?" for _ in item_ids)
    now = utc_now_iso()

    with get_connection() as connection:
        # Preload existing keys so we can report how many rows were inserted vs updated.
        existing_rows = connection.execute(
            f"SELECT item_id FROM scraped_items WHERE source_id = ? AND item_id IN ({placeholders})",
            (source_id, *item_ids),
        ).fetchall()
        existing_ids = {row["item_id"] for row in existing_rows}

        for item in items:
            connection.execute(
                """
                INSERT INTO scraped_items (
                    source_id,
                    run_source_id,
                    item_id,
                    section,
                    item_type,
                    title,
                    description,
                    published_date,
                    url,
                    image,
                    read_time,
                    button_text,
                    external,
                    source_page,
                    raw_json,
                    first_seen_at_utc,
                    last_seen_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (source_id, item_id) DO UPDATE SET
                    -- Keep the latest run linkage and normalized payload in sync with
                    -- whatever the scraper currently sees for this item.
                    run_source_id = excluded.run_source_id,
                    section = excluded.section,
                    item_type = excluded.item_type,
                    title = excluded.title,
                    description = excluded.description,
                    published_date = excluded.published_date,
                    url = excluded.url,
                    image = excluded.image,
                    read_time = excluded.read_time,
                    button_text = excluded.button_text,
                    external = excluded.external,
                    source_page = excluded.source_page,
                    raw_json = excluded.raw_json,
                    last_seen_at_utc = excluded.last_seen_at_utc
                """,
                (
                    source_id,
                    run_source_id,
                    item["id"],
                    item.get("section", ""),
                    item.get("type", ""),
                    item.get("title", ""),
                    item.get("description", ""),
                    item.get("published_date", ""),
                    item.get("url", ""),
                    item.get("image", ""),
                    item.get("read_time", ""),
                    item.get("button_text", ""),
                    int(bool(item.get("external", False))),
                    item.get("source_page", ""),
                    json.dumps(item, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )

    # inserted/updated counts are useful in logs when checking whether a run truly
    # discovered new content or merely re-confirmed already-known items.
    inserted = sum(1 for item_id in item_ids if item_id not in existing_ids)
    updated = len(item_ids) - inserted
    return {"inserted": inserted, "updated": updated}
