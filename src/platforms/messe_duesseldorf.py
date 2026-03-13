from __future__ import annotations

import asyncio
import html
import re
from urllib.parse import urlparse

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import MAX_RETRIES, REQUEST_DELAY
from src.models import Exhibitor, ScrapeResult
from src.platforms.base import BaseScraper

DOMAIN_PATTERN = re.compile(
    r"(messe-duesseldorf\.com|"
    r"euroshop-tradefair\.com|"
    r"euroshop\.de|"
    r"medica-tradefair\.com|"
    r"boot-tradefair\.com|"
    r"drupa-tradefair\.com|"
    r"interpack-tradefair\.com)",
    re.IGNORECASE,
)

# Map main domains to their tradefair API domains
DOMAIN_MAP: dict[str, str] = {
    "www.euroshop.de": "www.euroshop-tradefair.com",
    "euroshop.de": "www.euroshop-tradefair.com",
}


def _parse_location(location: str) -> tuple[str | None, str | None]:
    """Parse 'Hall 7, level 0 / C39' into (hall, stand)."""
    if not location:
        return None, None
    parts = location.split("/", 1)
    hall = parts[0].strip() if parts else None
    stand = parts[1].strip() if len(parts) > 1 else None
    return hall, stand


class MesseDuesseldorfScraper(BaseScraper):
    name = "messe_duesseldorf"
    description = "Messe Düsseldorf VIS API (euroshop, medica, boot, drupa, etc.)"
    url_patterns = ["*-tradefair.com", "messe-duesseldorf.com", "euroshop.de"]

    @classmethod
    def detect(cls, url: str) -> bool:
        return bool(DOMAIN_PATTERN.search(url))

    def _get_api_hostname(self, url: str) -> str:
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        return DOMAIN_MAP.get(hostname, hostname)

    def _get_hostname(self, url: str) -> str:
        parsed = urlparse(url)
        return parsed.hostname or ""

    def _extract_fair_slug(self, url: str) -> str:
        host = self._get_hostname(url)
        match = re.match(r"(?:www\.)?(.+?)-tradefair\.com", host)
        if match:
            return match.group(1)
        # Handle domains like euroshop.de
        match = re.match(r"(?:www\.)?(.+?)\.(?:de|com)", host)
        if match:
            return match.group(1)
        parts = [p for p in urlparse(url).path.strip("/").split("/") if p]
        return parts[0] if parts else "unknown"

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def _fetch_directory_letter(
        self, client: httpx.AsyncClient, base_url: str, letter: str
    ) -> list[dict]:
        resp = await client.get(f"{base_url}/{letter}", timeout=30)
        resp.raise_for_status()
        return resp.json()

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=1, max=10),
    )
    async def _fetch_exhibitor_detail(
        self, client: httpx.AsyncClient, api_host: str, exh_id: str
    ) -> dict:
        url = f"https://{api_host}/vis-api/vis/v1/en/exhibitors/{exh_id}/slices/profile"
        resp = await client.get(url, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _parse_exhibitor_from_detail(self, detail: dict) -> Exhibitor:
        location = detail.get("location", "")
        hall, stand = _parse_location(location)

        # Extract website from links
        website = None
        for link in detail.get("links", []):
            link_url = link.get("link", "")
            if link_url and link_url.startswith("http"):
                website = link_url
                break

        # Extract phone
        phone_data = detail.get("phone", {})
        phone = phone_data.get("phone") if isinstance(phone_data, dict) else None

        # Extract address
        addr_data = detail.get("profileAddress", {})
        address_parts = []
        for line in addr_data.get("address", []):
            if line:
                address_parts.append(line)
        if addr_data.get("zip") or addr_data.get("city"):
            address_parts.append(
                f"{addr_data.get('zip', '')} {addr_data.get('city', '')}".strip()
            )
        address = ", ".join(address_parts) if address_parts else None

        # Extract categories
        categories = []
        for cat in detail.get("categories", []):
            label = cat.get("label")
            if label:
                categories.append(label)

        # Extract description (strip HTML tags and decode entities)
        description = detail.get("text")
        if description:
            description = re.sub(r"<[^>]+>", "", description)
            description = html.unescape(description).strip()
            if len(description) > 500:
                description = description[:500] + "..."

        return Exhibitor(
            company_name=detail.get("name", "Unknown"),
            website=website,
            hall=hall,
            stand=stand,
            country=addr_data.get("country") or None,
            city=addr_data.get("city") or None,
            categories=categories,
            description=description,
            phone=phone,
            email=detail.get("email") or None,
            address=address,
        )

    async def scrape(self, url: str, limit: int = 0) -> ScrapeResult:
        import typer

        fair_slug = self._extract_fair_slug(url)
        api_host = self._get_api_hostname(url)
        base_url = f"https://{api_host}/vis-api/vis/v1/en/directory"

        async with httpx.AsyncClient(
            headers={
                "User-Agent": "Mozilla/5.0 AusstellerListe/0.1",
                "X-Vis-Domain": api_host,
                "Accept": "application/json",
            },
            follow_redirects=True,
        ) as client:
            # Step 1: Get available letters
            meta_resp = await client.get(f"{base_url}/meta", timeout=30)
            meta_resp.raise_for_status()
            letters = [
                l["link"] for l in meta_resp.json()["links"] if l["isFilled"]
            ]

            # Step 2: Collect exhibitor IDs from directory
            exh_ids: list[str] = []
            for letter in letters:
                typer.echo(f"  Fetching letter '{letter}'...")
                items = await self._fetch_directory_letter(client, base_url, letter)

                for item in items:
                    if item.get("type") != "profile":
                        continue
                    exh_id = item.get("exh")
                    if exh_id:
                        exh_ids.append(exh_id)
                    if limit and len(exh_ids) >= limit:
                        break

                if limit and len(exh_ids) >= limit:
                    break

                await asyncio.sleep(REQUEST_DELAY)

            if limit:
                exh_ids = exh_ids[:limit]

            # Step 3: Fetch full details for each exhibitor
            typer.echo(f"\nFetching details for {len(exh_ids)} exhibitors...")
            exhibitors: list[Exhibitor] = []
            for i, exh_id in enumerate(exh_ids, 1):
                if i % 25 == 0 or i == len(exh_ids):
                    typer.echo(f"  Detail {i}/{len(exh_ids)}...")
                try:
                    detail = await self._fetch_exhibitor_detail(
                        client, api_host, exh_id
                    )
                    exhibitors.append(self._parse_exhibitor_from_detail(detail))
                except Exception as e:
                    typer.echo(f"  Warning: Could not fetch detail for {exh_id}: {e}")

                await asyncio.sleep(REQUEST_DELAY)

        return ScrapeResult(
            fair_name=fair_slug,
            fair_url=url,
            exhibitors=exhibitors,
        )
