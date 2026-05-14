import argparse

# Load DATABASE_URL from a local .env file before any database import.
from dotenv import load_dotenv
load_dotenv()

from src.database import ensure_runtime_directories, get_connection, migrate_db
from src.utils.util import ScrapperType, item_hashcode, scrapper_type_from_item_id


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One-time setup and maintenance tasks for the crawler database."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "migrate",
        help="Apply any pending schema migrations to the PostgreSQL database.",
    )
    subparsers.add_parser(
        "backfill",
        help=(
            "Populate empty scrapper_type and hashcode values in scraped_items. "
            "Safe to re-run; only rows with at least one empty field are touched."
        ),
    )
    return parser


def cmd_migrate() -> None:
    migrate_db()
    print("Migration complete.")


def cmd_backfill() -> None:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                si.id,
                si.item_id,
                si.title,
                si.description,
                si.published_date,
                si.url,
                si.image,
                si.read_time,
                si.button_text,
                si.external,
                si.source_page,
                si.scrapper_type,
                si.hashcode,
                s.scraper_key
            FROM scraped_items si
            JOIN sources s ON s.id = si.source_id
            WHERE si.scrapper_type = '' OR si.hashcode = ''
            """
        ).fetchall()

        updated = 0
        for row in rows:
            scrapper_type = row["scrapper_type"]
            hashcode = row["hashcode"]

            if not scrapper_type:
                inferred = scrapper_type_from_item_id(row["item_id"])
                if inferred:
                    scrapper_type = inferred.value
                else:
                    try:
                        scrapper_type = ScrapperType(row["scraper_key"]).value
                    except ValueError:
                        scrapper_type = row["scraper_key"]

            if not hashcode:
                hashcode = item_hashcode(dict(row))

            connection.execute(
                "UPDATE scraped_items SET scrapper_type = ?, hashcode = ? WHERE id = ?",
                (scrapper_type, hashcode, row["id"]),
            )
            updated += 1

    print(f"Backfilled {updated} row(s).")


def main() -> int:
    args = build_parser().parse_args()
    ensure_runtime_directories()

    if args.command == "migrate":
        cmd_migrate()
        return 0

    if args.command == "backfill":
        cmd_backfill()
        return 0

    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
