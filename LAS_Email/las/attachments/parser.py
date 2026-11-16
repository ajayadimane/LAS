"""LiteParse adapter (run-llama/liteparse, Apache-2.0): local layout-aware text extraction for
PDF / DOCX / images with bundled Tesseract OCR. No cloud calls. DOCX needs LibreOffice installed.

We use the layout-preserved *text* output: in testing it keeps each table row on one line
("Machinery   3,50,000"), which DPR figure extraction depends on; its Markdown output grouped
columns instead of rows for position-based tables.
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
from pathlib import Path
from typing import Any

from las.common.interfaces import ParsedDocument

SUFFIX = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "image/jpeg": ".jpg",
    "image/png": ".png",
}


class LiteParseParser:
    provider = "liteparse"

    def __init__(
        self,
        *,
        ocr_language: str = "eng+hin+kan",
        max_pages: int = 80,
        dpi: float | None = None,
        tessdata_path: str | None = None,
    ) -> None:
        import liteparse

        self.model = f"liteparse-{getattr(liteparse, '__version__', 'unknown')}"
        self._kwargs: dict[str, Any] = {
            "output_format": "text",
            "quiet": True,
            "ocr_enabled": True,
            "ocr_language": ocr_language,
            "max_pages": max_pages,
            "continue_on_page_error": True,
        }
        if dpi:
            self._kwargs["dpi"] = dpi
        if tessdata_path:
            self._kwargs["tessdata_path"] = tessdata_path
        self._local = (
            threading.local()
        )  # one parser per worker thread (thread safety not documented)

    def _parser(self) -> Any:
        parser = getattr(self._local, "parser", None)
        if parser is None:
            from liteparse import LiteParse

            parser = self._local.parser = LiteParse(**self._kwargs)
        return parser

    def _parse_sync(self, data: bytes, mime_type: str) -> ParsedDocument:
        from liteparse import ParseError

        with tempfile.TemporaryDirectory(prefix="las-parse-") as tmp:
            path = Path(tmp) / f"input{SUFFIX.get(mime_type, '.bin')}"
            path.write_bytes(data)
            try:
                result = self._parser().parse(path)
            except ParseError as e:
                if "password" in str(e).lower():
                    return ParsedDocument("", 0, self.provider, self.model, password_required=True)
                raise
        pages = int(getattr(result, "total_pages", 0) or len(result.pages) or 1)
        return ParsedDocument(result.text or "", pages, self.provider, self.model)

    async def parse(self, data: bytes, mime_type: str) -> ParsedDocument:
        return await asyncio.to_thread(self._parse_sync, data, mime_type)
