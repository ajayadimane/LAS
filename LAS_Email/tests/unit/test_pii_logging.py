import structlog

from las.common.logging import pii_mask_processor
from las.common.pii import mask_text


def test_masks_all_pii_kinds() -> None:
    raw = "Ramesh +91 98765-43210, ramesh.p@gmail.com, PAN ABCDE1234F, Aadhaar 2345 6789 0123"
    out = mask_text(raw)
    assert "98765" not in out and "43210" not in out
    assert "ramesh.p@gmail.com" not in out and "r***@gmail.com" in out
    assert "ABCDE1234F" not in out
    assert "2345 6789 0123" not in out and "0123" in out


def test_processor_masks_nested_and_drops_bodies() -> None:
    ev = pii_mask_processor(
        None,
        "info",
        {"event": "x", "who": {"mobile": "9876543210"}, "body": "secret", "items": ["a@b.com"]},
    )
    assert ev["who"]["mobile"] == "98xxxx3210"
    assert ev["body"] == "[redacted]"
    assert ev["items"] == ["a***@b.com"]


def test_structlog_pipeline_masks(capsys: object) -> None:
    from las.common.logging import configure_logging

    configure_logging("INFO")
    structlog.get_logger().info("customer_created mobile 9876543210")
    out = capsys.readouterr().out  # type: ignore[attr-defined]
    assert "9876543210" not in out and "98xxxx3210" in out
