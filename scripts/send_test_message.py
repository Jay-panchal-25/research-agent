"""Simulate a signed Meta webhook POST to your local API.  python -m scripts.send_test_message "your question"
Uses the first number in ALLOWED_NUMBERS. The worker will really reply to that WhatsApp number."""
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402

number = next(iter(sorted(settings.allowed_numbers_set)), None)
if not number:
    sys.exit("Set ALLOWED_NUMBERS in .env first")
text = " ".join(sys.argv[1:]) or "hello from the test script"
ts = int(time.time())
payload = {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [{"field": "messages", "value": {
    "messaging_product": "whatsapp",
    "messages": [{"from": number, "id": f"wamid.TEST{ts}", "timestamp": str(ts), "type": "text", "text": {"body": text}}],
}}]}]}
body = json.dumps(payload).encode()
sig = "sha256=" + hmac.new(settings.meta_app_secret.encode(), body, hashlib.sha256).hexdigest()
r = httpx.post("http://127.0.0.1:8000/webhook", content=body,
               headers={"X-Hub-Signature-256": sig, "Content-Type": "application/json"}, timeout=15)
print(r.status_code, r.text)
