"""Mailbox listener process (``las-ingest``).

Loop: connect → SELECT INBOX → catch-up ``UID SEARCH`` from the DB checkpoint → persist each new
mail and enqueue processing → IDLE (max ``imap_catchup_seconds``, re-IDLE well before Zoho's ~29
min cut-off) → repeat. Mails whose processing finished are MOVEd to LAS/Processed|Rejected|Review
here, on the listener's connection. UIDVALIDITY changes reset the checkpoint; Message-ID dedupe
makes the re-read harmless.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from las.common.db import SessionFactory
from las.common.interfaces import MailboxAdapter, ObjectStore
from las.common.logging import configure_logging, get_logger
from las.common.models import InboundEmail, MailboxCheckpoint
from las.common.settings import Settings, get_settings
from las.common.timeutil import utcnow
from las.ingest.persist import ingest_raw
from las.ingest.zoho_imap import ImapError

log = get_logger(__name__)

Enqueue = Callable[[int], None]

FOLDER_FOR_STATUS = {
    "PROCESSED": "processed",
    "IGNORED": "processed",
    "REJECTED": "rejected",
    "NEEDS_REVIEW": "review",
}


def provider_uid(folder: str, uidvalidity: int, uid: int) -> str:
    return f"{folder}|{uidvalidity}|{uid}"


async def load_checkpoint(
    factory: SessionFactory, mailbox: str, folder: str, uidvalidity: int
) -> int:
    async with factory() as s:
        cp = (
            await s.execute(
                select(MailboxCheckpoint)
                .where(MailboxCheckpoint.mailbox == mailbox, MailboxCheckpoint.folder == folder)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if cp is None:
            s.add(
                MailboxCheckpoint(
                    mailbox=mailbox, folder=folder, uidvalidity=uidvalidity, last_uid=0
                )
            )
            await s.commit()
            return 0
        if cp.uidvalidity != uidvalidity:
            log.warning("imap_uidvalidity_changed", old=cp.uidvalidity, new=uidvalidity)
            cp.uidvalidity, cp.last_uid, cp.updated_at = uidvalidity, 0, utcnow()
            await s.commit()
            return 0
        last = cp.last_uid
        await s.commit()
        return last


async def save_checkpoint(
    factory: SessionFactory, mailbox: str, folder: str, uidvalidity: int, last_uid: int
) -> None:
    async with factory() as s:
        await s.execute(
            insert(MailboxCheckpoint)
            .values(mailbox=mailbox, folder=folder, uidvalidity=uidvalidity, last_uid=last_uid)
            .on_conflict_do_update(
                index_elements=["mailbox", "folder"],
                set_={"uidvalidity": uidvalidity, "last_uid": last_uid, "updated_at": utcnow()},
                where=(MailboxCheckpoint.last_uid < last_uid)
                | (MailboxCheckpoint.uidvalidity != uidvalidity),
            )
        )
        await s.commit()


async def catch_up(
    mb: MailboxAdapter,
    factory: SessionFactory,
    store: ObjectStore,
    enqueue: Enqueue,
    *,
    folder: str,
    uidvalidity: int,
    last_uid: int,
) -> int:
    for uid in await mb.uid_search_after(last_uid):
        raw = await mb.fetch_raw(uid)
        result = await ingest_raw(
            factory, store, raw, provider_uid=provider_uid(folder, uidvalidity, uid)
        )
        # stale RECEIVED rows are re-enqueued by the sweeper job; a mail that failed to parse is
        # already terminal (NEEDS_REVIEW) and must not be handed to the pipeline.
        if result.created and result.processing_status == "RECEIVED":
            enqueue(result.inbound_id)
        last_uid = uid
        await save_checkpoint(factory, mb.mailbox, folder, uidvalidity, last_uid)
        log.info("mail_ingested", inbound_id=result.inbound_id, uid=uid, created=result.created)
    return last_uid


async def move_finished(
    mb: MailboxAdapter,
    factory: SessionFactory,
    settings: Settings,
    *,
    folder: str,
    uidvalidity: int,
    limit: int = 100,
) -> int:
    """MOVE mails whose processing reached a terminal status; record where they went."""
    dest_by_kind = {
        "processed": settings.imap_folder_processed,
        "rejected": settings.imap_folder_rejected,
        "review": settings.imap_folder_review,
    }
    moved = 0
    async with factory() as s:
        rows = (
            (
                await s.execute(
                    select(InboundEmail)
                    .where(
                        InboundEmail.provider_uid.like(f"{folder}|{uidvalidity}|%"),
                        InboundEmail.processing_status.in_(tuple(FOLDER_FOR_STATUS)),
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            assert row.provider_uid is not None
            uid = int(row.provider_uid.rsplit("|", 1)[1])
            dest = dest_by_kind[FOLDER_FOR_STATUS[row.processing_status]]
            try:
                await mb.move(uid, dest)
            except Exception:  # noqa: BLE001 - message may be gone; don't retry forever
                log.warning("imap_move_failed", inbound_id=row.inbound_id)
            row.provider_uid = f"{dest}|moved"
            moved += 1
        await s.commit()
    return moved


async def run_listener(
    make_mailbox: Callable[[], MailboxAdapter],
    factory: SessionFactory,
    store: ObjectStore,
    enqueue: Enqueue,
    settings: Settings,
    stop: asyncio.Event,
) -> None:
    folder = settings.imap_inbox
    idle_timeout = float(min(settings.imap_idle_renew_seconds, settings.imap_catchup_seconds))
    backoff = 1.0
    while not stop.is_set():
        mb = make_mailbox()
        try:
            await mb.connect()
            uidvalidity = await mb.select(folder)
            last_uid = await load_checkpoint(factory, mb.mailbox, folder, uidvalidity)
            backoff = 1.0
            while not stop.is_set():
                last_uid = await catch_up(
                    mb,
                    factory,
                    store,
                    enqueue,
                    folder=folder,
                    uidvalidity=uidvalidity,
                    last_uid=last_uid,
                )
                await move_finished(mb, factory, settings, folder=folder, uidvalidity=uidvalidity)
                idle = asyncio.create_task(mb.idle(idle_timeout))
                stopper = asyncio.create_task(stop.wait())
                done, _ = await asyncio.wait({idle, stopper}, return_when=asyncio.FIRST_COMPLETED)
                if idle not in done:
                    await idle  # IDLE ends within its timeout; let it finish cleanly
                else:
                    stopper.cancel()
                    idle.result()
        except Exception as exc:
            if isinstance(exc, (ImapError, ConnectionError, OSError)):
                # The server dropped the connection (Zoho sometimes closes it right after IDLE):
                # expected, so no traceback. Reconnect + catch-up means no mail is missed.
                log.warning("imap_reconnect", reason=str(exc)[:200], backoff=backoff)
            else:
                log.exception("imap_listener_error", backoff=backoff)
            with contextlib.suppress(TimeoutError, asyncio.TimeoutError):  # distinct on 3.10
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            backoff = min(backoff * 2, 300.0)
        finally:
            try:
                await mb.close()
            except Exception:  # noqa: BLE001 - connection already broken
                log.debug("imap_close_failed")


def main() -> None:
    from las.common.db import get_session_factory
    from las.common.deps import build_deps
    from las.ingest.zoho_imap import ZohoImapMailbox
    from las.worker.tasks import enqueue_process_inbound

    s = get_settings()
    configure_logging(s.log_level, json=s.log_json)
    deps = build_deps(s)

    def make() -> MailboxAdapter:
        return ZohoImapMailbox(
            s.imap_host,
            s.imap_port,
            s.imap_username,
            s.imap_password.get_secret_value(),
            use_ssl=s.imap_use_ssl,
            mailbox=s.las_mailbox_address,
        )

    async def _run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await run_listener(
            make, get_session_factory(), deps.store, enqueue_process_inbound, s, stop
        )

    asyncio.run(_run())
