import argparse

from database import ensure_runtime_directories, get_all_sources, init_db, seed_default_sources, ensure_source, set_source_enabled


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage source records for the multi-source crawler.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_parser = subparsers.add_parser("add", help="Add or update a source.")
    add_parser.add_argument("--name", required=True, help="Unique source name.")
    add_parser.add_argument("--link", required=True, help="Unique source link.")
    add_parser.add_argument("--scraper-key", required=True, help="Scraper key, for example digantara_newsroom.")
    add_parser.add_argument(
        "--disabled",
        action="store_true",
        help="Create or update the source as disabled.",
    )

    list_parser = subparsers.add_parser("list", help="List all sources.")
    list_parser.add_argument(
        "--enabled-only",
        action="store_true",
        help="Show only enabled sources.",
    )

    enable_parser = subparsers.add_parser("enable", help="Enable a source by id.")
    enable_parser.add_argument("--id", required=True, type=int)

    disable_parser = subparsers.add_parser("disable", help="Disable a source by id.")
    disable_parser.add_argument("--id", required=True, type=int)

    return parser


def print_sources(enabled_only: bool) -> None:
    for source in get_all_sources():
        if enabled_only and not source.enabled:
            continue
        print(
            f"id={source.id} enabled={int(source.enabled)} scraper_key={source.scraper_key} "
            f"folder={source.folder_name} name={source.name} link={source.link}"
        )


def main() -> int:
    args = build_parser().parse_args()
    ensure_runtime_directories()
    init_db()
    seed_default_sources()

    if args.command == "add":
        source = ensure_source(
            name=args.name,
            link=args.link,
            scraper_key=args.scraper_key,
            enabled=not args.disabled,
        )
        print(
            f"upserted id={source.id} enabled={int(source.enabled)} folder={source.folder_name} "
            f"name={source.name} link={source.link} scraper_key={source.scraper_key}"
        )
        return 0

    if args.command == "list":
        print_sources(enabled_only=args.enabled_only)
        return 0

    if args.command == "enable":
        set_source_enabled(args.id, True)
        print(f"enabled source id={args.id}")
        return 0

    if args.command == "disable":
        set_source_enabled(args.id, False)
        print(f"disabled source id={args.id}")
        return 0

    raise ValueError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
