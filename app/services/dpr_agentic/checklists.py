"""Fixed per-role checklists: the same questions on every run.

Why: asked open-endedly to "find issues", an agent picks which issues to
mention, how to word them and how severe they are -- differently on each run
of the same DPR (about half the findings differed between two runs,
PROGRESS.md 2026-11-12). With a checklist the model answers a fixed set of
questions; code turns each "issue" answer into a finding whose title,
category, severity and priority come from this file. The model contributes
the judgement (is this a problem in this DPR?) and the evidence; the
wording and rating stop varying.

What the checklists cover: the bank reference CAM template's judgement
issues (one asset without cover against its loss, a single booking channel,
off-season upkeep, the running-cost basis, a missing quotation) and common
DPR defects seen in the evaluation set. Arithmetic is NOT here -- DSCR,
reconciliations, capacity, utilisation, revenue = volume x price are code
checks (financial_calc.py, cam_engine_bridge.py, unit_economics.py), and
the prompts say not to repeat them.

Severity and priority per item are for a credit reviewer to tune; changing
them changes no prompt. An item's id is stable: the evaluation's stability
metric compares runs by it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .schemas import AgentOutput, Finding

# Free-form findings an agent may add beyond its checklist.
MAX_EXTRA_FINDINGS = 3

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


@dataclass(frozen=True)
class ChecklistItem:
    id: str
    question: str
    title: str
    category: str
    severity: str
    priority: str
    action: str
    # "Is X attached?" items: the agent is given every page that mentions
    # X, so "cannot_tell" means it is not in the DPR -- treated as an issue.
    absence: bool = False
    # Answered only for the report's documents table: never a finding (a
    # DPR not mentioning, say, a Udyam number is not a defect in itself).
    report_only: bool = False


def _item(
    id,
    question,
    title,
    category,
    severity,
    priority,
    action,
    absence=False,
    report_only=False,
):
    return ChecklistItem(
        id, question, title, category, severity, priority, action, absence, report_only
    )


def _document(id, question, title):
    """A documents-table item (report_only)."""
    return _item(
        id,
        question,
        title,
        "missing_document",
        "info",
        "good_to_fix",
        "",
        report_only=True,
    )


CHECKLISTS: dict[str, tuple[ChecklistItem, ...]] = {
    "document": (
        _item(
            "DOC_QUOTATION",
            "Is a vendor quotation or pro-forma invoice for the main asset "
            "attached, with a total that supports the project cost?",
            "Vendor quotation or pro-forma invoice missing or not matching",
            "missing_document",
            "high",
            "must_fix",
            "Attach a current quotation or pro-forma invoice from the "
            "supplier, matching the project-cost table.",
            absence=True,
        ),
        _item(
            "DOC_IDENTITY",
            "Are the applicant's name, project and location the same on "
            "every page (no traces of another customer's document)?",
            "Applicant or project details inconsistent across pages",
            "identity",
            "high",
            "should_fix",
            "Correct every page to the applicant's own details.",
        ),
        _item(
            "DOC_PREMISES",
            "Does the DPR state where the project runs (own land, lease or "
            "rented premises) and support it?",
            "Project premises or land not evidenced",
            "missing_document",
            "medium",
            "should_fix",
            "State the premises and attach the ownership or lease document.",
        ),
        _document(
            "DOC_REGISTRATION",
            "Does the DPR state the applicant's entity registration (Udyam "
            "number, partnership deed, MoA or society registration)? 'ok' if "
            "a number or document is given, 'issue' if the applicant is an "
            "entity and none is given, 'not_applicable' for an individual "
            "without an enterprise.",
            "Entity registration or Udyam not stated",
        ),
        _document(
            "DOC_PAST_FINANCIALS",
            "Does the DPR include or refer to the applicant's income-tax "
            "returns or past financial statements? 'not_applicable' if the "
            "DPR says the applicant is a first-time entrepreneur.",
            "Past ITRs or financial statements not included",
        ),
        _document(
            "DOC_APPROVALS",
            "Does the DPR list the statutory approvals or licences the "
            "project needs (e.g. FSSAI, pollution consent, electricity "
            "connection, vehicle registration) and their status?",
            "Statutory approvals not listed",
        ),
        _document(
            "DOC_SCHEME_REGISTRATION",
            "If the DPR claims a government scheme, does it give the "
            "scheme application or portal registration number? "
            "'not_applicable' if no scheme is claimed.",
            "Scheme portal registration not stated",
        ),
        _document(
            "DOC_CERTIFICATES",
            "Does the DPR mention the promoter's training certificate or "
            "category certificate (e.g. EDP training, SC/ST, women, ex-"
            "serviceman) that a claimed scheme benefit depends on? "
            "'not_applicable' if no benefit depends on one.",
            "Training or category certificate not mentioned",
        ),
    ),
    "finance": (
        _item(
            "FIN_WORKING_CAPITAL",
            "Where the project has running costs before its receipts come "
            "in, does the project cost or funding provide working capital "
            "(or explain why none is needed)?",
            "No working capital provided for running costs",
            "funding",
            "medium",
            "should_fix",
            "Add a working-capital margin to the project cost, or explain "
            "how running costs are met before receipts.",
        ),
        _item(
            "FIN_INTEREST_IN_PNL",
            "Does the profit and loss statement charge interest on the "
            "term loan in every year the loan is outstanding?",
            "Term-loan interest missing from the profit and loss statement",
            "inconsistency",
            "high",
            "should_fix",
            "Charge term-loan interest in the P&L for every year it is paid.",
        ),
        _item(
            "FIN_STATEMENTS_CONSISTENT",
            "Do the P&L, cash flow, balance sheet and DSCR table use the "
            "same figures for the same year?",
            "Financial statements disagree with each other",
            "inconsistency",
            "high",
            "should_fix",
            "Make every statement use the same yearly figures.",
        ),
        _item(
            "FIN_SUBSIDY_AS_FUNDING",
            "Is a subsidy or grant that is not yet sanctioned counted as "
            "promoter contribution or as funding of the project cost?",
            "Unsanctioned subsidy counted as funding",
            "funding",
            "high",
            "should_fix",
            "Fund the project without the subsidy, or show its sanction.",
        ),
        _item(
            "FIN_DEPRECIATION",
            "Is depreciation consistent with the asset cost and a stated rate or life?",
            "Depreciation not consistent with the asset cost",
            "assumption",
            "medium",
            "should_fix",
            "Compute depreciation from the asset cost at a stated rate.",
        ),
        _item(
            "FIN_TAX",
            "Is income tax provided for in the projections where profits are taxable?",
            "Income tax not provided for",
            "operating_costs",
            "medium",
            "should_fix",
            "Provide for income tax at the applicable rate.",
        ),
        _item(
            "FIN_BREAKEVEN_BASIS",
            "If the DPR states a break-even, is it computed on the full "
            "running cost (not a partial cost)?",
            "Break-even computed on an incomplete cost",
            "assumption",
            "medium",
            "should_fix",
            "Recompute break-even on the full running cost.",
        ),
    ),
    "sector": (
        _item(
            "SEC_CAPACITY_REALISTIC",
            "Are the stated capacity per day and operating days realistic "
            "for this asset, sector and location?",
            "Capacity or operating-day assumptions not realistic",
            "assumption",
            "high",
            "should_fix",
            "Base capacity and operating days on the manufacturer's rating "
            "and the local season, with a source.",
        ),
        _item(
            "SEC_PRICE_REALISTIC",
            "Is the price or rate per unit in line with what the DPR shows "
            "about the local market?",
            "Price per unit not supported",
            "market",
            "medium",
            "should_fix",
            "Support the rate with local market evidence.",
        ),
        _item(
            "SEC_RUNNING_COSTS_COMPLETE",
            "Are all running costs included: fuel or power, labour, "
            "repairs and maintenance, insurance, and off-season upkeep?",
            "Running costs incomplete",
            "operating_costs",
            "medium",
            "should_fix",
            "Add every running cost, including off-season upkeep and insurance.",
        ),
        _item(
            "SEC_SEASONALITY",
            "Do the projections reflect the business's seasonality?",
            "Seasonality not reflected in the projections",
            "operations",
            "medium",
            "should_fix",
            "Show income and costs by season.",
        ),
        _item(
            "SEC_INPUTS_AVAILABLE",
            "Does the DPR show that inputs (raw material, fuel, skilled "
            "labour, spares and service) are available locally?",
            "Availability of inputs not shown",
            "operations",
            "low",
            "good_to_fix",
            "Name the local sources of inputs, labour and service.",
        ),
        _item(
            "SEC_DEMAND_EVIDENCE",
            # Proof of revenue: every other revenue check compares the DPR
            # with itself or with benchmarks; this one asks for evidence that
            # the customers exist. A presence question (see RISK_SINGLE_ASSET).
            "Does the DPR show evidence of demand for its output -- buyer, "
            "milk-union or supply agreements, booked customers or acreage, "
            "letters of intent, or past sales of an existing business? Answer "
            "'issue' if it shows none of these.",
            "No evidence of demand for the output",
            "market",
            "medium",
            "should_fix",
            "Attach evidence of demand: buyer or union agreements, bookings, "
            "letters of intent or past sales records.",
            absence=True,
        ),
    ),
    "risk": (
        _item(
            "RISK_SINGLE_ASSET",
            # Phrased as a presence check: asked "is this a risk?", the
            # same DPR got "ok" in one run and "issue" in the next.
            # Sector-neutral wording: "breakdown cover" fitted a harvester
            # but read as a template error on a polyhouse (seen live), whose
            # risk is storm or crop loss, not a breakdown.
            "If the income depends on one main asset (a machine, a "
            "polyhouse, a cold store, a herd...): does the DPR show cover "
            "against its loss -- insurance of the asset (and of the crop or "
            "livestock where relevant) AND a maintenance, service or back-up "
            "arrangement? Answer 'issue' if it does not show this; "
            "'not_applicable' if income does not depend on one asset.",
            "Income depends on one asset without cover against its loss",
            "risk",
            "high",
            "must_fix",
            "Show insurance of the asset (and of the crop or livestock where "
            "relevant) and a maintenance, service or back-up arrangement.",
            absence=True,
        ),
        _item(
            "RISK_DEMAND_CONCENTRATION",
            "Does demand depend on a single channel, platform or buyer?",
            "Demand depends on a single channel or buyer",
            "market",
            "medium",
            "should_fix",
            "Show more than one source of customers, or firm commitments.",
        ),
        _item(
            "RISK_PROMOTER_EXPERIENCE",
            "Does the DPR show the promoter's experience in this business?",
            "Promoter's experience in the business not shown",
            "risk",
            "medium",
            "should_fix",
            "Describe the promoter's relevant experience or training.",
        ),
        _item(
            "RISK_SENSITIVITY",
            "Does the DPR show what happens to repayment if price or volume falls?",
            "No sensitivity analysis",
            "assumption",
            "low",
            "good_to_fix",
            "Add a sensitivity table for lower price and volume.",
        ),
    ),
}

# What each item's finding prints in the action plan: why the bank cares,
# and what the corrected DPR should show. Fixed text, like the title.
WHY_AND_SHOULD_BE: dict[str, tuple[str, str]] = {
    "DOC_QUOTATION": (
        "The bank sanctions the cost against a quotation",
        "Current quotation, total = project cost",
    ),
    "DOC_IDENTITY": (
        "Mismatched details make the bank doubt the whole DPR",
        "Applicant's own details on every page",
    ),
    "DOC_PREMISES": (
        "The bank needs proof the project has a site",
        "Site stated, with title or lease document",
    ),
    "FIN_WORKING_CAPITAL": (
        "Running costs before receipts would go unfunded",
        "Working-capital margin in the project cost",
    ),
    "FIN_INTEREST_IN_PNL": (
        "Profit and DSCR are overstated without interest",
        "Term-loan interest charged every year",
    ),
    "FIN_STATEMENTS_CONSISTENT": (
        "Statements that disagree get the DPR returned",
        "Same yearly figures in every statement",
    ),
    "FIN_SUBSIDY_AS_FUNDING": (
        "An unsanctioned subsidy may never arrive",
        "Funding complete without the subsidy",
    ),
    "FIN_DEPRECIATION": (
        "Wrong depreciation misstates profit and tax",
        "Depreciation = asset cost x stated rate",
    ),
    "FIN_TAX": (
        "Profit after tax and DSCR are overstated",
        "Income tax provided each year",
    ),
    "FIN_BREAKEVEN_BASIS": (
        "Break-even looks safer than it is",
        "Break-even on the full running cost",
    ),
    "SEC_CAPACITY_REALISTIC": (
        "Revenue rests on capacity the asset may not reach",
        "Rated capacity and local operating days, with a source",
    ),
    "SEC_PRICE_REALISTIC": (
        "Revenue may be overstated",
        "Price backed by market evidence",
    ),
    "SEC_RUNNING_COSTS_COMPLETE": (
        "Profit is overstated if costs are left out",
        "Every running cost, off-season included",
    ),
    "SEC_SEASONALITY": (
        "Cash may run short in the off season",
        "Income and costs shown by season",
    ),
    "SEC_INPUTS_AVAILABLE": (
        "The bank checks the project can actually run",
        "Local sources of inputs and labour named",
    ),
    "SEC_DEMAND_EVIDENCE": (
        "The bank needs to see who will buy the output",
        "Buyer letters, bookings or past sales attached",
    ),
    "RISK_SINGLE_ASSET": (
        "One breakdown or loss stops all income",
        "Asset insured, service or back-up arranged",
    ),
    "RISK_DEMAND_CONCENTRATION": (
        "Losing one buyer would stop the income",
        "More than one customer source",
    ),
    "RISK_PROMOTER_EXPERIENCE": (
        "The bank lends to people who can run the business",
        "Relevant experience or training described",
    ),
    "RISK_SENSITIVITY": (
        "The bank tests repayment if prices or volumes fall",
        "Sensitivity table for lower price and volume",
    ),
}

# The topic of each item, as words a restatement of it uses in its title.
# The verifier is asked for issues no candidate covers, yet sometimes re-adds
# a checklist finding under new words (seen live on 912a: "No documented
# proof of demand" next to "No evidence of demand for the output").
# finding_judge.absorb_restatements folds such a finding into the item's.
# Items with no reliable words (FIN_STATEMENTS_CONSISTENT) are left out.
TOPIC_TERMS: dict[str, re.Pattern] = {
    key: re.compile(pattern, re.I)
    for key, pattern in {
        "DOC_QUOTATION": r"quotation|pro[\s-]*forma|invoice",
        "DOC_IDENTITY": r"applicant (?:name|details)|identity|"
        r"another (?:customer|borrower)",
        "DOC_PREMISES": r"premises|\bland\b|title document|lease",
        "FIN_WORKING_CAPITAL": r"working[\s-]*capital (?:margin|provision|allocation)|"
        r"no working[\s-]*capital",
        "FIN_INTEREST_IN_PNL": r"interest\b.*\b(?:missing|omitted|not charged)",
        "FIN_SUBSIDY_AS_FUNDING": r"subsidy\b.*\b"
        r"(?:funding|means of finance|unsanctioned)",
        "FIN_DEPRECIATION": r"depreciation",
        "FIN_TAX": r"\btax\b",
        "FIN_BREAKEVEN_BASIS": r"break[\s-]*even",
        "SEC_CAPACITY_REALISTIC": r"capacity|operating days|"
        r"acres? (?:per|a) (?:day|year)",
        "SEC_PRICE_REALISTIC": r"\bprice|rate per|tariff",
        "SEC_RUNNING_COSTS_COMPLETE": r"costs? (?:are )?(?:incomplete|omitted|missing)|"
        r"omission of .*(?:cost|expense)",
        "SEC_SEASONALITY": r"season",
        "SEC_INPUTS_AVAILABLE": r"availability of (?:inputs|labour|operators)|"
        r"input supply",
        "SEC_DEMAND_EVIDENCE": r"demand",
        "RISK_SINGLE_ASSET": r"single[\s-]*asset|one asset|breakdown|asset dependen",
        "RISK_DEMAND_CONCENTRATION": r"(?:buyer|channel|customer) concentration|"
        r"single (?:buyer|channel|customer)",
        "RISK_PROMOTER_EXPERIENCE": r"promoter|track record|experience",
        "RISK_SENSITIVITY": r"sensitivity|stress[\s-]*test",
    }.items()
}

FINDING_TEXT_INSTRUCTION = (
    " For each entry in 'findings' also give why_it_matters (one clause, at "
    "most 12 words: why the bank cares), and, only when the supplied pages "
    "show them, dpr_stated_value (the DPR's figure or statement, at most 8 "
    "words) and should_be (the corrected figure or what the DPR should "
    "show, at most 8 words); never invent a figure."
)

CHECKLIST_INSTRUCTION = (
    " CHECKLIST: answer every item in 'checklist' in 'checks', one entry per "
    "id: status 'issue' (this DPR has the problem -- give an explanation of "
    "at most two sentences, evidence_page and a short verbatim "
    "evidence_quote), 'ok' (checked; no problem), 'not_applicable', or "
    "'cannot_tell' (the supplied pages do not show enough). Leave "
    "explanation and evidence empty for any status other than 'issue'. "
    "Judge each item only from the supplied pages. For an item "
    "asking whether a document or attachment is present: 'section_index' "
    "covers every page of the DPR and every page that mentions it is "
    "supplied, so if you do not find it, answer 'issue', not 'cannot_tell'. "
    "Put an "
    "entry in 'findings' only for a material problem that no checklist item "
    f"and no calculated fact covers -- at most {MAX_EXTRA_FINDINGS} -- and "
    "never restate a checklist item there." + FINDING_TEXT_INSTRUCTION
)


def _short(quote: str | None, words: int = 10) -> str | None:
    """The DPR's own words, cut to fit the action plan's column."""
    if not quote:
        return None
    parts = quote.split()
    return " ".join(parts[:words]) + (" …" if len(parts) > words else "")


def checklist_for(role: str) -> list[dict]:
    return [{"id": i.id, "question": i.question} for i in CHECKLISTS.get(role, ())]


def apply_checklist(role: str, output: AgentOutput) -> AgentOutput:
    """Builds a finding for each 'issue' answer (title, category, severity,
    priority from the item), keeps at most MAX_EXTRA_FINDINGS free-form
    findings (most severe first, then as returned), and ignores answers to
    unknown or repeated ids. Returns a copy."""
    items = {i.id: i for i in CHECKLISTS.get(role, ())}
    if not items:
        return output
    out = output.model_copy(deep=True)
    seen: set[str] = set()
    built: list[Finding] = []
    for answer in out.checks:
        item = items.get(answer.check_id)
        if item is None or answer.check_id in seen:
            continue
        seen.add(answer.check_id)
        if answer.status == "cannot_tell" and item.absence:
            # Seen live: "cannot_tell" for a quotation that is not in the
            # DPR, dropping a must-fix the bank's template requires.
            answer = answer.model_copy(
                update={
                    "status": "issue",
                    "explanation": answer.explanation
                    or "Not found anywhere in the DPR.",
                }
            )
        if answer.status != "issue" or item.report_only:
            continue
        why, should_be = WHY_AND_SHOULD_BE.get(item.id, (None, None))
        finding = Finding(
            category=item.category,
            severity=item.severity,
            title=item.title,
            explanation=answer.explanation or item.question,
            evidence_page=answer.evidence_page,
            evidence_quote=answer.evidence_quote,
            suggested_action=item.action,
            provenance="ai_interpreted",
            check_id=item.id,
            why_it_matters=why,
            should_be=should_be,
            dpr_stated_value=(
                "Not in the DPR"
                if item.absence and not answer.evidence_quote
                else _short(answer.evidence_quote)
            ),
        )
        finding.priority = item.priority
        finding.priority_rule = (
            f"{item.priority.upper()}: checklist {item.id} (checklists.py)."
        )
        built.append(finding)
    extras = sorted(
        (f for f in out.findings if not f.check_id),
        key=lambda f: _SEVERITY_RANK.get(f.severity, 9),
    )[:MAX_EXTRA_FINDINGS]
    out.findings = built + extras
    return out
