from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ExtractionRule(BaseModel):
    """How to extract one field from a page or API response."""

    source: str = "listing"  # "listing" or "detail"

    # For HTML pages
    css: str | None = None
    attribute: str | None = "text"  # "text", "href", "data-*", etc.

    # For JSON APIs
    json_path: str | None = None

    # Post-processing
    regex: str | None = None
    transform: str | None = None  # "strip_html", "truncate_500", "join_comma", "unescape_html"
    is_array: bool = False


class ItemFilter(BaseModel):
    """Filter items from listing (e.g., type == 'profile')."""

    field: str
    equals: str


class AuthConfig(BaseModel):
    """How to capture authentication for API calls."""

    method: str = "browser_intercept"  # "browser_intercept", "static_header", "none"
    page_url: str | None = None  # URL to visit in browser to capture auth
    intercept_pattern: str | None = None  # URL pattern to match in intercepted requests
    header_name: str | None = None  # Header name containing the auth token (e.g., "apikey")


class PaginationConfig(BaseModel):
    """How to navigate through pages."""

    type: str  # "page_number", "offset", "next_url", "cursor", "load_more_button"
    start: int = 1
    page_size: int = 50
    max_pages: int = 100
    next_url_selector: str | None = None
    stop_when_empty: bool = True

    # For API parameter-based pagination
    param_name: str | None = None  # e.g., "pageNumber"
    page_size_param: str | None = None  # e.g., "pageSize"
    total_path: str | None = None  # JSON path to total count, e.g., "result.metaData.hitsTotal"
    items_path: str | None = None  # JSON path to items array, e.g., "result.hits"


class ListingConfig(BaseModel):
    """How to reach and iterate through the exhibitor list."""

    strategy: str  # "single_page", "paged", "alpha_index", "api_endpoint"
    url_template: str
    meta_url: str | None = None
    meta_letters_path: str | None = None
    pagination: PaginationConfig | None = None
    item_container_selector: str | None = None  # CSS selector for HTML pages
    item_id_path: str | None = None  # JSON path for API responses
    item_filter: ItemFilter | None = None

    # Extra query parameters for API calls
    query_params: dict[str, str] = Field(default_factory=dict)


class DetailConfig(BaseModel):
    """How to fetch exhibitor detail pages."""

    url_template: str
    source_type: str = "api"  # "api" or "html"
    container_selector: str | None = None  # CSS selector for HTML detail pages


class SiteProfile(BaseModel):
    """Everything needed to re-scrape a trade fair site without AI."""

    profile_version: int = 1
    platform_id: str
    domain_patterns: list[str]
    source_type: str  # "api" or "html"
    requires_javascript: bool = False
    headers: dict[str, str] = Field(default_factory=dict)

    auth: AuthConfig | None = None
    listing: ListingConfig
    detail: DetailConfig | None = None
    field_map: dict[str, ExtractionRule]

    learned_at: datetime = Field(default_factory=datetime.now)
    last_used_at: datetime | None = None
    confidence: float = 0.0
    notes: str = ""
