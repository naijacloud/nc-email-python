"""POST /v1/emails — the wire format, the two calling styles, attachments."""

from __future__ import annotations

import base64
import unittest

from _support import TEST_KEY, MockAPI

from nc_email import Naijamail, RejectedRecipient, SendEmailResponse


class SendTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.api = MockAPI().start()
        self.addCleanup(self.api.stop)
        self.client = Naijamail(TEST_KEY, base_url=self.api.base_url, max_retries=0)

    @property
    def last(self):  # type: ignore[no-untyped-def]
        return self.api.requests[-1]


class SendTest(SendTestCase):
    def test_minimal_send(self) -> None:
        self.api.enqueue_accepted(message_id="msg-1")
        sent = self.client.emails.send(
            from_="Acme <hello@acme.com>",
            to="customer@example.com",
            subject="Hi",
            html="<p>Hi</p>",
        )

        self.assertIsInstance(sent, SendEmailResponse)
        self.assertEqual(sent.id, "msg-1")
        self.assertEqual(sent.status, "queued")
        self.assertEqual(sent.rejected, [])

        request = self.last
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.path, "/v1/emails")
        self.assertEqual(request.headers["authorization"], "Bearer " + TEST_KEY)
        self.assertEqual(request.headers["content-type"], "application/json")
        self.assertEqual(request.headers["accept"], "application/json")
        self.assertTrue(request.headers["user-agent"].startswith("nc-email-python/"))

        body = request.json
        self.assertEqual(body["from"], "Acme <hello@acme.com>")
        self.assertEqual(body["to"], ["customer@example.com"])
        self.assertEqual(body["subject"], "Hi")
        self.assertEqual(body["html"], "<p>Hi</p>")

    def test_dict_positional_form(self) -> None:
        # The shape someone porting from another SDK already has.
        self.api.enqueue_accepted()
        self.client.emails.send(
            {
                "from": "Acme <hello@acme.com>",
                "to": ["a@example.com", "b@example.com"],
                "subject": "Hi",
                "text": "Hi",
            }
        )
        body = self.last.json
        self.assertEqual(body["from"], "Acme <hello@acme.com>")
        self.assertEqual(body["to"], ["a@example.com", "b@example.com"])
        self.assertEqual(body["text"], "Hi")

    def test_keyword_overrides_the_dict(self) -> None:
        self.api.enqueue_accepted()
        self.client.emails.send({"from": "a@acme.com", "to": "x@example.com"}, subject="Later")
        self.assertEqual(self.last.json["subject"], "Later")

    def test_subject_is_always_sent(self) -> None:
        # The server defaults it to "", but a body whose shape depends on the
        # caller's arguments is one more thing to reproduce later.
        self.api.enqueue_accepted()
        self.client.emails.send(from_="a@acme.com", to="x@example.com")
        self.assertEqual(self.last.json["subject"], "")

    def test_recipient_shapes_are_normalised_to_lists(self) -> None:
        self.api.enqueue_accepted()
        self.client.emails.send(
            from_="a@acme.com",
            to="x@example.com",
            cc=["c@example.com"],
            bcc="b@example.com",
            reply_to="support@acme.com",
        )
        body = self.last.json
        self.assertEqual(body["to"], ["x@example.com"])
        self.assertEqual(body["cc"], ["c@example.com"])
        self.assertEqual(body["bcc"], ["b@example.com"])
        self.assertEqual(body["reply_to"], ["support@acme.com"])

    def test_reply_to_camel_case_is_accepted_and_sent_snake_case(self) -> None:
        self.api.enqueue_accepted()
        self.client.emails.send(from_="a@acme.com", to="x@example.com", replyTo="s@acme.com")
        body = self.last.json
        self.assertEqual(body["reply_to"], ["s@acme.com"])
        self.assertNotIn("replyTo", body)

    def test_optional_fields_are_omitted_when_unset(self) -> None:
        self.api.enqueue_accepted()
        self.client.emails.send(from_="a@acme.com", to="x@example.com")
        body = self.last.json
        for key in ("cc", "bcc", "reply_to", "html", "text", "headers", "attachments", "tags"):
            self.assertNotIn(key, body)

    def test_headers_and_tags(self) -> None:
        self.api.enqueue_accepted()
        self.client.emails.send(
            from_="a@acme.com",
            to="x@example.com",
            headers={"X-Entity-Ref-ID": "abc"},
            tags={"campaign": "invoices"},
        )
        body = self.last.json
        self.assertEqual(body["headers"], {"X-Entity-Ref-ID": "abc"})
        self.assertEqual(body["tags"], {"campaign": "invoices"})


class RejectedTest(SendTestCase):
    def test_rejected_is_passed_through(self) -> None:
        self.api.enqueue_accepted(
            rejected=[{"address": "x@y.com", "reason": "suppressed"}]
        )
        sent = self.client.emails.send(from_="a@acme.com", to=["x@y.com", "ok@y.com"])
        self.assertEqual(
            sent.rejected, [RejectedRecipient(address="x@y.com", reason="suppressed")]
        )

    def test_absent_rejected_becomes_an_empty_list(self) -> None:
        # The server omits the key entirely when it refused nobody. Callers must
        # never have to branch on absence.
        self.api.enqueue_accepted()
        sent = self.client.emails.send(from_="a@acme.com", to="x@y.com")
        self.assertEqual(sent.rejected, [])

    def test_rejected_is_not_an_error(self) -> None:
        self.api.enqueue_accepted(rejected=[{"address": "x@y.com", "reason": "suppressed"}])
        sent = self.client.emails.send(from_="a@acme.com", to="x@y.com")
        self.assertEqual(sent.status, "queued")


class UnknownFieldsTest(SendTestCase):
    def test_unknown_response_fields_are_ignored(self) -> None:
        # A field the platform adds next month must not break a pinned SDK.
        self.api.enqueue_accepted(
            extra={"scheduled_at": "2027-01-01T00:00:00Z", "region": "af-west"}
        )
        sent = self.client.emails.send(from_="a@acme.com", to="x@y.com")
        self.assertEqual(sent.status, "queued")

    def test_unknown_status_is_passed_through(self) -> None:
        self.api.enqueue_accepted(status="shredded")
        sent = self.client.emails.send(from_="a@acme.com", to="x@y.com")
        self.assertEqual(sent.status, "shredded")


class AttachmentTest(SendTestCase):
    def test_bytes_are_base64_encoded(self) -> None:
        self.api.enqueue_accepted()
        self.client.emails.send(
            from_="a@acme.com",
            to="x@y.com",
            attachments=[
                {
                    "filename": "invoice.pdf",
                    "content": b"%PDF-1.4\n",
                    "content_type": "application/pdf",
                }
            ],
        )
        attachment = self.last.json["attachments"][0]
        self.assertEqual(attachment["filename"], "invoice.pdf")
        self.assertEqual(attachment["content_type"], "application/pdf")
        self.assertEqual(base64.b64decode(attachment["content"]), b"%PDF-1.4\n")

    def test_bytearray_is_accepted(self) -> None:
        self.api.enqueue_accepted()
        self.client.emails.send(
            from_="a@acme.com",
            to="x@y.com",
            attachments=[{"filename": "a.bin", "content": bytearray(b"\x00\x01")}],
        )
        self.assertEqual(
            base64.b64decode(self.last.json["attachments"][0]["content"]), b"\x00\x01"
        )

    def test_already_encoded_base64_string_is_passed_through(self) -> None:
        self.api.enqueue_accepted()
        encoded = base64.b64encode(b"hello world").decode()
        self.client.emails.send(
            from_="a@acme.com",
            to="x@y.com",
            attachments=[{"filename": "a.txt", "content": encoded}],
        )
        self.assertEqual(self.last.json["attachments"][0]["content"], encoded)

    def test_content_id_camel_case(self) -> None:
        self.api.enqueue_accepted()
        self.client.emails.send(
            from_="a@acme.com",
            to="x@y.com",
            attachments=[{"filename": "l.png", "content": b"x", "contentId": "logo"}],
        )
        self.assertEqual(self.last.json["attachments"][0]["content_id"], "logo")


if __name__ == "__main__":
    unittest.main()
