"""Thin, injectable wrapper around the LiteParse CLI (local text + OCR parser).

LiteParse (`pip install liteparse`, wrapping the `@llamaindex/liteparse` Node
CLI) does its own local text extraction and, per page, falls back to local OCR
(ImageMagick + Tesseract) without any network call. It is the *primary* parser
for PDFs and images in document_pipeline/pipeline.py; Gemini vision
(document_pipeline/vision.py) is only used afterwards, for the specific pages
LiteParse itself could not recover, or for the whole document if LiteParse is
unavailable on this host.

`parse()` is a blocking subprocess call, not a network request -- callers run
it the same way pipeline.inspect_document() already ran PyMuPDF's synchronous
text extraction. `install_if_not_available` is always False: a worker process
must never trigger `npm install -g ...` as a side effect of job traffic. Pin
the CLI (Node.js, @llamaindex/liteparse, ImageMagick, tesseract-ocr) at deploy
time instead -- see deploy/README.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.logging import get_logger

try:
    from liteparse import CLINotFoundError, LiteParse, ParseError
except ImportError:  # pragma: no cover - exercised only if the dependency is missing
    LiteParse = None  # type: ignore[assignment,misc]

    class ParseError(Exception):  # type: ignore[no-redef]
        pass

    class CLINotFoundError(Exception):  # type: ignore[no-redef]
        pass


logger = get_logger(__name__)


@dataclass(frozen=True)
class LiteParsePage:
    page: int
    text: str
    # Average of this page's OCR'd text items' confidence scores. None when the
    # page had a real text layer (no OCR ran) -- never treat None as "low".
    confidence: float | None


class LiteParseUnavailable(Exception):
    """The CLI is missing, the process failed, or it timed out.

    Callers fall back to the whole-document Gemini vision path (see
    scripts/dpr_worker.py) when this is raised -- the one scenario where a
    per-page fallback isn't possible, because LiteParse produced nothing at
    all to decide per-page usability from.
    """


class LiteParseClient:
    def __init__(
        self,
        *,
        cli_path: str | None = None,
        ocr_language: str = "en",
        timeout: float = 120.0,
        parser: object | None = None,
    ) -> None:
        self._cli_path = cli_path
        self._ocr_language = ocr_language
        self._timeout = timeout
        # Test seam: inject a fake with a `.parse(...)`/`.cli_path` surface
        # instead of spawning the real Node CLI subprocess.
        self._parser = parser

    def _get_parser(self) -> LiteParse:
        if self._parser is not None:
            return self._parser
        if LiteParse is None:
            raise LiteParseUnavailable("the 'liteparse' package is not installed")
        # install_if_not_available=False: a missing CLI must raise, never
        # silently `npm install -g` on a worker serving real job traffic.
        self._parser = LiteParse(
            cli_path=self._cli_path, install_if_not_available=False
        )
        return self._parser

    def ensure_available(self) -> bool:
        """One cheap resolution check, meant for worker startup: logs a clear
        warning if the CLI can't be found, so a broken install is visible in
        logs immediately rather than only showing up later as every document
        silently taking the costlier whole-document Gemini vision path."""
        try:
            _ = self._get_parser().cli_path
        except (LiteParseUnavailable, CLINotFoundError) as exc:
            logger.warning("dpr_liteparse_unavailable reason=%s", exc)
            return False
        return True

    def parse(self, data: bytes, *, max_pages: int) -> list[LiteParsePage]:
        try:
            parser = self._get_parser()
            result = parser.parse(
                data,
                ocr_enabled=True,
                ocr_language=self._ocr_language,
                max_pages=max_pages,
                timeout=self._timeout,
            )
        except (ParseError, CLINotFoundError, TimeoutError) as exc:
            raise LiteParseUnavailable(str(exc)) from exc
        pages: list[LiteParsePage] = []
        for p in result.pages:
            confidences = [
                item.confidence for item in p.textItems if item.confidence is not None
            ]
            avg_confidence = (
                sum(confidences) / len(confidences) if confidences else None
            )
            pages.append(
                LiteParsePage(
                    page=p.pageNum, text=p.text or "", confidence=avg_confidence
                )
            )
        return pages
