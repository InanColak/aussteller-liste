from __future__ import annotations

import asyncio
import html
import logging
import re
from urllib.parse import urlparse

import httpx
from playwright.async_api import async_playwright

from src.config import REQUEST_DELAY
from src.learning.models import AuthConfig, DetailConfig, ExtractionRule, ListingConfig, SiteProfile
from src.models import Exhibitor, ScrapeResult
from src.platforms.base import ProgressCallback

logger = logging.getLogger("aussteller-api")


def _resolve_template(template: str, **kwargs: str) -> str:
    """Replace {hostname}, {letter}, {page}, {offset}, {id} etc. in a URL template."""
    for key, value in kwargs.items():
        template = template.replace(f"{{{key}}}", str(value))
    return template


def _extract_json_path(data: dict | list, path: str) -> str | list | None:
    """Simple dot-notation + array access for JSON.

    Supports: "name", "phone.phone", "links[0].link", "categories[*].label"
    """
    if not path:
        return None

    parts = re.split(r"\.", path)
    current: object = data

    for part in parts:
        if current is None:
            return None

        # Handle array access: items[0] or items[*]
        match = re.match(r"^(\w+)\[([0-9*]+|\?\w+)]$", part)
        if match:
            key, idx = match.group(1), match.group(2)
            if isinstance(current, dict):
                current = current.get(key)
            if current is None:
                return None

            if idx == "*":
                # Collect from all items
                if isinstance(current, list):
                    remaining = ".".join(parts[parts.index(part) + 1 :])
                    if remaining:
                        return [_extract_json_path(item, remaining) for item in current if item]
                    return current
                return None
            else:
                idx_int = int(idx)
                if isinstance(current, list) and idx_int < len(current):
                    current = current[idx_int]
                else:
                    return None
        else:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None

    return current


def _apply_transform(value: str | None, rule: ExtractionRule) -> str | None:
    """Apply post-processing to an extracted value."""
    if value is None:
        return None

    if rule.regex:
        match = re.search(rule.regex, value)
        if match and match.groups():
            value = match.group(1)
        elif match:
            value = match.group(0)
        else:
            return None

    if rule.transform == "strip_html":
        value = re.sub(r"<[^>]+>", "", value)
        value = html.unescape(value).strip()
    elif rule.transform == "unescape_html":
        value = html.unescape(value).strip()
    elif rule.transform == "truncate_500":
        if len(value) > 500:
            value = value[:500] + "..."

    return value.strip() if value else None


def _extract_field(data: dict, rule: ExtractionRule) -> str | list[str] | None:
    """Extract a single field from JSON data using an extraction rule."""
    raw = _extract_json_path(data, rule.json_path or "")
    if raw is None:
        return [] if rule.is_array else None

    if rule.is_array:
        if isinstance(raw, list):
            return [_apply_transform(str(v), rule) for v in raw if v]
        return [_apply_transform(str(raw), rule)] if raw else []

    return _apply_transform(str(raw), rule)


def _build_exhibitor(
    listing_data: dict,
    detail_data: dict | None,
    field_map: dict[str, ExtractionRule],
) -> Exhibitor | None:
    """Build an Exhibitor from listing and/or detail data using field_map."""
    fields: dict[str, object] = {}
    for field_name, rule in field_map.items():
        source_data = detail_data if (rule.source == "detail" and detail_data) else listing_data
        fields[field_name] = _extract_field(source_data, rule)

    if not fields.get("company_name"):
        return None

    return Exhibitor(**fields)


async def _capture_auth_header(auth: AuthConfig) -> str:
    """Open a browser, visit the page, and intercept the auth header from API calls."""
    if not auth.page_url or not auth.intercept_pattern or not auth.header_name:
        return ""

    logger.info("Capturing auth from %s (pattern: %s)", auth.page_url, auth.intercept_pattern)

    captured_value = ""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )

        async def on_request(request):
            nonlocal captured_value
            if auth.intercept_pattern in request.url and not captured_value:
                captured_value = request.headers.get(auth.header_name.lower(), "")

        page.on("request", on_request)

        try:
            await page.goto(auth.page_url, wait_until="load", timeout=60_000)
        except Exception:
            pass
        await page.wait_for_timeout(8000)
        await browser.close()

    if captured_value:
        logger.info("Auth captured successfully (%s: %s...)", auth.header_name, captured_value[:20])
    else:
        logger.warning("Failed to capture auth header from %s", auth.page_url)

    return captured_value


async def _fetch_meta_letters(
    client: httpx.AsyncClient, listing: ListingConfig, hostname: str
) -> list[str]:
    """Fetch available letters from meta endpoint."""
    if not listing.meta_url:
        return list("abcdefghijklmnopqrstuvwxyz")

    url = _resolve_template(listing.meta_url, hostname=hostname)
    resp = await client.get(url, timeout=30)
    resp.raise_for_status()
    meta = resp.json()

    if listing.meta_letters_path and "links" in str(listing.meta_letters_path):
        links = meta.get("links", [])
        return [l["link"] for l in links if l.get("isFilled", True)]

    return list("abcdefghijklmnopqrstuvwxyz")


async def replay_api_scrape(
    profile: SiteProfile, url: str, limit: int = 0, progress_callback: ProgressCallback = None
) -> ScrapeResult:
    """Replay a scrape using a learned API profile."""
    import typer

    hostname = urlparse(url).hostname or ""
    listing = profile.listing
    detail = profile.detail
    pagination = listing.pagination

    # Build headers
    headers = {
        k: _resolve_template(v, hostname=hostname)
        for k, v in profile.headers.items()
    }
    headers.setdefault("User-Agent", "Mozilla/5.0 AusstellerListe/0.1")

    # Capture auth if needed
    if profile.auth and profile.auth.method == "browser_intercept":
        # Resolve page_url template
        auth = profile.auth
        if auth.page_url:
            auth_page_url = _resolve_template(auth.page_url, hostname=hostname)
            auth = AuthConfig(
                method=auth.method,
                page_url=auth_page_url,
                intercept_pattern=auth.intercept_pattern,
                header_name=auth.header_name,
            )
        auth_value = await _capture_auth_header(auth)
        if auth_value and auth.header_name:
            headers[auth.header_name] = auth_value
        elif not auth_value:
            raise RuntimeError(f"Could not capture auth from {auth.page_url}")

    async with httpx.AsyncClient(
        headers=headers, follow_redirects=True
    ) as client:
        listing_items: list[dict] = []

        if listing.strategy == "alpha_index":
            letters = await _fetch_meta_letters(client, listing, hostname)
            for letter in letters:
                list_url = _resolve_template(
                    listing.url_template, hostname=hostname, letter=letter
                )
                typer.echo(f"  Fetching '{letter}'...")
                resp = await client.get(list_url, timeout=30)
                resp.raise_for_status()
                items = resp.json()

                for item in items:
                    if listing.item_filter:
                        if item.get(listing.item_filter.field) != listing.item_filter.equals:
                            continue
                    listing_items.append(item)
                    if limit and len(listing_items) >= limit:
                        break

                if progress_callback:
                    await progress_callback(len(listing_items), f"Directory '{letter}' scanned — {len(listing_items)} exhibitor IDs found")

                if limit and len(listing_items) >= limit:
                    break
                await asyncio.sleep(REQUEST_DELAY)

        elif listing.strategy in ("paged", "api_endpoint"):
            page_num = pagination.start if pagination else 1
            page_size = pagination.page_size if pagination else 50
            max_pages = pagination.max_pages if pagination else 100
            use_param_pagination = pagination and pagination.param_name

            for _ in range(max_pages):
                if use_param_pagination:
                    # Build URL with query parameters
                    base_url = _resolve_template(listing.url_template, hostname=hostname)
                    params = dict(listing.query_params)
                    params[pagination.param_name] = str(page_num)
                    if pagination.page_size_param:
                        params[pagination.page_size_param] = str(page_size)
                    resp = await client.get(base_url, params=params, timeout=30)
                else:
                    # Legacy template-based pagination
                    list_url = _resolve_template(
                        listing.url_template,
                        hostname=hostname,
                        page=str(page_num),
                        offset=str((page_num - 1) * page_size),
                    )
                    resp = await client.get(list_url, timeout=30)

                resp.raise_for_status()
                response_data = resp.json()

                # Extract items using items_path or common patterns
                if pagination and pagination.items_path:
                    items = _extract_json_path(response_data, pagination.items_path)
                    if not isinstance(items, list):
                        items = []
                elif isinstance(response_data, dict):
                    items = response_data.get("data", response_data.get("results", response_data.get("items", [response_data])))
                else:
                    items = response_data

                if not items:
                    break

                for item in items:
                    if listing.item_filter:
                        if item.get(listing.item_filter.field) != listing.item_filter.equals:
                            continue
                    listing_items.append(item)

                typer.echo(f"  Page {page_num}: {len(items)} items (total: {len(listing_items)})")

                if progress_callback:
                    total_hint = ""
                    if pagination and pagination.total_path:
                        t = _extract_json_path(response_data, pagination.total_path)
                        if t and isinstance(t, (int, float, str)):
                            total_hint = f"/{int(t)}"
                    await progress_callback(len(listing_items), f"Page {page_num} processed — {len(listing_items)}{total_hint} exhibitors")

                if limit and len(listing_items) >= limit:
                    break

                # Check total if available
                if pagination and pagination.total_path:
                    total = _extract_json_path(response_data, pagination.total_path)
                    if total and isinstance(total, (int, float, str)):
                        if len(listing_items) >= int(total):
                            break

                page_num += 1
                await asyncio.sleep(REQUEST_DELAY)

        elif listing.strategy == "single_page":
            list_url = _resolve_template(listing.url_template, hostname=hostname)
            params = dict(listing.query_params) if listing.query_params else None
            resp = await client.get(list_url, params=params, timeout=30)
            resp.raise_for_status()
            response_data = resp.json()

            if pagination and pagination.items_path:
                items = _extract_json_path(response_data, pagination.items_path)
                listing_items = items if isinstance(items, list) else []
            elif isinstance(response_data, dict):
                listing_items = response_data.get("data", response_data.get("results", response_data.get("items", [response_data])))
            else:
                listing_items = response_data or []

        if limit:
            listing_items = listing_items[:limit]

        # Fetch details if needed
        exhibitors: list[Exhibitor] = []

        if detail and detail.source_type == "api":
            typer.echo(f"\nFetching details for {len(listing_items)} exhibitors...")
            id_path = listing.item_id_path or "id"

            for i, item in enumerate(listing_items, 1):
                if i % 25 == 0 or i == len(listing_items):
                    typer.echo(f"  Detail {i}/{len(listing_items)}...")

                exh_id = _extract_json_path(item, id_path)
                if not exh_id:
                    continue

                try:
                    detail_url = _resolve_template(
                        detail.url_template, hostname=hostname, id=str(exh_id)
                    )
                    resp = await client.get(detail_url, timeout=30)
                    resp.raise_for_status()
                    detail_data = resp.json()

                    ex = _build_exhibitor(item, detail_data, profile.field_map)
                    if ex:
                        exhibitors.append(ex)
                except Exception as e:
                    typer.echo(f"  Warning: {exh_id}: {e}")

                if i % 25 == 0 and progress_callback:
                    await progress_callback(len(exhibitors), f"Detail {i}/{len(listing_items)} — {len(exhibitors)} exhibitors")

                await asyncio.sleep(REQUEST_DELAY)
        else:
            for item in listing_items:
                ex = _build_exhibitor(item, None, profile.field_map)
                if ex:
                    exhibitors.append(ex)

    fair_name = urlparse(url).hostname or "unknown"
    fair_name = re.sub(r"^www\.", "", fair_name).split(".")[0]

    return ScrapeResult(
        fair_name=fair_name,
        fair_url=url,
        exhibitors=exhibitors,
    )


async def replay_html_scrape(
    profile: SiteProfile, url: str, limit: int = 0, progress_callback: ProgressCallback = None
) -> ScrapeResult:
    """Replay a scrape using a learned HTML profile (Playwright-based)."""
    import typer

    typer.echo("HTML replay scraping not yet implemented. Falling back to AI discovery.")
    return ScrapeResult(fair_name="unknown", fair_url=url, exhibitors=[])


async def replay_scrape(
    profile: SiteProfile, url: str, limit: int = 0, progress_callback: ProgressCallback = None
) -> ScrapeResult:
    """Replay a scrape using a learned profile."""
    if profile.source_type == "api":
        return await replay_api_scrape(profile, url, limit=limit, progress_callback=progress_callback)
    else:
        return await replay_html_scrape(profile, url, limit=limit, progress_callback=progress_callback)
