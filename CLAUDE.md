# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Trade fair exhibitor list scraper. Given a trade fair website URL, it extracts exhibitor data (company name, website, hall/stand, contact info, etc.) and exports to Excel/CSV. Uses a tiered scraping strategy with built-in platform scrapers, learned site profiles, and AI-powered fallback discovery.

## Commands

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"
playwright install chromium

# Run scraper
aussteller scrape <URL>
aussteller scrape <URL> --format csv --limit 50

# List known platforms and learned profiles
aussteller platforms

# Lint
ruff check src/
ruff format --check src/

# Tests
pytest
pytest tests/test_specific.py -k "test_name"
```

## Environment

Requires a `.env` file with:
- `OPENAI_API_KEY` — required for AI-powered discovery (link finding, data extraction, site analysis)
- `OPENAI_MODEL` — defaults to `gpt-4o-mini`
- `REQUEST_DELAY` — delay between API requests (default 0.5s)

## Architecture

### Scraping Pipeline (src/orchestrator.py)

The orchestrator (`scrape_url`) tries 5 strategies in order:

1. **Built-in platform scrapers** — `detect_platform(url)` matches URL against known domains
2. **Learned site profiles** — JSON profiles saved from previous successful scrapes
3. **Platform detection from links** — fetches page links and looks for API patterns (VIS API, Corussoft Navigator)
4. **AI-powered discovery** — uses OpenAI to identify exhibitor list links, then extracts data from page text
5. **Learning** — after successful AI discovery, analyzes the site and saves a reusable profile

### Built-in Platform Scrapers (src/platforms/)

Each scraper extends `BaseScraper` (ABC with `detect()` and `scrape()` methods) and is registered in `registry.py`'s `SCRAPERS` list.

- **MesseDuesseldorfScraper** — VIS API (`/vis-api/vis/v1/`): fetches alphabetical directory, then detail per exhibitor. Covers euroshop, medica, boot, drupa, interpack.
- **MesseBerlinScraper** — Corussoft Navigator API (`messebackend.aws.corussoft.de`): registers device, searches via POST, fetches company details. Covers ITB, Grüne Woche, InnoTrans. New fairs need entries in `NAVIGATOR_MAP`.

To add a new platform scraper: create a class extending `BaseScraper`, implement `detect()` and `scrape()`, add to `SCRAPERS` in `registry.py`.

### AI Discovery Layer (src/discovery/)

- **page_fetcher.py** — Playwright-based page loading with cookie banner dismissal. Shared `_dismiss_cookie_banner()` function.
- **link_finder.py** — Sends page links to OpenAI to identify exhibitor list URLs.
- **data_extractor.py** — Loads pages with Playwright, sends visible text to OpenAI for structured exhibitor extraction with pagination support.

### Learning System (src/learning/)

Allows the system to "remember" how to scrape a site without AI next time:

- **models.py** — `SiteProfile` with `ListingConfig`, `DetailConfig`, `ExtractionRule`, `PaginationConfig`. Supports strategies: `single_page`, `paged`, `alpha_index`, `api_endpoint`.
- **analyzer.py** — Visits site with Playwright, captures network requests and structure, asks OpenAI to generate a `SiteProfile`.
- **store.py** — Saves/loads profiles as JSON files in `src/learning/profiles/`. Matches profiles to URLs via domain glob patterns.
- **replay.py** — Re-executes a saved profile's scraping steps using httpx (API) or Playwright (HTML, not yet implemented). Uses `_extract_json_path()` for dot-notation JSON traversal with array support (`items[*].name`).

### Data Models (src/models.py)

- `Exhibitor` — Pydantic model with fields: company_name (required), website, hall, stand, country, city, categories, description, phone, email, address.
- `ScrapeResult` — wraps a list of exhibitors with fair metadata. `total_exhibitors` auto-computed from list length.

### Export (src/exporters.py)

Exports to `output/` directory as timestamped `.xlsx` or `.csv` files (UTF-8 with BOM for CSV).

## Key Patterns

- All scraping is async (`asyncio`). CLI entry point uses `asyncio.run()`.
- HTTP requests use `httpx.AsyncClient`; browser automation uses Playwright (headless Chromium).
- Retry logic via `tenacity` on API calls.
- Rate limiting via `REQUEST_DELAY` between requests.
- German-language UI elements are common (cookie banners, pagination text like "nächste", "weiter").
