"""The service catalogue."""

from fastapi import APIRouter, Depends

from app.api.deps import get_enrolment_service
from app.schemas.enrolment import Catalog
from app.services.enrolment import EnrolmentService

router = APIRouter(tags=["services"])


@router.get("/services", response_model=Catalog)
async def get_catalog(
    service: EnrolmentService = Depends(get_enrolment_service),
) -> Catalog:
    """The services the dashboard sells, with fees in whole rupees.

    From our config (SERVICES, JOINING_FEE), so no vendor call and no token.
    """
    return service.catalog()
