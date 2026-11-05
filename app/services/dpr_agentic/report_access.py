"""Pure Core LAS release policy. No Wallet storage dependency for analysis/report.

Payment and consent evidence must be obtained from authorized Core LAS/Wallet
services at the moment of report access. Never use user-entered/email-supplied
payment state, or an old cached 'paid' flag, as proof of payment.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProcessingStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class StorageStatus(str, Enum):
    PENDING = "PENDING"
    STORED = "STORED"
    RETRY = "RETRY"
    FAILED = "FAILED"


class RetentionStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    COMPLIANT = "COMPLIANT"
    NONCOMPLIANT = "NONCOMPLIANT"


@dataclass(frozen=True)
class ReportGateInput:
    analysis_status: ProcessingStatus
    report_status: ProcessingStatus
    wallet_storage_status: StorageStatus
    identity_verified: bool
    authenticated_owner: bool
    payment_verified: bool
    consent_satisfied: bool
    retention_status: RetentionStatus
    report_artifact_verified: bool


@dataclass(frozen=True)
class ReportGateDecision:
    can_generate: bool
    can_release: bool
    reasons: tuple[str, ...]
    wallet_sync_pending: bool


def evaluate_report_access(state: ReportGateInput) -> ReportGateDecision:
    """Fail closed for payment, access, integrity and retention; not Wallet sync.

    An authorized policy service must validate whether approved *temporary*
    retention is sufficient while the Wallet upload is retried. This function
    makes no assumption that local staging is compliant.
    """
    can_generate = state.analysis_status is ProcessingStatus.COMPLETED
    reasons: list[str] = []
    if not can_generate or state.report_status is not ProcessingStatus.COMPLETED:
        reasons.append("REPORT_NOT_COMPLETED")
    if not state.report_artifact_verified:
        reasons.append("REPORT_ARTIFACT_UNVERIFIED")
    if not state.authenticated_owner:
        reasons.append("REPORT_ACCESS_UNAUTHORIZED")
    if not state.identity_verified:
        reasons.append("SOURCE_IDENTITY_UNVERIFIED")
    if not state.payment_verified:
        reasons.append("PAYMENT_NOT_VERIFIED")
    if not state.consent_satisfied:
        reasons.append("CONSENT_NOT_SATISFIED")
    if state.retention_status is not RetentionStatus.COMPLIANT:
        reasons.append("DOCUMENT_RETENTION_NOT_COMPLIANT")
    return ReportGateDecision(
        can_generate=can_generate,
        can_release=not reasons,
        reasons=tuple(reasons),
        wallet_sync_pending=state.wallet_storage_status is not StorageStatus.STORED,
    )
