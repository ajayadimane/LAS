"""Record and replay Gemini HTTP traffic for the evaluation harness.

Recording wraps the real transport and saves each successful response under
a hash of the request (model + canonical JSON body). Replay serves those
files with no network, so a recorded evaluation can be re-scored offline
after a change to code that does not alter what is sent to Gemini (scoring,
consolidation, priority rules, CAM mapping). Changing a prompt, context or
schema changes the request hash: those calls miss and must be re-recorded.

Cassettes contain the DPR text that was sent to Gemini -- real customer
data. They live in eval/cassettes/, which is gitignored, and must never be
committed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx


class CassetteMiss(LookupError):
    """Replay found no recording for this request."""


def request_key(request: httpx.Request) -> str:
    """Stable across runs: the URL path (which names the model) plus the
    JSON body with sorted keys. Never includes headers (the API key)."""
    body = json.loads(request.content or b"{}")
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(f"{request.url.path}\n{canonical}".encode()).hexdigest()


def _response(request: httpx.Request, status: int, body: bytes) -> httpx.Response:
    # Content is already decoded; never forward content-encoding/length.
    return httpx.Response(
        status,
        headers={"content-type": "application/json"},
        content=body,
        request=request,
    )


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, directory: Path, inner: httpx.AsyncBaseTransport | None = None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.inner = inner or httpx.AsyncHTTPTransport(verify=True)
        self.recorded = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        key = request_key(request)
        response = await self.inner.handle_async_request(request)
        body = await response.aread()
        if response.status_code == 200:
            (self.directory / f"{key}.json").write_bytes(body)
            self.recorded += 1
        return _response(request, response.status_code, body)

    async def aclose(self) -> None:
        await self.inner.aclose()


class ReplayTransport(httpx.AsyncBaseTransport):
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.hits = 0
        self.misses = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = self.directory / f"{request_key(request)}.json"
        if not path.exists():
            self.misses += 1
            raise CassetteMiss(
                "no recording for this Gemini request (prompt, context or "
                "schema changed since it was recorded); re-record with --record"
            )
        self.hits += 1
        return _response(request, 200, path.read_bytes())
