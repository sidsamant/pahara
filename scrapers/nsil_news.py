import asyncio
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


LISTING_PATTERN = re.compile(
    r'<div class="nw_bl_rw row_section">\s*'
    r'<img src="(?P<icon>[^"]+)">\s*'
    r'<h3>\s*'
    r'<a href="(?P<url>[^"]+)">(?P<title>.*?)</a>.*?'
    r'<span class="date-display-single"[^>]*content="(?P<published_iso>[^"]+)"[^>]*>(?P<published_text>.*?)</span>',
    re.DOTALL,
)

DETAIL_BLOCK_PATTERN = re.compile(
    r'<div class="section news_details">(?P<detail_block>.*?)</div>\s*</div>\s*</section>',
    re.DOTALL,
)

PARAGRAPH_PATTERN = re.compile(r"<p>(?P<text>.*?)</p>", re.DOTALL)
IMAGE_PATTERN = re.compile(r'<img[^>]+src="(?P<src>[^"]+)"', re.DOTALL)
TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")


def strip_html(value: str) -> str:
    value = html.unescape(value)
    value = TAG_PATTERN.sub(" ", value)
    value = WHITESPACE_PATTERN.sub(" ", value)
    return value.strip()


def get_html_payload(result: Any) -> str:
    for field_name in ("html", "cleaned_html", "fit_html"):
        value = getattr(result, field_name, None)
        if value:
            return value
    raise RuntimeError("Crawl4AI did not return HTML content.")


def make_run_config(timeout_ms: int) -> CrawlerRunConfig:
    return CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=timeout_ms,
        verbose=False,
        wait_until="domcontentloaded",
        delay_before_return_html=0.5,
    )


async def fetch_page_html(crawler: AsyncWebCrawler, url: str, timeout_ms: int) -> str:
    result = await crawler.arun(url=url, config=make_run_config(timeout_ms))
    if not result.success:
        raise RuntimeError(f"Failed to crawl {url}: {result.error_message}")
    return get_html_payload(result)


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


def extract_listing_items(raw_html: str, source_link: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for match in LISTING_PATTERN.finditer(raw_html):
        url = html.unescape(match.group("url")).strip()
        title = strip_html(match.group("title"))
        item_id = f"news:{url}"
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        items.append(
            {
                "id": item_id,
                "section": "news",
                "type": "News",
                "title": title,
                "description": "",
                "published_date": match.group("published_iso").split("T", 1)[0],
                "published_date_text": strip_html(match.group("published_text")),
                "url": url,
                "image": "",
                "read_time": "",
                "button_text": "Read More",
                "external": False,
                "source_page": source_link,
            }
        )

    return items


def extract_detail_fields(raw_html: str) -> dict[str, str]:
    match = DETAIL_BLOCK_PATTERN.search(raw_html)
    if not match:
        return {"description": "", "image": ""}

    block = match.group("detail_block")
    paragraphs: list[str] = []
    for paragraph_match in PARAGRAPH_PATTERN.finditer(block):
        text = strip_html(paragraph_match.group("text"))
        if text and text not in paragraphs:
            paragraphs.append(text)

    image_match = IMAGE_PATTERN.search(block)
    image = image_match.group("src").strip() if image_match else ""
    if image.startswith("/"):
        image = f"https://www.nsilindia.co.in{image}"

    return {
        "description": " ".join(paragraphs[:3]).strip(),
        "image": image,
    }


async def enrich_item(
    crawler: AsyncWebCrawler, item: dict[str, Any], timeout_ms: int
) -> dict[str, Any]:
    detail_html = await fetch_page_html(crawler, item["url"], timeout_ms=timeout_ms)
    detail_fields = extract_detail_fields(detail_html)
    item.update(detail_fields)
    return item


async def collect_items(source_link: str, timeout_ms: int, logger: logging.Logger) -> list[dict[str, Any]]:
    logger.info("Starting Crawl4AI fetch for %s", source_link)
    logger.info("NSIL scraper is intentionally limited to page 1 of the listing.")
    browser_config = BrowserConfig(headless=True, verbose=False)
    async with AsyncWebCrawler(
        config=browser_config,
        base_directory=str(PROJECT_ROOT),
    ) as crawler:
        listing_html = await fetch_page_html(crawler, source_link, timeout_ms=timeout_ms)
        items = extract_listing_items(listing_html, source_link=source_link)
        logger.info("Collected %s listing item(s) from NSIL page 1.", len(items))

        if items:
            enriched_items = await asyncio.gather(
                *(enrich_item(crawler, item, timeout_ms=timeout_ms) for item in items)
            )
            return enriched_items

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
