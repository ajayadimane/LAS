"""M5: Policy and Research agents with Google Search grounding
(grounding.py). Privacy of the search brief, source checks, caching,
benchmark findings, graph wiring and the CAM snapshot."""

import asyncio
import hashlib
import json

import httpx
import pytest

from app.services.dpr_agentic.cam_adapter import build_snapshot_from_analysis
from app.services.dpr_agentic.financial_schema import (
    ExtractedValue,
    HarvesterAssumptions,
    ProjectFinancials,
    UnitEconomics,
    YearFinancials,
)
from app.services.dpr_agentic.gemini import GeminiAgentClient, GeminiAgentError
from app.services.dpr_agentic.grounding import (
    FileGroundingCache,
    benchmark_findings,
    benchmarks_from,
    build_search_brief,
    cache_key,
    run_grounded_role,
    state_of,
    statements_from_segments,
    supported_segments,
    tier_of,
)
from app.services.dpr_agentic.schemas import (
    AgentOutput,
    AnalysisInput,
    AnalysisResult,
    DocumentFact,
)


def ev(value, page=4):
    return ExtractedValue(value=value, page=page, status="extracted")


def fact(field, value):
    return DocumentFact(field=field, value=value, page=1, quote=value)


FACTS = [
    fact("borrower_name", "Ramesh Kumar Patel"),
    fact("project_location", "Vill- Bhahuribandh, Ward No.17, Dist: Rewa (MP), 486446"),
    fact("business_sector", "Agriculture"),
    fact("business_subsector", "Combine harvester custom hiring by Ramesh"),
    fact("scheme_claimed", "Agriculture Infrastructure Fund (AIF)"),
    fact("bank_name", "State Bank of India"),
]


def test_the_search_brief_carries_no_borrower_identity():
    fin = ProjectFinancials(
        term_loan_amount=ev(21.0),
        harvester_assumptions=HarvesterAssumptions(
            acres_per_year=ev(1200), price_per_acre=ev(1800)
        ),
    )
    brief = build_search_brief(FACTS, fin, {})
    sent = json.dumps(brief).casefold()
    for secret in (
        "ramesh",
        "kumar",
        "patel",
        "bhahuribandh",
        "rewa",
        "486446",
        "ward",
    ):
        assert secret not in sent
    assert brief["state"] == "Madhya Pradesh"
    assert brief["loan_band"] == "10-25 lakh"
    assert brief["bank"] == "State Bank of India"
    # Canonical, the same every run: the cache key must not change with
    # wording ("AGRI INFRA" vs "Agriculture Infrastructure Fund AIF").
    assert brief["subsector"] == "combine harvester custom hiring"
    assert brief["scheme_claimed"] == "Agriculture Infrastructure Fund (AIF)"
    assert brief["dpr_operating_figures"] == {
        "unit": "acre",
        "acres_per_year": 1200,
        "price_per_unit_rupees": 1800,
    }


def test_the_upload_bank_name_wins_and_unknown_states_are_left_out():
    brief = build_search_brief(FACTS[2:5], None, {"bank_name": "Canara Bank"})
    assert brief["bank"] == "Canara Bank"
    assert state_of("Plot 4, Some Village") is None


def test_cache_key_ignores_the_dprs_own_figures():
    a = {"bank": "SBI", "state": "Goa", "dpr_operating_figures": {"x": 1}}
    b = {"bank": "sbi", "state": "Goa", "dpr_operating_figures": {"x": 2}}
    assert cache_key("policy", a) == cache_key("policy", b) != cache_key("research", a)


def test_source_tiers():
    assert tier_of("agriinfra.dac.gov.in") == "official"
    assert tier_of("www.sbi.co.in") == "official"
    assert tier_of("pib.gov.in") == "official"
    assert tier_of("krishijagran.com") == "secondary"
    assert tier_of("notsbi.co.in.example.com") == "secondary"


GROUNDING = {
    "webSearchQueries": ["AIF scheme interest subvention", "harvester acres per day"],
    "groundingChunks": [
        {"web": {"uri": "https://redirect/1", "title": "agriinfra.dac.gov.in"}},
        {"web": {"uri": "https://redirect/2", "title": "krishijagran.com"}},
    ],
    "groundingSupports": [
        {
            "segment": {"text": "AIF gives 3% interest subvention up to Rs 2 crore."},
            "groundingChunkIndices": [0],
        },
        {
            "segment": {"text": "A combine harvests 8-10 acres a day."},
            "groundingChunkIndices": [1],
        },
        {"segment": {"text": "Unsupported sentence."}, "groundingChunkIndices": []},
    ],
}


def test_only_source_backed_segments_survive():
    segments = supported_segments(GROUNDING)
    assert [s["id"] for s in segments] == ["S1", "S2"]
    assert segments[0]["sources"][0]["tier"] == "official"


def test_statements_are_the_supported_sentences_official_first():
    grounding = {
        **GROUNDING,
        "groundingSupports": [
            GROUNDING["groundingSupports"][1],
            GROUNDING["groundingSupports"][0],
            {
                "segment": {"text": "This applicant is eligible for AIF."},
                "groundingChunkIndices": [0],
            },
        ],
    }
    statements = statements_from_segments("policy", supported_segments(grounding))
    assert [s.text[:8] for s in statements] == ["AIF give", "A combin"]
    assert statements[0].official and not statements[1].official
    assert statements[0].sources[0].domain == "agriinfra.dac.gov.in"


RESEARCH_ANSWER = """A combine harvests 8-10 acres a day.
BENCHMARK: capacity_per_day = 8-10 acres/day
BENCHMARK: operating_days_per_season = 30-45 days
BENCHMARK: price_per_unit_rupees = 1200-1500 rupees/acre
BENCHMARK: seasons_per_year = 2-2 seasons
BENCHMARK: made_up_metric = 1-2 x
"""
RESEARCH_GROUNDING = {
    "groundingChunks": GROUNDING["groundingChunks"],
    "groundingSupports": [
        {
            "segment": {"text": "A combine harvests 8-10 acres a day."},
            "groundingChunkIndices": [1],
        },
        {
            "segment": {
                "text": "Harvest windows last 30 to 45 days, twice a year (2 seasons)."
            },
            "groundingChunkIndices": [1],
        },
        {
            "segment": {"text": "Custom hiring rates are Rs 1,200-1,500 per acre."},
            "groundingChunkIndices": [0],
        },
    ],
}


def test_benchmark_lines_need_their_figures_in_a_sourced_sentence():
    segments = supported_segments(RESEARCH_GROUNDING)
    benchmarks = benchmarks_from(RESEARCH_ANSWER, segments)
    got = {b.metric: (b.value_low, b.value_high) for b in benchmarks}
    assert got == {
        "capacity_per_day": (8, 10),
        "operating_days_per_season": (30, 45),
        "price_per_unit_rupees": (1200, 1500),
        "seasons_per_year": (2, 2),
    }
    rate = next(b for b in benchmarks if b.metric == "price_per_unit_rupees")
    assert rate.official and rate.sources[0].domain == "agriinfra.dac.gov.in"
    unsourced = benchmarks_from(
        "BENCHMARK: capacity_per_day = 25-30 acres/day", segments
    )
    assert unsourced == []


class _GroundedLLM:
    def __init__(self, answer="answer", grounding=None):
        self.answer, self.grounding, self.calls = answer, grounding or GROUNDING, []

    async def grounded_search(self, prompt, brief):
        self.calls.append(("search", brief))
        return {"text": self.answer, "grounding": self.grounding}


@pytest.mark.asyncio
async def test_results_are_cached_by_brief(tmp_path):
    llm = _GroundedLLM()
    cache = FileGroundingCache(tmp_path)
    brief = {"bank": "SBI", "sector": "Agriculture"}
    first = await run_grounded_role(llm, "policy", brief, cache)
    second = await run_grounded_role(llm, "policy", brief, cache)
    assert [c[0] for c in llm.calls] == ["search"]
    assert [s.text for s in first] == [s.text for s in second]
    assert len(first) == 2


def test_search_benchmarks_check_the_rate_only_outside_the_table():
    statements = benchmarks_from(
        RESEARCH_ANSWER, supported_segments(RESEARCH_GROUNDING)
    )
    # A harvester DPR is checked against the fixed table (benchmarks.py),
    # never against whatever a search returned.
    harvester = ProjectFinancials(
        harvester_assumptions=HarvesterAssumptions(
            acres_per_year=ev(1200), price_per_acre=ev(1800)
        )
    )
    assert benchmark_findings(statements, harvester) == []
    other = ProjectFinancials(
        unit_economics=UnitEconomics(unit_name="acre", price_per_unit_rupees=ev(1800))
    )
    [rate] = benchmark_findings(statements, other)
    assert rate.priority == "should_fix" and rate.check_id == "BENCHMARK_RATE"
    assert "1200-1500" in rate.explanation
    within = ProjectFinancials(
        unit_economics=UnitEconomics(unit_name="acre", price_per_unit_rupees=ev(1400))
    )
    assert benchmark_findings(statements, within) == []


@pytest.mark.asyncio
async def test_research_role_returns_sentences_and_benchmarks_in_one_call():
    llm = _GroundedLLM(RESEARCH_ANSWER, RESEARCH_GROUNDING)
    statements = await run_grounded_role(llm, "research", {"b": 1}, None)
    assert len(llm.calls) == 1
    assert sum(1 for s in statements if s.metric) == 4
    assert sum(1 for s in statements if not s.metric) == 3


@pytest.mark.asyncio
async def test_grounded_search_request_uses_the_search_tool_without_a_schema():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {"parts": [{"text": "answer"}]},
                        "groundingMetadata": GROUNDING,
                    }
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gemini = GeminiAgentClient(client, "fake-key", "gemini-3.8-flash")
        out = await gemini.grounded_search("prompt", {"bank": "SBI"})
    assert bodies[0]["tools"] == [{"google_search": {}}]
    assert "responseSchema" not in bodies[0]["generationConfig"]
    assert out["grounding"]["webSearchQueries"]


def _payload(project_context=None):
    return AnalysisInput(
        service_request_id="SR",
        wallet_document_id="W",
        document_sha256=hashlib.sha256(b"g").hexdigest(),
        pages=[{"page": 1, "text": "Project report: combine harvester"}],
        project_context=project_context or {},
    )


class _GraphLLM(_GroundedLLM):
    async def generate(self, prompt, context):
        from app.services.dpr_agentic.orchestrator import PROMPTS

        name = next(k for k, v in PROMPTS.items() if prompt.startswith(v))
        if name == "document":
            return AgentOutput(summary="doc", document_facts=FACTS)
        return AgentOutput(summary=name)

    async def extract(self, prompt, context):
        return ProjectFinancials(
            term_loan_amount=ev(21.0),
            harvester_assumptions=HarvesterAssumptions(acres_per_year=ev(1200)),
            years=[YearFinancials(year=1, net_profit=2.0, depreciation=1.0)],
        )


def test_the_graph_runs_grounded_policy_and_research_and_fills_the_cam():
    from app.services.dpr_agentic.parallel_graph import analyze_parallel

    llm = _GraphLLM()
    result = asyncio.run(
        analyze_parallel(
            _payload({"bank_name": "Canara Bank"}), llm, grounding_enabled=True
        )
    )
    searches = [c[1] for c in llm.calls if c[0] == "search"]
    assert (
        len(searches) == 2
        and sum(b.get("bank") == "Canara Bank" for b in searches) == 1
    )
    assert "ramesh" not in json.dumps(searches).casefold()
    assert {s.kind for s in result.external_evidence} == {"policy", "research"}
    assert any(
        q.startswith("POLICY_CONFIRMATION:") for q in result.unresolved_questions
    )
    snap = build_snapshot_from_analysis(
        result, report_id="r", report_version=1, analysis_run_id="r"
    )
    [statement] = snap.policy_assessment.statements
    assert statement.source_url == "https://redirect/1"
    assert statement.status.value == "bank_confirmation_pending"
    assert any(
        k.startswith("Market evidence")
        for k in snap.operational_assumptions.assumptions
    )


def test_a_failed_search_degrades_the_role_and_search_off_makes_no_call():
    from app.services.dpr_agentic.parallel_graph import analyze_parallel

    class Failing(_GraphLLM):
        async def grounded_search(self, prompt, brief):
            raise GeminiAgentError("down")

    result = asyncio.run(
        analyze_parallel(_payload(), Failing(), grounding_enabled=True)
    )
    assert result.status == "completed" and result.external_evidence == []
    assert "ROLE_UNAVAILABLE: policy -- re-run the analysis to include it." in (
        result.unresolved_questions
    )
    llm = _GraphLLM()
    asyncio.run(analyze_parallel(_payload(), llm))
    assert llm.calls == []


def test_old_reports_without_evidence_still_load():
    old = {
        "service_request_id": "s",
        "wallet_document_id": "w",
        "document_sha256": "a" * 64,
        "status": "completed",
        "agent_outputs": {},
        "findings": [],
        "unresolved_questions": [],
    }
    assert AnalysisResult.model_validate(old).external_evidence == []


def test_upload_project_context_skips_unset_details():
    from types import SimpleNamespace

    from app.services.dpr_agentic.document_queue import _project_context

    svc = SimpleNamespace(
        details=[
            SimpleNamespace(key="bank_name", field_id="BankName"),
            SimpleNamespace(key="project_name", field_id="Project_Name"),
        ]
    )
    assert _project_context(svc, {"BankName": " SBI ", "Project_Name": "NA"}) == {
        "bank_name": "SBI"
    }


def test_staged_project_context_is_encrypted_and_round_trips(dpr_pg):
    from cryptography.fernet import Fernet
    from sqlalchemy import text

    from app.services.documents import StoredFile
    from app.services.dpr_agentic.jobs import submit
    from app.services.dpr_agentic.upload_staging import (
        load_staged_project_context,
        stage_upload,
    )

    key = Fernet.generate_key()
    run_id, _ = submit(
        dpr_pg,
        service_request_id="p:DPR",
        document_sha256="b" * 64,
        source_reference="ref",
    )
    stage_upload(
        dpr_pg,
        run_id,
        StoredFile(name="d.pdf", content_type="application/pdf", data=b"%PDF"),
        key,
        project_context={"bank_name": "Canara Bank"},
    )
    assert load_staged_project_context(dpr_pg, run_id, key) == {
        "bank_name": "Canara Bank"
    }
    with dpr_pg.connect() as conn:
        raw = conn.execute(
            text("SELECT project_context_ciphertext FROM dpr_upload_staging")
        ).scalar()
    assert b"Canara" not in bytes(raw)


def test_a_harvester_dpr_gets_its_subsector_from_the_extraction():
    fin = ProjectFinancials(harvester_assumptions=HarvesterAssumptions())
    assert (
        build_search_brief([], fin, {})["subsector"]
        == "combine harvester custom hiring"
    )


def test_free_text_loses_borrower_name_words_and_unknown_schemes():
    facts = [
        fact("borrower_name", "Ramesh Kumar"),
        fact("business_subsector", "Dairy unit of Ramesh Kumar"),
        fact("scheme_claimed", "Ramesh's own savings plan"),
    ]
    brief = build_search_brief(facts, None, {"bank_name": "SBI, Rewa branch"})
    assert brief == {"bank": "State Bank of India", "subsector": "Dairy unit of"}


def test_canonical_scheme_and_bank_names_and_bank_domains():
    from app.services.dpr_agentic.grounding import canonical_bank, canonical_schemes

    assert canonical_schemes("INTEREST SUBSIDY UNDER AGRI INFRA") == (
        canonical_schemes("Agriculture Infrastructure Fund AIF")
    )
    assert canonical_bank("STATE BANK OF INDIA", set()) == canonical_bank("SBI", set())
    assert tier_of("bankofmaharashtra.bank.in") == "official"


def test_statements_are_capped():
    from app.services.dpr_agentic.grounding import MAX_STATEMENTS

    segments = [
        {
            "id": f"S{i}",
            "text": f"Fact {i}.",
            "sources": [{"url": f"u{i}", "domain": "example.com", "tier": "secondary"}],
        }
        for i in range(40)
    ]
    assert len(statements_from_segments("policy", segments)) == MAX_STATEMENTS


def test_markdown_table_rows_become_plain_text_and_universities_are_official():
    segments = [
        {
            "id": "S1",
            "sources": [
                {"url": "u", "domain": "tnau.ac.in", "tier": tier_of("tnau.ac.in")}
            ],
            "text": "| **Machine Capacity per Day** | **12 – 25 acres/day** |",
        }
    ]
    [statement] = statements_from_segments("research", segments)
    assert statement.text == "Machine Capacity per Day: 12 – 25 acres/day"
    assert statement.official


def test_the_cache_key_changes_with_the_grounding_version(monkeypatch):
    import app.services.dpr_agentic.grounding as grounding

    before = cache_key("policy", {"bank": "SBI"})
    monkeypatch.setattr(grounding, "GROUNDING_VERSION", grounding.GROUNDING_VERSION + 1)
    assert cache_key("policy", {"bank": "SBI"}) != before


@pytest.mark.asyncio
async def test_research_searches_without_bank_scheme_or_loan():
    llm = _GroundedLLM()
    brief = {
        "bank": "SBI",
        "scheme_claimed": "AIF",
        "loan_band": "10-25 lakh",
        "subsector": "combine harvester custom hiring",
        "state": "Goa",
    }
    await run_grounded_role(llm, "research", brief, None)
    await run_grounded_role(llm, "policy", brief, None)
    research_brief, policy_brief = (c[1] for c in llm.calls)
    assert research_brief == {
        "subsector": "combine harvester custom hiring",
        "state": "Goa",
    }
    assert policy_brief == brief


def test_benchmark_findings_are_anchored_against_downgrades():
    from app.services.dpr_agentic.finding_judge import apply_verdicts, assign_ids
    from app.services.dpr_agentic.schemas import Verdict

    statements = benchmarks_from(
        RESEARCH_ANSWER, supported_segments(RESEARCH_GROUNDING)
    )
    fin = ProjectFinancials(
        unit_economics=UnitEconomics(unit_name="acre", price_per_unit_rupees=ev(1800))
    )
    [rate] = assign_ids(benchmark_findings(statements, fin))
    assert rate.check_id == "BENCHMARK_RATE"
    kept, _ = apply_verdicts(
        [rate],
        [
            Verdict(
                finding_id="F01", decision="downgrade", reason="r", new_severity="low"
            )
        ],
    )
    assert (kept[0].severity, kept[0].priority) == ("medium", "should_fix")
