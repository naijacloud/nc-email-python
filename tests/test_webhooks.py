"""Webhook signature verification — SDK-CONTRACT.md section 6."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import unittest

from _support import TEST_KEY  # noqa: F401 - puts src/ on sys.path

from nc_email import ValidationError, WebhookEvent, Webhooks, WebhookVerificationError

SECRET = "nmail_whsec_0000000000000000000000"
BODY = json.dumps(
    {
        "id": "evt_1",
        "type": "email.delivered",
        "created_at": "2026-08-29T10:00:04.000Z",
        "data": {"email_id": "5b1e", "to": "x@y.com"},
    }
).encode("utf-8")


def sign(body: bytes, secret: str = SECRET, timestamp: int = None) -> str:  # type: ignore[assignment]
    when = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(
        secret.encode(), str(when).encode("ascii") + b"." + body, hashlib.sha256
    ).hexdigest()
    return "t={},v1={}".format(when, digest)


class VerifyTest(unittest.TestCase):
    def test_accepts_a_good_signature(self) -> None:
        event = Webhooks.verify(BODY, sign(BODY), SECRET)
        self.assertIsInstance(event, WebhookEvent)
        self.assertEqual(event.type, "email.delivered")
        self.assertEqual(event.id, "evt_1")
        self.assertEqual(event.data["email_id"], "5b1e")
        self.assertEqual(event.raw["created_at"], "2026-08-29T10:00:04.000Z")

    def test_accepts_a_str_payload(self) -> None:
        text = BODY.decode()
        self.assertEqual(Webhooks.verify(text, sign(BODY), SECRET).type, "email.delivered")

    def test_accepts_any_of_several_signatures(self) -> None:
        # Both secrets are live during a rotation, so both signatures arrive.
        when = int(time.time())
        old = hmac.new(
            b"nmail_whsec_old", str(when).encode() + b"." + BODY, hashlib.sha256
        ).hexdigest()
        new = hmac.new(
            SECRET.encode(), str(when).encode() + b"." + BODY, hashlib.sha256
        ).hexdigest()
        header = "t={},v1={},v1={}".format(when, old, new)
        self.assertEqual(Webhooks.verify(BODY, header, SECRET).type, "email.delivered")

    def test_rejects_a_bad_signature(self) -> None:
        header = "t={},v1={}".format(int(time.time()), "0" * 64)
        with self.assertRaises(WebhookVerificationError):
            Webhooks.verify(BODY, header, SECRET)

    def test_rejects_a_signature_from_another_secret(self) -> None:
        with self.assertRaises(WebhookVerificationError):
            Webhooks.verify(BODY, sign(BODY, secret="nmail_whsec_wrong"), SECRET)

    def test_rejects_a_tampered_body(self) -> None:
        header = sign(BODY)
        tampered = BODY.replace(b"x@y.com", b"z@y.com")
        with self.assertRaises(WebhookVerificationError):
            Webhooks.verify(tampered, header, SECRET)

    def test_rejects_a_stale_timestamp(self) -> None:
        old = int(time.time()) - 3600
        with self.assertRaises(WebhookVerificationError) as caught:
            Webhooks.verify(BODY, sign(BODY, timestamp=old), SECRET)
        self.assertIn("tolerance", str(caught.exception))

    def test_rejects_a_timestamp_far_in_the_future(self) -> None:
        ahead = int(time.time()) + 3600
        with self.assertRaises(WebhookVerificationError):
            Webhooks.verify(BODY, sign(BODY, timestamp=ahead), SECRET)

    def test_tolerance_is_configurable(self) -> None:
        old = int(time.time()) - 600
        with self.assertRaises(WebhookVerificationError):
            Webhooks.verify(BODY, sign(BODY, timestamp=old), SECRET)
        self.assertEqual(
            Webhooks.verify(BODY, sign(BODY, timestamp=old), SECRET, tolerance=900).type,
            "email.delivered",
        )

    def test_rejects_a_malformed_header(self) -> None:
        for header in ["", "garbage", "t=abc,v1=deadbeef", "v1=deadbeef", "t=123"]:
            with self.subTest(header=header):
                with self.assertRaises(WebhookVerificationError):
                    Webhooks.verify(BODY, header, SECRET)

    def test_rejects_a_missing_header(self) -> None:
        with self.assertRaises(WebhookVerificationError):
            Webhooks.verify(BODY, None, SECRET)

    def test_the_error_never_reveals_the_expected_signature(self) -> None:
        when = int(time.time())
        expected = hmac.new(
            SECRET.encode(), str(when).encode() + b"." + BODY, hashlib.sha256
        ).hexdigest()
        with self.assertRaises(WebhookVerificationError) as caught:
            Webhooks.verify(BODY, "t={},v1={}".format(when, "0" * 64), SECRET)
        text = str(caught.exception) + repr(caught.exception)
        self.assertNotIn(expected, text)
        self.assertNotIn(SECRET, text)

    def test_a_parsed_object_is_refused(self) -> None:
        # Re-serialising differs from the signed bytes by key order and spacing,
        # so accepting one would fail at random and invite someone to disable
        # the check.
        with self.assertRaises(ValidationError):
            Webhooks.verify({"type": "email.delivered"}, sign(BODY), SECRET)  # type: ignore[arg-type]

    def test_an_api_key_used_as_the_secret_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            Webhooks.verify(BODY, sign(BODY), TEST_KEY)

    def test_an_empty_secret_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            Webhooks.verify(BODY, sign(BODY), "")

    def test_a_verified_payload_that_is_not_json(self) -> None:
        body = b"not json"
        with self.assertRaises(WebhookVerificationError):
            Webhooks.verify(body, sign(body), SECRET)

    def test_a_verified_payload_that_is_not_an_object(self) -> None:
        for body in (b"[1,2]", b'"text"', b"42", b"null"):
            with self.subTest(body=body):
                with self.assertRaises(WebhookVerificationError):
                    Webhooks.verify(body, sign(body), SECRET)

    def test_an_upper_case_signature_is_accepted(self) -> None:
        header = sign(BODY)
        timestamp, signature = header.split(",")
        self.assertEqual(
            Webhooks.verify(BODY, timestamp + "," + signature.upper().replace("V1=", "v1="),
                            SECRET).type,
            "email.delivered",
        )

    def test_a_timestamp_must_be_plain_ascii_digits(self) -> None:
        now = int(time.time())
        digest = sign(BODY).split(",")[1]
        for t in ("+{}".format(now), " {}x".format(now),
                  "{}".format(now).replace("1", "\u0661"), "-{}".format(now), "1e9",
                  "{}.0".format(now), ""):
            with self.subTest(t=t):
                with self.assertRaises(WebhookVerificationError):
                    Webhooks.verify(BODY, "t={},{}".format(t, digest), SECRET)

    def test_an_underscored_timestamp_is_refused_even_when_signed(self) -> None:
        # int("1_756_468_800") == 1756468800; the signed string would differ from
        # what int() normalised to, but the parser must refuse it outright.
        now = str(int(time.time()))
        underscored = now[:4] + "_" + now[4:]
        with self.assertRaises(WebhookVerificationError):
            Webhooks.verify(BODY, "t={},v1={}".format(underscored, "0" * 64), SECRET)

    def test_a_huge_timestamp_is_refused_cleanly(self) -> None:
        for t in ("9" * 13, "9" * 400):
            with self.subTest(length=len(t)):
                with self.assertRaises(WebhookVerificationError):
                    Webhooks.verify(BODY, "t={},v1={}".format(t, "0" * 64), SECRET)

    def test_twelve_digits_parse_but_are_outside_tolerance(self) -> None:
        with self.assertRaises(WebhookVerificationError) as caught:
            Webhooks.verify(BODY, "t={},v1={}".format("9" * 12, "0" * 64), SECRET)
        self.assertIn("tolerance", str(caught.exception))

    def test_unknown_event_fields_are_ignored(self) -> None:
        body = json.dumps({"type": "email.opened", "id": "e", "surprise": 1}).encode()
        event = Webhooks.verify(body, sign(body), SECRET)
        self.assertEqual(event.type, "email.opened")
        self.assertEqual(event.raw["surprise"], 1)

    def test_module_level_alias(self) -> None:
        from nc_email import verify_webhook

        self.assertEqual(verify_webhook(BODY, sign(BODY), SECRET).type, "email.delivered")


if __name__ == "__main__":
    unittest.main()


class ToleranceTest(unittest.TestCase):
    def test_a_nan_tolerance_is_refused_rather_than_disabling_replay_protection(self) -> None:
        # `drift > nan` is always False, so a NaN tolerance (from
        # float(os.environ.get(...)) on a typo) accepted any replayed event.
        stale = sign(BODY, timestamp=int(time.time()) - 86400)
        for bad in (float("nan"), float("inf"), -1):
            with self.assertRaises(ValidationError):
                Webhooks.verify(BODY, stale, SECRET, tolerance=bad)

    def test_a_zero_tolerance_is_strict_not_the_default(self) -> None:
        with self.assertRaises(WebhookVerificationError):
            Webhooks.verify(BODY, sign(BODY, timestamp=int(time.time()) - 5), SECRET, tolerance=0)
        # A signature from this very second still passes (retry once if the
        # clock ticked between signing and verifying).
        for _ in range(2):
            try:
                Webhooks.verify(BODY, sign(BODY), SECRET, tolerance=0)
                break
            except WebhookVerificationError:
                continue
        else:
            self.fail("a current signature was refused with tolerance=0")
