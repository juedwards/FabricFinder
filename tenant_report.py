"""Generate a detailed tenant PDF report from a structured spec.

The bot (LLM) gathers the data via run_sql and assembles a single dict matching
:func:`render_tenant_pdf`'s schema. We render charts with matplotlib (PNG) and
compose the PDF with ReportLab. The output is intentionally fact-only — every
number originates from the warehouse queries the model ran; this module adds no
inferences of its own beyond the layout.

Spec schema (all keys optional unless noted; missing data shows "n/a"):

    {
      "tenant_name": str,                 # required
      "tenant_id":   str,
      "geography":   {"country": str, "region": str, "city": str},
      "status":      "HEALTHY" | "INTENSIVE" | "PIPELINE" | "DECLINE",
      "status_rationale": str,            # facts only, no assumptions
      "current_month": {"month": "YYYY-MM", "mau": int, "nua": int},
      # NOTE: current_month must be the LAST FULL month (never the partial
      # in-progress one), so YoY comparisons aren't skewed by an incomplete
      # period.
      "yoy": {                            # single-month YoY vs same month -12
        "prior_month": "YYYY-MM",
        "prior_mau": int,  "prior_nua": int,
        "mau_change_pct": float, "nua_change_pct": float,
      },
      "yoy_6m": {                         # trailing-6-month YoY (seasonality)
        "metric": "sum" | "avg",
        "current_period": "YYYY-MM..YYYY-MM",
        "prior_period":   "YYYY-MM..YYYY-MM",
        "current_mau": int, "current_nua": int,
        "prior_mau":   int, "prior_nua":   int,
        "mau_change_pct": float, "nua_change_pct": float,
      },
      "monthly_series": [                 # full long-view, oldest first
        {"month": "YYYY-MM", "mau": int, "nua": int}, ...
      ],
      "top_content":  [{"name": str, "sessions": int}, ...],
      "device_breakdown": [{"device": str, "value": int}, ...],
      "highlights":   [str, ...],         # short, factual bullets
      "data_sources": [str, ...],         # optional list of SQL summaries
    }
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from typing import Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import LETTER  # noqa: E402
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402
from reportlab.pdfbase import pdfmetrics  # noqa: E402
from reportlab.pdfbase.ttfonts import TTFont  # noqa: E402
from reportlab.platypus import (  # noqa: E402
    Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)


# --------------------------------------------------------------------------- #
# Unicode font registration
# --------------------------------------------------------------------------- #
# ReportLab's built-in Helvetica uses WinAnsi encoding and renders many
# Unicode characters (Δ, U+2011 non-breaking hyphen, U+2212 minus sign, etc.)
# as the missing-glyph box (■). Register DejaVu Sans (ships with matplotlib)
# so all our styles can render arbitrary Unicode produced by the LLM.
BASE_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"


def _register_unicode_fonts() -> None:
    global BASE_FONT, BOLD_FONT
    try:
        import matplotlib as _mpl
        font_dir = os.path.join(_mpl.get_data_path(), "fonts", "ttf")
        regular = os.path.join(font_dir, "DejaVuSans.ttf")
        bold = os.path.join(font_dir, "DejaVuSans-Bold.ttf")
        if os.path.exists(regular):
            pdfmetrics.registerFont(TTFont("DejaVuSans", regular))
            BASE_FONT = "DejaVuSans"
        if os.path.exists(bold):
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold))
            BOLD_FONT = "DejaVuSans-Bold"
        if BASE_FONT == "DejaVuSans":
            from reportlab.pdfbase.pdfmetrics import registerFontFamily
            registerFontFamily(
                "DejaVuSans",
                normal="DejaVuSans",
                bold=BOLD_FONT,
                italic="DejaVuSans",
                boldItalic=BOLD_FONT,
            )
    except Exception:
        # Fall back to Helvetica silently; PDF will still render.
        pass


_register_unicode_fonts()

REPORTS_DIR = os.environ.get(
    "FABRICFINDER_REPORTS_DIR",
    os.path.join(os.path.dirname(__file__), "reports"),
)

STATUS_COLORS = {
    "HEALTHY":   colors.HexColor("#2E7D32"),
    "INTENSIVE": colors.HexColor("#1565C0"),
    "PIPELINE":  colors.HexColor("#EF6C00"),
    "DECLINE":   colors.HexColor("#C62828"),
}
STATUS_DEFS = {
    "HEALTHY":   "MAU growth AND NUA growth",
    "INTENSIVE": "MAU flat, NUA growth",
    "PIPELINE":  "MAU growth, NUA flat/decline",
    "DECLINE":   "MAU decline AND NUA decline",
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _slug(text: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_" else "" for c in text or "")
    return "-".join(keep.lower().split())[:60] or "tenant"


def _fmt_int(v) -> str:
    if v is None or v == "":
        return "n/a"
    try:
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


def _fmt_pct(v) -> str:
    if v is None or v == "":
        return "n/a"
    try:
        return f"{float(v):+.1f}%"
    except (TypeError, ValueError):
        return str(v)


def _coerce_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0


# --------------------------------------------------------------------------- #
# Chart renderers (return PNG path or None)
# --------------------------------------------------------------------------- #
def _chart_path(stem: str, suffix: str) -> str:
    tmp = tempfile.gettempdir()
    return os.path.join(
        tmp, f"ff-{stem}-{suffix}-{datetime.now():%H%M%S%f}.png"
    )


def _line_mau_nua(series, stem) -> Optional[str]:
    if not series:
        return None
    months = [str(p.get("month", "")) for p in series]
    mau = [_coerce_int(p.get("mau")) for p in series]
    nua = [_coerce_int(p.get("nua")) for p in series]
    fig, ax = plt.subplots(figsize=(8, 3.5))
    ax.plot(months, mau, marker="o", color="#1565C0", label="MAU")
    ax.plot(months, nua, marker="s", color="#EF6C00", label="NUA")
    ax.set_title("MAU and NUA — monthly trend")
    ax.set_ylabel("Users")
    ax.grid(axis="y", linestyle=":", alpha=0.5)
    ax.legend()
    step = max(1, len(months) // 12)
    for i, label in enumerate(ax.get_xticklabels()):
        label.set_visible(i % step == 0)
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    fig.tight_layout()
    path = _chart_path(stem, "trend")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _bar_content(items, stem) -> Optional[str]:
    if not items:
        return None
    items = items[:10]
    labels = [str(i.get("name", ""))[:32] for i in items]
    vals = [_coerce_int(i.get("sessions")) for i in items]
    fig, ax = plt.subplots(figsize=(8, 3.5))
    ax.barh(labels[::-1], vals[::-1], color="#4C78A8")
    ax.set_title("Top content by sessions")
    ax.set_xlabel("Sessions")
    fig.tight_layout()
    path = _chart_path(stem, "content")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


def _pie_devices(items, stem) -> Optional[str]:
    if not items:
        return None
    items = sorted(items, key=lambda x: _coerce_int(x.get("value")), reverse=True)[:8]
    labels = [str(i.get("device", "")) for i in items]
    vals = [_coerce_int(i.get("value")) for i in items]
    if sum(vals) == 0:
        return None
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.pie(vals, labels=labels, autopct="%1.1f%%", startangle=90)
    ax.axis("equal")
    ax.set_title("Device breakdown")
    fig.tight_layout()
    path = _chart_path(stem, "devices")
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- #
# PDF assembly
# --------------------------------------------------------------------------- #
def render_tenant_pdf(spec: dict, output_dir: Optional[str] = None) -> str:
    """Render a tenant PDF from ``spec`` and return the output path."""
    tenant_name = spec.get("tenant_name") or "Unknown Tenant"
    output_dir = output_dir or REPORTS_DIR
    os.makedirs(output_dir, exist_ok=True)
    stem = f"{datetime.now():%Y-%m-%d_%H%M%S}_tenant-{_slug(tenant_name)}"
    pdf_path = os.path.join(output_dir, stem + ".pdf")

    styles = getSampleStyleSheet()
    h1 = styles["Heading1"]
    h2 = styles["Heading2"]
    body = styles["BodyText"]
    # Apply the Unicode font to all default styles so any character the LLM
    # emits (Δ, en/em dashes, non-breaking hyphens, minus sign, emoji-ish
    # symbols, etc.) renders instead of showing as a missing-glyph box.
    for _style in (h1, h2, body):
        _style.fontName = BOLD_FONT if _style.fontName.endswith("-Bold") else BASE_FONT
    body.fontName = BASE_FONT
    small = ParagraphStyle(
        "small", parent=body, fontSize=8, textColor=colors.grey, leading=10,
        fontName=BASE_FONT,
    )

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title=f"Tenant report — {tenant_name}",
        author="FabricFinder",
    )
    story = []

    # --- Header ----------------------------------------------------------- #
    story.append(Paragraph(f"Tenant Report — {tenant_name}", h1))
    geo = spec.get("geography") or {}
    geo_line = " · ".join(
        v for v in [geo.get("country"), geo.get("region"), geo.get("city")] if v
    )
    sub = []
    if spec.get("tenant_id"):
        sub.append(f"<b>Tenant ID:</b> {spec['tenant_id']}")
    if geo_line:
        sub.append(f"<b>Geography:</b> {geo_line}")
    sub.append(
        f"<b>Generated:</b> {datetime.now():%Y-%m-%d %H:%M:%S} · "
        "Source: HelixMCEDU (Microsoft Fabric)"
    )
    story.append(Paragraph("<br/>".join(sub), body))
    story.append(Spacer(1, 0.18 * inch))

    # --- Status badge ----------------------------------------------------- #
    status = (spec.get("status") or "").upper()
    if status in STATUS_COLORS:
        badge = Table(
            [[Paragraph(
                f'<font color="white"><b>STATUS: {status}</b></font>'
                f'<br/><font color="white" size="9">{STATUS_DEFS[status]}</font>',
                body,
            )]],
            colWidths=[7.2 * inch],
        )
        badge.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), STATUS_COLORS[status]),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("TOPPADDING", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ]))
        story.append(badge)
        if spec.get("status_rationale"):
            story.append(Spacer(1, 0.08 * inch))
            story.append(Paragraph(
                f"<b>Rationale (facts only):</b> {spec['status_rationale']}",
                body,
            ))
        story.append(Spacer(1, 0.18 * inch))

    # --- Current month + YoY ---------------------------------------------- #
    cur = spec.get("current_month") or {}
    yoy = spec.get("yoy") or {}
    summary = [
        ["", "Current month", f"YoY ({yoy.get('prior_month', 'n/a')})", "Δ YoY"],
        [
            "MAU",
            _fmt_int(cur.get("mau")),
            _fmt_int(yoy.get("prior_mau")),
            _fmt_pct(yoy.get("mau_change_pct")),
        ],
        [
            "NUA",
            _fmt_int(cur.get("nua")),
            _fmt_int(yoy.get("prior_nua")),
            _fmt_pct(yoy.get("nua_change_pct")),
        ],
    ]
    story.append(Paragraph(
        f"Current month: <b>{cur.get('month', 'n/a')}</b>", h2
    ))
    t = Table(summary, colWidths=[1.1 * inch, 1.7 * inch, 2.2 * inch, 1.4 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ECEFF1")),
        ("FONTNAME", (0, 0), (-1, -1), BASE_FONT),
        ("FONTNAME", (0, 0), (-1, 0), BOLD_FONT),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.18 * inch))

    # --- Trailing-6-month YoY (smooths seasonality) ----------------------- #
    yoy6 = spec.get("yoy_6m") or {}
    if yoy6:
        metric = (yoy6.get("metric") or "sum").lower()
        metric_label = "Sum" if metric == "sum" else "Average"
        story.append(Paragraph(
            f"Trailing 6 months YoY ({metric_label}): "
            f"<b>{yoy6.get('current_period', 'n/a')}</b> "
            f"vs <b>{yoy6.get('prior_period', 'n/a')}</b>",
            h2,
        ))
        six_rows = [
            ["", f"Current 6mo ({metric_label.lower()})",
             f"Prior 6mo ({metric_label.lower()})", "Δ YoY"],
            [
                "MAU",
                _fmt_int(yoy6.get("current_mau")),
                _fmt_int(yoy6.get("prior_mau")),
                _fmt_pct(yoy6.get("mau_change_pct")),
            ],
            [
                "NUA",
                _fmt_int(yoy6.get("current_nua")),
                _fmt_int(yoy6.get("prior_nua")),
                _fmt_pct(yoy6.get("nua_change_pct")),
            ],
        ]
        t6 = Table(
            six_rows,
            colWidths=[1.1 * inch, 2.0 * inch, 1.9 * inch, 1.4 * inch],
        )
        t6.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ECEFF1")),
            ("FONTNAME", (0, 0), (-1, -1), BASE_FONT),
            ("FONTNAME", (0, 0), (-1, 0), BOLD_FONT),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(t6)
        story.append(Paragraph(
            "Trailing-period totals smooth out single-month seasonality and "
            "give a more reliable read on year-over-year direction.",
            small,
        ))
        story.append(Spacer(1, 0.18 * inch))

    # --- Long-view trend chart -------------------------------------------- #
    trend_path = _line_mau_nua(spec.get("monthly_series") or [], stem)
    if trend_path:
        story.append(Paragraph("Long-view trend", h2))
        story.append(Image(trend_path, width=7.2 * inch, height=3.0 * inch))
        story.append(Spacer(1, 0.12 * inch))

    # --- Monthly table (compact, last 24) --------------------------------- #
    series = spec.get("monthly_series") or []
    if series:
        last = series[-24:]
        rows = [["Month", "MAU", "NUA"]]
        for p in last:
            rows.append([
                str(p.get("month", "")),
                _fmt_int(p.get("mau")),
                _fmt_int(p.get("nua")),
            ])
        mt = Table(rows, colWidths=[1.5 * inch, 1.4 * inch, 1.4 * inch])
        mt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ECEFF1")),
            ("FONTNAME", (0, 0), (-1, -1), BASE_FONT),
            ("FONTNAME", (0, 0), (-1, 0), BOLD_FONT),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
            ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(Paragraph(
            f"Monthly detail (last {len(last)} months)", h2
        ))
        story.append(mt)
        story.append(Spacer(1, 0.18 * inch))

    story.append(PageBreak())

    # --- Content & devices ------------------------------------------------- #
    content_path = _bar_content(spec.get("top_content") or [], stem)
    if content_path:
        story.append(Paragraph("Content usage", h2))
        story.append(Image(content_path, width=7.2 * inch, height=3.0 * inch))
        story.append(Spacer(1, 0.12 * inch))

    device_path = _pie_devices(spec.get("device_breakdown") or [], stem)
    if device_path:
        story.append(Paragraph("Devices", h2))
        story.append(Image(device_path, width=5.0 * inch, height=3.3 * inch))
        story.append(Spacer(1, 0.12 * inch))

    # --- Account contact (from LXP) --------------------------------------- #
    contact = spec.get("account_contact") or {}
    _CONTACT_ROWS = [
        ("Account Manager (alias)", "am"),
        ("Account Executive",       "account_executive"),
        ("Vertical",                "vertical"),
        ("Field Area",              "field_area"),
        ("Field Region",            "field_region"),
        ("Sales Unit",              "sales_unit"),
        ("Dominant SKU",            "dominant_position"),
        ("Primary Upsell",          "primary_upsell"),
        ("Renewal Date",            "renewal_date"),
        ("Renewal/Midterm Flag",    "renewal_flag"),
        ("Earliest Anniversary",    "earliest_anniversary"),
        ("Advisory Board",          "advisory_board"),
        ("FY26 Engagement",         "fy26_engagement"),
        ("Training Partner",        "training_partner"),
        ("Technical Blocker",       "technical_blocker"),
    ]
    rows = [[label, str(contact[key])]
            for label, key in _CONTACT_ROWS
            if contact.get(key) not in (None, "")]
    if rows:
        story.append(Paragraph("Account contact (LXP)", h2))
        ct = Table([["Field", "Value"]] + rows,
                   colWidths=[2.2 * inch, 5.0 * inch])
        ct.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#ECEFF1")),
            ("FONTNAME", (0, 0), (-1, -1), BASE_FONT),
            ("FONTNAME", (0, 0), (-1, 0), BOLD_FONT),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
        ]))
        story.append(ct)
        story.append(Spacer(1, 0.14 * inch))

    # --- Highlights -------------------------------------------------------- #
    highlights = spec.get("highlights") or []
    if highlights:
        story.append(Paragraph("Key facts", h2))
        for h in highlights:
            story.append(Paragraph(f"• {h}", body))
        story.append(Spacer(1, 0.12 * inch))

    # --- Data sources ------------------------------------------------------ #
    sources = spec.get("data_sources") or []
    if sources:
        story.append(Paragraph("Data sources / queries", h2))
        for s in sources:
            story.append(Paragraph(s, small))

    story.append(Spacer(1, 0.2 * inch))
    story.append(Paragraph(
        "This report is fact-only: every figure originates from the HelixMCEDU "
        "Microsoft Fabric warehouse. It contains no projections or assumptions.",
        small,
    ))

    doc.build(story)

    # Best-effort cleanup of chart PNGs.
    for p in [trend_path, content_path, device_path]:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass

    return pdf_path
