"""
Sentinel — scraper code review agent for the Indian space industry newsletter.

Workflow
--------
For each discovery_targets row with status='code_ready':

1. Read the generated scraper file from disk.
2. Build a review prompt that includes:
     - The project's BaseScraper and convention rules (cached in prompt).
     - The full text of the scraper file to review.
3. Ask Claude (claude-sonnet-4-6) to review the code against a checklist
   and return a structured JSON verdict.
4. Write the review to the scraper_reviews table.
5. Update discovery_targets.status to 'approved' or 'needs_revision'.

Running
-------
    python -m agents.sentinel.agent                  # review all 'code_ready' targets
    python -m agents.sentinel.agent --target-id 3   # review one specific target
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import anthropic

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from agents_use_skills.db import (
    get_targets_by_status,
    insert_scraper_review,
    update_target,
)

logger = logging.getLogger(__name__)

# claude-sonnet-4-6 is sufficient for structured code review tasks and
# keeps review costs lower than Opus.
SENTINEL_MODEL = "claude-sonnet-4-6"

_BASE_PY    = PROJECT_ROOT / "src" / "scrapers" / "base.py"
_MIXINS_PY  = PROJECT_ROOT / "src" / "scrapers" / "mixins.py"


# ---------------------------------------------------------------------------
# System prompt — static checklist + project context, sent with cache_control
# ---------------------------------------------------------------------------

def _build_system_prompt() -> list[dict]:
    """
    Build the Sentinel system prompt.

    The project source context is cached so repeated reviews within the
    same session only pay the token cost once.
    """
    base_code   = _BASE_PY.read_text(encoding="utf-8")
    mixins_code = _MIXINS_PY.read_text(encoding="utf-8")

    static_context = f"""\
You are Sentinel, a strict but fair code reviewer for the crawl4ai scraper project.

# Project source — review against these

## BaseScraper  (src/scrapers/base.py)
```python
{base_code}
```

## DetailEnrichmentMixin  (src/scrapers/mixins.py)
```python
{mixins_code}
```

# Review checklist — verify every item

STRUCTURE
□ S1  Class inherits BaseScraper (and optionally DetailEnrichmentMixin).
□ S2  extract_items() is implemented (even as a stub returning []).
□ S3  collect_items() is overridden and uses AsyncWebCrawler from crawl4ai.
□ S4  scrape_source = ClassName().scrape_source is the final line.

CRAWL4AI USAGE
□ C1  Uses BFSDeepCrawlStrategy with max_depth=1.
□ C2  Uses URLPatternFilter with a specific pattern (not "*" alone).
□ C3  ContentTypeFilter(allowed_types=["text/html"]) is included.
□ C4  No regex applied to raw HTML strings (result.markdown is fine).
□ C5  Data extracted from result.metadata, result.markdown, result.media only.

ITEM FIELDS
□ F1  All required fields present: id, section, type, title, description,
      published_date, url, image, read_time, button_text, external, source_page.
□ F2  id follows "category:url" format (e.g. "press_release:https://...").
□ F3  published_date is "YYYY-MM-DD" or empty string — never None.
□ F4  external is a Python bool (False), not an integer.
□ F5  Listing page URL is excluded from returned items.

ERROR HANDLING
□ E1  Failed crawl results (result.success == False) are logged and skipped.
□ E2  _result_to_item() returns None for items with no title or URL.
□ E3  No unhandled exceptions that would crash the whole scraper run.

REGISTRATION
□ R1  File ends with scrape_source = ClassName().scrape_source (no extra code after).

# Response format

Return ONLY a JSON object — no markdown fences, no prose:
{{
  "verdict": "approved" or "needs_revision",
  "issues": [
    "S2: extract_items() is missing entirely",
    "F4: external field is set to 0 instead of False"
  ],
  "suggestions": [
    "Consider adding a 2-second delay_before_return_html to handle JS-heavy pages"
  ]
}}

issues   : specific checklist failures (reference the code with e.g. "S2:", "F4:").
           Empty array [] if there are no issues.
suggestions : optional improvements that are NOT blockers for approval.
              Empty array [] if there are none.
verdict  : "approved" only if issues is empty; "needs_revision" otherwise.
"""

    return [
        {
            "type": "text",
            "text": static_context,
            "cache_control": {"type": "ephemeral"},
        }
    ]


# ---------------------------------------------------------------------------
# Review logic
# ---------------------------------------------------------------------------

def _review_scraper(
    client: anthropic.Anthropic,
    system: list[dict],
    scraper_code: str,
    org_name: str,
    scraper_key: str,
) -> dict:
    """
    Send the scraper code to Claude for review.
    Returns the parsed JSON verdict dict, or a fallback error dict.
    """
    response = client.messages.create(
        model=SENTINEL_MODEL,
        max_tokens=2_048,
        system=system,
        messages=[{
            "role": "user",
            "content": (
                f"Review this scraper for {org_name} (key={scraper_key}):\n\n"
                f"```python\n{scraper_code}\n```"
            ),
        }],
    )

    raw = response.content[0].text.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error("Sentinel › failed to parse review JSON for %s: %s\n%s", org_name, exc, raw)
        # Return a safe fallback so the run doesn't crash.
        return {
            "verdict": "needs_revision",
            "issues": [f"Sentinel could not parse its own review output: {exc}"],
            "suggestions": [],
        }


# ---------------------------------------------------------------------------
# Main Sentinel workflow
# ---------------------------------------------------------------------------

def run_sentinel(target_id: int | None = None) -> None:
    """
    Review all 'code_ready' targets (or one specific target if target_id is set).

    This function is synchronous — no crawl4ai async work is done here.
    """
    client  = anthropic.Anthropic()
    system  = _build_system_prompt()

    targets = get_targets_by_status("code_ready")
    if target_id is not None:
        targets = [t for t in targets if t["id"] == target_id]

    if not targets:
        logger.info("Sentinel › no 'code_ready' targets to review.")
        return

    logger.info("Sentinel › reviewing %d target(s).", len(targets))

    for target in targets:
        org_name    = target["org_name"]
        scraper_key = target["scraper_key"]
        scraper_file = target.get("scraper_file", "")

        logger.info("Sentinel › reviewing %s (%s)...", org_name, scraper_key)

        # Load the scraper file Forge wrote.
        if not scraper_file:
            logger.error("Sentinel › no scraper_file path for target %d — skipping.", target["id"])
            continue

        file_path = PROJECT_ROOT / scraper_file
        if not file_path.exists():
            logger.error("Sentinel › scraper file not found: %s — skipping.", file_path)
            continue

        scraper_code = file_path.read_text(encoding="utf-8")

        # Ask Claude to review.
        review = _review_scraper(client, system, scraper_code, org_name, scraper_key)

        verdict     = review.get("verdict", "needs_revision")
        issues      = review.get("issues", [])
        suggestions = review.get("suggestions", [])

        # Write review record to DB.
        insert_scraper_review(
            target_id=target["id"],
            scraper_file=scraper_file,
            verdict=verdict,
            issues=issues,
            suggestions=suggestions,
        )

        # Update the target status.
        update_target(target["id"], status=verdict)

        # Log the outcome for the operator.
        if verdict == "approved":
            logger.info("Sentinel › ✓ APPROVED  %s", org_name)
        else:
            logger.warning(
                "Sentinel › ✗ NEEDS REVISION  %s\n  Issues:\n    %s",
                org_name,
                "\n    ".join(issues),
            )
            if suggestions:
                logger.info(
                    "Sentinel › Suggestions for %s:\n    %s",
                    org_name,
                    "\n    ".join(suggestions),
                )

    logger.info("Sentinel › review pass complete.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sentinel — reviews generated scraper code for quality and correctness."
    )
    parser.add_argument(
        "--target-id",
        type=int,
        default=None,
        metavar="ID",
        help="Review only the discovery_targets row with this id.",
    )
    return parser


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    args = _build_parser().parse_args()
    run_sentinel(target_id=args.target_id)
