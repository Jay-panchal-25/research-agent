import asyncio
import json
import logging
import re

import litellm
from pydantic import BaseModel, ValidationError

from app.services.budget import get_budget
from app.config import settings
from app.errors import LLMError
from app.services.text import extract_json

log = logging.getLogger(__name__)
litellm.suppress_debug_info = True
litellm.drop_params = True

_sem = asyncio.Semaphore(settings.llm_concurrency)
_NO_RETRY = tuple(getattr(litellm, n) for n in
                  ("BadRequestError", "AuthenticationError", "NotFoundError", "PermissionDeniedError")
                  if hasattr(litellm, n))


def _roles() -> dict[str, str]:
    return {"fast": settings.model_fast, "main": settings.model_main, "strong": settings.model_strong}


def _retry_after(msg: str) -> float | None:
    m = re.search(r"try again in (?:(\d+)m)?(\d+(?:\.\d+)?)(ms|s)", msg)
    if not m:
        return None
    secs = float(m.group(2)) / (1000 if m.group(3) == "ms" else 1)
    return secs + (int(m.group(1)) * 60 if m.group(1) else 0)


def _track(job_id: str | None, resp) -> None:
    if not job_id:
        return
    b = get_budget(job_id)
    b.llm_calls += 1
    usage = getattr(resp, "usage", None)
    b.tokens += int(getattr(usage, "total_tokens", 0) or 0)


def _is_json_validation_error(e: Exception) -> bool:
    msg = str(e).lower()
    return "json_validate_failed" in msg or "failed to generate json" in msg or "failed to validate json" in msg


async def complete(
    role: str,
    system: str,
    user: str,
    *,
    json_mode: bool = False,
    json_schema: type[BaseModel] | None = None,
    max_tokens: int = 1200,
    temperature: float = 0.2,
    job_id: str | None = None,
) -> str:
    """One chat completion with retries, rate-limit backoff and model fallback."""
    fallback_defaults = ["groq/llama-3.3-70b-versatile", "groq/llama-3.1-8b-instant"] if settings.groq_api_key else []
    models = list(dict.fromkeys([_roles()[role], settings.model_fallback] + fallback_defaults))
    last_err: Exception | None = None
    for model in models:
        for attempt in range(settings.llm_retries):
            kwargs = dict(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=90,
            )
            if model.startswith("groq/"):
                kwargs["api_key"] = settings.groq_api_key
            if json_schema is not None:
                # Groq's gpt-oss models have browser_search built into their chat template,
                # which requires json_schema (Structured Outputs) mode.
                # Other models (e.g. Qwen, Llama) fail with json_schema due to Groq grammar bugs,
                # but work reliably with json_object mode.
                if "gpt-oss" in model.lower():
                    kwargs["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {
                            "name": json_schema.__name__,
                            "strict": False,
                            "schema": json_schema.model_json_schema(),
                        },
                    }
                else:
                    kwargs["response_format"] = {"type": "json_object"}
            elif json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                async with _sem:
                    resp = await litellm.acompletion(**kwargs)
                _track(job_id, resp)
                return resp.choices[0].message.content or ""
            except litellm.RateLimitError as e:
                last_err = e
                wait = _retry_after(str(e)) or 2 ** (attempt + 1)
                log.warning("Rate limited on %s (wait %.1fs)", model, wait)
                if wait > 45 or attempt == settings.llm_retries - 1:
                    break  # try the fallback model if wait is too long or retries exhausted
                await asyncio.sleep(wait + 0.5)
            except Exception as e:  # noqa: BLE001
                last_err = e
                log.warning("LLM error on %s: %s", model, str(e)[:200])
                if _is_json_validation_error(e):
                    # json_validate_failed is a model generation failure, not an invalid API key
                    if kwargs.get("response_format", {}).get("type") == "json_schema":
                        log.info("Retrying with json_object mode for %s", model)
                        kwargs["response_format"] = {"type": "json_object"}
                    await asyncio.sleep(1)
                    continue
                if _NO_RETRY and isinstance(e, _NO_RETRY):
                    log.error("Non-retryable LLM error on %s (check the API key and model name): %s",
                              model, str(e)[:200])
                    break
                await asyncio.sleep(2**attempt)
    raise LLMError(f"All models failed: {str(last_err)[:200]}")


async def complete_json(
    role: str,
    system: str,
    user: str,
    schema: type[BaseModel],
    *,
    job_id: str | None = None,
    max_tokens: int = 1200,
) -> BaseModel:
    """Ask for JSON, validate with Pydantic, retry once with the error message."""
    err = ""
    sys_prompt = system
    if "json" not in sys_prompt.lower():
        sys_prompt = f"{sys_prompt}\n\nReturn a valid JSON object matching the requested schema."
    for _ in range(2):
        prompt = user if not err else f"{user}\n\nYour previous reply was invalid ({err}). Reply with valid JSON only."
        raw = await complete(role, sys_prompt, prompt, json_schema=schema, max_tokens=max_tokens,
                             temperature=0.0, job_id=job_id)
        try:
            return schema.model_validate(extract_json(raw))
        except (ValueError, ValidationError, json.JSONDecodeError) as e:
            err = str(e)[:150]
    raise LLMError(f"Invalid JSON from model: {err}")