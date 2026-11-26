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

from .schemas import AgentOutput, ChecklistAnswer, Finding

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
            "Supplier's price quotation is missing or does not match",
            "missing_document",
            "high",
            "must_fix",
            "Attach a recent price quotation from the supplier. Its total "
            "should match the cost of the machine or item in your project cost.",
            absence=True,
        ),
        _item(
            "DOC_IDENTITY",
            "Are the applicant's name, project and location the same on "
            "every page (no traces of another customer's document)?",
            "Your name or project details differ between pages",
            "identity",
            "high",
            "should_fix",
            "Check every page and correct it to your own name, project and place.",
        ),
        _item(
            "DOC_PREMISES",
            "Does the DPR state where the project runs (own land, lease or "
            "rented premises) and support it?",
            "Proof of the land or place of work is missing",
            "missing_document",
            "medium",
            "should_fix",
            "Say where the project will run and attach the ownership or rent "
            "(lease) papers for that place.",
        ),
        # The items marked 2026-11-25 below are topics the agents raised in
        # some runs of a DPR and not in others (measured over 169 saved
        # reports); asked every run, the answer no longer depends on what an
        # agent happens to notice.
        _item(
            "DOC_QUOTATION_FIRM",  # 2026-11-25
            "If the DPR gives a supplier's quoted price (an attached "
            "quotation, or its price copied into the text): is it firm -- "
            "dated, valid for a stated period, and priced with delivery to "
            "the site and GST included (not marked 'indicative' or "
            "'ex-works')? 'not_applicable' only if no supplier price is given "
            "at all.",
            "The supplier's quotation is not final",
            "missing_document",
            "medium",
            "should_fix",
            "Get a final quotation with a date, how long the price is valid, "
            "and delivery to your site and GST included.",
        ),
        _item(
            "DOC_ASSET_SAME",  # 2026-11-25
            "Is the main machine or asset described by make, model and "
            "capacity, the same way on every page? 'issue' if they are not "
            "given, or differ between pages.",
            "The machine's make and model are missing or differ between pages",
            "inconsistency",
            "medium",
            "should_fix",
            "State the machine's make, model and capacity, the same on every "
            "page and matching the quotation.",
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
            "Does the DPR include or list as enclosures the applicant's "
            "income-tax returns or past financial statements? 'ok' if they "
            "are attached or listed; 'not_applicable' if the applicant is a "
            "first-time entrepreneur and the DPR relies on no past income; "
            "'issue' if the DPR mentions past income or an existing business "
            "but attaches and lists none.",
            "Past ITRs or financial statements not included",
        ),
        _document(
            "DOC_APPROVALS",
            "Does the DPR name the statutory approvals or licences the "
            "project needs (e.g. FSSAI, pollution consent, electricity "
            "connection, vehicle registration)? 'ok' if it names them, even "
            "as 'to be obtained', or budgets for them; 'issue' only if it "
            "names none of the ones the project needs.",
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
            "No money set aside to run the business at the start",
            "funding",
            "medium",
            "should_fix",
            "Add money for the first months' running costs (working capital) "
            "to the project cost, or explain how you will pay them before "
            "income starts.",
        ),
        _item(
            "FIN_INTEREST_IN_PNL",
            "Does the profit and loss statement charge interest on the "
            "term loan in every year the loan is outstanding?",
            "Loan interest is left out of the yearly profit figures",
            "inconsistency",
            "high",
            "should_fix",
            "Show the loan interest as an expense in the profit and loss "
            "statement for every year you pay it.",
        ),
        _item(
            "FIN_STATEMENTS_CONSISTENT",
            "Do the P&L, cash flow, balance sheet, DSCR table, ratio sheet, "
            "working-capital schedule and the written text use the same "
            "figures for the same year?",
            "The yearly figures do not match between tables",
            "inconsistency",
            "high",
            "should_fix",
            "Make the profit, cash flow, balance sheet and repayment tables "
            "use the same figures for each year.",
        ),
        _item(
            "FIN_SUBSIDY_AS_FUNDING",
            "Is a subsidy or grant that is not yet sanctioned counted as "
            "promoter contribution or as funding of the project cost?",
            "A subsidy not yet approved is counted as money in hand",
            "funding",
            "high",
            "should_fix",
            "Show how the project is paid for without the subsidy, or attach "
            "the subsidy approval letter.",
        ),
        _item(
            "FIN_DEPRECIATION",
            "Is depreciation consistent with the asset cost and a stated rate or life?",
            "Yearly wear-and-tear (depreciation) does not match the cost",
            "assumption",
            "medium",
            "should_fix",
            "Work out depreciation from the cost of the machine or building "
            "at a stated yearly rate.",
        ),
        _item(
            "FIN_TAX",
            "Is income tax provided for in the projections where profits are "
            "taxable, and does it move in line with the profit each year?",
            "Income tax is not included",
            "operating_costs",
            "medium",
            "should_fix",
            "Add income tax at the correct rate to each year's profit figures.",
        ),
        _item(
            "FIN_BREAKEVEN_BASIS",
            "If the DPR states a break-even, is it computed on the full "
            "running cost (not a partial cost)?",
            "Break-even leaves out some running costs",
            "assumption",
            "medium",
            "should_fix",
            "Work out the break-even point (the sales needed to cover all "
            "costs) again, using all running costs.",
        ),
        _item(
            "FIN_COST_COMPLETE",  # 2026-11-25
            "Does the project cost include everything the DPR says the project "
            "will buy or use -- accessories, registration, a shed, GPS or "
            "sensors? (Delivery and GST are checked separately under the "
            "supplier's price; count them here only if the DPR gives no "
            "supplier price at all.)",
            "Some items you plan to buy are not in the project cost",
            "project_cost",
            "medium",
            "should_fix",
            "Add every item you plan to buy to the project cost.",
        ),
        _item(
            "FIN_LOAN_REPAID",  # 2026-11-25
            "Does the repayment schedule repay the whole loan within the "
            "stated repayment period, without unpaid interest being added to "
            "the loan balance?",
            "The loan is not fully repaid in the repayment schedule",
            "repayment",
            "medium",
            "should_fix",
            "Make the repayment table pay back the whole loan, with all "
            "interest paid as it falls due, within the loan period.",
        ),
        _item(
            "FIN_INTEREST_SUBSIDY",  # 2026-11-25
            "If an interest subsidy or subvention is claimed: does the DPR "
            "name the scheme and show the subsidy correctly in the interest "
            "figures? 'not_applicable' if none is claimed.",
            "The interest subsidy is not clearly shown",
            "policy",
            "medium",
            "should_fix",
            "Name the scheme that gives the interest subsidy and show the "
            "interest before and after it.",
        ),
        _item(
            "FIN_REVENUE_BUILT_UP",  # 2026-11-25
            "Is each year's income worked out from the amount of work or "
            "output times the rate, with any yearly increase explained?",
            "Your income is not worked out step by step",
            "assumption",
            "medium",
            "should_fix",
            "Show each year's income as amount of work times your rate, and "
            "say why it grows from year to year.",
        ),
        _item(
            "FIN_YEARS_CURRENT",  # 2026-11-25 (finance sees the tables)
            "Do all tables use this project's own years, with every year of the "
            "projection shown (no years left over from another DPR, and none "
            "skipped)?",
            "Some tables use old or missing years",
            "inconsistency",
            "low",
            "should_fix",
            "Show every year of the project, starting from the year it begins, "
            "in every table.",
        ),
    ),
    "sector": (
        _item(
            "SEC_CAPACITY_REALISTIC",
            "Are the stated capacity per day and operating days realistic "
            "for this asset, sector and location?",
            "Work per day or working days look too high",
            "assumption",
            "high",
            "should_fix",
            "Use the work per day the maker states for the machine and the "
            "real number of working days in your area's season, and say "
            "where these figures come from.",
        ),
        _item(
            "SEC_PRICE_REALISTIC",
            "Is the price or rate per unit in line with what the DPR shows "
            "about the local market?",
            "Your selling price or rate is not backed up",
            "market",
            "medium",
            "should_fix",
            "Show what others charge locally (mandi rates, other operators' "
            "rates or past bills) to support your price.",
        ),
        _item(
            "SEC_RUNNING_COSTS_COMPLETE",
            "Go through the yearly running costs line by line: fuel or "
            "power, labour or wages, repairs and maintenance, insurance "
            "premium, and off-season upkeep. 'issue' if any of these has no "
            "line or amount of its own (name the missing ones); 'ok' only if "
            "every one has.",
            "Some running costs are left out",
            "operating_costs",
            "medium",
            "should_fix",
            "List every running cost: fuel, wages, repairs, insurance, and "
            "upkeep in the off season.",
        ),
        _item(
            "SEC_SEASONALITY",
            "Does the DPR state the working season(s) or months, with the "
            "projected working days fitting inside them? 'ok' if it does, "
            "even with yearly (not monthly) figures; 'issue' if it assumes "
            "work all year or names no season.",
            "Income is shown as even all year, ignoring seasons",
            "operations",
            "medium",
            "should_fix",
            "Show income and costs season by season, including the months "
            "with little or no work.",
        ),
        _item(
            "SEC_INPUTS_AVAILABLE",
            "Does the DPR show that inputs (raw material, fuel, skilled "
            "labour, spares and service) are available locally?",
            "Local supply of inputs and workers is not shown",
            "operations",
            "low",
            "good_to_fix",
            "Name where you will get raw material, fuel, workers, spare parts "
            "and repair service locally.",
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
            "No proof that customers will buy your service or product",
            "market",
            "medium",
            "should_fix",
            "Attach proof of customers: buyer or union agreements, advance "
            "bookings, farmers' letters or past sales records.",
            absence=True,
        ),
        _item(
            "SEC_FUEL_REALISTIC",  # 2026-11-25
            "Is the fuel used per acre, per hour or per unit of output "
            "realistic for this machine and this work?",
            "Fuel use looks too low",
            "operating_costs",
            "medium",
            "should_fix",
            "Use the fuel use the maker states for this machine, or local "
            "operators' figures, and say where it comes from.",
        ),
        _item(
            "SEC_MAINTENANCE_RISES",  # 2026-11-25
            "Does the repair and maintenance budget rise as the machine gets older?",
            "Repair costs do not rise as the machine gets older",
            "operating_costs",
            "low",
            "good_to_fix",
            "Let the repair and maintenance cost go up each year as the machine ages.",
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
            "No full plan if your main machine or asset breaks down or is damaged",
            "risk",
            "high",
            "must_fix",
            "Add yearly insurance for the machine or asset (and the crop or "
            "animals, if any) to your running costs, and attach a service or "
            "repair arrangement.",
            absence=True,
        ),
        _item(
            "RISK_DEMAND_CONCENTRATION",
            "Does demand depend on a single channel, platform or buyer?",
            "All your sales depend on one buyer or channel",
            "market",
            "medium",
            "should_fix",
            "Show more than one source of customers, or attach a firm "
            "agreement with the buyer.",
        ),
        _item(
            "RISK_PROMOTER_EXPERIENCE",
            "Does the DPR show the promoter's experience in this business "
            "with at least one specific detail -- years in the work, a "
            "previous business or job, machines owned or run before, or a "
            "training or certificate? 'issue' if it only calls the promoter "
            "experienced, or says nothing.",
            "Your experience in this work is not shown",
            "risk",
            "medium",
            "should_fix",
            "Describe your experience or training in this kind of work.",
        ),
        _item(
            "RISK_SENSITIVITY",
            "Does the DPR show what happens to repayment if price or volume falls?",
            "No check of what happens if prices or sales fall",
            "assumption",
            "low",
            "good_to_fix",
            "Add a small table showing whether you can still repay the loan "
            "if prices or sales fall by 10-20%.",
        ),
    ),
}

# What each item's finding prints in the action plan: why the bank cares,
# and what the corrected DPR should show. Fixed text, like the title.
WHY_AND_SHOULD_BE: dict[str, tuple[str, str]] = {
    "DOC_QUOTATION": (
        "The bank fixes the loan on the supplier's quoted price",
        "Current quotation, total = project cost",
    ),
    "DOC_IDENTITY": (
        "Mixed-up details make the bank doubt the whole DPR",
        "Your own details on every page",
    ),
    "DOC_PREMISES": (
        "The bank needs proof of where the project will run",
        "Site stated, with ownership or rent papers",
    ),
    "FIN_WORKING_CAPITAL": (
        "Without it, you cannot pay fuel and wages before income comes in",
        "Money for the first months' costs in the project cost",
    ),
    "FIN_INTEREST_IN_PNL": (
        "Profit and your ability to repay look better than they are",
        "Loan interest shown as a cost every year",
    ),
    "FIN_STATEMENTS_CONSISTENT": (
        "The bank returns a DPR whose tables disagree",
        "Same figures for each year in every table",
    ),
    "FIN_SUBSIDY_AS_FUNDING": (
        "A subsidy that is not approved may never arrive",
        "Funding complete without the subsidy",
    ),
    "FIN_DEPRECIATION": (
        "Wrong depreciation makes profit and tax wrong",
        "Depreciation = cost x stated yearly rate",
    ),
    "FIN_TAX": (
        "Profit and your ability to repay look better than they are",
        "Income tax shown every year",
    ),
    "FIN_BREAKEVEN_BASIS": (
        "The business looks safer than it is",
        "Break-even on all running costs",
    ),
    "SEC_CAPACITY_REALISTIC": (
        "Your income depends on work the machine may not manage",
        "Maker's rating and real local working days",
    ),
    "SEC_PRICE_REALISTIC": (
        "Your income may be shown higher than it will be",
        "Price backed by local market rates",
    ),
    "SEC_RUNNING_COSTS_COMPLETE": (
        "Leaving out costs makes profit look higher than it is",
        "Every running cost, off season included",
    ),
    "SEC_SEASONALITY": (
        "Money may run short in the months with no work",
        "Income and costs shown by season",
    ),
    "SEC_INPUTS_AVAILABLE": (
        "The bank checks the project can actually run",
        "Local sources of inputs and workers named",
    ),
    "SEC_DEMAND_EVIDENCE": (
        "The bank needs to see who will pay you",
        "Buyer letters, bookings or past sales attached",
    ),
    "DOC_QUOTATION_FIRM": (
        "A price that is not final can rise before you buy",
        "Dated quotation, delivered price with GST",
    ),
    "DOC_ASSET_SAME": (
        "The bank checks the loan is for the machine you will buy",
        "Same make and model on every page",
    ),
    "FIN_YEARS_CURRENT": (
        "Old or missing years make the bank doubt the figures",
        "Every project year in every table",
    ),
    "FIN_COST_COMPLETE": (
        "Anything left out you will have to pay for yourself",
        "Every planned item in the project cost",
    ),
    "FIN_LOAN_REPAID": (
        "The bank will not lend if the table leaves the loan unpaid",
        "Whole loan repaid within the loan period",
    ),
    "FIN_INTEREST_SUBSIDY": (
        "The bank may charge the full interest if it is unclear",
        "Scheme named, interest shown before and after",
    ),
    "FIN_REVENUE_BUILT_UP": (
        "The bank needs to see how you reach your income",
        "Amount of work x rate for each year",
    ),
    "SEC_FUEL_REALISTIC": (
        "Too little fuel makes profit look higher than it is",
        "Fuel use backed by the maker or local operators",
    ),
    "SEC_MAINTENANCE_RISES": (
        "Older machines cost more to keep running",
        "Repair cost rising each year",
    ),
    "RISK_SINGLE_ASSET": (
        "If it breaks down or is damaged, your income stops",
        "Machine insured, with a service or back-up plan",
    ),
    "RISK_DEMAND_CONCENTRATION": (
        "Losing that one buyer would stop your income",
        "More than one source of customers",
    ),
    "RISK_PROMOTER_EXPERIENCE": (
        "The bank lends to people who can run the business",
        "Your experience or training described",
    ),
    "RISK_SENSITIVITY": (
        "The bank checks you can still repay in a bad year",
        "Table for lower prices and sales",
    ),
}

# The topic of each item, as words a restatement of it uses in its title.
# The verifier is asked for issues no candidate covers, yet sometimes re-adds
# a checklist finding under new words (seen live on 912a: "No documented
# proof of demand" next to "No evidence of demand for the output").
# finding_judge.absorb_restatements folds such a finding into the item's.
# FIN_STATEMENTS_CONSISTENT matches only figures said to disagree between
# named parts of the DPR.
TOPIC_TERMS: dict[str, re.Pattern] = {
    key: re.compile(pattern, re.I)
    for key, pattern in {
        # Not a quotation's firmness (DOC_QUOTATION_FIRM), only its absence
        # or mismatch.
        "DOC_QUOTATION": r"^(?!.*(?:indicative|ex[\s-]*works|validity|firm|"
        r"escalation|non[\s-]*binding)).*(?:quotation|pro[\s-]*forma|invoice)",
        "DOC_QUOTATION_FIRM": r"indicative|ex[\s-]*works|validity|price firmness|"
        r"non[\s-]*binding|(?:price|quotation).*(?:escalation|subject to)|"
        r"quot\w*\b.*\b(?:exclud\w*|without|omits?|not includ\w*)\b.*"
        r"(?:transport|freight|delivery|gst|transit|insurance)",
        "DOC_ASSET_SAME": r"(?:harvester|machine|machinery|equipment|tractor|"
        r"combine)\b.{0,20}\bmodel|model (?:name|number|specifications?|"
        r"designations?)|(?:make|model|specifications?)\b.*\b(?:not "
        r"(?:mentioned|specified|given|stated|named)|missing|absent|"
        r"unspecified)|(?:unspecified|unnamed) (?:machine|make|model)",
        "FIN_YEARS_CURRENT": r"templated? (?:base )?(?:years|labels|headers|dates)|"
        r"outdated|stale|base (?:financial )?years?|alternate years|"
        r"intermediate (?:projection )?years|(?:skip|skips|skipped) .*years|"
        r"biennial",
        "DOC_IDENTITY": r"applicant (?:name|details)|identity|"
        r"another (?:customer|borrower)",
        # "land" alone matched "Land cost included in project cost".
        "DOC_PREMISES": r"premises|land (?:title|document|deed|ownership|record|"
        r"lease|not (?:evidenced|shown|documented))|title document|lease",
        # Plain wording too (the agents avoid "working capital" since the
        # plain-language instruction): "no upfront money budgeted for
        # initial operational expenses" (2026-11-25).
        "FIN_WORKING_CAPITAL": r"working[\s-]*capital (?:margin|provision|allocation)|"
        r"(?:no|upfront|initial|zero) working[\s-]*capital|"
        r"(?:upfront|start[\s-]*up|starting) (?:money|cash|funds?)|"
        r"(?:initial|first[\s-]*months?'?) (?:running|operating|operational) "
        r"(?:costs|expenses)",
        "FIN_INTEREST_IN_PNL": r"interest\b.*\b(?:missing|omitted|not charged)",
        "FIN_SUBSIDY_AS_FUNDING": r"subsidy\b.*\b"
        r"(?:funding|means of finance|unsanctioned)",
        "FIN_DEPRECIATION": r"depreciation",
        "FIN_TAX": r"\btax\b",
        # A shed or garage as a cost only; its land papers are DOC_PREMISES.
        "FIN_COST_COMPLETE": r"\bgps\b|sensors?|telemetry|trolley|freight|"
        r"transit (?:cost|expense|charge|insurance)|machinery transit|"
        r"transport (?:cost|charges)|\bgst\b|accessor|"
        r"(?:shed|garag\w*) (?:cost|construction|not (?:costed|budgeted))",
        # "Amortization" alone also names statements that disagree with the
        # schedule (FIN_STATEMENTS_CONSISTENT); only an unpaid loan is this.
        "FIN_LOAN_REPAID": r"negative (?:debt )?amorti|"
        r"(?:incomplete|truncated|partial|not fully|fails to|does not fully|"
        r"full|fully) (?:\w+ ){0,2}amorti|amorti[sz]\w* .*(?:residual|unpaid|"
        r"maturity|incomplete)|residual (?:\w+ ){0,2}balance|"
        r"unpaid (?:\w+ ){0,2}balance|balloon|interest capitali[sz]|"
        r"capitali[sz]\w* .*interest|not fully (?:re)?paid|"
        r"(?:loan|principal)\b.*\boutstanding (?:balance|at the end)",
        "FIN_INTEREST_SUBSIDY": r"interest (?:subsidy|subvention)|subvention",
        "FIN_REVENUE_BUILT_UP": r"revenue\b.*\b(?:derivation|derived|build[\s-]*up|"
        r"break[\s-]*up|calculation|unit[\s-]*level|basis)|"
        r"(?:revenue|income) (?:growth|escalation)|compound\w* .*(?:growth|"
        r"escalation)|(?:dual|annual) escalation|(?:revenue|income)\b.*\b"
        r"(?:billing rates?|acreage|lump[\s-]*sum|breakdown)",
        "FIN_BREAKEVEN_BASIS": r"break[\s-]*even",
        # Figures that disagree between parts of the DPR, in the words seen
        # live: "Variable cost per acre conflicts across your report",
        # "Stated average DSCR conflicts with project tables".
        "FIN_STATEMENTS_CONSISTENT": r"(?:conflicts?|inconsistent|differs?|"
        r"mismatch\w*|disagree\w*) (?:across|between|with) (?:your |the )?"
        r"(?:report|pages|project tables|tables|sections|sheets|schedules|"
        r"statements)",
        "SEC_CAPACITY_REALISTIC": r"capacity|operating days|"
        r"acres? (?:per|a) (?:day|year)",
        "SEC_PRICE_REALISTIC": r"\bprice|rate per|tariff",
        "SEC_RUNNING_COSTS_COMPLETE": r"costs? (?:are )?(?:incomplete|omitted|missing|"
        r"left out)|omission of .*(?:cost|expense|wage)",
        "SEC_SEASONALITY": r"season",
        "SEC_INPUTS_AVAILABLE": r"availability of (?:inputs|labour|operators)|"
        r"input supply",
        "SEC_DEMAND_EVIDENCE": r"demand|(?:advance )?booking agreements?|"
        r"customer proof|proof of customers|buyer (?:letters?|agreements?)|"
        r"tie[\s-]*ups?|advance (?:bookings?|agreements?)|customer letters?|"
        r"farmer commitments?|booking proof",
        # "insur" also folds "Single High-Value Asset Risk Without Stated
        # Insurance" and "Omission of Machine Insurance ..." (seen live on
        # PSR/2026/000010, where insurance appeared three times).
        "SEC_FUEL_REALISTIC": r"fuel (?:consumption|use|usage|rate|norm)|"
        r"diesel consumption|litres? (?:per|an?) (?:acre|hour)",
        "SEC_MAINTENANCE_RISES": r"maintenance (?:budget|cost|expense|provision)s?\b"
        r".*(?:flat|static|fixed|constant|ag(?:e|ing)|older|low|rise)|"
        r"(?:flat|static|fixed|constant) .*maintenance|"
        r"repair costs? .*(?:flat|rise|older|ag(?:e|ing))",
        "RISK_SINGLE_ASSET": r"single[\s-]*asset|one asset|breakdown|asset dependen|"
        r"high[\s-]*value asset|(?<!transit )\binsur(?:ance|ed)\b|"
        r"maintenance contract|"
        r"service (?:back[\s-]*up|contract)",
        "RISK_DEMAND_CONCENTRATION": r"(?:buyer|channel|customer) concentration|"
        r"single (?:buyer|channel|customer)",
        # "promoter" alone matched promoter *contribution* findings.
        "RISK_PROMOTER_EXPERIENCE": r"promoter'?s?'? (?:experience|background|"
        r"track record|qualification)|track record|experience",
        "RISK_SENSITIVITY": r"sensitivity|stress[\s-]*test",
    }.items()
}

# When a restated title matches several items, the most specific wins: the
# 2026-11-25 items name one narrow topic, while older patterns such as
# SEC_PRICE_REALISTIC ("price", "tariff") or SEC_SEASONALITY ("season") are
# broad ("Harvester quotation is indicative and subject to price changes" is
# about the quotation, not the selling price).
_SPECIFIC_FIRST = (
    "DOC_ASSET_SAME",
    "DOC_QUOTATION_FIRM",
    "FIN_LOAN_REPAID",
    "FIN_INTEREST_SUBSIDY",
    "FIN_REVENUE_BUILT_UP",
    "FIN_YEARS_CURRENT",
    "FIN_COST_COMPLETE",
    "SEC_FUEL_REALISTIC",
    "SEC_MAINTENANCE_RISES",
)
TOPIC_PRECEDENCE: dict[str, int] = {
    key: i
    for i, key in enumerate(
        [*_SPECIFIC_FIRST, *(k for k in TOPIC_TERMS if k not in _SPECIFIC_FIRST)]
    )
}

# Every role that writes findings gets this: the report's "What to fix"
# table is read by the applicant (often a farmer or small business owner),
# and AI titles like "Obsolete Base Year Labels in Operational Schedules"
# were hard to follow (PSR/2026/000010, 2026-11-25).
PLAIN_LANGUAGE_INSTRUCTION = (
    " WRITING STYLE for every entry in 'findings': the applicant reads them, "
    "so use simple everyday English and speak to them as 'you' / 'your DPR'. "
    "title: one plain statement of what is wrong, at most 10 words, in "
    "sentence case (capital first letter and capitals for names of places, "
    "firms and schemes; not Title Case), no banking jargon. explanation: one or "
    "two short sentences saying what you saw in their DPR; if a technical "
    "term is unavoidable, explain it in brackets. suggested_action: one "
    "instruction that starts with a verb and says exactly what to add or "
    "change. evidence_quote stays word for word from the page."
)

FINDING_TEXT_INSTRUCTION = (
    " For each entry in 'findings' also give why_it_matters (one plain "
    "clause, at most 12 words, saying what it means for the applicant, e.g. "
    "'The bank may cut the loan or ask for this'), and, only when the supplied pages "
    "show them, dpr_stated_value (the DPR's figure or statement, at most 8 "
    "words) and should_be (the corrected figure or what the DPR should "
    "show, at most 8 words); never invent a figure."
)

CHECKLIST_INSTRUCTION = (
    " CHECKLIST: answer every item in 'checklist' in 'checks', one entry per "
    "id: status 'issue' (this DPR has the problem -- give an explanation of "
    "at most two sentences, evidence_page and a short verbatim "
    "evidence_quote), 'ok' (checked; no problem), 'not_applicable', or "
    "'cannot_tell' (the supplied pages do not show enough: use it only "
    "when the page the question is about is not supplied at all, never "
    "because the DPR gives too little detail -- missing detail is an "
    "'issue'). Leave "
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


# A repayment table may fall short of the loan by rounding (seen live: Rs
# 5,000 of Rs 22 lakh, 0.2%, called "issue" in one run and "ok" the next).
LOAN_REPAID_TOLERANCE = 0.01  # share of the loan


def code_checklist_answers(role: str, financials) -> dict[str, ChecklistAnswer]:
    """Checklist items code can answer from the extracted figures, so they
    get the same answer every run. Each is left to the agent when a figure
    it needs is missing."""
    if role != "finance" or financials is None:
        return {}
    answers: dict[str, ChecklistAnswer] = {}
    loan = financials.term_loan_amount.value
    rows = financials.repayment_table
    principal = [r.principal_due.value for r in rows]
    if (
        loan
        and rows
        and all(p is not None for p in principal)
        # A negative year is unpaid interest added to the loan: the agent
        # reads that from the schedule.
        and all(p >= 0 for p in principal)
    ):
        short = loan - sum(principal)
        if abs(short) > loan / 2:
            # Far off: more likely a unit mix-up (rupees against lakh) than
            # an unpaid loan.
            return answers
        page = next((r.principal_due.page for r in rows if r.principal_due.page), None)
        if short > loan * LOAN_REPAID_TOLERANCE:
            answers["FIN_LOAN_REPAID"] = ChecklistAnswer(
                check_id="FIN_LOAN_REPAID",
                status="issue",
                explanation=(
                    f"Your repayment table pays back Rs {sum(principal):.2f} "
                    f"lakh of the Rs {loan:.2f} lakh loan; Rs {short:.2f} lakh "
                    "is still unpaid at the end."
                ),
                evidence_page=page,
            )
        elif short >= -loan * LOAN_REPAID_TOLERANCE:
            answers["FIN_LOAN_REPAID"] = ChecklistAnswer(
                check_id="FIN_LOAN_REPAID", status="ok"
            )
    if (answer := _tax_answer(financials)) is not None:
        answers["FIN_TAX"] = answer
    if (answer := _depreciation_answer(financials)) is not None:
        answers["FIN_DEPRECIATION"] = answer
    return answers


_FIGURE_TOLERANCE = 0.02  # lakh: two-decimal rounding in printed tables

# The free-form titles that restate a code-answered item. Narrower than
# TOPIC_TERMS ("tax" alone would also catch road tax in the project cost):
# these findings are dropped, since code has settled the question either
# way. Seen live (e3aae76bbdce run 2): "Term loan is not fully paid off in
# seven years" for a Rs 5,000 rounding gap code had answered "ok".
CODE_SETTLED_TERMS: dict[str, re.Pattern] = {
    "FIN_LOAN_REPAID": TOPIC_TERMS["FIN_LOAN_REPAID"],
    "FIN_TAX": re.compile(
        r"income[\s-]*tax|tax (?:provision|liability|charge|rate|trend)|"
        r"(?:no|zero|missing|irregular) tax",
        re.I,
    ),
    "FIN_DEPRECIATION": re.compile(r"depreciation", re.I),
}


def drop_code_settled(output: AgentOutput, code_answers: dict) -> AgentOutput:
    """Removes free-form findings restating an item code has answered."""
    patterns = [CODE_SETTLED_TERMS[i] for i in code_answers if i in CODE_SETTLED_TERMS]
    if not patterns:
        return output
    return output.model_copy(
        update={
            "findings": [
                f
                for f in output.findings
                if f.check_id or not any(p.search(f.title) for p in patterns)
            ]
        }
    )


def _tax_answer(financials) -> ChecklistAnswer | None:
    """'issue' when tax falls in a year profit rises; 'ok' when tax is
    charged in every profitable year (the first may have none: an
    allowance or a part year) and never falls against profit. Seen live
    (e3aae76bbdce): tax rising with profit from year 2, none in year 1,
    "issue" in one run and "ok" in the next. Anything else (no tax read at
    all, several untaxed profitable years) is left to the agent."""
    years = sorted(financials.years, key=lambda y: y.year)
    if not any(y.tax_paid for y in years):
        return None
    for a, b in zip(years, years[1:], strict=False):
        if None in (a.tax_paid, b.tax_paid, a.net_profit, b.net_profit):
            continue
        if (
            b.net_profit > a.net_profit + _FIGURE_TOLERANCE
            and b.tax_paid < a.tax_paid - _FIGURE_TOLERANCE
        ):
            return ChecklistAnswer(
                check_id="FIN_TAX",
                status="issue",
                explanation=(
                    f"Your tax falls from Rs {a.tax_paid:.2f} lakh in year "
                    f"{a.year} to Rs {b.tax_paid:.2f} lakh in year {b.year}, "
                    "while your profit rises."
                ),
            )
    untaxed = [
        y
        for y in years[1:]
        if y.net_profit is not None and y.net_profit > 0 and not y.tax_paid
    ]
    if untaxed:
        return None
    return ChecklistAnswer(check_id="FIN_TAX", status="ok")


def _depreciation_answer(financials) -> ChecklistAnswer | None:
    """'ok' when the yearly depreciation follows one method on a base no
    larger than the project cost: straight line (equal amounts, a smaller
    last year allowed) or reducing balance (a constant yearly ratio). Seen
    live (e3aae76bbdce): a clean 15% reducing balance on Rs 29.40 lakh
    against a Rs 29.30 lakh cost, "issue" in one run and "ok" the next.
    Anything else is left to the agent."""
    dep = [
        y.depreciation
        for y in sorted(financials.years, key=lambda y: y.year)
        if y.depreciation is not None
    ]
    cost = financials.cost_of_project_stated_total or sum(
        c.amount for c in financials.cost_of_project_components
    )
    if len(dep) < 3 or not cost or dep[0] <= 0:
        return None
    ceiling = cost * 1.02
    straight = all(abs(d - dep[0]) <= _FIGURE_TOLERANCE for d in dep[:-1]) and (
        dep[-1] <= dep[0] + _FIGURE_TOLERANCE
    )
    if straight:
        return (
            ChecklistAnswer(check_id="FIN_DEPRECIATION", status="ok")
            if sum(dep) <= ceiling
            else None
        )
    ratios = [b / a for a, b in zip(dep, dep[1:], strict=False) if a > 0]
    mean = sum(ratios) / len(ratios)
    if not 0 < mean < 1 or any(abs(r - mean) > 0.01 for r in ratios):
        return None
    base = dep[0] / (1 - mean)
    if base > ceiling:
        return None
    return ChecklistAnswer(check_id="FIN_DEPRECIATION", status="ok")


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
