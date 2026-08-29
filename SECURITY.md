# Security

## Reporting a vulnerability

Email **security@naijacloud.com**. Do not open a public issue, and do not post
the details anywhere public until we have shipped a fix.

Include what you need to make the problem reproducible: the SDK version, the
Python version, and the smallest snippet that shows the behaviour. If you have a
proof of concept, send it — we would rather read it than guess at it.

We aim to acknowledge within two working days (Nigeria, WAT) and to ship a fix
or a mitigation for anything confirmed as high severity within seven days. You
will be credited in the changelog unless you would rather not be.

**Never send us a real API key**, in a report or anywhere else. If you believe a
key has been exposed, revoke it in the dashboard first, then tell us.

## What this package is protecting

A Naijamail key can send mail as one of your verified domains, DKIM-signed, with
your own DNS vouching for it. A leak is a phishing incident with your brand
attached, not an information disclosure. The rules below follow from that.

## What the SDK guarantees

- **HTTPS or loopback.** A non-`https` `base_url` is refused at construction
  unless the host is `localhost`, `127.0.0.1` or `::1`.
- **No redirect following.** A 3xx is surfaced as an error. Following one would
  re-send `Authorization` to a host named by the response.
- **Explicit TLS verification.** Certificate and hostname checks are forced on in
  a context the SDK builds itself, not inherited from process-wide defaults.
- **No key in output.** `repr`, `str`, the User-Agent and every exception message
  show `nmail_live_***`. The key is not an attribute of the client object, and
  the client refuses to pickle.
- **Header-injection defence** on every field that becomes a mail header, plus
  the idempotency key, which becomes an HTTP header on our own request.
- **Automatic idempotency**, so the retry policy cannot double-send.
- **Constant-time signature comparison** for webhooks.
- **No implicit file access.** Attachments take bytes. The SDK never opens a path
  on your behalf.
- **Zero runtime dependencies**, so installing this package adds exactly one
  supply chain — ours.

## What it cannot do for you

- Keep your key out of your own logs, error trackers or environment dumps.
- Stop you passing a `base_url` you do not control.
- Verify a webhook you have already parsed and re-serialised. Pass the raw bytes.

## Supported versions

Security fixes land on the latest minor release. Older ones are not backported;
upgrade.
