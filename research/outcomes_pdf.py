"""PDF of the outcomes matrix (research/outcomes.py) plus today's Autopilot rules."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT, BOLD = "Helvetica", "Helvetica-Bold"
for f, b in (("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),):
    if os.path.exists(f) and os.path.exists(b):
        pdfmetrics.registerFont(TTFont("DV", f)); pdfmetrics.registerFont(TTFont("DVB", b))
        FONT, BOLD = "DV", "DVB"

VC = {"better": "#1a7f37", "more profit, deeper fall": "#9a6700", "less profit, shallower fall": "#0969da", "worse": "#cf222e",
      "about the same": "#57606a", "baseline": "#000000"}


def rs(x):
    x = float(x)
    a = abs(x)
    s = f"{a / 1e7:.2f} Cr" if a >= 1e7 else f"{a / 1e5:.2f} L" if a >= 1e5 else f"{a:,.0f}"
    return ("-" if x < 0 else "") + "Rs " + s


def pct(x):
    return f"{x:+.0f}%"


def build(out, path):
    ss = getSampleStyleSheet()
    H1 = ParagraphStyle("h1", parent=ss["Title"], fontName=BOLD, fontSize=18, spaceAfter=6)
    H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName=BOLD, fontSize=13, spaceBefore=8, spaceAfter=4)
    P = ParagraphStyle("p", parent=ss["BodyText"], fontName=FONT, fontSize=8.5, leading=11)
    SM = ParagraphStyle("sm", parent=P, fontSize=7, leading=8.5)
    SMB = ParagraphStyle("smb", parent=SM, fontName=BOLD)
    doc = SimpleDocTemplate(path, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm, topMargin=10 * mm, bottomMargin=10 * mm,
                            title="Autopilot outcomes - every change vs today's rules")
    st = [Paragraph("Autopilot outcomes: every change vs today's rules", H1),
          Paragraph(f"Generated {out['generated']}. Nifty + Bank Nifty + Sensex. Full history {out['periods']['full'][0]} to {out['periods']['full'][1]}; "
                    f"'2026' restarts the account on 1 Jan 2026. Starting capital Rs 2 lakh and Rs 20,000.", P), Spacer(1, 4)]
    for n in out["notes"]:
        st.append(Paragraph("• " + n, P))
    st.append(Paragraph("How to read the verdict", H2))
    st.append(Paragraph("Compared with the baseline row at the same capital and period. <b>better</b> = more profit and the worst fall no deeper "
                        "(within 2 points); <b>more profit, deeper fall</b> = a trade-off; <b>less profit, shallower fall</b> = safer but earns less; "
                        "<b>worse</b> = less profit and no safer; <b>about the same</b> = within 2% of the baseline's profit.", P))
    try:
        from fno import aprules
        rb = aprules.rulebook()
        st.append(Paragraph("Today's Autopilot rules (the baseline)", H2))
        rows = []
        for sec in rb["sections"]:
            rows.append([Paragraph(sec["title"], SMB), ""])
            rows += [[Paragraph(a, SM), Paragraph(b, SM)] for a, b in sec["rows"]]
        t = Table(rows, colWidths=[60 * mm, 215 * mm])
        t.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#d0d7de")),
                               ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]))
        st.append(t)
    except Exception as ex:
        st.append(Paragraph(f"(rules unavailable: {ex})", P))

    for cap in out["capitals"]:
        st.append(PageBreak())
        st.append(Paragraph(f"Starting capital {rs(cap)}", H2))
        bf, b6 = out["rows"][0]["runs"][f"{cap}_full"], out["rows"][0]["runs"][f"{cap}_y2026"]
        st.append(Paragraph(f"Baseline: full history ends at <b>{rs(bf['final'])}</b> (worst fall {bf['max_dd_pct']}%, {bf['trades']} trades); "
                            f"2026 alone ends at <b>{rs(b6['final'])}</b> (worst fall {b6['max_dd_pct']}%).", P))
        hdr = ["Group", "Scenario", "Full: ends at", "vs current", "Worst fall", "Win %", "2026: ends at", "vs current", "Worst fall", "Verdict (full)", "Verdict (2026)"]
        data = [[Paragraph(h, SMB) for h in hdr]]
        styles = []
        last_g = None
        for i, r in enumerate(out["rows"], start=1):
            f, y = r["runs"][f"{cap}_full"], r["runs"][f"{cap}_y2026"]
            vf, vy = r["verdict"][f"{cap}_full"], r["verdict"][f"{cap}_y2026"]
            df = (f["final"] - bf["final"]) / max(bf["net"], 1) * 100 if bf["net"] else 0
            dy = (y["final"] - b6["final"]) / max(abs(b6["net"]), 1) * 100 if b6["net"] else 0
            data.append([Paragraph(r["group"] if r["group"] != last_g else "", SM), Paragraph(r["name"], SM),
                         rs(f["final"]), "-" if i == 1 else pct(df), f"{f['max_dd_pct']}%", f"{f['win_rate']}",
                         rs(y["final"]), "-" if i == 1 else pct(dy), f"{y['max_dd_pct']}%",
                         Paragraph(f'<font color="{VC.get(vf, "#000")}">{vf}</font>', SM), Paragraph(f'<font color="{VC.get(vy, "#000")}">{vy}</font>', SM)])
            last_g = r["group"]
            if i == 1:
                styles.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#fff8c5")))
        t = Table(data, colWidths=[24 * mm, 70 * mm, 22 * mm, 16 * mm, 16 * mm, 12 * mm, 22 * mm, 16 * mm, 16 * mm, 30 * mm, 30 * mm], repeatRows=1)
        t.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), FONT), ("FONTSIZE", (0, 0), (-1, -1), 7), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("ALIGN", (2, 1), (8, -1), "RIGHT"), ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.black),
                               ("LINEBELOW", (0, 1), (-1, -1), 0.25, colors.HexColor("#d0d7de")),
                               ("TOPPADDING", (0, 0), (-1, -1), 1.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2)] + styles))
        st.append(t)
    doc.build(st)
    return path
