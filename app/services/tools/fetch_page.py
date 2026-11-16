import asyncio
import logging
import socket
from urllib.parse import urljoin

import httpx

from app.errors import FetchError
from app.services.cache import TTLCache
from app.services.tools.pdf_reader import extract_pdf
from app.services.tools.safety import check_url_shape, is_public_ip

log = logging.getLogger(__name__)
MAX_BYTES = 3_000_000
MAX_REDIRECTS = 3
_cache = TTLCache(ttl=3600, max_items=60)


async def assert_public(url: str) -> None:
    """SSRF guard: every hop must resolve only to public IPs."""
    try:
        host, port = check_url_shape(url)
    except ValueError as e:
        raise FetchError(str(e)) from e
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise FetchError("DNS lookup failed") from e
    if not infos or not all(is_public_ip(i[4][0]) for i in infos):
        raise FetchError("blocked: address is not public")


def _extract_html(html: str) -> tuple[str, str]:
    import trafilatura

    text = trafilatura.extract(html, include_comments=False, include_tables=True, favor_recall=True) or ""
    title = ""
    try:
        meta = trafilatura.extract_metadata(html)
        title = (meta.title if meta else "") or ""
    except Exception:  # noqa: BLE001
        pass
    return title, text


async def fetch(url: str) -> dict:
    """Download a page/PDF safely and return {url, title, text}. Raises FetchError."""
    cached = _cache.get(url)
    if cached:
        return cached
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        async with httpx.AsyncClient(follow_redirects=False, timeout=httpx.Timeout(15.0), headers=headers) as client:
            current = url
            for _ in range(MAX_REDIRECTS + 1):
                await assert_public(current)
                async with client.stream("GET", current) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        current = urljoin(current, r.headers["location"])
                        continue
                    if r.status_code >= 400:
                        raise FetchError(f"HTTP {r.status_code}")
                    ctype = r.headers.get("content-type", "").lower()
                    data = bytearray()
                    async for part in r.aiter_bytes():
                        data.extend(part)
                        if len(data) > MAX_BYTES:
                            break
                break
            else:
                raise FetchError("too many redirects")
    except httpx.HTTPError as e:
        raise FetchError(f"{type(e).__name__}: {str(e)[:100]}") from e

    body = bytes(data)
    is_pdf = "pdf" in ctype or current.lower().split("?")[0].endswith(".pdf") or body[:5] == b"%PDF-"
    try:
        if is_pdf:
            title, text = await asyncio.to_thread(extract_pdf, body)
        else:
            title, text = await asyncio.to_thread(_extract_html, body.decode("utf-8", errors="ignore"))
    except Exception as e:  # noqa: BLE001
        raise FetchError(f"parse error: {str(e)[:100]}") from e
    text = text.strip()
    if len(text) < 200:
        raise FetchError("no readable text")
    result = {"url": url, "title": title.strip() or url, "text": text}
    _cache.set(url, result)
    return result
