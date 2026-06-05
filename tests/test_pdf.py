import pytest
# Import using the subfolder path
from src.scrapers.nsil_news import NSILScraper

LOCALFILENAME ="./downloaded_file.pdf";

@pytest.fixture
def scraper():
    return NSILScraper()

def test_extract_english(scraper):
    result = scraper._convert_bilingual_pdf_with_images(pdf_path=LOCALFILENAME)
    assert result