# Section 12 — Detailed CAM Annexures

> Pasted verbatim from the user, 2026-11-05, as the authoritative spec for
> milestone 7. Saved here per the user's own instruction so it survives
> context compaction and future sessions, rather than living only in chat
> history. See also `PROGRESS.md` for what has actually been built against
> this spec and when.

The six-page borrower report must remain concise. Create a separate
technical annexure package for consultants and professional reviewers,
generated from the same CAM snapshot.

**Annexure A — Financial Statements**: projected profit and loss; balance
sheet; cash flow; working capital; ratio analysis.

**Annexure B — Repayment**: loan amortization; principal schedule; interest
schedule; moratorium treatment; year-wise DSCR; repayment assumptions.

**Annexure C — Sensitivity Analysis**: scenario assumptions; revenue
impact; cost impact; cash-flow impact; debt-service impact.

**Annexure D — Project Feasibility**: sector-specific assumptions;
installed capacity; utilization; raw material requirements; production
assumptions; market evidence.

**Annexure E — Findings Register**: finding ID; dedup key and merged
duplicate count; severity and the priority rule that fired; defect or
business-risk classification; verification status; financial impact;
source evidence; proposed correction; resolution status.

**Annexure F — SBI and Scheme References**: lending product; relevant
public policy; document requirements; scheme eligibility (including
subsidy and AIF benefit status: verified, conditional or unverified);
source URLs; effective dates; retrieval dates; unverified policy areas.

**Annexure G — Audit Trail**: analysis ID; report version; source document
checksum; agent execution details; Gemini model and pinned version;
calculation version; configuration values used (DSCR definition, priority
thresholds, readiness mode); reviewer actions; report generation
timestamp.

Do not invent content merely to make annexures appear complete. An
annexure may explicitly state that data is unavailable or analysis is
incomplete.

Constraints that apply here: no borrower KYC or bureau data in any
annexure (refer to consent and transaction references only, and mask
Aadhaar to the last 4 digits). Every figure must come from the CAM
snapshot and be reproducible from its calculation trace; scenarios are
labelled as scenarios.

Before building, list for each annexure A–G which snapshot fields feed it
and which are missing.
