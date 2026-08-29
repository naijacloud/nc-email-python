"""Send a message with an attachment.

    export NAIJAMAIL_API_KEY=nmail_live_...
    python examples/attachment.py you@example.com ./invoice.pdf
"""

from __future__ import annotations

import os
import sys

from nc_email import Naijamail

FROM = os.environ.get("NAIJAMAIL_FROM", "Acme <hello@acme.com>")


def main() -> int:
    recipient = sys.argv[1] if len(sys.argv) > 1 else "customer@example.com"
    path = sys.argv[2] if len(sys.argv) > 2 else None

    if path:
        # You open the file, not the SDK. An SDK that reads paths on your behalf
        # is a local-file-read primitive the moment one of those paths comes
        # from an HTTP request.
        with open(path, "rb") as handle:
            content = handle.read()
        filename = os.path.basename(path)
    else:
        content = b"%PDF-1.4\n% a placeholder, not a real PDF\n"
        filename = "invoice-1024.pdf"

    nm = Naijamail()
    sent = nm.emails.send(
        from_=FROM,
        to=recipient,
        subject="Invoice #1024",
        html='<p>Your invoice is attached.</p>',
        attachments=[
            {
                # Raw bytes. The SDK base64-encodes them, because a caller
                # hand-encoding is a caller getting it subtly wrong.
                "filename": filename,
                "content": content,
                "content_type": "application/pdf",
            }
        ],
        tags={"campaign": "invoices"},
    )
    print("queued:", sent.id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
