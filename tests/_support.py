"""A local mock of the Naijamail API, plus the sys.path shim.

The whole suite runs against a `ThreadingHTTPServer` on 127.0.0.1:0 — a real
socket, a real HTTP parse, real headers. Nothing here monkeypatches urllib: the
redirect handler, the TLS context wiring and the timeout plumbing are exactly
the parts most likely to break, and a test that stubs the transport out would
not notice any of them. It also means the suite passes with no network.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional

# Lets the suite run straight from a checkout as well as against an installed
# wheel. An installed nc_email is preferred and src/ is only added when there is
# none, so CI tests what it is about to publish rather than what is on disk.
if importlib.util.find_spec("nc_email") is None:
    _SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
    if _SRC not in sys.path:
        sys.path.insert(0, _SRC)

#: The literal test key from SDK-CONTRACT.md section 8. Never a real one.
TEST_KEY = "nmail_live_test0000000000000000"
TEST_KEY_TEST_MODE = "nmail_test_test0000000000000000"


class RecordedRequest:
    __slots__ = ("method", "path", "headers", "body")

    def __init__(self, method: str, path: str, headers: Dict[str, str], body: bytes) -> None:
        self.method = method
        self.path = path
        self.headers = headers
        self.body = body

    @property
    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body else None

    def __repr__(self) -> str:
        return "<RecordedRequest {} {}>".format(self.method, self.path)


class CannedResponse:
    __slots__ = ("status", "body", "headers", "delay")

    def __init__(
        self,
        status: int,
        body: bytes = b"",
        headers: Optional[Dict[str, str]] = None,
        delay: float = 0.0,
    ) -> None:
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.delay = delay


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "MockNaijamail/1.0"

    def _respond(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        api: MockAPI = self.server.api  # type: ignore[attr-defined]
        api.record(
            RecordedRequest(
                self.command,
                self.path,
                {key.lower(): value for key, value in self.headers.items()},
                body,
            )
        )
        canned = api.take()
        if canned.delay:
            time.sleep(canned.delay)
        self.send_response(canned.status)
        for key, value in canned.headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(canned.body)))
        self.end_headers()
        if canned.body:
            self.wfile.write(canned.body)

    do_GET = _respond
    do_POST = _respond
    do_PUT = _respond
    do_DELETE = _respond

    def log_message(self, fmt: str, *args: Any) -> None:
        # Silence: the suite deliberately provokes timeouts and aborted
        # connections, and stderr noise from those reads as a failing test.
        return


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        # The timeout tests hang up mid-response on purpose; the resulting
        # BrokenPipeError in the handler thread is the expected outcome.
        return


class MockAPI:
    """Scripted responses, in the order they were enqueued.

    A test enqueues what it wants the server to say, makes the call, then reads
    `requests` to assert on what actually went over the socket — which is the
    only place the header names, the JSON shape and the retry behaviour can be
    checked for real.
    """

    def __init__(self) -> None:
        self.requests: List[RecordedRequest] = []
        self._queue: List[CannedResponse] = []
        self._lock = threading.Lock()
        self._server: Optional[_Server] = None
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> MockAPI:
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.api = self  # type: ignore[attr-defined]
        # A short poll interval because `shutdown()` waits for the loop to
        # notice: the 0.5s default adds half a second to every test.
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    @property
    def port(self) -> int:
        assert self._server is not None, "the mock server is not running"
        return self._server.server_address[1]

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:{}".format(self.port)

    # -- scripting ---------------------------------------------------------
    def enqueue(self, response: CannedResponse) -> CannedResponse:
        with self._lock:
            self._queue.append(response)
        return response

    def enqueue_json(
        self,
        status: int,
        payload: Any,
        headers: Optional[Dict[str, str]] = None,
        delay: float = 0.0,
    ) -> CannedResponse:
        merged = {"Content-Type": "application/json"}
        merged.update(headers or {})
        return self.enqueue(
            CannedResponse(status, json.dumps(payload).encode("utf-8"), merged, delay)
        )

    def enqueue_raw(
        self,
        status: int,
        body: bytes = b"",
        headers: Optional[Dict[str, str]] = None,
        delay: float = 0.0,
    ) -> CannedResponse:
        return self.enqueue(CannedResponse(status, body, headers, delay))

    def enqueue_error(
        self,
        status: int,
        message: Any,
        error: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> CannedResponse:
        payload: Dict[str, Any] = {"statusCode": status, "message": message}
        if error is not None:
            payload["error"] = error
        return self.enqueue_json(status, payload, headers)

    def enqueue_accepted(
        self,
        message_id: str = "5b1e0000-0000-4000-8000-000000000001",
        status: str = "queued",
        rejected: Optional[List[Dict[str, str]]] = None,
        extra: Optional[Dict[str, Any]] = None,
        delay: float = 0.0,
    ) -> CannedResponse:
        payload: Dict[str, Any] = {"id": message_id, "status": status}
        if rejected:
            payload["rejected"] = rejected
        if extra:
            payload.update(extra)
        return self.enqueue_json(202, payload, delay=delay)

    # -- server side -------------------------------------------------------
    def record(self, request: RecordedRequest) -> None:
        with self._lock:
            self.requests.append(request)

    def take(self) -> CannedResponse:
        with self._lock:
            if self._queue:
                return self._queue.pop(0)
        # A test that under-scripted its server should fail loudly rather than
        # hang or accidentally exercise the retry path.
        return CannedResponse(
            500,
            json.dumps({"statusCode": 500, "message": "mock server had no canned response"}).encode(),
            {"Content-Type": "application/json"},
        )


def reserve_closed_port() -> int:
    """A port nothing is listening on, for the connection-refused test."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port
