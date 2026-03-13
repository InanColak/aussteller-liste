from __future__ import annotations

import json

from openai import AsyncOpenAI
from playwright.async_api import async_playwright

from src.config import OPENAI_API_KEY, OPENAI_MODEL
from src.discovery.page_fetcher import _dismiss_cookie_banner
from src.models import Exhibitor

SYSTEM_PROMPT = """\
You are an expert at extracting exhibitor data from trade fair web pages.
Given the visible text content of a page, extract all exhibitor/company entries you can find.
Return a JSON array of objects with these fields (use null if not found):
- company_name (required)
- website
- hall
- stand
- country
- city
- categories (array of strings)
- description
- phone
- email
- address

Also check if there is a "next page" or pagination link. If yes, include it as
a separate field "next_page_url" in a wrapper object.

Return format:
{
  "exhibitors": [...],
  "next_page_url": "https://..." or null
}
Only return valid JSON, nothing else.
"""

MAX_TEXT_CHARS = 30_000


async def _fetch_page_text(url: str) -> tuple[str, list[dict]]:
    """Load page with Playwright, dismiss cookie banner, return visible text and links."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        try:
            await page.goto(url, wait_until="networkidle", timeout=60_000)
        except Exception:
            # Fallback: try without waiting for networkidle
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        await page.wait_for_timeout(2000)
        await _dismiss_cookie_banner(page)
        await page.wait_for_timeout(2000)

        # Scroll to load lazy content
        for _ in range(5):
            await page.evaluate("window.scrollBy(0, 2000)")
            await page.wait_for_timeout(500)

        # Get visible text (no HTML noise)
        text = await page.inner_text("body")

        # Get links for pagination detection
        links = await page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                href: a.href,
                text: a.innerText.trim().substring(0, 100)
            })).filter(l => l.text.length > 0 && l.text.length < 100)
        """)

        await browser.close()

    # Clean up text
    text = text.replace("\u00AD", "")  # Remove soft hyphens
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    clean_text = "\n".join(lines)

    if len(clean_text) > MAX_TEXT_CHARS:
        clean_text = clean_text[:MAX_TEXT_CHARS]

    return clean_text, links


async def extract_exhibitors(
    html: str, page_url: str
) -> tuple[list[Exhibitor], str | None]:
    """Extract exhibitor data from a trade fair page.

    Uses Playwright to get visible text, then GPT to extract structured data.

    Returns:
        Tuple of (exhibitors, next_page_url or None)
    """
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is required for AI-powered discovery. "
            "Set it in your .env file."
        )

    # Fetch visible text instead of raw HTML
    page_text, page_links = await _fetch_page_text(page_url)

    # Add pagination links as context
    pagination_hint = ""
    for link in page_links:
        text_lower = link["text"].lower()
        if any(kw in text_lower for kw in ["next", "nächste", "weiter", ">>", "›"]):
            pagination_hint += f"\nPossible next page: {link['text']} -> {link['href']}"

    user_content = f"Page URL: {page_url}\n\nVisible page content:\n{page_text}"
    if pagination_hint:
        user_content += f"\n\nPagination links found:{pagination_hint}"

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        temperature=0,
        max_tokens=16000,
    )

    content = response.choices[0].message.content or "{}"
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return [], None

    exhibitors = []
    raw_list = data.get("exhibitors", [])
    for item in raw_list:
        if not item.get("company_name"):
            continue
        item.setdefault("categories", [])
        try:
            exhibitors.append(Exhibitor(**item))
        except Exception:
            continue

    next_url = data.get("next_page_url")
    return exhibitors, next_url
