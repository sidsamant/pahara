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


ARTICLE_PATTERN = re.compile(
    r'\{\\"title\\":\\"(?P<title>(?:\\\\.|[^"\\])*)\\",'
    r'\\"description\\":\\"(?P<description>(?:\\\\.|[^"\\])*)\\",'
    r'\\"publishedDate\\":\\"(?P<published_date>\d{4}-\d{2}-\d{2})\\",'
    r'\\"image\\":\\"(?P<image>(?:\\\\.|[^"\\])*)\\",'
    r'\\"imageMobile\\":\\"(?:\\\\.|[^"\\])*\\",'
    r'\\"imageTab\\":\\"(?:\\\\.|[^"\\])*\\",'
    r'\\"readTime\\":\\"(?P<read_time>(?:\\\\.|[^"\\])*)\\",'
    r'\\"redirectionButton\\":\{\\"text\\":\\"(?P<button_text>(?:\\\\.|[^"\\])*)\\",\\"link\\":\\"(?P<link>(?:\\\\.|[^"\\])*)\\",\\"external\\":(?P<external>true|false)\},'
    r'\\"type\\":\\"(?P<item_type>News|Media_and_Interviews)\\"',
    re.DOTALL,
)

PRESS_RELEASE_PATTERN = re.compile(
    r'\{\\"title\\":\\"(?P<title>(?:\\\\.|[^"\\])*)\\",\\"description\\":\\"(?P<description>(?:\\\\.|[^"\\])*)\\",\\"slug\\":\\"(?P<slug>(?:\\\\.|[^"\\])*)\\",\\"readTime\\":\\"(?P<read_time>(?:\\\\.|[^"\\])*)\\"\}'
)

PRESS_RELEASE_DETAIL_PATTERN = re.compile(
    r'\{\\"slug\\":\\"(?P<slug>(?:\\\\.|[^"\\])*)\\",\\"title\\":\\"(?P<title>(?:\\\\.|[^"\\])*)\\",\\"description\\":\\"(?P<description>(?:\\\\.|[^"\\])*)\\",\\"publishedDate\\":\\"(?P<published_date>\d{4}-\d{2}-\d{2})\\",\\"publishedDateText\\":\\"(?P<published_date_text>(?:\\\\.|[^"\\])*)\\",\\"type\\":\\"Press_Release\\".*?\\"detailContent\\":\[(?P<detail_content>.*?)\],\\"alsoReadSection\\"',
    re.DOTALL,
)

DETAIL_FRAGMENT_PATTERN = re.compile(
    r'\{\\"title\\":\\"(?P<title>(?:\\.|[^"\\])*)\\",\\"description\\":\\"(?P<description>(?:\\.|[^"\\])*)\\",\\"image\\":\[.*?\](?:,\\"imageMobile\\":\[.*?\],\\"imageTab\\":\[.*?\])?\}',
    re.DOTALL,
)

TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"\s+")


def decode_json_string(value: str) -> str:
    return json.loads(f'"{value}"')


def clean_text(value: str) -> str:
    value = decode_json_string(value)
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


async def fetch_page_html(crawler: AsyncWebCrawler, url: str, timeout_ms: int) -> str:
    config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=timeout_ms,
        verbose=False,
        wait_until="domcontentloaded",
        delay_before_return_html=0.5,
    )
    result = await crawler.arun(url=url, config=config)
    if not result.success:
        raise RuntimeError(f"Failed to crawl {url}: {result.error_message}")
    return get_html_payload(result)


def extract_article_items(raw_html: str, source_link: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for match in ARTICLE_PATTERN.finditer(raw_html):
        link = decode_json_string(match.group("link"))
        item_type = match.group("item_type")
        section = "news" if item_type == "News" else "media_interviews"
        item_id = f"{section}:{link}"
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        items.append(
            {
                "id": item_id,
                "section": section,
                "type": item_type,
                "title": clean_text(match.group("title")),
                "description": clean_text(match.group("description")),
                "published_date": match.group("published_date"),
                "url": link,
                "image": decode_json_string(match.group("image")),
                "read_time": clean_text(match.group("read_time")),
                "button_text": clean_text(match.group("button_text")),
                "external": match.group("external") == "true",
                "source_page": source_link,
            }
        )

    return items


def extract_press_release_listing(raw_html: str, source_link: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for match in PRESS_RELEASE_PATTERN.finditer(raw_html):
        slug = clean_text(match.group("slug"))
        item_id = f"press_release:{slug}"
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        items.append(
            {
                "id": item_id,
                "section": "press_release",
                "type": "Press_Release",
                "slug": slug,
                "title": clean_text(match.group("title")),
                "description": clean_text(match.group("description")),
                "published_date": None,
                "url": f"{source_link}/{slug}",
                "image": "",
                "read_time": clean_text(match.group("read_time")),
                "button_text": "Read More",
                "external": False,
                "source_page": source_link,
            }
        )

    return items


def summarize_detail_content(raw_detail_content: str) -> str:
    fragments: list[str] = []
    for match in DETAIL_FRAGMENT_PATTERN.finditer(raw_detail_content):
        for group_name in ("title", "description"):
            cleaned = clean_text(match.group(group_name))
            if not cleaned or cleaned.startswith("$"):
                continue
            if cleaned not in fragments:
                fragments.append(cleaned)
    return " ".join(fragments[:4]).strip()


def sanitize_description(value: str) -> str:
    suspicious_tokens = (
        '","description":"',
        '","image":',
        "imageMobile",
        '{"title":"',
        '},{',
    )
    if any(token in value for token in suspicious_tokens):
        return ""
    return value


def extract_press_release_detail(raw_html: str, slug: str) -> dict[str, str]:
    match = PRESS_RELEASE_DETAIL_PATTERN.search(raw_html)
    if not match:
        raise RuntimeError(f"Unable to extract press release detail payload for slug '{slug}'.")

    extracted_slug = clean_text(match.group("slug"))
    if extracted_slug != slug:
        raise RuntimeError(
            f"Unexpected slug mismatch while parsing detail page. Expected '{slug}', got '{extracted_slug}'."
        )

    description = clean_text(match.group("description"))
    if not description:
        description = summarize_detail_content(match.group("detail_content"))
    description = sanitize_description(description)

    return {
        "title": clean_text(match.group("title")),
        "description": description,
        "published_date": match.group("published_date"),
    }


async def enrich_press_release(
    crawler: AsyncWebCrawler, item: dict[str, Any], timeout_ms: int
) -> dict[str, Any]:
    raw_html = await fetch_page_html(crawler, item["url"], timeout_ms=timeout_ms)
    details = extract_press_release_detail(raw_html, item["slug"])
    item.update(details)
    return item


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


def sort_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        items,
        key=lambda item: (
            item.get("published_date") or "",
            item.get("section") or "",
            item.get("title") or "",
        ),
        reverse=True,
    )


async def collect_items(source_link: str, timeout_ms: int, logger: logging.Logger) -> list[dict[str, Any]]:
    logger.info("Starting Crawl4AI fetch for %s", source_link)
    browser_config = BrowserConfig(headless=True, verbose=False)
    async with AsyncWebCrawler(
        config=browser_config,
        base_directory=str(PROJECT_ROOT),
    ) as crawler:
        newsroom_html = await fetch_page_html(crawler, source_link, timeout_ms=timeout_ms)
        items = extract_article_items(newsroom_html, source_link=source_link)
        press_releases = extract_press_release_listing(newsroom_html, source_link=source_link)

        if press_releases:
            logger.info("Enriching %s press release detail page(s).", len(press_releases))
            enriched = await asyncio.gather(
                *(enrich_press_release(crawler, item, timeout_ms=timeout_ms) for item in press_releases)
            )
            items.extend(enriched)

        logger.info("Collected %s current item(s).", len(items))
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
