#!/usr/bin/env python3
"""Offline diagnosis of Core LAS DPR financial extraction, without Gemini.

Usage:
  python scripts/diagnose_dpr_financials.py path/to/analysis_result.json

Only structural presence/status is printed; no borrower or monetary amounts
are logged. Use an authorized local JSON export of an AnalysisResult.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.dpr_agentic.financial_schema import (
    ProjectFinancials, financial_extraction_completeness,
)


def diagnose(payload: dict) -> dict:
    candidate = payload.get("report", payload)
    finance_raw = candidate.get("extracted_financials")
    financials = ProjectFinancials.model_validate(finance_raw or {})
    result = financial_extraction_completeness(financials)
    return {
        "has_financial_data": result["has_financial_data"],
        "missing_sections": result["missing_sections"],
        "cost_components": len(financials.cost_of_project_components),
        "project_total_present": financials.cost_of_project_stated_total is not None,
        "funding_components": len(financials.means_of_finance_components),
        "repayment_rows": len(financials.repayment_table),
        "projection_years": len(financials.years),
        "financial_warnings": [
            x.split(":", 1)[0] for x in candidate.get("unresolved_questions", [])
            if str(x).startswith(("FINANCIAL_", "FINANCIAL SECTION"))
        ],
    }


def main() -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("json_report", type=Path)
    args = cli.parse_args()
    data = json.loads(args.json_report.read_text(encoding="utf-8"))
    print(json.dumps(diagnose(data), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
