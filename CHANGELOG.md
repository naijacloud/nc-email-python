# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-08-29

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

[Unreleased]: https://github.com/naija-cloud/nc-email-python/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/naija-cloud/nc-email-python/releases/tag/v0.1.0
