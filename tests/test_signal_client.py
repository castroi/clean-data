import base64
import json
import threading
import time
from unittest.mock import MagicMock

import pytest

from signal_client import MAX_MISSED_PINGS, SignalClient, WSTimeout, parse_frame, to_data_uri

TOKEN = "t" * 32
NUMBER = "+15550000001"
SENDER = "42cc8f08-8b0a-4a79-a46d-000000000001"


def _client(session=None, ws_connect=None, sleep=None):
    return SignalClient(
        base_url="http://wrapper-clean-data:8080/",
        number=NUMBER,
        token=TOKEN,
        session=session or MagicMock(),
        ws_connect=ws_connect,
        sleep=sleep or (lambda _s: None),
    )


# --- send ---


def test_send_text_has_auth_and_no_number():
    session = MagicMock()
    _client(session).send_message("hi", [SENDER])

    url = session.post.call_args.args[0]
    kwargs = session.post.call_args.kwargs
    assert url == "http://wrapper-clean-data:8080/v2/send"
    assert kwargs["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert kwargs["json"] == {"message": "hi", "recipients": [SENDER]}
    session.post.return_value.raise_for_status.assert_called_once()


def test_send_attachment_as_data_uri():
    session = MagicMock()
    _client(session).send_message("Done.", [SENDER], attachments=[("cleaned.pdf", b"%PDF-1.4\n")])

    body = session.post.call_args.kwargs["json"]
    assert "number" not in body
    assert body["base64_attachments"] == [
        "data:application/pdf;filename=cleaned.pdf;base64," + base64.b64encode(b"%PDF-1.4\n").decode()
    ]


def test_send_without_token_omits_header():
    session = MagicMock()
    SignalClient("http://w:8080", NUMBER, "", session=session).send_message("x", [SENDER])
    assert session.post.call_args.kwargs["headers"] == {}


def test_send_raises_on_http_error():
    session = MagicMock()
    session.post.return_value.raise_for_status.side_effect = RuntimeError("400")
    with pytest.raises(RuntimeError):
        _client(session).send_message("x", [SENDER])


def test_data_uri_docx_mime():
    uri = to_data_uri("cleaned.docx", b"PK")
    assert uri.startswith(
        "data:application/vnd.openxmlformats-officedocument.wordprocessingml.document;filename=cleaned.docx;base64,"
    )


# --- attachments ---


def test_get_attachment_streams_and_caps():
    session = MagicMock()
    resp = session.get.return_value
    resp.iter_content.return_value = iter([b"a" * 6, b"b" * 6, b"c" * 6])

    data = _client(session).get_attachment("abc123.pdf", max_bytes=10)

    assert session.get.call_args.args[0] == "http://wrapper-clean-data:8080/v1/attachments/abc123.pdf"
    assert session.get.call_args.kwargs["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert session.get.call_args.kwargs["stream"] is True
    assert len(data) == 12  # stopped one chunk past the cap; caller rejects > max
    resp.close.assert_called_once()


def test_get_attachment_escapes_id():
    session = MagicMock()
    session.get.return_value.iter_content.return_value = iter([b"x"])
    _client(session).get_attachment("../x", max_bytes=10)
    assert session.get.call_args.args[0].endswith("/v1/attachments/..%2Fx")


def test_delete_attachment():
    session = MagicMock()
    _client(session).delete_attachment("abc123")
    assert session.delete.call_args.args[0] == "http://wrapper-clean-data:8080/v1/attachments/abc123"
    assert session.delete.call_args.kwargs["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    session.delete.return_value.raise_for_status.assert_called_once()


def _resp(status):
    r = MagicMock()
    r.status_code = status
    if status >= 400:
        r.raise_for_status.side_effect = RuntimeError(str(status))
    return r


def test_delete_retries_then_succeeds():
    session = MagicMock()
    session.delete.side_effect = [_resp(502), _resp(204)]
    sleeps = []
    _client(session, sleep=sleeps.append).delete_attachment("abc")
    assert session.delete.call_count == 2
    assert sleeps == [1]


def test_delete_404_counts_as_gone():
    session = MagicMock()
    session.delete.return_value = _resp(404)
    _client(session).delete_attachment("abc")
    assert session.delete.call_count == 1


def test_delete_gives_up_after_attempts():
    session = MagicMock()
    session.delete.return_value = _resp(503)
    with pytest.raises(RuntimeError):
        _client(session).delete_attachment("abc")
    assert session.delete.call_count == 3


# --- receive ---


def test_receive_url():
    assert _client().receive_url() == "ws://wrapper-clean-data:8080/v1/receive/%2B15550000001"
    https = SignalClient("https://w.example/api", NUMBER, TOKEN, session=MagicMock())
    assert https.receive_url() == "wss://w.example/api/v1/receive/%2B15550000001"


def test_parse_frame_forms():
    env = {"sourceUuid": SENDER, "dataMessage": {"message": "hi"}}
    assert parse_frame(json.dumps({"envelope": env, "account": NUMBER}))["envelope"] == env
    assert parse_frame(json.dumps({"jsonrpc": "2.0", "method": "receive", "params": {"envelope": env}}))["envelope"] == env
    assert parse_frame("not json") is None
    assert parse_frame(json.dumps({"result": 1})) is None
    assert parse_frame(json.dumps([1, 2])) is None


class _FakeWS:
    """Stands in for _WSConnection: read() returns frames, None (pong),
    raises WSTimeout (idle) or ConnectionError (closed)."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.pings = 0
        self.closed = False

    def read(self):
        if not self._frames:
            raise ConnectionError("closed")
        f = self._frames.pop(0)
        if isinstance(f, BaseException) or (isinstance(f, type) and issubclass(f, BaseException)):
            raise f
        return f

    def ping(self):
        self.pings += 1

    def close(self):
        self.closed = True


def _run(client, handler, until, timeout=5.0):
    """Run receive_forever until ``until()`` is true (or fail after timeout)."""
    deadline = time.time() + timeout
    client.receive_forever(handler, should_stop=lambda: until() or time.time() > deadline)
    assert until(), "condition not reached before timeout"


def test_receive_dispatches_both_frame_forms_and_reconnects():
    env = {"sourceUuid": SENDER, "dataMessage": {"message": "hi"}}
    sockets = [
        _FakeWS([json.dumps({"envelope": env}), "garbage", None]),
        _FakeWS([json.dumps({"method": "receive", "params": {"envelope": env}})]),
    ]
    connects = []

    def ws_connect(url, headers):
        connects.append((url, headers))
        return sockets[min(len(connects) - 1, 1)]

    got = []
    _run(_client(ws_connect=ws_connect), got.append, lambda: len(got) >= 2)
    assert [m["envelope"] for m in got] == [env, env]
    assert connects[0] == ("ws://wrapper-clean-data:8080/v1/receive/%2B15550000001",
                           [f"Authorization: Bearer {TOKEN}"])
    assert len(connects) >= 2


def test_receive_pings_when_idle_and_reconnects_when_unanswered():
    socks = []

    def ws_connect(url, headers):
        ws = _FakeWS([WSTimeout] * (MAX_MISSED_PINGS + 1))
        socks.append(ws)
        return ws

    _run(_client(ws_connect=ws_connect), lambda m: None, lambda: len(socks) >= 2)
    assert socks[0].pings == MAX_MISSED_PINGS
    assert socks[0].closed


def test_receive_pong_resets_missed_pings():
    env = {"sourceUuid": SENDER, "dataMessage": {"message": "x"}}
    frames = [WSTimeout, None, WSTimeout, None, WSTimeout, None, json.dumps({"envelope": env})]
    ws = _FakeWS(frames)
    got = []
    _run(_client(ws_connect=lambda u, h: ws), got.append, lambda: len(got) >= 1)
    assert ws.pings == 3  # never exceeded MAX_MISSED_PINGS in a row


def test_receive_keeps_reading_while_handler_is_slow():
    """The reader thread keeps answering/reading while a message is processed."""
    env = {"sourceUuid": SENDER, "dataMessage": {"message": "x"}}
    ws = _FakeWS([json.dumps({"envelope": env})] + [None] * 50 + [json.dumps({"envelope": env})])
    got = []

    def slow(m):
        got.append(m)
        if len(got) == 1:
            deadline = time.time() + 2
            while ws._frames and time.time() < deadline:
                time.sleep(0.01)  # handler busy; reader must drain meanwhile

    _run(_client(ws_connect=lambda u, h: ws), slow, lambda: len(got) >= 2)
    assert not ws._frames  # everything was read during the slow handler


def test_receive_heartbeat_touched(tmp_path):
    hb = tmp_path / "hb"
    ws = _FakeWS([None])
    client = SignalClient("http://w:8080", NUMBER, TOKEN, session=MagicMock(),
                          ws_connect=lambda u, h: ws, sleep=lambda s: None, heartbeat_path=hb)
    _run(client, lambda m: None, hb.exists)


def test_receive_backoff_caps_and_survives_handler_errors():
    attempts = []

    def ws_connect(url, headers):
        attempts.append(1)
        raise ConnectionRefusedError()

    sleeps = []
    _run(_client(ws_connect=ws_connect, sleep=sleeps.append), lambda m: None, lambda: len(attempts) >= 7)
    assert sleeps[:6] == [1, 2, 4, 8, 16, 30]

    env = {"sourceUuid": SENDER, "dataMessage": {"message": "x"}}
    ws = _FakeWS([json.dumps({"envelope": env}), json.dumps({"envelope": env})])
    calls = []

    def handler(m):
        calls.append(m)
        raise ValueError("document text")

    _run(_client(ws_connect=lambda u, h: ws), handler, lambda: len(calls) >= 2)


def test_delete_shutdown_limits():
    session = MagicMock()
    session.delete.return_value = _resp(503)
    with pytest.raises(RuntimeError):
        _client(session).delete_attachment("abc", timeout=2.5, attempts=1)
    assert session.delete.call_count == 1
    assert session.delete.call_args.kwargs["timeout"] == 2.5


def test_shutdown_hands_queued_frames_to_on_shutdown():
    """SystemExit while handling: the reader stops, queued frames go to on_shutdown."""
    env = {"sourceUuid": SENDER, "dataMessage": {"message": "x"}}
    frames = [json.dumps({"envelope": {**env, "n": i}}) for i in range(4)]
    ws = _FakeWS(frames)
    handled, drained = [], []

    def handler(m):
        handled.append(m)
        deadline = time.time() + 2
        while ws._frames and time.time() < deadline:  # let the reader queue the rest
            time.sleep(0.01)
        raise SystemExit(0)  # docker stop during the first message

    with pytest.raises(SystemExit):
        _client(ws_connect=lambda u, h: ws).receive_forever(handler, on_shutdown=drained.extend)
    assert [m["envelope"]["n"] for m in handled] == [0]
    assert [m["envelope"]["n"] for m in drained] == [1, 2, 3]


def test_shutdown_without_queued_frames_skips_callback():
    ws = _FakeWS([])
    called = []

    def handler(m):
        raise AssertionError("no frames")

    stop_at = time.time() + 0.3

    def stop():
        # Ctrl-C/SIGTERM land on the main thread only; the reader just polls.
        if time.time() > stop_at and threading.current_thread() is threading.main_thread():
            raise KeyboardInterrupt
        return False

    with pytest.raises(KeyboardInterrupt):
        _client(ws_connect=lambda u, h: ws).receive_forever(handler, should_stop=stop, on_shutdown=called.append)
    assert called == []
