"""Verify a signed webhook inside a Flask handler.

The control plane does not emit customer-facing webhooks yet — it currently only
ingests provider callbacks from Mailgun and SES. This example is here because the
signing scheme is fixed in the SDK contract, so both halves ship against one
definition. Do not point a production endpoint at this until the platform sends
its first event.

    pip install flask          # not a dependency of this SDK
    export NAIJAMAIL_WEBHOOK_SECRET=nmail_whsec_...
    python examples/webhook_flask.py
"""

from __future__ import annotations

import os

from flask import Flask, request

from nc_email import Webhooks, WebhookVerificationError

app = Flask(__name__)

SECRET = os.environ.get("NAIJAMAIL_WEBHOOK_SECRET", "")


@app.post("/webhooks/naijamail")
def handle_webhook():  # type: ignore[no-untyped-def]
    try:
        event = Webhooks.verify(
            # The raw body, before any parsing. A dict re-serialised by Flask
            # differs from the signed bytes by key order and spacing, so it
            # would fail verification at random.
            request.get_data(),
            request.headers.get("NC-Signature"),
            SECRET,
        )
    except WebhookVerificationError:
        # Say nothing about why. A verification error that explains itself is a
        # forger's feedback loop.
        return "", 400

    # Answer fast and do the work elsewhere: a sender that times out will retry,
    # and a retry storm is worse than a slow queue.
    print("event:", event.type, event.id, event.data)
    return "", 204


if __name__ == "__main__":
    if not SECRET:
        raise SystemExit("set NAIJAMAIL_WEBHOOK_SECRET")
    app.run(port=5000)
