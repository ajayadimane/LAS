"""IMAP adapter for Zoho Mail (imap.zoho.in:993) with IDLE, UID SEARCH and UID MOVE.

imaplib (3.12) has no IDLE. We implement it without ever timing out a buffered read: we only
call ``readline`` when ``select`` (or the TLS layer's ``pending``) says data is available.
Anything a server pushes that we miss is picked up by the periodic catch-up UID SEARCH.
"""

from __future__ import annotations

import asyncio
import contextlib
import imaplib
import re
import select
import ssl
import time
from typing import TypeVar

from las.common.logging import get_logger

_T = TypeVar("_T")

log = get_logger(__name__)

_UIDVALIDITY = re.compile(rb"UIDVALIDITY (\d+)")


class ImapError(Exception):
    pass


class ZohoImapMailbox:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        use_ssl: bool = True,
        mailbox: str | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.host, self.port, self.username, self.password = host, port, username, password
        self.use_ssl, self.timeout = use_ssl, timeout
        self.mailbox = mailbox or username
        self._imap: imaplib.IMAP4 | None = None
        self._lock = asyncio.Lock()

    # -- sync internals (run in a thread) --------------------------------------------------
    def _conn(self) -> imaplib.IMAP4:
        if self._imap is None:
            raise ImapError("not connected")
        return self._imap

    def _connect_sync(self) -> None:
        if self.use_ssl:
            imap: imaplib.IMAP4 = imaplib.IMAP4_SSL(
                self.host, self.port, timeout=self.timeout, ssl_context=ssl.create_default_context()
            )
        else:
            imap = imaplib.IMAP4(self.host, self.port, timeout=self.timeout)
        imap.login(self.username, self.password)
        self._imap = imap

    def _close_sync(self) -> None:
        if self._imap is not None:
            with contextlib.suppress(imaplib.IMAP4.error, OSError):
                self._imap.logout()
        self._imap = None

    @staticmethod
    def _q(folder: str) -> str:
        return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'

    def _select_sync(self, folder: str) -> int:
        imap = self._conn()
        typ, _ = imap.select(self._q(folder))
        if typ != "OK":
            raise ImapError(f"select {folder} failed")
        _, data = imap.response("UIDVALIDITY")
        if data and data[0]:
            return int(data[0])
        typ, st = imap.status(self._q(folder), "(UIDVALIDITY)")
        m = _UIDVALIDITY.search(st[0] if st and isinstance(st[0], bytes) else b"")
        if not m:
            raise ImapError("no UIDVALIDITY")
        return int(m.group(1))

    def _ensure_folder_sync(self, folder: str) -> None:
        imap = self._conn()
        typ, data = imap.create(self._q(folder))
        if (
            typ != "OK"
            and b"ALREADYEXISTS" not in b" ".join(d for d in data if isinstance(d, bytes)).upper()
            and b"EXISTS" not in b" ".join(d for d in data if isinstance(d, bytes)).upper()
        ):
            log.warning("imap_create_folder_failed", folder=folder)

    def _search_sync(self, last_uid: int) -> list[int]:
        typ, data = self._conn().uid("SEARCH", f"UID {last_uid + 1}:*")
        if typ != "OK":
            raise ImapError("uid search failed")
        uids = [int(x) for x in (data[0] or b"").split()]
        # "N:*" always matches the highest UID even when it is <= last_uid.
        return sorted(u for u in uids if u > last_uid)

    def _fetch_sync(self, uid: int) -> bytes:
        typ, data = self._conn().uid("FETCH", str(uid), "(BODY.PEEK[])")
        if typ != "OK":
            raise ImapError(f"fetch {uid} failed")
        for item in data:
            if isinstance(item, tuple) and len(item) == 2:
                return bytes(item[1])
        raise ImapError(f"fetch {uid}: no body")

    def _move_sync(self, uid: int, dest: str) -> None:
        imap = self._conn()
        self._ensure_folder_sync(dest)
        typ, _ = imap.uid("MOVE", str(uid), self._q(dest))
        if typ != "OK":  # servers without MOVE: COPY + \Deleted + EXPUNGE
            typ, _ = imap.uid("COPY", str(uid), self._q(dest))
            if typ != "OK":
                raise ImapError(f"move {uid} failed")
            imap.uid("STORE", str(uid), "+FLAGS.SILENT", r"(\Deleted)")
            imap.expunge()

    def _move_by_message_id_sync(self, message_id: str, dest: str) -> bool:
        typ, data = self._conn().uid("SEARCH", "HEADER", "Message-ID", self._q(message_id))
        if typ != "OK":
            return False
        uids = (data[0] or b"").split()
        for u in uids:
            self._move_sync(int(u), dest)
        return bool(uids)

    def _idle_sync(self, timeout: float) -> bool:
        imap = self._conn()
        tag = imap._new_tag()
        imap.send(tag + b" IDLE\r\n")
        cont = imap.readline()
        if not cont.startswith(b"+"):
            raise ImapError(f"IDLE refused: {cont[:80]!r}")
        sock = imap.socket()
        deadline = time.monotonic() + timeout
        changed = False
        while not changed:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            pending = sock.pending() if isinstance(sock, ssl.SSLSocket) else 0
            if not pending:
                readable, _, _ = select.select([sock], [], [], min(remaining, 30.0))
                if not readable:
                    continue
            line = imap.readline()
            if not line:
                raise ImapError("connection closed during IDLE")
            if b"EXISTS" in line or b"RECENT" in line:
                changed = True
            elif line.startswith(b"* BYE"):
                raise ImapError("server said BYE during IDLE")
        imap.send(b"DONE\r\n")
        while True:
            line = imap.readline()
            if not line:
                raise ImapError("connection closed ending IDLE")
            if line.startswith(tag):
                parts = line.split(b" ", 2)
                if len(parts) < 2 or parts[1].upper() != b"OK":
                    raise ImapError(f"IDLE ended badly: {line[:80]!r}")
                break
            if b"EXISTS" in line:
                changed = True
        return changed

    # -- async interface -----------------------------------------------------------------
    async def _run(self, fn: object, *args: object) -> _T:
        async with self._lock:
            return await asyncio.to_thread(fn, *args)  # type: ignore[arg-type]

    async def connect(self) -> None:
        await self._run(self._connect_sync)

    async def close(self) -> None:
        await self._run(self._close_sync)

    async def select(self, folder: str) -> int:
        result: int = await self._run(self._select_sync, folder)
        return result

    async def uid_search_after(self, last_uid: int) -> list[int]:
        result: list[int] = await self._run(self._search_sync, last_uid)
        return result

    async def fetch_raw(self, uid: int) -> bytes:
        result: bytes = await self._run(self._fetch_sync, uid)
        return result

    async def idle(self, timeout: float) -> bool:
        result: bool = await self._run(self._idle_sync, timeout)
        return result

    async def move(self, uid: int, dest_folder: str) -> None:
        await self._run(self._move_sync, uid, dest_folder)

    async def move_by_message_id(self, message_id: str, dest_folder: str) -> bool:
        result: bool = await self._run(self._move_by_message_id_sync, message_id, dest_folder)
        return result
