import base64
import json
from unittest.mock import MagicMock

import pytest

from signal_client import SignalClient, parse_frame, to_data_uri

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
    def __init__(self, frames):
        self._frames = list(frames)
        self.closed = False

    def recv(self):
        if not self._frames:
            return ""  # server closed
        f = self._frames.pop(0)
        if isinstance(f, Exception):
            raise f
        return f


def test_receive_forever_dispatches_and_reconnects():
    env = {"sourceUuid": SENDER, "dataMessage": {"message": "hi"}}
    sockets = [
        _FakeWS([json.dumps({"envelope": env}), "garbage"]),
        _FakeWS([json.dumps({"method": "receive", "params": {"envelope": env}})]),
    ]
    connects = []

    def ws_connect(url, headers):
        connects.append((url, headers))
        return sockets[len(connects) - 1]

    got = []
    sleeps = []
    client = _client(ws_connect=ws_connect, sleep=sleeps.append)
    client.receive_forever(got.append, should_stop=lambda: len(connects) >= 2 and not sockets[1]._frames)

    assert [m["envelope"] for m in got] == [env, env]
    assert connects[0] == ("ws://wrapper-clean-data:8080/v1/receive/%2B15550000001",
                           [f"Authorization: Bearer {TOKEN}"])
    assert sleeps == [1]


def test_receive_forever_backoff_caps_and_survives_handler_errors():
    attempts = []

    def ws_connect(url, headers):
        attempts.append(1)
        raise ConnectionRefusedError()

    sleeps = []
    client = _client(ws_connect=ws_connect, sleep=sleeps.append)
    client.receive_forever(lambda m: None, should_stop=lambda: len(attempts) >= 7)
    assert sleeps == [1, 2, 4, 8, 16, 30]

    def boom(_m):
        raise ValueError("document text")

    env = {"sourceUuid": SENDER, "dataMessage": {"message": "x"}}
    ws = _FakeWS([json.dumps({"envelope": env}), json.dumps({"envelope": env})])
    calls = []

    def handler(m):
        calls.append(m)
        boom(m)

    _client(ws_connect=lambda u, h: ws).receive_forever(handler, should_stop=lambda: len(calls) >= 2)
    assert len(calls) == 2
