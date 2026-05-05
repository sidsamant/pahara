from src.scrapers.bellatrix_updates import scrape_source as scrape_bellatrix_updates
from src.scrapers.digantara_newsroom import scrape_source as scrape_digantara_newsroom
from src.scrapers.nsil_news import scrape_source as scrape_nsil_news
from src.scrapers.pixxel_newsroom import scrape_source as scrape_pixxel_newsroom
from src.scrapers.satsure_newsroom import scrape_source as scrape_satsure_newsroom
from src.scrapers.skyroot_newsroom import scrape_source as scrape_skyroot_newsroom
from src.scrapers.x_latest_posts import scrape_source as scrape_x_latest_posts
from src.utils.util import ScrapperType

SCRAPER_REGISTRY = {
    ScrapperType.BELLATRIX_UPDATES.value: scrape_bellatrix_updates,
    ScrapperType.DIGANTARA_NEWSROOM.value: scrape_digantara_newsroom,
    ScrapperType.NSIL_NEWS.value: scrape_nsil_news,
    ScrapperType.PIXXEL_NEWSROOM.value: scrape_pixxel_newsroom,
    ScrapperType.SATSURE_NEWSROOM.value: scrape_satsure_newsroom,
    ScrapperType.SKYROOT_NEWSROOM.value: scrape_skyroot_newsroom,
    ScrapperType.X_LATEST_POSTS.value: scrape_x_latest_posts,
}
