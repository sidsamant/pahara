from src.scrapers.bellatrix_updates import scrape_source as scrape_bellatrix_updates
from src.scrapers.digantara_newsroom import scrape_source as scrape_digantara_newsroom
from src.scrapers.nsil_news import scrape_source as scrape_nsil_news
from src.scrapers.pixxel_newsroom import scrape_source as scrape_pixxel_newsroom
from src.scrapers.skyroot_newsroom import scrape_source as scrape_skyroot_newsroom
from src.scrapers.x_latest_posts import scrape_source as scrape_x_latest_posts


SCRAPER_REGISTRY = {
    "bellatrix_updates": scrape_bellatrix_updates,
    "digantara_newsroom": scrape_digantara_newsroom,
    "nsil_news": scrape_nsil_news,
    "pixxel_newsroom": scrape_pixxel_newsroom,
    "skyroot_newsroom": scrape_skyroot_newsroom,
    "x_latest_posts": scrape_x_latest_posts,
}
