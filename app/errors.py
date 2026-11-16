"""Application errors.

Every AppError has:
  code          short code shown to the user as "Reference: <code>-<job id>" and written to the logs
  detail        technical text for the logs (never sent to the user)
  user_message  safe sentence that can be sent to the WhatsApp user
"""
import asyncio


class AppError(Exception):
    code = "E_INTERNAL"
    user_message = "Something went wrong on my side."

    def __init__(self, detail: str = "", *, user_message: str | None = None, code: str | None = None):
        super().__init__(detail or self.code)
        self.detail = detail or self.code
        if user_message:
            self.user_message = user_message
        if code:
            self.code = code  # instance override, e.g. StoreError(code="E_VECTOR")


class ConfigError(AppError):
    code = "E_CONFIG"
    user_message = "The service is not configured correctly. The administrator needs to check the settings."


class LLMError(AppError):
    code = "E_LLM"
    user_message = "The AI model is busy or its free quota is used up. Please try again in a few minutes."


class EmbeddingError(AppError):
    code = "E_EMBED"
    user_message = "The embedding service is busy or its free quota is used up. Please try again in a few minutes."


class StoreError(AppError):
    """A database/queue failure. Use code E_DB (MongoDB), E_QUEUE (Redis) or E_VECTOR (Qdrant)."""
    code = "E_DB"
    user_message = "My database is temporarily unavailable. Please try again in a few minutes."


class FetchError(AppError):
    code = "E_FETCH"
    user_message = "I couldn't open that web page."


class ReportError(AppError):
    code = "E_REPORT"
    user_message = "I couldn't build the PDF report."


class JobTimeoutError(AppError):
    code = "E_TIMEOUT"
    user_message = "That took longer than my time limit. Try a narrower question."


class WhatsAppError(AppError):
    code = "E_WA"

    def __init__(self, detail: str = "", *, status: int | None = None, wa_code: int | None = None,
                 user_message: str | None = None):
        super().__init__(detail, user_message=user_message)
        self.status = status
        self.wa_code = wa_code

    @property
    def is_token_error(self) -> bool:
        """Meta error 190 / HTTP 401 means the access token is expired or invalid."""
        return self.wa_code == 190 or self.status == 401


def classify(exc: BaseException) -> AppError:
    """Map any exception to an AppError so callers can log `code`/`detail` and message the user safely."""
    if isinstance(exc, AppError):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return JobTimeoutError(f"{type(exc).__name__}: {exc}")
    try:
        from pymongo.errors import PyMongoError
        if isinstance(exc, PyMongoError):
            return StoreError(f"MongoDB: {type(exc).__name__}: {str(exc)[:200]}", code="E_DB")
    except ImportError:  # pragma: no cover
        pass
    try:
        from redis.exceptions import RedisError
        if isinstance(exc, RedisError):
            return StoreError(f"Redis: {type(exc).__name__}: {str(exc)[:200]}", code="E_QUEUE")
    except ImportError:  # pragma: no cover
        pass
    return AppError(f"{type(exc).__name__}: {str(exc)[:300]}")
