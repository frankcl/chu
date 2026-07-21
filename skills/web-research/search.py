"""Web search for the web-research skill (Tavily-backed).

Usage: python search.py "<query>" ["<query2>" ...]

Accepts one OR MANY queries and runs them CONCURRENTLY (one process, one tool
call). Prints JSON:
    {"searches": [{"query": ..., "results": [{title, url, favicon, content}]}, ...]}

Guards:
- Queries are de-duplicated (order preserved).
- At most MAX_QUERIES distinct queries per call — extras are dropped and noted.
  This keeps a single call bounded; combined with the skill's per-turn call cap
  it stops runaway searching.

Requires the TAVILY_API_KEY environment variable (inherited from the agent process).
"""

import json
import sys
from concurrent.futures import ThreadPoolExecutor

# 单次调用的硬上限：一次最多搜这么多个不同 query（多余的丢弃并在输出里说明）。
MAX_QUERIES = 5


def _search_one(query: str) -> dict:
    """搜一个 query，返回 {"query", "results"}；单个失败不拖垮整批，记为 error。"""
    try:
        from langchain_tavily import TavilySearch

        raw = TavilySearch(max_results=5, include_favicon=True).invoke({"query": query})
        results = raw.get("results", []) if isinstance(raw, dict) else raw
        out = []
        for r in results or []:
            if isinstance(r, dict):
                out.append({
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "favicon": r.get("favicon"),
                    "content": (r.get("content") or "")[:500],
                })
        return {"query": query, "results": out}
    except Exception as e:  # noqa: BLE001 — 单 query 失败降级为 error 字段
        return {"query": query, "results": [], "error": str(e)}


def main() -> None:
    # 去重（保序）后再截断到 MAX_QUERIES。
    seen: set[str] = set()
    queries: list[str] = []
    for raw_arg in sys.argv[1:]:
        q = raw_arg.strip()
        if q and q not in seen:
            seen.add(q)
            queries.append(q)
    if not queries:
        print('usage: search.py "<query>" ["<query2>" ...]', file=sys.stderr)
        sys.exit(1)

    dropped = max(0, len(queries) - MAX_QUERIES)
    queries = queries[:MAX_QUERIES]

    try:
        import langchain_tavily  # noqa: F401 — 提前探测依赖，缺失则整体失败
    except Exception as e:  # pragma: no cover - import guard
        print(f"search unavailable: {e}", file=sys.stderr)
        sys.exit(1)

    # Tavily 调用是 IO 密集，用线程池并发；worker 数 = query 数（已 ≤ MAX_QUERIES）。
    with ThreadPoolExecutor(max_workers=len(queries)) as ex:
        searches = list(ex.map(_search_one, queries))

    payload: dict = {"searches": searches}
    if dropped:
        payload["note"] = f"{dropped} extra queries ignored (cap {MAX_QUERIES} per call)"
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
