from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import typer

from src.discovery.data_extractor import extract_exhibitors
from src.discovery.link_finder import find_exhibitor_links
from src.discovery.page_fetcher import fetch_page_links
from src.learning.analyzer import analyze_site
from src.learning.replay import replay_scrape
from src.learning.store import find_profile, save_profile, update_last_used
from src.models import Exhibitor, ScrapeResult
from src.platforms.registry import detect_platform


async def scrape_url(url: str, limit: int = 0) -> ScrapeResult:
    """Main entry point: detect platform and scrape exhibitors."""

    # 1. Try known platform scrapers (hand-coded)
    scraper = detect_platform(url)
    if scraper:
        typer.echo(f"Detected platform: {scraper.name}")
        return await scraper.scrape(url, limit=limit)

    # 2. Try learned site profiles
    profile = find_profile(url)
    if profile and profile.confidence >= 0.5:
        typer.echo(f"Using learned profile: {profile.platform_id} (confidence: {profile.confidence:.0%})")
        try:
            result = await replay_scrape(profile, url, limit=limit)
            if result.total_exhibitors > 0:
                update_last_used(profile)
                return result
            typer.echo("Learned profile returned 0 results. Falling back to AI discovery...")
        except Exception as e:
            typer.echo(f"Learned profile failed: {e}. Falling back to AI discovery...")

    # 3. Check if site uses a known platform by analyzing its links
    typer.echo("Analyzing site links...")
    links = await fetch_page_links(url)
    detected_scraper = _detect_platform_from_links(url, links)
    if detected_scraper:
        typer.echo(f"Detected platform from site structure: {detected_scraper.name}")
        # Use the detected URL if available (e.g., Ungerboeck portal URL found in links)
        scrape_target = getattr(detected_scraper, "_detected_url", url)
        return await detected_scraper.scrape(scrape_target, limit=limit)

    # 4. Fall back to AI-powered discovery
    typer.echo("Unknown platform — using AI-powered discovery...")
    exhibitor_urls = await find_exhibitor_links(url, links)
    if not exhibitor_urls:
        typer.echo("Could not find exhibitor list link. Trying to extract from current page...")
        exhibitor_urls = [url]

    result = await _discovery_scrape_from_urls(url, exhibitor_urls, limit=limit)

    # 5. Try to learn the site for next time (only if successful)
    if result.total_exhibitors > 0:
        typer.echo("\nAnalyzing site structure for future use...")
        try:
            new_profile = await analyze_site(url)
            if new_profile:
                path = save_profile(new_profile)
                typer.echo(f"Learned profile saved: {new_profile.platform_id} -> {path}")
            else:
                typer.echo("Could not learn site structure (will use AI discovery next time too).")
        except Exception as e:
            typer.echo(f"Could not learn site structure: {e}")

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
            typer.echo(f"  Found VIS API pattern in links: {href}")
            return MesseDuesseldorfScraper()
        # Corussoft Navigator pattern: navigate.*.com or event-cloud.com
        if "navigate." in href and ("/company" in href or "/showfloor" in href):
            typer.echo(f"  Found Corussoft Navigator pattern in links: {href}")
            return MesseBerlinScraper()

    # Ungerboeck pattern: *.ungerboeck.com/*/app85.cshtml
    ungerboeck_url = UngerboeckScraper.detect_from_links(links)
    if ungerboeck_url:
        typer.echo(f"  Found Ungerboeck portal in links: {ungerboeck_url}")
        scraper = UngerboeckScraper()
        scraper._detected_url = ungerboeck_url
        return scraper

    return None


async def _discovery_scrape_from_urls(
    url: str, exhibitor_urls: list[str], limit: int = 0
) -> ScrapeResult:
    """AI-powered scraping from pre-identified exhibitor URLs."""
    fair_name = urlparse(url).hostname or "unknown"

    all_exhibitors: list[Exhibitor] = []
    for ex_url in exhibitor_urls:
        typer.echo(f"Extracting exhibitors from: {ex_url}")
        page_url = ex_url
        max_pages = 20

        for _ in range(max_pages):
            try:
                exhibitors, next_url = await extract_exhibitors("", page_url)
            except Exception as e:
                typer.echo(f"  Error extracting from {page_url}: {e}")
                break
            all_exhibitors.extend(exhibitors)
            typer.echo(f"  Found {len(exhibitors)} exhibitors (total: {len(all_exhibitors)})")

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
