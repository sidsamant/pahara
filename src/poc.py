import asyncio
import json
from crawl4ai import AsyncWebCrawler, CacheMode, ContentTypeFilter, CrawlerRunConfig, DefaultMarkdownGenerator, DomainFilter, FilterChain, JsonCssExtractionStrategy, URLPatternFilter
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy, ContentRelevanceFilter
from crawl4ai.content_scraping_strategy import LXMLWebScrapingStrategy

# Only follow URLs containing "blog" or "docs"

async def main():
    url_filter = URLPatternFilter(patterns=["*press-release*"])

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
        ContentTypeFilter(allowed_types=["text/html"])
        # relevance_filter

    ])
    
    schema = {
        "name": "Example Items",
        "baseSelector": "div.elementor-page-title",
        "fields": [
            {"name": "title", "selector": "h1", "type": "text"},
            # {"name": "link", "selector": "a", "type": "attribute", "attribute": "href"}
        ]
    }


    # Configure a 2-level deep crawl
    config = CrawlerRunConfig(
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=1, 
            include_external=False,
            filter_chain=filter_chain
        ),
        max_range=1,
        scraping_strategy=LXMLWebScrapingStrategy(),
        verbose=True,
        
        markdown_generator=DefaultMarkdownGenerator(),

        cache_mode=CacheMode.BYPASS,
        extraction_strategy=JsonCssExtractionStrategy(schema)
    )

    async with AsyncWebCrawler() as crawler:
        results = await crawler.arun("https://www.satsure.co/newsroom/", config=config)

        print(f"Crawled {len(results)} pages in total")

        # Access individual results
        for result in results:  # Show first 3 results
            print(f"URL: {result.url}")
            print(f"Depth: {result.metadata.get('depth', 0)}")
            # print(result.fit_html)
            print(json.loads(result.extracted_content))
            print(f"********************************************")

if __name__ == "__main__":
    asyncio.run(main())