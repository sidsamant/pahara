-- PostgreSQL schema for the crawl4ai crawler database.
--
-- Run this once against a fresh database:
--     psql $DATABASE_URL -f schema.sql
--
-- All timestamps are stored as TEXT in ISO-8601 format (same convention as
-- the Python code) so they sort lexicographically and need no special type.
-- The "enabled" and "external" columns are INTEGER (0/1) for simplicity.

-- -----------------------------------------------------------------------
-- sources
--   One row per news source that the crawler knows about.
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sources (
    id             BIGSERIAL    PRIMARY KEY,
    name           TEXT         NOT NULL UNIQUE,
    link           TEXT         NOT NULL UNIQUE,
    folder_name    TEXT         NOT NULL UNIQUE,
    scraper_key    TEXT         NOT NULL,
    enabled        INTEGER      NOT NULL DEFAULT 1,
    created_at_utc TEXT         NOT NULL,
    updated_at_utc TEXT         NOT NULL
);

-- -----------------------------------------------------------------------
-- runs
--   One row per execution of run_sources.py.
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
    id                 BIGSERIAL  PRIMARY KEY,
    started_at_utc     TEXT       NOT NULL,
    finished_at_utc    TEXT,
    status             TEXT       NOT NULL,
    log_dir            TEXT       NOT NULL,
    results_path       TEXT,
    total_sources      INTEGER    NOT NULL DEFAULT 0,
    succeeded_sources  INTEGER    NOT NULL DEFAULT 0,
    failed_sources     INTEGER    NOT NULL DEFAULT 0
);

-- -----------------------------------------------------------------------
-- run_sources
--   One row per (run, source) pair — tracks per-source outcome.
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run_sources (
    id                  BIGSERIAL  PRIMARY KEY,
    run_id              BIGINT     NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    source_id           BIGINT     NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    started_at_utc      TEXT       NOT NULL,
    finished_at_utc     TEXT,
    status              TEXT       NOT NULL,
    log_path            TEXT       NOT NULL,
    output_path         TEXT,
    total_current_count INTEGER    NOT NULL DEFAULT 0,
    new_items_count     INTEGER    NOT NULL DEFAULT 0,
    error_text          TEXT,
    UNIQUE (run_id, source_id)
);

-- -----------------------------------------------------------------------
-- scraped_items
--   Permanent store of every item ever returned by a scraper.
--   (source_id, item_id) is the natural key; the scraper guarantees item_id
--   is stable across runs for the same article/post.
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scraped_items (
    id                BIGSERIAL  PRIMARY KEY,
    source_id         BIGINT     NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    run_source_id     BIGINT     REFERENCES run_sources(id) ON DELETE SET NULL,
    item_id           TEXT       NOT NULL,
    section           TEXT       NOT NULL DEFAULT '',
    item_type         TEXT       NOT NULL DEFAULT '',
    scrapper_type     TEXT       NOT NULL DEFAULT '',
    title             TEXT       NOT NULL DEFAULT '',
    description       TEXT       NOT NULL DEFAULT '',
    published_date    TEXT       NOT NULL DEFAULT '',
    url               TEXT       NOT NULL DEFAULT '',
    image             TEXT       NOT NULL DEFAULT '',
    read_time         TEXT       NOT NULL DEFAULT '',
    button_text       TEXT       NOT NULL DEFAULT '',
    external          INTEGER    NOT NULL DEFAULT 0,
    source_page       TEXT       NOT NULL DEFAULT '',
    raw_json          TEXT       NOT NULL DEFAULT '',
    hashcode          TEXT       NOT NULL DEFAULT '',
    first_seen_at_utc TEXT       NOT NULL,
    last_seen_at_utc  TEXT       NOT NULL,
    UNIQUE (source_id, item_id)
);

-- Index used by filter_unseen_items() to quickly check for duplicates.
CREATE INDEX IF NOT EXISTS idx_scraped_items_scrapper_type_hashcode
    ON scraped_items (scrapper_type, hashcode);

-- =======================================================================
-- Agent pipeline tables
-- These are written by the Scout → Forge → Sentinel agent pipeline and
-- are separate from the scraper runtime tables above.
-- =======================================================================

-- -----------------------------------------------------------------------
-- discovery_targets
--   Scout writes one row per discovered organisation.
--   Forge and Sentinel update the row as they process it.
--
--   Status lifecycle:
--     discovered  → Scout confirmed a live newsroom URL
--     code_ready  → Forge wrote and registered the scraper file
--     approved    → Sentinel reviewed the code and approved it
--     needs_revision → Sentinel found issues; Forge must re-process
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS discovery_targets (
    id            BIGSERIAL  PRIMARY KEY,
    org_name      TEXT       NOT NULL,
    website       TEXT       NOT NULL,
    newsroom_url  TEXT       NOT NULL,
    -- Glob pattern for individual article URLs, e.g. "*/press-release/*"
    url_pattern   TEXT       NOT NULL DEFAULT '',
    -- JSON blob of CSS/structure observations captured during Scout crawl
    page_hints    TEXT       NOT NULL DEFAULT '{}',
    -- Value used as ScrapperType enum key and SCRAPER_REGISTRY key
    scraper_key   TEXT       NOT NULL UNIQUE,
    -- Relative path to the generated scraper file, set by Forge
    scraper_file  TEXT       NOT NULL DEFAULT '',
    status        TEXT       NOT NULL DEFAULT 'discovered',
    discovered_at TEXT       NOT NULL,
    updated_at    TEXT       NOT NULL
);

-- -----------------------------------------------------------------------
-- scraper_reviews
--   Sentinel writes one row per review attempt.
--   Multiple reviews may exist for the same target (e.g. after revision).
-- -----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scraper_reviews (
    id            BIGSERIAL  PRIMARY KEY,
    target_id     BIGINT     NOT NULL REFERENCES discovery_targets(id) ON DELETE CASCADE,
    scraper_file  TEXT       NOT NULL,
    -- "approved" or "needs_revision"
    verdict       TEXT       NOT NULL,
    -- JSON array of specific issues found (empty array if none)
    issues        TEXT       NOT NULL DEFAULT '[]',
    -- JSON array of improvement suggestions (empty array if none)
    suggestions   TEXT       NOT NULL DEFAULT '[]',
    reviewed_at   TEXT       NOT NULL
);
