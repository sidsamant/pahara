from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urldefrag, urljoin
from pathlib import Path
from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.pipeline_options import PdfPipelineOptions
from langdetect import detect, DetectorFactory

from crawl4ai import (
    AsyncWebCrawler,
    BrowserConfig,
    CacheMode,
    CrawlResult,
    CrawlerRunConfig,
    DefaultMarkdownGenerator,
)
from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy
from crawl4ai.processors.pdf import PDFContentScrapingStrategy, PDFCrawlerStrategy
import requests

from .base import BaseScraper, PROJECT_ROOT
from .mixins import CrawlResultItemMixin

NEWS_DETAIL_PATH_PATTERN = re.compile(r"/news-details/\d+", re.IGNORECASE)

OUTPUT_FOLDER = Path("processed_docs")
OUTPUT_FOLDER.mkdir(parents=True, exist_ok=True)


def _normalise(url: str) -> str:
    return urldefrag(url.rstrip("/"))[0]


def _ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


class NSILScraper(CrawlResultItemMixin, BaseScraper):
    result_item_id_prefix = "news"
    result_item_section = "news"
    result_item_type = "News"
    result_title_metadata_keys = ("og:title", "title")
    result_description_metadata_keys = ("og:description", "description")
    result_description_skip_prefixes = ("#", "!")
    result_description_min_length = 30

    def extract_items(self, raw_html: str, source_link: str) -> list[dict[str, Any]]:
        # Not used; collect_items explicitly crawls the listing page and detail pages.
        return []

    def _build_listing_config(self, timeout_ms: int) -> CrawlerRunConfig:
        return CrawlerRunConfig(
            scraping_strategy=LXMLWebScrapingStrategy(),
            cache_mode=CacheMode.BYPASS,
            page_timeout=timeout_ms,
            delay_before_return_html=1.0,
            wait_until="domcontentloaded",
            verbose=False,
        )

    def _build_detail_config(self, timeout_ms: int) -> CrawlerRunConfig:
        md_generator = DefaultMarkdownGenerator(
            options={"ignore_links": False, "ignore_images": True}
        )
        
        return CrawlerRunConfig(
            scraping_strategy=LXMLWebScrapingStrategy(),
            markdown_generator=md_generator,
            cache_mode=CacheMode.BYPASS,
            page_timeout=timeout_ms,
            delay_before_return_html=1.0,
            wait_until="domcontentloaded",
            verbose=False,
            css_selector="div.news_details" #works to ge the links inside the selection
        )

    def _build_pdf_config(self, timeout_ms: int) -> CrawlerRunConfig:
        return CrawlerRunConfig(
            # Prefer Crawl4AI's PDF scraping strategy over direct PDF libraries so
            # PDF content enters the same CrawlResult/markdown pipeline as pages.
            scraping_strategy=PDFContentScrapingStrategy(),
            cache_mode=CacheMode.BYPASS,
            page_timeout=timeout_ms,
            verbose=False,
            js_code=None
        )

    def _extract_detail_urls(self, result: CrawlResult, source_link: str) -> list[str]:
        internal_links =result.links.get("internal", [])

        urls: list[str] = []
        for link in internal_links:
            if isinstance(link, dict):
                raw_url = str(link.get("href") or link.get("url") or "").strip()
            else:
                raw_url = str(link or "").strip()
            if not raw_url:
                continue
            url = _normalise(urljoin(source_link, raw_url))
            if NEWS_DETAIL_PATH_PATTERN.search(url):
                urls.append(url)
        return _ordered_unique(urls)

    def _extract_first_pdf_url(self, result: CrawlResult) -> str:
        links = result.links
        for bucket_name in ("internal", "external"):
            for link in links.get(bucket_name, []) or []:
                if isinstance(link, dict):
                    raw_url = str(link.get("href") or link.get("url") or "").strip()
                else:
                    raw_url = str(link or "").strip()
                if raw_url and ".pdf" in raw_url.lower():
                    return _normalise(urljoin(result.url, raw_url))
        return ""

    def collect_english_only(self, doc:Any) -> str:
        # # Collect only text elements identified as English ("en")
        english_blocks = []

        # Iterate through all document items
        # for item in doc.iterate_items():
        for item in doc.texts:
            # Target item types that contain text
            if hasattr(item, "text") and item.text:
                text_content = item.text.strip()
                if not text_content:
                    continue
                try:
                    # Append only if the text language is English
                    if detect(text_content) == "en":
                        english_blocks.append(text_content)
                except Exception:
                    # Skip blocks that fail language detection (e.g., symbols, numbers)
                    continue

        return "\n\n".join(english_blocks)

    def _convert_bilingual_pdf_with_images(self, pdf_path: str, output_dir: str = "output_results"):
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # 1. Disable all advanced layout models, OCR, and table extraction
        pipeline_options = PdfPipelineOptions()
        pipeline_options.do_ocr = False
        pipeline_options.do_table_structure = False

        print(f"Processing document: {pdf_path}...")

        converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
            }
        )
        result = converter.convert(pdf_path)
        
        # 3. Export layout directly to structured Markdown format
        # This natively maps English, Hindi Unicode script strings, and table formats
        return self.collect_english_only(result.document)

    async def _extract_pdf_markdown(
        self,
        pdf_url: str,
        timeout_ms: int,
        logger: logging.Logger,
    ) -> str:
        if not pdf_url:
            return ""

        # URL of the PDF file you want to download
        # pdf_url = "https://media.geeksforgeeks.org/wp-content/uploads/20240226121023/GFG.pdf"
    

        # Local filename where you want to save the PDF
        local_filename = "downloaded_file.pdf"

        # 1. Send an HTTP GET request to the URL
        response = requests.get(pdf_url)
        
        text: str = "";
        # 2. Check if the request was successful
        if response.status_code == 200:
            try:
                # 3. Open a local file in 'write binary' (wb) mode
                with open(local_filename, "wb") as pdf_file:
                    # 4. Write the binary content to the file
                    pdf_file.write(response.content)
                text = self._convert_bilingual_pdf_with_images(pdf_path=local_filename)
            except Exception as exc:
                logger.warning("Failed to crawl NSIL PDF %s: %s", pdf_url, exc)
            print("PDF downloaded successfully!")
        else:
            print(f"Failed to download file. Status code: {response.status_code}")

        # # 1. Configure Crawl4AI to download the binary PDF
        # browser_config = BrowserConfig(
        #     accept_downloads=True,
        #     downloads_path=str(OUTPUT_FOLDER),
        #     headless=True
        # )
        # async with AsyncWebCrawler(
        #     config=browser_config
        #     # crawler_strategy=PDFCrawlerStrategy(),
        #     # base_directory=str(PROJECT_ROOT),
        # ) as pdf_crawler:
        #     try:
        #         logger.info("Extracting NSIL PDF content with Crawl4AI from %s", pdf_url)
        #         result = await pdf_crawler.arun(pdf_url, config=self._build_pdf_config(timeout_ms))
        #         if not result.downloaded_files:
        #             print("❌ Download failed.")
        #             return
                
        #         local_pdf_path = result.downloaded_files[0]
        #         print(f"✅ Downloaded: {local_pdf_path}")

        #         # 2. Configure Docling's Pipeline Options cleanly without importing missing classes
        #         # pipeline_options = PdfPipelineOptions()
        #         # pipeline_options.images_scale = 2.0  # Resolution scale for image rendering
        #         # pipeline_options.do_ocr = True        # Enable OCR fallback
                
        #         # Configure OCR target languages (English + Hindi) 
        #         # pipeline_options.ocr_options.lang = ["en", "hi"] 
        #         # 2. Process with Docling
        #         # print("Processing with Docling...")
        #         # converter = DocumentConverter(pipeline_options=pipeline_options)
        #         # docling_result = converter.convert(local_pdf_path)


        #         # # Export and save result
        #         # output_md = OUTPUT_FOLDER / "output.md"
        #         # with open(output_md, "w", encoding="utf-8") as f:
        #         #     f.write(docling_result.document.export_to_markdown())
        #         # print(f"✅ Extracted: {output_md}")

        #     except Exception as exc:
        #         logger.warning("Failed to crawl NSIL PDF %s: %s", pdf_url, exc)
        #         return ""
        #     if not result.success:
        #         logger.warning("Failed to extract NSIL PDF content from %s: %s", pdf_url, result.error_message)
        #         return ""

        # text = self._result_markdown(result).strip()
        return text

    async def collect_items(
        self, source_link: str, timeout_ms: int, logger: logging.Logger
    ) -> list[dict[str, Any]]:
        logger.info("Starting NSIL listing crawl for %s", source_link)

        items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        async with AsyncWebCrawler(
            config=self._browser_config(), base_directory=str(PROJECT_ROOT)
        ) as crawler:
            listing_result = await crawler.arun(
                source_link,
                config=self._build_listing_config(timeout_ms),
            )
            if not listing_result.success:
                raise RuntimeError(f"Failed to crawl NSIL listing page: {listing_result.error_message}")

            detail_urls = self._extract_detail_urls(listing_result, source_link)
            logger.info("NSIL listing yielded %s detail page URL(s).", len(detail_urls))

            for detail_url in detail_urls:
                result = await crawler.arun(detail_url, config=self._build_detail_config(timeout_ms))
                logger.debug("NSIL detail url=%s success=%s", detail_url, result.success)
                if not result.success:
                    logger.warning("Failed to crawl %s: %s", detail_url, result.error_message)
                    continue

                item = self.result_to_item(result, source_link)
                if not item or item["id"] in seen_ids:
                    continue

                pdf_url = self._extract_first_pdf_url(result)
                pdf_markdown = await self._extract_pdf_markdown(pdf_url, timeout_ms, logger)
                if pdf_markdown:
                    base_markdown = str(item.get("markdown") or "").strip()
                    item["markdown"] = "\n\n".join(part for part in (base_markdown, pdf_markdown) if part)
                    item["markdown_length"] = len(str(item["markdown"]))
                    item["pdf_url"] = pdf_url

                seen_ids.add(item["id"])
                items.append(item)

        logger.info("Collected %s NSIL item(s).", len(items))
        return self._sort_items(items)

    def _sort_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            items,
            key=lambda item: (item.get("published_date") or "", item.get("title") or ""),
            reverse=True,
        )

scrape_source = NSILScraper().scrape_source
