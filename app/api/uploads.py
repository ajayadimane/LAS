"""Reading an uploaded file.

Files are uploaded as the raw request body (the browser sends the File as-is,
with its own Content-Type) and named by a `filename` query parameter. That
avoids multipart, and lets us reject an oversized upload from its
Content-Length before reading it.
"""

import os
from urllib.parse import quote

from fastapi import HTTPException, Request, status
from fastapi.responses import Response

from app.core.config import Settings
from app.services.documents import StoredFile


async def read_upload(
    request: Request, filename: str, settings: Settings
) -> StoredFile:
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip()
    if content_type not in settings.document_allowed_types:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Unsupported file type '{content_type or 'none'}'. Allowed: "
            + ", ".join(settings.document_allowed_types),
        )
    too_large = HTTPException(
        status.HTTP_413_CONTENT_TOO_LARGE,
        f"File is larger than {settings.document_max_bytes} bytes.",
    )
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > settings.document_max_bytes:
        raise too_large
    data = await request.body()
    if len(data) > settings.document_max_bytes:
        raise too_large
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is empty.")
    name = os.path.basename(filename.replace("\\", "/")).strip()[:200]
    if not name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A file name is required.")
    return StoredFile(name=name, content_type=content_type, data=data)


def file_response(file: StoredFile) -> Response:
    return Response(
        content=file.data,
        media_type=file.content_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(file.name)}",
            "Cache-Control": "no-store",
        },
    )
