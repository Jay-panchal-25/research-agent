import logging
from datetime import datetime, timedelta, timezone

from pymongo import AsyncMongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.config import settings

log = logging.getLogger(__name__)
_client: AsyncMongoClient | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def get_db():
    global _client
    if _client is None:
        _client = AsyncMongoClient(settings.mongodb_uri)
    return _client[settings.mongodb_db]


async def init_indexes() -> None:
    db = get_db()
    await db.messages.create_index("message_id", unique=True)
    await db.messages.create_index("created_at", expireAfterSeconds=7 * 24 * 3600)
    await db.conversations.create_index("phone", unique=True)
    await db.reports.create_index("report_id", unique=True)
    await db.evidence.create_index("report_id")
    await db.usage.create_index([("phone", 1), ("day", 1)], unique=True)
    await db.usage.create_index("created_at", expireAfterSeconds=3 * 24 * 3600)


# ---------- webhook dedupe ----------
async def mark_message_seen(message_id: str) -> bool:
    try:
        await get_db().messages.insert_one({"message_id": message_id, "created_at": _now()})
        return True
    except DuplicateKeyError:
        return False


async def unmark_message(message_id: str) -> None:
    await get_db().messages.delete_one({"message_id": message_id})


# ---------- conversation state (per phone number) ----------
async def get_conversation(phone: str) -> dict:
    return await get_db().conversations.find_one({"phone": phone}) or {}


async def update_conversation(phone: str, **fields) -> None:
    await get_db().conversations.update_one({"phone": phone}, {"$set": fields}, upsert=True)


async def unset_conversation(phone: str, *names: str) -> None:
    await get_db().conversations.update_one({"phone": phone}, {"$unset": {n: "" for n in names}})


async def try_start_job(phone: str, job_id: str) -> bool:
    """One research job per user at a time. The same job id may re-acquire (ARQ retry after a crash).
    Stale locks (>25 min) are ignored."""
    cutoff = _now() - timedelta(minutes=25)
    try:
        await get_db().conversations.find_one_and_update(
            {"phone": phone, "$or": [{"running_since": None}, {"running_since": {"$lt": cutoff}},
                                     {"running_job": job_id}]},
            {"$set": {"running_since": _now(), "running_job": job_id}},
            upsert=True,
        )
        return True
    except DuplicateKeyError:  # row exists and is locked by another job
        return False


async def finish_job(phone: str) -> None:
    await get_db().conversations.update_one({"phone": phone}, {"$set": {"running_since": None, "running_job": None}})


async def check_and_incr_usage(phone: str, limit: int) -> bool:
    day = _now().strftime("%Y-%m-%d")
    doc = await get_db().usage.find_one_and_update(
        {"phone": phone, "day": day},
        {"$inc": {"count": 1}, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["count"] <= limit


# ---------- reports and evidence ----------
async def save_report(doc: dict) -> None:
    doc = {**doc, "created_at": _now()}
    await get_db().reports.replace_one({"report_id": doc["report_id"]}, doc, upsert=True)


async def get_report(report_id: str) -> dict | None:
    return await get_db().reports.find_one({"report_id": report_id})


async def save_evidence(items: list[dict]) -> None:
    if items:
        await get_db().evidence.insert_many([dict(e) for e in items])  # copies: insert_many adds _id


async def close() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
