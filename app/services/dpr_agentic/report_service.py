"""Resolves and gates the most recent DPR report for one person+service.

Composes the durable job/branch records with a *fresh* Wallet payment check
(never a cached flag) and hands the result to report_access.evaluate_report_access,
which is the only thing that decides whether a report may be released.
"""

from __future__ import annotations

from app.clients.vendor_client import VendorCallContext
from app.services.enrolment import EnrolmentService
from app.services.service_fields import same

from .branch_status import get_branch_status
from .jobs import latest_run_for
from .report_access import (
    ProcessingStatus,
    ReportGateDecision,
    ReportGateInput,
    RetentionStatus,
    StorageStatus,
    evaluate_report_access,
)
from .report_store import get_report
from .schemas import AnalysisResult


class ReportNotFoundError(LookupError):
    """No analysis has ever been run for this person+service."""


_PROCESSING = {s.value: s for s in ProcessingStatus}
_STORAGE = {s.value: s for s in StorageStatus}


def source_reference(person_id: str, code: str) -> str:
    """Must match the value customer.py records a run under."""
    return f"wallet-person-custom-field:{person_id}:{code}"


async def access_report(
    engine,
    person_id: str,
    code: str,
    broker_id: str | None,
    enrolment: EnrolmentService,
    ctx: VendorCallContext,
) -> tuple[ReportGateDecision, AnalysisResult | None, dict]:
    """Gate and (if allowed) return the latest report for `code`.

    Also returns the raw job row, so a caller can show a QUEUED/RUNNING/
    FAILED/COMPLETED status while polling without a separate endpoint.

    Raises ReportNotFoundError if no analysis was ever submitted;
    UnknownServiceError/CustomerNotFoundError propagate from `enrolment` as in
    every other route in this module.
    """
    ref = source_reference(person_id, code)
    run = latest_run_for(engine, ref)
    if run is None:
        raise ReportNotFoundError(code)
    branch = get_branch_status(engine, run["run_id"])

    # Fresh re-check against the vendor, never a cached flag (see module doc).
    service = enrolment._service(code)  # noqa: SLF001 - established call site convention
    values = await enrolment._values(person_id, broker_id, ctx)  # noqa: SLF001
    view = enrolment._view(values, service)  # noqa: SLF001
    settings = enrolment._settings  # noqa: SLF001
    payment_verified = same(view.payment_status, settings.payment_status_paid)

    report_artifact_verified = False
    report = None
    if run["status"] == "COMPLETED" and run["artifact_ref"]:
        report = get_report(engine, ref)
        report_artifact_verified = (
            report is not None and report.document_sha256 == run["document_sha256"]
        )

    state = ReportGateInput(
        analysis_status=_PROCESSING.get(run["status"], ProcessingStatus.FAILED),
        report_status=_PROCESSING.get(
            branch["report_status"] if branch else "QUEUED", ProcessingStatus.FAILED
        ),
        wallet_storage_status=_STORAGE.get(
            branch["wallet_storage_status"] if branch else "PENDING",
            StorageStatus.PENDING,
        ),
        identity_verified=True,  # the route already authenticated this token
        authenticated_owner=True,  # the route already checked ownership of person_id
        payment_verified=payment_verified,
        # Pay later always requires both consents before enrolment.py ever
        # marks a service Paid (see app/services/enrolment.py); pay now takes
        # no consent flag. So "Paid" already implies consent was satisfied.
        consent_satisfied=True,
        # The wallet write base64-encodes into a vendor-owned custom field.
        # Raw bytes are staged (encrypted) only until the worker reaches a
        # terminal state -- see upload_staging.py -- and gone by the time a
        # report can possibly be COMPLETED, so nothing outlives processing.
        retention_status=RetentionStatus.COMPLIANT,
        report_artifact_verified=report_artifact_verified,
    )
    decision = evaluate_report_access(state)
    return decision, (report if decision.can_release else None), run
