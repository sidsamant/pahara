# Multi-Source Newsroom Runner

This project uses `crawl4ai` plus SQLite to run all enabled newsroom sources, track each run, and write namespaced results into a dedicated folder per source.

## Data Model

- `sources`: unique source id, unique name, unique link, scraper key, enabled flag, folder name
- `runs`: one row per execution of the runner
- `run_sources`: one row per source inside a run, including status, output path, counts, and errors

## Runtime Layout

- SQLite database: `.\data\crawler.sqlite3`
- Per-run logs: `.\.logs\<timestamp>\`
- Per-source folders: `.\sources\source_link<id>_<CamelCaseName>\`
- Per-source results: `.\sources\source_link<id>_<CamelCaseName>\results\<timestamp>.json`
- Per-source state: `.\sources\source_link<id>_<CamelCaseName>\state\seen_items.json`
- Aggregate run results: `.\data\runs\<timestamp>.json`

The seeded sources are `Digantara Newsroom` and `Skyroot Newsroom`.

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
