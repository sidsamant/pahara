from scrapers.digantara_newsroom import scrape_source as scrape_digantara_newsroom
from scrapers.nsil_news import scrape_source as scrape_nsil_news
from scrapers.skyroot_newsroom import scrape_source as scrape_skyroot_newsroom


SCRAPER_REGISTRY = {
    "digantara_newsroom": scrape_digantara_newsroom,
    "nsil_news": scrape_nsil_news,
    "skyroot_newsroom": scrape_skyroot_newsroom,
}
