from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import typer

from src.discovery.data_extractor import extract_exhibitors
from src.discovery.link_finder import find_exhibitor_links
from src.discovery.page_fetcher import fetch_page_html, fetch_page_links
from src.models import Exhibitor, ScrapeResult
from src.platforms.registry import detect_platform


async def scrape_url(url: str, limit: int = 0) -> ScrapeResult:
    """Main entry point: detect platform and scrape exhibitors."""

    # 1. Try known platform scrapers
    scraper = detect_platform(url)
    if scraper:
        typer.echo(f"Detected platform: {scraper.name}")
        return await scraper.scrape(url, limit=limit)

    # 2. Fall back to AI-powered discovery
    typer.echo("Unknown platform — using AI-powered discovery...")
    return await _discovery_scrape(url, limit=limit)


async def _discovery_scrape(url: str, limit: int = 0) -> ScrapeResult:
    """AI-powered scraping for unknown platforms."""
    fair_name = urlparse(url).hostname or "unknown"

    # Step 1: Find exhibitor list link
    typer.echo("Fetching page links...")
    links = await fetch_page_links(url)
    typer.echo(f"Found {len(links)} links, asking AI to identify exhibitor list...")

    exhibitor_urls = await find_exhibitor_links(url, links)
    if not exhibitor_urls:
        typer.echo("Could not find exhibitor list link. Trying to extract from current page...")
        exhibitor_urls = [url]

    # Step 2: Extract exhibitors from each page
    all_exhibitors: list[Exhibitor] = []
    for ex_url in exhibitor_urls:
        typer.echo(f"Extracting exhibitors from: {ex_url}")
        page_url = ex_url
        max_pages = 20

        for _ in range(max_pages):
            html = await fetch_page_html(page_url)
            exhibitors, next_url = await extract_exhibitors(html, page_url)
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
