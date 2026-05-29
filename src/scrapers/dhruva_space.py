import logging
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
from .mixins import CrawlResultItemMixin


def _normalise(url: str) -> str:
    return urldefrag(url.rstrip("/"))[0]


class DhruvaSpaceScraper(CrawlResultItemMixin, BaseScraper):

    def extract_items(self, raw_html: str, source_link: str) -> list[dict[str, Any]]:
        return []

    async def collect_items(
        self, source_link: str, timeout_ms: int, logger: logging.Logger
    ) -> list[dict[str, Any]]:
        logger.info("Starting deep crawl for %s", source_link)

        filter_chain = FilterChain([
            URLPatternFilter(patterns=["*/press-releases/*"]),
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
            # domcontentloaded: networkidle causes timeouts on this Webflow site
            delay_before_return_html=2.0,
            wait_until="domcontentloaded",
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
                item = self.result_to_item(result, source_link)
                if item:
                    items.append(item)

        logger.info("Collected %s item(s) from deep crawl.", len(items))
        return items


scrape_source = DhruvaSpaceScraper().scrape_source
