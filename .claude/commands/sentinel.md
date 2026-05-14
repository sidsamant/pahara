# Sentinel — Review Generated Scrapers

You are running the **Sentinel** workflow for the crawl4ai project at `d:\ai\crawl4ai`.

Sentinel reads `discovery_targets` rows with `status='code_ready'`, reviews each
generated scraper file against the project checklist, records the verdict in
`scraper_reviews`, and updates the target status to `approved` or `needs_revision`.

Arguments: $ARGUMENTS
(Supported: `--target-id N` to review one specific target)

**Defaults:** review at most **10 targets** per run unless `--target-id N` is given.
DB write retries: if `insert_scraper_review` or `update_target` fails, retry up to **3 times**
before logging the error and moving to the next target.

---

## Step 1 — Load targets to review

```bash
cd /d/ai/crawl4ai && .venv/Scripts/python -c "
from dotenv import load_dotenv; load_dotenv()
from agents_use_skills.db import get_targets_by_status
import json
rows = get_targets_by_status('code_ready')
print(json.dumps(rows, indent=2))
"
```

If `--target-id N` is in $ARGUMENTS, filter to that id only.
Otherwise apply the default limit of **10** (take the 10 oldest `code_ready` rows).
If there are no `code_ready` targets, stop and report.

---

## Step 2 — Read the base classes (do this once for context)

Read these so you know what the scraper must conform to:

- [src/scrapers/base.py](../src/scrapers/base.py)
- [src/scrapers/mixins.py](../src/scrapers/mixins.py)

---

## Step 3 — For each target: read and review the scraper file

Read the file at the path given in `scraper_file`. Then review it against every item
in the checklist below. You are the reviewer — no external LLM call is needed.

### Review checklist

**Structure**
- [ ] **S1** Class inherits `BaseScraper` (and optionally `DetailEnrichmentMixin`).
- [ ] **S2** `extract_items()` is implemented (even as a stub that returns `[]`).
- [ ] **S3** `collect_items()` is overridden and opens `AsyncWebCrawler`.
- [ ] **S4** `scrape_source = ClassName().scrape_source` is the final line.

**crawl4ai usage**
- [ ] **C1** Uses `BFSDeepCrawlStrategy` with `max_depth=1`.
- [ ] **C2** Uses `URLPatternFilter` with a specific non-wildcard-only pattern.
- [ ] **C3** Includes `ContentTypeFilter(allowed_types=["text/html"])`.
- [ ] **C4** No regex applied to raw HTML strings (regex on `result.markdown` is fine).
- [ ] **C5** Data comes only from `result.metadata`, `result.markdown`, `result.media`.

**Item fields**
- [ ] **F1** All required fields present: `id`, `section`, `type`, `title`, `description`,
             `published_date`, `url`, `image`, `read_time`, `button_text`, `external`, `source_page`.
- [ ] **F2** `id` format is `"category:url"` (e.g. `"press_release:https://..."`).
- [ ] **F3** `published_date` is `"YYYY-MM-DD"` or `""` — never `None`.
- [ ] **F4** `external` is Python `False` (bool), not `0` (int).
- [ ] **F5** The listing page URL is excluded from returned items.

**Error handling**
- [ ] **E1** Results where `result.success == False` are logged and skipped.
- [ ] **E2** `_result_to_item()` returns `None` for items with no title or URL, and the
             caller skips `None` results.

For each failed check, note the exact line or block and what needs to change.

---

## Step 4 — Decide the verdict

- **`approved`** — all checklist items pass. The scraper is ready to be added to sources.
- **`needs_revision`** — one or more checklist items fail. List every specific issue.

---

## Step 5 — Write the review to the database

Retry the DB write up to **3 times** on failure before skipping.

```bash
cd /d/ai/crawl4ai && .venv/Scripts/python -c "
from dotenv import load_dotenv; load_dotenv()
from agents_use_skills.db import insert_scraper_review, update_target
import json, time

MAX_RETRIES = 3

def with_retry(fn, *args, **kwargs):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            print(f'Attempt {attempt}/{MAX_RETRIES} failed: {e}')
            if attempt < MAX_RETRIES:
                time.sleep(1)
    raise RuntimeError('All retries exhausted')

review_id = with_retry(insert_scraper_review,
    target_id=TARGET_ID,
    scraper_file='SCRAPER_FILE',
    verdict='VERDICT',
    issues=json.loads('ISSUES_JSON'),
    suggestions=json.loads('SUGGESTIONS_JSON'),
)
with_retry(update_target, TARGET_ID, status='VERDICT')
print('Review id:', review_id, '| verdict:', 'VERDICT')
"
```

Replace `TARGET_ID`, `SCRAPER_FILE`, `VERDICT`, `ISSUES_JSON`, `SUGGESTIONS_JSON`.
`VERDICT` must be exactly `approved` or `needs_revision`.

---

## Step 6 — Fix issues immediately (optional but preferred)

If the verdict is `needs_revision`, fix the issues in the scraper file directly
rather than leaving them for a separate pass. After fixing:

1. Re-run this skill on the same target to confirm the fixes pass the checklist.
2. Or update `status` to `approved` manually if you are satisfied:

```bash
cd /d/ai/crawl4ai && .venv/Scripts/python -c "
from dotenv import load_dotenv; load_dotenv()
from agents_use_skills.db import update_target
update_target(TARGET_ID, status='approved')
print('Target TARGET_ID approved.')
"
```

---

## Step 7 — Repeat for remaining targets, then summarise

```bash
cd /d/ai/crawl4ai && .venv/Scripts/python -c "
from dotenv import load_dotenv; load_dotenv()
from agents_use_skills.db import get_all_targets, get_reviews_for_target
import json
targets = get_all_targets()
for t in targets:
    reviews = get_reviews_for_target(t['id'])
    latest = reviews[0] if reviews else None
    verdict = latest['verdict'] if latest else '—'
    print(f'[{t[\"id\"]}] {t[\"org_name\"]:30s}  status={t[\"status\"]:16s}  verdict={verdict}')
"
```

---

## After approval — add to sources

For each `approved` target, add it to the runtime sources table:

```bash
cd /d/ai/crawl4ai && .venv/Scripts/python -m src.manage_sources add \
  --name "ORG_NAME" \
  --link "NEWSROOM_URL" \
  --scraper-key "SCRAPER_KEY"
```

Then run `python -m src.run_sources` to include the new source in the next crawl.
