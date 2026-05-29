import asyncio
import logging
import re
from datetime import datetime
from typing import Any

from crawl4ai import AsyncWebCrawler

_DATE_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DATE_LONG_PATTERN = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2}),\s+(\d{4})\b"
)
_NEWLINE_PATTERN = re.compile(r"[\r\n]+")
_CONSECUTIVE_SPACE_PATTERN = re.compile(r" {2,}")


class CrawlResultItemMixin:
    """Converts Crawl4AI detail-page results into scraper item dictionaries."""

    result_item_id_prefix = "press_release"
    result_item_section = "press_release"
    result_item_type = "Press Release"
    result_item_button_text = "Read More"
    result_title_metadata_keys = ("title",)
    result_ignored_titles: tuple[str, ...] = ()
    result_description_metadata_keys = ("description",)
    result_description_skip_prefixes = ("#",)
    result_description_min_length = 0
    result_date_metadata_keys = ("article:published_time", "og:article:published_time")
    result_skip_url_substrings: tuple[str, ...] = ()
    result_skip_url_suffixes: tuple[str, ...] = ()

    def _result_item_logger(self) -> logging.Logger:
        return logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    def _result_title(self, result: Any, url: str, logger: logging.Logger) -> str:
        # Prefer configured metadata keys because these usually carry the clean
        # document title from Crawl4AI/OpenGraph.
        scraper_name = self.__class__.__name__
        markdown = result.markdown or ""
        title = ""
        for key in self.result_title_metadata_keys:
            title = (result.metadata.get(key) or "").strip()
            if title:
                logger.debug("%s selected title from metadata key '%s' for %s.", scraper_name, key, url)
                break
        if title in self.result_ignored_titles:
            logger.debug("%s ignored generic metadata title '%s' for %s.", scraper_name, title, url)
            title = ""
        if not title:
            # Some sites expose a generic metadata title; fall back to the first
            # substantive markdown line after a heading.
            past_heading = False
            for line in markdown.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    past_heading = True
                    continue
                if past_heading and not line.startswith("!") and len(line) > 10:
                    title = line
                    logger.debug("%s selected title from markdown for %s.", scraper_name, url)
                    break
        return title

    def _result_description(
        self,
        result: Any,
        url: str,
        title: str,
        logger: logging.Logger,
    ) -> str:
        description = ""
        scraper_name = self.__class__.__name__
        markdown = result.markdown or ""

        # Description extraction mirrors title extraction: metadata first, then
        # markdown as a fallback for sites with sparse meta tags.
        for key in self.result_description_metadata_keys:
            description = result.metadata.get(key) or ""
            if description:
                logger.debug(
                    "%s selected description from metadata key '%s' for %s.",
                    scraper_name,
                    key,
                    url,
                )
                break
        if not description and markdown:
            for line in markdown.split("\n"):
                line = line.strip()
                if (
                    line
                    and line != title
                    and not line.startswith(self.result_description_skip_prefixes)
                    and len(line) > self.result_description_min_length
                ):
                    description = line
                    logger.debug("%s selected description from markdown for %s.", scraper_name, url)
                    break
        return description

    def _result_image(self, result: Any, url: str, logger: logging.Logger) -> str:
        # Prefer OpenGraph image, then fall back to the first media image
        # returned by Crawl4AI.
        scraper_name = self.__class__.__name__
        image = result.metadata.get("og:image") or ""
        if image:
            logger.debug("%s selected image from og:image for %s.", scraper_name, url)
        if not image:
            images = (result.media or {}).get("images", [])
            if images:
                image = images[0].get("src", "")
                logger.debug("%s selected image from Crawl4AI media for %s.", scraper_name, url)
        return image

    def _result_published_date(self, result: Any, url: str, logger: logging.Logger) -> str:
        scraper_name = self.__class__.__name__
        markdown = result.markdown or ""
        date_str = ""
        for key in self.result_date_metadata_keys:
            date_str = result.metadata.get(key) or ""
            if date_str:
                logger.debug("%s selected published date from metadata key '%s' for %s.", scraper_name, key, url)
                break
        if date_str:
            published_date = date_str[:10]
        else:
            # Crawl4AI markdown often contains dates even when page metadata does
            # not. Support ISO dates and long month-name dates.
            m = _DATE_PATTERN.search(markdown)
            if m:
                published_date = m.group(1)
                logger.debug("%s selected ISO published date from markdown for %s.", scraper_name, url)
            else:
                m = _DATE_LONG_PATTERN.search(markdown)
                if m:
                    try:
                        parsed_date = datetime.strptime(m.group(0), "%B %d, %Y")
                        published_date = parsed_date.date().isoformat()
                        logger.debug("%s selected long-form published date from markdown for %s.", scraper_name, url)
                    except ValueError:
                        published_date = ""
                        logger.debug("%s found invalid long-form date in markdown for %s.", scraper_name, url)
                else:
                    published_date = ""
                    logger.debug("%s found no published date for %s.", scraper_name, url)
        return published_date

    def _result_cleaned_html(self, result: Any) -> str:
        without_newlines = _NEWLINE_PATTERN.sub(" ", result.cleaned_html or "")
        return _CONSECUTIVE_SPACE_PATTERN.sub(" ", without_newlines).strip()

    def result_to_item(self, result: Any, source_link: str) -> dict[str, Any] | None:
        logger = self._result_item_logger()
        scraper_name = self.__class__.__name__
        url = result.url
        if not url:
            logger.debug("%s skipped Crawl4AI result because url is empty.", scraper_name)
            return None
        if any(part in url for part in self.result_skip_url_substrings):
            logger.debug("%s skipped url matching excluded substring: %s", scraper_name, url)
            return None
        if any(url.rstrip("/").endswith(suffix) for suffix in self.result_skip_url_suffixes):
            logger.debug("%s skipped url matching excluded suffix: %s", scraper_name, url)
            return None

        title = self._result_title(result, url, logger)
        if not title:
            logger.debug("%s skipped %s because no usable title was found.", scraper_name, url)
            return None

        description = self._result_description(result, url, title, logger)
        image = self._result_image(result, url, logger)
        published_date = self._result_published_date(result, url, logger)

        item = {
            "id": f"{self.result_item_id_prefix}:{url}",
            "section": self.result_item_section,
            "type": self.result_item_type,
            "title": title,
            "description": description,
            "published_date": published_date,
            "published_date_text": published_date,
            "url": url,
            "image": image,
            "read_time": "",
            "button_text": self.result_item_button_text,
            "external": False,
            "source_page": source_link,
            "cleaned_html": self._result_cleaned_html(result),
            "fit_html": result.fit_html,
            "extracted_content": result.extracted_content,
        }
        logger.debug("%s converted Crawl4AI result to item id=%s title=%s", scraper_name, item["id"], title)
        return item


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
