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

## Remaining work

- The balance-sheet/cash-flow gap (milestone 2, disclosed above).
- Wiring `cam_financial_engine.py`/`sector_feasibility.py` into
  `orchestrator.py`'s `extract_calculated_facts()` and `cam_adapter.py`,
  which requires extending the Gemini extraction prompt to pull
  harvester-specific assumptions (acres/day, fuel rate, crew staffing,
  fund-flow totals) that `financial_schema.ProjectFinancials` doesn't carry
  today.
- Milestones 4–7 per Section 3A: templates/PDF, persistence/APIs/
  authorization, frontend, annexures. Not started.
