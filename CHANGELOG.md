# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-10-04

The first version published to PyPI (`pip install naijacloud-email`). 0.1.0
was written up here but never uploaded, so its entries below are part of this
release too.

### Added

- Accept a workspace API key (`nc_live_…`) alongside the Naijamail keys. It is
  the credential from **Settings → API keys**, and it reaches the mail API when
  it carries the **Email send** scope — so a team that already has one for
  deploys and the platform API does not need a second secret to send mail.
  Redaction knows the new prefix, so a dump still shows which kind of credential
  a process is holding. `nc_pat_…` platform tokens remain refused: they predate
  the scope and the API rejects them on the mail routes.
- `Email.sandbox` on a retrieved email: true for a message sent with a test key,
  which is recorded but never delivered, so a simulated bounce can be told from
  a real one.

### Fixed

- **Security:** the API key is trimmed and checked with a full match. A key
  with a trailing newline used to pass the check and then appear, whole, in a
  raw `ValueError` from `http.client`.
- `send()` refuses an argument it does not know (`htlm=`) instead of dropping it.
- `Webhooks.verify` refuses a NaN, infinite or negative `tolerance`, which
  switched replay protection off.
- A 413 (request too large) is a `ValidationError`.
- Tag length is counted in UTF-16 units, the way the server counts it.
- Test keys (`nmail_test_…`) are sandboxed by the API, not refused with a 403.
  The README said otherwise.

## 0.1.0 - 2026-08-29 (never published)

First release. Implements SDK-CONTRACT.md in full.

### Added

- `Naijamail` client with `api_key`, `base_url`, `timeout`, `max_retries` and
  `user_agent_suffix` options. Key and base URL are read from
  `NAIJAMAIL_API_KEY` and `NAIJAMAIL_BASE_URL` when not passed.
- `emails.send()`, accepting keyword arguments (`from_=`) or a single dict
  (`{"from": ...}`) for callers porting from another SDK.
- `emails.get(id)`.
- Frozen response objects — `SendEmailResponse`, `Email`, `RejectedRecipient`,
  `WebhookEvent` — that ignore response fields they do not recognise, so a
  server-side addition cannot break a pinned SDK.
- `rejected` normalised to an always-present list.
- `MessageStatus` constants with passthrough of values the SDK has not seen.
- Full error hierarchy under `NaijamailError`, including the 400
  `message not found` mapping to `NotFoundError`.
- Retries on 429, 408, 5xx, connection and timeout failures, with full-jitter
  exponential backoff and `Retry-After` support in both its integer-seconds and
  HTTP-date forms.
- Automatic per-call idempotency key, held constant across every retry of a
  single `send()`.
- `Webhooks.verify()` for the `NC-Signature` scheme. Naija Cloud emits these
  events; the scheme is shared with every other Naijamail SDK, so all of them
  verify identically.
- Type hints throughout, with a `py.typed` marker.

### Security

- HTTPS enforced at construction, with a loopback exemption for local
  development.
- Redirects refused rather than followed, so `Authorization` is never replayed to
  a host named by a response.
- Explicit `ssl.create_default_context()` with certificate and hostname
  verification forced on.
- API key redacted from `repr`, `str` and the User-Agent, absent from the client
  object's attributes, and the client refuses to pickle.
- Header-injection rejection on every field that becomes a mail header, and on
  the idempotency key.
- Forbidden custom headers refused locally.
- Client-side recipient, payload, header and tag limits mirroring the server.
- Attachments take bytes; file paths are never opened on the caller's behalf.
- Constant-time webhook signature comparison, with a replay window.

[Unreleased]: https://github.com/naijacloud/nc-email-python/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/naijacloud/nc-email-python/releases/tag/v0.2.0
