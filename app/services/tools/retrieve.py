import logging

from app.config import settings
from app.database import qdrant
from app.services import embeddings
from app.services.text import chunk_text

log = logging.getLogger(__name__)


async def index_page(report_id: str, user_id: str, url: str, title: str, text: str) -> int:
    """Chunk -> embed (API) -> store in Qdrant. Returns number of chunks stored."""
    chunks = chunk_text(text, settings.chunk_size, settings.chunk_overlap, settings.max_chunks_per_page)
    if not chunks:
        return 0
    vectors = await embeddings.embed(chunks)
    await qdrant.upsert_chunks(report_id, user_id, url, title, chunks, vectors)
    return len(chunks)


async def retrieve(report_id: str, query: str, k: int | None = None) -> list[dict]:
    """Semantic search over everything indexed for one report."""
    vec = (await embeddings.embed([query]))[0]
    return await qdrant.search(report_id, vec, k or settings.retrieve_k)
