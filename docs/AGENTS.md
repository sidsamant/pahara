# Agent Pipeline — Technical Documentation

## Overview

The agent pipeline automates the end-to-end process of discovering Indian space industry
news sources, generating scrapers for them, and reviewing the generated code for quality.

Three agents work in sequence. Each agent has a single, well-defined responsibility:

```
Scout  →  discovery_targets (status=discovered)
           ↓
         Forge  →  src/scrapers/<key>.py  +  discovery_targets (status=code_ready)
                    ↓
                 Sentinel  →  scraper_reviews  +  discovery_targets (status=approved | needs_revision)
                               ↓
                            Manual step: python -m src.manage_sources add ...
                               ↓
                            sources table  →  run_sources picks it up
```

---

## Agents

### Scout

**File:** `agents_use_skills/scout/agent.py`  
**Purpose:** Discover Indian space industry organisations and confirm that they publish
news on their own domain.

**Workflow:**

1. Calls Claude (`claude-sonnet-4-6`) once with a research prompt asking for a structured
   list of Indian space organisations — including their newsroom URL and article URL pattern.
   Claude provides this directly from training knowledge; no page content is fetched.
2. For each organisation, sends a lightweight HTTP HEAD request to verify the newsroom URL
   is reachable. No page body is read, no content is analysed.
3. Writes confirmed sources to `discovery_targets` with `status = 'discovered'`.

**Inputs:**  
- `ANTHROPIC_API_KEY` (env)  
- `DATABASE_URL` (env)

**Outputs:**  
- Rows in `discovery_targets`

**CLI:**
```powershell
python -m agents_use_skills.scout.agent                # run and write to DB
python -m agents_use_skills.scout.agent --dry-run      # print JSON, skip DB
python -m agents_use_skills.scout.agent --limit 5      # process at most 5 orgs
```

**Claude calls per run:**  
**1 call total** — one enumeration call regardless of how many organisations are processed.

---

### Forge

**File:** `agents/forge/agent.py`  
**Purpose:** Generate a working scraper file for each discovered target.

**Workflow:**

1. Reads `discovery_targets` rows with `status = 'discovered'`.
2. Uses crawl4ai to fetch the newsroom listing page and one sample article page.
3. Builds a prompt that includes the project's `BaseScraper` source, `DetailEnrichmentMixin`,
   and the `satsure_newsroom.py` reference scraper as static context
   (cached with `cache_control: ephemeral` to reduce token costs on repeat calls).
4. Calls Claude (`claude-opus-4-7`) with the page analysis output and asks it to generate
   a complete, valid Python scraper class.
5. Validates the output with `ast.parse()` — rejects syntactically invalid code.
6. Writes the scraper to `src/scrapers/<scraper_key>.py`.
7. Registers the scraper:
   - Adds an enum value to `ScrapperType` in `src/utils/util.py`.
   - Adds an import and registry entry to `src/scrapers/__init__.py`.
8. Updates `discovery_targets`: `scraper_file`, `status = 'code_ready'`.

**Inputs:**  
- `discovery_targets` rows with `status = 'discovered'`  
- `ANTHROPIC_API_KEY` (env)  
- `DATABASE_URL` (env)

**Outputs:**  
- `src/scrapers/<scraper_key>.py`  
- Updated `src/utils/util.py` (ScrapperType enum)  
- Updated `src/scrapers/__init__.py` (SCRAPER_REGISTRY)  
- Updated `discovery_targets` rows

**CLI:**
```powershell
python -m agents.forge.agent                     # process all discovered targets
python -m agents.forge.agent --target-id 3       # process one specific target
python -m agents.forge.agent --dry-run           # print generated code, write nothing
```

**Claude calls per run:**  
1 call per target (code generation). Prompt caching means the static project context
(~3 000 tokens) is charged at the cache read rate from the second call onwards.

---

### Sentinel

**File:** `agents/sentinel/agent.py`  
**Purpose:** Review generated scraper code for correctness and project compliance.

**Workflow:**

1. Reads `discovery_targets` rows with `status = 'code_ready'`.
2. Reads the scraper file from disk.
3. Builds a review prompt including the `BaseScraper` and `DetailEnrichmentMixin` source
   as cached context plus a numbered checklist covering structure, crawl4ai usage, item
   fields, error handling, and registration.
4. Calls Claude (`claude-sonnet-4-6`) asking for a structured JSON verdict.
5. Writes the verdict to `scraper_reviews`.
6. Sets `discovery_targets.status` to `'approved'` or `'needs_revision'`.

**Checklist categories:**  
- **S** Structure — class hierarchy, required method stubs  
- **C** crawl4ai usage — BFSDeepCrawlStrategy, URLPatternFilter, no raw HTML parsing  
- **F** Item fields — all required keys, correct formats  
- **E** Error handling — failed results logged and skipped  
- **R** Registration — `scrape_source` at the end of the file

**Inputs:**  
- `discovery_targets` rows with `status = 'code_ready'`  
- Scraper files on disk  
- `ANTHROPIC_API_KEY` (env)  
- `DATABASE_URL` (env)

**Outputs:**  
- Rows in `scraper_reviews`  
- Updated `discovery_targets` rows

**CLI:**
```powershell
python -m agents.sentinel.agent                  # review all code_ready targets
python -m agents.sentinel.agent --target-id 3   # review one specific target
```

**Claude calls per run:**  
1 call per target. Prompt caching applies for the static checklist context.

---

## Database Tables

### `discovery_targets`

| Column | Type | Description |
|---|---|---|
| `id` | BIGSERIAL | Primary key |
| `org_name` | TEXT | Full organisation name |
| `website` | TEXT | Organisation homepage |
| `newsroom_url` | TEXT | Confirmed news/press-release listing URL |
| `url_pattern` | TEXT | Glob pattern for article URLs, e.g. `*/press-release/*` |
| `page_hints` | TEXT | JSON blob of crawl4ai observations from Scout |
| `scraper_key` | TEXT UNIQUE | Snake-case identifier, e.g. `agnikul_cosmos` |
| `scraper_file` | TEXT | Relative path to generated scraper, set by Forge |
| `status` | TEXT | `discovered` → `code_ready` → `approved` / `needs_revision` |
| `discovered_at` | TEXT | ISO timestamp |
| `updated_at` | TEXT | ISO timestamp |

### `scraper_reviews`

| Column | Type | Description |
|---|---|---|
| `id` | BIGSERIAL | Primary key |
| `target_id` | BIGINT FK | References `discovery_targets.id` |
| `scraper_file` | TEXT | Path reviewed |
| `verdict` | TEXT | `approved` or `needs_revision` |
| `issues` | TEXT | JSON array of specific checklist failures |
| `suggestions` | TEXT | JSON array of non-blocking suggestions |
| `reviewed_at` | TEXT | ISO timestamp |

---

## Configuration

All three agents read from the same `.env` file used by the rest of the project:

```env
DATABASE_URL=postgresql://user:password@localhost:5432/crawl4ai
ANTHROPIC_API_KEY=sk-ant-...
```

---

## Cost Profile

| Agent | Model | Calls per full run | Notes |
|---|---|---|---|
| Scout | claude-sonnet-4-6 | **1** | Single enumeration call; URL check is a plain HTTP HEAD |
| Forge | claude-opus-4-7 | 1 per target | Prompt caching reduces repeat cost |
| Sentinel | claude-sonnet-4-6 | 1 per target | Prompt caching applies |

Prompt caching (marked with `cache_control: ephemeral`) means the static project context
sent by Forge and Sentinel (~3 000 tokens) is charged at the cache read rate
(roughly 10 % of the standard input rate) from the second target onwards in the same run.

For 20 newly discovered targets a typical full pipeline costs approximately **$0.50–$2.00**
depending on the length of the generated scraper code and how many article pages Forge crawls.

---

## Alternatives to Direct API Calls

The agents are designed for **fully autonomous, unattended** runs.  
If that level of automation is not required, or if you want to avoid direct API billing,
there are cheaper and simpler options.

### Option A — Claude Code Skills (recommended, zero additional API cost)

Claude Code skills are slash commands stored in `.claude/commands/`. When invoked,
Claude Code follows the instructions using its own reasoning and built-in tools
(Read, Write, Edit, Bash) — **no separate Anthropic API calls are made**.

| Skill | File | What it does |
|---|---|---|
| `/scout` | `.claude/commands/scout.md` | Discovers orgs + newsroom URLs, writes to `discovery_targets` |
| `/forge` | `.claude/commands/forge.md` | Generates and registers scrapers for discovered targets |
| `/sentinel` | `.claude/commands/sentinel.md` | Reviews generated code, writes verdict to `scraper_reviews` |

**Key difference from the Python agents:** Claude Code *is* the LLM. Scout uses
Claude Code's own knowledge to enumerate companies. Forge writes scraper code
directly. Sentinel reviews code using its own reasoning. No `ANTHROPIC_API_KEY`
is needed and no per-token cost is incurred.

**Run order:**
```
/scout                        # discover orgs → discovery_targets
/forge                        # generate scrapers → src/scrapers/
/sentinel                     # review scrapers → scraper_reviews
```

Pass arguments after the skill name:
```
/scout --dry-run --limit 5
/forge --target-id 3
/sentinel --target-id 3
```

**When to use:** Day-to-day source additions, one-off discoveries, or any time
a human is present to review the output at each step.

---

### Option B — Anthropic Batch API (50 % cost reduction)

For Forge and Sentinel, each target is independent — a perfect fit for
[Anthropic's Message Batches API](https://docs.anthropic.com/en/docs/build-with-claude/batch-processing).
Batch requests are processed asynchronously and cost 50 % less than real-time calls.

Refactor the agents to submit all targets as a batch, poll for completion, then
process the results. Turnaround time is typically under 1 hour.

**When to use this:** For large batches of new targets (10+) where latency does not matter.

---

### Option C — Smaller models for cheaper tasks

Not every step needs `claude-opus-4-7`. Scout and Sentinel work well on
`claude-sonnet-4-6`. For very simple, well-structured newsroom pages,
`claude-haiku-4-5` can generate adequate scrapers at a fraction of the cost.

Adjust the `SCOUT_MODEL`, `FORGE_MODEL`, and `SENTINEL_MODEL` constants in each
agent file to experiment with cost/quality trade-offs.

---

### Summary

| Approach | Cost | Automation | Human effort |
|---|---|---|---|
| **Claude Code Skills** (`/scout`, `/forge`, `/sentinel`) | Subscription only | Semi (user-triggered) | Low |
| Autonomous Python agents | ~$0.50–$2.00 per pipeline run | Full (schedulable) | Minimal |
| Autonomous agents + Batch API | ~$0.25–$1.00 per pipeline run | Full (async) | Minimal |
| Autonomous agents + smaller models | ~$0.05–$0.30 per pipeline run | Full | Minimal, quality risk |

Recommended path:
- **Day-to-day / on-demand:** Claude Code Skills — free within subscription, human reviews each step.
- **Scheduled / unattended discovery:** Python agents, optionally with Batch API for larger runs.
