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

## Notes

- The runner executes every enabled source from the `sources` table.
- Each source gets its own log file inside the current run's `.logs` folder.
- Results are namespaced by the source folder name.
- The Digantara scraper enriches press release dates from detail pages because the listing exposes only slug/title for those items.
- The Skyroot scraper currently reads only the first listing page, by design.
- The NSIL scraper currently reads only the first listing page, by design.
- For future new sources, the preferred folder naming rule is `<id>_<ShortSourceNameCamelCase>`.
