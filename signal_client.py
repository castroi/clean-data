"""Thin transport to Signal through this bot's own signal-wrapper.

All Signal I/O lives here. The wrapper (https://github.com/castroi/signal-wrapper)
pins the sender and the receive stream to this bot's number, so this client
never sends a ``number`` field. It needs the wrapper running with
``ALLOW_ATTACHMENTS=true`` so the bot can download, delete and send files.

``session`` (requests-compatible) and ``ws_connect`` are injectable so the
client unit-tests without a live server.
"""

import base64
import json
import logging
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import requests

import shutdown

logger = logging.getLogger(__name__)

# Must outlast the wrapper's own deadline for a file send (UPSTREAM_TIMEOUT_SEC=120
# in docker-compose.yml), or the bot gives up on a send the wrapper still completes.
HTTP_TIMEOUT_SECONDS = 150
RECONNECT_MIN_SECONDS = 1
RECONNECT_MAX_SECONDS = 30
# Keepalive on the receive WebSocket: ping after this many idle seconds, and
# reconnect if nothing (not even a pong) arrives for MAX_MISSED_PINGS pings.
PING_INTERVAL_SECONDS = 30
MAX_MISSED_PINGS = 2
# Frames waiting for the (slow, sequential) document processor. Frames are
# small (ids, never file contents); when full, the reader waits.
RECEIVE_QUEUE_SIZE = 1000
DELETE_ATTEMPTS = 3
# Deleting a local file through the wrapper is quick; keep any single attempt
# short so one slow delete cannot use up docker's stop grace period.
DELETE_TIMEOUT_SECONDS = 15
SHUTDOWN_DELETE_TIMEOUT_SECONDS = 5.0
# How long shutdown waits for the reader thread to finish after closing the
# connection (it can only be blocked handing over a frame already read).
READER_STOP_SECONDS = 5.0

_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


class WSTimeout(Exception):
    """No frame arrived within the read timeout (connection may still be fine)."""


class _WSConnection:
    """Adapter over websocket-client exposing the small surface the client uses.

    read() returns the next text frame, returns None for control frames (pong)
    and raises WSTimeout when idle or ConnectionError when the stream closed.
    websocket-client answers the server's pings itself while reading.
    """

    def __init__(self, url: str, headers: list[str], timeout: float) -> None:
        # Imported lazily so unit tests don't need the package installed.
        import websocket

        self._websocket = websocket
        self._ws = websocket.create_connection(url, header=headers, timeout=timeout)

    def read(self) -> str | None:
        ws = self._websocket
        try:
            opcode, frame = self._ws.recv_data_frame(True)
        except ws.WebSocketTimeoutException as e:
            raise WSTimeout() from e
        except ws.WebSocketConnectionClosedException as e:
            raise ConnectionError("receive stream closed") from e
        if opcode == ws.ABNF.OPCODE_CLOSE:
            raise ConnectionError("receive stream closed")
        if opcode in (ws.ABNF.OPCODE_TEXT, ws.ABNF.OPCODE_BINARY):
            return frame.data.decode("utf-8", "replace") if isinstance(frame.data, bytes) else frame.data
        return None  # ping/pong/continuation handled by the library

    def ping(self) -> None:
        self._ws.ping()

    def close(self) -> None:
        self._ws.close()

    def abort(self) -> None:
        """Stop the connection at once, from another thread, without a close
        handshake. Unblocks a read() in progress on the reader thread.

        websocket-client's abort() does socket.shutdown(SHUT_RDWR): a blocked
        recv returns at once and the peer sees EOF, so signal-cli stops sending.
        Its shutdown() only closes the file descriptor, which does NOT wake a
        blocked recv and leaves the TCP connection open, so frames could still
        arrive and be lost. The reader closes the socket itself afterwards.
        """
        ws_abort = getattr(self._ws, "abort", None)
        if ws_abort is not None:
            try:
                ws_abort()
                return
            except OSError:
                pass  # e.g. already disconnected; fall through to closing it
        shutdown_socket = getattr(self._ws, "shutdown", None)
        if shutdown_socket is not None:
            shutdown_socket()
        else:
            self._ws.close()


def _default_ws_connect(url: str, headers: list[str]) -> _WSConnection:
    return _WSConnection(url, headers, timeout=PING_INTERVAL_SECONDS)


def to_data_uri(filename: str, content: bytes) -> str:
    """Encode a file as the data URI that bbernhard/the wrapper accept."""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    mime = _MIME_BY_EXT.get(ext, "application/octet-stream")
    data = base64.b64encode(content).decode("ascii")
    return f"data:{mime};filename={filename};base64,{data}"


def parse_frame(raw: str | bytes) -> dict | None:
    """Return ``{"envelope": ...}`` from a receive frame, or None to skip.

    Accepts bbernhard's plain form ``{envelope, account}`` and the json-rpc
    form ``{method: "receive", params: {envelope, ...}}``.
    """
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict):
        return None
    if isinstance(msg.get("params"), dict):
        msg = msg["params"]
    if not isinstance(msg.get("envelope"), dict):
        return None
    return msg


class SignalClient:
    def __init__(
        self,
        base_url: str,
        number: str,
        token: str,
        session: Any = None,
        ws_connect: Callable[[str, list[str]], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        heartbeat_path: Path | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._number = number
        self._token = token
        self._session = session or requests.Session()
        self._ws_connect = ws_connect or _default_ws_connect
        self._sleep = sleep
        self._heartbeat_path = heartbeat_path
        self._current_ws: Any = None  # the reader's open connection, for abort()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def send_message(
        self,
        message: str,
        recipients: list[str],
        attachments: list[tuple[str, bytes]] | None = None,
    ) -> None:
        """POST /v2/send. ``attachments`` is a list of (filename, bytes)."""
        body: dict[str, Any] = {"message": message, "recipients": recipients}
        if attachments:
            body["base64_attachments"] = [to_data_uri(n, c) for n, c in attachments]
        resp = self._session.post(
            f"{self._base}/v2/send",
            json=body,
            headers=self._headers(),
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()

    def get_attachment(self, attachment_id: str, max_bytes: int) -> bytes:
        """Download a received attachment, reading at most ``max_bytes + 1``.

        Returning one byte over the limit lets the caller reject oversize files
        without buffering an arbitrarily large body.
        """
        resp = self._session.get(
            f"{self._base}/v1/attachments/{quote(attachment_id, safe='')}",
            headers=self._headers(),
            timeout=HTTP_TIMEOUT_SECONDS,
            stream=True,
        )
        try:
            resp.raise_for_status()
            buf = bytearray()
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    break
            return bytes(buf)
        finally:
            resp.close()

    def delete_attachment(self, attachment_id: str) -> None:
        """Remove a received attachment from signal-cli storage.

        Retries transient failures; a 404 counts as success (already gone).
        Raises after DELETE_ATTEMPTS failed attempts.

        Once shutdown has started (checked before every attempt, so this also
        applies to a delete already running when SIGTERM arrives): no more
        retries or backoff, each attempt at most SHUTDOWN_DELETE_TIMEOUT_SECONDS
        and never past the shutdown budget (raises TimeoutError when spent).
        """
        url = f"{self._base}/v1/attachments/{quote(attachment_id, safe='')}"
        attempt = 0
        while True:
            attempt += 1
            timeout: float = DELETE_TIMEOUT_SECONDS
            if shutdown.requested():
                left = shutdown.remaining()
                if left <= 0:
                    raise TimeoutError("shutdown budget spent")
                timeout = min(SHUTDOWN_DELETE_TIMEOUT_SECONDS, left)
            try:
                resp = self._session.delete(url, headers=self._headers(), timeout=timeout)
                if resp.status_code == 404:
                    return
                resp.raise_for_status()
                return
            except Exception:
                if attempt >= DELETE_ATTEMPTS or shutdown.requested():
                    raise
                self._sleep(attempt)

    def receive_url(self) -> str:
        parts = urlsplit(self._base)
        scheme = "wss" if parts.scheme == "https" else "ws"
        path = f"{parts.path}/v1/receive/{quote(self._number, safe='')}"
        return urlunsplit((scheme, parts.netloc, path, "", ""))

    def _beat(self) -> None:
        if self._heartbeat_path is not None:
            try:
                self._heartbeat_path.touch()
            except OSError:
                pass

    def _read_loop(self, frames: "queue.Queue[dict]", should_stop: Callable[[], bool]) -> None:
        """Reader thread: keep the WebSocket open and queue every envelope.

        Runs independently of message processing, so pings are answered and the
        stream stays connected while a document is being cleaned.
        """
        delay = RECONNECT_MIN_SECONDS
        headers = [f"Authorization: Bearer {self._token}"] if self._token else []
        while not should_stop():
            ws = None
            try:
                ws = self._ws_connect(self.receive_url(), headers)
                self._current_ws = ws
                if should_stop():  # stop requested while connecting
                    break
                logger.info("Receive stream connected")
                self._beat()
                delay = RECONNECT_MIN_SECONDS
                missed = 0
                while not should_stop():
                    try:
                        raw = ws.read()
                    except WSTimeout:
                        missed += 1
                        if missed > MAX_MISSED_PINGS:
                            raise ConnectionError("receive stream unresponsive")
                        ws.ping()
                        continue
                    missed = 0
                    self._beat()
                    if raw is None:
                        continue
                    msg = parse_frame(raw)
                    if msg is not None:
                        frames.put(msg)
            except Exception as e:
                if should_stop():  # closed on purpose at shutdown
                    break
                logger.warning("Receive stream error (type: %s); reconnecting in %ss",
                               type(e).__name__, delay)
                logger.debug("Receive stream error detail: %s", e)
                self._sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_SECONDS)
            finally:
                self._current_ws = None
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

    def _stop_reader(self, reader: threading.Thread, stop_reader: threading.Event,
                     frames: "queue.Queue[dict]") -> list[dict]:
        """Stop the reader for good and return every frame it delivered.

        Closing the connection means no new frame can be read; draining while
        waiting lets a reader blocked on a full queue finish its hand-over. A
        final drain after it has exited catches the last frame it put.
        """
        stop_reader.set()
        ws = self._current_ws
        if ws is not None:
            try:
                ws.abort()
            except Exception:
                pass
        pending: list[dict] = []

        def drain() -> None:
            while True:
                try:
                    pending.append(frames.get_nowait())
                except queue.Empty:
                    return

        deadline = time.monotonic() + READER_STOP_SECONDS
        while reader.is_alive() and time.monotonic() < deadline:
            drain()
            reader.join(0.05)
        drain()
        if reader.is_alive():
            logger.warning("Receive thread did not stop in time; frames may be lost")
        return pending

    def receive_forever(
        self,
        on_message: Callable[[dict], None],
        should_stop: Callable[[], bool] = lambda: False,
        on_shutdown: Callable[[list[dict]], None] | None = None,
    ) -> None:
        """Receive envelopes on a reader thread; call ``on_message`` on this thread.

        Processing stays on the calling (main) thread because the pipeline's
        timeout uses SIGALRM and shutdown uses SIGTERM, which Python delivers
        only to the main thread. ``on_message`` errors are logged by type only
        (they may carry document content) and never stop the loop.

        On shutdown (SystemExit / KeyboardInterrupt) the reader is stopped and
        every frame it delivered but that was not fully handled, including the
        one in flight, is handed to ``on_shutdown`` before the exception
        propagates: signal-cli does not redeliver them, so this is the last
        chance to delete their attachments. Further signals are postponed until
        that is done (``shutdown.deferred``).
        """
        frames: queue.Queue[dict] = queue.Queue(maxsize=RECEIVE_QUEUE_SIZE)
        stop_reader = threading.Event()
        reader = threading.Thread(
            target=self._read_loop,
            args=(frames, lambda: should_stop() or stop_reader.is_set()),
            name="signal-receive",
            daemon=True,
        )
        reader.start()
        in_flight: dict | None = None
        try:
            while not should_stop():
                try:
                    in_flight = frames.get(timeout=1)
                except queue.Empty:
                    if not reader.is_alive() and not should_stop():
                        raise RuntimeError("receive thread stopped")
                    continue
                try:
                    on_message(in_flight)
                except Exception as e:
                    logger.error("Message handler failed (type: %s)", type(e).__name__)
                in_flight = None
        except (SystemExit, KeyboardInterrupt):
            with shutdown.deferred():
                pending = self._stop_reader(reader, stop_reader, frames)
                if in_flight is not None:
                    pending.insert(0, in_flight)
                if on_shutdown is not None and pending:
                    on_shutdown(pending)
            raise
