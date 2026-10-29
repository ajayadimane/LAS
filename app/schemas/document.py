"""A stored file, as the dashboard sees it."""

from pydantic import BaseModel


class FileInfo(BaseModel):
    """What the dashboard sees about a stored file. The bytes are fetched
    separately so lists never carry them."""

    name: str
    content_type: str
    size_bytes: int
