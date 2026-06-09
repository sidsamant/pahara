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
    PruningContentFilter,
    URLPatternFilter,
)
from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

from .base import BaseScraper, PROJECT_ROOT
from .mixins import CrawlResultItemMixin


def _normalise(url: str) -> str:
    return urldefrag(url.rstrip("/"))[0]


class OrbitaidScraper(CrawlResultItemMixin, BaseScraper):
    result_item_id_prefix = "news"
    result_item_section = "newsroom"
    result_item_type = "News"
    result_item_button_text = "Read now"

    def extract_items(self, raw_html: str, source_link: str) -> list[dict[str, Any]]:
        # Not used; collect_items is overridden to use deep crawl.
        return []

    async def collect_items(
        self, source_link: str, timeout_ms: int, logger: logging.Logger
    ) -> list[dict[str, Any]]:
        logger.info("Starting deep crawl for %s", source_link)

        filter_chain = FilterChain([
            URLPatternFilter(patterns=["*/newsroom/*"]),
            ContentTypeFilter(allowed_types=["text/html"]),
        ])

        prune_filter = PruningContentFilter(
            threshold=0.5,
            threshold_type="fixed",  # or "dynamic"
            min_word_threshold=50
        )

        md_generator = DefaultMarkdownGenerator(
            # content_filter=prune_filter,
            # content_source="cleaned_html",
            options={"ignore_links": True,"ignore_images": True}
        )

        # Target article body and sidebar, but not other content
        target_elements=["div.main-content"]
        config = CrawlerRunConfig(
            deep_crawl_strategy=BFSDeepCrawlStrategy(
                max_depth=1,
                include_external=False,
                filter_chain=filter_chain,
            ),
            scraping_strategy=LXMLWebScrapingStrategy(),
            cache_mode=CacheMode.BYPASS,
            page_timeout=timeout_ms,
            # Give Webflow enough time to render cards before link discovery.
            delay_before_return_html=2.0,
            wait_until="networkidle",
            verbose=False,
            target_elements=target_elements,
            markdown_generator=md_generator
        )

        listing_url = _normalise(source_link)
        items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        async with AsyncWebCrawler(
            config=self._browser_config(), base_directory=str(PROJECT_ROOT)
        ) as crawler:
            results = await crawler.arun(source_link, config=config)
            logger.info("Deep crawl returned %s result(s) total.", len(results))
            for result in results:
                depth = result.metadata.get("depth", 0)
                normalised = _normalise(result.url)
                logger.debug("Result depth=%s url=%s success=%s", depth, result.url, result.success)
                if normalised == listing_url:
                    logger.info("Skipping listing page URL: %s", result.url)
                    continue
                if not result.success:
                    logger.warning("Failed to crawl %s: %s", result.url, result.error_message)
                    continue

                item = self.result_to_item(result, source_link)
                if not item or item["id"] in seen_ids:
                    continue
                seen_ids.add(item["id"])
                items.append(item)

        logger.info("Collected %s item(s) from deep crawl.", len(items))
        return self._sort_items(items)

    def _sort_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            items,
            key=lambda item: (item.get("published_date") or "", item.get("title") or ""),
            reverse=True,
        )


scrape_source = OrbitaidScraper().scrape_source
