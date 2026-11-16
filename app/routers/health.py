import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.store import mongo

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(request: Request):
    checks: dict[str, str] = {}
    try:
        await request.app.state.arq.ping()
        checks["redis"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["redis"] = f"error: {type(e).__name__}"
        log.error("Redis health check failed: %s", e)
    try:
        await mongo.get_db().command("ping")
        checks["mongodb"] = "ok"
    except Exception as e:  # noqa: BLE001
        checks["mongodb"] = f"error: {type(e).__name__}"
        log.error("MongoDB health check failed: %s", e)
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(status_code=200 if ok else 503, content={"status": "ok" if ok else "degraded", **checks})
