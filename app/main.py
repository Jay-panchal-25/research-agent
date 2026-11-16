import asyncio
import logging
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from arq.worker import create_worker
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.errors import AppError, classify
from app.logging_config import setup_logging
from app.routers import health, webhook
from app.database import mongo
from app import worker as worker_module

setup_logging(settings.log_level)
log = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        app.state.arq = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    except Exception:
        log.critical("Cannot connect to Redis at %s. Is `docker compose up -d` running?", settings.redis_url)
        raise
    try:
        await mongo.init_indexes()
    except Exception:
        log.critical("Cannot connect to MongoDB at %s. Is `docker compose up -d` running?", settings.mongodb_uri)
        raise

    # Run the arq worker loop in-process (same event loop as uvicorn) instead of a
    # separate `arq app.worker.WorkerSettings` process. Same queue, same job function,
    # same retry/timeout behavior as app/worker.py — just hosted here for one-command startup.
    app.state.arq_worker = create_worker(worker_module.WorkerSettings, handle_signals=False)
    app.state.arq_worker_task = asyncio.create_task(app.state.arq_worker.async_run())
    log.info("In-process arq worker started")

    log.info("API started")
    yield

    app.state.arq_worker_task.cancel()
    try:
        await app.state.arq_worker_task
    except asyncio.CancelledError:
        pass
    await app.state.arq_worker.close()

    await app.state.arq.aclose()
    await mongo.close()
    log.info("API stopped")


app = FastAPI(title="Research Agent", lifespan=lifespan)
app.include_router(health.router)
app.include_router(webhook.router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    err = classify(exc)
    log.error("Unhandled error on %s %s [%s]: %s", request.method, request.url.path, err.code, err.detail,
              exc_info=exc)
    return JSONResponse(status_code=500, content={"error": {"code": err.code, "message": err.user_message}})


@app.exception_handler(AppError)
async def app_error(request: Request, exc: AppError):
    log.error("%s on %s %s: %s", exc.code, request.method, request.url.path, exc.detail)
    return JSONResponse(status_code=500, content={"error": {"code": exc.code, "message": exc.user_message}})