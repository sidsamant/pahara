import asyncio
import html
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("CRAWL4_AI_BASE_DIRECTORY", str(PROJECT_ROOT))

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig


POST_PATTERN = re.compile(
    r'data-framer-name="Post".*?'
    r'data-framer-name="Title".*?<a[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
    r'data-framer-name="Date".*?<p[^>]*>(?P<published_date>.*?)</p>.*?'
    r'data-framer-name="Preload"[^>]+href="(?P<preload_href>[^"]+)".*?'
    r'<img[^>]+src="(?P<image>https://[^"]+)"',
    re.DOTALL,
)

DETAIL_CONTENT_PATTERN = re.compile(
    r'data-framer-name="Content">(?P<content>.*?)<div class="ssr-variant',
    re.IGNORECASE | re.DOTALL,
)
PARAGRAPH_PATTERN = re.compile(r"<p[^>]*>(?P<text>.*?)</p>", re.DOTALL)
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


def looks_like_display_date(value: str) -> bool:
    cleaned = strip_html(value)
    for fmt in ("%d %B %Y", "%B %d, %Y"):
        try:
            datetime.strptime(cleaned, fmt)
            return True
        except ValueError:
            continue
    return False


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


def is_internal_bellatrix_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc in {"bellatrix.aero", "www.bellatrix.aero"} and parsed.path.startswith("/updates/")


def extract_listing_items(raw_html: str, source_link: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for match in POST_PATTERN.finditer(raw_html):
        relative_or_absolute_url = html.unescape(match.group("href")).strip()
        absolute_url = urljoin(source_link, relative_or_absolute_url)
        item_id = f"news:{absolute_url}"
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        parsed = urlparse(absolute_url)
        is_external = parsed.netloc not in {"bellatrix.aero", "www.bellatrix.aero"}

        items.append(
            {
                "id": item_id,
                "section": "updates",
                "type": "News",
                "title": strip_html(match.group("title")),
                "description": "",
                "published_date": normalize_published_date(match.group("published_date")),
                "url": absolute_url,
                "image": html.unescape(match.group("image")).strip(),
                "read_time": "",
                "button_text": "Read More",
                "external": is_external,
                "source_page": source_link,
            }
        )

    return items


def extract_detail_fields(raw_html: str) -> dict[str, str]:
    content_match = DETAIL_CONTENT_PATTERN.search(raw_html)
    if not content_match:
        return {"description": ""}

    paragraphs: list[str] = []
    for paragraph_match in PARAGRAPH_PATTERN.finditer(content_match.group("content")):
        text = strip_html(paragraph_match.group("text"))
        if not text:
            continue
        if looks_like_display_date(text):
            continue
        if text in paragraphs:
            continue
        if text.startswith("For media inquiries") or text.startswith("Email:") or text.startswith("Copyright @"):
            break
        paragraphs.append(text)

    description = " ".join(paragraphs[:3]).strip()
    return {
        "description": description,
    }


async def enrich_item(
    crawler: AsyncWebCrawler, item: dict[str, Any], timeout_ms: int, logger: logging.Logger
) -> dict[str, Any]:
    try:
        detail_html = await fetch_page_html(crawler, item["url"], timeout_ms=timeout_ms)
    except Exception:
        logger.exception("Bellatrix detail enrichment failed for %s; keeping listing fields.", item["url"])
        return item

    detail_fields = extract_detail_fields(detail_html)
    if detail_fields.get("description"):
        item["description"] = detail_fields["description"]
    return item


def sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (item.get("published_date") or "", item.get("title") or ""),
        reverse=True,
    )


async def collect_items(source_link: str, timeout_ms: int, logger: logging.Logger) -> list[dict[str, Any]]:
    logger.info("Starting Crawl4AI fetch for %s", source_link)
    logger.info("Bellatrix scraper is intentionally limited to page 1 of the updates listing.")
    browser_config = BrowserConfig(headless=True, verbose=False)
    async with AsyncWebCrawler(
        config=browser_config,
        base_directory=str(PROJECT_ROOT),
    ) as crawler:
        listing_html = await fetch_page_html(crawler, source_link, timeout_ms=timeout_ms)
        items = extract_listing_items(listing_html, source_link=source_link)
        logger.info("Collected %s listing item(s) from Bellatrix page 1.", len(items))

        internal_items = [item for item in items if is_internal_bellatrix_url(item["url"])]
        if internal_items:
            logger.info("Enriching %s internal Bellatrix detail page(s).", len(internal_items))
            await asyncio.gather(
                *(enrich_item(crawler, item, timeout_ms=timeout_ms, logger=logger) for item in internal_items)
            )

        return sort_items(items)


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
