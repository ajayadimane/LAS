"""Independent wallet synchronization and report artifact status persistence.

Never infer payment or consent from these snapshots. A separate authorized
Core LAS service must evaluate those values before returning a report.
"""

from sqlalchemy import text


def init_branches(engine, run_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("""INSERT INTO dpr_analysis_branch_status (run_id)
          VALUES (:run_id) ON CONFLICT (run_id) DO NOTHING"""),
            {"run_id": run_id},
        )


def mark_wallet_stored(engine, run_id: str, wallet_reference: str) -> bool:
    if not wallet_reference.strip():
        raise ValueError("Wallet reference required")
    with engine.begin() as conn:
        result = conn.execute(
            text("""UPDATE dpr_analysis_branch_status
          SET wallet_storage_status='STORED', wallet_reference=:ref,
              wallet_last_error_code=NULL, wallet_next_retry_at=NULL,
              updated_at=now() WHERE run_id=:id"""),
            {"ref": wallet_reference, "id": run_id},
        )
        return result.rowcount == 1


def mark_wallet_retry(engine, run_id: str, error_code: str, retry_at) -> bool:
    # A stale failure must never overwrite a previously successful storage event.
    with engine.begin() as conn:
        result = conn.execute(
            text("""UPDATE dpr_analysis_branch_status
          SET wallet_storage_status='RETRY', wallet_last_error_code=:code,
              wallet_next_retry_at=:retry_at, updated_at=now()
          WHERE run_id=:id AND wallet_storage_status <> 'STORED'"""),
            {"code": error_code[:80], "retry_at": retry_at, "id": run_id},
        )
        return result.rowcount == 1


def mark_report_completed(engine, run_id: str, artifact_ref: str) -> bool:
    if not artifact_ref.strip():
        raise ValueError("Artifact reference required")
    with engine.begin() as conn:
        result = conn.execute(
            text("""UPDATE dpr_analysis_branch_status
          SET report_status='COMPLETED', report_artifact_ref=:ref,
              updated_at=now() WHERE run_id=:id"""),
            {"ref": artifact_ref, "id": run_id},
        )
        return result.rowcount == 1


def get_branch_status(engine, run_id: str) -> dict | None:
    with engine.connect() as conn:
        row = (
            conn.execute(
                text("""SELECT run_id, wallet_storage_status,
          wallet_reference, wallet_last_error_code, wallet_next_retry_at,
          report_status, report_artifact_ref, document_retention_status,
          updated_at FROM dpr_analysis_branch_status WHERE run_id=:id"""),
                {"id": run_id},
            )
            .mappings()
            .first()
        )
        return dict(row) if row else None
