"""In-memory fakes for every external interface. Used by tests and local runs."""

from __future__ import annotations

import asyncio
import email
import hashlib
import itertools
from collections.abc import Awaitable, Callable

from las.common.interfaces import (
    AVResult,
    LLMAPIError,
    LLMRequest,
    LLMResponse,
    ParsedDocument,
)


# --- mailbox ---------------------------------------------------------------------------------
class FakeMailbox:
    def __init__(self, mailbox: str = "las@integramicro.co.in", uidvalidity: int = 1) -> None:
        self.mailbox = mailbox
        self.uidvalidity = uidvalidity
        self.folders: dict[str, dict[int, bytes]] = {"INBOX": {}}
        self._next_uid = itertools.count(1)
        self._selected = "INBOX"
        self._changed = asyncio.Event()
        self.connected = False

    def deliver(self, raw: bytes, folder: str = "INBOX") -> int:
        uid = next(self._next_uid)
        self.folders.setdefault(folder, {})[uid] = raw
        self._changed.set()
        return uid

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def select(self, folder: str) -> int:
        self._selected = folder
        self.folders.setdefault(folder, {})
        return self.uidvalidity

    async def uid_search_after(self, last_uid: int) -> list[int]:
        return sorted(u for u in self.folders[self._selected] if u > last_uid)

    async def fetch_raw(self, uid: int) -> bytes:
        return self.folders[self._selected][uid]

    async def idle(self, timeout: float) -> bool:
        self._changed.clear()
        try:
            await asyncio.wait_for(self._changed.wait(), timeout)
            return True
        except (TimeoutError, asyncio.TimeoutError):  # distinct on 3.10
            return False

    async def move(self, uid: int, dest_folder: str) -> None:
        raw = self.folders[self._selected].pop(uid)
        self.folders.setdefault(dest_folder, {})[uid] = raw

    async def move_by_message_id(self, message_id: str, dest_folder: str) -> bool:
        for folder, msgs in self.folders.items():
            for uid, raw in list(msgs.items()):
                if email.message_from_bytes(raw).get("Message-ID", "").strip() == message_id:
                    if folder != dest_folder:
                        self.folders.setdefault(dest_folder, {})[uid] = msgs.pop(uid)
                    return True
        return False


# --- object store ----------------------------------------------------------------------------
class FakeObjectStore:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        uri = f"mem://{key}"
        self.objects[uri] = (data, content_type)
        return uri

    async def get(self, uri: str) -> bytes:
        return self.objects[uri][0]

    def uri_for(self, key: str) -> str:
        return f"mem://{key}"

    async def signed_url(
        self,
        uri: str,
        *,
        ttl_seconds: int,
        filename: str,
        content_type: str = "application/pdf",
        disposition: str = "attachment",
    ) -> str:
        token = hashlib.sha256(uri.encode()).hexdigest()[:16]
        return (
            f"https://files.example.test/{token}/{filename}"
            f"?ttl={ttl_seconds}&mime={content_type}&disp={disposition}"
        )


# --- AV --------------------------------------------------------------------------------------
EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"


class FakeAVScanner:
    async def scan(self, data: bytes) -> AVResult:
        if b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE" in data:
            return AVResult("INFECTED", "Eicar-Test-Signature")
        return AVResult("CLEAN")


# --- document parser -------------------------------------------------------------------------
class FakeDocumentParser:
    """Scripted text per file (sha256); PDFs otherwise use their real text layer (PyMuPDF), so
    tests stay fast and deterministic. Images without a scripted text read as empty."""

    provider = "fake_parser"
    model = "fake-parser-v1"

    def __init__(self, texts: dict[str, str] | None = None, default: str = "") -> None:
        self.texts = texts or {}  # sha256 -> text
        self.default = default
        self.calls = 0

    async def parse(self, data: bytes, mime_type: str) -> ParsedDocument:
        self.calls += 1
        scripted = self.texts.get(hashlib.sha256(data).hexdigest())
        if scripted is not None:
            return ParsedDocument(scripted, 1, self.provider, self.model)
        if mime_type == "application/pdf":
            from las.attachments.text import pdf_info

            info = pdf_info(data)
            return ParsedDocument(
                info.text,
                info.page_count or 1,
                self.provider,
                self.model,
                password_required=info.needs_password,
            )
        return ParsedDocument(self.default, 1, self.provider, self.model)


# --- LLM -------------------------------------------------------------------------------------
LLMHandler = Callable[[LLMRequest], Awaitable[LLMResponse] | LLMResponse]


class FakeLLM:
    """Scriptable LLM. Either queue responses or install a handler(request) -> response."""

    provider = "fake_llm"

    def __init__(self, handler: LLMHandler | None = None) -> None:
        self.handler = handler
        self.queue: list[LLMResponse | Exception] = []
        self.requests: list[LLMRequest] = []

    def push(self, item: LLMResponse | Exception | str) -> None:
        self.queue.append(
            LLMResponse(text=item, input_tokens=100, output_tokens=50)
            if isinstance(item, str)
            else item
        )

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self.queue:
            item = self.queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        if self.handler is not None:
            result = self.handler(request)
            return result if isinstance(result, LLMResponse) else await result
        raise LLMAPIError("FakeLLM has no scripted response", retryable=False)
