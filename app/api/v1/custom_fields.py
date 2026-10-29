"""Custom field routes: definitions for a program manager, values on a person."""

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_authenticated_context, get_custom_field_service, require_bd
from app.clients.vendor_client import VendorCallContext
from app.schemas.custom_field import (
    CreateCustomFieldRequest,
    CreateCustomFieldResponse,
    CustomFieldListResponse,
    UpdateCustomFieldResponse,
    UpdateCustomFieldsRequest,
    UpdateCustomFieldValueRequest,
)
from app.services.custom_field import CustomFieldService

# Not scoped to a broker. BDs only.
router = APIRouter(tags=["custom-fields"], dependencies=[Depends(require_bd)])


@router.post(
    "/custom-fields",
    response_model=CreateCustomFieldResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_custom_field(
    payload: CreateCustomFieldRequest,
    service: CustomFieldService = Depends(get_custom_field_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> CreateCustomFieldResponse:
    return await service.create(payload, ctx)


@router.get("/custom-fields", response_model=CustomFieldListResponse)
async def list_custom_fields(
    program_manager_id: str | None = Query(default=None),
    service: CustomFieldService = Depends(get_custom_field_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> CustomFieldListResponse:
    """Every custom field defined for the program manager.

    Call this before registering a person: the vendor rejects a registration
    that does not supply all of them.
    """
    return await service.list_for_program_manager(program_manager_id, ctx)


@router.put(
    "/persons/{person_id}/custom-fields/{custom_field_id}",
    response_model=UpdateCustomFieldResponse,
)
async def update_custom_field_value(
    person_id: str,
    custom_field_id: str,
    payload: UpdateCustomFieldValueRequest,
    service: CustomFieldService = Depends(get_custom_field_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> UpdateCustomFieldResponse:
    return await service.update_value(person_id, custom_field_id, payload, ctx)


@router.put(
    "/persons/{person_id}/custom-fields", response_model=UpdateCustomFieldResponse
)
async def update_custom_field_values(
    person_id: str,
    payload: UpdateCustomFieldsRequest,
    service: CustomFieldService = Depends(get_custom_field_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> UpdateCustomFieldResponse:
    """Update several of a person's custom field values in one vendor call."""
    return await service.update_values(person_id, payload, ctx)
