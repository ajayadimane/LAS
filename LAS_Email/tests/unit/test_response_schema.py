"""to_response_schema (las/common/llm.py): pydantic's auto-generated JSON-Schema ``"title"``
metadata must be stripped (Gemini's structured-output schema doesn't want it), but a field that is
genuinely *named* ``title`` must survive -- a regression test for the bug where a structured-output
call (originally DPR qualitative review) failed against the real Gemini API (``properties.anomalies.items`` listed
``title`` as required but had no ``title`` entry in ``properties``, because the old filter deleted
any dict key equal to ``"title"`` regardless of whether it was metadata or a field name)."""

from __future__ import annotations

from pydantic import BaseModel

from las.common.llm import to_response_schema


class _Inner(BaseModel):
    title: str
    note: str = "default"


class _Outer(BaseModel):
    items: list[_Inner]
    label: str


def test_pydantic_title_metadata_is_stripped() -> None:
    schema = to_response_schema(_Outer)
    assert "title" not in schema
    assert "title" not in schema["properties"]["label"]


def test_a_field_literally_named_title_survives_in_properties() -> None:
    schema = to_response_schema(_Outer)
    item_schema = schema["properties"]["items"]["items"]
    assert "title" in item_schema["properties"]
    assert item_schema["properties"]["title"]["type"] == "string"


def test_every_required_field_is_present_in_properties() -> None:
    """The exact shape Gemini's structured-output API rejects: a field named in ``required`` with
    no matching entry in ``properties``."""
    schema = to_response_schema(_Outer)
    item_schema = schema["properties"]["items"]["items"]
    assert set(item_schema["required"]).issubset(item_schema["properties"].keys())


def test_extraction_schema_is_valid_for_the_real_gemini_contract() -> None:
    """The one structured output this service asks Gemini for (las/parse/schema.py)."""
    from las.parse.schema import Extraction

    def check(node: object) -> None:
        if isinstance(node, dict):
            if "required" in node and "properties" in node:
                assert set(node["required"]).issubset(node["properties"].keys())
            for v in node.values():
                check(v)
        elif isinstance(node, list):
            for v in node:
                check(v)

    check(to_response_schema(Extraction))
