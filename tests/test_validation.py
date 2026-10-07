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

    def test_a_padded_forbidden_name_is_still_refused(self) -> None:
        # The MIME composer trims header names, so " From" or "Bcc\t" would
        # otherwise land as the real header.
        for name in [" From", "bcc\t", " DKIM-Signature ", "Received "]:
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
            attachments=[{"filename": "big.bin", "content": b"\x00" * (10 * 1024 * 1024 + 1)}],
        )
        self.assertIn("byte limit", str(error))

    def test_size_is_measured_on_decoded_bytes_like_the_server(self) -> None:
        # 9 MiB of attachment is ~12 MiB of base64. The server measures the
        # decoded bytes, so this must go out; it used to be refused locally.
        self.api.enqueue_accepted()
        self.client.emails.send(
            from_="a@acme.com",
            to="x@y.com",
            text="hi",
            attachments=[{"filename": "big.bin", "content": b"\x00" * (9 * 1024 * 1024)}],
        )
        self.assertEqual(len(self.api.requests), 1)

    def test_exactly_ten_mib_is_allowed_and_html_text_count_as_utf8(self) -> None:
        limit = 10 * 1024 * 1024
        # "é" is two bytes of UTF-8: html + text + attachment = limit exactly.
        html = "é" * 10
        attachment = b"\x00" * (limit - 20 - 1)
        self.api.enqueue_accepted()
        self.client.emails.send(
            from_="a@acme.com", to="x@y.com", html=html, text="a",
            attachments=[{"filename": "a.bin", "content": attachment}],
        )
        self.assertEqual(len(self.api.requests), 1)
        self.api.requests.clear()
        self.assertRejectedLocally(
            from_="a@acme.com", to="x@y.com", html=html, text="ab",
            attachments=[{"filename": "a.bin", "content": attachment}],
        )

    def test_a_base64_string_attachment_counts_its_decoded_size(self) -> None:
        import base64

        encoded = base64.b64encode(b"\x00" * (10 * 1024 * 1024 + 1)).decode()
        self.assertRejectedLocally(
            from_="a@acme.com", to="x@y.com",
            attachments=[{"filename": "a.bin", "content": encoded}],
        )

    def test_a_lone_surrogate_is_a_validation_error(self) -> None:
        self.assertRejectedLocally(from_="a@acme.com", to="x@y.com", text="half \ud83d emoji")


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
        for empty in (b"", bytearray(), memoryview(b""), "", "  "):
            with self.subTest(content=empty):
                self.assertRejectedLocally(
                    from_="a@acme.com", to="x@y.com",
                    attachments=[{"filename": "a.txt", "content": empty}],
                )

    def test_a_memoryview_is_raw_bytes(self) -> None:
        self.api.enqueue_accepted()
        self.client.emails.send(
            from_="a@acme.com", to="x@y.com", text="hi",
            attachments=[{"filename": "a.txt", "content": memoryview(b"hello")}],
        )
        self.assertEqual(self.api.requests[-1].json["attachments"][0]["content"], "aGVsbG8=")

    def test_content_type_and_content_id_reject_line_breaks(self) -> None:
        for field in ("content_type", "content_id"):
            with self.subTest(field=field):
                self.assertRejectedLocally(
                    from_="a@acme.com", to="x@y.com",
                    attachments=[{"filename": "a", "content": b"x", field: "a\r\nBcc: v@x.com"}],
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


class UnknownParamsTest(ValidationTestCase):
    def test_a_misspelt_parameter_is_refused_not_dropped(self) -> None:
        # `htlm=` used to vanish silently and the message went out with no body.
        err = self.assertRejectedLocally(
            from_="a@acme.com", to="x@y.com", subject="Hi", htlm="<p>hi</p>"
        )
        self.assertIn('"htlm"', str(err))
        self.assertIn('did you mean "html"', str(err))

    def test_unknown_keys_in_the_dict_form_are_refused_too(self) -> None:
        with self.assertRaises(ValidationError):
            self.client.emails.send({"from": "a@acme.com", "to": "x@y.com", "replyto": "r@acme.com"})
        self.assertEqual(self.api.requests, [])


class TagLengthUnitsTest(ValidationTestCase):
    def test_tag_length_is_counted_like_the_server_counts_it(self) -> None:
        # The server truncates at 64/256 JavaScript (UTF-16) units. An emoji is
        # two of those, so 33 emoji is 66 units: over the key limit.
        self.assertRejectedLocally(
            from_="a@acme.com", to="x@y.com", subject="Hi", text="x", tags={"\U0001F600" * 33: "v"}
        )
        self.assertRejectedLocally(
            from_="a@acme.com", to="x@y.com", subject="Hi", text="x", tags={"k": "\U0001F600" * 129}
        )

    def test_accented_tags_within_the_limit_still_pass(self) -> None:
        self.api.enqueue_json(202, {"id": "1", "status": "queued"})
        self.client.emails.send(
            from_="a@acme.com", to="x@y.com", subject="Hi", text="x", tags={"ọ̀" * 30: "é" * 250}
        )
        self.assertEqual(len(self.api.requests), 1)
