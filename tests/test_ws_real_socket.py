"""The receive connection against a real TCP socket and real websocket-client.

The other transport tests use fake sockets; they cannot catch a call that
does not actually unblock a thread waiting in recv. These do. Skipped where
websocket-client is not installed.
"""
import base64
import hashlib
import json
import socket
import threading
import time

import pytest

pytest.importorskip("websocket")

import shutdown  # noqa: E402
import signal_client  # noqa: E402
from signal_client import SignalClient, _WSConnection  # noqa: E402

SENDER = "42cc8f08-8b0a-4a79-a46d-000000000001"
GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


@pytest.fixture(autouse=True)
def _no_shutdown_state():
    shutdown.reset()
    yield
    shutdown.reset()


class _Server:
    """Minimal WebSocket server: handshake, send text frames on demand, and
    record when the client closes the TCP connection (EOF)."""

    def __init__(self):
        self._listen = socket.socket()
        self._listen.bind(("127.0.0.1", 0))
        self._listen.listen(1)
        self.port = self._listen.getsockname()[1]
        self.conn = None
        self.connected = threading.Event()
        self.eof_at = None
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        c, _ = self._listen.accept()
        data = b""
        while b"\r\n\r\n" not in data:
            data += c.recv(4096)
        key = next(line.split(b": ", 1)[1] for line in data.split(b"\r\n")
                   if line.lower().startswith(b"sec-websocket-key"))
        accept = base64.b64encode(hashlib.sha1(key + GUID).digest())
        c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                  b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
        self.conn = c
        self.connected.set()
        c.settimeout(30)
        try:
            while c.recv(4096):  # client frames (pings/close) until EOF
                pass
            self.eof_at = time.monotonic()
        except OSError:
            self.eof_at = time.monotonic()

    def send_text(self, text: str):
        payload = text.encode()
        assert len(payload) < 126
        self.conn.sendall(bytes([0x81, len(payload)]) + payload)

    def close(self):
        self._listen.close()


def test_abort_wakes_a_blocked_read_and_closes_the_connection():
    server = _Server()
    conn = _WSConnection(f"ws://127.0.0.1:{server.port}/", [], timeout=30)
    assert server.connected.wait(5)
    result = {}

    def reader():
        try:
            conn.read()
            result["outcome"] = "returned"
        except Exception as e:  # noqa: BLE001
            result["outcome"] = type(e).__name__
        result["at"] = time.monotonic()

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    time.sleep(0.3)  # reader is now blocked in recv
    aborted_at = time.monotonic()
    conn.abort()
    t.join(5)
    assert not t.is_alive(), "abort() did not wake the blocked read"
    assert result["outcome"] == "ConnectionError"
    assert result["at"] - aborted_at < 2
    deadline = time.monotonic() + 2
    while server.eof_at is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.eof_at is not None and server.eof_at - aborted_at < 2, "peer never saw the close"
    server.close()


def test_shutdown_is_quick_and_stops_taking_frames():
    """Full path: docker stop while handling a frame. The stream is closed at
    once (no 5 s wait for the reader), the frame goes to on_shutdown, and the
    server sees EOF so nothing more is sent and lost."""
    server = _Server()
    client = SignalClient(f"http://127.0.0.1:{server.port}", "+15550000001", "t" * 32)
    frame = json.dumps({"envelope": {"sourceUuid": SENDER, "dataMessage": {"message": "x"}}})
    drained = []

    def feed():
        assert server.connected.wait(5)
        server.send_text(frame)

    threading.Thread(target=feed, daemon=True).start()

    def handler(m):
        time.sleep(0.2)  # reader is back in recv, waiting for the next frame
        raise SystemExit(0)

    started = time.monotonic()
    with pytest.raises(SystemExit):
        client.receive_forever(handler, on_shutdown=drained.extend)
    took = time.monotonic() - started
    assert took < signal_client.READER_STOP_SECONDS - 2, f"shutdown stalled {took:.1f}s"
    assert len(drained) == 1  # the in-flight frame
    deadline = time.monotonic() + 2
    while server.eof_at is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.eof_at is not None
    server.close()
