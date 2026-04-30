import html
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("CRAWL4_AI_BASE_DIRECTORY", str(PROJECT_ROOT))

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig


ITEM_PATTERN = re.compile(
    r'<div role="listitem" class="news-header_item w-dyn-item">.*?'
    r'(?P<anchor_tag><a[^>]+href="(?P<url>[^"]+)"[^>]*class="news-header_link w-inline-block">).*?'
    r'<div fs-cmsfilter-field="type" class="newsroom_detail-text text-size-xtiny">(?P<item_type>.*?)</div>.*?'
    r'<div class="newsroom_detail-text text-size-xtiny">(?P<published_date>.*?)</div>.*?'
    r'<h3 fs-cmsfilter-field="name" class="newsroom_subheading font-ppmori text-size-medium">(?P<title>.*?)</h3>',
    re.DOTALL,
)

TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")


def strip_html(value: str) -> str:
    value = html.unescape(value)
    value = TAG_PATTERN.sub(" ", value)
    value = WHITESPACE_PATTERN.sub(" ", value)
    try:
        value = value.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    replacements = {
        "â€™": "’",
        "â€˜": "‘",
        "â€œ": "“",
        "â€": "”",
        "â€“": "–",
        "â€”": "—",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value.strip()


def get_html_payload(result: Any) -> str:
    for field_name in ("html", "cleaned_html", "fit_html"):
        value = getattr(result, field_name, None)
        if value:
            return value
    raise RuntimeError("Crawl4AI did not return HTML content.")


def load_seen_ids(state_file: Path) -> set[str]:
    if not state_file.exists():
        return set()

    payload = json.loads(state_file.read_text(encoding="utf-8"))
    return set(payload.get("seen_ids", []))


def save_state(state_file: Path, items: list[dict[str, Any]]) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "seen_ids": sorted(item["id"] for item in items),
        "total_items": len(items),
    }
    state_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def normalize_item_type(value: str) -> str:
    normalized = strip_html(value)
    return normalized.replace("  ", " ")


def extract_items(raw_html: str, source_link: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for match in ITEM_PATTERN.finditer(raw_html):
        url = html.unescape(match.group("url")).strip()
        item_id = f"listing:{url}"
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        items.append(
            {
                "id": item_id,
                "section": "newsroom",
                "type": normalize_item_type(match.group("item_type")),
                "title": strip_html(match.group("title")),
                "description": "",
                "published_date": strip_html(match.group("published_date")),
                "url": url,
                "image": "",
                "read_time": "",
                "button_text": "Read More",
                "external": 'target="_blank"' in match.group("anchor_tag"),
                "source_page": source_link,
            }
        )

    return items


async def collect_items(source_link: str, timeout_ms: int, logger: logging.Logger) -> list[dict[str, Any]]:
    logger.info("Starting Crawl4AI fetch for %s", source_link)
    logger.info("Skyroot scraper is intentionally limited to page 1 of the newsroom listing.")
    browser_config = BrowserConfig(headless=True, verbose=False)
    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=timeout_ms,
        verbose=False,
        wait_until="domcontentloaded",
        delay_before_return_html=0.5,
    )
    async with AsyncWebCrawler(
        config=browser_config,
        base_directory=str(PROJECT_ROOT),
    ) as crawler:
        result = await crawler.arun(url=source_link, config=run_config)
        if not result.success:
            raise RuntimeError(f"Failed to crawl {source_link}: {result.error_message}")
        items = extract_items(get_html_payload(result), source_link=source_link)
        logger.info("Collected %s current item(s) from Skyroot page 1.", len(items))
        return items


def scrape_source(
    *,
    source: Any,
    source_dir: Path,
    timeout_ms: int,
    all_items: bool,
    logger: logging.Logger,
) -> dict[str, Any]:
    state_file = source_dir / "state" / "seen_items.json"
    seen_ids = load_seen_ids(state_file)
    import asyncio

    current_items = asyncio.run(collect_items(source.link, timeout_ms=timeout_ms, logger=logger))

    if all_items:
        selected_items = current_items
    else:
        selected_items = [item for item in current_items if item["id"] not in seen_ids]

    save_state(state_file, current_items)

    payload = {
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "namespace": source.folder_name,
        "source_name": source.name,
        "source_link": source.link,
        "returned_count": len(selected_items),
        "total_current_count": len(current_items),
        "items": selected_items,
    }
    logger.info(
        "Returning %s item(s); %s current item(s) recorded in state.",
        payload["returned_count"],
        payload["total_current_count"],
    )
    return payload
