"""Everything the SDK refuses before a socket is opened.

Each test asserts `api.requests == []` as well as the exception: the point of
local validation is that the request never leaves the process, and a check that
merely raised *after* sending would still have put a bad message on the wire.
"""

from __future__ import annotations

import unittest

from _support import TEST_KEY, MockAPI

from nc_email import Naijamail, ValidationError


class ValidationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.api = MockAPI().start()
        self.addCleanup(self.api.stop)
        self.client = Naijamail(TEST_KEY, base_url=self.api.base_url, max_retries=0)

    def assertRejectedLocally(self, **params) -> ValidationError:  # type: ignore[no-untyped-def]
        with self.assertRaises(ValidationError) as caught:
            self.client.emails.send(**params)
        self.assertEqual(self.api.requests, [], "the request must never be sent")
        return caught.exception


class RequiredFieldsTest(ValidationTestCase):
    def test_from_is_required(self) -> None:
        self.assertIn('"from"', str(self.assertRejectedLocally(to="x@y.com")))

    def test_to_is_required(self) -> None:
        self.assertIn('"to"', str(self.assertRejectedLocally(from_="a@acme.com")))

    def test_empty_recipient_list_is_rejected(self) -> None:
        self.assertRejectedLocally(from_="a@acme.com", to=[])

    def test_empty_address_is_rejected(self) -> None:
        self.assertRejectedLocally(from_="a@acme.com", to=["   "])

    def test_non_string_recipient_is_rejected(self) -> None:
        self.assertRejectedLocally(from_="a@acme.com", to=[123])

    def test_conflicting_from_spellings(self) -> None:
        with self.assertRaises(ValidationError):
            self.client.emails.send({"from": "a@acme.com"}, from_="b@acme.com", to="x@y.com")
        self.assertEqual(self.api.requests, [])


class HeaderInjectionTest(ValidationTestCase):
    """SDK-CONTRACT.md section 5.5. CR/LF/NUL anywhere that becomes a header."""

    INJECTIONS = ["a@acme.com\r\nBcc: attacker@evil.com", "a@acme.com\nX: 1", "a@acme.com\x00"]

    def test_from(self) -> None:
        for value in self.INJECTIONS:
            with self.subTest(value=value):
                self.assertRejectedLocally(from_=value, to="x@y.com")

    def test_to_cc_bcc_reply_to(self) -> None:
        for field in ("to", "cc", "bcc", "reply_to"):
            with self.subTest(field=field):
                params = {"from_": "a@acme.com", "to": "x@y.com"}
                params[field] = "victim@y.com\r\nBcc: attacker@evil.com"
                self.assertRejectedLocally(**params)

    def test_subject(self) -> None:
        self.assertRejectedLocally(
            from_="a@acme.com", to="x@y.com", subject="Hi\r\nBcc: attacker@evil.com"
        )

    def test_header_name_and_value(self) -> None:
        self.assertRejectedLocally(
            from_="a@acme.com", to="x@y.com", headers={"X-A\r\nB": "1"}
        )
        self.assertRejectedLocally(
            from_="a@acme.com", to="x@y.com", headers={"X-A": "1\r\nBcc: attacker@evil.com"}
        )

    def test_attachment_filename(self) -> None:
        self.assertRejectedLocally(
            from_="a@acme.com",
            to="x@y.com",
            attachments=[{"filename": "in\r\nvoice.pdf", "content": b"x"}],
        )

    def test_idempotency_key(self) -> None:
        # This one becomes a header on our own request, not on the message.
        self.assertRejectedLocally(
            from_="a@acme.com", to="x@y.com", idempotency_key="k\r\nX-Evil: 1"
        )


class ForbiddenHeaderTest(ValidationTestCase):
    def test_each_forbidden_name(self) -> None:
        for name in ["from", "To", "CC", "bcc", "Subject", "DKIM-Signature", "received"]:
            with self.subTest(name=name):
                error = self.assertRejectedLocally(
                    from_="a@acme.com", to="x@y.com", headers={name: "spoofed"}
                )
                self.assertIn("cannot be overridden", str(error))

    def test_an_ordinary_header_is_allowed(self) -> None:
        self.api.enqueue_accepted()
        self.client.emails.send(
            from_="a@acme.com", to="x@y.com", headers={"X-Entity-Ref-ID": "abc"}
        )
        self.assertEqual(len(self.api.requests), 1)


class LimitsTest(ValidationTestCase):
    def test_recipient_ceiling_counts_to_cc_and_bcc_together(self) -> None:
        error = self.assertRejectedLocally(
            from_="a@acme.com",
            to=["a{}@y.com".format(i) for i in range(20)],
            cc=["b{}@y.com".format(i) for i in range(20)],
            bcc=["c{}@y.com".format(i) for i in range(20)],
        )
        self.assertIn("too many recipients", str(error))

    def test_fifty_recipients_is_allowed(self) -> None:
        self.api.enqueue_accepted()
        self.client.emails.send(
            from_="a@acme.com", to=["a{}@y.com".format(i) for i in range(50)]
        )
        self.assertEqual(len(self.api.requests), 1)

    def test_header_ceiling(self) -> None:
        self.assertRejectedLocally(
            from_="a@acme.com",
            to="x@y.com",
            headers={"X-{}".format(i): "v" for i in range(26)},
        )

    def test_tag_ceiling(self) -> None:
        self.assertRejectedLocally(
            from_="a@acme.com", to="x@y.com", tags={"t{}".format(i): "v" for i in range(11)}
        )

    def test_tag_key_and_value_length(self) -> None:
        self.assertRejectedLocally(from_="a@acme.com", to="x@y.com", tags={"k" * 65: "v"})
        self.assertRejectedLocally(from_="a@acme.com", to="x@y.com", tags={"k": "v" * 257})

    def test_payload_ceiling(self) -> None:
        error = self.assertRejectedLocally(
            from_="a@acme.com",
            to="x@y.com",
            attachments=[{"filename": "big.bin", "content": b"\x00" * (8 * 1024 * 1024)}],
        )
        self.assertIn("byte limit", str(error))


class AttachmentValidationTest(ValidationTestCase):
    def test_a_file_path_is_never_opened(self) -> None:
        error = self.assertRejectedLocally(
            from_="a@acme.com",
            to="x@y.com",
            attachments=[{"filename": "passwd", "path": "/etc/passwd"}],
        )
        self.assertIn("never opens files", str(error))

    def test_a_non_base64_string_is_rejected(self) -> None:
        error = self.assertRejectedLocally(
            from_="a@acme.com",
            to="x@y.com",
            attachments=[{"filename": "a.txt", "content": "!!!garbage!!!"}],
        )
        self.assertIn("base64", str(error))

    def test_empty_content_is_rejected(self) -> None:
        self.assertRejectedLocally(
            from_="a@acme.com", to="x@y.com", attachments=[{"filename": "a.txt", "content": b""}]
        )

    def test_missing_filename_is_rejected(self) -> None:
        self.assertRejectedLocally(
            from_="a@acme.com", to="x@y.com", attachments=[{"content": b"x"}]
        )

    def test_unsupported_content_type_is_rejected(self) -> None:
        self.assertRejectedLocally(
            from_="a@acme.com", to="x@y.com", attachments=[{"filename": "a", "content": 42}]
        )


class GetValidationTest(ValidationTestCase):
    def test_empty_id(self) -> None:
        with self.assertRaises(ValidationError):
            self.client.emails.get("")
        self.assertEqual(self.api.requests, [])

    def test_id_with_a_newline(self) -> None:
        with self.assertRaises(ValidationError):
            self.client.emails.get("abc\r\nX: 1")
        self.assertEqual(self.api.requests, [])


if __name__ == "__main__":
    unittest.main()
