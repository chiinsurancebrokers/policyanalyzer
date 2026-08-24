"""
Builds the client-facing PDF report: applicant profile, per-provider
extraction detail (with critical limitations called out), HAL's
recommendation and reasoning, and GPT's second opinion on that
recommendation, if available.
"""
from __future__ import annotations

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

BRAND_NAVY = colors.HexColor("#1a2b4c")
BRAND_ACCENT = colors.HexColor("#c9932f")
WARNING_BG = colors.HexColor("#fff4e0")

_styles = getSampleStyleSheet()
_styles.add(ParagraphStyle(name="AshlarTitle", parent=_styles["Title"], textColor=BRAND_NAVY, fontSize=20))
_styles.add(ParagraphStyle(name="AshlarH2", parent=_styles["Heading2"], textColor=BRAND_NAVY, spaceBefore=14))
_styles.add(ParagraphStyle(name="AshlarH3", parent=_styles["Heading3"], textColor=BRAND_ACCENT, spaceBefore=8))
_styles.add(ParagraphStyle(name="AshlarBody", parent=_styles["Normal"], fontSize=9.5, leading=13, alignment=TA_LEFT))
_styles.add(ParagraphStyle(name="AshlarSmall", parent=_styles["Normal"], fontSize=8, textColor=colors.grey))
_styles.add(ParagraphStyle(name="AshlarWarning", parent=_styles["Normal"], fontSize=9.5, leading=13, textColor=colors.HexColor("#7a4a00")))


def _p(text: str, style: str = "AshlarBody") -> Paragraph:
    return Paragraph(str(text).replace("\n", "<br/>"), _styles[style])


def _bullet_list(items: list[str], style: str = "AshlarBody") -> ListFlowable:
    return ListFlowable(
        [ListItem(_p(item, style)) for item in items],
        bulletType="bullet",
        start="circle",
        leftIndent=12,
    )


def _applicant_table(profile: dict) -> Table:
    rows = [["Field", "Applicant details"]]
    labels = {
        "age_range": "Age(s) / dependents",
        "residence_country": "Country of residence",
        "travel_area": "Area of cover needed",
        "budget_annual": "Annual budget",
        "priorities": "Stated priorities",
        "known_conditions": "Known / pre-existing conditions",
        "notes": "Additional notes",
    }
    for key, label in labels.items():
        value = profile.get(key)
        if isinstance(value, list):
            value = ", ".join(value) if value else "—"
        rows.append([Paragraph(label, _styles["AshlarSmall"]), Paragraph(str(value) if value else "—", _styles["AshlarSmall"])])

    table = Table(rows, colWidths=[55 * mm, 115 * mm])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f9")]),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def _comparison_table(records: list[dict]) -> Table:
    header = ["Provider", "Plan", "Deductible", "Excess", "Annual Limit", "Area of Cover", "Underwriting"]
    rows = [header]
    for r in records:
        data = r["analysis"]
        underwriting = data.get("underwriting", {}) or {}
        rows.append([
            r["provider"],
            data.get("plan_name") or "—",
            data.get("deductible", "N/A"),
            data.get("excess", "N/A"),
            data.get("annual_limit", "N/A"),
            data.get("area_of_cover", "N/A"),
            underwriting.get("basis", "N/A"),
        ])
    # wrap long cells in Paragraphs so the table doesn't overflow the page
    wrapped_rows = [header] + [
        [Paragraph(str(c), _styles["AshlarSmall"]) for c in row] for row in rows[1:]
    ]
    col_widths = [26 * mm, 22 * mm, 22 * mm, 20 * mm, 22 * mm, 30 * mm, 28 * mm]
    table = Table(wrapped_rows, colWidths=col_widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7f7f9")]),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def _provider_section(record: dict) -> list:
    data = record["analysis"]
    flowables = [Paragraph(f"{record['provider']}" + (f" — {data.get('plan_name')}" if data.get("plan_name") else ""), _styles["AshlarH3"])]
    flowables.append(_p(data.get("coverage_summary", "—")))

    cat_labels = {
        "mental_health": "Mental health", "outpatient": "Outpatient", "inpatient": "Inpatient",
        "emergency": "Emergency", "dental": "Dental", "maternity": "Maternity", "preventive": "Preventive",
    }
    categories = data.get("categories", {})
    cat_rows = [[cat_labels[k], categories.get(k, "N/A")] for k in cat_labels]
    cat_table = Table(
        [[Paragraph(a, _styles["AshlarSmall"]), Paragraph(str(b), _styles["AshlarSmall"])] for a, b in cat_rows],
        colWidths=[35 * mm, 135 * mm],
    )
    cat_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    flowables.append(Spacer(1, 4))
    flowables.append(cat_table)

    limitations = data.get("critical_limitations", [])
    if limitations:
        flowables.append(Spacer(1, 6))
        flowables.append(Paragraph("⚠ Critical limitations to flag with the client", _styles["AshlarH3"]))
        items = [f"<b>{item.get('topic', '')}:</b> {item.get('detail', '')}" for item in limitations]
        warn_table = Table([[_bullet_list(items, "AshlarWarning")]], colWidths=[170 * mm])
        warn_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), WARNING_BG),
            ("BOX", (0, 0), (-1, -1), 0.5, BRAND_ACCENT),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ]))
        flowables.append(warn_table)

    flowables.append(Spacer(1, 4))
    flowables.append(_p(f"<b>Exclusions:</b> {data.get('exclusions_summary', 'N/A')}"))
    flowables.append(_p(f"<b>Waiting periods:</b> {(data.get('underwriting') or {}).get('waiting_periods', 'N/A')}"))
    method = data.get("method", "")
    if data.get("confidence") == "low":
        flowables.append(_p("⚠ Low-confidence extraction — verify these figures against the source document.", "AshlarWarning"))
    if method.startswith("rule-based fallback"):
        flowables.append(_p(f"⚠ AI extraction did not run for this policy — {method}", "AshlarWarning"))
    flowables.append(Spacer(1, 8))
    return flowables


def _recommendation_section(hal: dict) -> list:
    flowables = [Paragraph("HAL's Recommendation", _styles["AshlarH2"])]
    if "error" in hal:
        flowables.append(_p(f"Recommendation unavailable: {hal['error']}"))
        return flowables

    flowables.append(_p(f"<b>Recommended plan: {hal.get('recommended_provider', '—')}</b>"))
    flowables.append(Spacer(1, 4))
    flowables.append(_p(hal.get("reasoning", "")))

    ranking = hal.get("ranking", [])
    if ranking:
        flowables.append(Spacer(1, 6))
        flowables.append(Paragraph("Ranking", _styles["AshlarH3"]))
        flowables.append(_bullet_list([f"<b>{r.get('provider', '')}:</b> {r.get('fit_summary', '')}" for r in ranking]))

    tradeoffs = hal.get("key_tradeoffs", [])
    if tradeoffs:
        flowables.append(Spacer(1, 6))
        flowables.append(Paragraph("Key trade-offs", _styles["AshlarH3"]))
        flowables.append(_bullet_list(tradeoffs))

    caveats = hal.get("flagged_caveats", [])
    if caveats:
        flowables.append(Spacer(1, 6))
        flowables.append(Paragraph("Caveats flagged for this client", _styles["AshlarH3"]))
        warn_table = Table([[_bullet_list(caveats, "AshlarWarning")]], colWidths=[170 * mm])
        warn_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), WARNING_BG),
            ("BOX", (0, 0), (-1, -1), 0.5, BRAND_ACCENT),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ]))
        flowables.append(warn_table)

    flowables.append(Spacer(1, 4))
    flowables.append(_p(f"<i>HAL confidence: {hal.get('confidence', 'unknown')}</i>", "AshlarSmall"))
    return flowables


def _second_opinion_section(verification: dict) -> list:
    flowables = [Paragraph("Second Opinion (ChatGPT review of HAL's recommendation)", _styles["AshlarH2"])]
    if "error" in verification:
        flowables.append(_p(f"Second opinion unavailable: {verification['error']}"))
        return flowables

    agreement = verification.get("agreement", "unknown")
    icon = {"agree": "✓", "partial": "~", "disagree": "✗"}.get(agreement, "?")
    flowables.append(_p(f"<b>Verdict: {icon} {agreement.upper()}</b>"))
    if verification.get("alternative_provider"):
        flowables.append(_p(f"<b>Alternative suggested:</b> {verification['alternative_provider']}"))
    flowables.append(Spacer(1, 4))
    flowables.append(_p(verification.get("reasoning", "")))

    concerns = verification.get("concerns", [])
    if concerns:
        flowables.append(Spacer(1, 6))
        flowables.append(Paragraph("Concerns raised", _styles["AshlarH3"]))
        flowables.append(_bullet_list(concerns))

    flowables.append(Spacer(1, 4))
    flowables.append(_p(verification.get("notes", ""), "AshlarSmall"))
    return flowables


def build_pdf_bytes(
    applicant_profile: dict,
    records: list[dict],
    hal_recommendation: dict | None = None,
    second_opinion: dict | None = None,
    broker_name: str = "Ashlar Insurance",
) -> bytes:
    """Assembles the full client-facing report. records: list of dicts with
    'provider', 'analysis' (extraction dict), 'content_hash'."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=18 * mm, bottomMargin=16 * mm, leftMargin=18 * mm, rightMargin=18 * mm,
    )
    story = []

    story.append(Paragraph(f"{broker_name} — Policy Comparison Report", _styles["AshlarTitle"]))
    story.append(_p(f"Generated {datetime.now().strftime('%d %B %Y, %H:%M')}", "AshlarSmall"))
    story.append(Spacer(1, 12))

    if applicant_profile:
        story.append(Paragraph("Applicant Needs", _styles["AshlarH2"]))
        story.append(_applicant_table(applicant_profile))
        story.append(Spacer(1, 10))

    story.append(Paragraph("Comparison Summary", _styles["AshlarH2"]))
    story.append(_comparison_table(records))
    story.append(Spacer(1, 10))

    story.append(Paragraph("Policy Detail", _styles["AshlarH2"]))
    for record in records:
        story.append(KeepTogether(_provider_section(record)))

    if hal_recommendation:
        story.append(Spacer(1, 6))
        story.append(KeepTogether(_recommendation_section(hal_recommendation)))

    if second_opinion:
        story.append(Spacer(1, 6))
        story.append(KeepTogether(_second_opinion_section(second_opinion)))

    story.append(Spacer(1, 16))
    story.append(_p(
        "This report is generated by AI-assisted analysis of policy wording documents for comparison "
        "purposes and does not constitute the full terms of any policy. Always refer to the insurer's "
        "official policy wording and terms & conditions before purchase. Prepared by "
        f"{broker_name}.",
        "AshlarSmall",
    ))

    doc.build(story)
    return buffer.getvalue()
