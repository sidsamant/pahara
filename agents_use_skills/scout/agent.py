"""
Scout — source discovery agent for the Indian space industry newsletter.

Workflow
--------
1. Ask Claude to produce a structured list of Indian space industry
   organisations with their newsroom URLs and article URL patterns.
   Claude provides this directly from its training knowledge — no page
   content is fetched or analysed, minimising token usage.
2. For each organisation, send a lightweight HTTP HEAD request to confirm
   the newsroom URL is reachable. No crawl4ai, no markdown, no LLM call.
3. Write confirmed sources to the discovery_targets database table.

Running
-------
    python -m agents_use_skills.scout.agent
    python -m agents_use_skills.scout.agent --dry-run   # print targets, don't write to DB
    python -m agents_use_skills.scout.agent --limit 5   # process at most 5 orgs
"""

import argparse
import json
import logging
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import anthropic

# Add project root to sys.path so "from src..." and "from agents_use_skills..."
# both work when this file is run directly.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from agents_use_skills.db import insert_discovery_target

logger = logging.getLogger(__name__)

SCOUT_MODEL = "claude-sonnet-4-6"
MAX_RETRIES = 3

# ---------------------------------------------------------------------------
# Discovery prompt — one LLM call per Scout run
# ---------------------------------------------------------------------------

_DISCOVERY_SYSTEM = """\
You are Scout, an AI research agent specialising in the Indian space industry.

Your job is to identify Indian space companies and organisations that publish
their own news or press releases directly on their own website — not through
external portals like Economic Times, Inc42, or YourStory.

Return a JSON array (no markdown fences, no prose). Each element must be:
{
  "org_name": "Full organisation name",
  "website": "https://their-homepage.com",
  "newsroom_url": "https://their-homepage.com/press-releases",
  "url_pattern": "*/press-release/*"
}

Rules:
- Only include organisations you are confident are active (published in the last 18 months).
- Include both government bodies (ISRO, NSIL, IN-SPACe) and private startups.
- Include at least 15 organisations.
- newsroom_url must be the full URL of the news/press-release listing page.
- url_pattern must be a glob pattern that matches individual article pages,
  e.g. "*/press-release/*", "*/news/*", "*/newsroom/*".
- Do not include news aggregators or external media sites.
"""


def _discover_organisations(client: anthropic.Anthropic) -> list[dict]:
    """
    Ask Claude (from its training knowledge) to enumerate Indian space orgs
    with their newsroom URL and article URL pattern. One API call per run.
    """
    logger.info("Scout › asking Claude to enumerate Indian space organisations...")

    response = client.messages.create(
        model=SCOUT_MODEL,
        max_tokens=2048,
        system=_DISCOVERY_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                "List Indian space industry companies and organisations that publish "
                "news or press releases on their own websites. Include ISRO, NSIL, "
                "IN-SPACe, Skyroot, Agnikul, Pixxel, Digantara, Bellatrix Aerospace, "
                "SatSure, Dhruva Space, GalaxEye, and any others you know of. "
                "For each, provide the full newsroom URL and the glob pattern for articles."
            ),
        }],
    )

    raw = response.content[0].text.strip()
    try:
        orgs = json.loads(raw)
        logger.info("Scout › Claude returned %d organisations.", len(orgs))
        return orgs
    except json.JSONDecodeError as exc:
        logger.error("Scout › failed to parse organisation list: %s\n%s", exc, raw)
        return []


# ---------------------------------------------------------------------------
# URL reachability check — no page content fetched
# ---------------------------------------------------------------------------

def _url_is_reachable(url: str) -> bool:
    """
    Send a HEAD request (falling back to GET on 405) to confirm the URL
    responds with a non-error HTTP status. Retries up to MAX_RETRIES times.
    No page body is read or stored.
    """
    headers = {"User-Agent": "Mozilla/5.0 (compatible; Scout/1.0)"}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, method="HEAD", headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status < 400:
                    return True
                logger.warning("Scout › HEAD %s → %s", url, resp.status)
        except urllib.error.HTTPError as exc:
            if exc.code == 405:
                # Server rejected HEAD — try a plain GET without reading the body.
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        if resp.status < 400:
                            return True
                except Exception as inner:
                    logger.warning(
                        "Scout › GET %s failed: %s (attempt %d/%d)",
                        url, inner, attempt, MAX_RETRIES,
                    )
            else:
                logger.warning(
                    "Scout › HEAD %s → HTTP %s (attempt %d/%d)",
                    url, exc.code, attempt, MAX_RETRIES,
                )
        except Exception as exc:
            logger.warning(
                "Scout › %s unreachable: %s (attempt %d/%d)",
                url, exc, attempt, MAX_RETRIES,
            )

    return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scraper_key(org_name: str) -> str:
    """"Agnikul Cosmos" → "agnikul_cosmos" """
    key = org_name.lower()
    key = re.sub(r"[^a-z0-9]+", "_", key)
    return key.strip("_")


# ---------------------------------------------------------------------------
# Main Scout workflow
# ---------------------------------------------------------------------------

def run_scout(dry_run: bool = False, limit: int | None = None) -> None:
    """
    Discover → verify URL exists → write to DB.

    One LLM call total (to enumerate orgs). No LLM calls for page analysis.
    """
    client = anthropic.Anthropic()

    orgs = _discover_organisations(client)
    if not orgs:
        logger.error("Scout › no organisations found — aborting.")
        return

    if limit:
        orgs = orgs[:limit]
        logger.info("Scout › limited to %d organisations.", limit)

    recorded = 0
    for org in orgs:
        org_name     = (org.get("org_name") or "").strip()
        website      = (org.get("website") or "").strip().rstrip("/")
        newsroom_url = (org.get("newsroom_url") or "").strip()
        url_pattern  = (org.get("url_pattern") or "").strip()

        if not org_name or not website or not newsroom_url:
            logger.warning("Scout › skipping incomplete entry: %s", org)
            continue

        scraper_key = _make_scraper_key(org_name)

        logger.info("Scout › verifying %s ...", newsroom_url)
        if not _url_is_reachable(newsroom_url):
            logger.info("Scout › %s newsroom unreachable — skipping.", org_name)
            continue

        logger.info(
            "Scout › ✓ %s  newsroom=%s  pattern=%s  key=%s",
            org_name, newsroom_url, url_pattern, scraper_key,
        )

        if dry_run:
            print(json.dumps({
                "org_name": org_name,
                "website": website,
                "newsroom_url": newsroom_url,
                "url_pattern": url_pattern,
                "scraper_key": scraper_key,
            }, indent=2))
        else:
            insert_discovery_target(
                org_name=org_name,
                website=website,
                newsroom_url=newsroom_url,
                url_pattern=url_pattern,
                scraper_key=scraper_key,
            )
            recorded += 1

    if not dry_run:
        logger.info("Scout › complete. %d target(s) written to discovery_targets.", recorded)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scout — discovers Indian space industry newsroom sources."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print discovered targets to stdout without writing to the database.",
    )
    parser.add_argument(
        "--limit", type=int, default=None, metavar="N",
        help="Process at most N organisations.",
    )
    return parser


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    args = _build_parser().parse_args()
    run_scout(dry_run=args.dry_run, limit=args.limit)
