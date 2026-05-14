# Scout — Discover Indian Space Industry News Sources

You are running the **Scout** workflow for the crawl4ai project at `d:\ai\crawl4ai`.

Scout discovers Indian space industry organisations that publish news on their own domain.
It uses URL patterns only — no page content is fetched or analysed, minimising token usage.

Arguments: $ARGUMENTS
(Supported: `--dry-run` to print without writing to DB, `--limit N` to cap at N orgs)

**Defaults:** process at most **10 organisations** per run unless `--limit N` overrides it.
URL reachability is retried up to **3 times** before an org is skipped.

---

## Step 1 — Check what's already in the database

```bash
cd /d/ai/crawl4ai && .venv/Scripts/python -c "
from dotenv import load_dotenv; load_dotenv()
from agents_use_skills.db import get_all_targets
targets = get_all_targets()
print('Existing scraper_keys:', [t['scraper_key'] for t in targets] or 'none')
"
```

---

## Step 2 — Compile the organisation list

Using your own knowledge, build a list of Indian space industry organisations.
**Do not fetch any web pages.** You already know the newsroom URLs and URL patterns
for the major players from your training data.

For each organisation, determine:
- `org_name` — full name (e.g. "Agnikul Cosmos")
- `website` — homepage URL (e.g. `https://www.agnikul.in`)
- `newsroom_url` — full URL of the news/press-release listing page
- `url_pattern` — glob pattern for individual article URLs (e.g. `*/news/*`, `*/press-release/*`)

Include both government bodies and private companies. Starting points:
ISRO, NSIL, IN-SPACe, Skyroot Aerospace, Agnikul Cosmos, Pixxel, Digantara,
Bellatrix Aerospace, SatSure, Dhruva Space, GalaxEye, Ananth Technologies,
and any others you know are active. Skip any org whose `scraper_key` already
exists in Step 1.

Apply the limit: use `--limit N` from $ARGUMENTS if provided, otherwise default to **10**.
Prioritise the most prominent and active orgs when trimming the list.

---

## Step 3 — Verify each newsroom URL is reachable

For each organisation, check that the `newsroom_url` returns a non-error HTTP status.
Use a lightweight HEAD request — **do not read or analyse any page content**.
Retry up to **3 times** before marking the org as unreachable and skipping it.

```bash
cd /d/ai/crawl4ai && .venv/Scripts/python -c "
import urllib.request, urllib.error

MAX_RETRIES = 3
url = 'NEWSROOM_URL'
headers = {'User-Agent': 'Mozilla/5.0 (compatible; Scout/1.0)'}

def check(url):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, method='HEAD', headers=headers)
            with urllib.request.urlopen(req, timeout=10) as r:
                print('OK', r.status, url)
                return True
        except urllib.error.HTTPError as e:
            if e.code == 405:
                try:
                    req = urllib.request.Request(url, headers=headers)
                    with urllib.request.urlopen(req, timeout=10) as r:
                        print('OK (GET)', r.status, url)
                        return True
                except Exception as e2:
                    print(f'Attempt {attempt}/{MAX_RETRIES} GET failed: {e2}')
            else:
                print(f'Attempt {attempt}/{MAX_RETRIES} HTTP {e.code}')
        except Exception as e:
            print(f'Attempt {attempt}/{MAX_RETRIES} failed: {e}')
    print('UNREACHABLE:', url)
    return False

check(url)
"
```

Replace `NEWSROOM_URL` with the actual URL. Run once per org.
Skip orgs where the URL is unreachable after all retries.

---

## Step 4 — Derive the scraper_key

Convert the org name to snake_case:
- Lowercase, replace spaces and special characters with underscores.
- e.g. `"Agnikul Cosmos"` → `"agnikul_cosmos"`, `"IN-SPACe"` → `"in_space"`

---

## Step 5 — Record confirmed sources

If `--dry-run` is in $ARGUMENTS, print the details and stop here. Otherwise write to DB:

```bash
cd /d/ai/crawl4ai && .venv/Scripts/python -c "
from dotenv import load_dotenv; load_dotenv()
from agents_use_skills.db import insert_discovery_target
tid = insert_discovery_target(
    org_name='ORG_NAME',
    website='WEBSITE',
    newsroom_url='NEWSROOM_URL',
    url_pattern='URL_PATTERN',
    scraper_key='SCRAPER_KEY',
)
print('Recorded id:', tid)
"
```

Run one command per confirmed organisation.

---

## Step 6 — Print a summary

```bash
cd /d/ai/crawl4ai && .venv/Scripts/python -c "
from dotenv import load_dotenv; load_dotenv()
from agents_use_skills.db import get_targets_by_status
rows = get_targets_by_status('discovered')
print(f'{len(rows)} discovered target(s):')
for r in rows:
    print(f'  [{r[\"id\"]}] {r[\"org_name\"]:30s}  {r[\"newsroom_url\"]}')
"
```

---

## Notes

- Be conservative — only record orgs you are confident have active newsrooms.
- If a URL is unreachable, skip the org entirely; do not guess alternative URLs.
- The `insert_discovery_target` call is idempotent — re-running Scout with the same
  `scraper_key` updates the row rather than duplicating it.
- Next step: run `/forge` to generate scrapers for all `discovered` targets.
