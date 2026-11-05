"""Shared HTTP glue for the DPR report-derived endpoints.

app/api/v1/customer.py and app/api/v1/bd.py each expose the same four
endpoints (report, ?format=pdf, findings, versions) against the same
report_service.py gate, differing only in how they resolve person_id/
broker_id (self vs. an explicit customer under this BD). This module is the
one place that maps that gate's outcome to HTTP status/headers, so a fix
here -- e.g. the Cache-Control header below -- benefits both routers without
being written twice.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import HTTPException, Response, status

from app.clients.vendor_client import VendorCallContext
from app.services.bd import CustomerNotFoundError
from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.cam_annexures import build_annexures
from app.services.dpr_agentic.jobs import latest_run_for
from app.services.dpr_agentic.report_service import (
    ReportNotFoundError,
    access_report,
    access_report_pdf,
    access_report_versions,
    source_reference,
)
from app.services.dpr_agentic.report_store import list_report_versions
from app.services.dpr_agentic.schemas import AnalysisResult
from app.services.enrolment import EnrolmentService, UnknownServiceError

# A DPR report is sensitive financial data re-evaluated on every request
# (fresh payment/consent/ownership check) -- never cacheable by a shared
# intermediary, and never worth storing client-side across requests either.
NO_STORE = {"Cache-Control": "private, no-store"}


def _not_released(run: dict, decision) -> HTTPException:
    return HTTPException(
        status.HTTP_403_FORBIDDEN,
        {
            "detail": "The report is not available yet.",
            "status": run["status"],
            "reasons": list(decision.reasons),
        },
    )


async def resolve_report(
    *,
    engine,
    person_id: str,
    code: str,
    broker_id: str | None,
    service: EnrolmentService,
    ctx: VendorCallContext,
    response: Response,
    version: int | None,
    not_found: HTTPException,
    unknown_service: Callable[[str], HTTPException],
) -> AnalysisResult:
    try:
        decision, report, run = await access_report(
            engine, person_id, code, broker_id, service, ctx, version=version
        )
    except ReportNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No analysis has been run for this service yet."
        ) from None
    except CustomerNotFoundError:
        raise not_found from None
    except UnknownServiceError:
        raise unknown_service(code) from None
    if not decision.can_release or report is None:
        raise _not_released(run, decision)
    response.headers.update(NO_STORE)
    return report


async def resolve_report_pdf(
    *,
    engine,
    person_id: str,
    code: str,
    broker_id: str | None,
    service: EnrolmentService,
    ctx: VendorCallContext,
    version: int | None,
    not_found: HTTPException,
    unknown_service: Callable[[str], HTTPException],
) -> Response:
    """Serves the pre-rendered CAM PDF stored for this version -- never
    rendered on this call (see report_store.py); a version whose PDF never
    rendered is a 503, not a silent fallback to some other renderer."""
    try:
        decision, pdf, run = await access_report_pdf(
            engine, person_id, code, broker_id, service, ctx, version=version
        )
    except ReportNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No analysis has been run for this service yet."
        ) from None
    except CustomerNotFoundError:
        raise not_found from None
    except UnknownServiceError:
        raise unknown_service(code) from None
    if not decision.can_release:
        raise _not_released(run, decision)
    if pdf is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "The PDF for this report version is not available.",
        )
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            **NO_STORE,
            "Content-Disposition": 'attachment; filename="dpr-report.pdf"',
        },
    )


async def resolve_findings(
    *,
    engine,
    person_id: str,
    code: str,
    broker_id: str | None,
    service: EnrolmentService,
    ctx: VendorCallContext,
    response: Response,
    version: int | None,
    not_found: HTTPException,
    unknown_service: Callable[[str], HTTPException],
) -> list:
    """The same gated report's findings only -- a thin projection of
    resolve_report(), not a separate stored artifact."""
    report = await resolve_report(
        engine=engine,
        person_id=person_id,
        code=code,
        broker_id=broker_id,
        service=service,
        ctx=ctx,
        response=response,
        version=version,
        not_found=not_found,
        unknown_service=unknown_service,
    )
    return report.findings


async def resolve_annexures(
    *,
    engine,
    person_id: str,
    code: str,
    broker_id: str | None,
    service: EnrolmentService,
    ctx: VendorCallContext,
    response: Response,
    version: int | None,
    not_found: HTTPException,
    unknown_service: Callable[[str], HTTPException],
) -> dict:
    """All seven Section 12 annexures (A-G; docs/CAM_REPORT_SPEC.md) for the
    same gated report, keyed by letter (e.g. "A_financial_statements").

    This reshapes the already-stored, already-gated AnalysisResult with
    cam_adapter.build_snapshot_from_analysis() -- pure Python model mapping,
    not the WeasyPrint PDF render -- so it does not violate "no re-rendering
    on GET" for the PDF; no borrower/project profile is available here any
    more than it is in report_store.py's own one-time PDF render, so those
    fields are left at their explicit missing-data default. Each annexure
    also lists what it cannot yet populate ("missing"), per spec: an
    annexure states unavailable data rather than padding it.
    """
    report = await resolve_report(
        engine=engine,
        person_id=person_id,
        code=code,
        broker_id=broker_id,
        service=service,
        ctx=ctx,
        response=response,
        version=version,
        not_found=not_found,
        unknown_service=unknown_service,
    )
    ref = source_reference(person_id, code)
    resolved_version = version
    if resolved_version is None:
        versions = list_report_versions(engine, ref)
        resolved_version = versions[-1] if versions else 0
    snapshot = build_snapshot_from_analysis(
        report,
        report_id=ref,
        report_version=resolved_version,
        analysis_run_id=report.wallet_document_id,
    )
    run = latest_run_for(engine, ref)
    return build_annexures(snapshot, report, run)


async def resolve_summary(
    *,
    engine,
    person_id: str,
    code: str,
    broker_id: str | None,
    service: EnrolmentService,
    ctx: VendorCallContext,
    response: Response,
    version: int | None,
    not_found: HTTPException,
    unknown_service: Callable[[str], HTTPException],
) -> dict:
    """Section 17's dashboard summary: readiness status, Must/Should/Good
    finding counts, a short financial overview and an outstanding-items
    count -- for a status card that doesn't need the full report or
    annexures payload. Same gate, same snapshot reshape as
    resolve_annexures(); adds nothing that isn't already in the stored
    AnalysisResult.
    """
    report = await resolve_report(
        engine=engine,
        person_id=person_id,
        code=code,
        broker_id=broker_id,
        service=service,
        ctx=ctx,
        response=response,
        version=version,
        not_found=not_found,
        unknown_service=unknown_service,
    )
    ref = source_reference(person_id, code)
    resolved_version = version
    if resolved_version is None:
        versions = list_report_versions(engine, ref)
        resolved_version = versions[-1] if versions else 0
    snapshot = build_snapshot_from_analysis(
        report,
        report_id=ref,
        report_version=resolved_version,
        analysis_run_id=report.wallet_document_id,
    )
    priority_counts = {"must_fix": 0, "should_fix": 0, "good_to_fix": 0}
    evidence_references_count = 0
    for f in snapshot.findings:
        if f.priority.value in priority_counts:
            priority_counts[f.priority.value] += 1
        evidence_references_count += len(f.evidence) + len(f.source_pages)
    ra = snapshot.readiness_assessment
    ratios = snapshot.financial_ratios
    return {
        "version": resolved_version,
        "readiness_status": ra.status.value,
        "must_fix_count": priority_counts["must_fix"],
        "should_fix_count": priority_counts["should_fix"],
        "good_to_fix_count": priority_counts["good_to_fix"],
        "critical_findings_count": ra.critical_findings_count,
        "outstanding_clarifications_count": ra.outstanding_clarifications_count,
        "evidence_references_count": evidence_references_count,
        "financial_overview": {
            "project_cost_total": (
                snapshot.project_cost.dpr_reported_total.model_dump(mode="json")
                if snapshot.project_cost.dpr_reported_total
                else None
            ),
            "requested_loan": (
                snapshot.means_of_finance.requested_loan.model_dump(mode="json")
                if snapshot.means_of_finance.requested_loan
                else None
            ),
            "promoter_contribution": (
                snapshot.means_of_finance.promoter_contribution.model_dump(mode="json")
                if snapshot.means_of_finance.promoter_contribution
                else None
            ),
            "funding_gap": (
                snapshot.means_of_finance.funding_gap.model_dump(mode="json")
                if snapshot.means_of_finance.funding_gap
                else None
            ),
            "average_dscr": (
                ratios.average_dscr.model_dump(mode="json")
                if ratios.average_dscr
                else None
            ),
        },
    }


async def resolve_versions(
    *,
    engine,
    person_id: str,
    code: str,
    broker_id: str | None,
    service: EnrolmentService,
    ctx: VendorCallContext,
    response: Response,
    not_found: HTTPException,
    unknown_service: Callable[[str], HTTPException],
) -> list[int]:
    try:
        decision, versions, run = await access_report_versions(
            engine, person_id, code, broker_id, service, ctx
        )
    except ReportNotFoundError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "No analysis has been run for this service yet."
        ) from None
    except CustomerNotFoundError:
        raise not_found from None
    except UnknownServiceError:
        raise unknown_service(code) from None
    if not decision.can_release:
        raise _not_released(run, decision)
    response.headers.update(NO_STORE)
    return versions
