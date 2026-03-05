from __future__ import annotations

import asyncio
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
    r"medica-tradefair\.com|"
    r"boot-tradefair\.com|"
    r"drupa-tradefair\.com|"
    r"interpack-tradefair\.com)",
    re.IGNORECASE,
)


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
    url_patterns = ["*-tradefair.com", "messe-duesseldorf.com"]

    @classmethod
    def detect(cls, url: str) -> bool:
        return bool(DOMAIN_PATTERN.search(url))

    def _get_hostname(self, url: str) -> str:
        parsed = urlparse(url)
        return parsed.hostname or ""

    def _extract_fair_slug(self, url: str) -> str:
        host = self._get_hostname(url)
        match = re.match(r"(?:www\.)?(.+?)-tradefair\.com", host)
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

    def _parse_exhibitor(self, item: dict) -> Exhibitor:
        location = item.get("location", "")
        hall, stand = _parse_location(location)

        return Exhibitor(
            company_name=item.get("name", "Unknown"),
            country=item.get("country") or None,
            city=item.get("city") or None,
            hall=hall,
            stand=stand,
        )

    async def scrape(self, url: str, limit: int = 0) -> ScrapeResult:
        import typer

        fair_slug = self._extract_fair_slug(url)
        hostname = self._get_hostname(url)
        base_url = f"https://{hostname}/vis-api/vis/v1/en/directory"

        async with httpx.AsyncClient(
            headers={
                "User-Agent": "Mozilla/5.0 AusstellerListe/0.1",
                "X-Vis-Domain": hostname,
                "Accept": "application/json",
            },
            follow_redirects=True,
        ) as client:
            # Get available letters
            meta_resp = await client.get(f"{base_url}/meta", timeout=30)
            meta_resp.raise_for_status()
            letters = [
                l["link"] for l in meta_resp.json()["links"] if l["isFilled"]
            ]

            exhibitors: list[Exhibitor] = []
            for letter in letters:
                typer.echo(f"  Fetching letter '{letter}'...")
                items = await self._fetch_directory_letter(client, base_url, letter)

                for item in items:
                    # Only include exhibitor profiles, skip trademarks etc.
                    if item.get("type") != "profile":
                        continue
                    exhibitors.append(self._parse_exhibitor(item))
                    if limit and len(exhibitors) >= limit:
                        break

                if limit and len(exhibitors) >= limit:
                    break

                await asyncio.sleep(REQUEST_DELAY)

        if limit:
            exhibitors = exhibitors[:limit]

        return ScrapeResult(
            fair_name=fair_slug,
            fair_url=url,
            exhibitors=exhibitors,
        )
