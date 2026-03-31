import argparse
import json
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from database import (
    LOGS_DIR,
    RUN_RESULTS_DIR,
    SOURCES_DIR,
    SourceRecord,
    create_run,
    create_run_source,
    ensure_runtime_directories,
    finalize_run,
    finalize_run_source,
    get_enabled_sources,
    init_db,
    seed_default_sources,
    upsert_scraped_items,
)
from scrapers import SCRAPER_REGISTRY


X_SCRAPER_KEY = "x_latest_posts"
X_SOURCE_DELAY_SECONDS = 2.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run all enabled newsroom scrapers, track them in SQLite, and write per-source results."
    )
    parser.add_argument(
        "--all-items",
        action="store_true",
        help="Return all currently discoverable items instead of only unseen ones.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=90000,
        help="Per-page Crawl4AI timeout in milliseconds.",
    )
    return parser


def make_run_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def configure_logger(log_path: Path, logger_name: str) -> logging.Logger:
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger


def close_logger(logger: logging.Logger) -> None:
    handlers = list(logger.handlers)
    for handler in handlers:
        handler.close()
        logger.removeHandler(handler)


def ensure_source_folder(source: SourceRecord) -> Path:
    source_dir = SOURCES_DIR / source.folder_name
    (source_dir / "results").mkdir(parents=True, exist_ok=True)
    (source_dir / "state").mkdir(parents=True, exist_ok=True)
    return source_dir


def build_aggregate_payload(run_id: int, results_by_source: dict[str, dict], failed_sources: list[dict]) -> dict:
    return {
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "sources": results_by_source,
        "failed_sources": failed_sources,
    }


def order_sources_for_run(enabled_sources: list[SourceRecord]) -> list[SourceRecord]:
    # Keep non-X sources in their original DB order, but randomize the X targets
    # for each run so profile access order is less predictable.
    non_x_sources = [source for source in enabled_sources if source.scraper_key != X_SCRAPER_KEY]
    x_sources = [source for source in enabled_sources if source.scraper_key == X_SCRAPER_KEY]
    random.shuffle(x_sources)
    return non_x_sources + x_sources


def main() -> int:
    args = build_parser().parse_args()

    ensure_runtime_directories()
    init_db()
    seed_default_sources()

    enabled_sources = order_sources_for_run(get_enabled_sources())
    run_timestamp = make_run_timestamp()
    log_dir = LOGS_DIR / run_timestamp
    log_dir.mkdir(parents=True, exist_ok=True)
    run_logger = configure_logger(log_dir / "run.log", f"run.{run_timestamp}")

    run_id = create_run(log_dir=log_dir, total_sources=len(enabled_sources))
    run_logger.info("Run %s started with %s enabled source(s).", run_id, len(enabled_sources))

    results_by_source: dict[str, dict] = {}
    failed_sources: list[dict] = []
    succeeded_count = 0
    failed_count = 0

    try:
        previous_source_was_x = False
        for source in enabled_sources:
            if source.scraper_key == X_SCRAPER_KEY and previous_source_was_x:
                run_logger.info(
                    "Waiting %.1f second(s) before scraping the next X source '%s'.",
                    X_SOURCE_DELAY_SECONDS,
                    source.name,
                )
                time.sleep(X_SOURCE_DELAY_SECONDS)

            source_dir = ensure_source_folder(source)
            output_path = source_dir / "results" / f"{run_timestamp}.json"
            log_path = log_dir / f"{source.folder_name}.log"
            source_logger = configure_logger(log_path, f"source.{run_timestamp}.{source.id}")
            run_source_id = create_run_source(run_id=run_id, source_id=source.id, log_path=log_path)

            source_logger.info("Starting source '%s' (%s).", source.name, source.link)

            try:
                scraper = SCRAPER_REGISTRY[source.scraper_key]
                payload = scraper(
                    source=source,
                    source_dir=source_dir,
                    timeout_ms=args.timeout_ms,
                    all_items=args.all_items,
                    logger=source_logger,
                )
                payload["source"] = {
                    "id": source.id,
                    "name": source.name,
                    "link": source.link,
                    "folder_name": source.folder_name,
                }
                output_path.write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )

                results_by_source[source.folder_name] = payload
                succeeded_count += 1
                finalize_run_source(
                    run_source_id=run_source_id,
                    status="completed",
                    output_path=output_path,
                    total_current_count=payload["total_current_count"],
                    returned_count=payload["returned_count"],
                    error_text=None,
                )
                # Persist the returned items into SQLite after the per-source output
                # file is written. The source state file still controls "new vs seen"
                # behavior, while this table provides durable deduplicated storage.
                persistence_result = upsert_scraped_items(
                    source_id=source.id,
                    run_source_id=run_source_id,
                    items=payload["items"],
                )
                source_logger.info(
                    "Completed source '%s' with %s returned item(s) out of %s current item(s); inserted=%s updated=%s in SQLite.",
                    source.name,
                    payload["returned_count"],
                    payload["total_current_count"],
                    persistence_result["inserted"],
                    persistence_result["updated"],
                )
            except Exception as exc:
                failed_count += 1
                failed_entry = {
                    "source_id": source.id,
                    "source_name": source.name,
                    "folder_name": source.folder_name,
                    "error": str(exc),
                }
                failed_sources.append(failed_entry)
                source_logger.exception("Source '%s' failed.", source.name)
                finalize_run_source(
                    run_source_id=run_source_id,
                    status="failed",
                    output_path=None,
                    total_current_count=0,
                    returned_count=0,
                    error_text=str(exc),
                )
            finally:
                close_logger(source_logger)
                previous_source_was_x = source.scraper_key == X_SCRAPER_KEY

        aggregate_payload = build_aggregate_payload(run_id, results_by_source, failed_sources)
        aggregate_path = RUN_RESULTS_DIR / f"{run_timestamp}.json"
        aggregate_path.write_text(
            json.dumps(aggregate_payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        final_status = "completed" if failed_count == 0 else "completed_with_errors"
        finalize_run(
            run_id=run_id,
            status=final_status,
            succeeded_sources=succeeded_count,
            failed_sources=failed_count,
            results_path=aggregate_path,
        )

        run_logger.info(
            "Run %s finished with status=%s succeeded=%s failed=%s.",
            run_id,
            final_status,
            succeeded_count,
            failed_count,
        )
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "status": final_status,
                    "enabled_sources": len(enabled_sources),
                    "succeeded_sources": succeeded_count,
                    "failed_sources": failed_count,
                    "results_path": str(aggregate_path),
                    "log_dir": str(log_dir),
                },
                indent=2,
            )
        )
        return 0 if failed_count == 0 else 1
    finally:
        close_logger(run_logger)


if __name__ == "__main__":
    raise SystemExit(main())
