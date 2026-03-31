# X Latest Posts Scraper Plan

## Goal

Add a new Crawl4AI-powered scraper that reads the latest visible posts from configured X accounts, uses a regular-user authenticated browser session, returns results in the existing scraper output contract, and persists deduplicated post rows into SQLite.

## Input Design

- Config file: `config/x_targets.json`
- Each configured account becomes one `sources` row with:
  - unique source `name`
  - normalized `link` like `https://x.com/<handle>`
  - shared `scraper_key` of `x_latest_posts`
- Auth options supported by config:
  - `storage_state_path`
  - `cookies_path`
  - `user_data_dir`

## Output Design

The scraper follows `SCRAPER_RULES.md` and returns:

- `fetched_at_utc`
- `namespace`
- `source_name`
- `source_link`
- `returned_count`
- `total_current_count`
- `items`

Each post item is normalized into:

- `id`: stable URL-based ID such as `tweet:https://x.com/<handle>/status/<post_id>`
- `section`: `posts`
- `type`: `Post`
- `title`: shortened first line of post text
- `description`: full post text
- `published_date`: X timestamp when available
- `url`: canonical post URL
- `image`: first media image URL when available
- `read_time`: empty string
- `button_text`: `View Post`
- `external`: `true`
- `source_page`: account profile URL

## Technical Plan

1. Keep source registration config-driven.
   `seed_default_sources()` now syncs X accounts from `config/x_targets.json`, creating one source record per configured profile.

2. Use Crawl4AI with authenticated browser state.
   The scraper builds `BrowserConfig` from one of three auth strategies:
   - persistent context via `user_data_dir`
   - Playwright `storage_state`
   - cookie array via `cookies`

3. Extract posts from the live DOM instead of raw static HTML assumptions.
   A `js_code` block runs after timeline render, walks `article[data-testid="tweet"]`, filters posts to the configured handle, and injects a JSON payload into the page.

4. Parse and normalize the injected JSON payload.
   Python reads the exported payload from the returned HTML, converts it into the project’s item contract, sorts newest-first, and deduplicates within the run.

5. Preserve existing file-based state.
   The latest visible item IDs are still written to `sources/<folder>/state/seen_items.json`, and per-run results still land in `sources/<folder>/results/<timestamp>.json`.

6. Persist rows into SQLite with duplicate protection.
   A new `scraped_items` table stores returned items using `UNIQUE (source_id, item_id)`. Re-seen posts update `last_seen_at_utc` instead of creating duplicates.

## Run Flow

1. Populate `config/x_targets.json` with X profile URLs.
2. Provide either `storage_state_path`, `cookies_path`, or `user_data_dir`.
3. Run `python .\\manage_sources.py list` to verify source registration.
4. Run `python .\\run_sources.py` for only unseen latest posts.
5. Run `python .\\run_sources.py --all-items` to return all currently visible posts for each configured account.

## Notes

- The implementation intentionally reads only the latest visible profile timeline posts and does not paginate.
- Duplicate prevention now exists in two places:
  - scraper state file prevents returning the same item again in normal runs
  - SQLite unique constraint prevents duplicate storage rows
- If X changes its logged-in DOM, the JavaScript extractor is the place to refresh selectors first.
