import asyncio
from crawl4ai import AsyncWebCrawler, ContentTypeFilter, CrawlerRunConfig, DefaultMarkdownGenerator, DomainFilter, FilterChain, URLPatternFilter
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy, ContentRelevanceFilter
from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy

# Only follow URLs containing "blog" or "docs"

async def main():
    url_filter = URLPatternFilter(patterns=["*updates*", "*docs*"])

    # Create a content relevance filter
    relevance_filter = ContentRelevanceFilter(
        query="engine or thruster or propulsion",
        threshold=0.4  # Minimum similarity score (0.0 to 1.0)
    )
    
    # Create a chain of filters
    filter_chain = FilterChain([
        # Only follow URLs with specific patterns
        url_filter,

        # # Only crawl specific domains
        # DomainFilter(
        #     allowed_domains=["docs.example.com"],
        #     blocked_domains=["old.docs.example.com"]
        # ),

        # Only include specific content types
        ContentTypeFilter(allowed_types=["text/html"]),
        relevance_filter

    ])

    # Configure a 2-level deep crawl
    config = CrawlerRunConfig(
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=1, 
            include_external=False,
            filter_chain=filter_chain
        ),
        scraping_strategy=LXMLWebScrapingStrategy(),
        verbose=True,
        
        markdown_generator=DefaultMarkdownGenerator()
    )
    

    async with AsyncWebCrawler() as crawler:
        results = await crawler.arun("https://bellatrix.aero/updates", config=config)

        print(f"Crawled {len(results)} pages in total")

        # Access individual results
        for result in results:  # Show first 3 results
            print(f"URL: {result.url}")
            print(f"Depth: {result.metadata.get('depth', 0)}")

if __name__ == "__main__":
    asyncio.run(main())