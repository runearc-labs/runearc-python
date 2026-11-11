import base64
import hashlib
import hmac
import json
import os
import time
import uuid

from runearc import Runearc, verify_webhook

with Runearc() as runearc:  # reads RUNEARC_API_KEY and RUNEARC_BASE_URL
    inbox = runearc.inboxes.create({"display_name": "quickstart"})
    print("inbox", inbox["address"])

    subject = f"Hello from the Python SDK {uuid.uuid4().hex[:6]}"
    sent = runearc.emails.send(
        {
            "from": os.environ["RUNEARC_FROM"],
            "to": inbox["address"],
            "subject": subject,
            "text": "It works.",
        }
    )
    print("sent", sent["id"])

    waited = runearc.messages.wait(
        inbox["id"], subject=subject, after=inbox["created_at"], timeout=30
    )
    if waited is None:
        raise SystemExit("no message arrived within 30 seconds")
    print("received", waited["message"]["subject"])

    # Sign a sample event the way Runearc does (Standard Webhooks), then verify it.
    secret = "whsec_" + base64.b64encode(os.urandom(32)).decode()
    payload = json.dumps(
        {"id": "evt_1", "type": "message.delivered", "data": {"email_id": sent["id"]}}
    )
    timestamp = str(int(time.time()))
    key = base64.b64decode(secret.removeprefix("whsec_"))
    digest = hmac.new(key, f"evt_1.{timestamp}.{payload}".encode(), hashlib.sha256).digest()
    headers = {
        "webhook-id": "evt_1",
        "webhook-timestamp": timestamp,
        "webhook-signature": "v1," + base64.b64encode(digest).decode(),
    }
    verify_webhook(payload, headers, secret)
    print("webhook verified")

    runearc.inboxes.delete(inbox["id"])
