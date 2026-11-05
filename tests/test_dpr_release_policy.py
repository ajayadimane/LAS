from dataclasses import replace

from app.services.dpr_agentic.report_access import (
    ProcessingStatus,
    ReportGateInput,
    RetentionStatus,
    StorageStatus,
    evaluate_report_access,
)


def base(**overrides):
    state = ReportGateInput(
        analysis_status=ProcessingStatus.COMPLETED,
        report_status=ProcessingStatus.COMPLETED,
        wallet_storage_status=StorageStatus.RETRY,
        identity_verified=True,
        authenticated_owner=True,
        payment_verified=True,
        consent_satisfied=True,
        retention_status=RetentionStatus.COMPLIANT,
        report_artifact_verified=True,
    )
    return replace(state, **overrides)


def test_wallet_failure_does_not_block_paid_compliant_report():
    decision = evaluate_report_access(base(wallet_storage_status=StorageStatus.FAILED))
    assert decision.can_generate and decision.can_release
    assert decision.wallet_sync_pending


def test_wallet_failure_does_not_block_generation_when_payment_pending():
    decision = evaluate_report_access(base(payment_verified=False))
    assert decision.can_generate and not decision.can_release
    assert "PAYMENT_NOT_VERIFIED" in decision.reasons


def test_unverified_retention_blocks_release_even_when_wallet_stored():
    decision = evaluate_report_access(
        base(
            wallet_storage_status=StorageStatus.STORED,
            retention_status=RetentionStatus.UNKNOWN,
        )
    )
    assert not decision.can_release


def test_all_mandatory_gates_enforced():
    for key, value in [
        ("consent_satisfied", False),
        ("authenticated_owner", False),
        ("identity_verified", False),
        ("report_artifact_verified", False),
        ("analysis_status", ProcessingStatus.FAILED),
        ("report_status", ProcessingStatus.FAILED),
    ]:
        assert not evaluate_report_access(base(**{key: value})).can_release


def test_wallet_success_does_not_unlock_unpaid_report():
    decision = evaluate_report_access(
        base(wallet_storage_status=StorageStatus.STORED, payment_verified=False)
    )
    assert not decision.can_release
