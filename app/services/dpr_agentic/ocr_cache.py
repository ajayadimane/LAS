"""Reuses Gemini-vision OCR text for a document that is analysed again.

Gemini reads the same scanned page slightly differently on each call ("1.5"
one run, "1.6" the next), and everything downstream -- extraction, ratios,
findings -- inherits the difference. Keyed by the document's sha256, page
number and OCR model, the text recovered the first time is reused on every
later run of the same file, so a re-run analyses identical text (and does
not pay for the OCR again).

Two stores:
- PostgresOcrCache (worker): Fernet-encrypted with the staging key, like
  the staged upload itself, and kept only `ttl_days` (purged by the worker's
  poll loop). This is document content held after a run ends; ttl_days=0
  disables the cache entirely.
- FileOcrCache (scripts/dpr_eval.py): plain JSON under eval/ocr_cache/,
  gitignored, next to the cassettes that already hold the same text.

cached_ocr() wraps any OCR callable with extract_pages_via_vision's
signature, so prepare_document() needs no other change.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


class OcrCache(Protocol):
    def get_many(self, sha256: str, model: str, pages: list[int]) -> dict[int, str]: ...

    def put_many(self, sha256: str, model: str, texts: dict[int, str]) -> None: ...


OcrCallable = Callable[..., Awaitable[list[dict]]]


def cached_ocr(
    ocr: OcrCallable, cache: OcrCache | None, sha256: str, model: str
) -> OcrCallable:
    """`ocr` with per-page reuse. Only pages missing from the cache are sent
    to Gemini; only non-empty text is stored (an empty page must be retried,
    never remembered as empty). A cache error never fails the OCR."""
    if cache is None:
        return ocr

    async def wrapped(gemini, images, *, page_numbers=None):
        numbers = (
            list(page_numbers) if page_numbers else list(range(1, len(images) + 1))
        )
        if len(numbers) != len(images):
            raise ValueError("Target page numbers do not match rendered images")
        try:
            hits = cache.get_many(sha256, model, numbers)
        except Exception:
            logger.warning("dpr_ocr_cache_read_failed", exc_info=True)
            hits = {}
        missing = [
            (n, img)
            for n, img in zip(numbers, images, strict=True)
            if not (hits.get(n) or "").strip()
        ]
        fresh: dict[int, str] = {}
        if missing:
            pages = await ocr(
                gemini,
                [img for _, img in missing],
                page_numbers=[n for n, _ in missing],
            )
            fresh = {p["page"]: p["text"] for p in pages}
            try:
                cache.put_many(
                    sha256, model, {n: t for n, t in fresh.items() if t.strip()}
                )
            except Exception:
                logger.warning("dpr_ocr_cache_write_failed", exc_info=True)
        logger.info(
            "dpr_ocr_cache pages=%d reused=%d ocr=%d",
            len(numbers),
            len(numbers) - len(missing),
            len(missing),
        )
        return [
            {"page": n, "text": hits.get(n) if n not in fresh else fresh[n]}
            for n in numbers
        ]

    return wrapped


class FileOcrCache:
    def __init__(self, directory: Path):
        self.directory = directory

    def _path(self, sha256: str, model: str) -> Path:
        safe_model = "".join(c if c.isalnum() or c in "-._" else "_" for c in model)
        return self.directory / f"{sha256}.{safe_model}.json"

    def _load(self, sha256: str, model: str) -> dict[str, str]:
        path = self._path(sha256, model)
        return json.loads(path.read_text()) if path.exists() else {}

    def get_many(self, sha256, model, pages):
        data = self._load(sha256, model)
        return {p: data[str(p)] for p in pages if str(p) in data}

    def put_many(self, sha256, model, texts):
        if not texts:
            return
        data = self._load(sha256, model)
        data.update({str(p): t for p, t in texts.items()})
        self.directory.mkdir(parents=True, exist_ok=True)
        self._path(sha256, model).write_text(json.dumps(data))


class PostgresOcrCache:
    """Table dpr_ocr_cache (migration 011). Text is Fernet-encrypted."""

    def __init__(self, engine, key: bytes, ttl_days: int):
        from cryptography.fernet import Fernet

        self.engine = engine
        self.fernet = Fernet(key)
        self.ttl_days = ttl_days

    def get_many(self, sha256, model, pages):
        from sqlalchemy import bindparam, text

        stmt = text(
            "SELECT page, ciphertext FROM dpr_ocr_cache WHERE document_sha256=:sha "
            "AND model=:model AND page IN :pages AND expires_at > now()"
        ).bindparams(bindparam("pages", expanding=True))
        with self.engine.connect() as conn:
            rows = conn.execute(
                stmt, {"sha": sha256, "model": model, "pages": list(pages)}
            ).all()
        return {r.page: self.fernet.decrypt(bytes(r.ciphertext)).decode() for r in rows}

    def put_many(self, sha256, model, texts):
        from sqlalchemy import text

        if not texts:
            return
        with self.engine.begin() as conn:
            for page, page_text in texts.items():
                conn.execute(
                    text("""
                        INSERT INTO dpr_ocr_cache
                            (document_sha256, page, model, ciphertext, expires_at)
                        VALUES (:sha, :page, :model, :ct,
                                now() + make_interval(days => :ttl))
                        ON CONFLICT (document_sha256, page, model) DO UPDATE
                          SET ciphertext = EXCLUDED.ciphertext,
                              expires_at = EXCLUDED.expires_at
                    """),
                    {
                        "sha": sha256,
                        "page": page,
                        "model": model,
                        "ct": self.fernet.encrypt(page_text.encode()),
                        "ttl": self.ttl_days,
                    },
                )


def purge_expired_ocr_cache(engine) -> int:
    from sqlalchemy import text

    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM dpr_ocr_cache WHERE expires_at <= now()")
        )
    return result.rowcount or 0
