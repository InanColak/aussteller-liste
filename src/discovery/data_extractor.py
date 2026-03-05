from __future__ import annotations

import json
import re

from openai import AsyncOpenAI

from src.config import OPENAI_API_KEY, OPENAI_MODEL
from src.models import Exhibitor

SYSTEM_PROMPT = """\
You are an expert at extracting exhibitor data from trade fair HTML pages.
Given HTML content, extract all exhibitor/company entries you can find.
Return a JSON array of objects with these fields (use null if not found):
- company_name (required)
- website
- hall
- stand
- country
- city
- categories (array of strings)
- description
- phone
- email
- address

Also check if there is a "next page" or pagination link. If yes, include it as
a separate field "next_page_url" in a wrapper object.

Return format:
{
  "exhibitors": [...],
  "next_page_url": "https://..." or null
}
Only return valid JSON, nothing else.
"""

MAX_HTML_CHARS = 60_000


def _clean_html(html: str) -> str:
    """Strip scripts, styles, and excess whitespace to reduce token count."""
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL)
    html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)
    html = re.sub(r"\s+", " ", html)
    return html[:MAX_HTML_CHARS]


async def extract_exhibitors(
    html: str, page_url: str
) -> tuple[list[Exhibitor], str | None]:
    """Use GPT-4o-mini to extract exhibitor data from HTML.

    Returns:
        Tuple of (exhibitors, next_page_url or None)
    """
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is required for AI-powered discovery. "
            "Set it in your .env file."
        )

    cleaned = _clean_html(html)

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Page URL: {page_url}\n\nHTML content:\n{cleaned}",
            },
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
    except json.JSONDecodeError:
        return [], None

    exhibitors = []
    raw_list = data.get("exhibitors", [])
    for item in raw_list:
        if not item.get("company_name"):
            continue
        item.setdefault("categories", [])
        try:
            exhibitors.append(Exhibitor(**item))
        except Exception:
            # Skip malformed entries
            continue

    next_url = data.get("next_page_url")
    return exhibitors, next_url
