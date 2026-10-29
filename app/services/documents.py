"""Reading person custom field values, and files stored in them.

A service's document is stored in that service's custom field (e.g.
`GeoCheck`) as a JSON string {"name", "contentType", "data"}, with `data`
base64-encoded, so the file name and type survive the round trip. A bare
base64 value (written by something other than us) is still readable.
"""

import base64
import binascii
import json
from dataclasses import dataclass
from typing import Any

from app.schemas.document import FileInfo

FLAT_CUSTOM_FIELD_PREFIX = "customFields."


@dataclass(frozen=True)
class StoredFile:
    name: str
    content_type: str
    data: bytes


def custom_field_values(person: Any) -> dict[str, str]:
    """A person's custom field values, from a person-details response.

    The real shape (getPersonDetailsByBrokerId, 2026-10-27) is flat dotted
    keys on the person: `"customFields.GeoCheck": "NA"`. A nested map of
    id -> value, or a list of {customFieldId, customFieldValue}, is still
    accepted in case another endpoint nests them.
    """
    if not isinstance(person, dict):
        return {}
    values: dict[str, str] = {
        key[len(FLAT_CUSTOM_FIELD_PREFIX) :]: v if isinstance(v, str) else str(v)
        for key, v in person.items()
        if key.startswith(FLAT_CUSTOM_FIELD_PREFIX) and v is not None
    }
    raw = person.get("customFields")
    if isinstance(raw, dict):
        for key, value in raw.items():
            if value is not None:
                values[str(key)] = value if isinstance(value, str) else str(value)
    elif isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = item.get("customFieldId")
            value = item.get("customFieldValue", item.get("value"))
            if key is not None and value is not None:
                values[str(key)] = value if isinstance(value, str) else str(value)
    return values


def encode_file(file: StoredFile) -> str:
    return json.dumps(
        {
            "name": file.name,
            "contentType": file.content_type,
            "data": base64.b64encode(file.data).decode("ascii"),
        },
        separators=(",", ":"),
    )


def _parse_file(raw: str, fallback_name: str) -> tuple[str, str, str] | None:
    """(name, content_type, base64 data) from a stored value, or None if empty."""
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except ValueError:
        obj = None
    if isinstance(obj, dict) and isinstance(obj.get("data"), str):
        return (
            str(obj.get("name") or fallback_name),
            str(obj.get("contentType") or "application/octet-stream"),
            obj["data"],
        )
    # A bare base64 string.
    return fallback_name, "application/octet-stream", raw


def _b64_size(data: str) -> int:
    stripped = data.strip()
    return max(len(stripped) * 3 // 4 - stripped[-2:].count("="), 0)


def file_info(raw: str, fallback_name: str) -> FileInfo | None:
    parsed = _parse_file(raw, fallback_name)
    if parsed is None:
        return None
    name, content_type, data = parsed
    return FileInfo(name=name, content_type=content_type, size_bytes=_b64_size(data))


def decode_file(raw: str, fallback_name: str) -> StoredFile | None:
    parsed = _parse_file(raw, fallback_name)
    if parsed is None:
        return None
    name, content_type, data = parsed
    try:
        payload = base64.b64decode(data, validate=False)
    except (binascii.Error, ValueError):
        return None
    return StoredFile(name=name, content_type=content_type, data=payload)
