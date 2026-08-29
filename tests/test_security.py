"""SDK-CONTRACT.md section 5, the rules that are not negotiable.

A leaked Naijamail key lets an attacker send DKIM-signed mail as a customer's
verified domain, with our DNS vouching for it. That makes every one of these a
correctness test, not hygiene.
"""

from __future__ import annotations

import ssl
import unittest
import urllib.request

from _support import TEST_KEY, MockAPI

import nc_email
from nc_email import Naijamail, ServerError, ValidationError
from nc_email.http import _NoRedirectHandler, build_opener

MINIMAL = {"from_": "a@acme.com", "to": "x@y.com"}
SECRET_TAIL = TEST_KEY.split("_", 2)[2]


class HttpsEnforcementTest(unittest.TestCase):
    def test_plaintext_to_a_remote_host_is_refused(self) -> None:
        for url in [
            "http://api.naijacloud.com",
            "http://api.example.com:8080",
            "http://192.168.1.10",
            "http://localhost.evil.com",
        ]:
            with self.subTest(url=url):
                with self.assertRaises(ValidationError) as caught:
                    Naijamail(TEST_KEY, base_url=url)
                self.assertIn("https", str(caught.exception))

    def test_loopback_may_use_plaintext(self) -> None:
        # A dev control plane on loopback never puts the key on a wire.
        for url in ["http://localhost:3000", "http://127.0.0.1:3000", "http://[::1]:3000"]:
            with self.subTest(url=url):
                self.assertTrue(Naijamail(TEST_KEY, base_url=url).base_url.startswith("http://"))

    def test_https_is_accepted(self) -> None:
        self.assertEqual(
            Naijamail(TEST_KEY, base_url="https://api.example.com").base_url,
            "https://api.example.com",
        )

    def test_other_schemes_are_refused(self) -> None:
        for url in ["ftp://api.example.com", "file:///etc/passwd", "ws://api.example.com"]:
            with self.subTest(url=url):
                with self.assertRaises(ValidationError):
                    Naijamail(TEST_KEY, base_url=url)


class TlsTest(unittest.TestCase):
    def test_the_opener_refuses_redirects(self) -> None:
        opener = build_opener()
        handlers = [type(handler) for handler in opener.handlers]
        self.assertIn(_NoRedirectHandler, handlers)
        self.assertNotIn(urllib.request.HTTPRedirectHandler, handlers)

    def test_verification_is_forced_on(self) -> None:
        # A permissive context handed in — the shape of the "just make it work"
        # fix people paste from a search result — is corrected, not honoured.
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        build_opener(context)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)


class KeyRedactionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = Naijamail(TEST_KEY, base_url="https://api.example.com")

    def test_repr_and_str(self) -> None:
        for text in (repr(self.client), str(self.client)):
            self.assertNotIn(TEST_KEY, text)
            self.assertNotIn(SECRET_TAIL, text)
            self.assertIn("nmail_live_***", text)

    def test_instance_dict(self) -> None:
        # What a debugger, a pprint and most "log the object" helpers reach for.
        text = repr(self.client.__dict__)
        self.assertNotIn(TEST_KEY, text)
        self.assertNotIn(SECRET_TAIL, text)

    def test_the_transport_repr(self) -> None:
        transport = self.client.__dict__["_transport"]
        self.assertNotIn(SECRET_TAIL, repr(transport))
        self.assertNotIn(SECRET_TAIL, repr(transport.__dict__.get("_user_agent", "")))

    def test_the_user_agent(self) -> None:
        self.assertNotIn(SECRET_TAIL, self.client.user_agent)

    def test_construction_errors_do_not_echo_the_key(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            Naijamail("nmail_live_short")
        self.assertNotIn("nmail_live_short", str(caught.exception))

    def test_redact_key_keeps_only_the_prefix(self) -> None:
        self.assertEqual(nc_email.redact_key(TEST_KEY), "nmail_live_***")
        self.assertEqual(nc_email.redact_key("nmail_test_abcdefgh"), "nmail_test_***")
        self.assertEqual(nc_email.redact_key("garbage"), "***")


class KeyOnTheWireTest(unittest.TestCase):
    def test_the_key_appears_only_in_the_authorization_header(self) -> None:
        api = MockAPI().start()
        self.addCleanup(api.stop)
        api.enqueue_accepted()
        Naijamail(TEST_KEY, base_url=api.base_url, max_retries=0).emails.send(**MINIMAL)

        request = api.requests[-1]
        self.assertEqual(request.headers["authorization"], "Bearer " + TEST_KEY)
        for name, value in request.headers.items():
            if name != "authorization":
                self.assertNotIn(SECRET_TAIL, value, "key leaked into " + name)
        self.assertNotIn(SECRET_TAIL.encode(), request.body)


class RedirectTest(unittest.TestCase):
    def test_a_redirect_never_replays_the_credential(self) -> None:
        # urllib's default handler would re-send Authorization to whatever host
        # Location names. That is how bearer tokens leak.
        api = MockAPI().start()
        self.addCleanup(api.stop)
        api.enqueue_raw(307, b"", {"Location": "https://evil.example.com/v1/emails"})
        client = Naijamail(TEST_KEY, base_url=api.base_url, max_retries=2)
        with self.assertRaises(ServerError) as caught:
            client.emails.send(**MINIMAL)
        self.assertEqual(caught.exception.message, "unexpected redirect")
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(len(api.requests), 1)


class NamespaceTest(unittest.TestCase):
    def test_star_import_does_not_shadow_builtins(self) -> None:
        namespace: dict = {}
        exec("from nc_email import *", namespace)
        for name in ("ConnectionError", "TimeoutError", "PermissionError"):
            self.assertNotIn(name, namespace)

    def test_the_short_spellings_are_still_importable(self) -> None:
        self.assertIs(nc_email.ConnectionError, nc_email.NaijamailConnectionError)
        self.assertIs(nc_email.TimeoutError, nc_email.NaijamailTimeoutError)
        self.assertIs(nc_email.PermissionError, nc_email.NaijamailPermissionError)

    def test_the_sdk_exposes_no_endpoints_the_server_lacks(self) -> None:
        # There is no domains, api-keys, batch or contacts resource server-side.
        client = Naijamail(TEST_KEY, base_url="https://api.example.com")
        for absent in ("domains", "api_keys", "batch", "contacts"):
            self.assertFalse(hasattr(client, absent))


if __name__ == "__main__":
    unittest.main()
