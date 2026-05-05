import html
import re
from typing import Any

from .base import BaseScraper
from .mixins import DetailEnrichmentMixin

LISTING_PATTERN = re.compile(
    r'<div class="nw_bl_rw row_section">\s*'
    r'<img src="(?P<icon>[^"]+)">\s*'
    r'<h3>\s*'
    r'<a href="(?P<url>[^"]+)">(?P<title>.*?)</a>.*?'
    r'<span class="date-display-single"[^>]*content="(?P<published_iso>[^"]+)"[^>]*>(?P<published_text>.*?)</span>',
    re.DOTALL,
)

DETAIL_BLOCK_PATTERN = re.compile(
    r'<div class="section news_details">(?P<detail_block>.*?)</div>\s*</div>\s*</section>',
    re.DOTALL,
)

PARAGRAPH_PATTERN = re.compile(r"<p>(?P<text>.*?)</p>", re.DOTALL)
IMAGE_PATTERN = re.compile(r'<img[^>]+src="(?P<src>[^"]+)"', re.DOTALL)


class NSILScraper(BaseScraper, DetailEnrichmentMixin):

    def extract_items(self, raw_html: str, source_link: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for match in LISTING_PATTERN.finditer(raw_html):
            url = html.unescape(match.group("url")).strip()
            title = self.strip_html(match.group("title"))
            item_id = f"news:{url}"
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            items.append({
                "id": item_id,
                "section": "news",
                "type": "News",
                "title": title,
                "description": "",
                "published_date": match.group("published_iso").split("T", 1)[0],
                "published_date_text": self.strip_html(match.group("published_text")),
                "url": url,
                "image": "",
                "read_time": "",
                "button_text": "Read More",
                "external": False,
                "source_page": source_link,
            })
        return items

    def extract_detail_fields(self, raw_html: str) -> dict[str, Any]:
        match = DETAIL_BLOCK_PATTERN.search(raw_html)
        if not match:
            return {"description": "", "image": ""}
        block = match.group("detail_block")
        paragraphs: list[str] = []
        for paragraph_match in PARAGRAPH_PATTERN.finditer(block):
            text = self.strip_html(paragraph_match.group("text"))
            if text and text not in paragraphs:
                paragraphs.append(text)
        image_match = IMAGE_PATTERN.search(block)
        image = image_match.group("src").strip() if image_match else ""
        if image.startswith("/"):
            image = f"https://www.nsilindia.co.in{image}"
        return {
            "description": " ".join(paragraphs[:3]).strip(),
            "image": image,
        }


scrape_source = NSILScraper().scrape_source
