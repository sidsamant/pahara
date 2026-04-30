# Introduction

This document specifies the common information about the various types of scrappers.
Scrappers are used to read the content from various resources. The resources are sometimes called sources or targets.

## Scope
- Data is stored in SQLite database and the structure is specified in the [Data models section](#data-models)
- One source record per site in the `sources` table and serves as input to runs.
- A scrapper is Python source code and is used to read content from a specific source.
- Each scraper must work within the existing multi-source runner and SQLite tracking model.

## Types of scrappers
Common information about all types of scrappers are present at [COMMON_SCRAPER_RULES.md](docs/COMMON_SCRAPER_RULES.md)

Following types of scrappers are used for the sources:

1. Web page scrappers - These scrappers read from public webpages. Documentation about these scrappers can be found at [WEB_SCRAPER_RULES.md](WEB_SCRAPER_RULES.md)

2. X.com scrapper - This is a single scrapper that authenticates with X.com and scrapes posts from X.com. Documentation about the scrapper can be found at [X_SCRAPER_PLAN.md](X_SCRAPER_PLAN.md)

## Data models {#data-models}

- Data models for the database tables can be found as JSON files in the folder `src\models`.
- One JSON file for one table and it has description for the table and each of its fields and values indicate what are the data in the table rows.
- Data models should be updated when database tables are updated
 
## Source Registration

- Sources are registered in the sources table manually.
- A scrapper is associated with a source.
- Each of them can be enabled or disabled in the table. When enabled, the scrapper should target it. Otherwise it should skip targetting it.
- Every scraper must have a unique `scraper_key` and an id which is present in the sources table.
- The `scraper_key` field indicates the type of scrapper to be used for the source.
- The `folder_name` field is used to create output folder for a source. The preferred folder naming rule is `<id>_<ShortSourceNameCamelCase>`.

## Low Level Design

- `src\run_sources.py` runs mutliple scrappers for each of the listed sources or some of them.
- Each execution of run_sources.py is considered as a run.


## Output Contract
- Per-source output: `.\.output\sources\<folder_name>\results\<timestamp>.json`
- Aggregate run results: `.\.output\runs\<timestamp>.json`

- Every scraper must return a payload with:
  - `fetched_at_utc`
  - `namespace`
  - `source_name`
  - `source_link`
  - `total_current_count`
  - `items` — all items currently discoverable on the source (no filtering)

- `returned_count` is added by `run_sources.py` after deduplication and must not be set by the scraper.

- Every item in the scrapper output payload contains content scrapped.
  It should have following structure similar to `scrapped_items` table:
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

- `scrapper_type` and `hashcode` are computed by the runner (`run_sources.py`) before persisting to SQLite and must not be returned by the scraper.


## Instruction for AI
- Go through the data models and their JSONs and remember it to understand how the code works and for future development. DO NOT use other any other file to understand data models.

## State

- Deduplication is handled entirely by SQLite via the `(scrapper_type, hashcode)` index.
- Scrapers do not manage state files. There are no `seen_items.json` files.
- Results must be stored in:
  - `.output/sources/<folder_name>/results/<timestamp>.json`
- The runner handles aggregate results in `.output/runs/`.

## Logging

- Use the logger passed in by `run_sources.py`.
- Log:
  - start of source run
  - listing fetch
  - detail enrichment if used
  - item counts
  - any exceptional path before raising
- Do not create separate ad hoc log folders inside the scraper.
- Each source gets its own log file inside the current run's `.logs` folder.

## Fetching Rules

- Use Crawl4AI for live HTML retrieval.
- Set `CRAWL4_AI_BASE_DIRECTORY` to the project root.
- Use `CacheMode.BYPASS`.
- Keep the scraper deterministic and DOM-focused.
- Do not use LLM extraction for these newsroom sources.

## Pagination Rule

- Do not implement pagination.
- Only allowed behaviour is page 1 only.

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
- A new run row and run_source row or rows are recorded in SQLite.
- A source folder, result file, and per-source log file are created.
