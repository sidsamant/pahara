"""
Forge — scraper authoring agent for the Indian space industry newsletter.

Workflow
--------
For each discovery_targets row with status='discovered':

1. Use crawl4ai to fetch the newsroom listing page and one sample article.
2. Build a prompt that includes:
     - The project's BaseScraper and DetailEnrichmentMixin source code
       (sent with prompt caching so repeated calls are cheap).
     - The crawl4ai output (listing markdown, sample article metadata/markdown).
     - The target's URL pattern and org details.
3. Ask Claude (claude-opus-4-7, best code model) to generate a complete
   scraper class following the project's conventions.
4. Validate the output is syntactically valid Python (ast.parse).
5. Write the scraper file to src/scrapers/<scraper_key>.py.
6. Register the new scraper:
     - Add enum value to src/utils/util.py  (ScrapperType)
     - Add import + registry entry to src/scrapers/__init__.py
7. Update discovery_targets: scraper_file, status='code_ready'.

Running
-------
    python -m agents.forge.agent                  # process all 'discovered' targets
    python -m agents.forge.agent --target-id 3   # process one specific target
    python -m agents.forge.agent --dry-run        # print generated code, don't write files
"""

import argparse
import ast
import asyncio
import json
import logging
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import anthropic
from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig, DefaultMarkdownGenerator
from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from agents_use_skills.db import get_targets_by_status, get_existing_source_links, update_target

logger = logging.getLogger(__name__)

# claude-opus-4-7 produces the best code quality for complex generation tasks.
FORGE_MODEL = "claude-opus-4-7"

# Paths to the project files Forge reads at startup and caches in the prompt.
_BASE_PY       = PROJECT_ROOT / "src" / "scrapers" / "base.py"
_MIXINS_PY     = PROJECT_ROOT / "src" / "scrapers" / "mixins.py"
_EXAMPLE_PY    = PROJECT_ROOT / "src" / "scrapers" / "satsure_newsroom.py"
_UTIL_PY       = PROJECT_ROOT / "src" / "utils" / "util.py"
_INIT_PY       = PROJECT_ROOT / "src" / "scrapers" / "__init__.py"
_SCRAPERS_DIR  = PROJECT_ROOT / "src" / "scrapers"


# ---------------------------------------------------------------------------
# System prompt — the static part is sent once per session with cache_control
# so repeated Forge calls within a session hit the prompt cache.
# ---------------------------------------------------------------------------

def _build_system_prompt() -> list[dict]:
    """
    Build the system prompt content blocks.

    The project source files are included as a cached block so they are only
    tokenised once per session, even when Forge processes multiple targets.
    """
    base_code    = _BASE_PY.read_text(encoding="utf-8")
    mixins_code  = _MIXINS_PY.read_text(encoding="utf-8")
    example_code = _EXAMPLE_PY.read_text(encoding="utf-8")

    static_context = f"""\
You are Forge, an AI coding agent that writes web scrapers for the crawl4ai project.

# Project conventions you MUST follow

## BaseScraper API  (src/scrapers/base.py)
```python
{base_code}
```

## DetailEnrichmentMixin  (src/scrapers/mixins.py)
```python
{mixins_code}
```

## Reference scraper — deep-crawl style  (src/scrapers/satsure_newsroom.py)
```python
{example_code}
```

## Required item fields
Every dict in the returned items list MUST have these keys:
  id             : "press_release:<url>" or "news:<url>"
  section        : short string, e.g. "newsroom" or "press_release"
  type           : human-readable, e.g. "News" or "Press Release"
  title          : from result.metadata.get("title") or result.metadata.get("og:title")
  description    : from result.metadata.get("description") or first non-heading markdown line
  published_date : "YYYY-MM-DD" from result.metadata.get("article:published_time"), or "" if unknown
  url            : result.url
  image          : result.metadata.get("og:image") or first image from result.media["images"]
  read_time      : "" (leave blank unless clearly available)
  button_text    : "Read More"
  external       : False
  source_page    : the newsroom listing URL passed in as source_link

## Hard rules
1. Extend BaseScraper.  Never implement raw HTML scraping (no regex on html strings).
2. Use crawl4ai APIs only: BFSDeepCrawlStrategy, URLPatternFilter, result.metadata,
   result.markdown, result.media.  These are already imported in the reference scraper.
3. Override collect_items() with a BFSDeepCrawlStrategy deep crawl (max_depth=1).
4. Implement extract_items() as a stub returning [] — it is required by the ABC.
5. Extract data from CrawlResult fields (metadata, markdown, media), NOT raw HTML.
6. Filter out the listing page itself using _normalise(result.url) == _normalise(source_link).
7. Add _normalise(url) helper (strip trailing slash + fragment) — copy from reference.
8. scrape_source = ClassName().scrape_source must be the last line in the file.
9. Log failures per-result with logger.warning(); never raise from _result_to_item.
10. Output ONLY valid Python source — no markdown fences, no explanation text.
"""

    return [
        {
            "type": "text",
            "text": static_context,
            # Mark as ephemeral so Anthropic caches this block for ~5 minutes.
            "cache_control": {"type": "ephemeral"},
        }
    ]


# ---------------------------------------------------------------------------
# crawl4ai page analysis helpers
# ---------------------------------------------------------------------------

async def _analyse_newsroom(newsroom_url: str, url_pattern: str) -> dict:
    """
    Fetch the newsroom listing page and one sample article page.

    Returns a dict:
      listing_url      : final URL after redirects
      listing_markdown : first 4 000 chars of listing page markdown
      sample_url       : one article URL found on the listing page
      sample_markdown  : first 3 000 chars of sample article markdown
      sample_metadata  : result.metadata dict of the sample article
    """
    config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        scraping_strategy=LXMLWebScrapingStrategy(),
        markdown_generator=DefaultMarkdownGenerator(),
        page_timeout=30_000,
        delay_before_return_html=2.0,
        wait_until="networkidle",
        verbose=False,
    )

    out = {
        "listing_url": newsroom_url,
        "listing_markdown": "",
        "sample_url": "",
        "sample_markdown": "",
        "sample_metadata": {},
    }

    async with AsyncWebCrawler() as crawler:
        # --- Fetch the listing page ---
        listing = await crawler.arun(newsroom_url, config=config)
        if not listing.success:
            logger.warning("Forge › failed to fetch listing page %s", newsroom_url)
            return out

        out["listing_url"]     = listing.url
        out["listing_markdown"] = (listing.markdown or "")[:4_000]

        # --- Find a sample article link matching the URL pattern ---
        # Strip glob wildcards to get a plain substring for matching.
        pattern_fragment = url_pattern.replace("*", "").strip("/")
        internal_links = (listing.links or {}).get("internal", [])
        sample_url = next(
            (
                lnk["href"]
                for lnk in internal_links
                if pattern_fragment and pattern_fragment in lnk.get("href", "")
                and lnk.get("href", "") != listing.url
            ),
            "",
        )

        if not sample_url:
            logger.info("Forge › no sample article found for pattern '%s'", url_pattern)
            return out

        # --- Fetch the sample article ---
        article = await crawler.arun(sample_url, config=config)
        if article.success:
            out["sample_url"]      = article.url
            out["sample_markdown"] = (article.markdown or "")[:3_000]
            out["sample_metadata"] = dict(article.metadata or {})

    return out


# ---------------------------------------------------------------------------
# Claude code generation
# ---------------------------------------------------------------------------

def _generate_scraper(
    client: anthropic.Anthropic,
    system: list[dict],
    target: dict,
    page_analysis: dict,
) -> str:
    """
    Ask Claude to generate a complete scraper Python file for *target*.

    Returns the raw string of Python source code.
    """
    org_name    = target["org_name"]
    scraper_key = target["scraper_key"]
    newsroom_url = target["newsroom_url"]
    url_pattern  = target["url_pattern"]

    # Derive class name: "agnikul_cosmos" → "AgnikulCosmosScraper"
    class_name = "".join(word.capitalize() for word in scraper_key.split("_")) + "Scraper"

    user_message = f"""\
Generate a scraper for this source.

## Target details
org_name     : {org_name}
scraper_key  : {scraper_key}
newsroom_url : {newsroom_url}
url_pattern  : {url_pattern}
class_name   : {class_name}
file_path    : src/scrapers/{scraper_key}.py

## crawl4ai analysis of the newsroom listing page
listing_url      : {page_analysis['listing_url']}
listing_markdown :
{page_analysis['listing_markdown']}

## crawl4ai analysis of a sample article page
sample_url       : {page_analysis['sample_url']}
sample_metadata  : {json.dumps(page_analysis['sample_metadata'], indent=2)[:1_500]}
sample_markdown  :
{page_analysis['sample_markdown']}

## What to generate
A single Python file (no markdown fences) containing:
1. Necessary imports (copy the import block from the reference scraper and adjust).
2. _normalise(url) helper function.
3. _DATE_PATTERN regex for extracting YYYY-MM-DD from markdown.
4. {class_name}(BaseScraper) class with:
   - extract_items() stub
   - collect_items() override using BFSDeepCrawlStrategy + URLPatternFilter("{url_pattern}")
   - _result_to_item() extracting all required fields from CrawlResult
5. scrape_source = {class_name}().scrape_source  (last line)

Use the sample_metadata keys you can see above to choose the right metadata field names.
If article:published_time is absent, fall back to searching markdown with _DATE_PATTERN.
"""

    response = client.messages.create(
        model=FORGE_MODEL,
        max_tokens=8_192,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )

    return response.content[0].text.strip()


# ---------------------------------------------------------------------------
# File writing and registration
# ---------------------------------------------------------------------------

def _validate_python(code: str, scraper_key: str) -> bool:
    """Return True if *code* is syntactically valid Python."""
    try:
        ast.parse(code)
        return True
    except SyntaxError as exc:
        logger.error("Forge › generated code for '%s' has a syntax error: %s", scraper_key, exc)
        return False


def _write_scraper_file(scraper_key: str, code: str) -> Path:
    """Write the generated code to src/scrapers/<scraper_key>.py."""
    target_path = _SCRAPERS_DIR / f"{scraper_key}.py"
    target_path.write_text(code, encoding="utf-8")
    logger.info("Forge › wrote %s", target_path.relative_to(PROJECT_ROOT))
    return target_path


def _register_in_util(scraper_key: str) -> None:
    """
    Add  NEW_KEY = "scraper_key"  to the ScrapperType enum in util.py.

    Inserts the new value alphabetically before X_LATEST_POSTS, which is
    always kept last in the enum by convention.
    """
    enum_key = scraper_key.upper()
    new_line  = f'    {enum_key} = "{scraper_key}"\n'

    content = _UTIL_PY.read_text(encoding="utf-8")

    # Skip if already registered (idempotent).
    if f'    {enum_key} = ' in content:
        logger.info("Forge › %s already in ScrapperType — skipping util.py edit.", enum_key)
        return

    # Insert before the X_LATEST_POSTS line (alphabetically last by convention).
    if "    X_LATEST_POSTS = " not in content:
        logger.warning("Forge › could not find X_LATEST_POSTS anchor in util.py — skipping.")
        return

    updated = content.replace(
        "    X_LATEST_POSTS = ",
        f"{new_line}    X_LATEST_POSTS = ",
    )
    _UTIL_PY.write_text(updated, encoding="utf-8")
    logger.info("Forge › added ScrapperType.%s to util.py", enum_key)


def _register_in_init(scraper_key: str) -> None:
    """
    Add an import line and a SCRAPER_REGISTRY entry to src/scrapers/__init__.py.

    Both insertions use X_LATEST_POSTS as an anchor, keeping the file sorted.
    """
    enum_key   = scraper_key.upper()
    import_line   = f"from src.scrapers.{scraper_key} import scrape_source as scrape_{scraper_key}\n"
    registry_line = f"    ScrapperType.{enum_key}.value: scrape_{scraper_key},\n"

    content = _INIT_PY.read_text(encoding="utf-8")

    # Insert import before the x_latest_posts import (keeps imports grouped).
    if import_line not in content:
        content = content.replace(
            "from src.scrapers.x_latest_posts import",
            f"{import_line}from src.scrapers.x_latest_posts import",
        )

    # Insert registry entry before the X_LATEST_POSTS registry line.
    if registry_line not in content:
        content = content.replace(
            "    ScrapperType.X_LATEST_POSTS.value:",
            f"{registry_line}    ScrapperType.X_LATEST_POSTS.value:",
        )

    _INIT_PY.write_text(content, encoding="utf-8")
    logger.info("Forge › registered scrape_%s in SCRAPER_REGISTRY.", scraper_key)


# ---------------------------------------------------------------------------
# Main Forge workflow
# ---------------------------------------------------------------------------

async def run_forge(target_id: int | None = None, dry_run: bool = False) -> None:
    """
    Process all 'discovered' targets (or one specific target if target_id is set).
    """
    client = anthropic.Anthropic()

    # Build the cached system prompt once — reused for every target this session.
    system = _build_system_prompt()

    targets = get_targets_by_status("discovered")
    if target_id is not None:
        targets = [t for t in targets if t["id"] == target_id]

    # Skip targets whose newsroom_url already exists as a source link — the company's
    # newsroom is already covered and does not need a new scraper.
    # Match on normalised URL (lowercase, trailing slash stripped) to handle minor variations.
    existing_links = get_existing_source_links()
    if existing_links:
        before = len(targets)
        targets = [
            t for t in targets
            if t["newsroom_url"].rstrip("/").lower() not in existing_links
        ]
        skipped = before - len(targets)
        if skipped:
            logger.info("Forge › skipped %d target(s) already in sources table.", skipped)

    if not targets:
        logger.info("Forge › no 'discovered' targets to process.")
        return

    logger.info("Forge › processing %d target(s).", len(targets))

    for target in targets:
        org_name    = target["org_name"]
        scraper_key = target["scraper_key"]
        logger.info("Forge › starting %s (%s)...", org_name, scraper_key)

        # Step 1 — crawl4ai page analysis
        logger.info("Forge › analysing newsroom page for %s...", org_name)
        page_analysis = await _analyse_newsroom(
            newsroom_url=target["newsroom_url"],
            url_pattern=target["url_pattern"],
        )

        # Step 2 — Claude code generation
        logger.info("Forge › generating scraper code for %s...", org_name)
        code = _generate_scraper(client, system, target, page_analysis)

        # Step 3 — validate syntax
        if not _validate_python(code, scraper_key):
            logger.error("Forge › skipping %s due to invalid generated code.", org_name)
            continue

        if dry_run:
            print(f"\n{'='*60}\n# Scraper for {org_name}  (key={scraper_key})\n{'='*60}")
            print(code)
            continue

        # Step 4 — write file and register
        scraper_file = _write_scraper_file(scraper_key, code)
        _register_in_util(scraper_key)
        _register_in_init(scraper_key)

        # Step 5 — update discovery_targets row
        relative_path = str(scraper_file.relative_to(PROJECT_ROOT)).replace("\\", "/")
        update_target(
            target["id"],
            status="code_ready",
            scraper_file=relative_path,
        )
        logger.info("Forge › ✓ %s → %s (status=code_ready)", org_name, relative_path)

    logger.info("Forge › done.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Forge — generates scraper code for discovered targets."
    )
    parser.add_argument(
        "--target-id",
        type=int,
        default=None,
        metavar="ID",
        help="Process only the discovery_targets row with this id.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print generated code to stdout without writing any files.",
    )
    return parser


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    args = _build_parser().parse_args()
    asyncio.run(run_forge(target_id=args.target_id, dry_run=args.dry_run))
