"""Standalone regression tests: `python -m unittest tests/test_dpr_pdf_download_regression_standalone.py`.

No Postgres, vendor, Gemini or pytest plugins required.
"""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import unittest
from unittest.mock import patch

from app.services.dpr_agentic import report_store


class _Result:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class _Connection:
    def __init__(self, engine):
        self.engine = engine

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.engine.sql = str(sql)
        self.engine.params = params
        return _Result(self.engine.row)


class _Engine:
    def __init__(self, row):
        self.row = row
        self.sql = ""
        self.params = {}

    def connect(self):
        return _Connection(self)


class PdfSelectionTests(unittest.TestCase):
    def test_default_selects_latest_report_before_pdf_status(self):
        engine = _Engine(("FAILED", None))
        self.assertIsNone(report_store.get_report_pdf(engine, "svc:1"))
        self.assertIn("ORDER BY version DESC LIMIT 1", engine.sql)
        self.assertIn("render_status = 'RENDERED'", engine.sql)
        self.assertNotIn("pdf_render_status = 'RENDERED'", engine.sql)

    def test_explicit_version_is_preserved(self):
        engine = _Engine(("RENDERED", memoryview(b"%PDF-1.7\nhello")))
        self.assertEqual(report_store.get_report_pdf(engine, "svc:1", version=3), b"%PDF-1.7\nhello")
        self.assertEqual(engine.params["version"], 3)
        self.assertNotIn("ORDER BY", engine.sql)

    def test_invalid_or_missing_pdf_bytes_not_served(self):
        for row in (("RENDERED", b"not a pdf"), ("FAILED", b"%PDF-valid"), None):
            with self.subTest(row=row):
                self.assertIsNone(report_store.get_report_pdf(_Engine(row), "svc:1"))

    def test_dependency_diagnostics_identify_missing_jinja2(self):
        def find_spec(name):
            return None if name == "jinja2" else object()

        with patch.object(report_store.importlib.util, "find_spec", side_effect=find_spec):
            self.assertEqual(report_store.missing_pdf_dependencies(), ("jinja2",))
            self.assertFalse(report_store.check_pdf_renderer_ready())

    def test_valid_dependencies_pass(self):
        with patch.object(report_store.importlib.util, "find_spec", return_value=object()):
            self.assertTrue(report_store.check_pdf_renderer_ready())


if __name__ == "__main__":
    unittest.main()
