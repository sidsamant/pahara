# Multi-Source Newsroom Runner

This project uses Python, `crawl4ai` plus SQLite to run all enabled newsroom sources, track each run, and write namespaced results into a dedicated folder per source.

## Objectives
1. Scrape content from various internet based sources like webpages, X.com pages, etc.
2. Ability to download content and save locally. These would contain text, images and metadata about both.
3. Avoid scraping duplicate target source paths.
4. Scalable to include new source types
5. Data provenance should exist to trace every run to its output and vice a versa
6. Ability to run for scraping all targets or single target.
7. Ability to Log important milestones while scraping, statuses, stats like counts and errors and failures in files.

## Directory Layout

- SQLite database: `.\data\crawler.sqlite3`
- Per-run logs: `.\.logs\<timestamp>\`
- Outputs: `.\.outputs\`
- Source code exists in `.\src` folder
- Configuration for bootstrapping, execution exists in `.\config` folder
- The root folder has the requirements.txt for setting up a Python VENV for execution environment.
- The root folder is managed by Git.
- Various runtime and transient folders are created with names starting with `.` and should be not used as Context for AI unless explicitly asked to do so.

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
python -m src.run_sources
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
## Instructions for AI 
1. The documentation files may have outdated or incorrect or duplicate information. DO correct it when found.
2. Document files present hierarchilal information. README.md file is the root which points to other documentation. A rule or instruction or information in lower levels of documentation should override conflicting rules in the higher levels and the conflict should be highlighted or corrected by AI.

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
