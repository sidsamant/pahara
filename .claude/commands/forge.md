# Forge — Generate Scrapers for Discovered Targets

You are running the **Forge** workflow for the crawl4ai project at `d:\ai\crawl4ai`.

Forge reads `discovery_targets` rows with `status='discovered'`, generates a scraper
class for each one following the project's conventions, writes the file to
`src/scrapers/`, and registers it in the `ScrapperType` enum and `SCRAPER_REGISTRY`.

Arguments: $ARGUMENTS
(Supported: `--target-id N` to process one specific target, `--dry-run` to print code without writing files)

**Defaults:** process at most **10 targets** per run unless `--target-id N` is given.
Each crawl4ai page fetch is retried up to **3 times** before the target is skipped.

---

## Step 1 — Load the targets to process

```bash
cd /d/ai/crawl4ai && .venv/Scripts/python -c "
from dotenv import load_dotenv; load_dotenv()
from agents_use_skills.db import get_targets_by_status, get_existing_source_links
import json
rows = get_targets_by_status('discovered')
existing = get_existing_source_links()
rows = [r for r in rows if r['newsroom_url'].rstrip('/').lower() not in existing]
print(json.dumps(rows, indent=2))
"
```

This filters out any target whose `newsroom_url` already matches a `link` in the `sources`
table (normalised: lowercase, trailing slash stripped). The company's newsroom is already
covered and must not be regenerated. Note: scraper_key names may differ between the two
tables, so URL matching is used instead.

If `--target-id N` is in $ARGUMENTS, filter to that single id.
Otherwise apply the default limit of **10** (take the 10 oldest `discovered` rows).
If there are no eligible targets after filtering, stop and report.

---

## Step 2 — Read the project conventions (do this once)

Read these files so you understand the patterns to follow when generating code:

- [src/scrapers/base.py](../src/scrapers/base.py)
- [src/scrapers/mixins.py](../src/scrapers/mixins.py)
- [src/scrapers/satsure_newsroom.py](../src/scrapers/satsure_newsroom.py) — deep-crawl reference
- [src/scrapers/nsil_news.py](../src/scrapers/nsil_news.py) — single-page reference

---

## Step 3 — For each target: analyse the newsroom page

Fetch the newsroom listing page and one sample article with crawl4ai.
Retry each fetch up to **3 times** before giving up and skipping the target.

```bash
cd /d/ai/crawl4ai && .venv/Scripts/python -c "
import asyncio, json
from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig, DefaultMarkdownGenerator
from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy

MAX_RETRIES = 3

async def fetch_with_retry(crawler, url, cfg):
    for attempt in range(1, MAX_RETRIES + 1):
        r = await crawler.arun(url, config=cfg)
        if r.success:
            return r
        print(f'  Attempt {attempt}/{MAX_RETRIES} failed for {url}: {r.error_message}')
    return None

async def analyse(newsroom_url, url_pattern):
    cfg = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        scraping_strategy=LXMLWebScrapingStrategy(),
        markdown_generator=DefaultMarkdownGenerator(),
        page_timeout=30000, delay_before_return_html=2.0,
        wait_until='networkidle', verbose=False,
    )
    async with AsyncWebCrawler() as c:
        listing = await fetch_with_retry(c, newsroom_url, cfg)
        if not listing:
            print('SKIPPED: listing page unreachable after', MAX_RETRIES, 'retries')
            return
        print('=== LISTING PAGE ===')
        print('URL:', listing.url)
        print('MARKDOWN:', (listing.markdown or '')[:3000])

        fragment = url_pattern.replace('*','').strip('/')
        links = (listing.links or {}).get('internal', [])
        sample = next((l['href'] for l in links if fragment and fragment in l.get('href','') and l.get('href') != listing.url), '')
        print('SAMPLE_ARTICLE_URL:', sample)

        if sample:
            art = await fetch_with_retry(c, sample, cfg)
            if art:
                print('=== SAMPLE ARTICLE ===')
                print('URL:', art.url)
                print('METADATA:', json.dumps(dict(art.metadata or {}), indent=2)[:2000])
                print('MARKDOWN:', (art.markdown or '')[:2000])

asyncio.run(analyse('NEWSROOM_URL', 'URL_PATTERN'))
"
```

Substitute `NEWSROOM_URL` and `URL_PATTERN` from the target row.

**Study the output** to understand:
- Which `result.metadata` keys are populated (title, description, og:image, article:published_time, etc.)
- What URL pattern individual articles follow
- Whether the listing page uses JS rendering (affects whether delay is needed)

---

## Step 4 — Generate the scraper file

Write a scraper class for this target. You are the code author — generate it directly.

**Required structure** (follow `satsure_newsroom.py` exactly):

```python
# imports: copy from satsure_newsroom.py, adjust as needed
import logging
import re
from typing import Any
from urllib.parse import urldefrag

from crawl4ai import (AsyncWebCrawler, CacheMode, ContentTypeFilter,
    CrawlerRunConfig, DefaultMarkdownGenerator, FilterChain, URLPatternFilter)
from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy

from .base import BaseScraper, PROJECT_ROOT

_DATE_PATTERN = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")

def _normalise(url: str) -> str:
    return urldefrag(url.rstrip("/"))[0]

class ClassName(BaseScraper):

    def extract_items(self, raw_html, source_link):
        return []   # required stub — collect_items is overridden

    async def collect_items(self, source_link, timeout_ms, logger):
        # BFSDeepCrawlStrategy with URLPatternFilter("URL_PATTERN")
        # log results, skip listing page, call _result_to_item
        ...

    def _result_to_item(self, result, source_link):
        # extract all required fields from result.metadata / result.markdown / result.media
        # return None if title or url is missing
        ...

scrape_source = ClassName().scrape_source
```

**Required item fields** — every dict must have all of these:

| Field | Source |
|---|---|
| `id` | `f"press_release:{url}"` or `f"news:{url}"` |
| `section` | e.g. `"newsroom"` or `"press_release"` |
| `type` | e.g. `"News"` or `"Press Release"` |
| `title` | `result.metadata.get("title")` |
| `description` | `result.metadata.get("description")` or first non-heading markdown line |
| `published_date` | `result.metadata.get("article:published_time")[:10]` or `_DATE_PATTERN` on markdown, else `""` |
| `url` | `result.url` |
| `image` | `result.metadata.get("og:image")` or first image in `result.media` |
| `read_time` | `""` |
| `button_text` | `"Read More"` |
| `external` | `False` |
| `source_page` | `source_link` |

**Hard rules:**
- No regex applied to raw HTML. Use `result.metadata`, `result.markdown`, `result.media` only.
- Exclude the listing page: `if _normalise(result.url) == _normalise(source_link): continue`
- Log failed results with `logger.warning(...)` — never raise from `_result_to_item`
- `scrape_source = ClassName().scrape_source` must be the final line

If `--dry-run` is in $ARGUMENTS, print the generated code and skip Steps 5–7.

---

## Step 5 — Write the scraper file

Write the generated code to `src/scrapers/<scraper_key>.py`.

---

## Step 6 — Register the scraper

**In `src/utils/util.py`** — add an enum value to `ScrapperType` before `X_LATEST_POSTS`:
```python
    SCRAPER_KEY_UPPER = "scraper_key"
```

**In `src/scrapers/__init__.py`** — add an import before the `x_latest_posts` import:
```python
from src.scrapers.scraper_key import scrape_source as scrape_scraper_key
```
And add a registry entry before `ScrapperType.X_LATEST_POSTS.value`:
```python
    ScrapperType.SCRAPER_KEY_UPPER.value: scrape_scraper_key,
```

---

## Step 7 — Update the database

```bash
cd /d/ai/crawl4ai && .venv/Scripts/python -c "
from dotenv import load_dotenv; load_dotenv()
from agents_use_skills.db import update_target
update_target(TARGET_ID, status='code_ready', scraper_file='src/scrapers/SCRAPER_KEY.py')
print('Updated target TARGET_ID to code_ready')
"
```

---

## Step 8 — Repeat for remaining targets

Process every target from Step 1. After all are done, print a summary:

```bash
cd /d/ai/crawl4ai && .venv/Scripts/python -c "
from dotenv import load_dotenv; load_dotenv()
from agents_use_skills.db import get_targets_by_status
rows = get_targets_by_status('code_ready')
print(f'{len(rows)} target(s) now code_ready:')
for r in rows:
    print(f'  [{r[\"id\"]}] {r[\"org_name\"]:30s}  {r[\"scraper_file\"]}')
"
```

Next step: run `/sentinel` to review all `code_ready` scrapers.
