import contextvars
import logging
import sys

# Set per webhook request / per job so every log line can be traced back to one message.
job_ctx: contextvars.ContextVar[str] = contextvars.ContextVar("job_id", default="-")

_NOISY = ("httpx", "httpcore", "LiteLLM", "litellm", "LiteLLM Router", "LiteLLM Proxy", "pymongo",
          "qdrant_client", "urllib3", "asyncio", "trafilatura", "htmldate", "courlan", "primp",
          "ddgs", "fontTools", "PIL", "hpack", "charset_normalizer")
_ARQ = ("arq", "arq.worker", "arq.connections", "arq.jobs")


def mask(phone: str) -> str:
    """Never write full phone numbers to logs."""
    return f"***{phone[-4:]}" if len(phone) > 4 else "***"


class _ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.job = job_ctx.get()
        return True


def setup_logging(level: str | int = "INFO") -> None:
    """Idempotent. The ARQ CLI reconfigures logging after importing the worker module,
    so the worker calls this again in its startup hook."""
    if isinstance(level, str):
        resolved = logging.getLevelName(level.strip().upper())
        level = resolved if isinstance(resolved, int) else logging.INFO
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_ContextFilter())
    fmt = "%(asctime)s | %(levelname)-5s | %(filename)s:%(lineno)-3d | [%(job)s] %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    handler.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in _ARQ:  # drop ARQ's own handlers so its lines are not printed twice
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    for name in _NOISY:
        logging.getLogger(name).setLevel(logging.WARNING)
