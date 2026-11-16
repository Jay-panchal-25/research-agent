import asyncio
import logging

import httpx

from app.config import settings
from app.errors import WhatsAppError
from app.logging_config import mask

log = logging.getLogger(__name__)
MAX_LEN = 4000  # WhatsApp text limit is 4096


def _base() -> str:
    return f"https://graph.facebook.com/{settings.wa_api_version}/{settings.wa_phone_number_id}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {settings.wa_access_token}"}


def chunk_text(text: str, limit: int = MAX_LEN) -> list[str]:
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


def _to_error(r: httpx.Response) -> WhatsAppError:
    wa_code, message = None, r.text[:300]
    try:
        err = r.json().get("error", {})
        wa_code, message = err.get("code"), err.get("message", message)
    except ValueError:
        pass
    e = WhatsAppError(f"HTTP {r.status_code} code={wa_code}: {message}", status=r.status_code, wa_code=wa_code)
    if e.is_token_error:
        log.error("WhatsApp access token is invalid or expired. Generate a new one in Meta > WhatsApp > API Setup "
                  "and update WA_ACCESS_TOKEN in .env, then restart the API and worker.")
    return e


async def _post(path: str, *, json: dict | None = None, data: dict | None = None,
                files: dict | None = None) -> dict:
    """POST to the Graph API. Retries network errors, 429 and 5xx; raises WhatsAppError otherwise."""
    url = f"{_base()}/{path}"
    last: WhatsAppError | None = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(url, headers=_headers(), json=json, data=data, files=files)
        except httpx.HTTPError as e:
            last = WhatsAppError(f"network error: {type(e).__name__}: {str(e)[:150]}")
            log.warning("WhatsApp request failed (attempt %d/3): %s", attempt + 1, last.detail)
            await asyncio.sleep(2**attempt)
            continue
        if r.status_code < 400:
            try:
                return r.json()
            except ValueError as e:
                raise WhatsAppError(f"non-JSON success response: {r.text[:100]}") from e
        last = _to_error(r)
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            log.warning("WhatsApp API %s, retrying (attempt %d/3)", r.status_code, attempt + 1)
            await asyncio.sleep(2**attempt)
            continue
        raise last
    raise last or WhatsAppError("unknown WhatsApp error")


async def send_text(to: str, text: str) -> None:
    parts = chunk_text(text.strip() or "(empty)")
    for part in parts:
        await _post("messages", json={"messaging_product": "whatsapp", "to": to, "type": "text",
                                      "text": {"body": part}})
    log.info("Sent %d text message(s) to %s", len(parts), mask(to))


async def send_document(to: str, data: bytes, filename: str, caption: str = "") -> None:
    media = await _post("media", data={"messaging_product": "whatsapp", "type": "application/pdf"},
                        files={"file": (filename, data, "application/pdf")})
    media_id = media.get("id")
    if not media_id:
        raise WhatsAppError(f"media upload returned no id: {str(media)[:150]}")
    doc = {"id": media_id, "filename": filename}
    if caption:
        doc["caption"] = caption[:1000]
    await _post("messages", json={"messaging_product": "whatsapp", "to": to, "type": "document", "document": doc})
    log.info("Sent document %s (%d bytes) to %s", filename, len(data), mask(to))


async def mark_read(message_id: str) -> None:
    try:
        await _post("messages", json={"messaging_product": "whatsapp", "status": "read", "message_id": message_id})
    except WhatsAppError as e:  # a failed read receipt must never fail a job
        log.warning("mark_read failed: %s", e.detail)


async def notify(to: str, text: str) -> None:
    """Best-effort progress message. Never raises."""
    try:
        await send_text(to, text)
    except Exception as e:  # noqa: BLE001
        log.warning("Progress message not delivered: %s", str(e)[:150])
