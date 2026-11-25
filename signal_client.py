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
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import requests

logger = logging.getLogger(__name__)

# Must outlast the wrapper's own deadline for a file send (UPSTREAM_TIMEOUT_SEC=120
# in docker-compose.yml), or the bot gives up on a send the wrapper still completes.
HTTP_TIMEOUT_SECONDS = 150
RECONNECT_MIN_SECONDS = 1
RECONNECT_MAX_SECONDS = 30

_MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _default_ws_connect(url: str, headers: list[str]):
    # Imported lazily so unit tests don't need the package installed.
    import websocket

    return websocket.create_connection(url, header=headers)


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
    ) -> None:
        self._base = base_url.rstrip("/")
        self._number = number
        self._token = token
        self._session = session or requests.Session()
        self._ws_connect = ws_connect or _default_ws_connect
        self._sleep = sleep

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
        """Remove a received attachment from signal-cli storage."""
        resp = self._session.delete(
            f"{self._base}/v1/attachments/{quote(attachment_id, safe='')}",
            headers=self._headers(),
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()

    def receive_url(self) -> str:
        parts = urlsplit(self._base)
        scheme = "wss" if parts.scheme == "https" else "ws"
        path = f"{parts.path}/v1/receive/{quote(self._number, safe='')}"
        return urlunsplit((scheme, parts.netloc, path, "", ""))

    def receive_forever(
        self,
        on_message: Callable[[dict], None],
        should_stop: Callable[[], bool] = lambda: False,
    ) -> None:
        """Hold the receive WebSocket open and hand each envelope to ``on_message``.

        Reconnects with capped exponential backoff. ``on_message`` errors are
        logged by type only (they may carry document content) and never stop
        the loop.
        """
        delay = RECONNECT_MIN_SECONDS
        headers = [f"Authorization: Bearer {self._token}"] if self._token else []
        while not should_stop():
            ws = None
            try:
                ws = self._ws_connect(self.receive_url(), headers)
                logger.info("Receive stream connected")
                delay = RECONNECT_MIN_SECONDS
                while not should_stop():
                    raw = ws.recv()
                    if raw in (None, "", b""):
                        raise ConnectionError("receive stream closed")
                    msg = parse_frame(raw)
                    if msg is None:
                        continue
                    try:
                        on_message(msg)
                    except Exception as e:
                        logger.error("Message handler failed (type: %s)", type(e).__name__)
            except Exception as e:
                logger.warning("Receive stream error (type: %s); reconnecting in %ss",
                               type(e).__name__, delay)
                logger.debug("Receive stream error detail: %s", e)
                if should_stop():
                    break
                self._sleep(delay)
                delay = min(delay * 2, RECONNECT_MAX_SECONDS)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass
