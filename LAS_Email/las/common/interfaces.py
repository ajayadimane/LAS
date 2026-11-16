"""Interfaces for every external system this service touches: the mailbox, object storage, the
AV scanner, the document parser and the LLM. Production adapters live next to the feature that
uses them (``las/ingest/zoho_imap.py``, ``las/attachments/av.py``, ...); fakes live in
``las/common/fakes.py``. Core LAS is reached over HTTP by ``las/core_handoff.py`` only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable


# --- mailbox ---------------------------------------------------------------------------------
@runtime_checkable
class MailboxAdapter(Protocol):
    """A mailbox folder reachable over IMAP-like semantics (UIDs, IDLE, MOVE)."""

    mailbox: str

    async def connect(self) -> None: ...
    async def close(self) -> None: ...
    async def select(self, folder: str) -> int:
        """Select a folder and return its UIDVALIDITY."""
        ...

    async def uid_search_after(self, last_uid: int) -> list[int]: ...
    async def fetch_raw(self, uid: int) -> bytes: ...
    async def idle(self, timeout: float) -> bool:
        """Block until the server reports a change or ``timeout`` elapses. True if changed."""
        ...

    async def move(self, uid: int, dest_folder: str) -> None: ...
    async def move_by_message_id(self, message_id: str, dest_folder: str) -> bool: ...


# --- object storage --------------------------------------------------------------------------
class ObjectStore(Protocol):
    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        """Store encrypted at rest; return an opaque URI (``gs://...`` or ``local://...``)."""
        ...

    async def get(self, uri: str) -> bytes: ...
    def uri_for(self, key: str) -> str:
        """The URI ``put(key, ...)`` returns, without storing anything."""
        ...

    async def signed_url(
        self,
        uri: str,
        *,
        ttl_seconds: int,
        filename: str,
        content_type: str = "application/pdf",
        disposition: str = "attachment",
    ) -> str: ...


# --- AV --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class AVResult:
    status: Literal["CLEAN", "INFECTED", "ERROR"]
    signature: str | None = None


class AVScanner(Protocol):
    async def scan(self, data: bytes) -> AVResult: ...


# --- document parsing (text layer + OCR) -------------------------------------------------------
@dataclass(frozen=True)
class ParsedDocument:
    text: str  # layout-preserved plain text (table rows stay on one line)
    pages: int
    provider: str
    model: str
    password_required: bool = False


class DocumentParser(Protocol):
    """Local document parsing for PDF, DOCX and images, OCR included (regional scripts)."""

    provider: str
    model: str

    async def parse(self, data: bytes, mime_type: str) -> ParsedDocument: ...


# --- LLM -------------------------------------------------------------------------------------
@dataclass(frozen=True)
class LLMPart:
    text: str | None = None
    data: bytes | None = None
    mime_type: str | None = None


@dataclass(frozen=True)
class LLMRequest:
    model: str
    system: str
    parts: list[LLMPart]
    response_schema: dict[str, Any] | None = None  # JSON schema for structured output
    temperature: float = 0.0
    max_output_tokens: int = 4096
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class LLMResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    stop_reason: str | None = None
    request_id: str | None = None


class LLMAPIError(Exception):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMTimeoutError(LLMAPIError):
    pass


class LLMClient(Protocol):
    provider: str

    async def generate(self, request: LLMRequest) -> LLMResponse: ...
