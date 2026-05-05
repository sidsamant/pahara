import html
import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from .base import BaseScraper
from .mixins import DetailEnrichmentMixin

LISTING_PATTERN = re.compile(
    r'<div role="listitem" class="media__filtering_item w-dyn-item">.*?'
    r'<img[^>]+src="(?P<image>https://[^"]+)"[^>]*class="media__filtering_box-img.*?".*?'
    r'<div fs-list-field="category" class="media__filtering_box-tag\s*">(?P<published_date>.*?)</div>.*?'
    r'<h3 fs-list-field="name" class="font__inter text-size-18 text-style-3lines">(?P<title>.*?)</h3>.*?'
    r'<a data-animation="link" href="(?P<url>/news/[^"]+)" class="events__listing__link',
    re.DOTALL,
)

DETAIL_CONTENT_PATTERN = re.compile(
    r'<section class="newsroom__content">.*?<div class="text-rich-text w-richtext">(?P<content>.*?)</div>.*?</section>',
    re.DOTALL,
)

PARAGRAPH_PATTERN = re.compile(r"<p>(?P<text>.*?)</p>", re.DOTALL)


class PixxelScraper(BaseScraper, DetailEnrichmentMixin):
    encoding_fixes = {
        "\u00c3\u00a2\u00e2\u201a\u00ac\u00e2\u201e\u00a2": "\u2019",
        "\u00c3\u00a2\u00e2\u201a\u00ac\u00cb\u0153": "\u2018",
        "\u00c3\u00a2\u00e2\u201a\u00ac\u00c5\u201c": "\u201c",
        "\u00c3\u00a2\u00e2\u201a\u00ac\u00e2\u20ac\u0153": "\u2013",
        "\u00e2\u20ac\u2122": "\u2019",
        "\u00e2\u20ac\u02dc": "\u2018",
        "\u00e2\u20ac\u0153": "\u201c",
        "\u00e2\u20ac\u201c": "\u2013",
        "\u00e2\u20ac\u201d": "\u2014",
        "\u00e2\u201e\u00a2": "\u2122",
        "\u00e2\u201a\u00b9": "\u20b9",
    }

    def _normalize_published_date(self, value: str) -> str:
        from datetime import datetime
        cleaned = self.strip_html(value)
        try:
            return datetime.strptime(cleaned, "%B %d, %Y").date().isoformat()
        except ValueError:
            return cleaned

    def extract_items(self, raw_html: str, source_link: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for match in LISTING_PATTERN.finditer(raw_html):
            absolute_url = urljoin(source_link, html.unescape(match.group("url")).strip())
            item_id = f"news:{absolute_url}"
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            items.append({
                "id": item_id,
                "section": "newsroom",
                "type": "News",
                "title": self.strip_html(match.group("title")),
                "description": "",
                "published_date": self._normalize_published_date(match.group("published_date")),
                "url": absolute_url,
                "image": html.unescape(match.group("image")).strip(),
                "read_time": "",
                "button_text": "Read now",
                "external": False,
                "source_page": source_link,
            })
        return items

    def extract_detail_fields(self, raw_html: str) -> dict[str, Any]:
        match = DETAIL_CONTENT_PATTERN.search(raw_html)
        if not match:
            return {"description": ""}
        paragraphs: list[str] = []
        for paragraph_match in PARAGRAPH_PATTERN.finditer(match.group("content")):
            text = self.strip_html(paragraph_match.group("text"))
            if text and text not in paragraphs:
                paragraphs.append(text)
        return {"description": " ".join(paragraphs[:3]).strip()}

    def _sort_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            items,
            key=lambda i: (i.get("published_date") or "", i.get("title") or ""),
            reverse=True,
        )


scrape_source = PixxelScraper().scrape_source
