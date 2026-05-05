from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlparse

from src.discovery.data_extractor import extract_exhibitors
from src.discovery.link_finder import find_exhibitor_links
from src.discovery.page_fetcher import fetch_page_links
from src.learning.analyzer import analyze_site
from src.learning.replay import replay_scrape
from src.learning.store import find_profile, save_profile, update_last_used
from src.models import Exhibitor, ScrapeResult
from src.platforms.base import BaseScraper, ProgressCallback
from src.platforms.registry import detect_platform

logger = logging.getLogger("aussteller-orchestrator")


async def scrape_url(url: str, limit: int = 0, progress_callback: ProgressCallback = None) -> ScrapeResult:
    """Main entry point: detect platform and scrape exhibitors."""

    # 1. Try known platform scrapers (hand-coded)
    scraper = detect_platform(url)
    if scraper:
        logger.info("Detected platform: %s", scraper.name)
        return await scraper.scrape(url, limit=limit, progress_callback=progress_callback)

    # 2. Try learned site profiles
    profile = find_profile(url)
    if profile and profile.confidence >= 0.5:
        logger.info("Using learned profile: %s (confidence: %.0f%%)", profile.platform_id, profile.confidence * 100)
        try:
            result = await replay_scrape(profile, url, limit=limit, progress_callback=progress_callback)
            if result.total_exhibitors > 0:
                update_last_used(profile)
                return result
            logger.info("Learned profile returned 0 results. Falling back to AI discovery...")
        except Exception as e:
            logger.warning("Learned profile failed: %s. Falling back to AI discovery...", e)

    # 3. Check if site uses a known platform by analyzing its links
    logger.info("Analyzing site links...")
    links = await fetch_page_links(url)
    detected_scraper = _detect_platform_from_links(url, links)
    if detected_scraper:
        logger.info("Detected platform from site structure: %s", detected_scraper.name)
        # Use the detected URL if available (e.g., Ungerboeck portal URL found in links)
        scrape_target = getattr(detected_scraper, "_detected_url", url)
        return await detected_scraper.scrape(scrape_target, limit=limit, progress_callback=progress_callback)

    # 4. Fall back to AI-powered discovery
    logger.info("Unknown platform — using AI-powered discovery...")
    exhibitor_urls = await find_exhibitor_links(url, links)
    if not exhibitor_urls:
        logger.info("Could not find exhibitor list link. Trying to extract from current page...")
        exhibitor_urls = [url]

    result = await _discovery_scrape_from_urls(url, exhibitor_urls, limit=limit, progress_callback=progress_callback)

    # 5. Try to learn the site for next time (only if successful)
    if result.total_exhibitors > 0:
        logger.info("Analyzing site structure for future use...")
        try:
            new_profile = await analyze_site(url)
            if new_profile:
                path = save_profile(new_profile)
                logger.info("Learned profile saved: %s -> %s", new_profile.platform_id, path)
            else:
                logger.info("Could not learn site structure (will use AI discovery next time too).")
        except Exception as e:
            logger.warning("Could not learn site structure: %s", e)

    return result


def _detect_platform_from_links(url: str, links: list[dict[str, str]]) -> BaseScraper | None:
    """Detect known platforms by analyzing page links (e.g., VIS API pattern, Corussoft Navigator, Ungerboeck)."""
    from src.platforms.messe_berlin import MesseBerlinScraper
    from src.platforms.messe_duesseldorf import MesseDuesseldorfScraper
    from src.platforms.ungerboeck import UngerboeckScraper

    for link in links:
        href = link.get("href", "")
        # VIS API pattern: /vis/v1/ or /vis-api/
        if "/vis/v1/" in href or "/vis-api/" in href:
            logger.info("  Found VIS API pattern in links: %s", href)
            return MesseDuesseldorfScraper()
        # Corussoft Navigator pattern: navigate.*.com or event-cloud.com
        if "navigate." in href and ("/company" in href or "/showfloor" in href):
            logger.info("  Found Corussoft Navigator pattern in links: %s", href)
            return MesseBerlinScraper()

    # Ungerboeck pattern: *.ungerboeck.com/*/app85.cshtml
    ungerboeck_url = UngerboeckScraper.detect_from_links(links)
    if ungerboeck_url:
        logger.info("  Found Ungerboeck portal in links: %s", ungerboeck_url)
        scraper = UngerboeckScraper()
        scraper._detected_url = ungerboeck_url
        return scraper

    return None


async def _discovery_scrape_from_urls(
    url: str, exhibitor_urls: list[str], limit: int = 0, progress_callback: ProgressCallback = None
) -> ScrapeResult:
    """AI-powered scraping from pre-identified exhibitor URLs."""
    fair_name = urlparse(url).hostname or "unknown"

    all_exhibitors: list[Exhibitor] = []
    page_num = 0
    for ex_url in exhibitor_urls:
        logger.info("Extracting exhibitors from: %s", ex_url)
        page_url = ex_url
        max_pages = 20

        for _ in range(max_pages):
            page_num += 1
            try:
                exhibitors, next_url = await extract_exhibitors("", page_url)
            except Exception as e:
                logger.error("  Error extracting from %s: %s", page_url, e)
                break
            all_exhibitors.extend(exhibitors)
            logger.info("  Found %d exhibitors (total: %d)", len(exhibitors), len(all_exhibitors))

            if progress_callback:
                await progress_callback(len(all_exhibitors), f"Page {page_num} scanned — {len(all_exhibitors)} exhibitors")

            if limit and len(all_exhibitors) >= limit:
                break
            if not next_url:
                break
            page_url = next_url
            await asyncio.sleep(1)

        if limit and len(all_exhibitors) >= limit:
            break

    if limit:
        all_exhibitors = all_exhibitors[:limit]

    return ScrapeResult(
        fair_name=fair_name,
        fair_url=url,
        exhibitors=all_exhibitors,
    )
