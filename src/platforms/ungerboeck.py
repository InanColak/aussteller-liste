from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import urlparse

from playwright.async_api import Page, async_playwright

from src.config import REQUEST_DELAY
from src.models import Exhibitor, ScrapeResult
from src.platforms.base import BaseScraper, ProgressCallback

# Matches *.ungerboeck.com and *.ungerboeck.net URLs
DOMAIN_PATTERN = re.compile(r"ungerboeck\.(com|net)", re.IGNORECASE)


def _parse_double_encoded_json(raw: object) -> dict:
    """Ungerboeck API returns double-encoded JSON: [json_string, ...].

    The first element is a JSON string that must be parsed again.
    """
    if isinstance(raw, list) and raw and isinstance(raw[0], str):
        return json.loads(raw[0])
    if isinstance(raw, dict):
        return raw
    return {}


class UngerboeckScraper(BaseScraper):
    name = "ungerboeck"
    description = "Ungerboeck/Momentus VFP (IHM, iba, ExCeL London, Messe Bremen, etc.)"
    url_patterns = ["*.ungerboeck.com", "*.ungerboeck.net"]

    @classmethod
    def detect(cls, url: str) -> bool:
        return bool(DOMAIN_PATTERN.search(url))

    @classmethod
    def detect_from_links(cls, links: list[dict[str, str]]) -> str | None:
        """Check page links for Ungerboeck exhibitor directory URLs.

        Returns the ungerboeck URL if found, None otherwise.
        """
        for link in links:
            href = link.get("href", "")
            if DOMAIN_PATTERN.search(href) and "app85.cshtml" in href:
                return href
        return None

    def _parse_exhibitor(
        self,
        listing_item: dict,
        detail: dict | None = None,
        product_map: dict[str, str] | None = None,
    ) -> Exhibitor:
        """Parse exhibitor from listing data + optional detail data."""
        booth_names = listing_item.get("BoothNames", [])
        hall = None
        stand = None
        if booth_names:
            parts = booth_names[0].split(".", 1)
            hall = parts[0] if parts else None
            stand = parts[1] if len(parts) > 1 else None

        # Resolve product codes to descriptions
        categories = []
        if product_map:
            for code in listing_item.get("ProductCodes", []):
                desc = product_map.get(code)
                if desc:
                    categories.append(desc)

        # Base exhibitor from listing
        name = (listing_item.get("Name") or "Unknown").strip()
        country = listing_item.get("CatCountryDesc") or listing_item.get("CatCountry") or None

        if not detail:
            return Exhibitor(
                company_name=name,
                hall=hall,
                stand=stand,
                country=country,
                categories=categories,
            )

        # Enrich with detail data
        # Address
        address_parts = []
        for key in ("CatAddress1", "CatAddress2", "CatAddress3"):
            val = detail.get(key)
            if val:
                address_parts.append(val)
        zip_city = f"{detail.get('CatPostalCode', '')} {detail.get('CatCity', '')}".strip()
        if zip_city:
            address_parts.append(zip_city)
        detail_country = detail.get("CatCountry")
        if detail_country:
            address_parts.append(detail_country)
        address = ", ".join(address_parts) if address_parts else None

        # Website
        website = detail.get("WebsiteURL") or None
        if website and not website.startswith("http"):
            website = f"https://{website}"

        # Description
        description = detail.get("CatDesc") or None
        if description:
            description = re.sub(r"<[^>]+>", "", description).strip()
            if len(description) > 500:
                description = description[:500] + "..."

        # Products from detail (more descriptive than listing codes)
        if detail.get("Products"):
            categories = [
                p.get("Desc") or p.get("Description") or p.get("Name")
                for p in detail["Products"]
                if p.get("Desc") or p.get("Description") or p.get("Name")
            ]

        return Exhibitor(
            company_name=(detail.get("Name") or name).strip(),
            website=website,
            hall=hall,
            stand=stand,
            country=detail_country or country,
            city=detail.get("CatCity") or None,
            categories=categories,
            description=description,
            phone=detail.get("CatPhone") or None,
            email=detail.get("CatEmail") or None,
            address=address,
        )

    async def _fetch_detail_via_click(
        self, page: Page, index: int
    ) -> dict | None:
        """Fetch exhibitor detail by clicking the nth visible list item.

        The Ungerboeck API rejects direct fetch calls but accepts requests
        made through the SPA's internal AJAX pipeline (which adds session
        tokens like x-nonce, wsid, workstationname automatically).
        We trigger the SPA's own click handler and intercept the response.
        """
        detail_data: list[str | None] = [None]

        async def on_response(response):
            if "GetExhibitorDetails" in response.url and response.status in (200, 201):
                try:
                    body = await response.body()
                    detail_data[0] = body.decode("utf-8", errors="replace")
                except Exception:
                    pass

        page.on("response", on_response)

        # Click the nth visible exhibitor list item
        await page.evaluate(
            """(idx) => {
                const items = Array.from(
                    document.querySelectorAll('#exhibitorList li.au-target')
                ).filter(i => i.offsetParent !== null);
                if (items[idx]) items[idx].click();
            }""",
            index,
        )

        await page.wait_for_timeout(3000)

        # Close the modal
        await page.evaluate(
            """() => {
                const btn = document.querySelector(
                    '.modal-header button, .btn-close, [aria-label="Close"], .close'
                );
                if (btn) btn.click();
            }"""
        )
        await page.wait_for_timeout(500)

        page.remove_listener("response", on_response)

        if detail_data[0]:
            raw = json.loads(detail_data[0])
            parsed = _parse_double_encoded_json(raw)
            return parsed.get("ReturnObj", parsed)

        return None

    async def scrape(self, url: str, limit: int = 0, progress_callback: ProgressCallback = None) -> ScrapeResult:
        """Scrape exhibitors by loading the Ungerboeck SPA and intercepting API data.

        1. Load portal page → intercept GetInitialData (exhibitor listing)
        2. Click each exhibitor → intercept GetExhibitorDetails (contact info)
        """
        import typer

        typer.echo("Loading Ungerboeck portal (this may take a moment)...")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = await context.new_page()

            # Step 1: Intercept the GetInitialData response
            initial_data_raw: list[str | None] = [None]

            async def intercept_route(route):
                response = await route.fetch()
                body = await response.body()
                initial_data_raw[0] = body.decode("utf-8", errors="replace")
                await route.fulfill(response=response, body=body)

            await page.route("**/api/VFPServer/GetInitialData", intercept_route)

            try:
                await page.goto(url, wait_until="networkidle", timeout=90_000)
            except Exception:
                await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            await page.wait_for_timeout(15_000)

            await page.unroute("**/api/VFPServer/GetInitialData")

            if not initial_data_raw[0]:
                await browser.close()
                raise ValueError(
                    "Could not capture exhibitor data from Ungerboeck portal. "
                    "The page may require authentication or failed to load."
                )

            # Parse initial data
            raw = json.loads(initial_data_raw[0])
            data = _parse_double_encoded_json(raw)
            return_obj = data.get("ReturnObj", data)

            exhibitor_list = return_obj.get("ExhibitorList", [])
            config_code = return_obj.get("ConfigCode", "")
            product_map = return_obj.get("ProductDescMap", {})

            typer.echo(f"Found {len(exhibitor_list)} exhibitors in listing.")

            if limit:
                exhibitor_list = exhibitor_list[:limit]

            # Step 2: Fetch details by clicking each exhibitor in the SPA
            typer.echo(f"\nFetching details for {len(exhibitor_list)} exhibitors...")
            exhibitors: list[Exhibitor] = []

            for i, item in enumerate(exhibitor_list):
                if (i + 1) % 25 == 0 or (i + 1) == len(exhibitor_list):
                    typer.echo(f"  Detail {i + 1}/{len(exhibitor_list)}...")

                detail = None
                try:
                    detail = await self._fetch_detail_via_click(page, i)
                except Exception as e:
                    typer.echo(f"  Warning: detail for index {i}: {e}")

                exhibitors.append(
                    self._parse_exhibitor(item, detail, product_map)
                )

                if (i + 1) % 25 == 0 and progress_callback:
                    await progress_callback(len(exhibitors), f"Detail {i + 1}/{len(exhibitor_list)} — {len(exhibitors)} exhibitors")

                await asyncio.sleep(REQUEST_DELAY)

            await browser.close()

        fair_name = config_code or urlparse(url).hostname or "unknown"

        return ScrapeResult(
            fair_name=fair_name,
            fair_url=url,
            exhibitors=exhibitors,
        )
