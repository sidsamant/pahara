import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DATA_DIR = PROJECT_ROOT / "data"
SOURCES_DIR = PROJECT_ROOT / "sources"
LOGS_DIR = PROJECT_ROOT / ".logs"
RUN_RESULTS_DIR = DATA_DIR / "runs"
DB_PATH = DATA_DIR / "crawler.sqlite3"


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


def seed_default_sources() -> None:
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
    for path in (DATA_DIR, SOURCES_DIR, LOGS_DIR, RUN_RESULTS_DIR):
        path.mkdir(parents=True, exist_ok=True)
