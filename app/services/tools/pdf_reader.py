MAX_PAGES = 40


def extract_pdf(data: bytes) -> tuple[str, str]:
    """PDF bytes -> (title, text). Reads at most MAX_PAGES pages."""
    import pymupdf

    doc = pymupdf.open(stream=data, filetype="pdf")
    try:
        title = (doc.metadata or {}).get("title") or ""
        pages = [doc[i].get_text() for i in range(min(len(doc), MAX_PAGES))]
    finally:
        doc.close()
    text = "\n".join(pages)
    if not title:
        title = next((ln.strip() for ln in text.splitlines() if len(ln.strip()) > 10), "PDF document")[:120]
    return title, text
