"""Construction: key handling, base-URL policy, options."""

from __future__ import annotations

import os
import pickle
import unittest

from _support import TEST_KEY  # noqa: F401 - also puts src/ on sys.path

import nc_email
from nc_email import Naijamail, ValidationError


class ConstructionTest(unittest.TestCase):
    def test_reads_the_key_from_the_environment(self) -> None:
        previous = os.environ.get("NAIJAMAIL_API_KEY")
        os.environ["NAIJAMAIL_API_KEY"] = TEST_KEY
        try:
            client = Naijamail()
            self.assertEqual(client.masked_api_key, "nmail_live_***")
        finally:
            if previous is None:
                del os.environ["NAIJAMAIL_API_KEY"]
            else:
                os.environ["NAIJAMAIL_API_KEY"] = previous

    def test_missing_key_names_the_environment_variable(self) -> None:
        previous = os.environ.pop("NAIJAMAIL_API_KEY", None)
        try:
            with self.assertRaises(ValidationError) as caught:
                Naijamail()
            self.assertIn("NAIJAMAIL_API_KEY", str(caught.exception))
        finally:
            if previous is not None:
                os.environ["NAIJAMAIL_API_KEY"] = previous

    def test_rejects_a_key_of_the_wrong_shape(self) -> None:
        bad_keys = [
            "",
            "sk_live_whatever",
            "nmail_live_",
            "nmail_live_short",
            "nmail_prod_abcdefgh",
            # There is no test variant of a workspace key; the live/test split
            # belongs to the nmail_ family.
            "nc_test_0123456789abcdef",
        ]
        for bad in bad_keys:
            with self.subTest(key=bad):
                with self.assertRaises(ValidationError):
                    Naijamail(bad)

    def test_a_personal_access_token_gets_the_contract_message(self) -> None:
        # The pre-scopes platform token: the API refuses it on the mail routes,
        # so it fails here — and says why, in the wording all five SDKs share.
        for key in ("nc_pat_0000000000000000", "  nc_pat_0000000000000000\n", "nc_pat_x"):
            with self.subTest(key=key):
                with self.assertRaises(ValidationError) as caught:
                    Naijamail(key)
                self.assertEqual(
                    str(caught.exception),
                    "this is a personal access token (nc_pat_…), which cannot send mail; "
                    "use a mail API key (nmail_live_… or nmail_test_…) or a workspace API "
                    "key with the Email send scope (nc_live_…)",
                )
                self.assertNotIn("0000000000000000", str(caught.exception))

    def test_accepts_live_and_test_keys(self) -> None:
        self.assertTrue(Naijamail("nmail_live_abcdefgh").masked_api_key.startswith("nmail_live_"))
        self.assertTrue(Naijamail("nmail_test_abcdefgh").masked_api_key.startswith("nmail_test_"))

    def test_accepts_a_workspace_api_key(self) -> None:
        """A key from Settings -> API keys, carrying the Email send scope."""
        client = Naijamail("nc_live_0123456789abcdefghij")
        self.assertEqual(client.masked_api_key, "nc_live_***")

    def test_default_base_url(self) -> None:
        self.assertEqual(Naijamail(TEST_KEY).base_url, "https://api.naijacloud.com")

    def test_base_url_from_the_environment(self) -> None:
        previous = os.environ.get("NAIJAMAIL_BASE_URL")
        os.environ["NAIJAMAIL_BASE_URL"] = "https://staging.example.com/"
        try:
            self.assertEqual(Naijamail(TEST_KEY).base_url, "https://staging.example.com")
        finally:
            if previous is None:
                del os.environ["NAIJAMAIL_BASE_URL"]
            else:
                os.environ["NAIJAMAIL_BASE_URL"] = previous

    def test_a_blank_base_url_variable_means_unset(self) -> None:
        previous = os.environ.get("NAIJAMAIL_BASE_URL")
        try:
            for blank in ("", "   "):
                with self.subTest(value=blank):
                    os.environ["NAIJAMAIL_BASE_URL"] = blank
                    self.assertEqual(Naijamail(TEST_KEY).base_url, "https://api.naijacloud.com")
        finally:
            if previous is None:
                os.environ.pop("NAIJAMAIL_BASE_URL", None)
            else:
                os.environ["NAIJAMAIL_BASE_URL"] = previous

    def test_a_base_url_with_a_query_or_fragment_is_refused(self) -> None:
        for url in (
            "https://api.example.com/?region=eu",
            "https://api.example.com?",
            "https://api.example.com/#x",
            "http://localhost:4000/?a=b",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValidationError) as caught:
                    Naijamail(TEST_KEY, base_url=url)
                self.assertIn("query string", str(caught.exception))

    def test_trailing_slash_is_trimmed(self) -> None:
        # Otherwise every request path becomes //v1/emails.
        self.assertEqual(
            Naijamail(TEST_KEY, base_url="https://api.example.com/").base_url,
            "https://api.example.com",
        )

    def test_rejects_a_relative_base_url(self) -> None:
        with self.assertRaises(ValidationError):
            Naijamail(TEST_KEY, base_url="api.naijacloud.com")

    def test_rejects_bad_options(self) -> None:
        for kwargs in [{"timeout": 0}, {"timeout": -1}, {"timeout": "30"}, {"timeout": float("nan")}, {"timeout": float("inf")}, {"max_retries": -1}, {"max_retries": 1.5}, {"max_retries": 11}]:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValidationError):
                    Naijamail(TEST_KEY, **kwargs)  # type: ignore[arg-type]

    def test_max_retries_of_ten_is_the_ceiling(self) -> None:
        self.assertEqual(Naijamail(TEST_KEY, max_retries=10).max_retries, 10)

    def test_user_agent_shape(self) -> None:
        agent = Naijamail(TEST_KEY).user_agent
        self.assertTrue(agent.startswith("nc-email-python/" + nc_email.__version__))
        self.assertIn("(python/", agent)

    def test_user_agent_suffix(self) -> None:
        agent = Naijamail(TEST_KEY, user_agent_suffix="acme-billing/2.1").user_agent
        self.assertTrue(agent.endswith(" acme-billing/2.1"))

    def test_user_agent_suffix_rejects_newlines(self) -> None:
        with self.assertRaises(ValidationError):
            Naijamail(TEST_KEY, user_agent_suffix="acme\r\nX-Evil: 1")

    def test_user_agent_suffix_rejects_the_key(self) -> None:
        with self.assertRaises(ValidationError):
            Naijamail(TEST_KEY, user_agent_suffix="app " + TEST_KEY)

    def test_two_clients_do_not_share_state(self) -> None:
        first = Naijamail("nmail_live_aaaaaaaa", base_url="https://one.example.com")
        second = Naijamail("nmail_test_bbbbbbbb", base_url="https://two.example.com")
        self.assertEqual(first.base_url, "https://one.example.com")
        self.assertEqual(second.base_url, "https://two.example.com")
        self.assertNotEqual(first.masked_api_key, second.masked_api_key)
        self.assertIsNot(first.emails, second.emails)

    def test_client_cannot_be_pickled(self) -> None:
        with self.assertRaises(TypeError):
            pickle.dumps(Naijamail(TEST_KEY))


if __name__ == "__main__":
    unittest.main()


class KeyWhitespaceTest(unittest.TestCase):
    def test_a_trailing_newline_is_trimmed_not_sent(self) -> None:
        # `$` in a regex also matches before a final "\n", so a key read from a
        # file or a CI secret with a newline used to pass validation, reach
        # http.client, and surface as a raw ValueError quoting the whole key.
        from _support import MockAPI

        api = MockAPI().start()
        self.addCleanup(api.stop)
        api.enqueue_json(200, {"id": "1", "to": "x@y.com", "from": "a@acme.com",
                               "subject": "", "status": "queued",
                               "created_at": "2026-08-29T10:00:00.000Z",
                               "opened": False, "clicked": False})
        client = Naijamail(TEST_KEY + "\n", base_url=api.base_url, max_retries=0)
        client.emails.get("1")
        self.assertEqual(api.requests[-1].headers["authorization"], "Bearer " + TEST_KEY)

    def test_a_key_with_an_inner_newline_is_refused_without_quoting_it(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            Naijamail(TEST_KEY + "\nX-Evil: 1")
        self.assertNotIn(TEST_KEY, str(caught.exception))

    def test_a_whitespace_only_key_names_the_environment_variable(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            Naijamail("  \n")
        self.assertIn("NAIJAMAIL_API_KEY", str(caught.exception))
