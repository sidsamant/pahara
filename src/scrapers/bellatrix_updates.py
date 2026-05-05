import html
import re
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlparse

from .base import BaseScraper
from .mixins import DetailEnrichmentMixin

POST_PATTERN = re.compile(
    r'data-framer-name="Post".*?'
    r'data-framer-name="Title".*?<a[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
    r'data-framer-name="Date".*?<p[^>]*>(?P<published_date>.*?)</p>.*?'
    r'data-framer-name="Preload"[^>]+href="(?P<preload_href>[^"]+)".*?'
    r'<img[^>]+src="(?P<image>https://[^"]+)"',
    re.DOTALL,
)

DETAIL_CONTENT_PATTERN = re.compile(
    r'data-framer-name="Content">(?P<content>.*?)<div class="ssr-variant',
    re.IGNORECASE | re.DOTALL,
)

PARAGRAPH_PATTERN = re.compile(r"<p[^>]*>(?P<text>.*?)</p>", re.DOTALL)


class BellatrixScraper(BaseScraper, DetailEnrichmentMixin):
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
        cleaned = self.strip_html(value)
        try:
            return datetime.strptime(cleaned, "%B %d, %Y").date().isoformat()
        except ValueError:
            return cleaned

    def _looks_like_display_date(self, value: str) -> bool:
        cleaned = self.strip_html(value)
        for fmt in ("%d %B %Y", "%B %d, %Y"):
            try:
                datetime.strptime(cleaned, fmt)
                return True
            except ValueError:
                continue
        return False

    def _is_internal_url(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.netloc in {"bellatrix.aero", "www.bellatrix.aero"} and parsed.path.startswith("/updates/")

    def should_enrich(self, item: dict[str, Any]) -> bool:
        return self._is_internal_url(item["url"])

    def extract_items(self, raw_html: str, source_link: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for match in POST_PATTERN.finditer(raw_html):
            absolute_url = urljoin(source_link, html.unescape(match.group("href")).strip())
            item_id = f"news:{absolute_url}"
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            parsed = urlparse(absolute_url)
            is_external = parsed.netloc not in {"bellatrix.aero", "www.bellatrix.aero"}
            items.append({
                "id": item_id,
                "section": "updates",
                "type": "News",
                "title": self.strip_html(match.group("title")),
                "description": "",
                "published_date": self._normalize_published_date(match.group("published_date")),
                "url": absolute_url,
                "image": html.unescape(match.group("image")).strip(),
                "read_time": "",
                "button_text": "Read More",
                "external": is_external,
                "source_page": source_link,
            })
        return items

    def extract_detail_fields(self, raw_html: str) -> dict[str, Any]:
        content_match = DETAIL_CONTENT_PATTERN.search(raw_html)
        if not content_match:
            return {"description": ""}
        paragraphs: list[str] = []
        for paragraph_match in PARAGRAPH_PATTERN.finditer(content_match.group("content")):
            text = self.strip_html(paragraph_match.group("text"))
            if not text:
                continue
            if self._looks_like_display_date(text):
                continue
            if text in paragraphs:
                continue
            if text.startswith("For media inquiries") or text.startswith("Email:") or text.startswith("Copyright @"):
                break
            paragraphs.append(text)
        return {"description": " ".join(paragraphs[:3]).strip()}

    def _sort_items(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            items,
            key=lambda i: (i.get("published_date") or "", i.get("title") or ""),
            reverse=True,
        )


scrape_source = BellatrixScraper().scrape_source
