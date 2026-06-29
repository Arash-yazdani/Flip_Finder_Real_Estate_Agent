"""
Server-side PDF generation for the stakeholder flip/rental report.

Uses fpdf2 (pure Python, no system libraries or headless browser) so it deploys cleanly on
Render's free tier and — unlike the browser's window.print() — produces a real vector PDF with
NO browser chrome (no onrender.com URL / timestamp header-footer). Branded header, page numbers,
a standing disclaimer, and one card per property.

Entry point: build_pdf(payload: dict) -> bytes
  payload = {city, generated, summary, properties: [report_dict, ...]}
"""
from fpdf import FPDF
from fpdf.enums import XPos, YPos

ACCENT = (37, 99, 235)
INK = (26, 29, 34)
MUTED = (90, 99, 111)
LINE = (227, 230, 235)
SOFT = (247, 248, 250)
POS = (21, 128, 61)
NEG = (192, 57, 43)

VERDICT_COLOR = {
    "STRONG_FLIP": (21, 128, 61), "GOOD_RENTAL": (21, 128, 61),
    "MARGINAL_FLIP": (163, 94, 0), "DECENT_RENTAL": (163, 94, 0),
    "RENTAL_PLAY": (29, 78, 216),
    "NO_DEAL": (90, 99, 111), "POOR_RENTAL": (90, 99, 111), "NO_RENT_DATA": (90, 99, 111),
    "TEAR_DOWN": (192, 57, 43), "PENDING": (90, 99, 111),
}

_SUBST = {"—": "-", "–": "-", "·": "|", "•": "-", "✓": "Y",
          "✗": "N", "…": "...", "’": "'", "“": '"', "”": '"',
          "₂": "2", " ": " ", " ": " ", " ": " "}


def _san(s) -> str:
    """Map common unicode to ASCII and strip the rest — core PDF fonts are latin-1 only."""
    if s is None:
        return ""
    s = str(s)
    for a, b in _SUBST.items():
        s = s.replace(a, b)
    return s.encode("ascii", "ignore").decode("ascii")


def _money(n) -> str:
    try:
        return "$" + format(int(round(float(n))), ",")
    except (TypeError, ValueError):
        return "$0"


def _num(n) -> str:
    try:
        return format(int(n), ",")
    except (TypeError, ValueError):
        return str(n) if n not in (None, "") else "?"


class ReportPDF(FPDF):
    def __init__(self, city: str):
        super().__init__(orientation="P", unit="mm", format="Letter")
        self.city = city
        self.set_margins(14, 14, 14)
        self.set_auto_page_break(True, margin=16)

    def footer(self):
        self.set_y(-12)
        self.set_draw_color(*LINE)
        self.line(14, self.get_y() - 1, 201.9, self.get_y() - 1)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*MUTED)
        left = f"Real Estate Analyzer  -  {self.city}  -  estimates only, not financial advice; verify independently"
        self.cell(150, 5, _san(left), align="L")
        self.cell(0, 5, f"Page {self.page_no()}/{{nb}}", align="R")


def _section(pdf, title):
    pdf.ln(1.5)
    pdf.set_font("Helvetica", "B", 7.5)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 4, _san(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _row(pdf, label, value):
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*MUTED)
    pdf.cell(95, 4.6, _san(label))
    pdf.set_font("Courier", "", 8.5)
    pdf.set_text_color(*INK)
    pdf.cell(0, 4.6, _san(value), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _disclaimer(pdf):
    pdf.set_fill_color(*SOFT)
    pdf.set_draw_color(*LINE)
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(65, 73, 90)
    txt = (
        "Estimates, not appraisals. ARV, rehab, rent, cap rate, and projected profit are automated "
        "estimates derived from public listing and comparable-sale data - not appraisals, guarantees, or "
        "financial advice. Projected profit is a modeled figure that can swing materially with the true "
        "purchase price, rehab scope, and resale value; verify every number independently before making an "
        "offer. Comp confidence: high = 5+ tight comps, medium = 3+, low = under 3; an ARV pinned to a cap "
        "multiple of the list or as-is value is model-limited and warrants manual review."
    )
    pdf.multi_cell(0, 4, _san(txt), border=1, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _badges(pdf, p):
    v = p.get("verdict", "?")
    rv = p.get("rental_verdict", "NO_RENT_DATA")
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*VERDICT_COLOR.get(v, MUTED))
    pdf.cell(78, 5.2, _san(f"Flip: {str(v).replace('_', ' ')} ({p.get('flip_score', '?')})"))
    pdf.set_text_color(*VERDICT_COLOR.get(rv, MUTED))
    pdf.cell(0, 5.2, _san(f"Rent: {str(rv).replace('_', ' ')} ({p.get('rental_score', 0)})"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _property_card(pdf, p, rank):
    # Orphan guard: if the card header wouldn't fit, start a fresh page first.
    if pdf.get_y() > 232:
        pdf.add_page()

    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*INK)
    pdf.cell(0, 6, _san(f"#{rank}  {p.get('address', '?')}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    bd = p.get("bedrooms") or "?"
    ba = p.get("bathrooms") or "?"
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*MUTED)
    pdf.cell(0, 5, _san(f"{_money(p.get('purchase_price'))}  |  {bd}bd / {ba}ba  |  "
                        f"{_num(p.get('sqft'))} sqft  |  {p.get('home_type', '?')}"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    _badges(pdf, p)

    prof = p.get("projected_profit") or 0
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*(POS if prof > 0 else NEG))
    pdf.cell(0, 5.6, _san(f"{_money(prof)} profit  -  {p.get('profit_margin_pct', '?')}%  -  "
                          f"70% rule {'PASS' if p.get('passes_70_rule') else 'FAIL'}"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.set_font("Helvetica", "I", 8.5)
    pdf.set_text_color(*MUTED)
    if p.get("verdict_reason"):
        pdf.multi_cell(0, 4, _san("Flip: " + p["verdict_reason"]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if p.get("rental_verdict_reason"):
        pdf.multi_cell(0, 4, _san("Rent: " + p["rental_verdict_reason"]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    _section(pdf, "FLIP MATH")
    _row(pdf, "ARV", f"{_money(p.get('arv'))}  ({p.get('arv_source', '?')}, "
                     f"{p.get('arv_confidence', '?')}, {p.get('comp_count', 0)} comps)")
    _row(pdf, "Rehab", f"{_money(p.get('rehab_estimate'))}  (${p.get('rehab_psf', '?')}/sqft, "
                       f"{p.get('rehab_signal', '?')})")
    _row(pdf, "Buy-side closing", _money(p.get("buy_closing_cost")))
    _row(pdf, "Holding / Financing / Selling",
         f"{_money(p.get('holding_cost_6mo'))} / {_money(p.get('financing_cost'))} / "
         f"{_money(p.get('selling_cost'))}")
    _row(pdf, "All-in cost", _money(p.get("all_in_cost")))
    _row(pdf, "Net resale", _money(p.get("net_resale")))
    _row(pdf, "70% rule MAO", _money(p.get("mao_70_rule")))

    _section(pdf, "RENTAL MATH (BRRRR)")
    _row(pdf, "Rent estimate", f"{_money(p.get('monthly_rent_est'))}/mo")
    _row(pdf, "Cap rate", f"{p.get('cap_rate_pct', '?')}%")
    _row(pdf, "Cash flow", f"{_money(p.get('monthly_cash_flow'))}/mo")
    _row(pdf, "BRRRR refi proceeds", _money(p.get("brrrr_refi_proceeds")))

    risks = p.get("risk_flags") or []
    _section(pdf, f"RISKS ({len(risks)})")
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*INK)
    if risks:
        for r in risks:
            pdf.multi_cell(0, 4, _san("- " + str(r)), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        pdf.cell(0, 4, _san("- none flagged"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    comps = p.get("comps_summary") or []
    _section(pdf, f"COMPS USED ({len(comps)})")
    pdf.set_font("Courier", "", 8)
    pdf.set_text_color(*INK)
    for c in comps:
        pdf.multi_cell(0, 4, _san("- " + str(c)), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    pdf.ln(2)
    pdf.set_draw_color(*LINE)
    pdf.line(14, pdf.get_y(), 201.9, pdf.get_y())
    pdf.ln(3)


def build_pdf(payload: dict) -> bytes:
    city = (payload or {}).get("city") or "Flip & Rental Report"
    props = (payload or {}).get("properties") or []
    pdf = ReportPDF(city)
    pdf.alias_nb_pages()
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*ACCENT)
    pdf.cell(0, 5, _san("REAL ESTATE ANALYZER"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*INK)
    pdf.cell(0, 9, _san(city), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if payload.get("generated"):
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 5, _san(payload["generated"]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if payload.get("summary"):
        pdf.set_font("Helvetica", "", 9.5)
        pdf.set_text_color(*INK)
        pdf.multi_cell(0, 5, _san(payload["summary"]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*INK)
    pdf.cell(0, 6, _san(f"{len(props)} propert{'y' if len(props) == 1 else 'ies'} in this report"),
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Underwriting assumptions used by this run — makes a shared PDF self-documenting.
    assumptions = payload.get("assumptions") or []
    if assumptions:
        pdf.ln(1)
        pdf.set_font("Helvetica", "B", 7.5)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 4, _san("ASSUMPTIONS"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_font("Courier", "", 8)
        pdf.set_text_color(*INK)
        pdf.multi_cell(0, 4, _san("  |  ".join(str(a) for a in assumptions)),
                       new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)
    _disclaimer(pdf)
    pdf.ln(3)

    for i, p in enumerate(props, 1):
        _property_card(pdf, p, i)

    _hud_section(pdf, payload.get("hud") or [], payload.get("hud_state") or "")

    out = pdf.output()
    return bytes(out)


def _hud_section(pdf, hud, state):
    """Appends the HUD/FHA REO foreclosure leads (location-only — no price/analysis)."""
    if not hud:
        return
    if pdf.get_y() > 225:
        pdf.add_page()
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*INK)
    label = f"HUD / FHA Foreclosure Leads — {state} statewide ({len(hud)})" if state \
        else f"HUD / FHA Foreclosure Leads ({len(hud)})"
    pdf.cell(0, 7, _san(label), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(0, 4, _san("Bank-owned (REO) leads. HUD publishes no price/beds/comps, so these "
                              "are NOT analyzed — verify each at hudhomestore.gov."),
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)
    for h in hud:
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*INK)
        pdf.cell(150, 4.8, _san(h.get("address", "")))
        pdf.set_font("Courier", "", 8)
        pdf.set_text_color(*MUTED)
        pdf.cell(0, 4.8, _san("case " + (h.get("case_num") or "")),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
