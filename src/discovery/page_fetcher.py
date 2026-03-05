from __future__ import annotations

from playwright.async_api import async_playwright


async def fetch_page_html(url: str, wait_seconds: int = 3) -> str:
    """Load a page with Playwright and return its rendered HTML."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        await page.goto(url, wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(wait_seconds * 1000)
        html = await page.content()
        await browser.close()
    return html


async def fetch_page_links(url: str) -> list[dict[str, str]]:
    """Load a page and extract all links with their text."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        await page.goto(url, wait_until="networkidle", timeout=30_000)
        await page.wait_for_timeout(2000)

        links = await page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                href: a.href,
                text: a.innerText.trim().substring(0, 200)
            })).filter(l => l.text.length > 0)
        """)
        await browser.close()
    return links
