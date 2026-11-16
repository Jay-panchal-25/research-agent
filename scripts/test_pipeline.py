"""Directly test the async research pipeline without needing a live WhatsApp token."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.controllers import research_controller
from app.database import mongo, qdrant
from app.logging_config import setup_logging

setup_logging("INFO")


async def main():
    loop = asyncio.get_running_loop()
    orig_handler = loop.get_exception_handler()

    def _handler(loop, context):
        msg = context.get("message", "")
        exc = context.get("exception")
        if "SSL" in msg or (exc and "Event loop is closed" in str(exc)):
            return
        if orig_handler:
            orig_handler(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(_handler)

    await mongo.init_indexes()

    # Mock WhatsApp sending so we don't fail on expired WhatsApp tokens during local test
    async def mock_send_text(phone, text):
        print(f"\n[MOCK WHATSAPP TEXT to {phone}]:\n{text}\n")
        return {"messages": [{"id": "mock_id"}]}

    async def mock_send_doc(phone, pdf_bytes, filename, caption=""):
        print(f"\n[MOCK WHATSAPP PDF to {phone}]: file={filename} size={len(pdf_bytes)} bytes caption={caption}\n")
        return {"messages": [{"id": "mock_doc_id"}]}

    async def mock_notify(phone, text):
        print(f"\n[MOCK NOTIFY to {phone}]: {text}\n")

    async def mock_mark_read(mid):
        print(f"[MOCK MARK READ]: {mid}")

    research_controller.whatsapp.send_text = mock_send_text
    research_controller.whatsapp.send_document = mock_send_doc
    research_controller.whatsapp.notify = mock_notify
    research_controller.whatsapp.mark_read = mock_mark_read

    phone = next(iter(sorted(settings.allowed_numbers_set)), "15551234567")
    test_msg = {
        "id": "wamid.LOCALTEST001",
        "from": phone,
        "text": "What is Python asyncio?"
    }

    print("=== Testing handle_research_job ===")
    await research_controller.handle_research_job(test_msg)
    print("=== Pipeline Completed Successfully ===")

    await qdrant.close()
    await mongo.close()
    await asyncio.sleep(0.25)


if __name__ == "__main__":
    asyncio.run(main())
