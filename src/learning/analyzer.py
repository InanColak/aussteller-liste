from __future__ import annotations

import json
import logging
import re

from openai import AsyncOpenAI
from playwright.async_api import async_playwright

from src.config import OPENAI_API_KEY, OPENAI_MODEL
from src.discovery.page_fetcher import _safe_goto
from src.learning.models import SiteProfile

logger = logging.getLogger("aussteller-api")

SYSTEM_PROMPT = """\
You are an expert at analyzing trade fair / exhibition websites to create scraping profiles.

Given information about a website (its HTML structure, network requests, and page content),
create a JSON site profile that describes how to scrape the exhibitor list.

The profile must follow this exact structure:
{
  "profile_version": 1,
  "platform_id": "<short_snake_case_id>",
  "domain_patterns": ["<glob patterns for matching domains>"],
  "source_type": "api" or "html",
  "requires_javascript": true/false,
  "headers": {"<key>": "<value>"},

  "auth": {  // required if API calls use auth headers like apikey, authorization, etc.
    "method": "browser_intercept",  // capture auth by visiting a page
    "page_url": "<URL of the exhibitor search page that triggers the API call>",
    "intercept_pattern": "<substring to match in API request URL, e.g. 'exhibitor/search'>",
    "header_name": "<name of the auth header, e.g. 'apikey'>"
  },
  // Set auth to null if no auth headers are needed

  "listing": {
    "strategy": "single_page" | "paged" | "alpha_index" | "api_endpoint",
    "url_template": "<base API URL without pagination params>",
    "query_params": {"<key>": "<value>"},  // static query parameters
    "meta_url": null,
    "meta_letters_path": null,
    "item_container_selector": null,
    "item_id_path": "<JSON path for exhibitor ID>",
    "item_filter": null,
    "pagination": {
      "type": "page_number" | "offset",
      "start": 1,
      "page_size": 50,
      "max_pages": 200,
      "param_name": "<query param for page number, e.g. 'pageNumber'>",
      "page_size_param": "<query param for page size, e.g. 'pageSize'>",
      "total_path": "<JSON path to total count, e.g. 'result.metaData.hitsTotal'>",
      "items_path": "<JSON path to items array, e.g. 'result.hits'>",
      "stop_when_empty": true
    }
  },

  "detail": null,  // set if detail page needed per exhibitor

  "field_map": {
    "<field_name>": {
      "source": "listing",
      "json_path": "<dot.notation.path from each item in items_path>",
      "css": null,
      "attribute": null,
      "regex": null,
      "transform": null,
      "is_array": false
    }
  },

  "confidence": 0.0-1.0,
  "notes": "<any quirks>"
}

IMPORTANT RULES:
- For API-based sites, url_template should be the base API URL WITHOUT pagination query params
- Put ALL query parameters (including static ones like language, orderBy) in listing.query_params
- Pagination params (pageNumber, pageSize) are handled automatically — do NOT put them in query_params
- For field_map json_path: use paths relative to each item in the items array (from pagination.items_path)
  Example: if items_path is "result.hits" and each hit has {"exhibitor": {"name": "Foo"}},
  then json_path for company_name should be "exhibitor.name" (relative to each hit)
- If API calls include auth headers (apikey, authorization, x-api-key, etc.), you MUST set auth config
- Field names must be from: company_name, website, email, phone, address, country, city, hall, stand, categories, description

Return ONLY valid JSON, nothing else.
"""


async def analyze_site(url: str) -> SiteProfile | None:
    """Analyze a trade fair website and generate a scraping profile."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY required for site analysis.")

    # Step 1: Visit the site, capture structure and network requests
    site_info = await _collect_site_info(url)

    # Step 2: Ask GPT to create a profile
    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": site_info},
        ],
        temperature=0,
        max_tokens=4000,
    )

    content = response.choices[0].message.content or "{}"
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        data = json.loads(content)
        profile = SiteProfile(**data)
        logger.info("Site profile generated: %s (confidence: %.0f%%)", profile.platform_id, profile.confidence * 100)
        return profile
    except Exception as e:
        logger.error("Failed to parse site profile: %s\nResponse: %s", e, content[:500])
        return None


# Headers that indicate auth tokens
_AUTH_HEADER_NAMES = {"apikey", "authorization", "x-api-key", "x-apikey", "api-key", "token"}
# URL patterns to ignore (analytics, tracking, etc.)
_IGNORE_PATTERNS = {"analytics", "tracking", "consent", "usercentrics", "moin.ai", "fraud0", "login"}


async def _collect_site_info(url: str) -> str:
    """Visit a site with Playwright and collect structural information."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )

        # Capture API calls with both request headers and response bodies
        api_calls: list[dict] = []

        async def on_response(response):
            resp_url = response.url
            if any(pat in resp_url.lower() for pat in _IGNORE_PATTERNS):
                return
            if response.status == 200:
                ct = response.headers.get("content-type", "")
                if "json" in ct:
                    try:
                        body = await response.text()
                        if len(body) > 50 and len(body) < 100000:
                            # Capture request headers for auth detection
                            req_headers = dict(response.request.headers)
                            auth_headers = {
                                k: v for k, v in req_headers.items()
                                if k.lower() in _AUTH_HEADER_NAMES
                            }

                            api_calls.append({
                                "method": response.request.method,
                                "url": resp_url,
                                "auth_headers": auth_headers,
                                "body_preview": body[:3000],
                            })
                    except Exception:
                        pass

        page.on("response", on_response)

        await _safe_goto(page, url)
        await page.wait_for_timeout(8000)

        # Collect page info
        page_text = await page.inner_text("body")
        page_text_lines = [l.strip() for l in page_text.split("\n") if l.strip()]
        page_text_preview = "\n".join(page_text_lines[:100])

        # Collect links
        links = await page.evaluate("""
            () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                href: a.href,
                text: a.innerText.trim().substring(0, 80)
            })).filter(l => l.text.length > 0).slice(0, 100)
        """)

        # Collect HTML structure hints
        structure = await page.evaluate("""
            () => {
                const hints = [];
                const tables = document.querySelectorAll('table');
                hints.push('Tables: ' + tables.length);
                const lists = document.querySelectorAll('ul, ol');
                hints.push('Lists: ' + lists.length);
                const cards = document.querySelectorAll('[class*="card"], [class*="item"], [class*="entry"]');
                hints.push('Card-like elements: ' + cards.length);
                if (cards.length > 0) {
                    hints.push('First card classes: ' + cards[0].className);
                    hints.push('First card HTML: ' + cards[0].outerHTML.substring(0, 500));
                }
                return hints.join('\\n');
            }
        """)

        await browser.close()

    # Format for GPT
    parts = [
        f"URL: {url}",
        f"\n=== PAGE TEXT (first 100 lines) ===\n{page_text_preview}",
        f"\n=== HTML STRUCTURE ===\n{structure}",
        f"\n=== LINKS ({len(links)}) ===",
    ]
    for link in links[:50]:
        parts.append(f"  {link['text']}: {link['href']}")

    if api_calls:
        parts.append(f"\n=== API CALLS ({len(api_calls)}) ===")
        for call in api_calls[:10]:
            parts.append(f"\n{call['method']} {call['url']}")
            if call["auth_headers"]:
                parts.append(f"Auth headers: {json.dumps(call['auth_headers'])}")
            parts.append(f"Response preview: {call['body_preview']}")

    return "\n".join(parts)
