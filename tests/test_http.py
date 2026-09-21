import io
import socket
import urllib.error

import pytest

from lozatron.http import HttpError, request, request_json


class FakeResponse(io.BytesIO):
    headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError("https://x", code, "boom", headers, None)


def driver(outcomes):
    """Yield each outcome in turn; raise it if it is an exception."""
    calls = {"n": 0, "waits": []}

    def _open(req, timeout=None):
        item = outcomes[calls["n"]]
        calls["n"] += 1
        if isinstance(item, BaseException):
            raise item
        return FakeResponse(item)

    return _open, calls


def install(monkeypatch, outcomes):
    _open, calls = driver(outcomes)
    monkeypatch.setattr("urllib.request.urlopen", _open)
    return calls


def test_returns_body_without_retrying_on_success(monkeypatch):
    calls = install(monkeypatch, [b'{"ok": true}'])
    assert request_json("https://x") == {"ok": True}
    assert calls["n"] == 1


def test_retries_transient_status_then_succeeds(monkeypatch):
    calls = install(monkeypatch, [http_error(503), http_error(502), b"done"])
    waits = []
    assert request("https://x", sleep=waits.append, jitter=0) == b"done"
    assert calls["n"] == 3
    assert waits == [1.0, 3.0]


def test_honours_retry_after_header(monkeypatch):
    install(monkeypatch, [http_error(429, retry_after="7"), b"done"])
    waits = []
    request("https://x", sleep=waits.append, jitter=0)
    assert waits == [7.0]


def test_does_not_retry_client_error(monkeypatch):
    calls = install(monkeypatch, [http_error(400)])
    with pytest.raises(HttpError) as caught:
        request("https://x", sleep=lambda _: None)
    assert calls["n"] == 1
    assert caught.value.status == 400


def test_non_idempotent_never_retries_a_read_timeout(monkeypatch):
    """The rule that stops Lauren receiving the brief twice.

    A read timeout may mean the server already accepted the message, so a
    non-idempotent caller must surface it rather than resend.
    """
    calls = install(monkeypatch, [TimeoutError("read timed out"), b"done"])
    with pytest.raises(HttpError):
        request("https://x", method="POST", idempotent=False, sleep=lambda _: None)
    assert calls["n"] == 1


def test_non_idempotent_never_retries_a_server_error(monkeypatch):
    calls = install(monkeypatch, [http_error(503), b"done"])
    with pytest.raises(HttpError):
        request("https://x", method="POST", idempotent=False, sleep=lambda _: None)
    assert calls["n"] == 1


def test_non_idempotent_does_retry_a_pre_send_failure(monkeypatch):
    """Connection refused proves nothing was delivered, so a resend is safe."""
    refused = urllib.error.URLError(ConnectionRefusedError("no listener"))
    calls = install(monkeypatch, [refused, b"done"])
    assert request("https://x", method="POST", idempotent=False,
                   sleep=lambda _: None, jitter=0) == b"done"
    assert calls["n"] == 2


def test_dns_failure_counts_as_pre_send(monkeypatch):
    calls = install(monkeypatch, [urllib.error.URLError(socket.gaierror("no such host")), b"done"])
    assert request("https://x", method="POST", idempotent=False,
                   sleep=lambda _: None, jitter=0) == b"done"
    assert calls["n"] == 2


def test_retries_are_bounded(monkeypatch):
    calls = install(monkeypatch, [http_error(500)] * 10)
    with pytest.raises(HttpError) as caught:
        request("https://x", retries=2, sleep=lambda _: None)
    assert calls["n"] == 3
    assert caught.value.attempts == 3


def test_non_json_body_raises_http_error(monkeypatch):
    install(monkeypatch, [b"<html>nope</html>"])
    with pytest.raises(HttpError):
        request_json("https://x", sleep=lambda _: None)


def test_error_message_omits_query_string(monkeypatch):
    """Tokens ride in query strings on some APIs; they must not reach logs."""
    install(monkeypatch, [http_error(400)])
    with pytest.raises(HttpError) as caught:
        request("https://x/run?token=supersecret", sleep=lambda _: None)
    assert "supersecret" not in str(caught.value)


def test_gzipped_body_is_decompressed_by_header(monkeypatch):
    """Tubefilter shipped gzip and failed XML parsing on every run since cutover."""
    import gzip as gziplib

    payload = gziplib.compress(b"<rss>ok</rss>")

    class Resp(FakeResponse):
        headers = {"Content-Encoding": "gzip"}

    def _open(req, timeout=None):
        return Resp(payload)

    monkeypatch.setattr("urllib.request.urlopen", _open)
    assert request("https://x") == b"<rss>ok</rss>"


def test_gzipped_body_is_decompressed_without_a_header(monkeypatch):
    """Some origins compress without announcing it; magic bytes catch those."""
    import gzip as gziplib

    class Resp(FakeResponse):
        headers = {}

    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None: Resp(gziplib.compress(b"<rss>ok</rss>")))
    assert request("https://x") == b"<rss>ok</rss>"


def test_plain_body_passes_through_untouched(monkeypatch):
    class Resp(FakeResponse):
        headers = {}

    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None: Resp(b"<rss>plain</rss>"))
    assert request("https://x") == b"<rss>plain</rss>"


def test_corrupt_gzip_falls_back_to_raw_rather_than_raising(monkeypatch):
    class Resp(FakeResponse):
        headers = {"Content-Encoding": "gzip"}

    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None: Resp(b"\x1f\x8b" + b"garbage"))
    assert request("https://x") == b"\x1f\x8b" + b"garbage"
