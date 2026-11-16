import asyncio
import logging
import uuid

from qdrant_client import AsyncQdrantClient, models

from app.config import settings
from app.errors import AppError, ConfigError, StoreError
from app.services import embeddings

log = logging.getLogger(__name__)
_client: AsyncQdrantClient | None = None
_ready = False
_lock = asyncio.Lock()


def client() -> AsyncQdrantClient:
    global _client
    if _client is None:
        _client = AsyncQdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
    return _client


async def ensure_collection() -> None:
    """Create the collection on first use. Vector size is detected from the embedding API."""
    global _ready
    if _ready:
        return
    async with _lock:
        if _ready:
            return
        try:
            c, name = client(), settings.qdrant_collection
            dim = len((await embeddings.embed(["dimension probe"]))[0])
            if not await c.collection_exists(name):
                await c.create_collection(
                    collection_name=name,
                    vectors_config=models.VectorParams(size=dim, distance=models.Distance.COSINE),
                )
                log.info("Created Qdrant collection %s (dim=%s)", name, dim)
            else:
                vectors = (await c.get_collection(name)).config.params.vectors
                existing = getattr(vectors, "size", None)
                if existing is not None and existing != dim:
                    raise ConfigError(
                        f"Qdrant collection '{name}' has dimension {existing} but model "
                        f"'{settings.embedding_model}' returns {dim}. Delete the collection or set "
                        f"QDRANT_COLLECTION to a new name.")
            for field in ("report_id", "user_id"):
                try:
                    await c.create_payload_index(
                        collection_name=name, field_name=field, field_schema=models.PayloadSchemaType.KEYWORD
                    )
                except Exception as e:  # noqa: BLE001
                    log.debug("payload index %s not created: %s", field, str(e)[:100])
        except AppError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("Qdrant setup failed: %s", str(e)[:200])
            raise StoreError(f"qdrant setup failed: {str(e)[:150]}", code="E_VECTOR") from e
        _ready = True


async def upsert_chunks(report_id: str, user_id: str, url: str, title: str,
                        chunks: list[str], vectors: list[list[float]]) -> None:
    await ensure_collection()
    points = [
        models.PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{report_id}|{url}|{i}")),
            vector=vec,
            payload={"report_id": report_id, "user_id": user_id, "url": url, "title": title,
                     "idx": i, "text": text},
        )
        for i, (text, vec) in enumerate(zip(chunks, vectors))
    ]
    try:
        await client().upsert(collection_name=settings.qdrant_collection, points=points)
    except Exception as e:  # noqa: BLE001
        log.error("Qdrant upsert failed: %s", str(e)[:200])
        raise StoreError(f"qdrant upsert failed: {str(e)[:150]}", code="E_VECTOR") from e


async def search(report_id: str, vector: list[float], limit: int) -> list[dict]:
    await ensure_collection()
    flt = models.Filter(must=[models.FieldCondition(key="report_id", match=models.MatchValue(value=report_id))])
    try:
        res = await client().query_points(
            collection_name=settings.qdrant_collection,
            query=vector,
            query_filter=flt,
            limit=limit,
            with_payload=True,
        )
    except Exception as e:  # noqa: BLE001
        log.error("Qdrant search failed: %s", str(e)[:200])
        raise StoreError(f"qdrant search failed: {str(e)[:150]}", code="E_VECTOR") from e
    return [{**(p.payload or {}), "score": p.score} for p in res.points]


async def close() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
