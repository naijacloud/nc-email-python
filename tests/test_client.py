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
        for bad in ["", "sk_live_whatever", "nmail_live_", "nmail_live_short", "nmail_prod_abcdefgh"]:
            with self.subTest(key=bad):
                with self.assertRaises(ValidationError):
                    Naijamail(bad)

    def test_accepts_live_and_test_keys(self) -> None:
        self.assertTrue(Naijamail("nmail_live_abcdefgh").masked_api_key.startswith("nmail_live_"))
        self.assertTrue(Naijamail("nmail_test_abcdefgh").masked_api_key.startswith("nmail_test_"))

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
        for kwargs in [{"timeout": 0}, {"timeout": -1}, {"timeout": "30"}, {"max_retries": -1}, {"max_retries": 1.5}]:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValidationError):
                    Naijamail(TEST_KEY, **kwargs)  # type: ignore[arg-type]

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
