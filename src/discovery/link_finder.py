from __future__ import annotations

import json

from openai import AsyncOpenAI

from src.config import OPENAI_API_KEY, OPENAI_MODEL

SYSTEM_PROMPT = """\
You are an expert at identifying exhibitor/aussteller list pages on trade fair websites.
Given a list of links from a webpage, identify the link(s) that lead to the exhibitor list.
Return a JSON array of the most likely exhibitor list URLs (max 3).
Only return the JSON array, nothing else. Example: ["https://example.com/exhibitors"]
If no exhibitor list link is found, return an empty array: []
"""


async def find_exhibitor_links(
    page_url: str, links: list[dict[str, str]]
) -> list[str]:
    """Use GPT-4o-mini to identify exhibitor list links from page links."""
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is required for AI-powered discovery. "
            "Set it in your .env file."
        )

    # Truncate link list to fit in context
    link_text = "\n".join(
        f"- {l['text']}: {l['href']}" for l in links[:200]
    )

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Page URL: {page_url}\n\nLinks found on the page:\n{link_text}",
            },
        ],
        temperature=0,
        max_tokens=500,
    )

    content = response.choices[0].message.content or "[]"
    # Strip markdown fences if present
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        result = json.loads(content)
        return result if isinstance(result, list) else []
    except json.JSONDecodeError:
        return []
