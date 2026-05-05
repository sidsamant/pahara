import asyncio
import html as html_module
import logging
import os
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("CRAWL4_AI_BASE_DIRECTORY", str(PROJECT_ROOT))

from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig

_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"\s+")


class BaseScraper(ABC):
    # Override in subclasses to fix mojibake characters specific to that site.
    encoding_fixes: dict[str, str] = {}

    # --- Crawl4AI plumbing ---

    def _build_run_config(self, timeout_ms: int) -> CrawlerRunConfig:
        return CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            page_timeout=timeout_ms,
            verbose=False,
            wait_until="domcontentloaded",
            delay_before_return_html=0.5,
        )

    def _browser_config(self) -> BrowserConfig:
        return BrowserConfig(headless=True, verbose=False)

    def _get_html(self, result: Any) -> str:
        for field_name in ("html", "cleaned_html", "fit_html"):
            value = getattr(result, field_name, None)
            if value:
                return value
        raise RuntimeError("Crawl4AI did not return HTML content.")

    async def _fetch_html(self, crawler: AsyncWebCrawler, url: str, timeout_ms: int) -> str:
        result = await crawler.arun(url=url, config=self._build_run_config(timeout_ms))
        if not result.success:
            raise RuntimeError(f"Failed to crawl {url}: {result.error_message}")
        return self._get_html(result)

    # --- Text cleaning ---

    def strip_html(self, value: str) -> str:
        value = html_module.unescape(value)
        value = _TAG_PATTERN.sub(" ", value)
        value = _WHITESPACE_PATTERN.sub(" ", value)
        try:
            value = value.encode("latin1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        for old, new in self.encoding_fixes.items():
            value = value.replace(old, new)
        return value.strip()

    # --- Extraction (subclasses implement this) ---

    @abstractmethod
    def extract_items(self, raw_html: str, source_link: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    # --- Hooks ---

    async def _enrich(
        self,
        crawler: AsyncWebCrawler,
        items: list[dict[str, Any]],
        timeout_ms: int,
        logger: logging.Logger,
    ) -> list[dict[str, Any]]:
        return items

    def _sort_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return items

    # --- Default collect: fetch listing → extract → enrich → sort ---

    async def collect_items(
        self, source_link: str, timeout_ms: int, logger: logging.Logger
    ) -> list[dict[str, Any]]:
        logger.info("Starting Crawl4AI fetch for %s", source_link)
        async with AsyncWebCrawler(
            config=self._browser_config(), base_directory=str(PROJECT_ROOT)
        ) as crawler:
            raw_html = await self._fetch_html(crawler, source_link, timeout_ms)
            items = self.extract_items(raw_html, source_link)
            items = await self._enrich(crawler, items, timeout_ms, logger)
        return self._sort_items(items)

    # --- Fixed entrypoint (never overridden) ---

    def scrape_source(
        self,
        *,
        source: Any,
        timeout_ms: int,
        logger: logging.Logger,
    ) -> dict[str, Any]:
        current_items = asyncio.run(self.collect_items(source.link, timeout_ms, logger))
        logger.info("Collected %s current item(s).", len(current_items))
        return {
            "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
            "namespace": source.folder_name,
            "source_name": source.name,
            "source_link": source.link,
            "total_current_count": len(current_items),
            "items": current_items,
        }
