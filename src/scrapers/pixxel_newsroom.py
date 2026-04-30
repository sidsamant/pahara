import asyncio
import html
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin


PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("CRAWL4_AI_BASE_DIRECTORY", str(PROJECT_ROOT))

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig


LISTING_PATTERN = re.compile(
    r'<div role="listitem" class="media__filtering_item w-dyn-item">.*?'
    r'<img[^>]+src="(?P<image>https://[^"]+)"[^>]*class="media__filtering_box-img.*?".*?'
    r'<div fs-list-field="category" class="media__filtering_box-tag\s*">(?P<published_date>.*?)</div>.*?'
    r'<h3 fs-list-field="name" class="font__inter text-size-18 text-style-3lines">(?P<title>.*?)</h3>.*?'
    r'<a data-animation="link" href="(?P<url>/news/[^"]+)" class="events__listing__link',
    re.DOTALL,
)

DETAIL_CONTENT_PATTERN = re.compile(
    r'<section class="newsroom__content">.*?<div class="text-rich-text w-richtext">(?P<content>.*?)</div>.*?</section>',
    re.DOTALL,
)

PARAGRAPH_PATTERN = re.compile(r"<p>(?P<text>.*?)</p>", re.DOTALL)
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
        "Ã¢â‚¬â„¢": "’",
        "Ã¢â‚¬Ëœ": "‘",
        "Ã¢â‚¬Å“": "“",
        "Ã¢â‚¬Â": "”",
        "Ã¢â‚¬â€œ": "–",
        "Ã¢â‚¬â€": "—",
        "â€™": "’",
        "â€˜": "‘",
        "â€œ": "“",
        "â€": "”",
        "â€“": "–",
        "â€”": "—",
        "â„¢": "™",
        "â‚¹": "₹",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value.strip()


def normalize_published_date(value: str) -> str:
    cleaned = strip_html(value)
    try:
        return datetime.strptime(cleaned, "%B %d, %Y").date().isoformat()
    except ValueError:
        return cleaned


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


def extract_listing_items(raw_html: str, source_link: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for match in LISTING_PATTERN.finditer(raw_html):
        absolute_url = urljoin(source_link, html.unescape(match.group("url")).strip())
        item_id = f"news:{absolute_url}"
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        items.append(
            {
                "id": item_id,
                "section": "newsroom",
                "type": "News",
                "title": strip_html(match.group("title")),
                "description": "",
                "published_date": normalize_published_date(match.group("published_date")),
                "url": absolute_url,
                "image": html.unescape(match.group("image")).strip(),
                "read_time": "",
                "button_text": "Read now",
                "external": False,
                "source_page": source_link,
            }
        )

    return items


def extract_detail_fields(raw_html: str) -> dict[str, str]:
    match = DETAIL_CONTENT_PATTERN.search(raw_html)
    if not match:
        return {"description": ""}

    paragraphs: list[str] = []
    for paragraph_match in PARAGRAPH_PATTERN.finditer(match.group("content")):
        text = strip_html(paragraph_match.group("text"))
        if text and text not in paragraphs:
            paragraphs.append(text)

    return {
        "description": " ".join(paragraphs[:3]).strip(),
    }


async def enrich_item(
    crawler: AsyncWebCrawler, item: dict[str, Any], timeout_ms: int
) -> dict[str, Any]:
    detail_html = await fetch_page_html(crawler, item["url"], timeout_ms=timeout_ms)
    item.update(extract_detail_fields(detail_html))
    return item


def sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (item.get("published_date") or "", item.get("title") or ""),
        reverse=True,
    )


async def collect_items(source_link: str, timeout_ms: int, logger: logging.Logger) -> list[dict[str, Any]]:
    logger.info("Starting Crawl4AI fetch for %s", source_link)
    logger.info("Pixxel scraper is intentionally limited to page 1 of the newsroom listing.")
    browser_config = BrowserConfig(headless=True, verbose=False)
    async with AsyncWebCrawler(
        config=browser_config,
        base_directory=str(PROJECT_ROOT),
    ) as crawler:
        listing_html = await fetch_page_html(crawler, source_link, timeout_ms=timeout_ms)
        items = extract_listing_items(listing_html, source_link=source_link)
        logger.info("Collected %s listing item(s) from Pixxel page 1.", len(items))

        if items:
            logger.info("Enriching %s Pixxel detail page(s).", len(items))
            enriched_items = await asyncio.gather(
                *(enrich_item(crawler, item, timeout_ms=timeout_ms) for item in items)
            )
            return sort_items(enriched_items)

        return items


def scrape_source(
    *,
    source: Any,
    timeout_ms: int,
    logger: logging.Logger,
) -> dict[str, Any]:
    current_items = asyncio.run(collect_items(source.link, timeout_ms=timeout_ms, logger=logger))
    logger.info("Collected %s current item(s).", len(current_items))
    return {
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "namespace": source.folder_name,
        "source_name": source.name,
        "source_link": source.link,
        "total_current_count": len(current_items),
        "items": current_items,
    }
