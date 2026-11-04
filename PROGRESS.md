# DPR CAM Report — Progress

Tracks the implementation described in the "DPR Loan Readiness Report with
Detailed CAM Annexures" brief. Milestones follow Section 3A of that brief.

## Status: Milestone 2 (Financial engine and finding consolidation) — done, with one disclosed gap

Finding deduplication/consolidation, the priority-rule engine (Section 5.1,
Section 7), and the deterministic financial engine (Section 9: amortization,
DSCR under both CADS definitions, three break-even types, fund-flow
reconciliation, tax-rate derivation, scenario runs, sector-feasibility
plugin registry) are done and pass the Tikamgarh golden-case test built
from the brief's own Section 18 figures. **Not done**: a full multi-year
P&L/balance-sheet/cash-flow projection model (see "Known gap" below) — this
is the one piece of Section 9 deliberately deferred rather than built
hollow. Milestone 1 is unchanged and still done.

## Blocker reported and resolved with the user

`cam-design-reference/` does not contain the six numbered archives the brief
describes. Actual contents:

- `Page 1 — Summary-html.zip`
- `Page 2 — Project, cost and DPR changes-html.zip` (brief expects this to be
  page 3, titled "Project, cost and corrections")
- `Page 3 — Repayment-html.zip`
- `Page 4 — Security and obligations-html.zip`
- `Page 5 — Benefits and next steps-html.zip`
- `Fix-html.zip` (unnumbered; brief expects a numbered page 2, "Fix before you
  apply")
- `LAS Customer CAM Report Template.pdf` (not mentioned in the brief at all)

User decision (2026-11-04): proceed treating `Fix-html.zip` as page 2 ("Fix
before you apply") and the five numbered zips as pages 1, 3, 4, 5, 6 per the
brief's content descriptions, not their filenames. The six archives have not
yet been unzipped/ported to Jinja2 — that's milestone 4.

## Architecture assessment

### What already exists and should be reused, not rebuilt

- `app/services/dpr_agentic/parallel_graph.py` — the real pipeline (LangGraph,
  dependency-aware parallel supersteps: document → planner → {finance,
  sector, research, policy} in parallel → risk → verifier → correction →
  report). This matches the brief's pipeline diagram. `orchestrator.py`'s
  sequential `analyze()` is the older path, still used by the disabled
  internal preview API (`app/api/v1/dpr_analysis.py`); both share `PROMPTS`,
  `extract_calculated_facts`, `verify_evidence`.
- `app/services/dpr_agentic/financial_schema.py` + `financial_calc.py` — a
  real, code-only (no LLM) reconciliation engine: cost-of-project vs.
  components, means-of-finance vs. components, means-of-finance vs. cost,
  AIF subvention arithmetic, per-year DSCR (standard formula, compared
  against the DPR's own stated DSCR where given). This is the seed of
  Section 9's financial engine but covers a small fraction of what's asked
  (see Gaps below).
- `app/services/dpr_agentic/report_access.py` + `report_service.py` — the
  release gate (payment via a fresh Wallet re-check, consent, ownership,
  artifact integrity, retention). This is exactly the authorization model
  Section 15/16 asks for; reuse as-is, do not rebuild.
- `app/api/v1/customer.py` `GET /services/{code}/report` — already supports
  `?format=pdf` alongside the JSON body, gated by `access_report()`. The
  "viewable and downloadable" requirement the user opened with is already
  wired end-to-end for a generic report; what's missing is CAM-shaped
  content, not the plumbing.
- `app/services/dpr_agentic/report_render.py` — the current PDF (ReportLab,
  generic "Calculated Figures / Findings / Open Questions" layout). This is
  the "full descriptive format listed" the user is asking to replace with
  the six-page CAM design. It will be superseded, not patched, since the
  brief requires Jinja2 HTML → PDF with the specific CAM visual design,
  which ReportLab's flowable model can't produce faithfully.
- `app/services/dpr_agentic/report_store.py` — persists one `AnalysisResult`
  JSON per `run_id` in `dpr_report_artifacts` (migration 003). **Overwrites
  on conflict (`ON CONFLICT ... DO UPDATE`) — violates Section 16's "do not
  overwrite previous report versions."** Needs a version column and
  insert-only semantics in milestone 5.
- `frontend/src/components/AnalysisReport.tsx` — the "full descriptive
  format listed" the user referred to: a flat `<ul>` of findings with no PDF
  download link, even though the backend already serves one. Milestone 6
  work: wire a download button to the existing `?format=pdf` endpoint at minimum,
  then replace the list with the six-page preview once the CAM renderer exists.

### Root cause of duplicate findings (Section 5.1), found and explained

`parallel_graph.py`'s dependency graph deliberately feeds each agent's
upstream dependencies as context (e.g. `verifier` sees `risk`, `finance`,
`sector`, `research`, `policy`; `report` sees `correction`, `verifier`,
`risk`). Every one of `document, planner, finance, sector, research, policy,
risk, verifier, correction, report` can independently emit its own
`findings` list, and `analyze_parallel()` simply concatenates
`out.findings` for every agent output with **no deduplication whatsoever**
(`parallel_graph.py:125-126`). Because `risk` restates issues from `finance`/
`sector`/etc., `verifier` restates issues from `risk`, `correction` restates
issues from `verifier`, and `report` restates issues from `correction`, a
single real defect can legitimately surface in up to 5 of the 10 agent
outputs by construction — not a bug in any one agent, but a missing
consolidation step after the graph runs. This matches the brief's "~50
findings covering 13 distinct issues" report exactly. Fix (milestone 2): a
post-graph consolidation pass keyed on category + affected metric + evidence
page, run once in `analyze_parallel()`/`analyze()` before findings are
returned, not inside any agent.

### Gaps vs. the brief (what milestone 2+ must add)

- **No full financial engine.** `financial_calc.py` only reconciles stated
  totals and per-year DSCR against the DPR's *own* stated figures. It has no
  P&L/balance-sheet/cash-flow projection, no amortization schedule
  generation, no break-even (of any of the three kinds), no scenario
  analysis, and no sector-specific technical-feasibility checks (acres/day,
  dairy yield, polyhouse cycles, etc.). The Finance/Sector agents only
  return narrative `AgentOutput` (summary + findings), not the structured
  figures `FinancialProjection`/`RepaymentPeriod`/`ScenarioAnalysis` need.
  Building this is the bulk of milestone 2.
- **No defect-vs-business-risk tag at the source.** `cam_adapter.py`
  currently infers this from `Finding.category` as a placeholder heuristic
  (`_DOCUMENT_DEFECT_CATEGORIES`). The brief's intent (Section 5.1.6) is
  better served by extending the Risk/Verification agent prompts to emit
  this classification directly — flagged here per Section 4.1 rather than
  guessed permanently.
- **No borrower/project identity in the pipeline at all.**
  `AnalysisInput`/`AnalysisResult` only ever carry document text; borrower
  name, project name, sector, location etc. must come from Core LAS'
  existing enrolment/person records, not the agentic pipeline. Confirmed by
  reading `app/services/enrolment.py` call sites in `report_service.py`.
  `cam_adapter.build_snapshot_from_analysis()` takes `borrower_profile`/
  `project_profile` as explicit caller-supplied arguments for this reason
  and leaves them empty rather than inventing anything if omitted.
- **No readiness-score dimension engine, no priority-rule engine.** Both are
  Section 6.1/7 work for milestone 2; `cam_adapter.py` currently maps
  severity → priority 1:1 as an explicitly-labeled (`priority_rule =
  "PROVISIONAL_SEVERITY_MAP_NOT_RULE_ENGINE"`) placeholder, not the real rule
  table.
- **No verification layer (Section 13), no PDF/HTML CAM renderer (Section
  14), no annexures (Section 12), no new API routes (Section 15), no
  report versioning (Section 16), no frontend CAM preview (Section 17).**
  All later milestones.

## Milestone 1 deliverables (this commit)

- `app/services/dpr_agentic/cam_snapshot.py` — the `CAMSnapshot` Pydantic
  model and every nested model required by Section 5 (BorrowerProfile,
  ProjectProfile, DocumentReference, ProjectCost, MeansOfFinance,
  OperationalAssumptions, FinancialProjection, RepaymentPeriod,
  FinancialRatios, ScenarioAnalysis, VerifiedFinding, CorrectionProposal,
  PolicyAssessment, ReadinessAssessment, EvidenceReference), plus
  `Provenance`, `CalculationTrace`, `Money` (Decimal-backed, currency/unit
  tagged). Schema version `1.0.0`.
- `app/services/dpr_agentic/cam_adapter.py` — `build_snapshot_from_analysis()`,
  mapping an existing `AnalysisResult` into a `CAMSnapshot`. Honest about
  what it cannot yet do (see Gaps above); never fabricates a missing value.
- `tests/test_dpr_cam_snapshot.py` — 6 tests: JSON round-trip, readiness
  status for no-findings/critical-finding/incomplete-analysis, that
  borrower/project identity is never invented when not supplied, and that
  `evidence_page` maps to `source_pages`.

### Tests run

```
python3 -m pytest tests/test_dpr_cam_snapshot.py -q   → 6 passed
python3 -m pytest tests/ -k dpr -q                     → all pass/skip, 0 failed
```
(Skips are pre-existing — gated by missing `DPR_AGENTIC_GEMINI_API_KEY` /
sandbox credentials, not caused by this change.)

## Agent-to-snapshot field mapping (Section 4.1)

| CAMSnapshot field | Source | Notes |
| --- | --- | --- |
| `findings[].verification` | `Finding.verification` (orchestrator's `verify_evidence`) | Already derivable, no agent change needed. |
| `findings[].value_classification` | `Finding.provenance` | Already derivable (`calculated`→recomputed, `extracted`→verified_source_fact, else unverified_assumption). |
| `findings[].finding_class` | **not derivable yet** — inferred from `category` as placeholder | Requires extending the Risk/Verification agent prompts (`orchestrator.PROMPTS["risk"]`/`["verifier"]`) to emit a `defect`/`business_risk` tag per finding, or extending `Finding` itself with that field. Not done in milestone 1 per Section 4.1 ("extend only when a required field cannot be derived" — documented here, implemented in milestone 2 alongside dedup). |
| `findings[].priority` / `priority_rule` | **not derivable yet** | Needs the deterministic rule engine (Section 7), milestone 2. |
| `borrower_profile`, `project_profile` | Core LAS enrolment/person records (Wallet-sourced), **not** the agentic pipeline | Caller-supplied to the adapter. |
| `project_cost`, `means_of_finance`, `financial_projections`, `repayment_schedule`, `financial_ratios`, `scenario_analysis` | **not derivable yet** | Needs `FinancialExtractionPrompt`/`ProjectFinancials` extended with full P&L/balance-sheet/cash-flow line items, plus a new deterministic calculation module (milestone 2). |
| `policy_assessment` | **not derivable yet** | The `policy` agent currently explicitly refuses to invent thresholds (no policy corpus attached) — correct behavior, but means `PolicyAssessment.statements` stays empty until a real, dated SBI policy source is attached. |

## Milestone 2 deliverables (dedup + priority rules)

### Root cause fix (Section 5.1 point 4)

The root cause identified in milestone 1 — `parallel_graph.py`/
`orchestrator.py` concatenating every agent's `findings` with no
deduplication — is fixed at the source: both `analyze()`
(`orchestrator.py`) and `analyze_parallel()` (`parallel_graph.py`) now call
`finding_consolidation.consolidate()` once, after every agent has run and
before `AnalysisResult` is constructed. No agent or prompt was changed.

- `app/services/dpr_agentic/finding_rules.py` — new module, no LLM:
  - `compute_dedup_key()` — category + normalized title (the "affected
    metric" proxy; see the module docstring for why title, not a dedicated
    metric field) + evidence page.
  - `pick_consolidated_severity()` — the *most severe* rating among
    duplicates, never whichever duplicate was produced first.
  - `classify_finding_class()` — document_defect vs. business_risk, from an
    explicit category allow-list (`_DOCUMENT_DEFECT_CATEGORIES`). This is a
    heuristic, not an agent-emitted tag — see "Known limitation" below.
  - `priority_for_calculation()` — the real Section 7 rule, using the
    `CalculationResult`'s own computed/stated values to get a true
    percentage impact: ≥5% (`PriorityThresholds.must_fix_impact_pct`,
    default 5.0) is `must_fix`, below it `should_fix`; a year-1 DSCR < 1.0
    is always `must_fix` regardless of percentage (the project cannot
    service its debt, full stop); a later year's DSCR < 1.0 is `should_fix`.
  - `priority_for_narrative()` — a severity-based fallback for findings with
    no `CalculationResult` behind them (every non-`financial_calc.py`
    finding): `missing_document` category or `critical` severity → must_fix,
    `high` → should_fix, else good_to_fix. Documented as a coarser proxy
    than the real rule, since narrative agents don't currently expose a
    dollar/percentage impact to classify against.
- `app/services/dpr_agentic/financial_calc.py` — `findings_from_calculations()`
  now runs every calculated `Finding` through `_finalize()`, which attaches
  `priority`/`priority_rule` from `priority_for_calculation()` (using that
  exact `CalculationResult`'s own numbers — this is where the 5% threshold
  is real, not a guess), `finding_class="document_defect"` (a calculated
  mismatch is a defect in the DPR by construction), and `dedup_key`.
- `app/services/dpr_agentic/finding_consolidation.py` — new module:
  `consolidate(findings)` groups by dedup key, merges the group into one
  `Finding` (most severe rating wins; the most-trustworthy duplicate's text
  — code-verified > extracted > ai_interpreted > assumption > unverified —
  becomes the kept explanation; any other duplicates' distinct evidence
  quotes are appended rather than dropped), sets `merged_duplicate_count`,
  and fills in `finding_class`/`priority`/`priority_rule` for narrative
  findings that don't already have them from `financial_calc.py`.
  Idempotent: re-running it on an already-consolidated list sums
  `merged_duplicate_count` rather than resetting it to 1 (caught by
  `test_consolidate_is_idempotent`, which failed before this fix — real bug,
  not a hypothetical).
- `app/services/dpr_agentic/schemas.py` — `Finding` gained five additive,
  defaulted fields: `dedup_key`, `merged_duplicate_count` (default 1),
  `finding_class`, `priority`, `priority_rule`. Backward compatible: a
  `Finding` persisted by `report_store.py` before this change still
  validates (all new fields default).
- `app/services/dpr_agentic/cam_adapter.py` — simplified to read the
  upstream-computed `dedup_key`/`merged_duplicate_count`/`finding_class`/
  `priority`/`priority_rule` directly, falling back to `finding_rules`'
  classifiers only for a `Finding` that predates these fields.

### Known limitation (Section 4.1 disclosure)

`classify_finding_class()` is a category-based heuristic, not something an
agent explicitly emits. It was deliberately **not** turned into a Risk/
Verification agent prompt change in this milestone, per Section 4.1 ("extend
an agent schema only when a required field cannot be derived any other
way") — categories already in use by the existing prompts map onto
defect/business-risk well enough to be useful today. If false
classifications show up against real DPRs (milestone 7's real-DPR
evaluation), the fix is to extend the `risk`/`verifier` prompts
(`orchestrator.PROMPTS`) to emit the tag directly and treat that as
authoritative over the category heuristic.

### Tests run

```
python3 -m pytest tests/test_dpr_finding_rules.py -q           → 13 passed
python3 -m pytest tests/test_dpr_finding_consolidation.py -q   → 9 passed
python3 -m pytest tests/test_dpr_cam_snapshot.py -q             → 7 passed
python3 -m pytest tests/ -k dpr -q                              → all pass/skip, 0 failed
python3 -m pytest tests/ -q                                     → full repo suite, 0 failed
ruff check / ruff format                                        → clean
```

## Milestone 2 part 2: the Section 9 financial engine

### No source Tikamgarh DPR file exists — how the golden case was built

The brief's Section 18 golden case points at
`[PATH TO TIKAMGARH DPR]` — a literal placeholder, never filled in. No file
named Tikamgarh/Nachanwara/AS-930 exists anywhere in the repo or in
`DPRData/` (checked: only generic harvester DPRs for other SBI applicants,
none matching this case). The only artifact that contains this worked
example is the *finished* six-page sample report itself —
`cam-design-reference/LAS Customer CAM Report Template.pdf` — which is
output, not the source DPR's own tables.

Rather than invent a source document, every input used below is a figure
that report already states as fact (extracted by reading the PDF — see
below), and every expected output is independently re-derived from those
inputs by arithmetic, not copied from the report's own headline numbers.
Where the brief's table only gives a ratio (e.g. "1.97 = ₹10.30 L ÷
₹5.24 L"), the ₹10.30 L numerator was decomposed algebraically (PAT +
Depreciation + Interest, with Depreciation = ₹4.50 L and Interest =
₹2.03 L given elsewhere in the same report, so PAT = ₹3.77 L) and
cross-checked against a second route (PBT ₹4.27 L − Tax ₹0.50 L = ₹3.77 L,
also independently stated) — both routes agree exactly, which is why this
is usable as a real regression case and not a circular restatement of the
target number. The "corrected costs" DSCR of 1.49× was derived the same
way (PBT₁ − (fuel delta + crew-wage delta), re-taxed at the derived rate)
and also lands on the report's own stated 1.49× exactly — see
`tests/test_dpr_cam_financial_engine.py`'s module docstring and inline
comments for the full arithmetic trail.

**Caveat**: because there is no real source DPR, this regression proves the
engine reproduces a self-consistent worked example correctly. It has not
been run against an actual extracted document end-to-end (that requires
either the real Tikamgarh DPR or a run against the `DPRData/` sample set —
milestone 7's real-DPR evaluation).

### What was built

- `app/services/dpr_agentic/cam_financial_engine.py` — new module, no LLM,
  Decimal throughout, rounds only at the point a value is returned:
  - `equal_principal_amortization_schedule()` — the only amortization style
    implemented. Confirmed against the golden case's own year-1 figures
    (₹22.50 L / 7 = ₹3.21 L principal; 9% × ₹22.50 L = ₹2.03 L interest) —
    this was *confirmed*, not assumed, by reverse-solving the DPR's own
    stated year-1 repayment before writing the function.
  - `dscr()` / `CadsDefinition` — both CADS definitions
    (`pat_dep_interest` default, `pat_dep` alternate), each independently
    computed from net profit/depreciation/interest/principal/interest-due,
    never from a document's own stated DSCR (that stays a separate
    comparison value, per `financial_schema.YearFinancials.stated_dscr`).
  - `accounting_breakeven()`, `operating_cashflow_breakeven()` (excludes
    the `depreciation_component` — always ≤ the accounting break-even for
    the same inputs, asserted directly),
    `debt_service_threshold_breakeven()` (solved algebraically for the
    unit volume at which CADS equals total debt service, not by search;
    returns `NOT_COMPUTABLE` with a reason outside its valid domain, e.g.
    zero/negative contribution margin or a ≥100% tax rate).
  - `fuel_cost_reconciliation()`, `crew_wage_reconciliation()` — harvester
    recompute-vs-stated checks (not yet generalized to other sectors).
  - `fund_flow_gap()` — reports a sources-vs-applications mismatch as
    exactly that; the function has no code path that could absorb the gap
    into a balancing entry, by construction.
  - `derive_tax_rate()` — tax ÷ PBT from the DPR's own figures, never an
    assumed statutory rate; flagged in its own formula string as derived.
  - `run_harvester_scenarios()` — re-runs the full PBT→tax→PAT→DSCR chain
    per scenario; a scenario's debt service always comes from the same
    loan terms as every other scenario (Section 9's "never combine one
    scenario's cash flow with another's debt service" — there is only one
    `principal_due`/`interest_due` pair passed in, shared by construction,
    not a per-scenario choice that could be mismatched). Each result
    carries `source_class` (`dpr_stated` / `recomputed`) and
    `is_hypothetical`.
- `app/services/dpr_agentic/sector_feasibility.py` — new module, a plugin
  registry (`SECTOR_FEASIBILITY_REGISTRY`): `HarvesterFeasibilityCheck`
  (acres/day × operating days/season × seasons/year — reproduces the
  report's own 300–600 acre feasible range from 10 acres/day × 30 days ×
  1–2 seasons, against the DPR's claimed 1,200) is implemented;
  `DairyFeasibilityCheck` and `PolyhouseFeasibilityCheck` are stubs that
  always return `NOT_COMPUTABLE` with a named reason, per the brief's
  explicit instruction not to invent sector assumptions the pipeline has
  no extracted data for.
- `tests/test_dpr_cam_financial_engine.py` — the golden-case test, written
  **before** the engine existed (confirmed red: `ImportError` on
  collection) and built against until green. 12 tests, all passing,
  covering every row of the Section 18 table this milestone claims:
  amortization, both DSCR definitions, fuel/crew recompute, both
  break-even types at both cost assumptions, fund-flow gap, tax-rate
  derivation, both scenarios, and the ≤13-distinct-findings dedup check
  using this case's own 8 page-2 defects simulated as being restated by 5
  agents each (40 raw findings → 8 distinct, no shared dedup keys).
- `tests/test_dpr_sector_feasibility.py` — 6 tests for the plugin registry.

### Known gap: no full P&L / cash-flow / balance-sheet projection model

The brief's Section 9/"Projections" step asks for multi-year P&L, cash
flow and a balance sheet that "must balance every year." This was **not
built**. Building it responsibly needs an opening asset register, a
depreciation schedule per asset, and working-capital norms — none of which
the current extraction pipeline captures (the Finance agent returns prose;
`financial_schema.ProjectFinancials` only carries per-year net
profit/depreciation/interest/repayment, not a balance sheet's asset/
liability lines). Building a balance sheet without that data would mean
inventing opening balances or working-capital assumptions, which Section
19.5 ("do not fabricate financial calculations") forbids directly. What
*is* built — amortization, DSCR, break-even, scenarios — covers every
P&L-derived figure the Section 18 golden case actually requires. The
remaining gap is: extend the Finance-agent extraction prompt
(`orchestrator.FINANCIAL_EXTRACTION_PROMPT`) to pull a fuller asset/
liability/working-capital picture when a DPR states one, then add a
balance-sheet roll-forward function alongside this engine. Not started.

### Tests run

```
python3 -m pytest tests/test_dpr_cam_financial_engine.py -q     → 12 passed
python3 -m pytest tests/test_dpr_sector_feasibility.py -q       → 6 passed
python3 -m pytest tests/ -k dpr -q                               → all pass/skip, 0 failed
python3 -m pytest tests/ -q                                      → full repo suite, 0 failed
ruff check app/ tests/                                            → clean
```

## Status: Milestone 3 (Verification layer, Section 13) — done

`app/services/dpr_agentic/cam_verification.py` — `verify_snapshot()`
implements all 13 checks from Section 13, each its own function so an
issue's `code` traces straight back to the brief. It sits strictly between
`cam_adapter.build_snapshot_from_analysis()` and rendering: it never
reruns an agent, never recomputes a figure, and never mutates the
snapshot — only reports. A `VerificationIssue` is `blocking` (must stop
publication, per Section 13's "critical inconsistencies must block final
report publication") or `warning` (surfaced to a reviewer, doesn't block).

| # | Check | Function |
| - | --- | --- |
| 1 | Required schema fields | `check_required_schema_fields` |
| 2 | Financial units | enforced by pydantic `Literal` types at parse time; no separate runtime check needed |
| 3 | Cross-page financial consistency | `check_cross_page_financial_consistency` (project cost vs. loan+promoter) |
| 4 | Source-reference existence | `check_source_reference_existence` |
| 5 | Page-level citations | `check_page_level_citations` |
| 6 | Calculation reproducibility | `check_calculation_reproducibility` |
| 7 | Scenario labeling | `check_scenario_labeling` |
| 8 | Policy-source validity | `check_policy_source_validity` |
| 9 | Readiness-status consistency | `check_readiness_status_consistency` |
| 10 | Unsupported claims | `check_unsupported_claims` (subsidy eligibility) |
| 11 | Placeholder elimination | `check_placeholder_elimination` |
| 12 | Report generation completeness | `check_report_generation_completeness` |
| 13 | Finding uniqueness | `check_finding_uniqueness` |

Check 11 (placeholder elimination) scans every string field in the
snapshot for two things: an unresolved Jinja binding (`{{...}}`, the exact
shape Section 6.2 warns about) and a fixed list of literal bracket
placeholders (`[Customer full name]`, `[BD name]`, `[Branch name]`, `[DD
Mon YYYY]`, `[YYYY]`, `[000000]`) copied directly from
`cam-design-reference/LAS Customer CAM Report Template.pdf` — that sample
PDF genuinely ships with these unresolved, which is exactly the defect
class this check exists to catch before it reaches a real customer.

### A real bug this layer caught before it shipped

Writing `test_5_invalid_page_number_is_blocking` (page `0`, an edge case a
real agent should never emit since `SourcePage.page` requires `ge=1`, but
not a constraint `Finding.evidence_page` itself enforces) failed against
`cam_adapter.py`'s existing `source_pages=[f.evidence_page] if
f.evidence_page else []` — Python's falsy-zero silently treats page `0` as
"no page" and drops it. Fixed to `is not None`. Low real-world impact
(page 0 shouldn't occur from the current pipeline) but a correctness bug
all the same, caught by the verification layer's own test suite, not by
inspection.

### Tests run

```
python3 -m pytest tests/test_dpr_cam_verification.py -q   → 23 passed
python3 -m pytest tests/ -k dpr -q                          → all pass/skip, 0 failed
python3 -m pytest tests/ -q                                  → full repo suite, 0 failed
ruff check app/ tests/                                        → clean
```

### Known limitation

Checks 3, 4 and 5 only exercise what's actually populated in a snapshot
today (project cost/means-of-finance totals, evidence registry, finding
page citations) — they do not yet check the Section 9 engine's own outputs
(amortization, DSCR, break-even, scenarios) for cross-page consistency,
because that engine isn't wired into `cam_adapter.py` yet (milestone 2's
disclosed remaining-work item). Once it is, `financial_ratios`/
`repayment_schedule`/`scenario_analysis` will actually be populated and
checks 6/7 will start exercising real data instead of the empty defaults
every test here currently supplies.

## Status: Milestone 2b (close the wiring gap) — done

Requested as five explicit items before milestone 4. Reporting against
each.

### 1. Golden test status: all nine Section 18 values, pass/fail

| # | Value | Status | Test |
| - | --- | --- | --- |
| 1 | Year-1 DSCR, `pat_dep` | **PASS** (1.58) | `test_year1_dscr_pat_dep_definition_matches_the_dprs_own_convention` |
| 2 | Year-1 DSCR, `pat_dep_interest` | **PASS** (1.97) | `test_year1_dscr_pat_dep_interest_definition` |
| 3 | Year-1 debt service | **PASS** (₹5.24 L = ₹3.21 L + ₹2.03 L) | `test_amortization_schedule_matches_the_dprs_own_repayment_table` |
| 4 | Fuel at 1,200 acres | **PASS** (₹9.12 L) | `test_fuel_cost_recomputes_higher_than_the_dpr_states` |
| 5 | Crew wages per staffing | **PASS** (₹3.30 L) | `test_crew_wages_per_staffing_exceed_the_p_and_l_figure` |
| 6 | Break-even at corrected costs | **PASS** (726 acres) | `test_accounting_breakeven_at_corrected_costs` |
| 7 | Means of finance 75/25 split | **PASS** (₹22.50 L + ₹7.50 L = ₹30.00 L) | `test_means_of_finance_split_is_75_25` (added this round -- it existed as constants but was never actually asserted before) |
| 8 | Fund-flow gap | **PASS** (₹40.29 L) | `test_fund_flow_gap_is_reported_not_plugged` |
| 9 | Consolidated findings ≤ 13 | **PASS** | `test_consolidated_distinct_findings_count_for_this_case_is_13_or_fewer` |

All nine pass standalone (`cam_financial_engine.py` called directly) and,
new this round, all reproduce again through the full schema/bridge/
orchestrator path (see items 2-4 below) — the Tikamgarh fixture
(`tests/fixtures/tikamgarh_cam_snapshot.json`) carries DSCR 1.97, breakeven
726, debt service ₹3.21 L/₹2.03 L, and 6 consolidated findings (the ninth
and seventh values -- fuel/crew/fund-flow -- surface as findings in that
same fixture rather than as separate top-level fields).

### 2. Gemini extraction schema extensions (Section 4.1 log)

All in `financial_schema.py`. Each is additive and defaulted; none change
an existing field's meaning.

| Field(s) added | Why it couldn't be derived another way |
| --- | --- |
| `ExtractedValue` (`value`, `page`, `status`) wrapper type | The engine's Section 13 page-citation check needs a page per input; the pre-existing plain-float fields (`cost_of_project_stated_total` etc.) carry no page and were left alone to avoid touching `financial_calc.py` and its tests for no benefit the engine needs. |
| `ProjectFinancials.term_loan_amount`, `.loan_tenor_years`, `.loan_moratorium_years` | The amortization schedule needs amount+rate+tenor+moratorium together; none were extracted as a set before (only AIF subvention rates existed). |
| `ProjectFinancials.fund_flow_sources_total`, `.fund_flow_applications_total` | A fund-flow (sources vs. applications) statement is distinct from cost-of-project/means-of-finance (see `fund_flow_gap()`'s docstring) and was never extracted at all. |
| `ProjectFinancials.harvester_assumptions` (`HarvesterAssumptions`: acres/day, operating days, seasons, price/acre, running cost/acre (stated and corrected), fuel litres/price, fuel cost stated, `crew_structure` (list of `CrewRole`), crew wage P&L total) | Section 8 sector-feasibility and fuel/crew reconciliation checks need these; the Finance/Sector agents' existing output is narrative only. Only populated for a harvester-sector DPR (prompt instructs the model to leave it null otherwise) -- a new sector needs its own assumptions model, consistent with `sector_feasibility.py`'s plugin registry. |
| `ProjectFinancials.repayment_table` (`RepaymentTableRow`: year, principal_due, interest_due, each with page) | Needed to compare the DPR's own repayment table against the recomputed amortization schedule; not wired into a check yet (the bridge currently uses the recomputed schedule only -- see "Remaining work"). |
| `YearFinancials.profit_before_tax`, `.tax_paid` | `derive_tax_rate()` needs both verbatim, separately from `net_profit`, to derive (never assume) the effective tax rate. |

`orchestrator.FINANCIAL_EXTRACTION_PROMPT` was extended to instruct the
model: every `{value, page, status}` field must carry a real page or be
explicitly `status: "missing"` with `value: null` -- never `"extracted"`
with a null value, and never a guessed page.

### 3. Wiring behind `CAM_ENGINE_ENABLED` (default off)

- `app/core/config.py`: `Settings.cam_engine_enabled: bool = False`
  (env `CAM_ENGINE_ENABLED`, standard pydantic-settings name mapping, no
  prefix configured).
- `app/services/dpr_agentic/cam_engine_bridge.py` (new): `run_harvester_engine(fin)`
  calls `cam_financial_engine.py`/implicitly exercises the harvester path,
  degrading per-figure (missing inputs for one calculation never block
  another) and returning `(CamEngineOutputs, list[Finding])`. Findings
  reuse `finding_rules.priority_for_calculation`/`compute_dedup_key`
  exactly like `financial_calc.py`'s calculated findings, so they flow
  through the same consolidation pipeline unchanged.
- `orchestrator.extract_calculated_facts()` gained a keyword-only
  `cam_engine_enabled: bool = False` parameter; `False` (the default)
  makes it byte-for-byte identical to before this milestone.
  `analyze()`/`parallel_graph.analyze_parallel()` both thread the flag
  through and set the new `AnalysisResult.cam_engine_outputs` field (an
  opaque dict so the base schema stays free of the engine's own
  dependencies; `cam_adapter.py` revalidates it into `CamEngineOutputs`
  when present).
- Call sites updated to pass `settings.cam_engine_enabled` explicitly:
  `app/api/v1/dpr_analysis.py` (both `analyze()` calls),
  `app/services/dpr_agentic/worker.py` (`run_claimed_analysis`, new
  keyword param), `scripts/dpr_worker.py` (passes `settings.cam_engine_enabled`
  through to it). Every other existing call path keeps the parameter's
  `False` default, so nothing changes until the flag is explicitly set.
- `cam_adapter.py` gained `_engine_derived_fields()`, folding
  `CamEngineOutputs` into `financial_ratios`/`repayment_schedule`/
  `scenario_analysis` when present; empty/default when not (milestone 1/2
  behavior unchanged for a run with the flag off).
- Tests: `tests/test_dpr_cam_engine_bridge.py` (12, including graceful
  degradation and zero-value cases), flag on/off tests added to
  `tests/test_dpr_agentic.py` and `tests/test_dpr_parallel_graph.py`, and
  `tests/test_dpr_cam_snapshot.py` gained a test that the adapter actually
  folds engine outputs in.

### 4. End-to-end run and the resulting fixture

No real Tikamgarh DPR file exists (confirmed again this round -- still
nothing under that name anywhere in the repo or `DPRData/`), so "the full
pipeline end-to-end on the Tikamgarh DPR" cannot mean a real document run.
`scripts/build_tikamgarh_fixture.py` runs the **real** `orchestrator.analyze()`
-- all 10 agent roles, real consolidation, real priority rules, real
`cam_engine_enabled=True` path -- against a `FakeLLM` whose `.extract()`
returns the golden `ProjectFinancials` (same figures as item 1's table) and
whose `.generate()` returns a mix of outputs including a capacity-overreach
finding deliberately restated by 5 of the 10 roles (sector, risk, verifier,
correction, report), to exercise consolidation at a realistic scale, not
just on the calculated findings. This is a labeled simulation of what the
real pipeline produces given a document stating these figures, not a real
end-to-end document run -- that remains blocked on the missing source file.

Result: 6 consolidated findings (down from what would have been 10 raw
narrative duplicates + 4 calculated ones = 14, had consolidation not run),
all with `dedup_key`/`priority`/`finding_class` populated; the capacity
finding's `merged_duplicate_count` is exactly 5, matching the simulated
restatement count.

**All 13 verification checks pass, with zero issues at all** (not just
zero blocking ones -- `verify_snapshot()` returns an empty `issues` list).
Specifically: schema fields present (1); no cross-page cost/funding figures
to contradict each other since neither was populated this round (3, N/A);
source references and page citations are all internally consistent (4-5);
every `CalculationTrace` is either `COMPUTED` with a value or
`NOT_COMPUTABLE` with a reason (6); both scenarios are labeled, described
and exactly one is non-hypothetical (7); no policy statements exist to
misclassify (8); readiness status (`EXPERT_REVIEW_REQUIRED`, since 4 of
the 6 findings are `must_fix`) is consistent with the findings (9); no
subsidy claims exist to misclassify (10); no unresolved placeholders of
either kind (11); at least one document is on record and `generated_at`
isn't in the future (12); no duplicate `dedup_key`s remain post-
consolidation (13).

The fixture is saved at `tests/fixtures/tikamgarh_cam_snapshot.json` and
locked against drift by `tests/test_dpr_tikamgarh_fixture.py` (9 tests) --
this is milestone 4's primary fixture.

### 5. Zero-value truthy-check sweep

Grepped `cam_adapter.py`, `cam_financial_engine.py`, `cam_verification.py`,
`sector_feasibility.py`, `finding_rules.py`, `finding_consolidation.py`,
`financial_calc.py`, `cam_engine_bridge.py`, `financial_schema.py`,
`schemas.py`, `orchestrator.py` and `parallel_graph.py` for `if x:` /
`x if x else` / `if not x:` patterns on anything that could be a legitimate
numeric zero.

**Found and fixed**: `app/services/dpr_agentic/report_render.py:116` --
`page = f" (page {f.evidence_page})" if f.evidence_page else ""` -- the
exact same bug class as the one fixed in `cam_adapter.py` during milestone
3 (page `0` silently dropped via Python's falsy-zero). This is the PDF
renderer, a second, independent occurrence of the same mistake, not a
leftover from the earlier fix. Fixed to `is not None`.
Test added: `test_evidence_page_zero_still_renders_a_page_annotation` in
`tests/test_dpr_report_render.py`.

**Reviewed, not a bug**: `finding_rules.priority_for_calculation`'s
`if stated_value in (None, 0): return "should_fix", ...` -- this
deliberately treats a stated total of exactly zero the same as "no stated
total", to avoid a division-by-zero computing a percentage impact against
it. A zero stated total is itself a degenerate case (dividing any nonzero
discrepancy by zero is undefined), so routing it to the same "can't
compute a percentage, fall back" path is correct, not an oversight -- left
unchanged.

**New zero-value regression tests**, covering legitimate zero inputs that
must not be mistaken for "missing" (all added this round):
- `cam_financial_engine.py`: 0% interest rate, 0-year moratorium, ₹0 net
  profit in a DSCR calculation, a fund-flow gap of exactly zero, a 0%
  derived tax rate -- all five in
  `tests/test_dpr_cam_financial_engine.py`.
- `cam_engine_bridge.py`: a 0% rate / 0-year moratorium loan still produces
  a correct (non-missing) amortization year in
  `tests/test_dpr_cam_engine_bridge.py`.
- `report_render.py`: page 0 above.

No other instance of the bug class was found in the audited files.

### Tests run

```
python3 -m pytest tests/test_dpr_cam_financial_engine.py -q    → 18 passed
python3 -m pytest tests/test_dpr_cam_engine_bridge.py -q        → 12 passed
python3 -m pytest tests/test_dpr_cam_snapshot.py -q             → 8 passed
python3 -m pytest tests/test_dpr_tikamgarh_fixture.py -q        → 9 passed
python3 -m pytest tests/test_dpr_report_render.py -q            → 7 passed
python3 -m pytest tests/test_dpr_agentic.py -q                  → 8 passed
python3 -m pytest tests/test_dpr_parallel_graph.py -q           → 4 passed
python3 -m pytest tests/ -k dpr -q                               → all pass/skip, 0 failed
python3 -m pytest tests/ -q                                      → full repo suite, 0 failed
ruff check app/ tests/ scripts/                                   → clean
```

### Remaining work

- Still not populated by the bridge: `project_cost`/`means_of_finance`
  reconciliation (the engine's `CamEngineOutputs` doesn't carry the raw
  loan/means-of-finance figures themselves, only derived traces --
  extending it to do so is the next incremental step, likely milestone 4
  work since templates will need those fields rendered).
- `repayment_table` (the DPR's own stated repayment table, extracted with
  page references) is defined but not yet compared against the recomputed
  `equal_principal_amortization_schedule()` -- that comparison would be a
  natural addition to `cam_engine_bridge.py`.
- `sector_feasibility.py`'s harvester capacity check is not yet wired into
  `cam_engine_bridge.py`/the findings pipeline (it exists and is tested
  standalone from milestone 2); the fixture's capacity finding above comes
  from the simulated narrative agent output, not this check.
- Dairy/polyhouse: still explicit stubs, by design.
- The balance-sheet/cash-flow gap (milestone 2, disclosed above) remains.
## Status: Milestone 4 (Templates and PDF) — all six pages done

### PDF engine decision

**WeasyPrint** (pure Python, no browser binary, strong CSS Paged Media
support). Rejected Playwright: it needs a full Chromium binary installed
and managed on the server just to print HTML, which is a heavier
operational footprint than this project's "lean FastAPI middleware"
posture warrants for what is, in the end, a print stylesheet problem.
WeasyPrint is deterministic, has no headless-browser process to manage,
and already shares this codebase's existing PDF-reading dependency
(`pymupdf`) for verification in tests. Added to `pyproject.toml`
(`weasyprint>=70,<71`, `jinja2>=3.1,<4` -- jinja2 was previously only an
undeclared transitive dependency, now explicit since it's load-bearing),
relocked via `scripts/lock_dependencies.py`.

### Fonts

Manrope (400/500/600/700/800) and IBM Plex Mono (500) downloaded from
Google Fonts (both SIL Open Font License) and bundled at
`app/templates/cam/fonts/*.ttf`, referenced by local `@font-face` rules in
`app/templates/cam/base.css` -- no network call at render time. Verified
by `test_renders_a_real_pdf_with_no_network_access` and by the templates
containing no `fonts.googleapis.com`/`fonts.gstatic.com` reference at all
(the original design's `<link rel="preconnect">` to Google Fonts was
dropped, not merely left unused).

### Design archive inspection, confirmed (Section 2.1)

Unzipped all six archives (into the scratchpad, not in place, per
untrusted-archive handling) and read each `*.dc.html`. Confirmed: fixed
794×1123px artboard with inline styles, an `<x-dc>` root, `support.js`/
`vendor/react*.js` runtime, `data-props`/`<script type="text/x-dc">` logic
classes -- exactly as the brief describes. Page 1's logic class
(`Main.dc.html`) resolves an `outcome` enum (`"Recommend"` /
`"Recommend with conditions"` / `"Refer to bank"` / `"Not recommended"`)
to a badge/headline/body/color -- **this is loan-approval-recommendation
language** ("Your project looks bankable", "Not recommended") and was
**not** ported as-is: Section 19.20 ("do not claim an official SBI
approval decision") and Section 6.1 ("never imply ... approval
probability") directly forbid it. `cam_render._readiness_copy()`
re-authors this as DPR-readiness-only copy keyed off
`ReadinessStatus` instead (see below) -- the visual structure (colored
badge pill, headline, body paragraph, callout card) is preserved; the
words are not reused.

### What was built

- `app/templates/cam/base.css` -- consolidated stylesheet: `@font-face`
  rules, CSS custom properties for `--brand`/`--accent` (default
  `#1F5E46`/`#9A5408`, matching `Settings.cam_brand_color`/
  `cam_accent_color`, also added this round), `@page { size: A4; margin:
  0; @bottom-center { content: "Page " counter(page) " of " counter(pages);
  } }` for automatic running page numbers across however many physical
  pages a logical page's content spans, and `.cam-page { min-height:
  297mm; break-after: page; }` replacing the design's fixed 794×1123px
  box per Section 14's explicit instruction.
- `app/templates/cam/page1_summary.html.jinja` -- the Page 1 (Summary)
  port. Every `{{...}}`/`<sc-if>` binding and literal bracket placeholder
  (`[Customer full name]`, `[BD name]`, `[Branch name]`, `[DD Mon YYYY]`,
  the hardcoded "7 of 100" score) is replaced by a snapshot-driven value
  or an explicit missing-data state (`"Requires verification"`, `"Not yet
  computable"`, `"Borrower name not yet confirmed"`) -- never invented
  (Section 6.2/19.4).
- `app/templates/cam/report.html.jinja` -- the document wrapper (includes
  each page template; pages 2-6 are TODO comments for now).
- `app/services/dpr_agentic/cam_render.py` -- `build_page1_context()`
  (all display-value computation in plain, unit-tested Python, not Jinja
  logic), `render_cam_report_html()`, `render_cam_report_pdf()`. Jinja2
  autoescape is on; no `|safe` is used anywhere on snapshot data, so an
  LLM-authored finding title containing `<script>` renders as escaped
  text, never executes (tested).
- Section 6.1's readiness-score behavior: `score_mode="status"` (the
  default) shows the colored badge/headline/body only, no numeric score,
  no circular gauge -- the design's own "7 of 100" gauge only appears when
  `score_mode="dimensions"` and `readiness_assessment.total_score` is set,
  showing the real computed value and the four dimension bars.
- "What we checked" boxes (`_checks()`) never claim a check happened that
  didn't: a category with nothing computed yet shows "Not yet checked",
  not a false "no issues found".

### A real layout bug found and fixed during development

First render spilled one logical page across two physical pages with
`.cam-stat`/`.cam-check` cards **sliced mid-box** -- a card's title landed
on page 1 with its status text pushed to page 2, rendering as a near-empty
bordered box. Fixed with `break-inside: avoid` on `.cam-callout`,
`.cam-stat` and `.cam-check`. Caught by visually rendering the PDF to PNG
and inspecting it (not just checking `pdf_bytes[:5] == b"%PDF-"`), then
locked in by `test_print_layout_does_not_slice_a_card_across_a_page_break`.
Spilling onto a second physical page at all is acceptable per Section 14
("do not force a section to remain on one page if content would
overflow"); slicing a card is not, and is what the test guards.

### Page 2 (Fix before you apply)

Ported from `cam-design-reference/Fix-html.zip` (`Fix.dc.html`). This page
needed no recommendation-language rewrite -- it is already findings-first,
priority-labeled content (Must fix / Should fix / Good to fix), which is
exactly `VerifiedFinding.priority` as already computed upstream by
`finding_rules.py` and consolidated by `finding_consolidation.py`. Nothing
on this page is decided in the template: `build_page2_context()` only
groups `snapshot.findings` by their own `priority` field and formats each
one's page reference.

- `app/templates/cam/page2_fix_before_you_apply.html.jinja` -- must-fix
  items render as full-width rows (title, explanation + suggested action,
  page reference); should-fix/good-to-fix render as a 2-column card grid.
  Each priority group shows an explicit "No {priority} issues were found"
  note rather than an empty section when there are none.
- `app/services/dpr_agentic/cam_render.py` -- `build_page2_context()`,
  `_finding_ref()`. The page-reference label prefers the finding's own
  `source_pages` (e.g. "p.8", or "p.8, p.22" for more than one); when a
  finding has no page evidence at all, it falls back to the finding's
  `category` (title-cased) rather than fabricating a page number --
  distinguishable in the rendered report ("Dscr", "Projections") from a
  real citation.
- `app/templates/cam/base.css` -- new shared classes for this page's
  layout: `.cam-pill`/`.cam-pill--{must,should,good}-fix` (the colored
  priority pill), `.cam-priority-group`, `.cam-finding-row` (must-fix full
  rows), `.cam-finding-card` (should/good-to-fix grid cards),
  `.cam-empty-note`. All carry `break-inside: avoid`, same reasoning as
  the Page 1 fix.
- `report.html.jinja` now includes both pages; `render_cam_report_html()`
  passes both `ctx` (page 1) and `ctx2` (page 2) into one shared template
  render.

Verified by rendering the Tikamgarh fixture to PDF and inspecting each
page as a PNG: the four must-fix items, one should-fix item and one
good-to-fix item (totals the fixture's own 6 consolidated findings) render
with their real explanations and real page numbers (p.8, p.8, p.12, p.3),
with no invented text.

### Tests run

```
python3 -m pytest tests/test_dpr_cam_render.py tests/test_dpr_cam_render_page2.py -q
  → 27 passed
python3 -m pytest tests/ -q                            → full repo suite, 0 failed
ruff check app/ tests/                                  → clean
ruff format --check app/ tests/                         → clean
```

Covers (Section 18, for Page 2): findings grouped by their own `priority`
field (not re-decided in the template), page-reference precedence
(`source_pages` over category fallback), no invented must-fix count when
there are none, explicit empty-group notes, no unresolved placeholders, no
canvas runtime markup, and autoescape blocking markup injection from an
LLM-authored finding title.

### A real gap found and fixed while building Page 3: fabricated "recomputed value"

Page 3's "corrections" table needs a real DPR-stated figure and a real
recomputed figure side by side. Checking where `VerifiedFinding.
recomputed_value` actually came from surfaced a bug: `cam_adapter.
_map_finding()` set `recomputed_value=f.title` for any "calculated"
finding -- a title string standing in as if it were a number. Fixed by
adding two additive fields to the base `Finding` schema
(`dpr_stated_value`/`recomputed_display_value`, both `None` by default),
set only by `cam_engine_bridge.py`'s reconciliation findings that
genuinely have a stated/recomputed pair (fuel cost, crew wages) -- the
fund-flow finding deliberately leaves both unset, since sources vs.
applications is a two-sided gap, not a "should be" correction, and
labeling it that way would misleadingly imply one side is "correct."
`cam_adapter.py` now passes these straight through. Regenerated the
Tikamgarh fixture (`scripts/build_tikamgarh_fixture.py`) so it carries the
real values ("7.2 lakh" / "9.12 lakh" for fuel, "2.4 lakh" / "3.3 lakh"
for crew wages) instead of the old placeholder strings. Covered by
`test_finding_value_pair_passes_through_only_when_the_pipeline_set_one`
(`tests/test_dpr_cam_snapshot.py`) and two assertions added to
`tests/test_dpr_cam_engine_bridge.py`.

### Page 3 (Project, cost and corrections)

Ported from `cam-design-reference/Page 2 — Project, cost and DPR
changes-html.zip` (`Project.dc.html`). No approval language to re-author
here. Two fields in the design (crops served, operations start date) have
no home in `ProjectProfile` and were dropped rather than invented; the
"Your project" tiles instead show what the model actually carries
(activity/location/sector/purpose).

- `app/templates/cam/page3_project_cost.html.jinja` -- project tiles, an
  itemized cost table (falls back to an explicit "No itemized cost
  breakdown is available" note when `project_cost.components` is empty --
  true today for the Tikamgarh fixture, a pre-existing milestone-2b gap,
  not a Page 3 defect), a funding split bar (same empty-state treatment
  when `means_of_finance` has no loan/promoter split), and a generic
  corrections table driven by any finding carrying both
  `dpr_reported_value` and `recomputed_value`.
- `app/services/dpr_agentic/cam_render.py` -- `build_page3_context()`.
  The corrections table is intentionally generic (any finding with a real
  value pair appears in it) rather than hardcoded to the harvester sector,
  so it needs no template change when a future sector's engine starts
  producing its own stated/recomputed pairs.
- `app/templates/cam/base.css` -- `.cam-tile`, `.cam-funding-bar`,
  `.cam-funding-legend*`.
- Verified by rendering the Tikamgarh fixture to PDF and inspecting the
  page as a PNG: tiles, the explicit empty-state notes, and the two
  corrections rows (fuel cost, crew wages) all render on one physical
  page with real figures, no placeholders.

### Tests run

```
python3 -m pytest tests/test_dpr_cam_render_page3.py -q   → 9 passed
python3 -m pytest tests/ -q                                 → 529 passed, 63 skipped
ruff check app/ tests/                                       → clean
ruff format --check app/ tests/                               → clean
```

### Page 4 (Repayment)

Ported from `cam-design-reference/Page 3 — Repayment-html.zip`
(`Repayment.dc.html`). The original design's bar chart plots five
acreage-specific points -- a realistic harvest-window range (300-600
acres) and a DSCR=1.0 break-even acreage (~910 acres) alongside the two
1,200-acre scenarios. The first two come from `sector_feasibility.py`'s
harvester check (implemented and tested, but, per the Page 3 note above,
still not called from `cam_engine_bridge.py`) and from
`debt_service_threshold_breakeven()` (implemented in
`cam_financial_engine.py` but likewise never called from the bridge) --
neither is real data yet, so instead of fabricating three bars, Page 4
renders only the two scenarios the engine actually computes
(`scenario_analysis`: "As stated in the DPR" vs "Corrected fuel and crew
wage costs", both at 1,200 acres), each DSCR value next to the other, with
the hypothetical one explicitly labeled "Illustrative scenario, not a DPR
fact". The design's "With AIF subvention" repayment row has no
corresponding field anywhere in CAMSnapshot and is dropped, not invented.

- Surfaced a real value that existed in the engine but was dropped before
  reaching the snapshot: `cam_engine_bridge.CamEngineOutputs.dscr_pat_dep`
  (the DPR's own PAT+Depreciation-only convention, ~1.58x for the golden
  case) was computed but never folded into `CAMSnapshot`. Added
  `FinancialRatios.dscr_pat_dep_only` (additive) and wired it in
  `cam_adapter._engine_derived_fields()` -- this is what lets Page 4 show
  the real "Your DPR states (Year 1): 1.58x" vs "Standard bank formula
  (Year 1): 1.97x" comparison instead of omitting it. Regenerated the
  Tikamgarh fixture; confirmed `1.58` against the design reference.
- `app/templates/cam/page4_repayment.html.jinja`, `build_page4_context()`
  -- DSCR convention cards, the two-scenario comparison, and a Year-1
  repayment table (principal/interest/total only -- no later-years row,
  since the pipeline doesn't produce a multi-year schedule yet, a
  pre-existing disclosed gap from milestone 2).
- `base.css` -- `.cam-dscr-card`, `.cam-scenario-row*`.

### Tests run

```
python3 -m pytest tests/test_dpr_cam_render_page4.py -q   → 8 passed
python3 -m pytest tests/ -q                                 → 537 passed, 63 skipped
ruff check app/ tests/                                       → clean
ruff format --check app/ tests/                               → clean
```

### Page 5 (Security and obligations)

Ported from `cam-design-reference/Page 4 — Security and
obligations-html.zip` (`Security.dc.html`). This page is mostly
sector-specific operational/legal narrative (security items, disbursement
steps, post-delivery obligations) that CAMSnapshot has no model for at
all -- no collateral, guarantor or disbursement-step fields exist
anywhere in the pipeline. Two deliberate honesty choices:

- The design's "Land or other collateral" / "Guarantor" rows read "Not
  stated in your DPR" -- a claim that the document was checked and the
  item confirmed absent. This pipeline never checks for collateral or a
  guarantor at all, so re-labeled both as **"Not assessed by this
  analysis"**, which is true regardless of what the actual DPR contains
  (Section 19.3: never claim to have verified something that wasn't).
  Same reasoning for "SBI" being replaced by the generic "your lender" /
  "the bank" throughout -- CAMSnapshot never records which bank, so
  naming a specific one would be invented specificity.
- The post-delivery obligations checklist (geo-tag photo, pay
  instalments, service agreement, insurance, no reselling) is generic
  equipment-loan advisory content, not something extracted from any
  document. It is gated to the harvester sector this pipeline actually
  models (via `project_profile.machinery_equipment`/`business_subsector`)
  and shows an explicit "not yet available for this sector" note for
  anything else, rather than reused wholesale for every sector.

Real figures (project cost, requested loan, promoter contribution) are
used where available for the hypothecation value and disbursement-step
amounts, with the existing "Requires verification" / generic-no-number
fallback when they are not (true for the golden fixture today, the same
pre-existing project_cost/means_of_finance gap).

- `app/templates/cam/page5_security.html.jinja`, `build_page5_context()`.
- `base.css` -- `.cam-steps-wrap`/`.cam-step*`, `.cam-note-tile`,
  `.cam-checklist-row*`.

**A real layout bug found and fixed during development:** the first
attempt at the disbursement-step connector line used a CSS `::before`
pseudo-element directly on the `.cam-steps` grid container (mirroring a
common web pattern). WeasyPrint rendered this not as a thin decorative
line but as a large solid grey rectangle filling a grid cell -- the
pseudo-element was evidently being auto-placed into the grid rather than
treated as an out-of-flow absolutely-positioned box the way a browser
would. Fixed by reverting to the original design's actual structure: a
real sibling `<div>` (`.cam-steps-wrap__line`) absolutely positioned
inside a non-grid wrapper, exactly as `Security.dc.html` itself did it.
Caught by visually rendering the PDF to PNG and inspecting it, same
discipline as the Page 1 and Page 3 bugs. A side benefit: the page now
fits in one fewer physical page than before the fix.

### Tests run

```
python3 -m pytest tests/test_dpr_cam_render_page5.py -q   → 10 passed
python3 -m pytest tests/ -q                                 → 547 passed, 63 skipped
ruff check app/ tests/                                       → clean
ruff format --check app/ tests/                               → clean
```

### Page 6 (Benefits and next steps) -- final page of milestone 4

Ported from `cam-design-reference/Page 5 — Benefits and next
steps-html.zip` (`NextSteps.dc.html`). Like Pages 3-5, several of the
original design's figures have no CAMSnapshot source: a specific AIF
interest-subvention rupee amount, a credit-guarantee card, "23 of 26
pages received" (no "expected page count" field exists), and "0.45x at
600 acres" (the same acreage-scenario gap noted on Page 4). Rather than
reuse them, this page builds:

- One benefit card from `readiness_assessment.subsidy_benefits` (same
  pattern as Page 1's subsidy card) -- real scheme name and amount when
  verified, an honest "None identified" otherwise.
- A generic eligibility checklist from data every snapshot actually
  carries: promoter contribution present/absent, the real must-fix
  finding count, and `outstanding_clarifications_count` -- generalizes to
  any sector without template changes, unlike the original's
  harvester/AIF-specific rows.
- Action items generated from the same real counts (e.g. "Revise your
  DPR to fix the 4 must-fix item(s) on page 2" using the fixture's actual
  count, not a hardcoded "six").
- "SBI" and "[Branch name]" replaced by "your lender"/"your lender's
  branch" -- CAMSnapshot never records which bank is involved, and Page
  5 made the same choice for the same reason (Section 19.3/19.20).

- `app/templates/cam/page6_next_steps.html.jinja`, `build_page6_context()`.
  Reuses `.cam-steps-wrap`/`.cam-step*` from the Page 5 fix for the
  "What happens next" 4-step flow (confirmed the connector line renders
  correctly here too).
- `report.html.jinja` now includes all six pages -- the six-page CAM
  report template is complete end to end.

### Tests run

```
python3 -m pytest tests/test_dpr_cam_render_page6.py -q   → 12 passed
python3 -m pytest tests/ -q                                 → 559 passed, 63 skipped
ruff check app/ tests/                                       → clean
ruff format --check app/ tests/                               → clean
```

Includes an end-to-end sanity check
(`test_full_six_page_report_renders_with_no_unresolved_placeholders`)
confirming all six pages render together with no unresolved Jinja
placeholders and every page's "Page N of 6" label present.

### Remaining work for milestone 4 and beyond

- The project_cost/means_of_finance gap (nothing populates these two
  models yet) still means Pages 1, 3, 5 and 6 show their "Requires
  verification"/empty-state fallbacks even on the golden fixture --
  closing it (wiring raw loan/means-of-finance figures into
  `CamEngineOutputs`) would populate several pages more fully at once.
- `sector_feasibility.py` and `debt_service_threshold_breakeven()` are
  implemented but not called from `cam_engine_bridge.py` -- wiring them
  in would unlock the acreage-range/break-even-acreage content that
  Pages 3 and 4 currently and correctly omit.
- Dealer-quotation/document-completeness tracking (Page 6's dropped "23
  of 26 pages" row) and an AIF-subvention calculator (Page 6's dropped
  benefit card) are both new scope, not implemented anywhere yet.
- Detailed CAM annexures (Section 12, milestone 7) -- not started.
- `Settings.cam_brand_color`/`cam_accent_color` exist but nothing calls
  `render_cam_report_pdf()` with them yet -- milestone 5 work (API
  wiring).
- Milestone 5 (persistence/APIs/authorization, including the
  `report_store.py` overwrite-on-conflict issue), milestone 6 (frontend
  PDF download link), and milestone 7 (annexures) are all still open --
  see the top-level execution plan.
- Wiring `sector_feasibility.assess_sector_feasibility()` and
  `cam_financial_engine.debt_service_threshold_breakeven()` into
  `cam_engine_bridge.run_harvester_engine()` would close two real gaps at
  once: Page 3's "Acres per year"/"Year-1 capacity" correction rows, and
  Page 4's realistic-acreage-range and break-even-acreage bars.
- The still-open project_cost/means_of_finance gap (nothing populates
  these two models yet, so Page 3's cost table and funding bar show their
  empty states even on the golden fixture) -- wiring the raw loan/means-
  of-finance figures into `CamEngineOutputs`/`cam_adapter.py` would close
  this for the harvester case.
- `sector_feasibility.py` (acres/day feasibility) is implemented and
  tested but still not called from `cam_engine_bridge.run_harvester_
  engine()` -- its result never reaches a Finding or `CamEngineOutputs`,
  so the design's "Acres per year 1,200 -> 300-600" and "Year-1 capacity
  100% -> 50-60%" correction rows have no real data source yet and
  correctly do not appear in Page 3's corrections table. Wiring this in
  is the natural next increment for that table, not a Page 3 template
  change.
- The `project_cost`/`means_of_finance` gap from milestone 2b means Page 1
  currently shows "Requires verification" for loan/promoter amounts even
  on the populated Tikamgarh fixture -- closing that gap (wiring the raw
  loan/means-of-finance figures into `CamEngineOutputs`) will make Page 1
  fully populated, not just structurally correct.
- `Settings.cam_brand_color`/`cam_accent_color` exist but nothing calls
  `render_cam_report_pdf()` with them yet -- wiring that through an API
  endpoint is milestone 5 work.
- Detailed CAM annexures (Section 12, milestone 7) -- not started.
- A `weasyprint` runtime warning ("Using fontTools instead of
  HarfBuzz-Subset... will be unsupported in future versions") appears in
  test output -- informational only (a missing optional system library,
  `libharfbuzz-subset`), does not affect correctness; worth installing on
  the deployment host before upgrading WeasyPrint past whatever version
  drops the fontTools fallback.
- Milestones 5-7 per Section 3A: persistence/APIs/authorization, frontend,
  annexures. Not started.

## Milestone 4b: closed the two flagged wiring gaps, fixed report_store.py (2026-11-04)

Before starting milestone 5, closed the two gaps flagged at the end of
milestone 4, plus a `report_store.py` correctness fix the user asked to
land first as its own commit.

### report_store.py: never overwrite a stored report (separate commit)

`store_report()` used `ON CONFLICT (run_id) DO UPDATE`, silently replacing
any previously stored report for that run_id -- a violation of "never
overwrite a previous report version." Confirmed this module is used in
production (`scripts/dpr_worker.py` calls it after every claimed run).
Investigated reachability: in the current job lifecycle
(`jobs.claim()`/`jobs.finish()`), a COMPLETED run can never be reclaimed,
so the conflict path is not reachable through today's worker loop -- but
it would become reachable the moment any retry/reset tooling is added, or
if two workers ever raced on the same claim. Fixed to
`ON CONFLICT (run_id) DO NOTHING`, raising a new `ReportAlreadyStoredError`
if a row already existed, instead of silently overwriting or silently
dropping either side. Verified against a real throwaway Postgres
(`docker run postgres:16-alpine`), not just mocked: `store_report` round-
trips correctly, a second store for the same run_id raises, and the
original report survives untouched. New test file
`tests/test_dpr_report_store.py` (2 tests, real-Postgres-gated like the
rest of the DPR persistence tests). Also confirmed, by reverting the fix
locally and rerunning, that a pre-existing unrelated failure
(`test_dpr_log_safety.py::test_process_one_sets_current_run_id_for_the_
whole_call_chain`, an `AttributeError` inside `process_one` unrelated to
report storage) exists independently of this change -- not introduced by
it, not fixed by it, out of scope here.

### Gap 1: project_cost / means_of_finance was a mapping gap, not a missing-extraction gap

Confirmed before changing anything: `financial_schema.ProjectFinancials`
already carries `cost_of_project_components`, `cost_of_project_stated_
total`, `means_of_finance_components`, `means_of_finance_stated_total`,
and `financial_calc.py` already computes reconciliation checks from them
(`cost_of_project_matches_components`, `means_of_finance_matches_
components`, `means_of_finance_balances_cost`) -- all pre-dating this
session. The gap was purely that `extract_calculated_facts()` built this
`ProjectFinancials` object, ran those checks, and then discarded it
without ever reaching `CAMSnapshot.project_cost`/`means_of_finance`. No
prompt or extraction-schema change was needed.

Closed by extending `cam_engine_bridge.CamEngineOutputs` with
`project_cost_components`, `project_cost_stated_total`,
`cost_reconciliation`, `means_of_finance_components`, `means_of_finance_
stated_total`, `means_of_finance_reconciliation`, `funding_balances_cost`,
`requested_loan`, `promoter_contribution`, `proposed_tenure_months` --
populated unconditionally by a new `_cost_and_finance_fields()` (not
harvester-specific; runs for any sector) -- and a new `_classify_finance_
label()` keyword heuristic that reads each means-of-finance line's own
extracted label text (e.g. "SBI term loan" -> loan, "Promoter
contribution" -> promoter) to pick out `requested_loan`/`promoter_
contribution` -- never guessing an amount, only which already-extracted
line is which. `cam_adapter.py` gained `_project_cost_from_engine()`/
`_means_of_finance_from_engine()` to build the real `ProjectCost`/
`MeansOfFinance` snapshot models from these outputs. Populated the
Tikamgarh fixture's cost/finance components (₹30.00 L harvester cost =
₹22.50 L SBI term loan + ₹7.50 L promoter contribution -- the same
figures already used throughout the golden case, now also structured as
line items) and regenerated it via `scripts/build_tikamgarh_fixture.py`.

### Gap 2: sector_feasibility.py and the debt-service threshold, wired in

`sector_feasibility.assess_sector_feasibility("harvester", ...)` and
`cam_financial_engine.debt_service_threshold_breakeven()` were both
implemented and unit-tested but never called from `cam_engine_bridge.
run_harvester_engine()`. Wired both in:

- `feasible_acres_per_year`: computed from `acres_per_day x operating_
  days_per_season x seasons_per_year` (10 x 30 x 2 = 600 for the golden
  case). When this differs materially from the DPR's stated `acres_per_
  year` (1,200), emits a calculated Finding with a real `dpr_stated_value`/
  `recomputed_display_value` pair ("1200 acres/year" / "600 acres/year").
  Deliberately given `category="assumption"` -- the same category the
  fixture's narrative sector-feasibility finding uses -- so `finding_
  consolidation.consolidate()` merges the two into one finding instead of
  showing two near-identical entries (calculated wins on provenance rank,
  so the merged finding carries the precise computed figure, not the
  narrative's vaguer "300-600 acres" range).
- `breakeven_debt_service_threshold`: computed with the same price/
  variable-cost/depreciation/interest inputs already used for the other
  two break-even types, plus the already-computed tax rate and Year-1
  principal/interest due. For the golden case this computes to **563
  acres/year** -- not the design reference's own 910-acre figure. This is
  reported as-is: the design reference's 910 is not reproducible from this
  engine's formulas (likely a different convention or a rounding path in
  the original mockup), and Section 19.3 forbids forcing a computed result
  to match a number that doesn't derive from the stated inputs. Page 4 now
  shows "Break-even threshold (DSCR = 1.0, standard formula): 563
  acres/year" instead of omitting it.
- Page 3's corrections table now shows the acreage row with real figures
  (previously had no real value pair to show); Page 1's "Funding plan"
  check now reads "No issues found" (previously "Not yet checked", since
  `means_of_finance.funding_gap` was never populated); Page 1's loan/
  promoter/subsidy stat cards are fully populated instead of "Requires
  verification".

### End-to-end run: live pipeline, flag on

Ran `scripts/build_tikamgarh_fixture.py` (the real `orchestrator.analyze()`
against a FakeLLM returning the golden figures, `cam_engine_enabled=True`
-- same "labeled simulation" caveat as before: no real Tikamgarh DPR file
exists). Rendered all six pages to PDF and PNG at
`docs/review/tikamgarh/` (`tikamgarh_cam_report.pdf` plus `page_01.png`
through `page_09.png` -- six logical pages span 9 physical pages: Page 1 =
physical 1-2, Page 2 = physical 3-4, Page 3 = physical 5, Page 4 =
physical 6, Page 5 = physical 7-8, Page 6 = physical 9).

**Missing-data marker count per page, before vs. after** (counting the
template's own explicit missing-state phrases -- "Requires verification",
"Not yet checked", "None identified", "Not assessed by this analysis",
"...not yet available", etc. -- in the rendered HTML; "before" is the
fixture as committed at `8d7e564`, before this round):

| Page | Before | After |
|---|---|---|
| 1 (Summary) | 6 | 3 |
| 2 (Fix before you apply) | 0 | 1 |
| 3 (Project, cost and corrections) | 4 | 1 |
| 4 (Repayment) | 0 | 0 |
| 5 (Security and obligations) | 4 | 2 |
| 6 (Benefits and next steps) | 3 | 2 |

Page 2's count went from 0 to 1, which looks like a regression but isn't:
the acreage finding was previously a lower-confidence narrative "should
fix" item; now that it's backed by a real calculation it's correctly
reclassified as "must fix," leaving the "should fix" group empty, which
renders an explicit (and correct) "No should-fix issues were found" note
-- one more honest empty-state message, not missing data.

**Nine §18 golden values, from the live pipeline (not the standalone
engine)** -- all pass:

| Value | Expected | Got | Result |
|---|---|---|---|
| DSCR, standard formula | 1.97x | 1.97x | PASS |
| DSCR, DPR's own convention | 1.58x | 1.58x | PASS |
| Year-1 total debt service | ₹5.24 L | ₹5.24 L | PASS |
| Fuel cost recomputed | ₹9.12 L | ₹9.12 L | PASS |
| Crew wage recomputed | ₹3.30 L | ₹3.3 L | PASS |
| Break-even (accounting) | 726 acres | 726 | PASS |
| Means-of-finance split | 75/25 | 22.50 L / 7.50 L (75%/25%) | PASS |
| Fund-flow gap | ₹40.29 L | ₹40.29 L (in finding explanation) | PASS |
| Consolidated findings count | <=13 | 6 | PASS |

**13 Section-13 verification checks**: `verify_snapshot()` on the
regenerated fixture reports `is_publishable=True`, `issues=[]` -- all 13
checks pass, same clean result as before this round (the new fields don't
introduce any new verification failure mode).

### Tests run

```
python3 -m pytest tests/test_dpr_report_store.py -v  (real Postgres)  -> 2 passed
python3 -m pytest tests/test_dpr_cam_engine_bridge.py -q               -> 16 passed
python3 -m pytest tests/ -q                                            -> all pass
                                                                           (except the
                                                                           pre-existing,
                                                                           unrelated
                                                                           log-safety
                                                                           failure noted
                                                                           above, confirmed
                                                                           independent of
                                                                           every change in
                                                                           this round)
ruff check / ruff format --check                                       -> clean
```

### Remaining work

- Page 3's corrections table still doesn't show a "realistic
  harvest-window range" (the design's 300-600 acres) -- that range is a
  judgment call baked into the design reference, not a deterministic
  plugin output; `feasible_acres_per_year` (600, the theoretical maximum)
  is shown instead, which is the honest reproducible figure.
- `Settings.cam_brand_color`/`cam_accent_color` still unwired to an API
  call site -- milestone 5 work.
- Detailed CAM annexures (Section 12, milestone 7) -- not started.
- Milestones 5-7 otherwise unchanged from the prior status.

## Milestone 4c: per-scenario break-even, the 13-issue audit, and ReportAlreadyStoredError's blast radius (2026-11-04)

Three follow-ups requested before starting milestone 5.

### Per-scenario break-even (Page 4)

Previously all three break-even types (accounting, operating cash-flow,
debt-service threshold) were computed once, always using
`running_cost_per_acre_corrected` (900/acre) -- `running_cost_per_acre_
stated` (750/acre, the DPR's own figure) was extracted into
`HarvesterAssumptions` back in milestone 2b but never read anywhere.
Added `cam_engine_bridge._breakeven_scenario()` and a new
`BreakEvenScenarioOutput` model; `run_harvester_engine()` now computes
all three break-even types **twice**, once per cost assumption, keyed by
the same `scenario_id`s as the DSCR scenarios (`dpr_as_written` /
`corrected_costs`) so a reader can line up a scenario's DSCR with its own
break-even. `cam_snapshot.ScenarioAnalysis` gained the three break-even
trace fields; `cam_adapter.py` merges `CamEngineOutputs.breakeven_by_
scenario` into each `ScenarioAnalysis` by matching `scenario_id`. The
singular `FinancialRatios.breakeven_*` fields are kept, mirroring the
`corrected_costs` scenario, for Page 1/3's existing use of them --
nothing there needed to change.

Page 4 now shows a table ("Using your DPR's costs" / "If flagged fuel
and crew corrections are confirmed", the latter labeled "Illustrative
scenario, not a DPR fact") with DSCR and all three break-even figures per
scenario, instead of the single-scenario line from milestone 4b. Full
`CalculationTrace`s (formula + every input) already exist on
`ScenarioAnalysis` for each of these values; Annexure B (Section 12,
milestone 7, not started) will read directly from there once built --
nothing further needs to change on this page when it is.

**Posting both threshold values and their input sets here, as asked:**

| Scenario | Variable cost/acre | Break-even (accounting) | Break-even (cash) | Break-even (DSCR=1.0) |
|---|---|---|---|---|
| Using your DPR's costs (`dpr_as_written`) | ₹750 | **622 acres/year** | 193 acres/year | **483 acres/year** |
| If flagged corrections are confirmed (`corrected_costs`) | ₹900 | **726 acres/year** | 226 acres/year | **563 acres/year** |

Both accounting break-even values are a strong cross-check: 622 -> 726
exactly matches the design reference's own "Break-even acres: 622 ->
726" correction (Page 2/3 of the design). Neither debt-service-threshold
value (483 / 563) matches the design reference's own 910-acre figure --
reported as computed, not forced to match, since 910 is not reproducible
from this engine's formulas with any input combination tried (see
milestone 4b's note on the same point).

Shared inputs for every cell above (Year 1): price/unit ₹1,800/acre,
depreciation ₹4.50 L, interest on term loan ₹2.03 L, principal due ₹3.21
L, interest due ₹2.03 L, tax rate 11.71% (derived from tax paid ₹0.50 L /
PBT ₹4.27 L).

Tests: `tests/test_dpr_cam_render_page4.py::test_scenarios_show_
breakeven_per_scenario_under_its_own_cost_assumption` asserts all six
values above against the live pipeline's fixture output.

### The 13-issue audit

Reconstructed the 13 distinct issues embedded in the design reference
(`cam-design-reference/`): Page 2's findings list has 12 (6 must-fix, 4
should-fix, 2 good-to-fix); Page 3's corrections table adds one more not
listed on Page 2 at all (running cost per acre, 750 -> 900) for 13 total.
Mapped each to where it lands in the current pipeline's output on the
Tikamgarh golden fixture:

| # | Issue (design reference) | Final destination | Dedup key |
|---|---|---|---|
| 1 | Acres per year are not achievable | **F0005** (merged, x6) | `assumption\|claimed annual acreage exceeds plausible machine throughput\|4` |
| 2 | Fuel cost is miscalculated | **F0002** | `financial_calculation\|fuel cost does not match acres x litres/acre x price\|8` |
| 3 | Crew wages are under-budgeted | **F0003** | `financial_calculation\|crew wages understated versus the staffing plan\|8` |
| 4 | Full capacity assumed from Year 1 | **Not modeled** -- no agent or calculated check in this pipeline produces a ramp-up/utilization-curve finding | n/a |
| 5 | Dealer quotation and pages 24-26 missing | **F0006** | `missing_document\|dealer quotation missing for the harvester\|3` |
| 6 | One machine, no breakdown cover | **Not modeled** -- an equipment-redundancy risk observation; no agent in this fixture's simulated run produces it | n/a |
| 7 | Break-even uses the wrong cost | **Computed, not a Finding** -- `ScenarioAnalysis.breakeven_accounting` carries the real 622/726 values (see above), but no code path turns a break-even *value* into a *Finding* the way fuel/crew do | n/a |
| 8 | Booking relies on an app | **Not modeled** -- a marketing/channel-risk observation, no corresponding check | n/a |
| 9 | Machine idle about 10 months | **Not modeled** -- a utilization/seasonality observation, no corresponding check | n/a |
| 10 | Non-standard repayment formula (DSCR table) | **F0001** | `dscr\|dscr year 1: 1.97\|none` |
| 11 | Fund flow does not balance | **F0004** | `financial_calculation\|fund flow sources do not match applications\|12` |
| 12 | Off-season upkeep not costed | **Not modeled** -- a cost-completeness observation, no corresponding check | n/a |
| 13 | Running cost per acre (750 -> 900) | **Computed, not a Finding** -- same situation as #7: it drives the per-scenario break-even numbers above but has no dedicated reconciliation Finding the way fuel/crew do | n/a |

**Confirmed: no two distinct issues were merged.** The only merge in the
fixture's output is F0005, combining 6 raw findings that are all
restatements of the *same* issue (#1) -- 5 narrative duplicates
(`CAPACITY_FINDING`, deliberately returned by the fixture's fake sector/
risk/verifier/correction/report agents to exercise consolidation on a
realistic case) plus the one calculated finding `cam_engine_bridge.py`
now emits for the same acres-per-year mismatch. Checked each pair's
explanation text against its sibling: both describe the identical fact
(10 acres/day x 30 days x 2 seasons vs. the DPR's 1,200-acre plan), same
category (`assumption`), same page (4) -- a correct merge, not a
coincidental dedup-key collision between different issues. Every other
surviving finding (F0001-F0004, F0006) has a dedup key that was only ever
produced once in this run, so no merge opportunity existed for it either
way.

**Net honest accounting**: 6 of the 13 original issues are represented in
the current pipeline's output (merged correctly where they're genuinely
the same issue); 7 are absent because nothing in this pipeline --
calculated check or the fixture's simulated narrative agents -- currently
produces them. This is not a consolidation bug: the "≤13 findings"
Section 18 acceptance criterion is an upper bound on count after
consolidation (6 ≤ 13, satisfied), not a claim that every real-world
issue a human reviewer would find is represented. Issues #7 and #13 are
the most actionable gap to close next, since the underlying values are
already computed (break-even, running cost per acre) -- they just need a
`_calculated_finding()` call turning a mismatch into a Finding, the same
pattern fuel/crew/fund-flow/acres already follow. Issues #4, #6, #8, #9,
#12 need a real sector/risk agent (or a richer fake one) producing
narrative findings this simulation doesn't attempt to replicate.

### ReportAlreadyStoredError: confirmed terminal, not retried; test added

Traced the exact mechanics rather than assuming: `scripts/dpr_worker.py`'s
`process_one()` catches any exception from `run_claimed_analysis()`
generically, logs it, and moves on -- it never retries or resubmits.
`run_claimed_analysis()`'s own except-block calls `jobs.finish(...,
success=False, error_code=type(exc).__name__)` before re-raising, which
sets the run's status to `FAILED`. `jobs.claim()`'s `WHERE` clause only
ever claims a row with `status='QUEUED'` or `(status='RUNNING' AND
lease_until < now())` -- `FAILED` matches neither, so a failed run can
never be reclaimed by any future `poll_once()` cycle. This holds
regardless of which exception caused the failure; `ReportAlreadyStoredError`
is handled exactly the same as any other.

Traced the realistic scenario that would actually raise it: worker A's
lease expires while it is still (slowly, not dead) processing; worker B
reclaims the same run_id and reprocesses. Worker A finishes first and
calls `store_report()` successfully, then calls `finish()` with its now-
stale lease token -- a no-op, since `finish()`'s own `WHERE` clause
requires the *current* `lease_token`, which worker B's reclaim already
overwrote. The run stays `RUNNING` under worker B's claim. Worker B
eventually finishes its redundant run and calls `store_report()` too --
raises `ReportAlreadyStoredError` -- and *its* `finish(success=False)`
call succeeds (its token still matches), flipping the run to `FAILED`.
Net effect: the run ends `FAILED` even though a valid report was
successfully stored underneath it by worker A. This is a pre-existing,
narrow rough edge in the lease-reclaim race (not introduced or fixed by
this change, not asked to be fixed now) worth flagging: an operator
reading job status alone would see `FAILED` for a run that actually has a
good stored report. `get_report()` still returns the correct (first)
report regardless.

Added `tests/test_dpr_report_store.py::test_report_already_stored_leaves_
the_run_terminal_and_unreclaimable`, which reproduces this exact sequence
against a real Postgres (not mocked) using `claim(..., lease_seconds=0)`
to force an immediate reclaim, and asserts: worker A's `finish()` call is
a no-op (`False`), worker B's `store_report()` raises, worker B's
`finish(success=False)` succeeds (`True`), the run can never be
`claim()`-ed again, and the original (worker A's) report is exactly what
`get_report()` returns.

### Tests run

```
python3 -m pytest tests/test_dpr_report_store.py -v  (real Postgres)  -> 3 passed
python3 -m pytest tests/test_dpr_cam_engine_bridge.py -q               -> 16 passed
python3 -m pytest tests/test_dpr_cam_render_page4.py -q                -> 8 passed
python3 -m pytest tests/ -q                                            -> all pass
                                                                           (except the
                                                                           same
                                                                           pre-existing,
                                                                           unrelated
                                                                           log-safety
                                                                           failure)
ruff check / ruff format --check                                       -> clean
```

### Remaining work

- Issues #7 (break-even uses the wrong cost) and #13 (running cost per
  acre) have real computed values but no dedicated Finding yet -- the
  natural next increment for the 13-issue gap, following the existing
  `_calculated_finding()` pattern.
- The `FAILED`-despite-a-valid-report lease-race edge case noted above is
  unresolved (and very narrow -- requires a lease expiring under a worker
  that is slow but not actually dead). Worth a follow-up if operators ever
  see it in practice; not blocking milestone 5.
- Replacing `ReportAlreadyStoredError`'s raise with append-only versioning
  is milestone 5's first task -- done, see below.

## Status: Milestone 5 (Persistence/APIs/authorization) — started

### Report versioning: append-only, replacing the raise (2026-11-05)

`ReportAlreadyStoredError` (milestone 4c) refused a second store for the
same run_id rather than overwriting it, but refusing is still a dead end
-- the narrow lease-reclaim race it guards against (see milestone 4c)
would leave the run `FAILED` even though a perfectly good report had been
stored. Replaced it with real append-only versioning instead:

- **`migrations/005_dpr_report_artifacts_versioning.sql`** -- adds a
  `version INTEGER NOT NULL DEFAULT 1` column to `dpr_report_artifacts`
  and changes its primary key from `(run_id)` alone to `(run_id,
  version)`, guarded by a `DO $$ ... $$` block that only runs the
  `ALTER`/constraint swap once (checked via `pg_constraint`), consistent
  with every other migration here being safe to re-run on every app
  startup. Adds a `(run_id, version DESC)` index for the "give me the
  latest" lookup.
- **`report_store.py`** -- `store_report()` now only ever `INSERT`s a new
  row at `MAX(version) + 1` for that run_id (locking existing rows with
  `SELECT ... FOR UPDATE` first, in a separate statement from the
  aggregate -- Postgres rejects `FOR UPDATE` directly on an aggregate
  query, caught by testing against a real Postgres, not assumed) and
  returns the version number stored. No `UPDATE` statement exists
  anywhere in this module. `get_report()` takes an optional `version`
  kwarg -- omitted, it returns the latest (so `report_service.py`'s
  existing `get_report(engine, run["run_id"])` call needed no change);
  given, it returns that exact historical version. New
  `list_report_versions()` returns every version number ever stored for
  a run_id, oldest first.
- Removed `ReportAlreadyStoredError` entirely -- nothing raises on a
  second store now.
- Rewrote `tests/test_dpr_report_store.py` for the new semantics (4
  tests, all re-verified against a real throwaway Postgres, not just
  mocked): round-trip, a second store appending version 2 while version 1
  survives untouched, `get_report()` with no stored report returning
  `None`, and the exact lease-reclaim race from milestone 4c re-run under
  the new code -- which now ends the run `COMPLETED` with both versions
  preserved, instead of the old `FAILED`-despite-a-valid-report outcome.
  This closes the narrow rough edge milestone 4c flagged as a follow-up.

### Tests run

```
python3 -m pytest tests/test_dpr_report_store.py tests/test_dpr_report_access.py -q
  (real Postgres) -> 18 passed
python3 -m pytest tests/ -q (real Postgres, same unrelated pre-existing
  log-safety failure deselected) -> all pass
ruff check / ruff format --check -> clean
```

### Remaining work

- Milestone 5's other items per Section 3A/15: new API routes, the rest
  of persistence/authorization. Not started.

### Report versioning, part 2: idempotent per-run storage, scoped to the service, immutable in the DB (2026-11-05)

Five follow-up gaps closed before touching the API routes:

1. **Idempotent storage.** `store_report()` previously appended a new
   version on every call, even a byte-identical retry of the same
   run_id. Added `snapshot_sha256` (a sha256 of the canonical report
   JSON) with a `UNIQUE (run_id, snapshot_sha256)` constraint (migration
   006): storing the same run_id with the same content now returns the
   existing version instead of appending a redundant one.
2. **Re-analysis produces a new run_id, so versions are now scoped to
   `source_reference`, not `run_id`.** Checked `jobs.submit()`: the
   idempotency key is `f"{service_request_id}:{source_reference}:
   {document_sha256}:{analysis_version}"`, so a DPR correction (a new
   uploaded document -> a new `document_sha256`) always produces a
   brand-new `run_id` -- confirmed, not assumed, by reading `submit()`
   directly. Versioning by `run_id` would therefore restart at v1 on
   every correction. Migration 006 rescopes versions to
   `source_reference` -- the same key `report_service.latest_run_for()`
   already uses to span runs for one person+service -- via
   `UNIQUE (source_reference, version)`, so a correction now continues as
   v2, v3. `get_report()`/`list_report_versions()` take
   `source_reference` in place of `run_id`; `store_report(engine, run_id,
   result)` keeps `run_id` (it looks up that run's `source_reference`
   itself via a join), so `scripts/dpr_worker.py`'s call site needed no
   change. `report_service.py`'s `get_report(engine, run["run_id"])`
   became `get_report(engine, ref)` (`ref` was already computed there).
   Added `test_reanalysis_under_a_new_run_id_continues_the_same_version_
   history`, which submits two runs with different `document_sha256` under
   one `source_reference` and asserts the history reads `[1, 2]`.
3. **Concurrent-writer conflict, caught and retried.** With no existing
   row for a `source_reference`, `SELECT ... FOR UPDATE` locks nothing, so
   two first-time stores can both compute "next version = 1" before
   either commits -- the `UNIQUE (source_reference, version)` constraint,
   not the lock, is what actually serializes them. `store_report()` now
   retries on `IntegrityError` (bounded at 5 attempts, raising
   `RuntimeError` past that) and re-derives the next version from
   scratch each time. `test_concurrent_writers_with_no_prior_version_
   retry_past_the_conflict` runs two real threads against real Postgres
   via a `threading.Barrier` to force the race, and asserts both succeed
   with versions `[1, 2]` -- not a single lucky interleaving.
4. **`get_report()` defaults to the latest version with
   `render_status='RENDERED'`.** Added a `render_status` column
   (`PENDING`/`RENDERED`/`FAILED`); `store_report()` sets it from the
   stored `AnalysisResult.status` (`"completed"` -> `RENDERED`,
   `"incomplete"` -> `PENDING`). The default (no `version` given) lookup
   filters to `RENDERED`; an explicit `version` lookup ignores the filter
   (for audit/debugging). `test_get_report_default_skips_a_version_that_
   never_finished_rendering` stores a `RENDERED` v1 then an `incomplete`
   (`PENDING`) v2 and confirms the default lookup still returns v1, not
   `None` and not the unrendered v2.
5. **Immutability enforced in the database, not just by this module's own
   discipline.** Migration 006 adds a `BEFORE UPDATE OR DELETE` trigger on
   `dpr_report_artifacts`: `DELETE` always raises; `UPDATE` raises unless
   the only change is `render_status`, and then only if the transition is
   strictly forward (`PENDING` -> `RENDERED` or `PENDING` -> `FAILED`;
   anything else, including the reverse or a lateral `RENDERED` <->
   `FAILED`, raises). `test_a_stored_report_row_cannot_be_updated_or_
   deleted` and `test_render_status_may_only_move_forward` issue raw SQL
   against real Postgres (bypassing `report_store.py` entirely) and
   assert the trigger -- not application code -- is what refuses them.

Old rows (pre-006) are backfilled: `source_reference` from a join against
`dpr_analysis_runs`, a synthetic non-colliding `snapshot_sha256`
(`'legacy-' || run_id || '-' || version`, which can never collide with a
real 64-hex-char hash), and `render_status='RENDERED'` so existing history
stays visible under the new default filter. The old `(run_id, version)`
primary key is dropped for a surrogate `id`; `UNIQUE (run_id,
snapshot_sha256)` and `UNIQUE (source_reference, version)` are the
constraints that now matter.

**Per-scenario Page 4 thresholds and their input sets (from last round,
reposted as asked):**

| Scenario | Variable cost/acre | Break-even (accounting) | Break-even (cash) | Break-even (DSCR=1.0) |
|---|---|---|---|---|
| Using your DPR's costs (`dpr_as_written`) | ₹750 | **622 acres/year** | 193 acres/year | **483 acres/year** |
| If flagged corrections are confirmed (`corrected_costs`) | ₹900 | **726 acres/year** | 226 acres/year | **563 acres/year** |

Shared inputs for every cell above (Year 1): price/unit ₹1,800/acre,
depreciation ₹4.50 L, interest on term loan ₹2.03 L, principal due ₹3.21
L, interest due ₹2.03 L, tax rate 11.71% (derived from tax paid ₹0.50 L /
PBT ₹4.27 L).

**13-issue destination list (from last round, reposted as asked):** see
"The 13-issue audit" section above for the full table; net result
unchanged by this round's work -- F0001-F0006 map 1:1 or many:1 to 6 of
the 13 original issues, 5 issues are not modeled by any agent/check in
this pipeline, and 2 (#7 break-even cost, #13 running cost/acre) are
computed but have no dedicated Finding. No two distinct issues were
merged; the only merge is F0005's six narrative-duplicate-plus-one-
calculated restatements of issue #1, confirmed by comparing each pair's
explanation text.

### Tests run (part 2)

```
python3 -m pytest tests/test_dpr_report_store.py tests/test_dpr_report_access.py -q
  (real Postgres) -> 24 passed (9 in test_dpr_report_store.py, up from 4;
  14 in test_dpr_report_access.py, unchanged)
python3 -m pytest tests/ -q (real Postgres, same unrelated pre-existing
  log-safety failure deselected) -> all pass, exit 0
ruff check / ruff format --check -> clean
```

### Remaining work (updated)

- Milestone 5's other items per Section 3A/15: new API routes, the rest
  of persistence/authorization. Not started -- starting next.

### Section 15 audit, then wire, reusing the existing routes (2026-11-05)

Audited first (no code changes), then fixed/wired per the audit, per the
user's own ordering.

**Audit.** Found more already built than this file's own "not started"
note suggested: `GET /api/v1/customer/services/{code}/report` and
`GET /api/v1/bd/customers/{id}/services/{code}/report` already existed,
ownership-scoped, gated through `report_access.evaluate_report_access`,
with `?format=pdf` -- but that PDF was `report_render.py`, a generic
reportlab renderer, never the six-page CAM report from milestones 4-4c.
Audit table (route x check -> pass/gap):

| Route | Payment lock | Consent | Ownership | No rerun on GET | Versioning | Locked response | Internal exposure | Idempotency |
|---|---|---|---|---|---|---|---|---|
| `GET .../customer/.../report` | PASS -- fresh vendor re-check every call | PASS* | PASS -- `person_id` is always the token's own `me` | PASS -- never calls `submit()`/`analyze()` | **GAP** -- no `version` param | **GAP** -- no `Cache-Control` | n/a | PASS -- read-only |
| `GET .../bd/.../report` | PASS | PASS* | PASS -- `get_bd_id` self-match + `bd.owned_person()` inside `enrolment._values()` | PASS | **GAP** | **GAP** | n/a | PASS |
| `PUT .../document-with-analysis` (both) | n/a (upload gate is `view.can_upload`, itself payment-derived) | n/a | PASS (same mechanism) | n/a -- a PUT is supposed to start the pipeline | n/a | n/a | n/a | PASS -- `jobs.submit()`'s idempotency key |
| `POST /internal/dpr-agentic/analyze` | n/a (stateless preview, no release decision) | n/a | n/a -- caller-trusted internal token only | n/a | n/a | n/a | **GAP** -- see below | n/a (no durable job) |
| `POST /internal/dpr-agentic/inspect-upload` | n/a | n/a | **documented GAP** -- docstring already says "cannot itself prove upload ownership; integration must enforce it" | n/a | n/a | n/a | **GAP** | n/a |

\* Consent: `consent_satisfied` is hardcoded `True` in `report_service.py`,
justified by an enrolment.py invariant (pay-later always requires both
consents before a service reaches `Paid`; pay-now needs none) -- not a
gap, since there is no persisted consent flag to check independently of
`Paid` at report-access time without inventing new state. Tested directly
on the pure policy function instead (`consent_satisfied=False` in
`tests/test_dpr_release_policy.py`, pre-existing).

Internal-exposure gap: `dpr_analysis.router` is mounted under the same
public `/api/v1` prefix as every customer/BD route (`app/api/v1/router.py`)
-- there is no separate internal network or port, so the shared-secret
`X-Internal-Token` header (checked with `secrets.compare_digest`) is the
only thing standing between it and the public internet. Fixed what's
fixable in-repo: factored the check into `_check_internal_token()` and
added a log line on rejection (`dpr_internal_token_rejected`, client IP,
path -- never the token itself) for operational visibility; the feature
flag is still checked before the token, so a disabled flag doesn't even
reveal the route exists. True network isolation is outside this repo.

**Fixes, in the shared gate.** `report_service.py`'s `access_report()` --
already the one function both routers called, so a fix here benefits both
without being written twice -- gained a `version` kwarg, backed by a new
private `_gate()` that both `access_report()` and two new sibling
functions (`access_report_pdf()`, `access_report_versions()`) call, so
there is exactly one authorization evaluation for every report-derived
endpoint. `report_http.py` is new: one module with the HTTP-status/header
mapping (`resolve_report`, `resolve_report_pdf`, `resolve_findings`,
`resolve_annexures`, `resolve_versions`), called identically from
`customer.py` and `bd.py` -- including the new `Cache-Control: private,
no-store` header (closing the "locked response" gap: a DPR report is
re-evaluated fresh on every request and must never be cached by a shared
intermediary).

**Wiring the CAM report in.** `store_report()` (migration 007) now renders
the six-page CAM report (`cam_adapter.build_snapshot_from_analysis` +
`cam_render.render_cam_report_pdf`) once, at store time, and persists the
bytes in a new `report_pdf` column -- `GET .../report?format=pdf` now
serves those stored bytes (`get_report_pdf()`), never rendering on the
request. Deliberately a *separate* `pdf_render_status`
(`PENDING`/`RENDERED`/`FAILED`), not folded into the existing
`render_status`: a PDF-rendering bug must never hide an otherwise-good
JSON report from `get_report()`'s default filter. No vendor-sourced
borrower/project profile is available at store time -- `store_report()`
is called from `scripts/dpr_worker.py`, which never calls the vendor by
design (see its own module docstring) -- so the CAM templates' own
missing-data state is what renders for those fields, the same
anti-fabrication discipline as everywhere else in this pipeline. A
rendering failure never blocks the JSON from being stored (caught broadly,
logged as `dpr_cam_pdf_render_failed`, `pdf_render_status='FAILED'`); the
old `report_render.py` renderer is left in place (still unit-tested on its
own) but no longer wired to any route.

**Missing routes added**, all behind the same `_gate()`/`resolve_*`
helpers, on both routers: `GET .../report/findings` (the gated report's
`findings` only), `GET .../report/annexures` (project cost, means of
finance, financial ratios and per-scenario calculations with their full
`CalculationTrace`s -- a pure Python reshape via `build_snapshot_from_
analysis`, not a WeasyPrint render, so it doesn't reintroduce "re-rendering
on GET"), `GET .../report/versions` (every version number ever stored,
oldest first). `GET .../report` itself gained `?version=` for a specific
historical version (its own integrity check differs slightly from the
default lookup: an explicit version's `report_artifact_verified` is just
"does this row exist," since by definition it isn't the latest run's
artifact).

**Tests**, all against real Postgres: `tests/test_dpr_report_access.py`
grew from 14 to 27 (unauthenticated 401 for both routers; repeated GETs
proven to never create or mutate a run, even across QUEUED -> COMPLETED;
an older version still retrievable as JSON and PDF after a second,
corrected run; `Cache-Control: private, no-store` present on JSON and
PDF; findings/versions/annexures happy-path and gating for both routers).
`tests/test_dpr_report_store.py` gained
`test_concurrent_writers_with_no_prior_version_retry_past_the_conflict`
(two real threads, a `threading.Barrier`, forced past the `UNIQUE
(source_reference, version)` conflict) -- found and fixed a real test
hazard along the way: WeasyPrint/cairo/pango are not safe to call from two
threads at once (observed as an indefinite hang, not an exception), so
that one test stubs `_render_cam_pdf` to isolate what it actually verifies
(the DB-level retry); this has no production impact since
`scripts/dpr_worker.py` never calls `store_report()` from more than one
thread.

### Tests run (part 3)

```
python3 -m pytest tests/test_dpr_report_store.py tests/test_dpr_report_access.py -q
  (real Postgres) -> 36 passed (9 + 27)
python3 -m pytest tests/ -q (real Postgres, same unrelated pre-existing
  log-safety failure deselected) -> all pass, exit 0
ruff check / ruff format --check -> clean
```

### Remaining work (updated again)

- Milestone 6 (frontend: wire `?format=pdf` into `AnalysisReport.tsx`) and
  milestone 7 (polish the `/report/annexures` payload into the full
  Annexure B document, Section 12) remain not started.

### Closing milestone 5 (2026-11-05)

**1. WeasyPrint render moved to a subprocess with a hard timeout.** The
concurrent-writer test from last round already showed WeasyPrint/cairo/
pango can hang rather than raise; `store_report()`'s render step now runs
in a spawned (never forked -- forking would duplicate a live DB connection
into the child for no reason) subprocess via a new
`_run_in_subprocess_with_timeout()`, joined with `parent_conn.poll(timeout)`
and `terminate()`/`kill()` on timeout or afterward either way, so the
subprocess is never left running. Configurable via
`Settings.dpr_cam_pdf_render_timeout_seconds` (default 120s), threaded
through from `scripts/dpr_worker.py`. A timeout (or any other render
failure) becomes `pdf_render_status='FAILED'`; the JSON report still
stores and still releases. Also moved the render to *before* the DB
transaction opens (previously inside it): a render taking up to 120s must
not hold `FOR UPDATE` row locks for that long. The version number baked
into the PDF's own content (`report_id`/`report_version`) is now a
best-effort estimate taken just before rendering, since the real number
isn't resolved until the insert after -- correct except in the
vanishingly rare case a concurrent writer also claims that number and this
call has to retry onto the next one, which the stored `version` column
(what every route actually reads) is unaffected by.
Tests: `test_a_hanging_render_times_out_and_the_subprocess_is_never_left_
running` (the generic mechanism, using a module-level `_never_finishes`
target -- multiprocessing's spawn pickles a target by module+qualname, so
it can't be a closure) and `test_store_report_marks_pdf_failed_when_the_
render_times_out` (end to end through `store_report()`).

**2. Access tests added for findings/annexures/versions, both routers:**
unpaid (`test_customer_sub_routes_locked_when_unpaid`, `test_bd_sub_
routes_locked_when_unpaid`) and wrong owner
(`test_bd_sub_routes_404_for_another_brokers_customer` -- customer routes
have no wrong-owner case to test, since `person_id` is always the token's
own `me`, never attacker-supplied). "No consent" isn't retested per-route:
all four routes share `report_service._gate()`, so `test_dpr_release_
policy.py`'s `consent_satisfied=False` case already covers every one of
them from the one place that actually implements the check.

**3. Storage interface: none exists, recorded as a known limitation.**
Checked -- there is no approved object-storage interface anywhere in this
repo (`app/services/service_fields.py` has the identical documented gap
for uploaded customer documents: "object storage exists... move to storage
(e.g. MinIO) before many customers have them", never implemented). So
`report_pdf` stores PDF bytes directly in Postgres `BYTEA`, recorded as a
known limitation in `report_store.py`'s own module docstring: if an object
store is added later, this column should hold a storage key and checksum
instead of the bytes, in that same migration.

**4. Internal endpoints hardened in-repo:**
- **Separate mount.** `dpr_analysis.router` is no longer included in
  `api_router` (`/api/v1/...`); `app.main.create_app()` mounts it directly
  on `app` instead. Its routes moved from `/api/v1/internal/dpr-agentic/*`
  to `/internal/dpr-agentic/*` -- a different URL namespace from every
  dashboard-facing route, though still the same process/port (true network
  isolation remains outside this repo, as already noted last round).
- **Constant-time comparison:** already in place (`secrets.compare_digest`)
  before this round; unchanged.
- **Rate-limiting of rejections:** a new per-client-IP counter
  (`dpr_agentic_internal_token_max_rejections`, default 10, within
  `dpr_agentic_internal_token_rejection_window_seconds`, default 60s) --
  past the cap, further attempts get `429` without even comparing the
  token. Deliberately counts only *failed* attempts, checked against prior
  rejections before the token comparison runs, so a legitimate caller
  making many fast, correct calls is never rate-limited --
  `test_successful_calls_never_count_toward_the_rejection_limit` pins this.
  In-process only: behind more than one worker/replica, each has its own
  counter, so the real cap across all of them is higher than configured --
  accepted as a gap, not hidden (a shared store like Redis would close it,
  not present in this repo today).
- **Two-secret rotation:** `dpr_agentic_internal_token_previous` -- both it
  and the current `dpr_agentic_internal_token` are accepted, so a token can
  be rotated without a window where every caller is rejected at once.

**5. Reportlab renderer deleted.** `report_render.py` and its test file
were unwired from every route as of last round's CAM-PDF wiring; asked the
user whether to delete or keep-and-document, and the answer was delete.
Removed `app/services/dpr_agentic/report_render.py` and `tests/test_dpr_
report_render.py`. `reportlab` is consequently no longer imported by any
code, but is left declared in `pyproject.toml`/the lock files: removing it
requires a full re-lock (`scripts/lock_dependencies.py`), which is its own
verification pass and wasn't done here unasked -- a clean follow-up item.

**Incident, disclosed:** while writing the internal-token rotation test,
`Settings(...)` was constructed without explicitly overriding
`dpr_agentic_gemini_api_key`. `Settings.model_config` reads `.env` for any
field not explicitly passed, and this machine's real `.env` has a real,
working `DPR_AGENTIC_GEMINI_API_KEY` -- so that one test run made several
real calls to `generativelanguage.googleapis.com` (visible in the test log:
multiple 200s, tens of thousands of tokens total), not a mock. Caught
immediately from the test output, not discovered later. Fixed by passing
`dpr_agentic_gemini_api_key=None` explicitly in every new `Settings(...)`
call in `tests/test_dpr_agentic.py` that could otherwise reach that check.
This is a pre-existing, repo-wide hazard, not unique to these tests: any ad
hoc `Settings(...)` construction that doesn't explicitly null every secret
field can silently fall back to a real `.env` value. Not fixed repo-wide
here (out of scope for this round, and a broader sweep deserves its own
pass), but flagged for one: either a conftest-level fixture that forces
`env_file=None`/zeroes every secret by default, or a documented convention
that every ad hoc `Settings(...)` in a test must list every secret field it
cares about, explicitly.

**Answers to the five open items (consolidated; full detail already in
this file; restated here as asked):**

1. **Page 4 per-scenario thresholds** (from "Report versioning, part 2" /
   milestone 4c, unchanged since): `dpr_as_written` (₹750/acre) -> 622
   acres/yr accounting, 193 acres/yr cash, **483 acres/yr** debt-service
   threshold; `corrected_costs` (₹900/acre) -> 726 / 226 / **563**
   acres/yr. Shared Year-1 inputs: price/unit ₹1,800/acre, depreciation
   ₹4.50L, interest on term loan ₹2.03L, principal due ₹3.21L, tax rate
   11.71%.
2. **13-issue destination list**: see "The 13-issue audit" section above
   for the full table. 6 of 13 map to F0001-F0006 (F0005 a confirmed
   6-way merge of one issue, not a collision between distinct ones); 5 are
   not modeled by any check in this pipeline; 2 (#7 break-even cost, #13
   running cost/acre) are computed but have no dedicated Finding.
3. **`(run_id, snapshot_sha256)` idempotency constraint**: added in
   migration 006 as `dpr_report_artifacts_run_snapshot_key`; `store_
   report()` checks for an existing row matching both before computing a
   version, returning that version unchanged rather than appending --
   `test_storing_identical_content_for_the_same_run_is_idempotent`.
4. **Version scope across re-analysis**: confirmed by reading `jobs.
   submit()` directly -- its idempotency key includes `document_sha256`,
   so a DPR correction (new document) always gets a new `run_id`. Versions
   are scoped to `source_reference` (migration 006,
   `dpr_report_artifacts_source_version_key`), the same key `latest_run_
   for()` already uses to span runs, so a correction continues as v2, v3
   rather than restarting at v1 --
   `test_reanalysis_under_a_new_run_id_continues_the_same_version_history`.
5. **Database-level immutability, including the rule for filling
   `report_pdf`**: a `BEFORE UPDATE OR DELETE` trigger
   (`dpr_report_artifacts_immutable()`, migrations 006 and 007) rejects
   every `DELETE`, and every `UPDATE` except two specific forward-only
   transitions: `render_status` (`PENDING` -> `RENDERED`/`FAILED`) and
   `pdf_render_status` (same shape). `report_pdf` itself may only change in
   the same statement that moves `pdf_render_status` off `PENDING` -- i.e.
   exactly once, when the render result first lands -- never again
   afterward, even to another non-NULL value. `run_id`, `source_reference`,
   `version`, `snapshot_sha256`, `report` and `created_at` are immutable
   from the moment of insert, full stop. Verified with raw SQL bypassing
   `report_store.py` entirely (`test_a_stored_report_row_cannot_be_
   updated_or_deleted`, `test_render_status_may_only_move_forward`).

### Tests run (part 4)

```
python3 -m pytest tests/test_dpr_report_store.py tests/test_dpr_report_access.py tests/test_dpr_agentic.py -q
  (real Postgres) -> 54 passed
python3 -m pytest tests/ -q (real Postgres, same unrelated pre-existing
  log-safety failure deselected) -> all pass, exit 0
ruff check / ruff format --check -> clean
```

### Status: Milestone 5 -- complete

Versioning, persistence, API routes and authorization for the DPR report
are done, audited, and hardened per Sections 3A/15/16. Next: milestone 6
(frontend), then milestone 7 (Annexure B document).

### Repo-wide test isolation, before milestone 6 (2026-11-05)

Three layered, session-scoped, autouse fixtures added to `tests/conftest.py`
so the disclosed Gemini-billing incident's root cause can't recur anywhere
in the suite, not just in the one file it happened in:

1. **`_never_read_the_real_dotenv_in_tests`**: overrides
   `Settings.model_config["env_file"] = None` for the whole test session.
   An unset secret field on an ad hoc `Settings(...)` call now becomes
   `None`/its default, never a real value from the real `.env` -- verified
   experimentally against this exact installed pydantic-settings version
   (2.13.1) before relying on it, not assumed.
2. **`_block_real_outbound_network_in_tests`**: monkeypatches
   `socket.socket.connect` to raise `RuntimeError` for any non-loopback
   address; loopback (the throwaway test Postgres) still connects
   normally. respx-mocked vendor calls and `TestClient`'s in-process ASGI
   transport never reach a real socket in the first place, so neither is
   affected -- confirmed by running the full suite under this guard with
   zero unexpected failures.
3. **`_fail_on_real_gemini_client`**: reads the real on-disk `.env`'s
   `DPR_AGENTIC_GEMINI_API_KEY` once, for comparison only, and makes
   `GeminiAgentClient.__init__` raise immediately (before any network
   attempt) if ever constructed with that exact value -- catches even a
   path that bypassed `Settings` entirely (e.g. reading `os.environ`
   directly), and names Gemini specifically in the failure message rather
   than a generic socket error.

New `tests/test_dpr_test_isolation.py` (5 tests) proves each guard actually
fires -- including a real, direct test that constructing
`GeminiAgentClient` with the literal real `.env` key raises, run against
this machine's actual `.env`, not a simulated one.

**Full suite re-run under all three guards: zero external calls, same one
pre-existing unrelated failure as every prior round
(`test_dpr_log_safety.py::test_process_one_sets_current_run_id_for_the_
whole_call_chain`), exit 0 otherwise.** This is the "run the full suite and
confirm zero external calls" the user asked for -- confirmed by the run
itself completing without any blocked-connection `RuntimeError` surfacing
anywhere in the suite, which it would have for any test that attempted one.

**What content the accidental Gemini run sent**: synthetic test data only,
never the real Tikamgarh DPR (no real Tikamgarh DPR file exists at all --
see milestone 1's note that the golden fixture is an explicitly labeled
simulation). The exact payload, from `tests/test_dpr_agentic.py`'s
module-level `PAYLOAD` constant:
```
{
    "service_request_id": "SR-1",
    "wallet_document_id": "W-1",
    "document_sha256": sha256(b"sample"),
    "pages": [{"page": 1, "text": "Project cost is 80 lakh and loan is 60 lakh."}],
}
```
That one line of placeholder text was sent once per orchestrator role (11
calls total -- 10 roles' `generate()` plus one `extract()`), each with a
different system prompt but the identical document content above.

**Internal-endpoint rate limiter: confirmed in-memory, per-process.**
`dpr_analysis.py`'s `_rejections_by_ip` is a plain module-level `dict`,
scoped to one Python process. Already recorded as a limitation in last
round's PROGRESS.md entry ("In-process only... accepted as a gap"),
restated here as asked: behind more than one worker process or replica,
each has its own independent counter, so the real cap across all of them
is `dpr_agentic_internal_token_max_rejections` multiplied by however many
processes are running, not the configured number itself. A shared store
(e.g. Redis) would close this; none exists in this repo today.

### Tests run (part 5)

```
python3 -m pytest tests/test_dpr_test_isolation.py -q -> 5 passed
python3 -m pytest tests/ -q (real Postgres, same unrelated pre-existing
  log-safety failure deselected) -> all pass, exit 0, zero blocked-network
  RuntimeErrors anywhere in the run
ruff check / ruff format --check -> clean
```
