"""Renders a completed AnalysisResult as a PDF.

Pure rendering: never computes, infers or adds anything the stored JSON
doesn't already contain. The JSON (report_store.py) is the single source of
truth; a PDF is regenerated from it on every request, never stored itself,
so there is no risk of the two drifting apart.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .schemas import AnalysisResult, Finding

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_SEVERITY_COLOR = {
    "critical": colors.HexColor("#b00020"),
    "high": colors.HexColor("#d84315"),
    "medium": colors.HexColor("#f9a825"),
    "low": colors.HexColor("#2e7d32"),
    "info": colors.HexColor("#616161"),
}
_PROVENANCE_LABEL = {
    "calculated": "Calculated -- code-verified, not AI judgment",
    "extracted": "Extracted from document",
    "ai_interpreted": "AI interpretation",
    "assumption": "Assumption",
    "unverified": "Unverified",
}


def _styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            "Meta",
            parent=styles["Normal"],
            fontSize=9,
            textColor=colors.HexColor("#555555"),
        )
    )
    styles.add(
        ParagraphStyle(
            "FindingTitle", parent=styles["Heading4"], spaceBefore=10, spaceAfter=2
        )
    )
    styles.add(
        ParagraphStyle(
            "FindingMeta",
            parent=styles["Normal"],
            fontSize=8,
            textColor=colors.HexColor("#555555"),
        )
    )
    return styles


def _meta_table(result: AnalysisResult, styles) -> Table:
    rows = [
        ["Service request", result.service_request_id],
        ["Wallet document", result.wallet_document_id],
        ["Document SHA-256", result.document_sha256],
        ["Analysis status", result.status],
    ]
    table = Table(
        [
            [Paragraph(f"<b>{k}</b>", styles["Normal"]), Paragraph(v, styles["Normal"])]
            for k, v in rows
        ],
        colWidths=[4 * cm, 12 * cm],
    )
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd")),
            ]
        )
    )
    return table


def _finding_block(f: Finding, styles) -> list:
    color = _SEVERITY_COLOR.get(f.severity, colors.black)
    flowables = [
        Paragraph(
            f'<font color="{color.hexval()}">&#9632;</font> {f.title}',
            styles["FindingTitle"],
        ),
        Paragraph(
            f"Category: {f.category} &nbsp;|&nbsp; "
            f"Severity: {f.severity} &nbsp;|&nbsp; "
            f"{_PROVENANCE_LABEL.get(f.provenance, f.provenance)} &nbsp;|&nbsp; "
            f"Verification: {f.verification}",
            styles["FindingMeta"],
        ),
        Paragraph(f.explanation, styles["Normal"]),
    ]
    if f.evidence_quote:
        page = f" (page {f.evidence_page})" if f.evidence_page is not None else ""
        flowables.append(
            Paragraph(f'Source quote{page}: "{f.evidence_quote}"', styles["Meta"])
        )
    flowables.append(
        Paragraph(f"Suggested action: {f.suggested_action}", styles["Normal"])
    )
    return flowables


def render_report(result: AnalysisResult) -> bytes:
    styles = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
    )
    story = [
        Paragraph("DPR Analysis Report", styles["Title"]),
        Paragraph(
            f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
            styles["Meta"],
        ),
        Spacer(1, 0.4 * cm),
        _meta_table(result, styles),
        Spacer(1, 0.4 * cm),
        Paragraph(result.disclaimer, styles["Italic"]),
        Spacer(1, 0.6 * cm),
    ]

    calculated = [f for f in result.findings if f.provenance == "calculated"]
    interpreted = [f for f in result.findings if f.provenance != "calculated"]

    story.append(Paragraph("Calculated Figures", styles["Heading2"]))
    if calculated:
        for f in calculated:
            story.extend(_finding_block(f, styles))
    else:
        story.append(
            Paragraph(
                "No figures could be independently recomputed from this document.",
                styles["Normal"],
            )
        )
    story.append(Spacer(1, 0.5 * cm))

    story.append(Paragraph("Findings", styles["Heading2"]))
    if interpreted:
        for f in sorted(interpreted, key=lambda f: _SEVERITY_ORDER.get(f.severity, 99)):
            story.extend(_finding_block(f, styles))
    else:
        story.append(Paragraph("No additional findings.", styles["Normal"]))
    story.append(Spacer(1, 0.5 * cm))

    story.append(Paragraph("Open Questions", styles["Heading2"]))
    if result.unresolved_questions:
        story.append(
            ListFlowable(
                [
                    ListItem(Paragraph(q, styles["Normal"]))
                    for q in result.unresolved_questions
                ],
                bulletType="bullet",
            )
        )
    else:
        story.append(Paragraph("None.", styles["Normal"]))

    doc.build(story)
    return buf.getvalue()
