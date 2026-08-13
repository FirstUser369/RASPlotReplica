"""Floodway Data Table (FDT) PDF renderer — RASPLOT v3 §8."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter as LETTER_SIZE, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer

from .rasplot_db import RasplotDatabase, FloodwayRow, WorkHeader


STANDARD_FOOTNOTES = [
    "1 Feet above mouth",
    "2 This stream is subject to storm wave effects",
    "3 Widths between structures",
    "4 Floodway is contained between levees",
]


def _round1(v: Optional[float]) -> str:
    if v is None:
        return ""
    if abs(v + 8888) < 0.01 or abs(v + 9999) < 0.01:
        return "*"
    return f"{v:.1f}"


def render_fdt_pdf(
    out_path: str | Path,
    db: RasplotDatabase,
    footnotes: list[str] | None = None,
    column_notes: dict[str, str] | None = None,
) -> str:
    """Render a FIS-style Floodway Data Table PDF."""
    out_path = Path(out_path)
    header = db.header
    footnotes = footnotes or []
    column_notes = column_notes or {}

    lettered_rs = {
        r.rs: r.label_letter for r in db.working
        if r.kind == "xs" and r.label_letter
    }
    rows_out: list[list[str]] = []
    if db.floodway:
        for fw in db.floodway:
            if not fw.label_letter and fw.rs not in lettered_rs:
                continue
            xs_letter = fw.label_letter or lettered_rs.get(fw.rs, "")
            incr = fw.elev_diff
            if incr is None and fw.elev_with_fw is not None and fw.regulatory is not None:
                incr = fw.elev_with_fw - fw.regulatory
            rows_out.append([
                xs_letter or "",
                _round1(fw.width),
                _round1(fw.area),
                _round1(fw.velocity),
                _round1(fw.regulatory),
                _round1(fw.elev_without_fw),
                _round1(fw.elev_with_fw),
                _round1(incr),
            ])
    else:
        primary = next((p.name for p in db.profiles if p.selected), None)
        if primary is None and db.profiles:
            primary = db.profiles[0].name
        for r in db.working:
            if r.kind != "xs" or not r.label_letter:
                continue
            reg = r.profiles.get(primary) if primary else None
            rows_out.append([
                r.label_letter, "", "", "",
                _round1(reg), _round1(reg), "", "",
            ])

    doc = SimpleDocTemplate(
        str(out_path), pagesize=landscape(LETTER_SIZE),
        leftMargin=0.5 * inch, rightMargin=0.5 * inch,
        topMargin=0.5 * inch, bottomMargin=0.5 * inch,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("T", parent=styles["Heading1"], fontSize=12, alignment=1, spaceAfter=4)
    sub_style = ParagraphStyle("S", parent=styles["Normal"], fontSize=9, alignment=1, spaceAfter=2)
    body = []
    body.append(Paragraph((header.table_name or "Table 24").upper(), sub_style))
    body.append(Paragraph((header.table_title or "FLOODWAY DATA").upper(), title_style))
    body.append(Paragraph(
        f"{(header.stream_name or '').upper()} — "
        f"{(header.community or '').upper()}, {(header.county or '').upper()}, "
        f"{(header.state or '').upper()}",
        sub_style,
    ))
    body.append(Paragraph(f"VERTICAL DATUM: {header.vertical_datum}", sub_style))
    body.append(Spacer(1, 0.15 * inch))

    col_headers = [
        ["CROSS SECTION", "FLOODWAY", "", "", "BASE FLOOD WATER SURFACE ELEVATION", "", "", ""],
        ["", "WIDTH\n(FEET)", "SECTION\nAREA\n(SQ. FEET)", "MEAN\nVELOCITY\n(FEET/SEC)",
         "REGULATORY\n(FEET)", "WITHOUT\nFLOODWAY\n(FEET)", "WITH\nFLOODWAY\n(FEET)", "INCREASE\n(FEET)"],
    ]
    # Annotate headers with notes
    def ann(label: str, key: str) -> str:
        n = column_notes.get(key)
        return f"{label}{n}" if n else label

    data = col_headers + (rows_out or [["—", "", "", "", "", "", "", ""]])
    tbl = Table(data, repeatRows=2, colWidths=[1.1*inch, 1.0*inch, 1.1*inch, 1.1*inch,
                                               1.3*inch, 1.3*inch, 1.2*inch, 1.0*inch])
    tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, 1), colors.Color(0.9, 0.9, 0.9)),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("SPAN", (1, 0), (3, 0)),
        ("SPAN", (4, 0), (7, 0)),
        ("SPAN", (0, 0), (0, 1)),
    ]))
    body.append(tbl)
    body.append(Spacer(1, 0.2 * inch))
    note_style = ParagraphStyle("N", parent=styles["Normal"], fontSize=8, leading=10)
    body.append(Paragraph("<b>NOTES:</b>", note_style))
    for fn in footnotes:
        body.append(Paragraph(fn, note_style))
    if not footnotes:
        body.append(Paragraph("1 See corresponding FIS Flood Profiles for cross-section locations.", note_style))

    doc.build(body)
    return str(out_path)
