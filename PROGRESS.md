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

### Status: Milestone 5 -- closed

Reportlab deleted, test isolation in place, all five open items answered.
Starting milestone 6.

## Milestone 6: frontend -- wire `?format=pdf` into `AnalysisReport.tsx` (2026-11-05)

`frontend/src/components/AnalysisReport.tsx`'s `AnalysisReportCard` (the
BD's customer-preview Payment section; the only place this component is
used) gained a "View / download PDF" button next to the report status
pill, shown once a report exists. Reuses `DocumentPreview`
(`frontend/src/components/DocumentPreview.tsx`) exactly as
`ServicesPayment.tsx` already does for uploaded documents -- same
fetch-with-bearer-token -> blob -> iframe/Download pattern, no new
component written. New `reportPdfPath(bdId, personId, code)` in
`frontend/src/api/services.ts` mirrors the existing `documentPath()`,
appending `&format=pdf` to the same gated report endpoint
`useServiceReport` already polls as JSON.

`npm run typecheck` and `npm run build` both pass clean.

**Verification, and its limit.** No browser-automation tool is available
in this environment (checked: no `chromium-cli`, no Playwright/Puppeteer
installed) and a live `uvicorn` run would need real vendor sandbox
credentials for the payment/ownership check `access_report()` makes on
every call -- not something to spend for a UI click-through. What was
actually verified:
- The backend half end-to-end, live (not just pytest): seeded a real
  COMPLETED run against a fresh throwaway Postgres via `jobs.submit()`/
  `claim()`/`store_report()`/`finish()` run as a real script (not pytest),
  and confirmed `get_report_pdf()` returned real, non-empty PDF bytes
  (57,235 bytes) pulled from the database.
- The exact backend HTTP path the button calls is already covered end to
  end by `test_bd_report_pdf_format_releases_a_real_pdf` (real Postgres,
  real route, real auth/ownership/payment gate, respx-mocked vendor) --
  confirmed still passing in this round's full-suite run.
- `DocumentPreview` is the identical, already-in-production component
  `ServicesPayment.tsx` uses for documents; no new fetch/render logic was
  written for this change, only a new path and a second call site.
- What was **not** done: an actual click-through in a real browser window.
  Flagging this rather than claiming it, per the honesty this file has
  tried to keep throughout. Offered to the user: set up Playwright for a
  real click-through if that level of verification is wanted.

**Operational note, found while seeding the live verification (not a
production bug, but worth recording):** `multiprocessing`'s `spawn`
context (used by `_render_cam_pdf`'s subprocess-with-timeout, added this
round) requires the *calling* process's entry point to have the standard
`if __name__ == "__main__":` guard -- `scripts/dpr_worker.py` already has
one. A throwaway verification script that called `store_report()` from
plain top-level code without that guard failed exactly as designed
(`pdf_render_status='FAILED'`, store_report() itself raised nothing,
execution continued normally) rather than corrupting anything or hanging
-- re-run with the guard added, it produced a real PDF. No code change
needed; recorded here so a future ad hoc script (or a REPL/`python -c`
invocation) doesn't rediscover this the hard way.

## Milestone 6, completed for real: Section 17 dashboard, stub wallet client, Playwright click-through, upload-to-report timing (2026-11-05)

The user flagged that the previous round's milestone-6 report was premature
against Section 17. This round does the actual work and a real,
tool-verified click-through.

### Repo-wide test isolation -- status check (as asked)

Already done and verified in the previous round ("Repo-wide test
isolation, before milestone 6" above); nothing new needed here. Re-ran the
full suite again this round on a fresh throwaway Postgres: same result,
zero external calls, one pre-existing unrelated failure
(`test_dpr_log_safety.py::test_process_one_sets_current_run_id_for_the_whole_call_chain`,
see "Errors and fixes" below for its actual root cause, now identified).
The five PROGRESS.md answers from that round, restated exactly as asked:

1. **Page 4 per-scenario thresholds**: `dpr_as_written` (Rs.750/acre) ->
   622 acres/yr accounting, 193 acres/yr cash, **483 acres/yr** debt-service
   threshold; `corrected_costs` (Rs.900/acre) -> 726 / 226 / **563**
   acres/yr. Shared Year-1 inputs: price/unit Rs.1,800/acre, depreciation
   Rs.4.50L, interest on term loan Rs.2.03L, principal due Rs.3.21L, tax rate
   11.71%.
2. **13-issue destination list**: see "The 13-issue audit" section
   earlier in this file. 6 of 13 map to F0001-F0006 (F0005 a confirmed
   6-way merge of one issue); 5 are not modeled by any check in this
   pipeline; 2 (#7 break-even cost, #13 running cost/acre) are computed
   but have no dedicated Finding.
3. **`(run_id, snapshot_sha256)` idempotency constraint**: migration 006,
   `dpr_report_artifacts_run_snapshot_key`; `store_report()` checks for a
   match before computing a version, returning the existing version
   unchanged rather than appending.
4. **Version scope across re-analysis**: versions are scoped to
   `source_reference` (migration 006), not `run_id` -- a DPR correction
   always gets a new `run_id` (confirmed by reading `jobs.submit()`'s
   idempotency key directly) but continues the same version history.
5. **Database-level immutability, including the `report_pdf` rule**: a
   `BEFORE UPDATE OR DELETE` trigger rejects every `DELETE` and every
   `UPDATE` except forward-only `render_status`/`pdf_render_status`
   transitions; `report_pdf` may only be written once, in the same
   statement that moves `pdf_render_status` off `PENDING`.

**Internal-endpoint rate limiter: confirmed in-memory, per-process** (same
answer as last round -- `dpr_analysis.py`'s `_rejections_by_ip` is a plain
module-level dict; behind more than one worker process the real cap is
the configured limit multiplied by the process count).

### Section 17: dashboard status card, both sides (new this round)

New backend endpoint `GET .../report/summary` (customer and BD routers,
`report_http.resolve_summary()`), gated through the exact same
`access_report()`/`_gate()` as every other report route -- no new
authorization logic, just a smaller, dashboard-shaped projection of the
same `build_snapshot_from_analysis()` reshape `resolve_annexures()` already
uses. Returns: `readiness_status` (the same four-value enum the CAM PDF's
Page 1 badge uses), `must_fix_count`/`should_fix_count`/`good_to_fix_count`
(from each finding's existing `priority` field -- no new classification
logic), `critical_findings_count`, `outstanding_clarifications_count`,
`evidence_references_count`, and a `financial_overview` (project cost,
requested loan, promoter contribution, funding gap, average DSCR -- each
`None` rather than fabricated when the engine hasn't computed it, same
"not available" discipline as everywhere else in this pipeline).

Frontend: `ReportSummarySection.tsx` is a new **pure presentational**
component (no hooks of its own) rendering the status pill, the
Must/Should/Good tiles, the financial overview, the outstanding-items and
evidence-reference counts, the PDF/annexures buttons, and three explicit
states -- **locked** (403, same reasons list as before), **failed-render**
(the PDF button's `DocumentPreview` surfaces `report_store`'s own "PDF for
this report version is not available" message verbatim), and **error**
(anything else, shown as-is). Both `AnalysisReportCard.tsx` (BD) and the
new `CustomerAnalysisReport.tsx` (customer) fetch their own data with
their own routes and pass it into this one shared component -- the
dashboards share the card, not just the styling. Wired into the customer
dashboard via a new "View report" button + dialog on `CustomerHome.tsx`'s
service rows (there was no report UI there before this round; only the BD
side had one). New hooks: `useServiceReportSummary`/`useServiceReportAnnexures`
(services.ts, BD) and their `useMy*` mirrors (customer.ts).

Backend tests: `test_report_summary_endpoint_returns_readiness_and_counts`,
`summary` added to the existing `_SUB_ROUTES` locked/wrong-owner sweep (so
it inherits every one of those checks for free), and to the
findings/versions/annexures BD-mirrors-customer test.

### Stub wallet payment client (new this round)

`app/services/stub_wallet_client.py`: a `StubWalletPaymentClient` that
fakes a successful pool-to-main transfer and never makes an HTTP call.
Wired into `EnrolmentService._transfer()` behind a new
`Settings.vendor_wallet_stub_enabled` flag, which a new
`model_validator` refuses to be `True` unless `environment` is
`local`/`dev`/`test` -- raises `ValidationError` otherwise, so it can
never silently reach a real deployment's config. Tests:
`tests/test_stub_wallet_client.py` -- the validator's refusal/acceptance,
and a full pay-later flow proving the real vendor's
`/transfer/systemAccountPayment` is never called while the custom-field
writes are byte-identical to the real-vendor flow.

### Playwright click-through (new this round, with real screenshots)

Installed `playwright` as a pinned frontend devDependency (`1.49.1`) and
its Chromium build. Built `scripts/playwright_server.py`: runs the real
FastAPI app with the vendor faked by **respx** (the identical mocking
mechanism the pytest suite already uses, not a new approach) and a
throwaway Postgres, plus `vendor_wallet_stub_enabled=True`. Seeds three
customers under one BD: paid with **two** report versions, a document
uploaded but payment never verified (locked), and one whose PDF render was
deliberately forced to fail (`_render_cam_pdf` monkeypatched to `None` for
that one seed only -- never a real WeasyPrint hang). No real vendor
credentials, no real Gemini call, no real money movement anywhere in this
path.

`frontend/scripts/playwright_click_through.mjs` drives a real Chromium
against this server (fronted by a real Vite dev server) and captures five
scenarios to `docs/review/frontend/`:

- `01-customer-paid-report-summary.png` -- customer dashboard, "View
  report" dialog, full Section 17 card (readiness, 0/0/0 Must/Should/Good,
  financial overview, version 2).
- `02-unpaid-customer-locked.png` -- BD's customer-preview page, inline
  "Analysis reports" section, the explicit `PAYMENT_NOT_VERIFIED` locked
  message with the document already uploaded (proving this is the payment
  gate, not "nothing happened yet").
- `03-bd-view-paid-customer.png` -- same card on the BD side, inline on
  the page (no dialog needed there).
- `04-failed-pdf-render.png` -- clicking "View / download PDF" opens the
  dialog and shows "The PDF for this report version is not available."
  verbatim, while the rest of the card (readiness, counts) still renders
  normally from the JSON report, which never depended on the PDF.
- `05-older-version-v1-report-json.png` -- `?version=1` served distinctly
  from the latest (`v2`'s document_sha256 differs), proving the versioned
  store actually serves history, not just the latest.

One real bug was caught and fixed by this exercise, not simulated: the
seed script initially omitted the vendor's top-level `personId` field (it
only needed `mobileNumber` for the BD-list/person-lookup tests this
pattern was copied from), which left `BdCustomer.person_id` null on the
BD's customer-preview page and silently stuck `useCustomerServices` at
`enabled: false` forever -- "Loading payments..." with no network error
to see, a genuinely confusing failure mode caught only because something
was actually being clicked and screenshotted, not just asserted against
in a unit test.

**What this click-through is and isn't**: it is a real Chromium, a real
Vite dev server, and a real FastAPI app with a real Postgres behind it --
not a simulation. It is not a test of the real vendor sandbox or a real
Gemini call; those remain gated by the standing live-vendor permission
(sandbox reads/writes) and the separate real-billing concern (Gemini),
respectively, neither touched by this exercise.

### Upload-to-report latency (new this round, measured)

`scripts/dpr_worker.py`'s `process_one()` already logged its own
processing time (`dpr_run_completed duration_ms=...`), but that excludes
however long a run sits `QUEUED` before a worker claims it -- not what the
user actually experiences between uploading a document and the report
being ready. Added a second log line, `dpr_upload_to_report_ms`, computed
from `dpr_analysis_runs.created_at` (set at `submit()` time, the same
request that stores the uploaded document) to the moment the run is
marked `COMPLETED` -- by which point the report, PDF included, is already
stored. `jobs.get_run()` now also returns `created_at`.

**Measured, synthetically** (`tests/test_dpr_upload_to_report_timing.py`,
a `_FastGemini` stub returning instantly, real Postgres, real subprocess
WeasyPrint render, logged value read directly off a real run):
**`dpr_upload_to_report_ms=11292`** (~11.3s) of pure infrastructure
overhead on this machine -- almost entirely the WeasyPrint subprocess
(font subsetting in particular; `dpr_run_completed`'s own figure was
11286ms of that), not the DB round trips, which are negligible by
comparison. This number will vary with the machine and is not a
production SLA. It is **not** a real-world latency estimate -- it
deliberately excludes the one dominant real cost, actual Gemini
inference across 10 orchestrator roles plus one extraction call, which on
past real (non-test) runs has taken single-digit minutes. Measuring that
honestly needs a real, billed Gemini call against a real document, which
-- like moving real money -- is something this file's standing
instructions ask to be asked about first, every time, rather than run
silently as a side effect of an unrelated task. Not done here; offered as
a next step if wanted.

### Errors and fixes (this round)

- **`test_process_one_sets_current_run_id_for_the_whole_call_chain`'s
  actual root cause, now identified** (previously just flagged as "one
  pre-existing unrelated failure" across several rounds): the test
  constructs `settings` as a bare `SimpleNamespace` missing
  `cam_engine_enabled`/`dpr_cam_pdf_render_timeout_seconds`, and
  `process_one()` evaluates `settings.cam_engine_enabled` as a keyword
  argument to `run_claimed_analysis()` before any analysis runs --
  `AttributeError`, caught by `process_one`'s own `except Exception`,
  logged as `dpr_run_failed`, never raised. The sibling test
  `test_worker_never_logs_page_text_or_llm_output` uses the identical
  `SimpleNamespace` shape and still passes, because its assertion (no
  canary text in logs) holds trivially whether or not anything actually
  ran. This round's new timing test
  (`test_dpr_upload_to_report_timing.py`) avoids the trap by constructing
  a real `Settings()` instead. Not fixed in the pre-existing test itself
  (out of scope for this round's ask), but the actual cause is now on
  record rather than "known flaky."
- **Seed script database collision**: running an ad hoc seeding/debug
  script against the *same* throwaway Postgres the full pytest suite was
  still using concurrently produced a real
  `store_report: exceeded 5 attempts racing concurrent writers` failure --
  a genuine concurrency symptom, not a bug, caused by both processes
  writing report versions under the same hardcoded test `source_reference`
  at once. Fixed by always using a dedicated throwaway Postgres container
  per concurrent activity in this round, never sharing one across an
  active pytest run and an ad hoc script.
- **Seed data missing the vendor's `personId` field**: see the Playwright
  section above.

### Status: Section 17 and the Playwright click-through are complete

Backend: `ruff check`/`ruff format --check` clean; full suite green
(same one pre-existing, now-explained failure). Frontend: `npm run
typecheck` and `npm run build` clean. Playwright: five real screenshots in
`docs/review/frontend/`, one real bug found and fixed along the way.
Next: milestone 7 (the full Annexure B / CAM Annexures document --
`/report/annexures` today returns the computed figures as JSON, not yet a
rendered Annexure B document in its own right).

## Before milestone 7: process_one() resilience, then all seven Section 12 annexures (2026-11-05)

### Fix 1: the SimpleNamespace test, then process_one()'s real bug

Root cause, confirmed precisely this round (last round only flagged it as
"one pre-existing unrelated failure"): `process_one()` passes
`cam_engine_enabled=settings.cam_engine_enabled` as a keyword argument
while **building** the call to `run_claimed_analysis(...)` -- Python
evaluates every argument before the call executes, so a `settings` object
missing that attribute raises `AttributeError` *before* `claim()` ever
runs. `run_claimed_analysis()`'s own `except` block (which calls
`finish()`) never gets a chance to run, because `run_claimed_analysis`
itself was never entered. `process_one()`'s own `except` block only
logged a warning -- it never touched the run's row. Net effect: the run
stays `QUEUED` forever, retried identically (and identically failing)
every poll cycle, with no terminal state and no bound on the retries
(`claim()`'s `attempts` counter never increments either, since `claim()`
never ran) -- exactly the "stuck, silently swallowed" failure mode the
user described.

Three tests in `tests/test_dpr_log_safety.py` shared the same bare
`SimpleNamespace(dpr_agentic_vision_enabled=..., dpr_agentic_vision_max_pages=...,
dpr_agentic_liteparse_page_min_chars=...)` settings shape, missing
`cam_engine_enabled`/`dpr_cam_pdf_render_timeout_seconds`. One of the
three (`test_process_one_sets_current_run_id_for_the_whole_call_chain`)
actually asserted real completion (11 LLM calls with a consistent
`run_id`) and so failed outright. The other two
(`test_worker_never_logs_page_text_or_llm_output`,
`test_worker_failure_logging_never_includes_exception_message_text`)
"passed" only because their assertions (no canary text in logs) hold
trivially whether or not anything actually ran -- the second one in
particular never got to exercise what it claims to test (a `RuntimeError`
from the LLM), because the unrelated `AttributeError` fired first every
time.

**Fixed**:
1. All three tests now share a `_worker_settings()` helper returning a
   real `Settings(_env_file=None, ...)`, not a bare `SimpleNamespace` --
   all three now genuinely exercise what they claim to.
2. `jobs.fail_stuck_run(engine, run_id, error_code)` (new): terminal-fails
   a `QUEUED` or `RUNNING` run regardless of lease-token state -- the
   backstop for exactly this class of error, which has none to check. A
   safe no-op once a run is already `COMPLETED`/`FAILED` (so it is always
   safe to call unconditionally from `process_one()`'s `except` block,
   even on the normal path where `run_claimed_analysis()` already
   terminal-failed the run itself).
3. `scripts/dpr_worker.py`'s `process_one()` now calls `fail_stuck_run()`
   in its `except` block, so **any** exception -- before `claim()`, during
   analysis, or anywhere else -- ends with the run `FAILED` and an
   `error_code` recorded, never silently left `QUEUED`/`RUNNING`.

**New regression test**:
`test_a_config_error_before_claim_still_terminal_fails_the_run`
(`tests/test_dpr_log_safety.py`) reproduces the exact bug (a `settings`
missing `cam_engine_enabled`, an `UnreachableGemini` that asserts if ever
called) and proves the run ends `FAILED` with `error_code="AttributeError"`,
not stuck. Plus three new `jobs.py`-level tests in `tests/test_dpr_jobs.py`
for `fail_stuck_run()` itself: fails a `QUEUED` run, fails a `RUNNING` run
ignoring its lease, and is a no-op once a run is already terminal.

**Full suite confirmed green**: re-ran `tests/` twice more on fresh
throwaway Postgres instances -- zero failures, not even the one
pre-existing failure every prior round carried. `ruff check`/
`ruff format --check` clean; frontend `typecheck`/`build` clean.

### Fix 2: all seven Section 12 annexures (A-G), not only B

The user pasted Section 12 verbatim (saved to `docs/CAM_REPORT_SPEC.md`,
and `CLAUDE.md` now points there before any CAM report milestone). It
defines seven annexures: **A** Financial Statements, **B** Repayment, **C**
Sensitivity Analysis, **D** Project Feasibility, **E** Findings Register,
**F** SBI and Scheme References, **G** Audit Trail. `GET .../report/annexures`
previously returned only B's figures (project cost, means of finance,
financial ratios, scenario analysis) under unlettered keys. As asked:
audited what each needs against the `CAMSnapshot` fields that actually
exist and are actually populated, **before** writing any code, then built
the gaps -- reading what's already in the snapshot, inventing no new
calculation engine.

**The audit** (full detail and exact field names in
`app/services/dpr_agentic/cam_annexures.py`'s docstrings; summarized
here):

| Annexure | What's available today | What's missing |
|---|---|---|
| **A** Financial Statements | `financial_ratios` (DSCR/break-even family only) | `financial_projections` (P&L) is modeled but **never populated by any code path**; no balance sheet, cash flow or working capital model exists at all; ratio analysis beyond DSCR (current ratio, debt-equity, ...) is not modeled |
| **B** Repayment | `repayment_schedule` (year 1 only, when `cam_engine_enabled`), `scenario_analysis[*].dscr_by_year` | The engine only ever computes **year 1** of repayment, never a full tenure/amortization; moratorium treatment and repayment assumptions have no field at all |
| **C** Sensitivity Analysis | `scenario_analysis` (labels, descriptions, debt-service impact via DSCR/break-even) | `ScenarioAnalysis` has **no revenue_impact or cost_impact field** -- only the debt-service side of a scenario is modeled |
| **D** Project Feasibility | `operational_assumptions`'s shape exists (sector, free-form assumptions dict, classification) | **Always the empty default** -- no agent populates it; installed capacity, utilization, raw material, production assumptions and market evidence have no code path at all |
| **E** Findings Register | `findings` (`VerifiedFinding`): id, dedup key, merged count, severity, priority+rule, class, verification, dpr/recomputed value, source pages | `evidence` (quotes/pages as structured objects) is **always `[]`** -- `_map_finding()` hardcodes it; no structured financial-impact field (only text inside `priority_rule`); `proposed_corrections` is **never generated** by any code path; `resolution_status` has **no write path** -- every finding stays `"open"` |
| **F** SBI and Scheme References | Schema-ready models exist (`policy_assessment`, `readiness_assessment.subsidy_benefits`) | **Always empty** -- the policy agent explicitly refuses to invent policy without a real corpus (documented, not a bug); no subsidy/AIF-eligibility agent exists; "lending product" has no field anywhere |
| **G** Audit Trail | `analysis_run_id`, `report_id`, `report_version`, document checksum, `generated_at`, `dscr_definition_used`, readiness `score_mode`/`status`, `schema_version`, job-registry `run` row | Per-role **execution timing** isn't captured (only which roles ran, from `agent_outputs`' keys); the Gemini model name was **never persisted onto `AnalysisResult` at all** (only logged transiently) -- fixed this round (see below); priority thresholds are hardcoded constants in `finding_rules.py`, never recorded per run; no reviewer-action workflow exists |

**One small, real plumbing fix alongside the audit** (not new business
logic, just capturing an already-real value that was being thrown away):
`AnalysisResult.gemini_model` (new, optional, defaults `None`) is now set
from `getattr(llm, "model", None)` at the exact point each `AnalysisResult`
is constructed (`parallel_graph.py`, `orchestrator.py`) -- the real model
string the client actually used for that run, not reconstructed from
current config (which could have changed since). `None` for any report
stored before this field existed, reported as explicitly missing in
Annexure G rather than guessed from today's `Settings`.

**Built**: `app/services/dpr_agentic/cam_annexures.py` -- one pure
function per annexure (`build_annexure_a` .. `build_annexure_g`) plus
`build_annexures()` combining all seven, each reading only from the
existing `CAMSnapshot`/`AnalysisResult`/job-registry `run` row, each
returning an explicit `missing: list[str]` naming exactly what it could
not populate and why (per spec: "Do not invent content merely to make
annexures appear complete... may explicitly state that data is
unavailable"). `report_http.resolve_annexures()` now calls this instead of
hand-building the old four-key B-only dict; same gate, same "no
re-rendering on GET" discipline as before. No borrower KYC/bureau data is
in any annexure (Aadhaar is already masked to last 4 digits at the
`BorrowerProfile` level, and no annexure here reads `BorrowerProfile` at
all today since the adapter never has one to supply outside the PDF
render path).

**Tests**: `test_report_annexures_endpoint_returns_all_seven_sections`
(replaces the old four-key assertion) confirms all seven lettered keys,
that every section carries a `missing` list, and that Annexure G's
`analysis_id`/`report_version` are populated. Full suite re-run green
again after this change.

### Status: milestone 7's data layer is complete; the real Gemini run is blocked on one missing input

Per the user's own three-part instruction, the third step -- "I approve
one real Gemini run: the Tikamgarh DPR end to end with
`CAM_ENGINE_ENABLED=true`" -- cannot run yet: **no real Tikamgarh DPR file
exists anywhere in this repo.** `scripts/build_tikamgarh_fixture.py`'s own
docstring says so explicitly: *"No real Tikamgarh DPR source file exists
anywhere in this repo ... the brief's '[PATH TO TIKAMGARH DPR]' was never
filled in."* `tests/fixtures/tikamgarh_cam_snapshot.json` and
`docs/review/tikamgarh/tikamgarh_cam_report.pdf` are both built from a
`FakeLLM` returning hand-derived golden figures (from
`cam-design-reference/LAS Customer CAM Report Template.pdf`), explicitly
labeled as a simulation, never a real document run. Needed before that
step: the actual Tikamgarh DPR file (or a path to it on this machine).
Not fabricated or substituted here -- flagged back to the user instead,
per this file's standing discipline throughout every prior round.

## The real Tikamgarh run (2026-11-05)

The user supplied the real file: `DPRData/Approved DPR /TikamgarhDPR.pdf`
(910,758 bytes, 23 pages, sha256 `0b335886...`). Ran the real production
code path end to end -- `jobs.submit()` -> `upload_staging.stage_upload()`
-> `scripts/dpr_worker.py`'s real `process_one()` -> a real
`GeminiAgentClient` against the real Gemini API (no respx, no mock, no
FakeLLM) -- with `cam_engine_enabled=True`, via the new, one-off
`scripts/run_tikamgarh_real.py`. The vendor (iWallet) was never contacted
(this worker path never calls it, by design). Written under a clearly
fake `service_request_id`/`source_reference`
(`TEST-TIKAMGARH-REAL-RUN:GEO_CHECK`), never a real customer id.

### A real migration bug found and fixed first, before the run could proceed

`apply_migrations()` failed against the real database (`LASUAT`, the one
named by `.env`'s `DPR_DATABASE_URL`) on its first attempt:
`UniqueViolation: could not create unique index
"dpr_report_artifacts_source_version_key" ... Key (source_reference,
version)=(wallet-person-custom-field:9000000013:GEO_CHECK, 1) is
duplicated.` Migrations run in one transaction
(`migrate.py`'s `with engine.begin()`), so this rolled back cleanly --
nothing was left half-migrated, and no real data was at risk.

**Root cause**: migration 006 rescopes `version` from per-`run_id` to
per-`source_reference`, but its backfill copies each pre-006 row's old
version number verbatim (always `1`, since the old PK was
`(run_id, version)`). Two real, legitimate `COMPLETED` runs already shared
one `source_reference` in `LASUAT` (from real work on 2026-11-03/04,
confirmed by reading the actual rows, read-only, before touching
anything) -- exactly the "re-analysis after corrections, new `run_id`,
same version history" case migration 006 exists to support. Backfilling
both to version `1` collided on the very constraint meant to make that
case work. This had never surfaced before because every prior round's
migration tests only ever ran against a freshly created database with at
most one run per `source_reference`.

**Fixed** in migration 006 itself (never a follow-up migration file: it
had not successfully applied anywhere with this data shape, so there was
nothing already "in production" to preserve) -- a `ROW_NUMBER() OVER
(PARTITION BY source_reference ORDER BY version, created_at, id)` pass
renumbers every pre-006 row sequentially within its source_reference
group, oldest first, before the uniqueness constraint is added. Verified
against a reproduction of the exact real collision (two `COMPLETED` runs,
same `source_reference`, both originally version 1, one an hour older) in
a throwaway Postgres: the older run correctly becomes version 1, the
newer version 2, migration succeeds, and a second `apply_migrations()`
call is a confirmed no-op (idempotent). Full suite re-run green again
after this change (fresh throwaway Postgres has no pre-006 rows, so the
new step is a harmless no-op there).

### The real run's results

- **Upload-to-report time**: **367.7s (~6.1 minutes)**, end to end
  (`dpr_upload_to_report_ms`) -- this is the real number the earlier
  synthetic measurement (11.3s, infra-only) explicitly said it could not
  provide. Dominated by the 11 real Gemini calls (1 extraction + 1
  `generate()` per of 10 orchestrator roles) over a 23-page document, run
  sequentially.
- **Headline ("golden") figures** -- reporting the 9 figures this
  pipeline actually treats as headline outputs (project cost, means of
  finance, DSCR and break-even), not a predefined list of nine found
  anywhere in the code, since none exists:
  | Figure | Value |
  |---|---|
  | Project cost (DPR-reported total) | Rs.30.0 lakh |
  | Project cost (recomputed total) | not available |
  | Requested loan | Rs.22.5 lakh |
  | Promoter contribution | Rs.7.5 lakh |
  | Proposed tenure (months) | not available |
  | Average DSCR | not available |
  | Break-even (accounting) | not available |
  | Break-even (operating cash flow) | not available |
  | Break-even (debt-service threshold) | not available |

  Cost-of-project and means-of-finance each reconciled exactly (variance
  `0.0` against their own stated totals) -- the four "not available" DSCR/
  break-even/tenure figures are real extraction gaps, not a bug: reading
  the stored `cam_engine_outputs` directly confirms `tax_rate`,
  `year1_principal_due`, `year1_interest_due` and
  `proposed_tenure_months` all came back `null`, meaning this real
  document does not state (or the extraction did not recover) the
  interest rate, repayment schedule or loan tenure needed to compute
  DSCR/break-even -- consistent with 41 `unresolved_questions` logged for
  this run.
- **Findings**: 43 total -- 6 critical, 15 high, 19 medium, 2 low, 1 info;
  by priority, 6 must-fix, 15 should-fix, 22 good-to-fix. Readiness status
  would be `EXPERT_REVIEW_REQUIRED` (critical findings present).
- **Verification checks**: this pipeline runs **12** checks
  (`cam_verification._ALL_CHECKS`), not 13 -- correcting the number here
  rather than padding the report to match. Result: **`is_publishable=True`,
  0 issues** (0 blocking, 0 warning) against the real snapshot.
- **Missing-state count per page** (counting "not available"/"not yet
  checked"/"not computable"/"requires verification"/"insufficient
  information" strings in each page's own render context): **page 1: 2,
  pages 2-6: 0**. The two on page 1 track directly to the DSCR/break-even
  gap above (Page 1's repayment-capacity summary has nothing to show).
- **Annexure completeness** (`build_annexures()` on the real snapshot):
  every annexure returned its full real content where the pipeline has
  any, and its already-documented `missing` list elsewhere -- **A: 5
  missing, B: 3, C: 3, D: 5, E: 4, F: 3, G: 3** (26 total), unchanged from
  the audit above, since a single real document run does not by itself
  populate balance sheets, policy statements, or any of the other
  structurally-absent fields -- confirming the audit was accurate, not
  just plausible-sounding.
- **One new real finding from this run, not previously known**: the PDF
  render **timed out and failed**
  (`dpr_cam_pdf_render_timeout`, `pdf_render_status=FAILED` on the stored
  row) at the default 120-second cap -- the JSON report itself rendered
  and stored fine (`render_status=RENDERED`, 152KB). 43 real findings is
  far more than any fixture this pipeline has been tested against, and
  WeasyPrint's render time scales with content -- a real signal that
  `DPR_CAM_PDF_RENDER_TIMEOUT_SECONDS` (default 120s) may be too tight for
  a real, finding-heavy document in production, not just a theoretical
  concern. Not changed here (a production tuning decision, not a bug fix
  this task asked for) -- flagged for the user's call.
- **Gemini model actually used**: `gemini-3.8-flash` (captured via this
  round's `AnalysisResult.gemini_model` plumbing -- confirms that fix
  works against a real call, not just the schema).

### Status: milestone 7 complete

All three of the user's asks for this round are done: `process_one()`
hardened and tested, all seven Section 12 annexures built and audited,
and the one approved real Gemini run completed end to end with results
reported above -- including one real migration bug and one real PDF-
timeout-at-scale finding that only a real run against real data and a
real document could have surfaced.

## Production hardening, M1: analysis correctness fixes (2026-11-12)

Plan: `/home/islam/.claude/plans/plan-the-recommended-changes-humble-bee.md`
(milestones M0-M6). M1 was done first because it needs no real DPRs or
live Gemini calls; M0 (the evaluation harness) is next.

### Baseline: 9 stale DPR tests fixed first

Before any change, 9 DPR tests were failing: they had fallen behind the
uncommitted engine work in the tree, and none was a behaviour bug.
- `conftest._fail_on_real_gemini_client` did not accept the new
  `thinking_level` / `max_output_tokens` arguments.
- Fakes returning empty financials now take the early-stop path, so they
  return a minimal `cost_of_project_stated_total`.
- The usage-log assertion now checks only its own keys.

The 2 `test_stub_wallet_client` failures are unrelated and still open.

### Fixes

1. **Tolerance in the wrong unit.** `financial_calc.TOLERANCE` was 1.0,
   labelled "rupees", but every amount is in lakhs, so cost and funding
   gaps up to Rs 1,00,000 passed as matches. It is now 0.01 lakh
   (Rs 1,000).
   - The fund-flow check used the 1-lakh `MATERIAL_GAP_TOLERANCE`, so it
     now uses `MONEY_TOLERANCE`.
   - The acreage check has its own `ACREAGE_TOLERANCE`, in acres.
2. **DSCR falls back to `repayment_table`.** When `years[]` lacks
   principal or interest, `dscr_per_year()` uses the schedule's extracted
   cells, each figure independently and never a guess.
3. **Fund-flow totals are now extracted.** They were only in the retired
   monolithic schema, so `_fund_flow_finding` never ran in production. They
   are now in the `cost_funding` group schema and prompt (9 keys, 1,738
   characters; the size guard test now allows up to 10 keys).
   - **Still open, in M3:** `harvester_assumptions` is likewise never
     extracted, so the fuel, crew, acreage and scenario outputs are still
     empty on real runs.
4. **Extraction audit.**
   - The audit's `empty` list now names only groups still incomplete after
     their retry, where it used to also name recovered groups.
   - New `AnalysisResult.extraction_audit` (default `None`) holds the
     structured record.
   - Deviation from the plan: `FINANCIAL_EXTRACTION_EMPTY_RESPONSE` was
     *not* added to the readiness prefixes. Missing sections already raise
     `FINANCIAL_SECTION_INCOMPLETE`, so adding it would double-count.
5. **Extraction runs in two concurrent phases.** All first attempts run at
   once, then retries at once. Retry selection is in fixed `GROUPS` order
   (deterministic), and latency is at most 2 Gemini round trips instead of
   up to 6.
6. **Gemini resilience.**
   - `_post_with_retries` retries 429, 5xx, timeouts and connection errors
     (3 attempts, randomised exponential backoff capped at 20 s). 4xx
     errors are not retried.
   - `_structured` retries a truncated or schema-invalid response once.
   - Exhausted transport attempts raise `GeminiTransportError`, which is
     never retried at the content level.
   - Optional roles (`OPTIONAL_ROLES`: planner, sector, correction,
     research, policy) degrade to a `ROLE_UNAVAILABLE: <role>` output.
     Document, finance, risk and verifier still fail the run.
7. **One pipeline.** `orchestrator.analyze()` now delegates to
   `parallel_graph.analyze_parallel()`, so the internal endpoint, scripts
   and tests run exactly what the worker runs.
8. **Dedup bug found while regenerating the Tikamgarh fixture.**
   `compute_dedup_key` matched topic needles anywhere in title plus
   explanation, which caused two problems:
   - The calculated acreage finding and its narrative restatement got
     different keys, so the report showed the issue twice.
   - Unrelated findings that mentioned "operating days" in passing would
     over-merge.

   A topic now also needs one of its needle words in the title.
   - The fixture was regenerated: 6 findings, with acreage merged from 5
     copies (4 narrative plus the calculated one).
   - The other fixture diffs (dedup key format, `financial_projections`)
     come from the uncommitted work already in the tree.

New tests: `test_dpr_financial_extraction_phases.py`,
`test_dpr_gemini_retries.py`, `test_dpr_role_degradation.py`, plus cases in
`test_dpr_financial_calc.py`, `test_dpr_cam_engine_bridge.py` and
`test_dpr_finding_consolidation.py`.

Full suite: 699 passed, 103 skipped (Postgres), 2 failed (the
pre-existing stub-wallet tests).

## Production hardening, M0: evaluation harness (2026-11-12)

The data is the real DPR set at `../DPRData`: 9 in `Approved DPR /` and 6 in
`RejectedDPR/`. Five of the rejected harvester DPRs share one 21-page
template. The data is read in place and never copied into the repo.

### What was built

- **`document_input.prepare_document()`**: the worker's parse, vision-OCR
  and classify step, moved out of `scripts/dpr_worker.py` so the harness
  measures exactly what production runs.
  - The worker passes in its own module-level `inspect_document`,
    `render_pages_to_images` and `extract_pages_via_vision`, so tests that
    patch those names still work.
  - The exception names (`NeedsVisionFallback`, etc.) are unchanged.
- **`evaluation.py`**: offline scoring. It covers:
  - per-field extraction accuracy (0.5% or 0.01-lakh tolerance)
  - must-find recall and must-not-find violations
  - run-to-run stability (finding-key and must-fix Jaccard, identical
    figures)
  - tokens, INR and latency per role, using indicative prices that can be
    overridden
  - agreement with the bank's label (a rejected DPR should have at least
    one must-fix)
  - first-draft annotations
- **`gemini_cassette.py`**: HTTP-level record/replay keyed by the sha256 of
  the model plus the canonical request body. A replay miss is a hard
  `CassetteMiss`.
- **`scripts/dpr_eval.py`**: the runner. Without `--yes` it only lists
  documents and the estimated cost. Modes are `--record`, `--replay`,
  `--repeat N`, `--model`, `--only` and `--draft-annotations`.
- **Privacy:** reports and annotations name documents by sha256 prefix
  only. `eval/results/`, `eval/cassettes/` and `eval/manifest.local.json`
  are gitignored; cassettes hold DPR text.

### Offline parse check of all 15 DPRs (no Gemini calls)

- **9 parse fully** and classify as DPR (0.97).
- **5 have 1-2 weak pages**, which go to Gemini vision.
- **The scanned approved DPR (`00437a63adb1`, 26 pages) yields no text from
  LiteParse:** Tesseract is not installed on this host, so local OCR
  silently produces nothing and all 26 pages would go to Gemini vision.
  Install `tesseract-ocr` (plus `tesseract-ocr-hin` for Hindi) wherever the
  worker runs.

### Worker tests now actually run

Running the suite against a throwaway Postgres (the `docker run` line in
`tests/conftest.py`) un-skipped 104 tests. Seven of them were broken by
uncommitted work and nobody had seen it:
- Parse stand-ins lacked `low_text_pages`.
- Vision tests staged `b"dummy"`, which the new page-cap check can't open as
  a PDF; they now stage a real one-page PDF.
- The run-id test expected the old 10 LLM calls.

All seven are fixed. Full suite: 813 passed, 2 failed (the pre-existing
stub-wallet tests).

### Not yet done (needs the user)

- **First live run.** It needs approval for paid Gemini calls: about INR 15
  per run, measured per run in the report.
- **Annotation review** by a credit person, after `--draft-annotations`.

### First live evaluation: 4 DPRs, 3 runs (2026-11-12)

The runs covered approved `912a423f65cc` and `0b335886014b`, and rejected
`d19f63b7d0ab` and `e3aae76bbdce`, all on `gemini-3.8-flash`.
- **Cost:** about INR 9-19 per DPR (indicative prices); the three runs cost
  about INR 115 in total.
- **Latency:** 125-215 s per DPR (the earlier Tikamgarh run took 367 s).

**Fixed from these runs:**
1. **Fund-flow priority.** An unbalanced fund-flow statement is now
   good-to-fix. The bank's reference CAM template (page 2) rates this exact
   case "Good to fix: Add closing cash", while M1's newly enabled check had
   made it must-fix on a DPR the bank approved. The golden test was updated,
   and the page-6 tests now expect 4 must-fix (fuel, crew, acreage,
   quotation).
2. **Year numbering.** One call labelled the P&L by calendar year
   (2026-2033) while another used 1-8, so DSCR and the merge failed. The
   prompts now require 1..N, and `normalize_year_numbers()` renumbers each
   extraction domain before merging.
3. **Monthly repayment schedules.** One DPR prints an 84-row monthly
   schedule in rupees. Asking for yearly lakh totals made Gemini spend
   3.9k, and then 7.8k, tokens thinking and still truncate.
   - Gemini now transcribes the rows as printed (`repayment_instalments`
     plus `repayment_amount_unit`).
   - `aggregate_repayment_instalments()` sums them in code: positive
     principal only, interest = instalments - principal, no row for a
     moratorium year.
4. **Truncation.** Truncated calls are billed but went unlogged; they now
   count toward usage and cost. A retry after `MAX_TOKENS` doubles the
   output cap (`GeminiTruncatedError`).
5. **DSCR interest fallback.** DSCR uses the P&L's interest on the term loan
   when no separate interest-repaid figure was extracted. Recomputed on the
   stored Tikamgarh extraction: 2.31x against the DPR's stated 2.02x.

**Open:**
- **`d19f63b7d0ab`'s repayment extraction still truncates** when asked only
  to transcribe the monthly rows. Next option: parse that table in code
  from the page text.
- **The 4,096-token role caps are too tight on Gemini 3**, because thinking
  shares the budget: 5 first-attempt truncations in run 3 (sector, risk,
  operating). Raising them invalidates the recordings.
- **One issue restated as several critical findings** (2-3x, with invented
  categories): needs M2 (verifier as judge, category enum).
- **28-30 unresolved questions per DPR**, mostly from the planner.
- **Large run-to-run variance.** Tikamgarh went from 0 to 3 must-fix
  between runs; measure with `--repeat`.
- **Replay only covers changes that leave Gemini requests unchanged.** A
  calculation change alters `calculated_facts` in the agents' context.
- **Draft annotations** for the 4 DPRs are in `eval/annotations/` and are
  unreviewed.

## Production hardening: Gemini 3 caps, schedule parser, M2 verifier (2026-11-12)

### Changes

1. **Gemini 3 output caps.** Thinking tokens count against
   `maxOutputTokens`. The role caps now size the visible answer, plus a
   per-level thinking allowance: LOW +4,096, MEDIUM +8,192, HIGH +16,384.
   The ceiling is 32,768, and a truncation retry still doubles the cap.
   - Note: `.env` sets `DPR_AGENTIC_THINKING_LEVEL=medium`, which overrides
     the per-role levels (extraction roles were designed for LOW). Removing
     it is recommended.
2. **`schedule_parser.py`** reads per-instalment repayment schedules in
   code.
   - Rows are trusted only when closing = opening - principal and
     instalment = interest + principal (to within Rs 1), and the table is
     accepted only if at least 95% of rows pass.
   - On the 15 real DPRs it reads all 5 rejected harvester schedules (84
     rows each); the yearly totals match the DPR's own "Total in Rs." lines.
     It returns nothing for the other 10.
   - When it succeeds, Gemini is told not to transcribe the table.
3. **The verifier is now the judge (M2).**
   - A `candidates` code node consolidates and ids every analyst finding
     plus the calculated ones.
   - The verifier returns `verdicts` (keep / reject / merge / downgrade /
     needs_clarification).
   - `finding_judge.apply_verdicts()` enforces the guardrails: calculated
     findings are never rejected or downgraded, unknown or self merges are
     ignored, silence means keep, and severity can only be lowered.
   - Rejected findings go to `AnalysisResult.rejected_findings` with a
     `rejection_reason`.
   - Correction's findings no longer reach the customer list.
   - Planner agenda questions are not customer questions.
   - `FINDING_CATEGORIES` is now an enforced enum in the agent schema.
4. **Consolidation.** A narrative finding whose title is near-identical
   (word Jaccard of at least 0.75) to a calculated finding merges into it
   across categories.
5. **P&L completeness.** The operating-financials group needs net profit
   and depreciation, and `profit_and_loss` is now a reported missing
   section. The prompt forbids summing lines or copying PAT into PBT.
   - Seen live: one run returned revenue only, and another reported opex
     11.3, a self-computed sum, where the page prints "TOTAL OPERATING COST
     15.30".
6. **Evaluation metric.** Stability is now measured as *topic agreement*
   (greedy title-word matching at 0.4), with exact keys kept as a strict
   lower bound.

### Live repeat run: 4 DPRs x 2, before items 4-6 (INR 114)

- **No extraction failures in 8 runs**, against one failed DPR in every
  earlier run. 2 truncations, both recovered by the retry.
- **Fewer findings and questions:** 9-23 findings per run (was 24-36), with
  the verifier merging 3-7 per run, and 21-24 questions (was 28-30).
- **Both rejected DPRs now get at least one must-fix;** before, only 1 of 2
  did.
- **Missing quotation** appears as must-fix in every run of every DPR. This
  matches the reference template's must-fix #5.
- **Run-to-run agreement is still low.** Topic agreement is 0.15-0.47 for
  all findings and 0.0-1.0 for must-fix, and severity drifts (Tikamgarh had
  5 high findings in one run and 0 in the other).
- **Extracted figures** were identical in only 50-100% of fields; items 5
  above address the causes found.
- **Cost is about INR 12-17 per run** (verifier context plus larger caps).

Items 4-6 are not yet measured live.

### Live re-measurement and verifier-restatement fix (2026-11-12)

`DPR_AGENTIC_THINKING_LEVEL=medium` commented out in `.env` (per-role
levels now apply: extraction LOW). Then `dpr_eval.py --repeat 2` on the same
4 DPRs (`eval/results/20261112T121146Z`, gitignored):

| | Previous run | This run |
|---|---|---|
| Total cost (8 runs) | INR 114 | INR 75 (~INR 9.4/run) |
| Output tokens per doc (2 runs) | 58-79k | 37-47k |
| Mean latency | 194 s | 128 s |
| Extraction failed/empty/missing sections | 0 | 0 |
| Figures identical across runs | 0.5-1.0 | 1.0 on 3 docs, 0.91 on d19 |
| Topic agreement (all) | 0.15-0.47 | 0.24-0.60 |
| Must-fix topic agreement | 0-1.0 | 0-1.0 (d19's 0.0 is a metric miss: both runs' must-fix is the vendor quotation, worded differently) |

Rejected DPRs flagged with a must-fix: 2/2. Missing vendor quotation is
must-fix in all 8 runs.

Duplicates still reached the report (same issue twice per run): the
verifier, asked only for new issues, restated kept candidates in its own
`findings`, under a topic key, with no page, or reworded. Fixes:
- `finding_judge.absorb_restatements`: a verifier-new finding in the same
  category, compatible page, title similarity >= 0.5 (stop words removed,
  light stemming: `finding_consolidation.title_words`) folds into the kept one.
- `consolidate`: same category + compatible page (one may be None) + same
  issue topic or the same normalised title merges, whatever the keys.
Re-applied offline to the stored 8 runs: findings 8->7, 8->7, 17->16,
20->19, 8->8, 9->7, 17->15, 17->16; no distinct must-fix lost (e3aa r1's
doubled vendor-quotation must-fix merged).

### Stability step 1 + M3 unit economics (2026-11-12, offline)

**Vision OCR reuse** (`ocr_cache.py`, migration `011_dpr_ocr_cache.sql`).
Gemini reads a scanned page slightly differently each call; the text is now
kept per (document sha256, page, OCR model) and reused when the same file
is analysed again. Worker: Postgres, Fernet-encrypted with the staging key,
`DPR_OCR_CACHE_TTL_DAYS` (default 30; 0 = never stored), purged on each
poll. **This keeps document text after a run ends** (staged bytes are still
deleted at terminal state) -- a retention decision to confirm. Eval:
`eval/ocr_cache/` (gitignored). A cache error never fails OCR.
Note: a fully scanned DPR longer than `DPR_AGENTIC_VISION_MAX_PAGES` (20)
is refused when LiteParse OCR (Tesseract) is unavailable -- the scanned
26-page DPR in the eval set hits exactly this.

**d19 figures differed between runs** (principal row read in run 1, not run
2), and **its DSCR was never computed in either**:
- DSCR add-back interest falls back to the interest serviced when the P&L
  interest line was not extracted (`financial_calc.dscr_per_year`).
- The repayment group is complete only with principal; otherwise one retry
  with `REPAYMENT_RETRY_HINT` (no retry when the code-read schedule
  supplies it).
- Moratorium years (principal 0) get no DSCR (was 6.69 vs a printed 0.00).
- **Policy change, per the bank's reference CAM template** ("Non-standard
  repayment formula: Year 1 is 1.97x, not 1.58x" = should-fix): one finding
  "Stated DSCR does not follow the standard formula" (`dscr_stated_differs`,
  should-fix; high severity when the stated DSCR is ABOVE the standard one),
  naming the formula the DPR used when one reproduces it (principal-only
  denominator / no interest add-back). Per-year DSCR findings stay info.
  Offline on the stored runs: 912a 1.97 vs 1.58 and 0b33 -> should-fix
  (no interest add-back); d19 run 1 -> high, principal-only (stated 3.28,
  standard 2.27); e3aa matches, no finding.

**M3: unit economics** (`unit_economics.py`). Two optional extraction
groups (attempted only when their pages exist, never retried, never
reported missing): `unit_economics` (unit, capacity/day or /year, operating
days, price and variable cost per unit in rupees, per-year utilisation or
units sold) and `harvester` (only when the DPR names a harvester; fills
`harvester_assumptions`, so cam_engine_bridge's fuel / crew / acreage checks
finally get inputs -- they were never extracted by the grouped pipeline).
Every unit-economics figure must appear on its cited page or is dropped
(`verify_unit_economics_pages`). Checks, all calculated findings:

| Check | Priority |
|---|---|
| units_exceed_capacity (skipped when the harvester acreage check ran) | impact rule (>=5% -> must-fix) |
| year1_full_utilisation (>=90%; template must-fix #4, was "Not modeled") | must-fix |
| revenue_rebuild (units x price vs stated, >2%) | must-fix if stated is >=5% above, else should-fix |
| negative_contribution (price <= variable cost) | must-fix |
| operating_days_idle (<=90 days; template should-fix #9, was "Not modeled") | should-fix |

Thresholds are named constants in `unit_economics.py` -- for credit review.
The sector agent now sees these checks and is told to judge the inputs'
realism instead of re-deriving them. The CAM's operational assumptions
(Annexure D) are filled from the extraction. Offline: 14 of 15 eval DPRs
have pages for the unit-economics group, 11 are harvester DPRs. Expected
cost +INR 1-2 per DPR (two Flash calls). Not yet measured live.
Tests: 874 passed with Postgres (2 pre-existing stub-wallet failures).

### Stability step 3: fixed per-role checklists (2026-11-12, offline)

`checklists.py`: document (4 items), finance (7), sector (5) and risk (4)
answer a fixed list (`checks`: issue / ok / not_applicable / cannot_tell,
with evidence). Code turns each "issue" into a finding whose title,
category, severity and priority come from the item (`Finding.check_id`);
free-form findings are capped at 3 per role (most severe kept). Items cover
the reference template's judgement issues (#5 quotation, #6 one machine
without breakdown cover -> must-fix, #7 break-even basis, #8 single booking
channel, #12/#13 running costs incl. off-season upkeep) plus defects seen in
the eval set (interest missing from P&L, statements disagreeing, working
capital, unsanctioned subsidy as funding). Arithmetic stays in code.
Guards: the verifier may reject a checklist finding (evidence) but not
downgrade it; consolidation prefers a checklist finding over a free-form
restatement and keeps its severity. Evaluation: new stability measures
`checklist_answer_agreement` and `checklist_flagged_jaccard` (report column
"Checklist (answers / flagged)"). Item ratings are for credit review.
Tests: 882 passed with Postgres (2 pre-existing stub-wallet failures).
Not yet measured live.

### Live run with checklists, and fixes (2026-11-12)

`dpr_eval.py --repeat 2`, same 4 DPRs (`eval/results/20261112T132005Z`):
INR 131 (~16/run, up from ~9.4), latency 233 s. Topic agreement rose to
0.37-0.76 (was 0.24-0.60), must-fix topic agreement 0.67-1.0, figures
identical 0.89-1.0. But: rejected e3aa had no must-fix in either run, and
912a / e3aa r1 had no quotation must-fix. Causes and fixes:

- **The verifier merged checklist findings into free-form duplicates**, so
  the must-fix quotation and one-machine items took the free-form one's
  title and should/good-to-fix rating. `finding_judge._anchor_rank`: on a
  merge, calculated beats checklist beats free-form; two calculations or
  two checklist items never merge; a checklist finding's severity is never
  changed by an absorbed duplicate.
- **"cannot_tell" on the quotation item** (912a both runs, e3aa r1): the
  agent is given every page mentioning a quotation, so for a presence item
  (`ChecklistItem.absence`) cannot_tell now means missing; the prompt says
  so too.
- **Free-form findings are capped at should-fix** (`priority_for_narrative`).
  Must-fix now comes only from code checks and checklist items -- which
  together cover all six of the reference template's must-fix items.
  Free-form "missing document"/"critical" must-fixes were the ones that
  differed between runs.
- **DOC_MISSING_PAGES removed**: the verifier rejected it in 5 of 8 runs
  (the document agent sees only some pages, so "missing" was an artefact).
- **Non-checklist roles invented their own `checks`** (the field was in
  every role's schema): `CHECKLIST_OUTPUT_SCHEMA` is used only when the
  context carries a checklist; the stability metric counts only real items.
  Re-scored on this run: checklist answer agreement 0.79-0.90.

**Correction to the d19 DSCR entry above.** d19's "TL Installment 4.13" is
the full yearly instalment (principal + interest; it equals the monthly
schedule's instalment total), not principal. The DPR's 3.28 = 13.54 / 4.13
is correct; the "principal-only, standard 2.27" finding came from the
model copying the instalment row into principal_repayment. Fixes:
`YearFinancials.total_instalment` (prompt: a whole-instalment row never goes
in principal); DSCR on an instalment = (PAT + dep + interest) / instalment;
and `yearly_rows.py` reads the DSCR-statement rows (instalment, principal,
interest on TL, stated DSCR) in code when LiteParse gives "label v1..vN"
lines on a DSCR/repayment page -- code-read rows replace the model's, and a
model principal equal to the instalment row is cleared. Offline on the 4
DPRs with the stored runs: DSCR identical across runs on 3, within 0.01 on
0b33; 912a still 1.97 vs 1.58 (should-fix, as the template); d19 no false
finding. The principal-only detection remains for DPRs that really divide
by principal alone.
Tests: 892 passed with Postgres (2 pre-existing stub-wallet failures).

### Live run after the checklist fixes (2026-11-13)

`eval/results/20261113T054406Z`, same 4 DPRs x 2: INR 129 (~16/run),
latency 218 s, 0 extraction failures. Both rejected DPRs flagged with
must-fix in every run. Must-fix lists identical between runs on 912a, 0b33
and d19 (must-fix topic agreement 1.0); e3aa 0.667. Topic agreement
0.64-0.81 (was 0.24-0.60 before the checklist work); checklist answer
agreement 0.68-0.95; figures identical 1.0 on three DPRs, 0.91 on d19.
Missing quotation must-fix in all 8 runs; one-machine must-fix in 5 of 6
runs of the DPRs where it applies.

Fixed after the run (offline, tested):
- e3aa r2 answered RISK_SINGLE_ASSET "cannot_tell -- no DPR source pages
  were provided": the risk agent got only other agents' summaries. It now
  gets up to 6 operations/cost/quotation pages (3,500 chars each).
- d19 r1: the model derived principal (instalment - interest) itself; when
  code reads an instalment row and no principal row, model principals are
  cleared (yearly_rows.apply_yearly_rows).
- Template must-fix #4 (full capacity from year 1) was missed on 912a/0b33:
  they print 1200 acres, Rs 1800/acre and year-1 revenue 21.6 lakh but no
  utilisation row. unit_economics now implies year-1 volume = revenue /
  price (utilisation only, never the revenue rebuild). Replayed on the
  stored runs: fires in both runs of 912a and 0b33, not on d19/e3aa.

Still open: acreage/fuel/crew checks cannot run on these DPRs -- they do not
print acres/day, days/season or litres/acre (the reference CAM judged them
against external benchmarks: M5 research). Fund-flow totals extracted in
one d19 run only. Cost ~INR 16/run: checklist answers carry explanations
for every item, not only issues.

### Confirmation run and cost cut (2026-11-13)

Before the run: the new extraction groups lacked per-role entries, so they
ran at MEDIUM thinking, uncapped (~7k output tokens per call); now LOW and
4096 (`gemini._ROLE_THINKING_LEVEL/_ROLE_OUTPUT_CAP`). Checklist answers
carry explanation/evidence only for "issue". Sector/risk visible cap 6144
(a truncated sector answer had been re-asked in full at double the cap).

`eval/results/20261113T062253Z`, same 4 DPRs x 2: **INR 111 (~14/run, was
16)**; the two extraction groups now 14-870 output tokens per DPR (was
~24k); mean latency 195 s; 0 extraction failures. Both rejected DPRs
flagged in every run; must-fix lists identical between runs on 0b33, d19,
e3aa (quotation + one-machine on all; 0b33 also full capacity from year 1);
checklist answer agreement 0.79-1.0; figures identical 0.91-1.0; topic
agreement 0.64-0.74.

912a differed: (1) no "full capacity from year 1" in either run -- the
model filed the DPR's 1200 planned acres under capacity in the previous run,
nowhere in run 1 and as units sold in run 2. `full_capacity_per_year` now
falls back to the harvester group's acres per year (read in every run) and
then to the largest printed per-year volume; with no generic unit economics,
one is built from the harvester rate and running cost per acre. Replayed on
the stored 16 runs of the last two rounds: fires in all 8 runs of 912a/0b33,
none of d19/e3aa. (2) RISK_SINGLE_ASSET "ok" in one run, "issue" in the
other: rephrased as a presence check (does the DPR show breakdown cover --
insurance and a service contract or back-up?), marked `absence`, so
"cannot_tell" means not shown.

### M5: Policy and Research agents with Google Search grounding (2026-11-13, offline)

`grounding.py`. Research and policy no longer return the fixed "not
verified" stub when `DPR_AGENTIC_GROUNDING_ENABLED` (default true): each runs
(1) `GeminiAgentClient.grounded_search` -- Gemini with the `google_search`
tool, plain text plus `groundingMetadata` -- then (2) `structure`, which
turns the answer into statements citing numbered *supported* segments.
Code keeps a statement only if it cites a segment Google attributes to a web
source; drops eligibility/approval/sanction wording; tiers each source by
domain (official: *.gov.in, *.nic.in, RBI, NABARD, SIDBI, public-sector
banks' sites; else secondary).

**Privacy:** the search brief is built by code (`build_search_brief`) from
bank, scheme claimed, sector/subsector, **state only** (never village,
district or address), a loan band and the DPR's operating figures; borrower-
name words are stripped from every free-text field. Checked offline on the 4
eval DPRs' stored facts: no borrower-name word in any brief.

**Bank name:** the DPR Analysis service detail `BankName`, now staged with
the upload (`dpr_upload_staging.project_context_ciphertext`, Fernet, migration
012) and passed as `AnalysisInput.project_context` (it was always empty);
else the new document fact `bank_name`.

**Outputs:** `AnalysisResult.external_evidence`. Official policy statements
-> Annexure F `PolicyAssessment.statements` (status "bank confirmation
pending", source URL, retrieval date) plus a `POLICY_CONFIRMATION:` question
each (max 4); non-official ones -> unverified policy areas. Research
statements -> Annexure D "Market evidence". Benchmarks (metric + range)
let code check the DPR: annual acreage vs benchmark acres/day x operating
days (template #1, which these DPRs cannot be checked for internally) and
rate per unit vs the market range -- should-fix at most, sources named.

Cache: `dpr_grounding_cache` (migration 012) / `eval/grounding_cache/`, key =
hash of the brief without the DPR's own figures, TTL
`DPR_GROUNDING_CACHE_TTL_DAYS` (7); purged by the worker poll. A failed
search degrades the role (`ROLE_UNAVAILABLE`); a client without
`grounded_search` runs without grounding. Search queries are logged per call
and priced in the evaluation (USD 0.014/query, indicative).

Not yet measured live. Note: Google's grounding source URLs are redirect
links (vertexaisearch...), the domain is in the title; they may expire.
Tests: 911 passed with Postgres (2 pre-existing stub-wallet failures).

### First live run with grounding (2026-11-13)

`eval/results/20261113T072743Z`, same 4 DPRs x 2: **must-fix lists
identical between runs on all 4 DPRs** (must-fix topic agreement 1.0), both
rejected flagged every run; 912a now has quotation, one-machine and full
capacity from year 1 in both runs. Topic agreement 0.67-0.88, checklist
answers 0.84-0.90. Policy search returned 18-26 statements per DPR, 2-16
from official sources (e.g. AIF 3% interest subvention up to Rs 2 crore --
dac.gov.in, pib.gov.in); scribd/apnikheti etc. correctly tiered secondary.
Cost INR 228 (~28/run) -- too high, because:
- **research structuring looped to its output cap** on 912a/0b33 (8-16k
  tokens from a 1.5k-token prompt, retried at double): research
  unavailable there, so no benchmark check ran. Fixed: at most 15
  statements (`maxItems` + prompt); on a structuring failure the supported
  sentences themselves are kept as statements (sources intact, no metrics).
- **the cache missed between runs**: the brief changed with the document
  agent's wording (scheme "AGRI INFRA" vs "Agriculture Infrastructure Fund
  AIF"; sector present in one run). Fixed: schemes and banks mapped to
  canonical names in code (unrecognised schemes are omitted, not sent as
  free text); a harvester DPR's sector/subsector come from the extraction.
  Offline on this run's facts: r1 and r2 briefs now identical on all 4 DPRs.
- `.bank.in` (RBI's bank domain) added to official sources.

### Second grounded run, and the one-call redesign (2026-11-13)

`eval/results/20261113T081823Z`: must-fix lists identical between runs on all
4 DPRs again; the cache worked (all 8 second-run searches were hits, so the
evidence was identical between runs); INR 174 (~22/run). But the
structuring call still looped to its cap on research (~24.5k output tokens
per DPR despite maxItems 15); the fallback kept the sentences, so no
benchmark figures, and once produced junk ("capacity 0.0").

Redesign (offline): **no structuring call**. Statements are the sentences
Google attributes to a source (markdown table rows flattened to plain
text), official first, max 15. Research ends its answer with
`BENCHMARK: metric = low-high unit` lines; code keeps one only if both
figures appear in a source-attributed sentence, whose sources it carries.
One Gemini call per agent instead of two. `.ac.in` / `.res.in`
(agricultural universities, ICAR) count as official. `GROUNDING_VERSION` is
part of the cache key, so results cached under an older design are never
reused.

Checked offline on the real recorded research answers from this run: the
sourced sentences carry the template's benchmarks (8-15 acres/day, 20-30
days/season, 2 seasons, 500-900 acres/year from tnau.ac.in); with BENCHMARK
lines in that form, 912a gets "The DPR plans 1200 acres a year. Published
benchmarks give at most 15 acres a day over about 60 operating days, i.e.
about 900 acres a year" -- the reference template's must-fix #1, here
should-fix because it rests on external evidence. Not yet measured live
(the old answers have no BENCHMARK lines).

### Live run with one-call grounding (2026-11-13)

`eval/results/20261113T085125Z`, same 4 DPRs x 2: **INR 159 (~20/run)**,
latency 201 s. BENCHMARK lines work: sourced benchmarks on all 4 DPRs (912a:
8-15 acres/day, 35-50 days/season, 2 seasons, Rs 1600-2400/acre), cached
identically for run 2 (8/8 cache hits); 13-21 official statements per DPR
(policy 12-15 of 15 official). Must-fix lists identical between runs on all
4 DPRs; topic agreement 0.73-0.78 (best so far); checklist answers
0.79-0.95; figures identical 0.90-1.0.

The acreage check did not fire: it compared with the top of every range
(15 x 100 = 1500 acres). Now it compares with the typical (mid-range)
figure and states both; on this run's evidence 912a -- the bank's
reference DPR -- gets "plans 1200 acres a year ... typically 11.5 acres a
day over about 85 operating days, i.e. about 978 acres a year (1500 at the
top of the published ranges)", the template's must-fix #1 (should-fix
here: external evidence). 0b33, same 1200-acre plan, was not flagged: its
research brief (with bank, scheme, state) found wider ranges. Research now
searches with its own brief -- sector, subsector, state, operating figures
only (`RESEARCH_KEYS`) -- so DPRs for the same machine and state share one
benchmark set; `GROUNDING_VERSION` 3.

### Full 15-DPR run (2026-11-13)

`eval/results/20261113T095759Z`, all 15 DPRs once: **15/15 completed**, INR
289 (~19/DPR), mean 217 s (the scanned DPR 454 s: `.env` allows 100 vision
pages, so all 26 pages went to Gemini OCR). **6/6 rejected DPRs have
must-fix items** (quotation + one machine on all six). Every approved DPR
also has 1-3 must-fix items (one machine on 13 of 15 harvester DPRs; full
capacity from year 1 on 5) -- consistent with the reference CAM, which
lists six must-fix items for a DPR the bank approved; must-fix here means
"fix before you apply", not "will be rejected".

Bugs found and fixed (offline):
- **False must-fix "DSCR YEAR 1: 0.0" (ed8d)**: `yearly_rows` read a DSCR-page
  row printed in rupees (4,06,680) as lakh. Money rows with a value >= 5000
  are now converted from rupees. The same DPR printed a "Total instalment"
  row equal to its "Principal" row: an instalment row equal to principal is
  now treated as principal (else the DSCR leaves out interest). Checked on
  ed8d's pages: DSCR year 1 now from principal 4.07 lakh plus interest.
- **"Insufficient information" on two approved DPRs (9136, 4e9c)**: the
  repayment call hit MAX_TOKENS transcribing a long monthly schedule the
  code parser does not recognise (3 of 15 DPRs). Repayment visible cap
  4096 -> 8192.
- **Benchmark findings** now carry `check_id` BENCHMARK_ACREAGE /
  BENCHMARK_RATE: anchored like checklist items (seen live: the verifier
  downgraded 912a's to good-to-fix).
- The research summary now names what research actually searched for.

**Limitation, not fixed: benchmark consistency across DPRs.** The acreage
check fired on 912a and c0aa (1200 acres) but not on 0b33 or 9136 (also
1200) nor on 3e1c / e778 (1600). Two cached benchmark sets were in play,
decided by whether the brief had a state: with "Madhya Pradesh", 15-25
acres/day and 60-90 days (typically ~1500 acres/year); without, 8-14
acres/day and 60-120 days (~990). The searches found different machine
classes. The check is only as consistent as one search result; the fix is
a credit-reviewed benchmark table per sector (and the machine type
extracted), used before search.
Tests: 922 passed with Postgres (2 pre-existing stub-wallet failures).

## Benchmark table by machine class (2026-11-13)

**Problem (15-DPR run):** the acreage benchmark came from web search, and
each DPR's cached search returned a different machine class (15-25 acres/day
for large self-propelled machines, 8-14 for tractor-mounted) and season
lengths from 40 to 300 days. The same 1200-acre plan for the same 14-ft
machine was flagged on two DPRs and not on two others.

**Change:**
- `benchmarks.py` (new):
  - `detect_harvester_machine` reads the machine from the DPR text by regex:
    self-propelled (also "PROPELIED", an Ashok Leyland engine, or 90 HP and
    above), tractor-mounted (only if nothing says self-propelled), cutter
    bar in feet, engine HP. No LLM.
  - `HARVESTER_BENCHMARKS` holds one entry per class
    (self_propelled_large, self_propelled_small, tractor_mounted), each
    with a status (draft or approved), basis, reviewed_by and reviewed_on.
    An unknown class uses the largest (most lenient) entry.
  - `run_benchmark_checks` checks planned acres/year against
    typical = mid acres/day x mid days/year, plus acres/day and rate/acre
    against the top of the range (10% tolerance).
  - Findings are should_fix, provenance "calculated", with check_ids
    BENCHMARK_ACREAGE, BENCHMARK_ACRES_PER_DAY and BENCHMARK_RATE, so the
    verifier can neither reject nor downgrade them.
- Wiring and output:
  - The checks run in `extract_calculated_facts`, after unit economics,
    whether or not grounding is on.
  - The acreage finding is skipped only when the engine's own-figures
    acreage check *flagged* the plan. Seen live: 20 acres/day x 80 days =
    1600 agrees with itself, so only the benchmark can question it.
  - The record goes to `ProjectFinancials.benchmark_used`, which is set by
    code and never in a Gemini schema. Annexure D shows the machine and the
    benchmark used, labelled draft or approved.
- `grounding.benchmark_findings`:
  - It no longer produces findings for harvester DPRs. Search figures stay
    as "Market evidence" in Annexure D.
  - It keeps only the rate check for sectors the table does not cover.

**Replay (offline: the 15 DPRs' pages + last run's financials):**
- Every harvester DPR's class was read: 13 self-propelled 14-ft or 100 HP
  and above ("large"), one 7-ft ("small"). The 2 non-harvester DPRs get no
  class.
- 1600-acre plans (3e1c, e778) are flagged. All four 1200-acre 14-ft plans
  pass. The 7-ft DPR (1100 acres against a typical 600) is flagged.
- The 5 Rewa DPRs have no planned acreage extracted, so they are not checked.

**Draft figures (pending credit review):**

| Class | Acres/day | Days/year | Rate per acre (Rs) |
|---|---|---|---|
| Large | 12-20 | 60-90 | 1400-2200 |
| Small | 6-10 | 60-90 | 1200-2000 |
| Tractor-mounted | 5-8 | 60-90 | 1000-1800 |

The small-class figures are scaled from the large class by cutter width;
they are the weakest. No per-state season lengths yet.

Tests: new tests/test_dpr_benchmarks.py; test_dpr_grounding updated.

Follow-up (same day): the draft figures' provenance is now written out per
figure in `benchmarks.py`: DPR claims (by sha prefix), the search domains,
and which parts are judgement. The small-class figures and the
tractor-mounted rate have no source. Each entry's `basis` states this
briefly, with no DPR references. Annexure D shows it as "Benchmark basis".

Benchmarks approved for use (2026-11-13, user): all three entries are
status "approved", reviewed_by "LAS project team (provisional, pending
credit review)". Findings and Annexure D now say "approved 2026-11-13".
The basis text still states the figures' real provenance. Values are to be
replaced when credit reviews them.

## Polyhouse benchmarks and the balance-sheet check (2026-11-13)

Analysed the approved polyhouse DPR (ed8d: NVPH, 4000 m2, Dutch rose, MP).
Its operating figures fit the benchmarks (structure Rs 900/m2, year-1 yield
about 160 stems/m2, Rs 6 a stem). Its projected balance sheet does not
balance in any year (gap 29.65 lakh), which nothing caught.

**Polyhouse extraction:**
- New optional group "polyhouse" (financial_extraction.py, gemini.py),
  `PolyhouseAssumptions`. It runs only when the pages say polyhouse,
  greenhouse, shade net or NVPH.
- Fields: structure type, crop, produce unit, area m2, structure cost, plants,
  yield per m2 or per plant, units a year, price per unit.
- Every figure must appear on its cited page (`verify_polyhouse_pages`). The
  structure cost may be printed in rupees.
- The context router tags polyhouse and sq.mt pages as operations, and
  "sales price" as revenue assumptions.

**Polyhouse benchmarks (benchmarks.py):**
- Structure cost norm per m2:
  - NVPH Rs 844-950: NHB, Assam, MP SLBC and West Bengal schedules.
  - Unstated type Rs 844-1650: parliamentary answer; lenient.
- Dutch rose: 100-200 stems/m2/year and Rs 3-8 a stem. These come from
  guides, IIHR, a university trial and one market study, not an official
  dataset.
- Provisionally approved like the harvester figures; sources are in the code
  comment and the `basis`.
- Checks, all should-fix and anchored:
  - BENCHMARK_STRUCTURE_COST.
  - BENCHMARK_YIELD: yield as printed, else per plant x plants, else units /
    area, else implied from year-1 revenue / price.
  - BENCHMARK_RATE: per-piece prices only.
  - A crop without a norm gets only the structure check.
- `benchmark_used["annexure"]` now holds Annexure D lines for every sector;
  cam_adapter copies them.
- Search benchmarks no longer produce findings for polyhouse DPRs either.

**Balance sheet (all sectors):**
- yearly_rows.py reads the "Total assets" / "Total liabilities" rows on
  balance-sheet pages, in code, converting rupees to lakh.
- financial_calc.balance_sheet_balances produces one finding, "Projected
  balance sheet does not balance", listing the years and the largest gap.
  The generic 5% rule makes it must-fix.
- Across the 15 DPRs: 4 have readable totals. 3 balance; ed8d does not.

Not yet verified live: the polyhouse group's Gemini extraction on ed8d (one
paid run, about Rs 20).

Tests: test_dpr_benchmarks, test_dpr_yearly_rows, test_dpr_financial_calc
and test_dpr_unit_economics extended. Result: 942 passed with Postgres, plus
the 2 pre-existing stub-wallet failures.

Live check (2026-11-13, run 20261113T120448Z, ed8d only, Rs 26.87, 207 s):
- The polyhouse group extracted exactly the hand-read figures: NVPH,
  Dutch Rose, Bunch, 4000 m2 (p4), structure 36 lakh (p23), Rs 6 (p30). Its
  page checks passed.
- Benchmarks: all within range; Annexure D lines present.
- The balance-sheet must-fix fired: 57.27 vs 86.92 lakh in year 1.
- DSCR year 1 is now 5.03 (it was a false 0.0 before the yearly_rows fix).
- 4 must-fix in all: means of finance (17.5+35+52.5 = 105 against a 70-lakh
  cost; the subsidy is counted on top), balance sheet, DOC_QUOTATION and
  RISK_SINGLE_ASSET.
- Note: RISK_SINGLE_ASSET is worded for machinery ("breakdown cover"); for a
  polyhouse the analogous risk is structure or crop loss. Its wording should
  become sector-neutral.

RISK_SINGLE_ASSET reworded to be sector-neutral (2026-11-13, user): "cover
against its loss", meaning insurance of the asset (and of the crop or
livestock) and a maintenance, service or back-up arrangement. Title: "Income
depends on one asset without cover against its loss". The check_id,
must-fix rating and absence rule are unchanged. No paid run.

## Dairy and cold-storage benchmarks (2026-11-13)

No dairy or cold-storage DPR is in the evaluation set. The figures come from
public sources only, with per-figure provenance in the benchmarks.py
comments. They are provisionally approved, like the others.

**Extraction:**
- New optional groups "dairy" (`DairyAssumptions`) and "cold_storage"
  (`ColdStorageAssumptions`). They are triggered by sector words
  (`_SECTOR_TERMS`): none of the 15 evaluation DPRs triggers them.
- Page verification is generalised: `verify_sector_pages` covers polyhouse,
  dairy and cold storage.

**Dairy (by breed):**
- The breed is read from the extracted animal type, else the breed named
  most often in the pages. If no breed is found, the crossbred-cow entry is
  used (lenient), and Annexure D says so.

| Breed | Yield (litres/day) | Price (Rs/litre) |
|---|---|---|
| Crossbred cow | 7-12 | 30-45 |
| Buffalo | 6-9 | 45-65 |
| Indigenous cow | 3-5 | 30-45 |

- Checks:
  - yield/day;
  - lactation days, at most 305;
  - annual milk per animal, at most high yield x 305;
  - price.

**Cold storage (by type):**
- Cost per MT:
  - Type 1 (single temperature zone): Rs 8,000-9,600.
  - Type 2 (multi-chamber/PEB): Rs 10,000-12,000. Also used when the type is
    not stated (lenient).
  - The tops of both ranges are the reported 2025 MIDH revision, unverified.
- Capacity: at most chamber volume / 3.4 m3 per MT (NHB norm).
- Potato rent: Rs 225-380 a quintal a season. It is checked only for potato
  on a seasonal basis.

**Other changes:**
- `run_benchmark_checks` runs every sector a DPR belongs to and merges the
  Annexure D lines.
- `covered_by_table` decides when search benchmarks stay evidence-only.

Tests: 948 passed with Postgres, plus the 2 pre-existing stub-wallet failures.
Nothing is verified live: there is no DPR for either sector.

Live check on public model projects (2026-11-13, run 20261113T122536Z,
Rs 37.19; plus two single-call dairy probes, under Rs 1 each):
- NABARD's 10-buffalo dairy model (agritech.tnau.ac.in) and Bihar's cold
  storage Type II, 2,000 MT model project (horticulture.bihar.gov.in), kept
  in the scratchpad only.
- Both groups ran and the reports completed.
- Cold storage read correctly: Type II, 2,000 MT, 468 lakh. Rent is printed
  per MT per day, so it is correctly not compared with the potato range.
- Dairy read the breed, animals and yield. It returned lactation days (270)
  and milk price (Rs 26) empty although both are printed on page 5. Naming
  the typical labels in the prompt fixed it: the probe read 270, 26 and feed
  Rs 93/day.
- Benchmark changes from this check:
  - Buffalo yield range widened to 6-10. At 6-9 it flagged NABARD's own
    10 litres/day.
  - The cost-norm findings (cold storage and polyhouse) are reworded around
    the subsidy cap: "... above the subsidy cost norm". The norm is what
    subsidy is computed on, not the market cost; the Bihar model itself is
    at Rs 23,400/MT against a norm of Rs 10,000.
  - The 3.4 m3/MT capacity norm is confirmed by the Bihar model's chamber
    sizes (3.46 m3/MT).

## CAM: documents not verified, verification by IFPL (2026-11-13, user)

The DPR analysis never verifies that documents are genuine. The CAM now
says so, and names who does.
- **Settings:** `document_verification_provider` ("IFPL") and
  `document_verification_service_code` ("VALUE_ADDITION").
- **Snapshot:** `CAMSnapshot.document_verification`
  (`DocumentVerificationNotice`) records provider, service name, fee and
  documents. The documents are: KYC (identity and address proof, PAN); land
  or premises records; credit bureau report.
  - It is frozen per report version, so a later fee change does not rewrite
    an issued report.
  - The fee comes from the service catalogue (Value Addition, Rs 500 today).
  - Old snapshots (None) show the notice without a fee.
- **Page 6:** an always-present "Documents not verified by this report"
  notice. IFPL, our regulated partner, verifies and collects the documents
  under the Value Addition service, with the service charge.
- **Elsewhere:** also in report_limitations and Annexure G.
- **Layout:** set in small type, page 6 still fits one physical page.
  Pre-existing: on the polyhouse DPR, CAM pages 1 and 3 overflow onto extra
  physical pages (8 PDF pages for a 6-page report), unrelated to this change.
- Follow-up (user): IFPL does not cover the vendor quotation. The notice
  now also says the quotation is validated under our Doc Verification
  service (`quotation_verification_service_code`; fee from the catalogue,
  Rs 500, frozen in the snapshot). The wording was tightened so page 6
  still fits one page (checked visually on the polyhouse report).

## Repayment capacity, proof of revenue, scheme fit (2026-11-13, user)

**DSCR norm (financial_calc.py):**
- `dscr_average` = total cash available / total debt service over the
  repayment years, via `dscr_parts`: the same inputs and skips as
  `dscr_per_year`.
- Compared with DSCR_NORM_AVERAGE = 1.25. No RBI figure exists; this is the
  common Indian-bank minimum per public sources, cited in the code.
- Below 1.0 is must-fix; below 1.25 is should-fix.

**Stress test, all sectors:**
- `dscr_stress`: the lowest yearly DSCR with the price 10% and 20% lower,
  every cost unchanged and no tax saving (conservative).
- Below 1.0 at 10% is should-fix; at 20% is good-to-fix. Only the smallest
  failing fall is reported.

**Proof of revenue:** new sector checklist item SEC_DEMAND_EVIDENCE
(should-fix, presence): buyer or union agreements, bookings, letters of
intent, or past sales.

**Scheme fit (schemes.py, new):**
- A scheme rules table: PMEGP, PMFME, SMAM CHC, AIF, MIDH/NHB, CGTMSE,
  NABARD refinance. Sources are in the docstring; provisionally approved.
- Uses the lenient end of each rule; the category and area are rarely
  stated.
- Run in parallel_graph's candidates node, which has the document agent's
  scheme_claimed and the financials.
- Checks:
  - subsidy in the means of finance vs the most any claimed scheme gives;
  - subsidy under a scheme that gives none;
  - the project-cost cap;
  - the minimum own contribution;
  - the AIF subvention rate (3%) and loan cap (Rs 2 crore).
- Findings are should-fix with SCHEME_* check_ids.
- Records go to `AnalysisResult.scheme_checks`; Annexure F lists the rules
  applied and the most each scheme can give.
- Fixed before shipping: "Applicant margin money" (the borrower's 25% share)
  was read as subsidy and flagged on 3 AIF DPRs. Only subsidy, grant or
  back-ended lines now count as subsidy.

**Offline replay on the 15 DPRs (stored results, no paid calls):**
- Average DSCR is 2.7-7.3 everywhere, so the norm does not fire.
- The stress test fails only for e3aa (20% fall, year 7, good-to-fix).
- No scheme findings: mostly AIF without a subsidy line.

Side note: running eval on another --data-dir overwrites
eval/manifest.local.json. It was rebuilt from DPRData (15 DPRs, 6 rejected).

Tests: test_dpr_schemes.py (new), financial_calc and checklists extended.
Result: 962 passed with Postgres, plus the 2 pre-existing stub-wallet
failures.

## New CAM layout, phase 1 (2026-11-13, management-approved design)

The design is in /NewCAMdesign: the C1-C5 HTML boards and the 5-page
template PDF. The user's decisions:
- product "LAS DPR Analyzer";
- reference from the DB row;
- readiness = share of checks passed;
- "Reviewed by" removed;
- IFPL credit/KYC through an API, in either order;
- re-review in 7 days;
- annexures as appendix pages.

**Layout:**
- cam_render_v2.py and templates/cam_v2/ (report.html.jinja, v2.css) build
  the cover, sections 1-5 and appendix A-G as flowing paged media.
  - Header and footer are running elements; "Page N of M" uses counter(pages).
  - The action-plan table continues across pages and repeats its headings.
  - Inter is bundled (cam/fonts/Inter-400..800.ttf, OFL licence alongside).
- `CAM_SNAPSHOT_SCHEMA_VERSION` is "2.0.0". `cam_render.uses_v2_layout`
  picks the layout, so 1.x snapshots (issued reports) keep the six-page
  layout. Their tests are pinned to it with an autouse fixture.

**New snapshot fields (all optional):**
- report_reference, financial_review, document_checklist,
  kyc_verification, scheme_review, stress_test, re_review_by;
- readiness checks_passed / checks_total, with score_mode "checks";
- ProjectProfile.lending_bank (BankName service detail, else the DPR);
- VerifiedFinding.why_it_matters / should_be.

**cam_review.py builds them:**
- the readiness score: check families plus checklist ok/issue;
- the page-4 measures from AnalysisResult.calculations: cost,
  contribution, subsidy vs scheme maximum, average and minimum DSCR. The
  rest are pending until phase 2;
- documents from the checklist answers;
- the scheme column and gaps (schemes.py now records gaps per rule);
- the stress box (10% price fall, lowest DSCR).

**AnalysisResult:**
- `calculations` (every code check) and `service_details` (bank and
  project name) are now stored with each run.

**Reference:**
- store_report reserves nextval of the dpr_report_artifacts.id sequence
  before building the snapshot and inserts with that id.
- It prints "PSR/<year>/<id:06d>", tested against the real row.

**KYC:** page 4 shows "Pending: verified by IFPL under our Value Addition
service (Rs 500)" until KycVerification is filled (phase 5, IFPL API).

Checked visually on ed8d (polyhouse, 27 findings, 14 pages: 7 report + 7
appendix) and 912a (harvester, AIF).

Tests: test_dpr_cam_render_v2.py (15). Result: 977 passed with Postgres,
plus the 2 pre-existing stub-wallet failures.

Next: phase 2 (IRR, payback, D:E, FACR, stated ratios, assumptions table,
stress average and sensitivity), phase 3 (scheme comparison), phase 4 (why
it matters / should be, documents checklist), phase 5 (IFPL API).

## New CAM layout, phase 2 (2026-11-13): page 3, "Your figures, recomputed"

**Bank ratios in code (financial_ratios.py, new; no LLM):**
- project IRR: -cost, then net profit + depreciation + interest, no
  terminal value, so conservative;
- payback: cash accruals against cost, interpolated within the year;
- debt:equity: loan / (cost - loan);
- fixed asset coverage: the lowest (fixed assets - depreciation) / (loan -
  principal repaid), from the start; working capital and preliminary
  expenses excluded;
- cash break-even (% capacity): needs the variable cost per unit. With
  no year-wise volume it uses the volume revenue / price implies; with no
  PBT line, tax counts as a cost (the cautious side);
- average DSCR with the price 10% lower;
- headroom: how far price can fall, or costs rise, before the average
  DSCR reaches 1.10. The smaller one is the most sensitive assumption.
- They are in run_all_checks, so agents see them, but they are listed in
  `financial_calc.MEASURE_CHECKS`: never a finding, and not counted in
  the readiness score. A DPR's IRR or break-even often differs from ours
  by method, not error.

**The DPR's own ratios:**
- new optional extraction group "stated_ratios" (StatedRatios): IRR,
  average DSCR, break-even and its basis, D:E, FACR, payback, price and
  cost escalation, depreciation method and rate;
- page-verified like the sector groups;
- runs when a page has a ratio, assumption or DSCR heading (new
  context_router sections "ratios" and "assumptions"; "irr" alone would
  match "irrigation"). These sections are kept out of FINANCIAL, so the
  agents' contexts are unchanged;
- LOW thinking, 2048-token cap: about one small call per DPR.

**cam_review on page 3:**
- the five rows that were pending (IRR vs the loan rate, cash break-even
  ≤ 65%, D:E, FACR ≥ 1.25, payback < tenure) now print;
- the D:E limit is 3:1, or what the scheme's minimum own share allows
  (10% own share means 9:1);
- the DPR's average DSCR is the printed one when the DPR states it.

**Assumptions to revise (new snapshot field `assumption_review`):**
- price and yield against the sector benchmark table. benchmarks.py
  records now carry `reference` ranges; a figure above the top is
  flagged and the top suggested;
- utilisation against a 60/70/80 ramp-up (provisional LAS reference);
- cost escalation against price escalation (RBI 4% target as reference);
- the interest rate (confirm with the branch);
- depreciation against Income Tax 15% WDV, using the implied year-1 rate
  when none is printed.

**Stress box:** average and lowest DSCR at -10% price, plus the most
sensitive assumption.

**Appendix:**
- Annexure F prints the source domain next to Google's redirect link
  (new PolicyStatement.source_domain);
- only URL cells wrap anywhere.
- Page-3 tables use a fixed layout, so the two tables and the stress box
  fit on one page.

**Checked offline on the 15 stored results** (a preview script recomputes
the new checks):
- IRR 15-70%; D:E 1.2-4.0;
- FACR: 0.93 in year 6 on 912a and c0aa (straight-line book value below
  the outstanding loan);
- cash break-even only on 912a (18.3%), since the others print no
  variable cost per unit. Their DPR column fills from stated_ratios on
  the next paid run.

Tests: test_dpr_financial_ratios.py (16). Full suite with Postgres: 1181
passed, plus the 2 pre-existing stub-wallet failures.

Next:
- phase 3, scheme comparison;
- phase 4, why it matters / should be, documents;
- phase 5, IFPL API;
- phase 6, a paid run on the 15 DPRs (fills the stated ratios) and a
  visual review.

## New CAM layout, phase 3 (2026-11-13): page 1, scheme comparison

**schemes.py additions (sources searched 2026-11-13, in the module
docstring):**
- `SchemeRule` gains:
  - `sectors`: AIF covers harvester, polyhouse and cold storage; SMAM
    covers harvester; MIDH covers polyhouse and cold storage; AHIDF
    covers dairy processing only;
  - `tenure_moratorium`;
  - `combines_with`: AIF with PMEGP, SMAM and MIDH;
  - `cautious_pct` / `cautious_cap_lakh`.
- New rule AHIDF: 3% subvention for 8 years, 10% own share, a 25%
  guarantee for MSMEs; covers dairy and meat processing, not milch
  animals.
- `project_sector(fin)`: harvester, polyhouse, cold_storage or
  dairy_farm, from the sector groups. None for any other project, or one
  that looks like two sectors.
- `subvention_value()`: the subvention rate on the loan outstanding each
  year (the DPR's own principal, else equal principal after the
  moratorium).
- `max_subsidy(..., cautious=True)` and `scheme_options()`: every
  covering scheme, alone and in allowed pairs, ranked by estimated
  benefit.
- **Two ends of each rule.** Checks of a DPR's claim keep the LENIENT end;
  recommendations use the CAUTIOUS end, so we never promise the best
  case:
  - SMAM: one self-propelled combine, 40% at most Rs 6.4 lakh (the CHC
    cap is Rs 100 lakh);
  - MIDH: 35% (general areas), polyhouse structure only;
  - PMEGP: 15%.
- MIDH polyhouse, checking a claim: 50% of the whole project cost, since
  planting material and cultivation also get assistance. A structure-only
  maximum falsely flagged ed8d (an approved DPR, 35 lakh = 50% of 70).
- New finding `SCHEME_ACTIVITY_<KEY>` (should-fix) and gap "Eligible
  activity" when the claimed scheme does not cover the sector (e.g. AIF
  for a dairy farm).

**cam_review.scheme_review:**
- Columns: chosen, recommended (best option) and alternative, each with
  - eligibility: likely / partly / no;
  - ceiling, own share, subsidy (cautious, with "up to" the lenient
    rate);
  - subvention (≈ value), guarantee, tenure, combinable schemes;
  - estimated total benefit.
- Verdict:
  - not_eligible: the claimed scheme does not cover the sector;
  - correct: the chosen option is the best, or within max(0.5 lakh, 5%)
    of it;
  - better_available: otherwise;
  - not_assessed: a sector the table does not compare.
- Recommendation text: "Keep X and add Y" or "Switch to Y", with both
  benefit figures and "confirm with your bank".
- Dairy farms: DEDS ended in 2020-21 and AHIDF does not cover them.
- Gaps under the recommended option: the scheme to add, the claimed
  scheme's gaps it keeps, and each added scheme's cost ceiling and own
  share.
- The cover's scheme-fit chip is coloured by verdict.

**Offline on the stored DPRs:**
- 10 AIF-only harvester DPRs: "Keep AIF and add SMAM", ~₹9 lakh against
  ~₹2.7 lakh;
- 0b33, which already claims AIF + SMAM: correct;
- ed8d: keep MIDH, add AIF;
- 64e1 (no scheme named): MIDH + AIF;
- no false activity findings.

Tests: test_dpr_schemes.py (+8). Full suite with Postgres: 1001 passed,
plus the 2 stub-wallet failures. The phase-2 entry's "1181" was a
miscount from the log's progress lines.

Next:
- phase 4: why it matters / should be, documents;
- phase 5: IFPL API;
- phase 6: a paid run and a visual review.

## New CAM layout, phase 4 (2026-11-13): action plan text, documents

**Every finding now carries the action plan's columns:**
- **Fields.** `Finding.why_it_matters` and `should_be` are new;
  `dpr_stated_value` already existed. cam_adapter maps all three into
  VerifiedFinding. The category fallback stays only for old results.
- **Checklist findings:**
  - fixed text per item (checklists.WHY_AND_SHOULD_BE, 20 items; a test
    keeps it complete);
  - "Your DPR" is the evidence quote cut to 10 words, or "Not in the
    DPR" for an absent attachment.
- **Code findings:**
  - financial_calc `_finalize` fills why (WHY_BY_CHECK) and both values
    from the CalculationResult, e.g. "Average DSCR 1.12" → "≥ 1.25",
    "Assets x, liabilities y" → "Assets = liabilities";
  - benchmarks: each failing check pairs with its finding in order,
    guarded by a count check: "12 ₹/unit" → "≤ 8 ₹/unit";
  - schemes: `gap()` copies "requires" and "DPR shows" onto the finding
    just made;
  - unit_economics and cam_engine_bridge add a why per check.
- **Free-form agent findings:** three optional schema fields
  (why_it_matters ≤ 12 words; dpr_stated_value and should_be ≤ 8 words,
  only from the pages, never invented), asked for in
  CHECKLIST_INSTRUCTION.

**Documents table:**
- 5 new document-role checklist items marked `report_only`. They never
  make a finding, since a DPR that doesn't mention a Udyam number isn't
  defective by that alone:
  - DOC_REGISTRATION, DOC_PAST_FINANCIALS, DOC_APPROVALS,
    DOC_SCHEME_REGISTRATION, DOC_CERTIFICATES;
  - the matching rows read ok → available, issue → missing,
    not_applicable → "to confirm" with a note;
  - KYC stays with IFPL.
- The document agent's context gains `document_mentions`: lines naming
  registrations, ITRs, licences, NOCs, certificates and portal numbers,
  from any page (up to 20 pages).

**Layout:**
- The action-plan table uses a fixed layout, so the issue column is no
  longer squeezed: 912a went from 14 to 12 pages.
- Cost: slightly longer prompts and output (5 questions, 3 short
  fields).

Not done: a DPR-date fact (the cover shows the review date). It is
optional.

Tests: test_dpr_action_plan_text.py (8). Full suite with Postgres: 1009
passed, plus the 2 stub-wallet failures.

Next:
- phase 5: the IFPL API (needs its details);
- phase 6: a paid run on the 15 DPRs (~₹300) and a visual review. That
  run is the first to exercise phases 2-4 end to end: the stated ratios,
  the new fields and the document answers.

**Phase 5 (IFPL credit/KYC API), decided 2026-11-13:** we will FETCH
(pull) the results from IFPL; IFPL does not push. Implementation waits
until the user's team has discussed it with IFPL (identifier, response
shape, auth, URLs). Until then page 4 shows "Pending: verified by IFPL"
and `KycVerification` stays empty.

## Single-DPR live test of phases 2-4 (2026-11-13, 912a, ₹15.52)

The user asked for 1 DPR first; the 15-DPR run (started, stopped after a
few calls, ~₹5) comes later.

**Run:** 200 s; extraction accuracy 100%. The stated_ratios call read
IRR 24.8, average DSCR 2.51, break-even 51.8 ("target capacity"), and
SLM at 15%.

**Fixed from what it showed:**
- **Duplicates.** The verifier re-added 4 checklist items under new
  titles (demand, single asset, promoter, sensitivity). The stored runs
  had 34 such duplicates across 11 of 15 reports.
  - checklists.TOPIC_TERMS: a title pattern per item;
  - finding_judge.absorb_restatements and consolidate() fold a free-form
    finding on a checklist item's topic into it (order-independent;
    evidence kept in the explanation).
- **Informational findings** (severity info, e.g. 7 "DSCR YEAR n") no
  longer fill the action plan or the cover counts; they stay in the
  appendix register. 912a: "Can improve" 15 → 5, 12 → 11 pages.
- **Readiness** no longer counts the report-only document questions
  (912a: 67% of 33 → 70% of 30).
- **Missing figures filled:**
  - "Full capacity from year 1": "100% in year 1" → "About 60% in year 1,
    rising";
  - fund flow: "Sources x, applications y" → "Add the closing cash so both
    sides match". Its test now checks this two-sided text; still no
    single "correct" figure.
- **Display:**
  - the DPR's break-even prints its basis: "51.8 (target capacity)";
  - benchmark source labels are shortened.

Tests +3; full suite with Postgres: 1012 passed, plus the 2 stub-wallet
failures.

Preview: the scratchpad's live_912a.pdf, rendered from the run with the
fixes re-applied.

## Paid run, all 15 DPRs (2026-11-14, results 20261113T190526Z, ₹285.61)

**Run totals:**
- 15/15 completed; mean latency 219 s; ₹14.8-24 per DPR, ed8d ₹36.87.
- Every rejected DPR has a must-fix (6/6).
- Mean extraction accuracy on the annotated DPRs: 88%.

**Checked across all 15:**
- every action-plan item has "Your DPR" / "Should be";
- all 8 document questions answered;
- no checklist duplicates left;
- 11-13 pages each.

**Scheme verdicts:**
- 0b33: correct;
- 4e9c and e3aa: not assessed (scheme names not in the table);
- the other 12: better scheme available (harvesters: add SMAM to AIF;
  ed8d: add AIF to NHB).

**The DPR's own ratios vs ours:** e778 and 3e1c print IRR 26.5% / 27.2%
while their own projections (net profit 5.5 → 43.6 lakh on a 31 lakh
project) give ~70%. e778's printed "payback 84 months" is the bank's
loan tenure. These are real inconsistencies, shown side by side.

**Approved DPRs still show 1-4 must-fix:** all from the bank reference
template's own must-fix items (quotation, one asset without cover, full
capacity from year 1) plus ed8d's balance sheet and means of finance.
Unchanged from earlier runs. Whether an approved DPR should read "Needs
major revision" is the user's call.

**Fixed after the run:**
- Agents restating a code finding (5 of 15 reports, e.g. "Means of
  Finance summation error..." next to "Means Of Finance Total does not
  reconcile"): finding_consolidation.CALCULATED_TOPICS folds them into the
  code finding.
- Reconciliation totals no longer say "Should be: 105". ed8d's back-ended
  NHB subsidy is listed as extra funding, so its items add to 105 against
  a correct 70. They now print "Total 70; items add to 105" → "Items that
  add up to the total", and funding vs cost likewise.
- Phase 4's WHY_BY_CHECK keys were wrong: the checks are named
  cost_of_project_total and means_of_finance_total.
- scripts/dpr_eval.py: `--only` no longer shrinks eval/manifest.local.json
  to the DPRs it ran; it writes every DPR found.

Tests: 1013 passed, plus the 2 stub-wallet failures.
