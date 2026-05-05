import logging
import re
from typing import Any
from urllib.parse import urldefrag

from crawl4ai import (
    CacheMode,
    ContentTypeFilter,
    CrawlerRunConfig,
    DefaultMarkdownGenerator,
    FilterChain,
    URLPatternFilter,
    AsyncWebCrawler,
)
from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

from .base import BaseScraper, PROJECT_ROOT

_DATE_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def _normalise(url: str) -> str:
    return urldefrag(url.rstrip("/"))[0]


class SatsureNewsroomScraper(BaseScraper):

    def extract_items(self, raw_html: str, source_link: str) -> list[dict[str, Any]]:
        # Not used — collect_items is overridden to use deep crawl
        return []

    async def collect_items(
        self, source_link: str, timeout_ms: int, logger: logging.Logger
    ) -> list[dict[str, Any]]:
        logger.info("Starting deep crawl for %s", source_link)

        # Articles live at /press-release/<slug>/ (not under /newsroom/)
        filter_chain = FilterChain([
            URLPatternFilter(patterns=["*press-release*"]),
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
            # Give JS time to render article cards before link discovery
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
                depth = result.metadata.get("depth", 0)
                normalised = _normalise(result.url)
                logger.debug("Result depth=%s url=%s success=%s", depth, result.url, result.success)
                # Skip the listing page however it appears (depth=0 re-crawl or self-link)
                if normalised == listing_url:
                    logger.info("Skipping listing page URL: %s", result.url)
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
        title = (result.metadata.get("title") or "").strip()
        if not title or not url:
            return None

        description = result.metadata.get("description") or ""
        if not description and result.markdown:
            for line in result.markdown.split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    description = line
                    break

        image = result.metadata.get("og:image") or ""
        if not image:
            images = (result.media or {}).get("images", [])
            if images:
                image = images[0].get("src", "")

        date_str = (
            result.metadata.get("article:published_time")
            or result.metadata.get("og:article:published_time")
            or ""
        )
        if date_str:
            published_date = date_str[:10]
        else:
            m = _DATE_PATTERN.search(result.markdown or "")
            published_date = m.group(1) if m else ""

        return {
            "id": f"press_release:{url}",
            "section": "press_release",
            "type": "Press Release",
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


scrape_source = SatsureNewsroomScraper().scrape_source
