import logging

from arq.connections import RedisSettings

from app.config import settings
from app.controllers.research_controller import handle_research_job
from app.database import mongo, qdrant
from app.logging_config import setup_logging

setup_logging(settings.log_level)
log = logging.getLogger("app.worker")


async def startup(ctx):
    setup_logging(settings.log_level)  # ARQ's CLI reconfigures logging after importing this module
    settings.require_worker_keys()
    await mongo.init_indexes()
    log.info(
        "Worker ready (models: fast=%s main=%s strong=%s | embeddings=%s)",
        settings.model_fast,
        settings.model_main,
        settings.model_strong,
        settings.embedding_model,
    )


async def shutdown(ctx):
    for name, closer in (("qdrant", qdrant.close), ("mongo", mongo.close)):
        try:
            await closer()
        except Exception:  # noqa: BLE001
            log.exception("Error while closing %s", name)
    log.info("Worker stopped")


async def run_research(ctx, msg: dict):
    # handle_research_job logs and reports its own errors to the user, so it does not raise
    await handle_research_job(msg)


class WorkerSettings:
    functions = [run_research]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 5                                    # concurrent research jobs
    job_timeout = settings.budget_max_seconds + 420  # always above the in-job timeout
    max_tries = 2                                   # a crashed job is retried
    keep_result = 60
