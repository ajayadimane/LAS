"""The UPI payment QR for emails (user, 2026-11-16: "Pay now with the same QR").

Same payment link as the dashboards (frontend/src/components/ui/UpiQr.tsx
`upiLink`) so the QR in an email, on the BD's Payment page and on the
customer's dashboard all pay the same account. The QR is drawn here as a PNG
and embedded in the email as an inline image: Gmail and Outlook do not show
SVG, and a hosted QR service would hand the payment details to a third party.

The UPI id is a placeholder (settings.intake_pay_now_upi_id, the same value
as the frontend's UPI_ID) until the real account exists.
"""

import struct
import zlib
from urllib.parse import quote

from reportlab.graphics.barcode.qrencoder import QRCode, QRErrorCorrectLevel


def upi_link(upi_id: str, amount: int | None, note: str) -> str:
    """upi://pay link; without an amount the payer types it in."""
    link = f"upi://pay?pa={upi_id}&pn=LAS%20Services"
    if amount is not None:
        link += f"&am={amount:.2f}"
    return f"{link}&cu=INR&tn={quote(note)}"


def _chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))


def qr_png(text: str, *, scale: int = 6, border: int = 4) -> bytes:
    """`text` as a black-on-white QR (error correction M), as a PNG."""
    qr = QRCode(None, QRErrorCorrectLevel.M)  # version chosen to fit
    qr.addData(text)
    qr.make()
    n = qr.getModuleCount()
    size = (n + 2 * border) * scale
    white, black = 0xFF, 0x00
    rows = bytearray()
    for y in range(size):
        r = y // scale - border
        row = bytearray([white] * size)
        if 0 <= r < n:
            for c in range(n):
                if qr.isDark(r, c):
                    x0 = (c + border) * scale
                    row[x0 : x0 + scale] = bytes([black]) * scale
        rows += b"\x00" + row  # filter type 0 (none) per scanline
    header = struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)  # 8-bit grayscale
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(rows), 9))
        + _chunk(b"IEND", b"")
    )
