# Contributing

## Layout

```
src/nc_email/     the package
tests/            unittest suite, runs against a local mock HTTP server
examples/         runnable scripts, key read from the environment
```

## Running the tests

The suite has no third-party dependencies and needs no network. It binds a real
`ThreadingHTTPServer` on `127.0.0.1:0` and speaks real HTTP to it, so the parts
most likely to break — the redirect handler, the TLS wiring, the timeout
plumbing — are actually exercised rather than stubbed out.

```bash
python -m unittest discover -s tests -v
```

From a checkout, `tests/_support.py` puts `src/` on `sys.path`, so this works
without installing anything. Against an installed wheel, the installed package
wins.

pytest works too, if you prefer it:

```bash
pip install -e ".[dev]"
pytest
ruff check .
mypy
```

## Supported Python versions

3.9 and newer, and CI runs all of them. That means:

- `from __future__ import annotations` at the top of every module.
- `typing.Optional` / `typing.Union`, never `X | Y` at runtime.
- No `match`, no `tomllib`, no `ExceptionGroup`.

## House rules

- **No runtime dependencies.** This package holds a live sending credential; a
  new import is a new supply chain. Dev and test tooling is unrestricted.
- **Comments explain why.** A comment restating the code is noise. Say what
  decision was made, what failure it prevents, or what trap it avoids.
- **The contract is the spec.** `email-sdks/spec/SDK-CONTRACT.md` fixes the wire
  format, error taxonomy, retry policy and security rules for every language
  SDK. If this SDK and that document disagree, one of them is a bug. If the
  document and the control plane disagree, the control plane wins and the
  document gets fixed.
- **Never commit a real key.** Tests use the literal
  `nmail_live_test0000000000000000`.
- **Do not add endpoints the server does not serve.** Two endpoints exist:
  `POST /v1/emails` and `GET /v1/emails/{id}`.

## Adding a feature

1. Check it against the contract. If the contract does not cover it, change the
   contract first — every language SDK has to end up with the same behaviour.
2. Write the test against the mock server before the code.
3. Add a `CHANGELOG.md` entry under `Unreleased`.

## Releasing

Releases come from CI, on a tag. Nobody uploads from a laptop, and there is no
PyPI token anywhere: `.github/workflows/release.yml` publishes with PyPI
trusted publishing.

1. Bump `__version__` in `src/nc_email/_version.py` (the build and the
   User-Agent both read it from there).
2. Move the `Unreleased` changelog entries under `## [x.y.z] - YYYY-MM-DD` and
   update the link definitions at the foot of the file.
3. Tag and push: `git tag v<version> && git push origin v<version>`.

The workflow runs the CI matrix, refuses a tag that disagrees with
`__version__` or has no changelog section, builds the sdist and wheel, uploads
them to PyPI, then creates the GitHub release from the changelog section.

### One-time setup

- **pypi.org → Your account → Publishing → Add a pending publisher** (before the
  first upload; afterwards it is under the project's Settings → Publishing):
  PyPI project `naijacloud-email`, owner `naijacloud`, repository
  `nc-email-python`, workflow `release.yml`, environment `pypi`.
- **GitHub → Settings → Environments → New environment `pypi`.** Add required
  reviewers to make an upload wait for a human.
