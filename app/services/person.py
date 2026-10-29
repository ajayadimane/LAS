"""Person registration and lookups."""

from app.clients.vendor_client import VendorCallContext, VendorClient
from app.core.config import Settings
from app.schemas.common import VendorAck
from app.schemas.person import (
    PersonDetails,
    PersonListResponse,
    RegisterPersonRequest,
    RegisterPersonResponse,
)

REGISTER_PATH = "/customermgmt/person/"
BY_PERSON_ID_PATH = (
    "/walletInfoRetrieval/customerMgmtService/getPersonDetailsByPersonId"
)
BY_BROKER_ID_PATH = (
    "/walletInfoRetrieval/customerMgmtService/getPersonDetailsByBrokerId"
)
CUSTOM_FIELDS_PATH = "/walletInfoRetrieval/customerMgmtService/customFields"


class PersonService:
    def __init__(self, client: VendorClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

    def _pm(self, supplied: str | None) -> str:
        return supplied or self._settings.vendor_program_manager_id

    async def _complete_custom_fields(
        self, req: RegisterPersonRequest, ctx: VendorCallContext
    ) -> dict[str, str]:
        """The supplied values, plus "" for every other defined field.

        The vendor rejects a registration that omits any custom field defined
        for the program manager (seven for LAS: one per service plus three
        statuses). Whether the vendor accepts "" for a field is unverified.
        """
        body = await self._client.post(
            CUSTOM_FIELDS_PATH,
            {"programManagerId": self._pm(req.program_manager_id)},
            ctx,
        )
        defined = [
            item["customFieldId"]
            for item in (body if isinstance(body, list) else [])
            if isinstance(item, dict) and item.get("customFieldId")
        ]
        return {**{field_id: "" for field_id in defined}, **req.custom_fields}

    async def register(
        self, req: RegisterPersonRequest, ctx: VendorCallContext
    ) -> RegisterPersonResponse:
        payload: dict[str, object] = {
            "emailAddress": str(req.email_address),
            "addressStreetAddress": req.address_street_address,
            "addressCity": req.address_city,
            "addressStateProvince": req.address_state_province,
            "addressPostalCode": req.address_postal_code,
            "addressCountryCode": req.address_country_code,
            "mobileNumber": req.mobile_number,
            "mobileCountryCode": req.mobile_country_code,
            "firstName": req.first_name,
            "settlementAccountNo": req.settlement_account_no,
            "settlementAccountIfscCode": req.settlement_account_ifsc_code,
            "programManagerId": self._pm(req.program_manager_id),
            "groupId": req.group_id,
            "customFields": await self._complete_custom_fields(req, ctx),
        }
        # Both are optional to the vendor; sending them as null is not the
        # same as omitting them, so only include what was supplied.
        if req.person_id is not None:
            payload["personId"] = req.person_id
        if req.last_name is not None:
            payload["lastName"] = req.last_name
        if req.broker_id is not None:
            payload["brokerId"] = req.broker_id

        body = await self._client.post(REGISTER_PATH, payload, ctx)
        ack = VendorAck.from_vendor(body)
        return RegisterPersonResponse(person_id=req.person_id, **ack.model_dump())

    async def get_by_person_id(
        self, person_id: str, program_manager_id: str | None, ctx: VendorCallContext
    ) -> PersonDetails:
        payload = {
            "personId": person_id,
            "programManagerId": self._pm(program_manager_id),
        }
        body = await self._client.post(BY_PERSON_ID_PATH, payload, ctx)
        return PersonDetails.from_vendor(body)

    async def list_by_broker_id(
        self, broker_id: str, program_manager_id: str | None, ctx: VendorCallContext
    ) -> PersonListResponse:
        payload = {
            "brokerId": broker_id,
            "programManagerId": self._pm(program_manager_id),
        }
        body = await self._client.post(BY_BROKER_ID_PATH, payload, ctx)
        items = body if isinstance(body, list) else []
        persons = [PersonDetails.from_vendor(i) for i in items]
        return PersonListResponse(count=len(persons), persons=persons)
