# Introduction

This document specifies the information about scrapers that scrape for publicly accessbile webpages.
This is an extension of [COMMON_SCRAPER_RULES.md](./COMMON_SCRAPER_RULES.md). It overrides the rules, information, context, instructions, etc. in the parent document.

## Scope
- These web scrappers can access Publicly accessible webpages.
- Each web scrapper is associated with a source row in `sources` table.
- The scrappers can be unique from each other or may have common code. Their logic is specific to the source.
- Each scraper must work within the existing multi-source runner and SQLite tracking model.

## Examples
In the sources table,  `Digantara Newsroom`, `Skyroot Newsroom`, and `NSIL News` are web scrapper sources.

## Low level design

- Each scrapper is a Python source file in the folder src\scrapers and added to `SCRAPER_REGISTRY` in `scrapers/__init__.py`. However `x_latest_posts.py` is not web scrapper.
- In the sources table, each row the link field value not starting with the string `https://x.com`.
- Each source has an independent scrapper.


