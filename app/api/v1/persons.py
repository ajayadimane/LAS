"""Person routes.

The vendor exposes these as POSTs with a JSON body. We expose the reads as
GETs because that is what they are, and because the dashboard should not have
to care that the vendor has no GET endpoints.
"""

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import get_authenticated_context, get_person_service, require_bd
from app.clients.vendor_client import VendorCallContext
from app.schemas.person import (
    PersonDetails,
    PersonListResponse,
    RegisterPersonRequest,
    RegisterPersonResponse,
)
from app.services.person import PersonService

# Not scoped to a broker: any person in the program manager. BDs only;
# the dashboard uses the scoped /bd and /customer routes instead.
router = APIRouter(
    prefix="/persons", tags=["persons"], dependencies=[Depends(require_bd)]
)


@router.post(
    "", response_model=RegisterPersonResponse, status_code=status.HTTP_201_CREATED
)
async def register_person(
    payload: RegisterPersonRequest,
    service: PersonService = Depends(get_person_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> RegisterPersonResponse:
    """Register a person. The vendor also creates an empty KYC document.

    `custom_fields` must carry every custom field defined for the program
    manager; `GET /api/v1/custom-fields` lists them.
    """
    return await service.register(payload, ctx)


@router.get("/{person_id}", response_model=PersonDetails)
async def get_person(
    person_id: str,
    program_manager_id: str | None = Query(default=None),
    service: PersonService = Depends(get_person_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> PersonDetails:
    return await service.get_by_person_id(person_id, program_manager_id, ctx)


@router.get("", response_model=PersonListResponse)
async def list_persons(
    broker_id: str = Query(
        description="Persons registered under this broker. The vendor offers "
        "no other way to list persons."
    ),
    program_manager_id: str | None = Query(default=None),
    service: PersonService = Depends(get_person_service),
    ctx: VendorCallContext = Depends(get_authenticated_context),
) -> PersonListResponse:
    return await service.list_by_broker_id(broker_id, program_manager_id, ctx)
