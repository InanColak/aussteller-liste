from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

import httpx
from tenacity import retry, stop_after_attempt

from src.config import REQUEST_DELAY
from src.models import Exhibitor, ScrapeResult
from src.platforms._retry import RETRY_ATTEMPTS, smart_retry_wait
from src.platforms.base import BaseScraper, ProgressCallback

# Known Messe Berlin / Corussoft Navigator sites
DOMAIN_PATTERN = re.compile(
    r"(itb\.com|"
    r"greuneswoche\.de|"
    r"gruenewoche\.de|"
    r"innotrans\.de|"
    r"fruitlogistica\.com|"
    r"ila-berlin\.de)",
    re.IGNORECASE,
)

# Map fair domains to their Navigator topic IDs and navigator URLs
NAVIGATOR_MAP: dict[str, dict[str, str]] = {
    "itb.com": {
        "topic": "2023_itb",
        "sot": "ITB",
        "navigator_url": "https://navigate.itb.com",
    },
    # Add more fairs as discovered
}

API_BASE = "https://live.messebackend.aws.corussoft.de"

# Default headers for the Corussoft API
DEFAULT_HEADERS = {
    "ec-client": "EventGuide/2.24.0-10878[52]",
    "accept": "application/json",
    "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
}

# Static token (public, used by the web app)
DEFAULT_BE_TOKEN = (
    "eyJhbGciOiJIUzUxMiJ9."
    "eyJpYXQiOjE2Mzg1NDEyMTgsImlzcyI6Imd1aWRlQkUiLCJzZXJpZXNPZlRvcGljc05hbWUiOiJJVEIifQ."
    "VQs0kLcNvd6x3QBZXVCZJHGzVxrJboEB95D01gYj4lgjPXhaB0IO5BIX5VKjy1-2RUJTlQgaCh6rn7z69qbD9A"
)


def _get_fair_key(url: str) -> str | None:
    """Extract the fair key from a URL (e.g., 'itb.com' from 'https://www.itb.com/de')."""
    hostname = urlparse(url).hostname or ""
    # Remove common prefixes
    for prefix in ("www.", "navigate."):
        if hostname.startswith(prefix):
            hostname = hostname[len(prefix):]
    for key in NAVIGATOR_MAP:
        if key in hostname:
            return key
    return None


class MesseBerlinScraper(BaseScraper):
    name = "messe_berlin"
    description = "Messe Berlin / Corussoft Navigator (ITB, Grüne Woche, InnoTrans, etc.)"
    url_patterns = ["itb.com", "navigate.itb.com", "gruenewoche.de", "innotrans.de"]

    @classmethod
    def detect(cls, url: str) -> bool:
        return bool(DOMAIN_PATTERN.search(url))

    async def _get_config(self, url: str) -> dict[str, str]:
        """Get Navigator config for the given URL. Try known map first, then auto-detect."""
        fair_key = _get_fair_key(url)
        if fair_key and fair_key in NAVIGATOR_MAP:
            config = NAVIGATOR_MAP[fair_key]
            return {
                "topic": config["topic"],
                "sot": config["sot"],
                "navigator_url": config["navigator_url"],
            }
        # Fallback: try to detect from the navigate subdomain
        raise ValueError(f"No Navigator config found for {url}. Add it to NAVIGATOR_MAP.")

    async def _register_device(self, client: httpx.AsyncClient, config: dict) -> str:
        """Register a device and get a beConnectionToken."""
        resp = await client.post(
            f"{API_BASE}/rest/appdevice/sot/{config['sot']}",
            params={
                "topic": config["topic"],
                "os": "web",
                "appUrl": config["navigator_url"],
                "lang": "en",
                "apiVersion": "52",
                "timezoneOffset": "0",
            },
            data='{"kind":"web","lang":"en"}',
            headers={"content-type": "application/json"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("beConnectionToken", DEFAULT_BE_TOKEN)

    @retry(
        stop=stop_after_attempt(RETRY_ATTEMPTS),
        wait=smart_retry_wait,
    )
    async def _fetch_exhibitor_page(
        self, client: httpx.AsyncClient, config: dict, start: int, count: int
    ) -> dict:
        """Fetch a page of exhibitors from the search API."""
        resp = await client.post(
            f"{API_BASE}/webservice/search",
            data={
                "topic": config["topic"],
                "os": "web",
                "appUrl": config["navigator_url"],
                "lang": "en",
                "apiVersion": "52",
                "timezoneOffset": "0",
                "filterlist": "entity_orga",
                "startresultrow": str(start),
                "numresultrows": str(count),
                "order": "lexic",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    @retry(
        stop=stop_after_attempt(RETRY_ATTEMPTS),
        wait=smart_retry_wait,
    )
    async def _fetch_company_detail(
        self, client: httpx.AsyncClient, config: dict, org_id: str
    ) -> dict:
        """Fetch full company details."""
        resp = await client.post(
            f"{API_BASE}/webservice/companydetails",
            data={
                "topic": config["topic"],
                "os": "web",
                "appUrl": config["navigator_url"],
                "lang": "en",
                "apiVersion": "52",
                "timezoneOffset": "0",
                "organizationid": org_id,
                "hideNewsdata": "false",
                "showPersonsEventDates": "true",
                "showCategoryHierarchy": "true",
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def _parse_exhibitor(self, detail: dict) -> Exhibitor:
        """Parse company detail response into an Exhibitor."""
        # Website
        website = detail.get("web")
        if website and not website.startswith("http"):
            website = f"https://{website}"

        # Address
        address_parts = []
        for key in ["adress1", "adress2", "adress3"]:
            val = detail.get(key)
            if val:
                address_parts.append(val)
        zip_city = f"{detail.get('postCode', '')} {detail.get('city', '')}".strip()
        if zip_city:
            address_parts.append(zip_city)
        country = detail.get("country")
        if country:
            address_parts.append(country)
        address = ", ".join(address_parts) if address_parts else None

        # Hall/Stand from stands in listing data (not in detail)
        hall = None
        stand = None

        # Categories
        categories = []
        for cat_group in detail.get("categories", []):
            for node in cat_group.get("nodes", []):
                name = node.get("name") or node.get("label")
                if name:
                    categories.append(name)

        # Description
        description = None
        desc_data = detail.get("description", {})
        if isinstance(desc_data, dict):
            description = desc_data.get("text") or desc_data.get("teaser")
        if description and len(description) > 500:
            description = description[:500] + "..."

        return Exhibitor(
            company_name=detail.get("name", "Unknown"),
            website=website,
            hall=hall,
            stand=stand,
            country=country,
            city=detail.get("city"),
            categories=categories,
            description=description,
            phone=detail.get("phone"),
            email=detail.get("email"),
            address=address,
        )

    def _parse_exhibitor_from_listing(self, entity: dict) -> Exhibitor:
        """Parse listing data (without detail) into an Exhibitor with basic info."""
        stands = entity.get("stands", [])
        hall = None
        stand = None
        if stands:
            hall = stands[0].get("hallName")
            stand = stands[0].get("standNameShort")

        categories = []
        for cat in entity.get("categories", []):
            name = cat.get("name")
            if name and name not in ("Branches", "Country"):
                categories.append(name)

        return Exhibitor(
            company_name=entity.get("name", "Unknown"),
            hall=hall,
            stand=stand,
            country=entity.get("country"),
            city=entity.get("city"),
            categories=categories,
            description=entity.get("teaser"),
        )

    async def scrape(self, url: str, limit: int = 0, progress_callback: ProgressCallback = None) -> ScrapeResult:
        import typer

        config = await self._get_config(url)
        fair_name = config["sot"]

        headers = {
            **DEFAULT_HEADERS,
            "beconnectiontoken": DEFAULT_BE_TOKEN,
            "ec-client-branding": config["topic"],
            "referer": config["navigator_url"] + "/",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }

        async with httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
        ) as client:
            # Try to register device for a fresh token (optional)
            try:
                token = await self._register_device(client, config)
                client.headers["beconnectiontoken"] = token
            except Exception:
                pass  # Use default static token

            # Get total count first
            typer.echo("Fetching exhibitor count...")
            first_page = await self._fetch_exhibitor_page(client, config, 0, 1)
            total = first_page.get("count", 0)
            typer.echo(f"Total exhibitors available: {total}")

            target = min(total, limit) if limit else total

            # Fetch all exhibitor IDs from listing
            PAGE_SIZE = 100
            all_ids: list[tuple[str, dict]] = []  # (id, listing_data)
            page_index = 0
            for start in range(0, target, PAGE_SIZE):
                page_index += 1
                count = min(PAGE_SIZE, target - start)
                typer.echo(f"  Fetching list {start + 1}-{start + count} of {target}...")
                page_data = await self._fetch_exhibitor_page(client, config, start, count)
                for entity in page_data.get("entities", []):
                    all_ids.append((entity["id"], entity))
                    if limit and len(all_ids) >= limit:
                        break

                if progress_callback:
                    await progress_callback(len(all_ids), f"Listing page {page_index} — {len(all_ids)}/{target} exhibitors found")

                if limit and len(all_ids) >= limit:
                    break
                await asyncio.sleep(REQUEST_DELAY)

            if limit:
                all_ids = all_ids[:limit]

            # Fetch details for each exhibitor
            typer.echo(f"\nFetching details for {len(all_ids)} exhibitors...")
            exhibitors: list[Exhibitor] = []
            for i, (org_id, listing) in enumerate(all_ids, 1):
                if i % 25 == 0 or i == len(all_ids):
                    typer.echo(f"  Detail {i}/{len(all_ids)}...")
                try:
                    detail = await self._fetch_company_detail(client, config, org_id)
                    ex = self._parse_exhibitor(detail)
                    # Fill in hall/stand from listing data
                    stands = listing.get("stands", [])
                    if stands:
                        ex.hall = stands[0].get("hallName")
                        ex.stand = stands[0].get("standNameShort")
                    exhibitors.append(ex)
                except Exception as e:
                    typer.echo(f"  Warning: Could not fetch detail for {org_id}: {e}")
                    # Fall back to listing data
                    exhibitors.append(self._parse_exhibitor_from_listing(listing))

                if i % 25 == 0 and progress_callback:
                    await progress_callback(len(exhibitors), f"Detail {i}/{len(all_ids)} — {len(exhibitors)} exhibitors")

                await asyncio.sleep(REQUEST_DELAY)

        return ScrapeResult(
            fair_name=fair_name,
            fair_url=url,
            exhibitors=exhibitors,
        )
