import asyncio
from collections.abc import Iterator

import pytest
from sqlalchemy import select, update

from las.common.db import SessionFactory
from las.common.deps import Deps
from las.common.models import InboundEmail, MailboxCheckpoint
from las.ingest.listener import catch_up, load_checkpoint, move_finished
from las.ingest.zoho_imap import ZohoImapMailbox
from tests.fake_imap_server import Store, start_server
from tests.helpers import make_mail


@pytest.fixture
def imap() -> Iterator[tuple[int, Store]]:
    server, store = start_server()
    yield server.server_address[1], store
    server.shutdown()


def _mb(port: int) -> ZohoImapMailbox:
    return ZohoImapMailbox("127.0.0.1", port, "las@integramicro.co.in", "x", use_ssl=False)


@pytest.mark.imap
async def test_adapter_search_fetch_idle_move(imap: tuple[int, Store]) -> None:
    port, store = imap
    store.deliver(make_mail(subject="one"))
    mb = _mb(port)
    await mb.connect()
    assert await mb.select("INBOX") == 777
    uids = await mb.uid_search_after(0)
    assert uids == [1]
    assert b"Subject: one" in await mb.fetch_raw(1)
    assert await mb.uid_search_after(1) == []  # "2:*" matching the last UID is filtered out
    assert await mb.idle(0.3) is False
    loop = asyncio.get_running_loop()
    loop.call_later(0.2, store.deliver, make_mail(subject="two"))
    assert await mb.idle(5) is True
    await mb.move(1, "LAS/Processed")
    assert 1 in store.folders["LAS/Processed"] and 1 not in store.folders["INBOX"]
    await mb.close()


@pytest.mark.imap
async def test_listener_checkpoint_dedupe_and_move(
    imap: tuple[int, Store], deps: Deps, db: SessionFactory
) -> None:
    port, store = imap
    raw = make_mail(subject="first")
    store.deliver(raw)
    store.deliver(raw)  # same Message-ID delivered twice
    store.deliver(make_mail(subject="second"))
    mb = _mb(port)
    await mb.connect()
    uv = await mb.select("INBOX")
    enqueued: list[int] = []
    last = await catch_up(
        mb,
        db,
        deps.store,
        enqueued.append,
        folder="INBOX",
        uidvalidity=uv,
        last_uid=await load_checkpoint(db, mb.mailbox, "INBOX", uv),
    )
    assert last == 3
    assert len(enqueued) == 2  # duplicate Message-ID persisted once, enqueued once
    async with db() as s:
        cp = (await s.execute(select(MailboxCheckpoint))).scalar_one()
        assert (cp.uidvalidity, cp.last_uid) == (777, 3)
        await s.execute(update(InboundEmail).values(processing_status="PROCESSED"))
        await s.commit()
    assert (
        await catch_up(
            mb, db, deps.store, enqueued.append, folder="INBOX", uidvalidity=uv, last_uid=last
        )
        == 3
    )
    moved = await move_finished(mb, db, deps.settings, folder="INBOX", uidvalidity=uv)
    assert moved == 2 and len(store.folders["LAS/Processed"]) == 2
    # UIDVALIDITY change resets the checkpoint.
    assert await load_checkpoint(db, mb.mailbox, "INBOX", 999) == 0
    await mb.close()
