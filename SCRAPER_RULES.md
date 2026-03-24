# Scraper Rules

These rules apply to every new scraper added to this project.

## Scope

- One scraper file per source under `scrapers/`.
- One source record per site in the `sources` table.
- Each scraper must work within the existing multi-source runner and SQLite tracking model.

## Source Registration

- Every scraper must have a unique `scraper_key`.
- Add the scraper to `SCRAPER_REGISTRY` in `scrapers/__init__.py`.
- Seed the source in `seed_default_sources()` if it should be enabled by default.
- Source records must keep:
  - unique auto-generated `id`
  - unique `name`
  - unique `link`
  - `folder_name` in the format `<id>_<ShortSourceNameCamelCase>`

## Output Contract

- Every scraper must return a payload with:
  - `fetched_at_utc`
  - `namespace`
  - `source_name`
  - `source_link`
  - `returned_count`
  - `total_current_count`
  - `items`
- Every item should include:
  - `id`
  - `section`
  - `type`
  - `title`
  - `description`
  - `published_date`
  - `url`
  - `image`
  - `read_time`
  - `button_text`
  - `external`
  - `source_page`

## State

- Seen-item state must be stored in:
  - `sources/<folder_name>/state/seen_items.json`
- Results must be stored in:
  - `sources/<folder_name>/results/<timestamp>.json`
- The runner handles aggregate results in `data/runs/`.

## Logging

- Use the logger passed in by `run_sources.py`.
- Log:
  - start of source run
  - listing fetch
  - detail enrichment if used
  - item counts
  - any exceptional path before raising
- Do not create separate ad hoc log folders inside the scraper.

## Fetching Rules

- Use Crawl4AI for live HTML retrieval.
- Set `CRAWL4_AI_BASE_DIRECTORY` to the project root.
- Use `CacheMode.BYPASS`.
- Keep the scraper deterministic and DOM-focused.
- Do not use LLM extraction for these newsroom sources.

## Pagination Rule

- Do not implement pagination unless the user explicitly asks for it.
- Default behavior is page 1 only.
- If page 1 only is intentional, log that decision in the scraper.

## Parsing Rules

- Prefer stable server-rendered selectors or regexes tied to durable markup.
- Avoid brittle parsing that depends on animation classes unless no better option exists.
- If the listing page already exposes enough metadata, do not fetch detail pages unnecessarily.
- If detail pages are needed, fetch only the fields that improve result quality.

## IDs and Deduplication

- Item IDs must be stable across runs.
- Prefer URL-based IDs such as:
  - `news:<absolute_url>`
  - `listing:<absolute_url>`
- Deduplicate within the scraper before returning items.

## Resilience

- Return empty strings instead of failing on missing optional fields.
- Raise only when the page fetch fails or the page structure is fundamentally unusable.
- Keep first-page scraping working even if optional detail enrichment partially degrades.

## Quality Checklist

- `python -m py_compile` passes.
- The source appears in `manage_sources.py list`.
- `python .\run_sources.py --all-items` completes successfully.
- A new run row and run_source row are recorded in SQLite.
- A source folder, result file, and per-source log file are created.
