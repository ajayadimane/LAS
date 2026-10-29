"""Our contract for custom field definitions and person field values.

Constraints come from api_docs/ENDPOINTS.md > Field validation reference.
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import VendorAck

# The vendor accepts these case-insensitively; we normalise to its spelling.
CustomFieldDataType = Literal["Text", "List"]
SearchScope = Literal["NA", "GLOBAL", "PROGRAM_MANAGER"]


class CreateCustomFieldRequest(BaseModel):
    custom_field_id: str = Field(min_length=1, examples=["pan"])
    custom_field_name: str = Field(min_length=1, examples=["panCard"])
    custom_field_description: str = Field(default="", examples=["Add pancard"])
    custom_field_data_type: CustomFieldDataType = "Text"
    is_searchable: bool = False
    is_unique: bool = False
    search_scope: SearchScope = "NA"
    program_manager_id: str | None = None


class CreateCustomFieldResponse(VendorAck):
    pass


def _flag(value: object) -> bool | None:
    """The read sends booleans as the strings "true" / "false"."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


def _first(item: dict, *keys: str) -> object:
    return next((item[k] for k in keys if item.get(k) is not None), None)


class CustomFieldDefinition(BaseModel):
    """One entry from the list-custom-fields read.

    Real shape (user, 2026-10-27), flattened like person details:
    `customFieldId.id`, `name`, `description`, `dataType` ("TEXT"),
    `isSearchable` / `isUnique` as "true"/"false" strings, `searchScope`,
    `programManagerId.id`, `mappedSearchKey` ("SK1".."SK8"). The create
    endpoint's names (`customFieldId`, `customFieldName`, ...) are still read
    as a fallback. Anything else is dropped rather than passed through raw.
    """

    custom_field_id: str | None = None
    custom_field_name: str | None = None
    custom_field_description: str | None = None
    custom_field_data_type: str | None = None
    is_searchable: bool | None = None
    is_unique: bool | None = None
    search_scope: str | None = None
    mapped_search_key: str | None = Field(
        default=None,
        description="The person search key (SK1-SK8) this field is indexed under.",
    )

    @classmethod
    def from_vendor(cls, item: object) -> "CustomFieldDefinition":
        if not isinstance(item, dict):
            return cls()
        field_id = _first(item, "customFieldId.id", "customFieldId")
        if isinstance(field_id, dict):  # nested rather than flattened
            field_id = field_id.get("id")
        return cls(
            custom_field_id=field_id,
            custom_field_name=_first(item, "name", "customFieldName"),
            custom_field_description=_first(
                item, "description", "customFieldDescription"
            ),
            custom_field_data_type=_first(item, "dataType", "customFieldDataType"),
            is_searchable=_flag(item.get("isSearchable")),
            is_unique=_flag(item.get("isUnique")),
            search_scope=item.get("searchScope"),
            mapped_search_key=item.get("mappedSearchKey"),
        )


class CustomFieldListResponse(BaseModel):
    program_manager_id: str
    custom_fields: list[CustomFieldDefinition]


class UpdateCustomFieldValueRequest(BaseModel):
    custom_field_value: str
    program_manager_id: str | None = None


class UpdateCustomFieldsRequest(BaseModel):
    custom_fields: dict[str, str] = Field(
        min_length=1,
        description="Map of customFieldId to new value.",
        examples=[{"pan": "ABCDE1234F", "aadhar": "123456789012"}],
    )
    program_manager_id: str | None = None


class UpdateCustomFieldResponse(VendorAck):
    pass
