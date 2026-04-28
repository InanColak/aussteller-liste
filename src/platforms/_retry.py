from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

RETRY_ATTEMPTS = 8


def smart_retry_wait(retry_state) -> float:
    """Tenacity wait function that respects HTTP 429 Retry-After.

    On 429: use Retry-After header (capped 60s) if numeric, else
    exponential backoff starting 5s, capped 60s.
    On other transient errors: short exponential backoff (1-10s).
    """
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    attempt = retry_state.attempt_number

    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
        retry_after = exc.response.headers.get("Retry-After", "")
        if retry_after.isdigit():
            wait = min(int(retry_after), 60)
        else:
            wait = min(60, 5 * 2 ** (attempt - 1))
        logger.warning(
            "HTTP 429 — sleeping %ds (attempt %d/%d)",
            wait, attempt, RETRY_ATTEMPTS,
        )
        return wait

    return min(10, 2 ** (attempt - 1))
