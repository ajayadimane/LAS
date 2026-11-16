"""Object store on the local server's filesystem, encrypted at rest (AES-256-GCM per object).

Every process (API, workers, IMAP listener) must see the same ``LOCAL_STORE_PATH``. Outside dev the
key must come from ``LOCAL_STORE_KEY_B64``; lose it and every stored file is unreadable, so back it
up separately from the data. Downloads are served by the API as signed, expiring ``/files/`` links.
"""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path
from urllib.parse import quote

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from las.common.signing import sign_token


class LocalEncryptedStore:
    """Files under ``root``; each object is ``nonce(12) || AESGCM(ciphertext)``."""

    def __init__(self, root: str, key: bytes, *, public_base_url: str, signing_key: str) -> None:
        if len(key) != 32:
            raise ValueError("local store key must be 32 bytes")
        self.root = Path(root)
        self._aead = AESGCM(key)
        self._public = public_base_url.rstrip("/")
        self._signing_key = signing_key

    @classmethod
    def from_settings(
        cls,
        path: str,
        key_b64: str,
        *,
        public_base_url: str,
        signing_key: str,
        allow_dev_key: bool = False,
    ) -> LocalEncryptedStore:
        root = Path(path)
        root.mkdir(parents=True, exist_ok=True)
        if key_b64:
            key = base64.b64decode(key_b64)
        elif not allow_dev_key:
            raise RuntimeError(
                "LOCAL_STORE_KEY_B64 is required outside dev "
                "(python -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())')"
            )
        else:  # dev convenience: persistent random key next to the data
            key_file = root / ".dev-key"
            if not key_file.exists():
                key_file.write_bytes(os.urandom(32))
                key_file.chmod(0o600)
            key = key_file.read_bytes()
        return cls(str(root), key, public_base_url=public_base_url, signing_key=signing_key)

    def _path(self, uri: str) -> Path:
        if not uri.startswith("local://"):
            raise ValueError(f"not a local uri: {uri}")
        rel = uri[len("local://") :]
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("path escapes store root")
        return p

    def uri_for(self, key: str) -> str:
        return f"local://{key}"

    async def put(self, key: str, data: bytes, *, content_type: str) -> str:
        uri = self.uri_for(key)
        path = self._path(uri)
        nonce = os.urandom(12)
        blob = nonce + self._aead.encrypt(nonce, data, key.encode())

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_bytes(blob)
            tmp.replace(path)

        await asyncio.to_thread(_write)
        return uri

    async def get(self, uri: str) -> bytes:
        blob = await asyncio.to_thread(self._path(uri).read_bytes)
        key = uri[len("local://") :]
        return self._aead.decrypt(blob[:12], blob[12:], key.encode())

    async def signed_url(
        self,
        uri: str,
        *,
        ttl_seconds: int,
        filename: str,
        content_type: str = "application/pdf",
        disposition: str = "attachment",
    ) -> str:
        token = sign_token(
            self._signing_key,
            {"uri": uri, "fn": filename, "mime": content_type, "disp": disposition},
            ttl_seconds,
        )
        return f"{self._public}/files/{quote(token)}"
