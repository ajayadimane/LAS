"""Settings normalisation."""

import pytest

from app.core.config import Settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # As the sandbox host was written on 2026-10-27; the dot broke DNS.
        ("https://10.10.20.153.:8080", "https://10.10.20.153:8080"),
        ("https://10.10.20.153:8080/", "https://10.10.20.153:8080"),
        ("https://10.10.20.153:8080", "https://10.10.20.153:8080"),
        ("https://api.workflo.ai.", "https://api.workflo.ai"),
    ],
)
def test_vendor_base_url_is_normalised(raw, expected):
    assert Settings(vendor_base_url=raw).vendor_base_url == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ""),
        ("/", ""),
        ("las-api", "/las-api"),
        ("/las-api/", "/las-api"),
        (" /demo/las-api ", "/demo/las-api"),
    ],
)
def test_context_path_is_normalised(raw, expected):
    assert Settings(context_path=raw).context_path == expected
