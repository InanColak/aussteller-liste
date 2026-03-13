from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

import httpx
from playwright.async_api import async_playwright
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import MAX_RETRIES, REQUEST_DELAY
from src.models import Exhibitor, ScrapeResult
from src.platforms.base import BaseScraper

DOMAIN_PATTERN = re.compile(
    r"messefrankfurt\.com",
    re.IGNORECASE,
)

# Maps subdomain prefixes to event variables used in the API
EVENT_MAP: dict[str, str] = {
    "light-building": "LIGHTBUILDING",
    "automechanika": "AUTOMECHANIKA",
    "texprocess": "TEXPROCESS",
    "techtextil": "TECHTEXTIL",
    "ambiente": "AMBIENTE",
    "christmasworld": "CHRISTMASWORLD",
    "creativeworld": "CREATIVEWORLD",
    "paperworld": "PAPERWORLD",
    "hypermotion": "HYPERMOTION",
    "ish": "ISH",
    "formnext": "FORMNEXT",
    "musikmesse": "MUSIKMESSE",
    "prolight-sound": "PROLIGHTSOUND",
}

BASE_API = "https://api.messefrankfurt.com/service/esb_api/exhibitor-service/api/2.1/public/exhibitor"
SEARCH_URL = f"{BASE_API}/search"
PAGE_SIZE = 100


class MesseFrankfurtScraper(BaseScraper):
    name = "Messe Frankfurt"
    description = "Messe Frankfurt exhibitor search API (Light + Building, ISH, Ambiente, etc.)"
    url_patterns = ["*.messefrankfurt.com*"]

    @classmethod
    def detect(cls, url: str) -> bool:
        return bool(DOMAIN_PATTERN.search(url))

    async def scrape(self, url: str, limit: int = 0) -> ScrapeResult:
        event_variable = self._resolve_event(url)
        apikey = await self._fetch_apikey(url)

        exhibitors: list[Exhibitor] = []
        page_number = 1

        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                data = await self._fetch_page(client, apikey, event_variable, page_number)
                result = data.get("result", {})
                hits = result.get("hits", [])

                if not hits:
                    break

                for hit in hits:
                    ex = self._parse_exhibitor(hit)
                    if ex:
                        exhibitors.append(ex)

                meta = result.get("metaData", {})
                total = meta.get("hitsTotal", 0)

                if limit and len(exhibitors) >= limit:
                    exhibitors = exhibitors[:limit]
                    break

                if len(exhibitors) >= total:
                    break

                page_number += 1
                await asyncio.sleep(REQUEST_DELAY)

        fair_name = self._extract_fair_name(url, event_variable)

        return ScrapeResult(
            fair_name=fair_name,
            fair_url=url,
            exhibitors=exhibitors,
        )

    def _resolve_event(self, url: str) -> str:
        """Determine the event variable from the URL subdomain."""
        hostname = urlparse(url).hostname or ""
        subdomain = hostname.split(".")[0]

        if subdomain in EVENT_MAP:
            return EVENT_MAP[subdomain]

        # Try partial match
        for prefix, event_var in EVENT_MAP.items():
            if prefix in subdomain:
                return event_var

        return subdomain.upper().replace("-", "")

    async def _fetch_apikey(self, url: str) -> str:
        """Load the exhibitor search page in a browser to capture the API key."""
        # Build the exhibitor search URL
        parsed = urlparse(url)
        search_url = f"{parsed.scheme}://{parsed.hostname}/frankfurt/de/ausstellersuche.html"

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )

            apikey = ""

            async def on_request(request):
                nonlocal apikey
                if "exhibitor/search" in request.url and not apikey:
                    apikey = request.headers.get("apikey", "")

            page.on("request", on_request)

            try:
                await page.goto(search_url, wait_until="load", timeout=60_000)
            except Exception:
                pass
            await page.wait_for_timeout(8000)
            await browser.close()

        if not apikey:
            raise RuntimeError(f"Could not capture API key from {search_url}")

        return apikey

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    async def _fetch_page(
        self, client: httpx.AsyncClient, apikey: str, event_variable: str, page_number: int
    ) -> dict:
        """Fetch one page of exhibitor search results."""
        response = await client.get(
            SEARCH_URL,
            params={
                "language": "de-DE",
                "q": "",
                "orderBy": "name",
                "pageNumber": page_number,
                "pageSize": PAGE_SIZE,
                "orSearchFallback": "false",
                "showJumpLabels": "false",
                "findEventVariable": event_variable,
            },
            headers={
                "apikey": apikey,
                "referer": "https://www.messefrankfurt.com/",
            },
        )
        response.raise_for_status()
        return response.json()

    def _parse_exhibitor(self, hit: dict) -> Exhibitor | None:
        """Parse a single exhibitor from an API hit."""
        ex = hit.get("exhibitor", {})
        if not ex:
            return None

        name = ex.get("name", "").strip()
        if not name:
            return None

        # Address info
        address_data = ex.get("address", {})
        country_data = address_data.get("country", {})

        # Hall and stand
        halls = []
        stands = []
        exhibition = ex.get("exhibition", {})
        for hall_info in exhibition.get("exhibitionHall", []):
            hall_name = hall_info.get("name", "")
            if hall_name:
                halls.append(hall_name)
            for stand_info in hall_info.get("stand", []):
                stand_name = stand_info.get("name", "")
                if stand_name:
                    stands.append(stand_name)

        # Website
        website = ex.get("url", "") or ""
        if not website:
            href = exhibition.get("href", "")
            if href and "messefrankfurt" not in href:
                website = href

        return Exhibitor(
            company_name=name,
            website=website or None,
            hall=", ".join(halls) if halls else None,
            stand=", ".join(stands) if stands else None,
            country=country_data.get("label") or None,
            city=address_data.get("city") or None,
            address=address_data.get("street") or None,
            phone=address_data.get("tel") or None,
            email=address_data.get("email") or None,
            description=ex.get("shortDescription") or None,
        )

    def _extract_fair_name(self, url: str, event_variable: str) -> str:
        hostname = urlparse(url).hostname or ""
        subdomain = hostname.split(".")[0]
        return subdomain.replace("-", " ").title()
