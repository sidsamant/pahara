import asyncio
import html as html_module
import json
import logging
import re
from typing import Any

from crawl4ai import AsyncWebCrawler

from .base import BaseScraper, PROJECT_ROOT
from .mixins import DetailEnrichmentMixin

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

_SUSPICIOUS_TOKENS = (
    '","description":"',
    '","image":',
    "imageMobile",
    '{"title":"',
    "},{",
)


def _decode_json_str(value: str) -> str:
    return json.loads(f'"{value}"')


class DigantaraScraper(BaseScraper, DetailEnrichmentMixin):

    def _clean(self, value: str) -> str:
        return self.strip_html(_decode_json_str(value))

    def should_enrich(self, item: dict[str, Any]) -> bool:
        return item["type"] == "Press_Release"

    def extract_items(self, raw_html: str, source_link: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for match in ARTICLE_PATTERN.finditer(raw_html):
            link = _decode_json_str(match.group("link"))
            item_type = match.group("item_type")
            section = "news" if item_type == "News" else "media_interviews"
            item_id = f"{section}:{link}"
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            items.append({
                "id": item_id,
                "section": section,
                "type": item_type,
                "title": self._clean(match.group("title")),
                "description": self._clean(match.group("description")),
                "published_date": match.group("published_date"),
                "url": link,
                "image": _decode_json_str(match.group("image")),
                "read_time": self._clean(match.group("read_time")),
                "button_text": self._clean(match.group("button_text")),
                "external": match.group("external") == "true",
                "source_page": source_link,
            })
        return items

    def _extract_press_release_listing(self, raw_html: str, source_link: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for match in PRESS_RELEASE_PATTERN.finditer(raw_html):
            slug = self._clean(match.group("slug"))
            item_id = f"press_release:{slug}"
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            items.append({
                "id": item_id,
                "section": "press_release",
                "type": "Press_Release",
                "slug": slug,
                "title": self._clean(match.group("title")),
                "description": self._clean(match.group("description")),
                "published_date": None,
                "url": f"{source_link}/{slug}",
                "image": "",
                "read_time": self._clean(match.group("read_time")),
                "button_text": "Read More",
                "external": False,
                "source_page": source_link,
            })
        return items

    def _summarize_detail(self, raw_detail_content: str) -> str:
        fragments: list[str] = []
        for match in DETAIL_FRAGMENT_PATTERN.finditer(raw_detail_content):
            for group_name in ("title", "description"):
                cleaned = self._clean(match.group(group_name))
                if not cleaned or cleaned.startswith("$"):
                    continue
                if cleaned not in fragments:
                    fragments.append(cleaned)
        return " ".join(fragments[:4]).strip()

    def extract_detail_fields(self, raw_html: str) -> dict[str, Any]:
        match = PRESS_RELEASE_DETAIL_PATTERN.search(raw_html)
        if not match:
            return {}
        description = self._clean(match.group("description"))
        if not description:
            description = self._summarize_detail(match.group("detail_content"))
        if any(tok in description for tok in _SUSPICIOUS_TOKENS):
            description = ""
        return {
            "title": self._clean(match.group("title")),
            "description": description,
            "published_date": match.group("published_date"),
        }

    async def collect_items(
        self, source_link: str, timeout_ms: int, logger: logging.Logger
    ) -> list[dict[str, Any]]:
        logger.info("Starting Crawl4AI fetch for %s", source_link)
        async with AsyncWebCrawler(
            config=self._browser_config(), base_directory=str(PROJECT_ROOT)
        ) as crawler:
            raw_html = await self._fetch_html(crawler, source_link, timeout_ms)
            items = self.extract_items(raw_html, source_link)
            press_releases = self._extract_press_release_listing(raw_html, source_link)
            if press_releases:
                logger.info("Enriching %s press release detail page(s).", len(press_releases))
                await asyncio.gather(
                    *(self._enrich_one(crawler, pr, timeout_ms, logger) for pr in press_releases)
                )
                items.extend(press_releases)
        return self._sort_items(items)

    def _sort_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            items,
            key=lambda i: (
                i.get("published_date") or "",
                i.get("section") or "",
                i.get("title") or "",
            ),
            reverse=True,
        )


scrape_source = DigantaraScraper().scrape_source
