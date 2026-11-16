import hashlib
import hmac
import logging

from fastapi import HTTPException
from pydantic import ValidationError

from app.config import settings
from app.database import mongo
from app.logging_config import mask
from app.models.whatsapp import WAMessage, WebhookPayload
from app.services import whatsapp

log = logging.getLogger(__name__)


def valid_signature(body: bytes, header: str) -> bool:
    """Validate WhatsApp / Meta Webhook X-Hub-Signature-256 header."""
    if not header.startswith("sha256="):
        return False
    expected = hmac.new(settings.meta_app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len("sha256="):], expected)


def verify_challenge(mode: str | None, token: str | None, challenge: str | None) -> str:
    """Verify webhook setup handshake with Meta."""
    if mode == "subscribe" and token and challenge and hmac.compare_digest(token, settings.wa_verify_token):
        log.info("Webhook verified by Meta")
        return challenge
    log.warning("Webhook verification rejected (mode=%s)", mode)
    raise HTTPException(status_code=403, detail="Verification failed")


async def handle_message(msg: WAMessage, arq_pool) -> None:
    """Allowlist -> dedupe -> enqueue into ARQ worker.
    
    Must stay fast: Meta expects a 200 within 3 seconds.
    """
    if msg.sender not in settings.allowed_numbers_set:
        log.info("Ignored message from non-allowlisted number %s", mask(msg.sender))
        return

    if not await mongo.mark_message_seen(msg.id):
        log.info("Duplicate message %s ignored", msg.id)
        return

    try:
        if msg.type != "text" or msg.text is None or not msg.text.body.strip():
            await whatsapp.send_text(
                msg.sender,
                "I can only read text messages for now. Send me a question as text."
            )
            return

        job = await arq_pool.enqueue_job(
            "run_research",
            {"id": msg.id, "from": msg.sender, "text": msg.text.body},
            _job_id=msg.id,
        )
        if job is None:
            log.info("Job %s already queued (same message id)", msg.id)
        else:
            log.info("Queued job %s from %s", msg.id, mask(msg.sender))
    except Exception as e:
        # Forget this id so Meta's retry can be processed instead of dropped as duplicate
        try:
            await mongo.unmark_message(msg.id)
        except Exception:  # noqa: BLE001
            log.exception("Could not unmark message %s after failure", msg.id)
        log.error("Failed to handle message %s: %s", msg.id, str(e)[:200])
        raise


async def process_webhook(body: bytes, signature_header: str, arq_pool) -> dict:
    """Validate webhook request, parse payload, and dispatch all messages."""
    if not valid_signature(body, signature_header):
        log.warning("Rejected webhook POST with invalid signature (check META_APP_SECRET)")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = WebhookPayload.model_validate_json(body)
    except ValidationError as e:
        log.warning("Unparseable webhook payload ignored: %s", str(e)[:200])
        return {"status": "ignored"}

    failures = 0
    for msg in payload.all_messages():
        try:
            await handle_message(msg, arq_pool)
        except Exception:  # noqa: BLE001
            failures += 1
            log.exception("Error while handling message %s", msg.id)

    if failures:
        # Non-2xx makes Meta retry delivery; already-queued messages are deduplicated
        raise HTTPException(status_code=500, detail="processing failed")

    return {"status": "ok"}
