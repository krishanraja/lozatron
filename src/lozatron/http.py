"""One retry/backoff helper for every outbound call.

The important idea here is that retries are not uniformly safe. `messages.send`
on the Gmail API has no idempotency key, so a read timeout after the server has
already accepted a message is indistinguishable from one where it has not.
Retrying that blind sends Lauren the brief twice, which converts a transient
network fault into the noisy-delivery failure this system already has history
with.

So callers declare intent. `idempotent=True` retries the usual transient
conditions. `idempotent=False` retries only faults raised before any byte
reached the server -- DNS, connection refused, TLS -- and never a read timeout
or a server error, because either may mean the request landed.
"""

from __future__ import annotations

import gzip
import json
import random
import socket
import time
import urllib.error
import zlib
import urllib.request
from typing import Any

RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# Raised before the request body can have been delivered, so retrying cannot
# duplicate a side effect.
PRE_SEND_ERRORS = (socket.gaierror, ConnectionRefusedError, ConnectionResetError)

DEFAULT_BACKOFF = (1.0, 3.0, 8.0)
MAX_RETRY_AFTER = 60.0


class HttpError(RuntimeError):
    """A request that failed after the retry policy was exhausted."""

    def __init__(self, message: str, *, status: int | None = None, attempts: int = 1):
        super().__init__(message)
        self.status = status
        self.attempts = attempts


def _decompress(raw: bytes, encoding: str) -> bytes:
    """Transparently inflate a compressed body.

    urllib sends no Accept-Encoding and performs no content decoding, but some
    origins compress regardless of what the client asked for. Tubefilter is one:
    its feed arrived gzipped and failed XML parsing on every single run since
    cutover, silently reduced to a one-word entry in the source-error list. The
    magic-byte check covers servers that compress without saying so.
    """
    if encoding == "gzip" or raw[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(raw)
        except (OSError, EOFError, zlib.error):
            return raw
    if encoding == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            try:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
            except zlib.error:
                return raw
    return raw


def _retry_after(error: urllib.error.HTTPError) -> float | None:
    raw = error.headers.get("Retry-After") if error.headers else None
    if not raw:
        return None
    try:
        return min(float(raw.strip()), MAX_RETRY_AFTER)
    except (TypeError, ValueError):
        return None


def _is_pre_send(error: BaseException) -> bool:
    """True when the fault provably happened before anything was sent."""
    if isinstance(error, urllib.error.URLError) and not isinstance(error, urllib.error.HTTPError):
        return isinstance(error.reason, PRE_SEND_ERRORS)
    return isinstance(error, PRE_SEND_ERRORS)


def request(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    backoff: tuple[float, ...] = DEFAULT_BACKOFF,
    jitter: float = 0.3,
    retry_status: frozenset[int] = RETRY_STATUS,
    idempotent: bool = True,
    read_limit: int = 8_000_000,
    sleep=time.sleep,
) -> bytes:
    """Perform a request, retrying per the policy above. Raises HttpError."""
    sent = dict(headers or {})
    # Several publishers 403 a bare token user agent from a datacenter IP.
    # Creator Handbook is the measured case: 403 on "Lozatron/1.0", 200 on
    # this. The identity and contact URL are still here, so this is not a
    # disguise -- it is the form the blocklists expect.
    sent.setdefault(
        "User-Agent",
        "Mozilla/5.0 (compatible; Lozatron/1.0; +https://github.com/krishanraja/lozatron)",
    )
    last: BaseException | None = None
    status: int | None = None

    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=sent)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read(read_limit)
                encoding = (response.headers.get("Content-Encoding") or "").lower()
            return _decompress(raw, encoding)
        except urllib.error.HTTPError as exc:
            last, status = exc, exc.code
            retryable = idempotent and exc.code in retry_status
            wait = _retry_after(exc)
        except Exception as exc:  # noqa: BLE001 - policy decides, not the type
            last, status = exc, None
            retryable = idempotent or _is_pre_send(exc)
            wait = None

        if not retryable or attempt == retries:
            break
        if wait is None:
            wait = backoff[min(attempt, len(backoff) - 1)]
        sleep(wait + random.uniform(0, jitter))

    raise HttpError(
        f"{method} {url.split('?', 1)[0]} failed: {type(last).__name__}",
        status=status,
        attempts=attempt + 1,
    ) from last


def request_json(url: str, **kwargs: Any) -> Any:
    """`request` plus JSON decoding. Decode failures raise HttpError."""
    raw = request(url, **kwargs)
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HttpError(f"{url.split('?', 1)[0]} returned non-JSON: {type(exc).__name__}") from exc
