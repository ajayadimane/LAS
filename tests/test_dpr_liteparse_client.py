"""Offline tests for app/services/dpr_agentic/document_pipeline/liteparse_client.py.

Never spawns the real @llamaindex/liteparse CLI subprocess -- a fake parser
is injected in its place (see LiteParseClient's `parser=` constructor arg).
"""

from types import SimpleNamespace

import pytest

from app.services.dpr_agentic.document_pipeline.liteparse_client import (
    CLINotFoundError,
    LiteParseClient,
    LiteParseUnavailable,
    ParseError,
)


def _text_item(confidence):
    return SimpleNamespace(confidence=confidence)


def _page(page_num, text, confidences=()):
    return SimpleNamespace(
        pageNum=page_num, text=text, textItems=[_text_item(c) for c in confidences]
    )


class FakeParser:
    def __init__(self, *, pages=None, error=None, cli_path_error=None):
        self._pages = pages or []
        self._error = error
        self._cli_path_error = cli_path_error

    @property
    def cli_path(self):
        if self._cli_path_error:
            raise self._cli_path_error
        return "/usr/local/bin/liteparse"

    def parse(self, data, *, ocr_enabled, ocr_language, max_pages, timeout):
        if self._error:
            raise self._error
        return SimpleNamespace(pages=self._pages)


def test_parse_maps_pages_and_averages_confidence():
    parser = FakeParser(
        pages=[
            _page(1, "a real text layer, no OCR ran"),
            _page(2, "ocr'd text", confidences=[0.9, 0.8]),
        ]
    )
    client = LiteParseClient(parser=parser)

    pages = client.parse(b"pdf-bytes", max_pages=10)

    assert pages[0].page == 1
    assert pages[0].confidence is None
    assert pages[1].page == 2
    assert pages[1].confidence == pytest.approx(0.85)


def test_parse_wraps_parse_error_as_unavailable():
    client = LiteParseClient(parser=FakeParser(error=ParseError("exit code 1")))
    with pytest.raises(LiteParseUnavailable):
        client.parse(b"pdf-bytes", max_pages=10)


def test_parse_wraps_cli_not_found_as_unavailable():
    client = LiteParseClient(parser=FakeParser(error=CLINotFoundError("no CLI")))
    with pytest.raises(LiteParseUnavailable):
        client.parse(b"pdf-bytes", max_pages=10)


def test_parse_wraps_timeout_as_unavailable():
    client = LiteParseClient(parser=FakeParser(error=TimeoutError("too slow")))
    with pytest.raises(LiteParseUnavailable):
        client.parse(b"pdf-bytes", max_pages=10)


def test_ensure_available_true_when_cli_resolves():
    client = LiteParseClient(parser=FakeParser())
    assert client.ensure_available() is True


def test_ensure_available_false_and_logs_when_cli_missing(caplog):
    client = LiteParseClient(
        parser=FakeParser(cli_path_error=CLINotFoundError("missing"))
    )
    with caplog.at_level("WARNING"):
        assert client.ensure_available() is False
    assert "dpr_liteparse_unavailable" in caplog.text
