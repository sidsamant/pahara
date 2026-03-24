from scrapers.digantara_newsroom import scrape_source as scrape_digantara_newsroom
from scrapers.skyroot_newsroom import scrape_source as scrape_skyroot_newsroom


SCRAPER_REGISTRY = {
    "digantara_newsroom": scrape_digantara_newsroom,
    "skyroot_newsroom": scrape_skyroot_newsroom,
}
