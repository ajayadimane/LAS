"""The custom fields that track LAS services on a person.

Design (agreed with the user, 2026-10-27). Each in-house service in
Settings.services, with vendor field id `F` (e.g. GeoCheck), has:

    F             lifecycle: NA -> (Awaiting BD) -> Opted -> Docs uploaded ->
                  Approved | Rejected -> Report ready. Searchable (the
                  vendor's original field). "Awaiting BD" is a customer's own
                  request; it is not opted until the BD authorizes it.
    F_Payment     NA -> Processing -> Paid
    F_PayMode     Pay now | Pay later
    F_TxnId       the pool transfer's transaction id (pay later)
    F_Doc         the document

Only F is searchable: there are 8 search keys and 7 are taken. The others
are created by scripts/setup_custom_fields.py, so a new service is a config
entry plus that script, no code.

Per customer, Payment_Status / Payment_Mode / Doc_Status are a searchable
summary of the services, recomputed by `summary()` on every write so the
dashboard can filter without reading every service.

Documents: `F_Doc` holds JSON {"name","contentType","data"} (base64) while no
object storage exists. Every customer-list call returns all custom fields, so
this does not scale: move to storage (e.g. MinIO) before many customers have
documents. Only app/services/documents.py needs to change for that.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.core.config import ServiceConfig, Settings

NOT_SET = "NA"
DOC_STATUS_FIELD = "Doc_Status"
PAYMENT_STATUS_FIELD = "Payment_Status"
PAYMENT_MODE_FIELD = "Payment_Mode"
SUMMARY_FIELDS = (DOC_STATUS_FIELD, PAYMENT_STATUS_FIELD, PAYMENT_MODE_FIELD)

# suffix -> description, for the per-service fields we create.
SERVICE_FIELD_SUFFIXES = {
    "_Payment": "payment status",
    "_PayMode": "payment mode",
    "_TxnId": "pool transfer transaction id",
    "_Doc": "document",
}

_UNSET = {"", NOT_SET}


def field_value(value: str | None) -> str | None:
    """The value, or None when it means "not set". Seen as "NA" and "na"."""
    if value is None or value.strip().upper() in _UNSET:
        return None
    return value.strip()


def same(value: str | None, expected: str) -> bool:
    return value is not None and value.strip().casefold() == expected.casefold()


def is_requested(status: str | None, settings: Settings) -> bool:
    """A customer's request, waiting for their BD to authorize or decline."""
    return same(field_value(status), settings.service_requested_value)


def is_opted(status: str | None, settings: Settings) -> bool:
    """Taken and paid for (or being paid): set, and not just requested."""
    return field_value(status) is not None and not is_requested(status, settings)


@dataclass(frozen=True)
class ServiceFields:
    """The vendor field ids of one in-house service."""

    status: str
    payment: str
    pay_mode: str
    txn_id: str
    doc: str

    @classmethod
    def of(cls, service: ServiceConfig) -> "ServiceFields":
        f = service.field_id or ""
        return cls(
            status=f,
            payment=f + "_Payment",
            pay_mode=f + "_PayMode",
            txn_id=f + "_TxnId",
            doc=f + "_Doc",
        )

    def all(self) -> tuple[str, ...]:
        return (self.status, self.payment, self.pay_mode, self.txn_id, self.doc)


def in_house(settings: Settings) -> list[ServiceConfig]:
    """Services paid and documented here (not on a partner portal)."""
    return [s for s in settings.services if s.field_id and not s.external]


def all_field_ids(settings: Settings) -> list[str]:
    """Every LAS custom field a customer carries, for registration."""
    ids = [f for s in in_house(settings) for f in ServiceFields.of(s).all()]
    return ids + list(SUMMARY_FIELDS)


def summary(values: Mapping[str, str], settings: Settings) -> dict[str, str]:
    """Payment_Status, Payment_Mode and Doc_Status from the services.

    `values` are the customer's fields as they will be after the write.
    """
    s = settings
    opted = [
        ServiceFields.of(x)
        for x in in_house(settings)
        if is_opted(values.get(ServiceFields.of(x).status), settings)
    ]

    def values_of(ids: Iterable[str]) -> list[str]:
        return [v for v in (field_value(values.get(i)) for i in ids) if v is not None]

    payments = values_of(f.payment for f in opted)
    if any(same(p, s.payment_status_processing) for p in payments):
        payment_status = s.payment_status_processing
    elif payments:
        payment_status = s.payment_status_paid
    else:
        payment_status = NOT_SET

    modes = {m.casefold(): m for m in values_of(f.pay_mode for f in opted)}
    if not modes:
        payment_mode = NOT_SET
    elif len(modes) == 1:
        payment_mode = next(iter(modes.values()))
    else:
        payment_mode = s.payment_mode_mixed

    documented = len(values_of(f.doc for f in opted))
    if documented == 0:
        doc_status = NOT_SET
    elif documented == len(opted):
        doc_status = s.doc_status_uploaded
    else:
        doc_status = s.doc_status_partial

    return {
        PAYMENT_STATUS_FIELD: payment_status,
        PAYMENT_MODE_FIELD: payment_mode,
        DOC_STATUS_FIELD: doc_status,
    }
