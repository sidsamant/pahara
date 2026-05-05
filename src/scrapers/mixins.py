import asyncio
import logging
from typing import Any

from crawl4ai import AsyncWebCrawler


class DetailEnrichmentMixin:
    """Adds parallel detail-page enrichment to a BaseScraper subclass.

    The base collect_items hook calls _enrich(), which this mixin overrides.
    Subclasses implement extract_detail_fields() and optionally should_enrich()
    to control which items get a detail fetch.
    """

    def should_enrich(self, item: dict[str, Any]) -> bool:
        return True

    def extract_detail_fields(self, raw_html: str) -> dict[str, Any]:
        raise NotImplementedError

    async def _enrich_one(
        self,
        crawler: AsyncWebCrawler,
        item: dict[str, Any],
        timeout_ms: int,
        logger: logging.Logger,
    ) -> dict[str, Any]:
        try:
            detail_html = await self._fetch_html(crawler, item["url"], timeout_ms)
            item.update(self.extract_detail_fields(detail_html))
        except Exception:
            logger.exception("Detail enrichment failed for %s; keeping listing fields.", item["url"])
        return item

    async def _enrich(
        self,
        crawler: AsyncWebCrawler,
        items: list[dict[str, Any]],
        timeout_ms: int,
        logger: logging.Logger,
    ) -> list[dict[str, Any]]:
        to_enrich = [i for i in items if self.should_enrich(i)]
        if to_enrich:
            logger.info("Enriching %s detail page(s).", len(to_enrich))
            await asyncio.gather(
                *(self._enrich_one(crawler, i, timeout_ms, logger) for i in to_enrich)
            )
        return items
