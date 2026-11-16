"""Pure text helpers (no heavy imports, easy to reason about)."""
import json
import re
from urllib.parse import urlparse


def extract_json(raw: str) -> dict:
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I)
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found")
    return json.loads(s[start : end + 1])


def norm(s: str) -> str:
    s = re.sub("[\u201c\u201d\u2018\u2019\"'`]", "", s)
    s = re.sub("[\u2013\u2014]", "-", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def quote_in_text(quote: str, text: str) -> bool:
    """True if the quote really appears in the source text (anti-hallucination check)."""
    q = norm(quote)
    return len(q) >= 20 and q in norm(text)


def domain_of(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


_HIGH = ("arxiv.org", "nature.com", "science.org", "who.int", "nih.gov", "acm.org", "ieee.org",
         "springer.com", "sciencedirect.com", "openalex.org", "semanticscholar.org", "pubmed",
         "oecd.org", "worldbank.org", "un.org", "europa.eu")
_LOW = ("medium.com", "blogspot.", "quora.com", "reddit.com", "pinterest.", "facebook.com", "tiktok.com")


def credibility(url: str) -> float:
    d = domain_of(url)
    if d.endswith((".gov", ".edu")) or ".gov." in d or ".edu." in d or ".ac." in d:
        return 0.9
    if any(h in d for h in _HIGH):
        return 0.85
    if "wikipedia.org" in d:
        return 0.6
    if any(l in d for l in _LOW):
        return 0.3
    return 0.5


def chunk_text(text: str, size: int, overlap: int, max_chunks: int) -> list[str]:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    chunks: list[str] = []
    i = 0
    while i < len(text) and len(chunks) < max_chunks:
        end = min(i + size, len(text))
        if end < len(text):
            window = text[i:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("\n"))
            if cut > size * 0.5:
                end = i + cut + 1
        piece = text[i:end].strip()
        if len(piece) > 80:
            chunks.append(piece)
        if end >= len(text):
            break
        i = max(end - overlap, i + 1)
    return chunks


CITE_RE = re.compile(r"\[(\d{1,3}(?:\s*[,;]\s*\d{1,3})*)\]")


def finalize_citations(text: str, sources: list[dict]) -> tuple[str, list[dict]]:
    """Drop invalid [n] markers, renumber the used ones 1..k, return (text, used_sources)."""
    valid = {s["n"]: s for s in sources}
    order: list[int] = []
    for m in CITE_RE.finditer(text):
        for x in map(int, re.findall(r"\d+", m.group(1))):
            if x in valid and x not in order:
                order.append(x)
    if not order:
        order = [s["n"] for s in sources]
    newnum = {old: i + 1 for i, old in enumerate(order)}

    def repl(m):
        nums = [newnum[x] for x in map(int, re.findall(r"\d+", m.group(1))) if x in newnum]
        return "".join(f"[{n}]" for n in dict.fromkeys(nums))

    text = CITE_RE.sub(repl, text)
    refs = [{**valid[old], "n": newnum[old]} for old in order]
    return text, refs


def references_md(refs: list[dict]) -> str:
    return "\n".join(f"[{r['n']}] {r['title']} - {r['url']}" for r in refs)


def extract_title(md: str, fallback: str) -> str:
    for line in md.splitlines():
        if line.startswith("# "):
            return line[2:].strip()[:120] or fallback
    return fallback[:120]


def md_to_whatsapp(md: str) -> str:
    s = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", md, flags=re.M)
    s = re.sub(r"\*\*(.+?)\*\*", r"*\1*", s)
    s = re.sub(r"__(.+?)__", r"_\1_", s)
    return s.strip()


def _balance_markers(s: str) -> str:
    """Ensure formatting markers (*, _) are properly balanced so WhatsApp renders them cleanly."""
    for marker in ("*", "_", "~"):
        if s.count(marker) % 2 != 0:
            s += marker
    return s


def clean_whatsapp_text(text: str, max_chars: int = 1500) -> str:
    """Format markdown for WhatsApp and ensure clean truncation without cutting words or leaving unclosed asterisks."""
    s = md_to_whatsapp(text).strip()
    if len(s) <= max_chars:
        return _balance_markers(s)
    # Cut at the last clean sentence or paragraph boundary before max_chars
    cut = -1
    for sep in ("\n\n", ".\n", ". ", "\n"):
        idx = s.rfind(sep, 0, max_chars)
        if idx > max_chars * 0.5:
            cut = idx + len(sep)
            break
    if cut == -1:
        idx = s.rfind(" ", 0, max_chars)
        cut = idx if idx > 0 else max_chars
    trimmed = s[:cut].strip()
    return _balance_markers(trimmed)


def wrap_untrusted(i: int, url: str, text: str) -> str:
    """Mark web text as untrusted data and neutralise attempts to close the tag early."""
    safe_text = text.replace("</untrusted_source", "</ untrusted_source")
    safe_url = url.replace(chr(34), "%22")
    return f'<untrusted_source id="{i}" url="{safe_url}">\n{safe_text}\n</untrusted_source>'


def mask_phone(phone: str) -> str:
    return f"***{phone[-4:]}" if len(phone) > 4 else "***"
