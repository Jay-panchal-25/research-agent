import asyncio
import logging

from app.services.cache import TTLCache

log = logging.getLogger(__name__)
_cache = TTLCache(ttl=3600)
_sem = asyncio.Semaphore(1)  # serialize searches to avoid triggering DuckDuckGo / backend rate limits


async def web_search(query: str, max_results: int = 8) -> list[dict]:
    """DuckDuckGo search. Returns [{title, url, snippet}]. Never raises."""
    key = query.strip().lower()
    cached = _cache.get(key)
    if cached is not None:
        return cached

    def _run(q: str):
        from ddgs import DDGS
        return list(DDGS(timeout=10).text(q, max_results=max_results))

    search_q = query
    words = query.strip().split()
    if len(words) > 6:
        search_q = " ".join(words[:6])

    rows = []
    queries_to_try = [search_q]
    if len(words) > 4 and search_q != " ".join(words[:4]):
        queries_to_try.append(" ".join(words[:4]))

    async with _sem:
        for attempt, q in enumerate(queries_to_try):
            try:
                rows = await asyncio.to_thread(_run, q)
                await asyncio.sleep(0.4)
                if rows:
                    break
            except Exception as e:  # noqa: BLE001
                if attempt < len(queries_to_try) - 1:
                    await asyncio.sleep(0.5)
                else:
                    log.warning("web_search failed for %r: %s", q, str(e)[:150])
    results = [
        {"title": r.get("title", "") or r.get("href", ""), "url": r["href"], "snippet": (r.get("body") or "")[:300]}
        for r in rows if r.get("href")
    ]
    _cache.set(key, results)
    return results
