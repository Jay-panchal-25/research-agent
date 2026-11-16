"""Preflight check. Run from the project root:  python -m scripts.doctor
Verifies .env values and that every external service answers, before you try WhatsApp."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402

OK, BAD = "[ OK ]", "[FAIL]"
failures = 0


def report(ok: bool, name: str, info: str = "") -> None:
    global failures
    failures += 0 if ok else 1
    print(f"{OK if ok else BAD} {name}" + (f" - {info}" if info else ""))


async def main() -> int:
    loop = asyncio.get_running_loop()
    orig_handler = loop.get_exception_handler()

    def _handler(loop, context):
        msg = context.get("message", "")
        exc = context.get("exception")
        if "SSL" in msg or (exc and "Event loop is closed" in str(exc)):
            return
        if orig_handler:
            orig_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)
    report(bool(settings.allowed_numbers_set), "ALLOWED_NUMBERS set", "empty = the bot ignores everyone")
    report(bool(settings.groq_api_key), "GROQ_API_KEY set")
    report(bool(settings.embedding_api_key), "EMBEDDING_API_KEY set")

    try:
        from arq import create_pool
        from arq.connections import RedisSettings
        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        await pool.ping()
        await pool.aclose()
        report(True, "Redis", settings.redis_url)
    except Exception as e:  # noqa: BLE001
        report(False, "Redis", f"{type(e).__name__}: {str(e)[:100]}  (run: docker compose up -d)")

    try:
        from app.store import mongo
        await mongo.get_db().command("ping")
        await mongo.close()
        report(True, "MongoDB", settings.mongodb_uri.split("@")[-1])
    except Exception as e:  # noqa: BLE001
        report(False, "MongoDB", f"{type(e).__name__}: {str(e)[:100]}")

    try:
        from app.store import qdrant
        await qdrant.client().get_collections()
        await qdrant.close()
        report(True, "Qdrant", settings.qdrant_url)
    except Exception as e:  # noqa: BLE001
        report(False, "Qdrant", f"{type(e).__name__}: {str(e)[:100]}")

    try:
        from app.services import llm
        for role in ("fast", "main", "strong"):
            out = await llm.complete(role, "Reply with the single word OK.", "ping", max_tokens=5)
            report(True, f"LLM role '{role}'", f"{getattr(settings, 'model_' + role)} -> {out.strip()[:20]!r}")
    except Exception as e:  # noqa: BLE001
        report(False, "LLM (Groq via LiteLLM)", f"{type(e).__name__}: {str(e)[:150]}")

    try:
        from app.services import embeddings
        dim = len((await embeddings.embed(["ping"]))[0])
        report(True, "Embedding API", f"{settings.embedding_model} -> dimension {dim}")
    except Exception as e:  # noqa: BLE001
        report(False, "Embedding API", f"{type(e).__name__}: {str(e)[:150]}")

    try:
        url = f"https://graph.facebook.com/{settings.wa_api_version}/{settings.wa_phone_number_id}"
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url, params={"fields": "display_phone_number"},
                            headers={"Authorization": f"Bearer {settings.wa_access_token}"})
        if r.status_code == 200:
            report(True, "WhatsApp token + phone number id", r.json().get("display_phone_number", ""))
        else:
            err = r.json().get("error", {}) if r.headers.get("content-type", "").startswith("application/json") else {}
            hint = "token expired/invalid: generate a new one" if err.get("code") == 190 else err.get("message", r.text[:100])
            report(False, "WhatsApp token + phone number id", f"HTTP {r.status_code}: {hint}")
    except Exception as e:  # noqa: BLE001
        report(False, "WhatsApp Graph API", f"{type(e).__name__}: {str(e)[:100]}")

    # small sleep to allow open SSL transports to cleanly shut down on Windows
    await asyncio.sleep(0.25)
    print("\nAll checks passed." if not failures else f"\n{failures} check(s) failed. Fix them before testing on WhatsApp.")
    return 1 if failures else 0


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    code = asyncio.run(main())
    sys.exit(code)
