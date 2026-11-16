"""Scoring for the evaluation harness, and Gemini record/replay. Offline."""

import asyncio
import json

import httpx
import pytest

from app.services.dpr_agentic.evaluation import (
    draft_annotation,
    figures_match,
    flatten_figures,
    score_document,
    score_extraction,
    score_findings,
    stability,
    usage_summary,
)
from app.services.dpr_agentic.gemini import GeminiAgentClient
from app.services.dpr_agentic.gemini_cassette import (
    CassetteMiss,
    RecordingTransport,
    ReplayTransport,
)

FIN = {
    "cost_of_project_stated_total": 30.0,
    "term_loan_amount": {"value": 22.5, "page": 3, "status": "extracted"},
    "loan_tenor_years": {"value": None, "page": None, "status": "missing"},
    "years": [{"year": 1, "net_profit": 4.27, "principal_repayment": None}],
    "repayment_table": [
        {
            "year": 1,
            "principal_due": {"value": 3.21, "page": 9, "status": "extracted"},
            "interest_due": {"value": 2.03, "page": 9, "status": "extracted"},
        }
    ],
}


def _finding(
    title, category="financial_calculation", page=8, priority="must_fix", key=None
):
    return {
        "title": title,
        "explanation": "",
        "category": category,
        "evidence_page": page,
        "priority": priority,
        "dedup_key": key or title,
    }


def test_figures_match_uses_relative_or_absolute_tolerance():
    assert figures_match(22.5, 22.5)
    assert figures_match(1000.0, 1004.0)  # within 0.5%
    assert not figures_match(1000.0, 1006.0)
    assert figures_match(0.0, 0.009)  # within 0.01 absolute
    assert not figures_match(22.5, None)


def test_flatten_reads_extracted_values_and_falls_back_to_repayment_table():
    flat = flatten_figures(FIN)
    assert flat["term_loan_amount"] == 22.5
    assert flat["loan_tenor_years"] is None  # status "missing" never counts
    assert flat["years.1.principal_repayment"] == 3.21
    assert flat["years.1.interest_repayment"] == 2.03


def test_score_extraction_reports_each_expected_field():
    expected = {
        "cost_of_project_stated_total": 30,
        "loan_tenor_years": 7,
        "years": {1: {"net_profit": 4.27}},
    }
    scores = {s.field: s.ok for s in score_extraction(expected, FIN)}
    assert scores == {
        "cost_of_project_stated_total": True,
        "loan_tenor_years": False,
        "years.1.net_profit": True,
    }


def test_must_find_recall_and_must_not_find_violations():
    annotation = {
        "must_find": [
            {"id": "fuel", "any_keywords": ["fuel"]},
            {"id": "crew", "any_keywords": ["crew"], "page": 8},
            {"id": "dscr", "any_keywords": ["dscr"], "categories": ["dscr"]},
        ],
        "must_not_find": [{"id": "fake_quote", "any_keywords": ["quotation missing"]}],
    }
    findings = [
        _finding("Fuel cost understated"),
        _finding("Crew wages understated", page=9),  # wrong page
        _finding("Dealer quotation missing", category="missing_document"),
    ]
    score = score_findings(annotation, findings)
    assert score.found == ["fuel"]
    assert score.missed == ["crew", "dscr"]
    assert score.recall == pytest.approx(1 / 3)
    assert score.violations == ["fake_quote"]


def test_a_spec_with_no_criteria_never_matches():
    assert score_findings({"must_find": [{"id": "empty"}]}, [_finding("x")]).found == []


def test_stability_compares_repeated_runs():
    a = {"findings": [_finding("A"), _finding("B")], "extracted_financials": FIN}
    b = {"findings": [_finding("A"), _finding("C")], "extracted_financials": FIN}
    s = stability([a, b])
    assert s["finding_key_jaccard"] == pytest.approx(1 / 3, abs=0.001)
    assert s["figures_identical_share"] == 1.0
    assert stability([a]) == {"runs": 1}


def test_usage_summary_prices_thinking_as_output_and_groups_by_role():
    usage = [
        {
            "role": "finance",
            "prompt_tokens": 10_000,
            "total_tokens": 14_000,
            "duration_ms": 900,
        },
        {
            "role": "finance",
            "prompt_tokens": 2_000,
            "total_tokens": 3_000,
            "duration_ms": 100,
        },
        {
            "role": "document",
            "prompt_tokens": 8_000,
            "total_tokens": 9_000,
            "duration_ms": 500,
        },
    ]
    s = usage_summary(usage, "gemini-3.8-flash", usd_inr=88.0)
    assert s["prompt_tokens"] == 20_000 and s["output_tokens"] == 6_000
    # (20k x 0.75 + 6k x 3.75) / 1M USD x 88
    assert s["cost_inr"] == round((20_000 * 0.75 + 6_000 * 3.75) / 1e6 * 88, 2)
    assert s["by_role"]["finance"]["calls"] == 2
    assert usage_summary(usage, "unknown-model")["cost_inr"] is None


def test_rejected_dpr_label_agreement_needs_a_must_fix():
    result = {
        "findings": [_finding("x", priority="should_fix")],
        "extracted_financials": FIN,
    }
    score = score_document("abc", "rejected", result, None)
    assert score.label_agrees is False
    assert score_document("abc", "approved", result, None).label_agrees is None


def test_draft_annotation_is_unreviewed_and_lists_candidates():
    result = {
        "findings": [_finding("Fuel cost understated")],
        "extracted_financials": FIN,
    }
    draft = draft_annotation("a" * 64, "rejected", result)
    assert draft["reviewed"] is False
    assert draft["expected"]["term_loan_amount"] == 22.5
    assert draft["expected"]["years"][1]["principal_repayment"] == 3.21
    assert draft["must_find"] == []
    assert draft["candidate_must_find_titles"] == ["Fuel cost understated"]


GEMINI_OK = {
    "candidates": [
        {
            "finishReason": "STOP",
            "content": {
                "parts": [
                    {
                        "text": json.dumps(
                            {
                                "summary": "ok",
                                "findings": [],
                                "unresolved_questions": [],
                            }
                        )
                    }
                ]
            },
        }
    ]
}


def test_record_then_replay_serves_the_same_response_offline(tmp_path):
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(200, json=GEMINI_OK)

    async def generate(transport):
        async with httpx.AsyncClient(transport=transport) as http:
            client = GeminiAgentClient(http, "fake-key", "gemini-3.8-flash")
            return await client.generate("Prompt", {"dpr": "same"})

    recorder = RecordingTransport(tmp_path, inner=httpx.MockTransport(upstream))
    assert asyncio.run(generate(recorder)).summary == "ok"
    assert recorder.recorded == 1
    # The API key is never part of what is stored.
    assert "fake-key" not in "".join(p.read_text() for p in tmp_path.iterdir())

    replay = ReplayTransport(tmp_path)
    assert asyncio.run(generate(replay)).summary == "ok"
    assert (replay.hits, len(calls)) == (1, 1)


def test_replay_miss_is_a_hard_failure(tmp_path):
    async def generate():
        async with httpx.AsyncClient(transport=ReplayTransport(tmp_path)) as http:
            client = GeminiAgentClient(http, "fake-key", "gemini-3.8-flash")
            return await client.generate("Prompt", {"dpr": "never recorded"})

    with pytest.raises(CassetteMiss):
        asyncio.run(generate())


def test_topic_agreement_matches_reworded_titles_but_not_different_issues():
    from app.services.dpr_agentic.evaluation import topic_agreement

    run1 = [
        "Missing Vendor Pro-Forma Invoice / Equipment Quotation",
        "Fuel cost understated",
    ]
    run2 = [
        "Missing Vendor Quotation / Pro-Forma Invoice for Equipment",
        "Crew wages understated",
    ]
    assert topic_agreement(run1, run2) == 0.5  # quotation matches; fuel != crew
    assert topic_agreement([], []) == 1.0
    assert topic_agreement(["A thing"], []) == 0.0
