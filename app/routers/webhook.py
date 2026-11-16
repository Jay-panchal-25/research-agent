import logging

from fastapi import APIRouter, Query, Request, Response

from app.controllers.webhook_controller import process_webhook, verify_challenge

router = APIRouter()
log = logging.getLogger(__name__)


@router.get("/webhook")
async def verify(
    mode: str | None = Query(None, alias="hub.mode"),
    token: str | None = Query(None, alias="hub.verify_token"),
    challenge: str | None = Query(None, alias="hub.challenge"),
):
    challenge_response = verify_challenge(mode, token, challenge)
    return Response(content=challenge_response, media_type="text/plain")


@router.post("/webhook")
async def receive(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")
    return await process_webhook(body, signature, request.app.state.arq)
