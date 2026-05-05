import html
import re
from typing import Any

from .base import BaseScraper

ITEM_PATTERN = re.compile(
    r'<div role="listitem" class="news-header_item w-dyn-item">.*?'
    r'(?P<anchor_tag><a[^>]+href="(?P<url>[^"]+)"[^>]*class="news-header_link w-inline-block">).*?'
    r'<div fs-cmsfilter-field="type" class="newsroom_detail-text text-size-xtiny">(?P<item_type>.*?)</div>.*?'
    r'<div class="newsroom_detail-text text-size-xtiny">(?P<published_date>.*?)</div>.*?'
    r'<h3 fs-cmsfilter-field="name" class="newsroom_subheading font-ppmori text-size-medium">(?P<title>.*?)</h3>',
    re.DOTALL,
)


class SkyRootScraper(BaseScraper):
    encoding_fixes = {
        "â€™": "’",
        "â€˜": "‘",
        "â€œ": "“",
        "â€“": "–",
        "â€”": "—",
    }

    def extract_items(self, raw_html: str, source_link: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for match in ITEM_PATTERN.finditer(raw_html):
            url = html.unescape(match.group("url")).strip()
            item_id = f"listing:{url}"
            if item_id in seen_ids:
                continue
            seen_ids.add(item_id)
            item_type = self.strip_html(match.group("item_type")).replace("  ", " ")
            items.append({
                "id": item_id,
                "section": "newsroom",
                "type": item_type,
                "title": self.strip_html(match.group("title")),
                "description": "",
                "published_date": self.strip_html(match.group("published_date")),
                "url": url,
                "image": "",
                "read_time": "",
                "button_text": "Read More",
                "external": 'target="_blank"' in match.group("anchor_tag"),
                "source_page": source_link,
            })
        return items


scrape_source = SkyRootScraper().scrape_source
