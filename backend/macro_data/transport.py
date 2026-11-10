"""Bounded HTTPS transport for official data GET requests."""

from __future__ import annotations

from dataclasses import dataclass
import http.client
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_BYTES = 8 * 1024 * 1024
TIMEOUT = 12
ATTEMPTS = 3
OFFICIAL_HOSTS = frozenset({"data-api.ecb.europa.eu", "ec.europa.eu"})
RETRYABLE = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class Response:
    body: bytes
    status: int
    content_type: str
    attempts: int


class FetchError(RuntimeError):
    def __init__(self, code: str, attempts: int, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.attempts = attempts
        self.status = status


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch(url: str, accept: str) -> Response:
    endpoint = urlsplit(url)
    if (endpoint.scheme != "https" or endpoint.netloc not in OFFICIAL_HOSTS
            or endpoint.fragment):
        raise ValueError("unapproved data endpoint")
    request = Request(url, headers={"Accept": accept, "User-Agent": "MacroPulse-AI/1.0"})
    opener = build_opener(_NoRedirect())
    for attempt in range(1, ATTEMPTS + 1):
        status = None
        try:
            deadline = time.monotonic() + TIMEOUT
            with opener.open(request, timeout=TIMEOUT) as response:
                status = response.status
                body = bytearray()
                while chunk := response.read1(min(64 * 1024, MAX_BYTES + 1 - len(body))):
                    if time.monotonic() >= deadline:
                        raise TimeoutError
                    body.extend(chunk)
                    if len(body) > MAX_BYTES:
                        raise FetchError("response_too_large", attempt, response.status)
                if time.monotonic() >= deadline:
                    raise TimeoutError
                declared_length = response.headers.get("Content-Length")
                if declared_length is not None and declared_length != str(len(body)):
                    raise http.client.HTTPException("incomplete response")
                return Response(bytes(body), response.status, response.headers.get_content_type(), attempt)
        except HTTPError as error:
            with error:
                failure = FetchError("provider_http_error", attempt, error.code)
            if error.code not in RETRYABLE:
                raise failure from None
        except (OSError, http.client.HTTPException):
            failure = FetchError("provider_transport_error", attempt, status)
        if attempt == ATTEMPTS:
            raise failure from None
        time.sleep(2 ** (attempt - 1))
    raise AssertionError("unreachable retry state")
