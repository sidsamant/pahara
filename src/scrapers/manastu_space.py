import logging
import re
from datetime import datetime
from typing import Any
from urllib.parse import urldefrag

from crawl4ai import (
    AsyncWebCrawler,
    CacheMode,
    ContentTypeFilter,
    CrawlerRunConfig,
    DefaultMarkdownGenerator,
    FilterChain,
    URLPatternFilter,
)
from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

from .base import BaseScraper, PROJECT_ROOT

_DATE_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DATE_LONG = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s+(\d{4})\b"
)
_SITE_TITLE = "Manastu Space"


def _normalise(url: str) -> str:
    return urldefrag(url.rstrip("/"))[0]


def _extract_date(markdown: str) -> str:
    m = _DATE_ISO.search(markdown)
    if m:
        return m.group(1)
    m = _DATE_LONG.search(markdown)
    if m:
        try:
            return datetime.strptime(m.group(0), "%B %d, %Y").date().isoformat()
        except ValueError:
            pass
    return ""


def _extract_title_from_markdown(markdown: str) -> str:
    """Return the first substantive text line after the blog-section H1."""
    past_h1 = False
    for line in markdown.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            past_h1 = True
            continue
        if past_h1 and not stripped.startswith("!") and len(stripped) > 10:
            return stripped
    return ""


class ManastuSpaceScraper(BaseScraper):

    def extract_items(self, raw_html: str, source_link: str) -> list[dict[str, Any]]:
        return []

    async def collect_items(
        self, source_link: str, timeout_ms: int, logger: logging.Logger
    ) -> list[dict[str, Any]]:
        logger.info("Starting deep crawl for %s", source_link)

        filter_chain = FilterChain([
            URLPatternFilter(patterns=["*/blogs/*"]),
            ContentTypeFilter(allowed_types=["text/html"]),
        ])

        config = CrawlerRunConfig(
            deep_crawl_strategy=BFSDeepCrawlStrategy(
                max_depth=1,
                include_external=False,
                filter_chain=filter_chain,
            ),
            scraping_strategy=LXMLWebScrapingStrategy(),
            markdown_generator=DefaultMarkdownGenerator(),
            cache_mode=CacheMode.BYPASS,
            page_timeout=timeout_ms,
            # networkidle gives JS time to render article cards and content
            delay_before_return_html=2.0,
            wait_until="networkidle",
            verbose=False,
        )

        listing_url = _normalise(source_link)
        items: list[dict[str, Any]] = []

        async with AsyncWebCrawler(
            config=self._browser_config(), base_directory=str(PROJECT_ROOT)
        ) as crawler:
            results = await crawler.arun(source_link, config=config)
            logger.info("Deep crawl returned %s result(s) total.", len(results))
            for result in results:
                normalised = _normalise(result.url)
                if normalised == listing_url:
                    continue
                if not result.success:
                    logger.warning("Failed to crawl %s: %s", result.url, result.error_message)
                    continue
                item = self._result_to_item(result, source_link)
                if item:
                    items.append(item)

        logger.info("Collected %s item(s) from deep crawl.", len(items))
        return items

    def _result_to_item(self, result: Any, source_link: str) -> dict[str, Any] | None:
        url = result.url
        markdown = result.markdown or ""

        # Metadata title is always the generic site name; extract real title from markdown
        meta_title = (result.metadata.get("title") or "").strip()
        title = (
            meta_title
            if meta_title and meta_title != _SITE_TITLE
            else _extract_title_from_markdown(markdown)
        )
        if not title or not url:
            return None

        description = result.metadata.get("description") or ""
        if not description:
            for line in markdown.split("\n"):
                line = line.strip()
                if line and not line.startswith("#") and not line.startswith("!") and len(line) > 30:
                    if line != title:
                        description = line
                        break

        image = result.metadata.get("og:image") or ""
        if not image:
            images = (result.media or {}).get("images", [])
            if images:
                image = images[0].get("src", "")

        published_date = _extract_date(markdown)

        return {
            "id": f"news:{url}",
            "section": "blog",
            "type": "News",
            "title": title,
            "description": description,
            "published_date": published_date,
            "published_date_text": published_date,
            "url": url,
            "image": image,
            "read_time": "",
            "button_text": "Read More",
            "external": False,
            "source_page": source_link,
        }


scrape_source = ManastuSpaceScraper().scrape_source
