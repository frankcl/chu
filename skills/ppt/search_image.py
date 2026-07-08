"""Image search for the ppt skill (Tavily-backed).

Usage: python search_image.py "<query>"
Prints JSON: {"query": ..., "images": [{"url", "description"}, ...]}

Returns candidate image URLs to drop into an image block's `src`. Pick a
relevant one, then add it to the deck spec as
`{"type": "image", "src": "<url>", "caption": "..."}`.

Requires the TAVILY_API_KEY environment variable (inherited from the agent
process). Network-dependent; degrades to an empty list on failure.
"""

import json
import sys


def _extract_images(raw) -> list:
    """Pull image entries from a Tavily response, tolerant of shape.

    Tavily returns `images` either as a list of URL strings, or — with
    `include_image_descriptions` — as a list of {"url", "description"} objects.
    """
    images = raw.get("images", []) if isinstance(raw, dict) else []
    out = []
    for img in images or []:
        if isinstance(img, str) and img.strip():
            out.append({"url": img.strip(), "description": ""})
        elif isinstance(img, dict):
            url = (img.get("url") or "").strip()
            if url:
                out.append({"url": url, "description": (img.get("description") or "")[:200]})
    return out


def main() -> None:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print('usage: search_image.py "<query>"', file=sys.stderr)
        sys.exit(1)
    query = sys.argv[1]

    try:
        from langchain_tavily import TavilySearch
    except Exception as e:  # pragma: no cover - import guard
        print(f"search unavailable: {e}", file=sys.stderr)
        sys.exit(1)

    raw = TavilySearch(
        max_results=5,
        include_images=True,
        include_image_descriptions=True,
    ).invoke({"query": query})
    images = _extract_images(raw)
    print(json.dumps({"query": query, "images": images}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
