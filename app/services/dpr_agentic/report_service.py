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
from .report_store import get_report, get_report_pdf, list_report_versions
from .schemas import AnalysisResult


class ReportNotFoundError(LookupError):
    """No analysis has ever been run for this person+service."""


_PROCESSING = {s.value: s for s in ProcessingStatus}
_STORAGE = {s.value: s for s in StorageStatus}


def source_reference(person_id: str, code: str) -> str:
    """Must match the value customer.py records a run under."""
    return f"wallet-person-custom-field:{person_id}:{code}"


async def _gate(
    engine,
    person_id: str,
    code: str,
    broker_id: str | None,
    enrolment: EnrolmentService,
    ctx: VendorCallContext,
    *,
    version: int | None,
) -> tuple[ReportGateDecision, str, dict, AnalysisResult | None]:
    """Shared authorization/release gate for every report-derived endpoint
    (the JSON report, its PDF, its findings, its version list): one policy
    evaluation that every caller in this module reuses, so a fix here
    benefits every route built on it, not just one.

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
        report = get_report(engine, ref, version=version)
        if version is None:
            # The default (latest) lookup must tie back to the run this
            # gate just evaluated -- a stale or mismatched artifact must
            # never be released even if payment/consent otherwise pass.
            report_artifact_verified = (
                report is not None and report.document_sha256 == run["document_sha256"]
            )
        else:
            # An explicit historical version is, by definition, not the
            # latest run's artifact -- its own existence is the only
            # integrity question that makes sense here.
            report_artifact_verified = report is not None

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
        # There is no persisted consent flag to check independently of Paid
        # status (consent is enforced transiently at request/authorize time,
        # see enrolment.py) -- evaluate_report_access's own unit tests cover
        # consent_satisfied=False directly, since no live code path here can
        # produce it.
        consent_satisfied=True,
        # The wallet write base64-encodes into a vendor-owned custom field.
        # Raw bytes are staged (encrypted) only until the worker reaches a
        # terminal state -- see upload_staging.py -- and gone by the time a
        # report can possibly be COMPLETED, so nothing outlives processing.
        retention_status=RetentionStatus.COMPLIANT,
        report_artifact_verified=report_artifact_verified,
    )
    decision = evaluate_report_access(state)
    return decision, ref, run, (report if decision.can_release else None)


async def access_report(
    engine,
    person_id: str,
    code: str,
    broker_id: str | None,
    enrolment: EnrolmentService,
    ctx: VendorCallContext,
    *,
    version: int | None = None,
) -> tuple[ReportGateDecision, AnalysisResult | None, dict]:
    """Gate and (if allowed) return the report for `code` -- the latest
    version by default, or a specific historical `version`.

    Also returns the raw job row, so a caller can show a QUEUED/RUNNING/
    FAILED/COMPLETED status while polling without a separate endpoint.
    """
    decision, _ref, run, report = await _gate(
        engine, person_id, code, broker_id, enrolment, ctx, version=version
    )
    return decision, report, run


async def access_report_pdf(
    engine,
    person_id: str,
    code: str,
    broker_id: str | None,
    enrolment: EnrolmentService,
    ctx: VendorCallContext,
    *,
    version: int | None = None,
) -> tuple[ReportGateDecision, bytes | None, dict]:
    """Same gate as access_report(), but returns the pre-rendered CAM PDF
    bytes instead of the JSON -- never rendered on this call (see
    report_store.py's module docstring): if the stored version's PDF never
    rendered, this returns None even when the gate allows release, and the
    caller must say so rather than render one inline."""
    decision, ref, run, report = await _gate(
        engine, person_id, code, broker_id, enrolment, ctx, version=version
    )
    pdf = get_report_pdf(engine, ref, version=version) if report is not None else None
    return decision, pdf, run


async def access_report_versions(
    engine,
    person_id: str,
    code: str,
    broker_id: str | None,
    enrolment: EnrolmentService,
    ctx: VendorCallContext,
) -> tuple[ReportGateDecision, list[int], dict]:
    """Same gate as access_report(), evaluated against the latest version;
    returns every version number ever stored for this service, oldest
    first, if release is currently allowed."""
    decision, ref, run, report = await _gate(
        engine, person_id, code, broker_id, enrolment, ctx, version=None
    )
    versions = list_report_versions(engine, ref) if report is not None else []
    return decision, versions, run
