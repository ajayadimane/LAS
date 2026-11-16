"""ClamAV scanner over clamd INSTREAM."""

from __future__ import annotations

import asyncio
import io

from las.common.interfaces import AVResult


class ClamdScanner:
    def __init__(self, host: str, port: int, timeout: float = 60.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    def _scan_sync(self, data: bytes) -> AVResult:
        import clamd

        try:
            cd = clamd.ClamdNetworkSocket(self.host, self.port, timeout=self.timeout)
            result = cd.instream(io.BytesIO(data))
        except Exception as e:  # noqa: BLE001 - scanner outage must not pass files as clean
            return AVResult("ERROR", type(e).__name__)
        status, signature = result.get("stream", ("ERROR", None))
        if status == "OK":
            return AVResult("CLEAN")
        if status == "FOUND":
            return AVResult("INFECTED", signature)
        return AVResult("ERROR", signature)

    async def scan(self, data: bytes) -> AVResult:
        return await asyncio.to_thread(self._scan_sync, data)
