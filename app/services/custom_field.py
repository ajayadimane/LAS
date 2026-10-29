"""Custom field definitions, and the values persons carry for them."""

from app.clients.vendor_client import VendorCallContext, VendorClient
from app.core.config import Settings
from app.schemas.common import VendorAck
from app.schemas.custom_field import (
    CreateCustomFieldRequest,
    CreateCustomFieldResponse,
    CustomFieldDefinition,
    CustomFieldListResponse,
    UpdateCustomFieldResponse,
    UpdateCustomFieldsRequest,
    UpdateCustomFieldValueRequest,
)

CREATE_PATH = "/customermgmt/customField/programManager/"
UPDATE_VALUE_PATH = "/customermgmt/person/customField/value"
UPDATE_VALUES_PATH = "/customermgmt/person/customFields"
LIST_PATH = "/walletInfoRetrieval/customerMgmtService/customFields"


class CustomFieldService:
    def __init__(self, client: VendorClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def _pm(self, supplied: str | None) -> str:
        return supplied or self._settings.vendor_program_manager_id

    async def create(
        self, req: CreateCustomFieldRequest, ctx: VendorCallContext
    ) -> CreateCustomFieldResponse:
        payload = {
            "programManagerId": self._pm(req.program_manager_id),
            "customFieldId": req.custom_field_id,
            "customFieldDataType": req.custom_field_data_type,
            "customFieldName": req.custom_field_name,
            "customFieldDescription": req.custom_field_description,
            "isSearchable": req.is_searchable,
            "isUnique": req.is_unique,
            "searchScope": req.search_scope,
        }
        body = await self._client.post(CREATE_PATH, payload, ctx)
        return CreateCustomFieldResponse(**VendorAck.from_vendor(body).model_dump())

    async def list_for_program_manager(
        self, program_manager_id: str | None, ctx: VendorCallContext
    ) -> CustomFieldListResponse:
        pm = self._pm(program_manager_id)
        body = await self._client.post(LIST_PATH, {"programManagerId": pm}, ctx)
        # Documented only as "a JSON array"; tolerate anything else.
        items = body if isinstance(body, list) else []
        return CustomFieldListResponse(
            program_manager_id=pm,
            custom_fields=[CustomFieldDefinition.from_vendor(i) for i in items],
        )

    async def update_value(
        self,
        person_id: str,
        custom_field_id: str,
        req: UpdateCustomFieldValueRequest,
        ctx: VendorCallContext,
    ) -> UpdateCustomFieldResponse:
        payload = {
            "personId": person_id,
            "programManagerId": self._pm(req.program_manager_id),
            "customFieldId": custom_field_id,
            "customFieldValue": req.custom_field_value,
        }
        body = await self._client.put(UPDATE_VALUE_PATH, payload, ctx)
        return UpdateCustomFieldResponse(**VendorAck.from_vendor(body).model_dump())

    async def update_values(
        self, person_id: str, req: UpdateCustomFieldsRequest, ctx: VendorCallContext
    ) -> UpdateCustomFieldResponse:
        payload = {
            "personId": person_id,
            "programManagerId": self._pm(req.program_manager_id),
            "customFields": req.custom_fields,
        }
        body = await self._client.put(UPDATE_VALUES_PATH, payload, ctx)
        return UpdateCustomFieldResponse(**VendorAck.from_vendor(body).model_dump())
