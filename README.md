<p align="center">
  <a href="https://www.naijacloud.com">
    <img alt="Naijamail — Python SDK" src="https://raw.githubusercontent.com/naijacloud/nc-email-python/main/.github/assets/banner.png" width="100%">
  </a>
</p>

<p align="center">
  <a href="https://pypi.org/project/naijacloud-email/"><img alt="pypi" src="https://img.shields.io/badge/pypi-naijacloud--email-008751?style=flat-square&labelColor=0A0E0C"></a>
  <img alt="python" src="https://img.shields.io/badge/python-%3E%3D_3.9-4B8BBE?style=flat-square&labelColor=0A0E0C">
  <img alt="dependencies" src="https://img.shields.io/badge/dependencies-0-46C98A?style=flat-square&labelColor=0A0E0C">
  <a href="LICENSE"><img alt="license" src="https://img.shields.io/badge/license-MIT-8A988F?style=flat-square&labelColor=0A0E0C"></a>
</p>

<p align="center">
  <a href="#quickstart">Quickstart</a> ·
  <a href="#the-api">The API</a> ·
  <a href="#options">Options</a> ·
  <a href="#errors">Errors</a> ·
  <a href="#retries">Retries</a> ·
  <a href="#webhooks">Webhooks</a> ·
  <a href="#security">Security</a>
</p>

# Naijamail for Python

The official Python SDK for [Naijamail](https://www.naijacloud.com), Naija
Cloud's transactional email API.

Zero runtime dependencies. Python 3.9 and newer.

```bash
pip install naijacloud-email
```

## Quickstart

```python
from nc_email import Naijamail

nm = Naijamail()  # reads NAIJAMAIL_API_KEY

sent = nm.emails.send(
    from_="Acme <hello@acme.com>",
    to="customer@example.com",
    subject="Your receipt",
    html="<p>Thanks for your order.</p>",
)
print(sent.id, sent.status)          # 5b1e… queued

email = nm.emails.get(sent.id)
print(email.status, email.delivered_at)
```

`from_` carries a trailing underscore because `from` is a reserved word. If you
are porting from another SDK you can pass the whole thing as a dict instead, and
spell it `from`:

```python
nm.emails.send({
    "from": "Acme <hello@acme.com>",
    "to": ["a@example.com", "b@example.com"],
    "reply_to": "support@acme.com",
    "subject": "Invoice #1024",
    "html": "<p>Attached.</p>",
    "attachments": [
        {"filename": "invoice-1024.pdf", "content": pdf_bytes, "content_type": "application/pdf"},
    ],
    "tags": {"campaign": "invoices"},
})
```

## The API

The service has exactly two endpoints, so the SDK has exactly two methods.

| | |
| --- | --- |
| `nm.emails.send(...)` | `POST /v1/emails` — returns `SendEmailResponse(id, status, rejected)` |
| `nm.emails.get(id)` | `GET /v1/emails/{id}` — returns `Email` |

There is no domains, api-keys, batch or contacts resource here. Those do not
exist server-side, and a method that 404s is worse than no method.

### Sending

| Parameter | Type | Required | Notes |
| --- | --- | --- | --- |
| `from_` (or `from`) | `str` | yes | `"Name <a@b.com>"` or `"a@b.com"`. The domain must be verified for your team. |
| `to` | `str` \| `list[str]` | yes | At least one address. |
| `cc`, `bcc`, `reply_to` | `str` \| `list[str]` | no | `replyTo` is accepted as well; the SDK always sends `reply_to`. |
| `subject` | `str` | no | Defaults to `""`, and is always sent. |
| `html`, `text` | `str` | no | |
| `headers` | `dict[str, str]` | no | At most 25. `From`, `To`, `Cc`, `Bcc`, `Subject`, `DKIM-Signature` and `Received` are refused. |
| `attachments` | `list[dict]` | no | `{filename, content, content_type?, content_id?}` |
| `tags` | `dict[str, str]` | no | At most 10; keys up to 64 chars, values up to 256. |
| `idempotency_key` | `str` | no | One is generated for you if you leave it out. |

`SendEmailResponse.rejected` is always a list, empty when nobody was rejected —
the API omits the field entirely in that case, and you should not have to branch
on absence. A non-empty `rejected` is not an error: those recipients are on the
suppression list and the rest of the message still went out.

`Email.from_` carries the same trailing underscore, for the same reason.

Statuses arrive as plain lowercase strings: `queued`, `sent`, `delivered`,
`bounced`, `deferred`, `complained`, `rejected`, `failed`. Compare them against
`MessageStatus.DELIVERED` and friends. They are constants rather than an enum on
purpose — a strict enum would raise the day the platform adds a status, breaking
every SDK older than that day. Use `MessageStatus.is_known(status)` if you need
to notice a new one.

### Attachments

Pass raw `bytes`. The SDK base64-encodes them:

```python
with open("invoice.pdf", "rb") as handle:
    content = handle.read()

nm.emails.send(
    from_="Acme <hello@acme.com>",
    to="customer@example.com",
    subject="Invoice",
    text="Attached.",
    attachments=[{"filename": "invoice.pdf", "content": content}],
)
```

A `str` is accepted only as already-encoded base64, and is validated strictly
rather than silently mangled. A file **path** is never accepted: an SDK that
opens arbitrary paths on your behalf becomes a local-file-read primitive the
moment one of those paths comes from an HTTP request. Read the file yourself.

## Options

```python
nm = Naijamail(
    api_key=None,             # else NAIJAMAIL_API_KEY
    base_url=None,            # else NAIJAMAIL_BASE_URL, else https://api.naijacloud.com
    timeout=30.0,             # seconds, per attempt
    max_retries=2,            # 3 attempts in total
    user_agent_suffix=None,   # appended to nc-email-python/<version> (python/<version>)
)
```

A client owns its key, its base URL and its HTTP opener. Nothing is held at
module level, so two clients with two keys in one process cannot interfere.

## Errors

Every failure is a subclass of `NaijamailError`, carrying `message`,
`status_code`, `error` (the server's short label), `request_id` (from
`x-request-id`) and the raw `body`.

| HTTP | Exception | Retried |
| --- | --- | --- |
| 400 | `ValidationError` (`NotFoundError` when the message is `message not found`) | no |
| 401 | `AuthenticationError` | no |
| 403 | `NaijamailPermissionError` | no |
| 404 | `NotFoundError` | no |
| 408 | `NaijamailTimeoutError` | yes |
| 409 | `ConflictError` | no |
| 422 | `ValidationError` | no |
| 429 | `RateLimitError` (`.retry_after` in seconds) | yes |
| 5xx | `ServerError` | yes |
| socket / DNS / TLS | `NaijamailConnectionError` | yes |
| client-side deadline | `NaijamailTimeoutError` | yes |
| caught before sending | `ValidationError` (`status_code == 0`) | no |

```python
from nc_email import Naijamail, NaijamailError, RateLimitError

try:
    nm.emails.send(from_="hello@acme.com", to="customer@example.com", subject="Hi")
except RateLimitError as exc:
    print("slow down for", exc.retry_after, "seconds")
except NaijamailError as exc:
    print(exc.status_code, exc.message, exc.request_id)
```

`ConnectionError`, `TimeoutError` and `PermissionError` are Python builtins, so
the canonical class names are prefixed — `NaijamailConnectionError`,
`NaijamailTimeoutError`, `NaijamailPermissionError`. The short spellings exist
as aliases (`nc_email.TimeoutError`) but are kept out of `__all__`, so a
`from nc_email import *` cannot quietly replace a builtin in your module and
change the meaning of an unrelated `except`.

Retrieving an id that does not exist raises `NotFoundError` even though the API
answers `400` for it. That is a known server quirk, mapped here so your `except`
reads the way you expect.

## Retries

Three attempts by default, 30 seconds each. Retried on `429`, `408`, any `5xx`,
and connection or timeout failures. Never on any other `4xx` — a `403` for an
unverified domain will not succeed on the second try.

Backoff is exponential with full jitter (`random(0, min(8s, 0.5s * 2^attempt))`).
A `Retry-After` header overrides it, in either its integer-seconds or its
HTTP-date spelling, clamped to 60 seconds.

Retrying a `POST` is only safe because of idempotency. If you do not supply an
`idempotency_key`, the SDK generates one UUIDv4 per `send()` call and sends it as
`Idempotency-Key` on every attempt of that call. Without that, a lost response
followed by a retry would send your customer two copies of the same receipt.
A key you supply yourself is used as given and never regenerated.

## Security

The rules the SDK enforces, and why:

- **HTTPS only.** A `base_url` that is not `https` is rejected at construction,
  unless the host is `localhost`, `127.0.0.1` or `::1`. A plaintext base URL
  would put a live sending credential on the wire in clear.
- **Redirects are refused, never followed.** urllib's default handler replays
  the original request — `Authorization` and all — at whatever host `Location`
  names. A 3xx surfaces as `ServerError("unexpected redirect")`.
- **TLS verification is explicit.** The SDK builds its own
  `ssl.create_default_context()` with certificate and hostname checks forced on,
  so its behaviour does not depend on a process-wide default someone loosened.
- **The key never leaves except in the auth header.** `repr()`, `str()` and the
  User-Agent all show `nmail_live_***`; the key is not an attribute of the
  client object; the client refuses to pickle.
- **Header-injection defence.** CR, LF or NUL in `from`, any address, `subject`,
  a custom header name or value, an attachment filename, or the idempotency key
  is rejected before any network call.
- **Client-side limits**, mirroring the server: 50 recipients across
  `to`+`cc`+`bcc`, 10 MiB encoded, 25 headers, 10 tags.
- **Key shape is checked at construction** (`nmail_live_…`, `nmail_test_…` or
  `nc_live_…`), so a bad key fails at deploy rather than as a 401 during a
  customer's checkout.

A **test** key (`nmail_test_…`) is refused by the send path with `403`. That is
deliberate: a staging box holding production credentials fails loudly instead of
mailing real customers.

### Which key

Two kinds work, and the SDK cannot tell them apart once it has one:

- **`nc_live_…`** — a workspace API key from **Settings → API keys**, ticked for
  the **Email send** scope. Most teams already have one: it is the same
  credential CI deploys with. Add **Platform API** as well if the key also needs
  to manage sending domains or suppressions.
- **`nmail_live_…` / `nmail_test_…`** — a Naijamail-only key from **Email**. The
  test variant is refused by the send path with a `403`, on purpose, so a
  staging box holding production credentials fails loudly instead of mailing
  real customers. There is no test variant of a workspace key.

An `nc_pat_…` platform token is not accepted: those predate the Email send scope
and the API refuses them on the mail routes, so the SDK refuses them at
construction rather than a request later.

Report a vulnerability to security@naijacloud.com. See [SECURITY.md](SECURITY.md).

## Webhooks

> **Live.** Naija Cloud delivers these events to endpoints you register, signed
> exactly as below. Two details this verifier already handles: the timestamp is
> taken per delivery *attempt*, so a retry never arrives outside the tolerance
> window; and during a secret rotation the header carries two `v1=` values for
> 24 hours, which is why any match is accepted.

```python
from nc_email import Webhooks, WebhookVerificationError

@app.post("/webhooks/naijamail")
def handle():
    try:
        event = Webhooks.verify(
            request.get_data(),                    # the raw body bytes, not a parsed dict
            request.headers.get("NC-Signature"),
            os.environ["NAIJAMAIL_WEBHOOK_SECRET"],
        )
    except WebhookVerificationError:
        return "", 400
    return "", 204
```

The header is `NC-Signature: t=<unix seconds>,v1=<hex sha256 hmac>`, over
`"<t>.<raw body>"`. Several `v1=` values may be present during a secret
rotation, and any one matching is enough. Timestamps outside 300 seconds are
rejected, which is what stops a captured request being replayed later. The
comparison is constant-time, and a failure never tells you the expected
signature.

Pass the **raw bytes**. A parsed object re-serialised differs from what was
signed by key order and spacing, so it would fail verification at random.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The test suite needs no network and no
third-party packages:

```bash
python -m unittest discover -s tests -v
```

## License

MIT. Copyright (c) 2026 Naija Cloud.
