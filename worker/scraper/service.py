from dataclasses import asdict
from typing import Callable, Optional

from worker.scraper.discovery import DiscoveryEngine
from worker.scraper.extraction import ContentExtractor
from worker.scraper.models import WebsiteReport


def process_website(
    target_url: str,
    max_articles_to_extract: Optional[int] = None,
    progress_callback: Optional[Callable[[int], None]] = None,
) -> WebsiteReport:

    engine = DiscoveryEngine()
    extractor = ContentExtractor()

    # ============================================================
    # Progress tracking
    # ============================================================

    last_reported_progress = 0

    def report_progress(progress: int) -> None:
        """
        Reports progress only when the percentage changes.
        This prevents duplicate DynamoDB updates.
        """
        nonlocal last_reported_progress

        if (
            progress_callback
            and progress != last_reported_progress
        ):
            progress_callback(progress)
            last_reported_progress = progress

    # ============================================================
    # Normalize URL
    # ============================================================

    normalized_url = engine.normalize_url(
        target_url
    )

    print(
        f"\n--- Processing: {normalized_url} ---"
    )

    # ============================================================
    # 1. Sitemap discovery
    # ============================================================

    sitemaps = engine.discover_sitemaps(
        normalized_url
    )

    print(
        f"Discovered {len(sitemaps)} sitemap(s): "
        f"{sitemaps}"
    )

    raw_urls = []

    for sitemap in sitemaps:
        raw_urls.extend(
            engine.parse_sitemap_urls(
                sitemap
            )
        )

    print(
        f"Discovered {len(raw_urls)} total raw "
        f"URLs from sitemaps."
    )

    # 10%
    report_progress(10)

    # ============================================================
    # 2. Hub page fallback discovery
    # ============================================================

    print(
        "Checking common blog hub pages "
        "for direct links..."
    )

    hub_pages = engine.discover_hub_pages(
        normalized_url
    )

    for hub in hub_pages:
        hub_links = engine.crawl_hub_for_links(
            hub,
            normalized_url,
        )

        print(
            f"  Harvested {len(hub_links)} link(s) "
            f"from hub page: {hub}"
        )

        raw_urls.extend(hub_links)

    # 20%
    report_progress(20)

    # ============================================================
    # 3. Filter candidate article URLs
    # ============================================================

    accepted_urls = engine.filter_candidate_urls(
        raw_urls
    )

    print(
        f"Accepted {len(accepted_urls)} likely "
        f"article URLs based on deterministic rules."
    )

    report = WebsiteReport(
        website_url=normalized_url,
        discovered_sitemaps=sitemaps,
        total_candidate_urls=len(raw_urls),
        accepted_article_urls=len(accepted_urls),
    )

    # 30%
    report_progress(30)

    # ============================================================
    # 4. Extract article content
    # ============================================================

    if max_articles_to_extract is None:
        target_urls = accepted_urls
    else:
        target_urls = accepted_urls[
            :max_articles_to_extract
        ]

    total_articles = len(target_urls)

    print(
        f"Extracting full content for "
        f"{total_articles} article(s)..."
    )

    # If there are no articles to extract,
    # extraction is immediately complete.
    if total_articles == 0:
        report_progress(90)

    for index, url in enumerate(
        target_urls,
        start=1,
    ):
        print(
            f"  Extracting ({index}/{total_articles}): "
            f"{url}"
        )

        res_dict = extractor.extract_all(
            url
        )

        article_summary = {
            "url": url
        }

        if (
            "trafilatura" in res_dict
            and res_dict["trafilatura"].content_length > 0
        ):
            report.parsed_trafilatura_count += 1

            article_summary["trafilatura"] = (
                asdict(
                    res_dict["trafilatura"]
                )
            )

        elif "error" in res_dict:
            article_summary["error"] = (
                res_dict["error"]
            )

        report.article_results.append(
            article_summary
        )

        # --------------------------------------------------------
        # Extraction progress
        #
        # 30% = extraction hasn't started yet
        # 90% = extraction is completely finished
        #
        # Therefore extraction gets the remaining 60%.
        # --------------------------------------------------------

        extraction_progress = (
            30
            + int(
                (index / total_articles) * 60
            )
        )

        # Round down to the nearest 10%.
        progress = (
            extraction_progress // 10
        ) * 10

        # Never exceed 90%.
        progress = min(
            progress,
            90,
        )

        report_progress(progress)

    return report