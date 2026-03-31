# Multi-Source Newsroom Runner

This project uses `crawl4ai` plus SQLite to run all enabled newsroom sources, track each run, and write namespaced results into a dedicated folder per source.

## Data Model

- `sources`: unique source id, unique name, unique link, scraper key, enabled flag, folder name
- `runs`: one row per execution of the runner
- `run_sources`: one row per source inside a run, including status, output path, counts, and errors

## Runtime Layout

- SQLite database: `.\data\crawler.sqlite3`
- Per-run logs: `.\.logs\<timestamp>\`
- Per-source folders live under `.\sources\` using the `folder_name` stored in SQLite.
- Per-source results: `.\sources\<folder_name>\results\<timestamp>.json`
- Per-source state: `.\sources\<folder_name>\state\seen_items.json`
- Aggregate run results: `.\data\runs\<timestamp>.json`

The seeded sources are `Digantara Newsroom`, `Skyroot Newsroom`, and `NSIL News`.
Configured X accounts can also be synced into the `sources` table from `.\config\x_targets.json`.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install
```

## Run

Run all enabled sources and return only newly discovered items:

```powershell
python .\run_sources.py
```

Return all currently discoverable items for each enabled source:

```powershell
python .\run_sources.py --all-items
```

## Manage Sources

List sources:

```powershell
python .\manage_sources.py list
```

Add or update a source:

```powershell
python .\manage_sources.py add --name "Digantara Newsroom" --link "https://www.digantara.co.in/newsroom" --scraper-key digantara_newsroom
python .\manage_sources.py add --name "Skyroot Newsroom" --link "https://www.skyroot.in/newsroom" --scraper-key skyroot_newsroom
python .\manage_sources.py add --name "NSIL News" --link "https://www.nsilindia.co.in/news" --scraper-key nsil_news
```

Disable a source:

```powershell
python .\manage_sources.py disable --id 1
```

## X Latest Posts

Add one or more X profile URLs to `.\config\x_targets.json`. Each account is synced as its own source using the `x_latest_posts` scraper key.

Auth is supported through one of:

- `storage_state_path`
- `cookies_path`
- `user_data_dir`

Example account entry:

```json
{
  "name": "X OpenAI",
  "url": "https://x.com/OpenAI",
  "enabled": true,
  "max_posts": 5
}
```

Validate the configured X auth before a full run:

```powershell
python .\manage_sources.py validate-x-auth --url https://x.com/OpenAI
```

Export an isolated X-only `storage_state` file:

```powershell
python .\scripts\export_x_storage_state.py
```

That helper opens a separate Chrome-backed browser profile just for manual X login, then saves a Playwright `storage_state` JSON that the scraper can reuse without pointing at your normal Chrome user folder.

Regenerate isolated X user data on Windows:

1. Launch Chrome with a dedicated profile folder that is used only for X:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="D:\ai\browser-profiles\x-only" --profile-directory="Default" "https://x.com"
```

If Chrome is installed under `Program Files (x86)`, use:

```powershell
& "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" --user-data-dir="D:\ai\browser-profiles\x-only" --profile-directory="Default" "https://x.com"
```

2. Log into X manually in that browser window.
3. Confirm you can open `https://x.com/home`.
4. Close Chrome completely so the profile is not locked.
5. Export a fresh `storage_state` from that isolated profile:

```powershell
python .\scripts\export_x_storage_state.py --profile-dir D:\ai\browser-profiles\x-only --output .\config\x_storage_state.json
```

6. Set `config/x_targets.json` to use that exported file:

```json
"auth": {
  "storage_state_path": "config/x_storage_state.json",
  "cookies_path": "",
  "user_data_dir": ""
}
```

7. Validate the auth before running the scraper:

```powershell
python .\manage_sources.py validate-x-auth --url https://x.com/OpenAI
```

## Notes

- The runner executes every enabled source from the `sources` table.
- Each source gets its own log file inside the current run's `.logs` folder.
- Results are namespaced by the source folder name.
- The Digantara scraper enriches press release dates from detail pages because the listing exposes only slug/title for those items.
- The Skyroot scraper currently reads only the first listing page, by design.
- The NSIL scraper currently reads only the first listing page, by design.
- The X scraper reads only the latest visible posts from the profile timeline, by design.
- For future new sources, the preferred folder naming rule is `<id>_<ShortSourceNameCamelCase>`.
