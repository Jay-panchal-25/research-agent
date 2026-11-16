import asyncio
import logging
import re
import xml.etree.ElementTree as ET

import httpx

from app.config import settings

log = logging.getLogger(__name__)
HEADERS = {"User-Agent": "ResearchAgent/1.0 (academic search)"}


async def _arxiv(c: httpx.AsyncClient, q: str, limit: int) -> list[dict]:
    terms = " AND ".join(f"all:{w}" for w in re.findall(r"\w+", q)[:8])
    r = await c.get("https://export.arxiv.org/api/query",
                    params={"search_query": terms, "max_results": limit, "sortBy": "relevance"})
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in ET.fromstring(r.content).findall("a:entry", ns):
        title = " ".join((e.findtext("a:title", "", ns)).split())
        abstract = " ".join((e.findtext("a:summary", "", ns)).split())
        page = e.findtext("a:id", "", ns)
        pdf = next((l.get("href") for l in e.findall("a:link", ns) if l.get("title") == "pdf"), None)
        out.append({"title": title, "url": pdf or page, "snippet": f"[arXiv] {abstract[:250]}", "abstract": abstract})
    return out


async def _semantic_scholar(c: httpx.AsyncClient, q: str, limit: int) -> list[dict]:
    r = await c.get("https://api.semanticscholar.org/graph/v1/paper/search",
                    params={"query": q, "limit": limit, "fields": "title,abstract,url,year,openAccessPdf"})
    if r.status_code == 429:
        return []
    r.raise_for_status()
    out = []
    for p in r.json().get("data", []):
        url = (p.get("openAccessPdf") or {}).get("url") or p.get("url")
        if not url or not p.get("title"):
            continue
        abstract = p.get("abstract") or ""
        out.append({"title": p["title"], "url": url,
                    "snippet": f"[SemanticScholar {p.get('year')}] {abstract[:250]}", "abstract": abstract})
    return out


def _rebuild_abstract(inv: dict | None) -> str:
    if not inv:
        return ""
    pos = {i: w for w, idxs in inv.items() for i in idxs}
    return " ".join(pos[i] for i in sorted(pos))


async def _openalex(c: httpx.AsyncClient, q: str, limit: int) -> list[dict]:
    params = {"search": q, "per-page": limit,
              "select": "id,title,publication_year,abstract_inverted_index,open_access,doi"}
    if settings.openalex_email:
        params["mailto"] = settings.openalex_email
    r = await c.get("https://api.openalex.org/works", params=params)
    r.raise_for_status()
    out = []
    for w in r.json().get("results", []):
        url = (w.get("open_access") or {}).get("oa_url") or w.get("doi") or w.get("id")
        if not url or not w.get("title"):
            continue
        abstract = _rebuild_abstract(w.get("abstract_inverted_index"))
        out.append({"title": w["title"], "url": url,
                    "snippet": f"[OpenAlex {w.get('publication_year')}] {abstract[:250]}", "abstract": abstract})
    return out


async def academic_search(query: str, limit: int = 4) -> list[dict]:
    """arXiv + Semantic Scholar + OpenAlex in parallel. Failures of one source are ignored."""
    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as c:
        results = await asyncio.gather(
            _arxiv(c, query, limit), _semantic_scholar(c, query, limit), _openalex(c, query, limit),
            return_exceptions=True,
        )
    merged, seen = [], set()
    for res in results:
        if isinstance(res, Exception):
            log.warning("academic source failed: %s", str(res)[:120])
            continue
        for item in res:
            key = re.sub(r"\W+", "", item["title"].lower())
            if key and key not in seen:
                seen.add(key)
                merged.append(item)
    return merged
