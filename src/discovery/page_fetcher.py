from __future__ import annotations

from playwright.async_api import Page, async_playwright

# Common cookie banner selectors and button texts
_COOKIE_DISMISS_SELECTORS = [
    "text=Alle akzeptieren",
    "text=Accept all",
    "text=Accept All",
    "text=Alles akzeptieren",
    "text=Alle Cookies akzeptieren",
    "text=Accept all cookies",
    "text=Akzeptieren",
    "text=Accept",
    "text=Zustimmen",
    "text=Agree",
    "text=OK",
    "text=Weiter ohne Einwilligung",
    "text=Continue without accepting",
    "button[id*='accept']",
    "button[id*='consent']",
    "button[class*='accept']",
    "button[class*='consent']",
    "[data-testid*='accept']",
    "[data-testid*='consent']",
]


async def _dismiss_cookie_banner(page: Page) -> None:
    """Try to dismiss cookie consent banners."""
    for selector in _COOKIE_DISMISS_SELECTORS:
        try:
            btn = await page.query_selector(selector)
            if btn and await btn.is_visible():
                await btn.click()
                await page.wait_for_timeout(1000)
                return
        except Exception:
            continue


async def _safe_goto(page: Page, url: str, timeout: int = 60_000) -> None:
    """Navigate to URL, falling back from networkidle to load if the site is slow."""
    try:
        await page.goto(url, wait_until="networkidle", timeout=timeout)
    except Exception:
        await page.goto(url, wait_until="load", timeout=timeout)


async def fetch_page_html(url: str, wait_seconds: int = 3) -> str:
    """Load a page with Playwright and return its rendered HTML."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        await _safe_goto(page, url)
        await page.wait_for_timeout(2000)
        await _dismiss_cookie_banner(page)
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
        await _safe_goto(page, url)
        await page.wait_for_timeout(2000)
        await _dismiss_cookie_banner(page)
        await page.wait_for_timeout(1000)

        links = await page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                href: a.href,
                text: a.innerText.trim().substring(0, 200)
            })).filter(l => l.text.length > 0)
        """)
        await browser.close()
    return links
