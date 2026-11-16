import asyncio
import logging

import litellm

from app.config import settings
from app.errors import EmbeddingError
from app.errors import EmbeddingError

log = logging.getLogger(__name__)
_sem = asyncio.Semaphore(1)  # gate embedding calls sequentially to avoid Gemini free-tier 15 RPM limits


async def embed(texts: list[str]) -> list[list[float]]:
    """Embed texts via the embedding API (batched, backoff on rate limits).

    Raises EmbeddingError when the API keeps failing.
    """
    out: list[list[float]] = []
    for i in range(0, len(texts), settings.embedding_batch):
        batch = texts[i : i + settings.embedding_batch]
        last: Exception | None = None
        for attempt in range(5):
            try:
                async with _sem:
                    resp = await litellm.aembedding(
                        model=settings.embedding_model,
                        input=batch,
                        api_key=settings.embedding_api_key,
                        timeout=60,
                    )
                    await asyncio.sleep(0.5)  # brief pause between batches to stay under RPM
                vectors = [d["embedding"] if isinstance(d, dict) else d.embedding for d in resp.data]
                if len(vectors) != len(batch):
                    raise EmbeddingError(f"API returned {len(vectors)} vectors for {len(batch)} inputs")
                out.extend(vectors)
                break
            except EmbeddingError:
                raise
            except litellm.RateLimitError as e:
                last = e
                # Gemini free tier resets on a 60s window; back off sufficiently to let the window reset
                wait = 20 * (attempt + 1)
                log.warning("Embedding API rate limited, waiting %ss (attempt %d)", wait, attempt + 1)
                await asyncio.sleep(wait)
            except Exception as e:  # noqa: BLE001
                last = e
                log.warning("Embedding API error (attempt %d): %s", attempt + 1, str(e)[:200])
                if isinstance(e, getattr(litellm, "BadRequestError", ())) or isinstance(
                    e, getattr(litellm, "AuthenticationError", ())
                ):
                    break  # retrying cannot fix a bad request or a bad key
                await asyncio.sleep(2**attempt)
        else:
            raise EmbeddingError(f"embedding API failed after retries: {str(last)[:200]}")
        if len(out) < i + len(batch):  # broke out of the loop on a non-retryable error
            raise EmbeddingError(f"embedding API rejected the request: {str(last)[:200]}")
    return out
