"""Send one message.

    export NAIJAMAIL_API_KEY=nmail_live_...
    python examples/send.py you@example.com
"""

from __future__ import annotations

import os
import sys

from nc_email import Naijamail

# Change this to an address on a domain your team has verified. The API refuses
# anything else with a 403 — verified DNS is the only evidence it accepts.
FROM = os.environ.get("NAIJAMAIL_FROM", "Acme <hello@acme.com>")


def main() -> int:
    recipient = sys.argv[1] if len(sys.argv) > 1 else "customer@example.com"

    # The key comes from NAIJAMAIL_API_KEY. Never hard-code it: this file is the
    # one people copy into their own repository.
    nm = Naijamail()

    sent = nm.emails.send(
        from_=FROM,
        to=recipient,
        subject="Your receipt",
        html="<p>Thanks for your order.</p>",
        text="Thanks for your order.",
    )

    print("queued:", sent.id, sent.status)

    # Not an error. Those addresses are on the suppression list; everyone else
    # still got the message.
    for rejected in sent.rejected:
        print("refused:", rejected.address, rejected.reason)

    # 202 means accepted, not delivered. Poll or wait for a webhook.
    email = nm.emails.get(sent.id)
    print("status now:", email.status, "delivered_at:", email.delivered_at)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
