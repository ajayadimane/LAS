"""Gemini-vision OCR text is reused for a document analysed again
(ocr_cache.py), so a re-run reads identical text."""

from types import SimpleNamespace

import pytest

from app.services.dpr_agentic.document_input import prepare_document
from app.services.dpr_agentic.ocr_cache import (
    FileOcrCache,
    PostgresOcrCache,
    cached_ocr,
    purge_expired_ocr_cache,
)


class _CountingOcr:
    def __init__(self, texts=None):
        self.calls: list[list[int]] = []
        self.texts = texts or {}

    async def __call__(self, gemini, images, *, page_numbers=None):
        numbers = page_numbers or list(range(1, len(images) + 1))
        self.calls.append(list(numbers))
        return [
            {"page": n, "text": self.texts.get(n, f"text of page {n}")} for n in numbers
        ]


class _MemoryCache:
    def __init__(self):
        self.data: dict[tuple, str] = {}

    def get_many(self, sha256, model, pages):
        return {
            p: self.data[(sha256, model, p)]
            for p in pages
            if (sha256, model, p) in self.data
        }

    def put_many(self, sha256, model, texts):
        self.data.update({(sha256, model, p): t for p, t in texts.items()})


@pytest.mark.asyncio
async def test_second_run_reuses_text_and_only_reads_new_pages():
    ocr, cache = _CountingOcr(), _MemoryCache()
    first = await cached_ocr(ocr, cache, "abc", "m")(
        None, [b"1", b"2"], page_numbers=[3, 5]
    )
    second = await cached_ocr(ocr, cache, "abc", "m")(
        None, [b"1", b"2", b"3"], page_numbers=[3, 5, 6]
    )
    assert ocr.calls == [[3, 5], [6]]
    assert second[:2] == first
    assert [p["page"] for p in second] == [3, 5, 6]


@pytest.mark.asyncio
async def test_other_document_or_model_is_not_reused():
    ocr, cache = _CountingOcr(), _MemoryCache()
    await cached_ocr(ocr, cache, "abc", "m")(None, [b"1"])
    await cached_ocr(ocr, cache, "xyz", "m")(None, [b"1"])
    await cached_ocr(ocr, cache, "abc", "other-model")(None, [b"1"])
    assert len(ocr.calls) == 3


@pytest.mark.asyncio
async def test_empty_text_is_not_remembered():
    ocr, cache = _CountingOcr(texts={1: "   "}), _MemoryCache()
    await cached_ocr(ocr, cache, "abc", "m")(None, [b"1"])
    await cached_ocr(ocr, cache, "abc", "m")(None, [b"1"])
    assert ocr.calls == [[1], [1]]


@pytest.mark.asyncio
async def test_a_broken_cache_never_fails_ocr():
    class Broken:
        def get_many(self, *a):
            raise RuntimeError("db down")

        def put_many(self, *a):
            raise RuntimeError("db down")

    ocr = _CountingOcr()
    pages = await cached_ocr(ocr, Broken(), "abc", "m")(None, [b"1"])
    assert pages == [{"page": 1, "text": "text of page 1"}]


def test_file_cache_round_trip(tmp_path):
    cache = FileOcrCache(tmp_path / "ocr")
    assert cache.get_many("abc", "gemini/x", [1]) == {}
    cache.put_many("abc", "gemini/x", {1: "one"})
    cache.put_many("abc", "gemini/x", {2: "two"})
    assert cache.get_many("abc", "gemini/x", [1, 2, 3]) == {1: "one", 2: "two"}


@pytest.mark.asyncio
async def test_prepare_document_uses_the_cache_for_weak_pages():
    parsed = SimpleNamespace(
        pages=[
            {"page": 1, "text": "Detailed Project Report " * 40},
            {"page": 2, "text": ""},
        ],
        doc_class="DPR",
        confidence=0.97,
        mime="application/pdf",
        low_text_pages=[2],
        needs_vision=True,
    )
    settings = SimpleNamespace(
        dpr_agentic_liteparse_page_min_chars=50,
        dpr_agentic_vision_enabled=True,
        dpr_agentic_vision_max_pages=20,
    )
    ocr, cache = _CountingOcr(), _MemoryCache()
    kwargs = dict(
        settings=settings,
        gemini=SimpleNamespace(model="m"),
        inspect=lambda *a, **k: parsed,
        render=lambda *a, **k: [b"png"],
        ocr=ocr,
        document_sha256="abc",
        ocr_cache=cache,
    )
    first = await prepare_document(b"%PDF", "dpr.pdf", **kwargs)
    second = await prepare_document(b"%PDF", "dpr.pdf", **kwargs)
    assert ocr.calls == [[2]]
    assert first.pages == second.pages


def test_postgres_cache_encrypts_expires_and_purges(dpr_pg):
    from cryptography.fernet import Fernet
    from sqlalchemy import text

    cache = PostgresOcrCache(dpr_pg, Fernet.generate_key(), ttl_days=30)
    cache.put_many("abc", "m", {1: "Net profit 7.53"})
    cache.put_many("abc", "m", {1: "Net profit 7.53 (re-read)"})
    assert cache.get_many("abc", "m", [1, 2]) == {1: "Net profit 7.53 (re-read)"}
    with dpr_pg.begin() as conn:
        raw = conn.execute(text("SELECT ciphertext FROM dpr_ocr_cache")).scalar_one()
        assert b"Net profit" not in bytes(raw)
        conn.execute(
            text("UPDATE dpr_ocr_cache SET expires_at = now() - interval '1 day'")
        )
    assert cache.get_many("abc", "m", [1]) == {}
    assert purge_expired_ocr_cache(dpr_pg) == 1
