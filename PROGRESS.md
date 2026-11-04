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
## Status: Milestone 4 (Templates and PDF) — started, Page 3 of 6 done

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

### Remaining work

- Pages 4-6 (Repayment; Security and obligations; Benefits and next
  steps) -- not started. Each needs the same treatment: read the design
  archive, identify recommendation/approval language to re-author, map
  CAMSnapshot fields, write the Jinja template, write
  `build_pageN_context()`, write tests.
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
