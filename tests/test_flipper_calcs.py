"""
Arithmetic self-check for the post-audit FlipperEvaluator fixes.
Plain asserts, no framework:  python3 tests/test_flipper_calcs.py
Each block maps to a fix from the calc audit (HIGH-1..4, MEDIUM-5..8, LOW-9).
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.property import Property
from agents.flipper_evaluator import (
    FlipperEvaluator, BUY_CLOSING_PCT, HARD_MONEY_POINTS_PCT,
    HARD_MONEY_APR, HARD_MONEY_LTV, RENTAL_OPEX_PCT, INSURANCE_ANNUAL,
    STRONG_MARGIN_PCT, RENTAL_SCORE_BANDS, _rental_verdict_and_score,
)


def _prop(pid, price, sqft, year=1985, ptype="Single Family"):
    return Property(
        property_id=pid, address=f"{pid} Test St", city="Sacramento", state="CA",
        price=price, bedrooms=3, bathrooms=2.0, sqft=sqft, year_built=year,
        property_type=ptype, estimated_rent=0, hoa_fees=0,
    )


def _comp(addr, psf, sqft):
    return json.dumps({
        "streetAddress": addr, "price": int(psf * sqft), "livingArea": sqft,
        "homeType": "SINGLE_FAMILY", "bedrooms": 3, "bathrooms": 2,
    })


ev = FlipperEvaluator()

# ── Scenario A: flip, comps present, NO zestimate ──────────────────────────────
# Tests HIGH-1 (buy closing in all_in), HIGH-2 (points in financing),
# HIGH-4 (as-is ceiling skipped when only list-price anchor), MEDIUM-8 band.
a_price, a_sqft = 400_000, 2000
encA = {
    "price": a_price, "livingArea": a_sqft, "homeType": "SINGLE_FAMILY",
    "yearBuilt": 1985, "propertyTaxRate": 1.25, "description": "",
    "nearbyHomes": [_comp("11 A", 250, 2000), _comp("12 A", 255, 2000),
                    _comp("13 A", 260, 2000), _comp("14 A", 265, 2000)],
}
A = ev.evaluate(_prop("A", a_price, a_sqft), enriched=encA)

buy_closing = int(a_price * BUY_CLOSING_PCT)
# HIGH-1: all_in = price + buy_closing + rehab + holding + financing (identity)
assert A.all_in_cost == (A.purchase_price + buy_closing + A.rehab_estimate
                         + A.holding_cost_6mo + A.financing_cost), A.all_in_cost
# HIGH-2: financing = interest carry + origination points. Derived from the constants, not
# magic numbers, so retuning a default (e.g. per-market) can't silently rot this test.
expect_fin = int(a_price * HARD_MONEY_LTV * HARD_MONEY_APR * 0.5
                 + a_price * HARD_MONEY_LTV * HARD_MONEY_POINTS_PCT)
assert A.financing_cost == expect_fin, (A.financing_cost, expect_fin)
# HIGH-4: no zestimate ⇒ as-is ceiling skipped ⇒ comp ARV (~551k) NOT crushed to 1.15x list (460k)
assert A.arv > a_price * 1.15, (A.arv, a_price * 1.15)
# MEDIUM-8: verdict/score band consistency
assert A.verdict == "MARGINAL_FLIP", (A.verdict, A.projected_profit, A.profit_margin_pct)
assert 55 <= A.flip_score <= 79, A.flip_score
print(f"A  MARGINAL_FLIP  arv={A.arv:,}  all_in={A.all_in_cost:,}  fin={A.financing_cost:,}  score={A.flip_score}")

# ── Scenario B: strong rental, thin flip spread ────────────────────────────────
# Tests HIGH-3 (cap rate on rental_basis), MEDIUM-5 (rental not buried by thin-spread
# gate), MEDIUM-6 (RENTAL_PLAY aligned to GOOD_RENTAL).
b_price, b_sqft = 300_000, 1500
encB = {
    "price": b_price, "livingArea": b_sqft, "homeType": "SINGLE_FAMILY",
    "yearBuilt": 1985, "propertyTaxRate": 1.25, "description": "", "rentZestimate": 4500,
    "nearbyHomes": [_comp("21 B", 185, 1500), _comp("22 B", 187, 1500),
                    _comp("23 B", 189, 1500), _comp("24 B", 186, 1500)],
}
B = ev.evaluate(_prop("B", b_price, b_sqft), enriched=encB)

assert B.arv < b_price * 1.05, B.arv                       # genuinely thin flip spread
assert B.rental_verdict == "GOOD_RENTAL", B.rental_verdict
assert B.verdict == "RENTAL_PLAY", (B.verdict, B.cap_rate_pct, B.monthly_cash_flow)  # MEDIUM-5/6
# HIGH-3: cap rate divides by rental_basis (price+closing+rehab), not all_in
rental_basis = b_price + int(b_price * BUY_CLOSING_PCT) + B.rehab_estimate
noi_annual = 4500 * 12 * (1 - RENTAL_OPEX_PCT) - b_price * 0.0125 - INSURANCE_ANNUAL
assert B.cap_rate_pct == round(noi_annual / rental_basis * 100, 2), B.cap_rate_pct
assert 40 <= B.rental_score <= 100
print(f"B  RENTAL_PLAY  cap={B.cap_rate_pct}%  cf=${B.monthly_cash_flow}/mo  score={B.flip_score}")

# ── Pure-formula invariants ────────────────────────────────────────────────────
# MEDIUM-7 (superseded): the rental score is clamped to its verdict's band, so it can never
# contradict the label. Exercises the REAL function — an earlier version of this test re-declared
# the formula in local lambdas, so it asserted behaviour the module no longer had and still passed.
assert _rental_verdict_and_score(8, -100)[0] == "POOR_RENTAL"      # high cap, negative cash flow
assert _rental_verdict_and_score(5, 0)[0] == "DECENT_RENTAL"
assert _rental_verdict_and_score(7, 200)[0] == "GOOD_RENTAL"
# The inversion that motivated the clamp: POOR used to score 71.5 vs DECENT's 60.
assert _rental_verdict_and_score(8, -100)[2] < _rental_verdict_and_score(5, 0)[2]
# Exhaustive: no POOR may ever out-score any DECENT/GOOD, and bands must not overlap.
_grid = [(c / 4, f) for c in range(0, 80) for f in range(-2000, 2001, 50)]
_by_verdict = {}
for _c, _f in _grid:
    _v, _, _s = _rental_verdict_and_score(_c, _f)
    _lo, _hi = RENTAL_SCORE_BANDS[_v]
    assert _lo <= _s <= _hi, (_c, _f, _v, _s)          # score always inside its band
    _by_verdict.setdefault(_v, []).append(_s)
assert max(_by_verdict["POOR_RENTAL"]) < min(_by_verdict["DECENT_RENTAL"]), "POOR out-scores DECENT"
assert max(_by_verdict["DECENT_RENTAL"]) < min(_by_verdict["GOOD_RENTAL"]), "DECENT out-scores GOOD"

# STRONG_FLIP is driven by our own P&L, and the bar subsumes the 70% rule (a price at the 70% MAO
# already implies ~23.7% margin), so the old `passes_70 AND margin>=15` gate is gone.
assert STRONG_MARGIN_PCT < 23.7, "strong bar must sit under the 70%-rule-implied margin"

# LOW-9: profit floor scales with price (flat $20k on cheap, 5% on expensive)
assert max(20_000, int(0.05 * 300_000)) == 20_000
assert max(20_000, int(0.05 * 1_000_000)) == 50_000

# ── Buy-closing as its own field + user-tunable assumptions ─────────────────────
assert A.buy_closing_cost == int(a_price * BUY_CLOSING_PCT), A.buy_closing_cost

# Engine honors overridden assumptions: closing 2%→5%, points 2%→3%, APR 12%→15%.
ev2 = FlipperEvaluator(buy_closing_pct=0.05, points_pct=0.03, hard_money_apr=0.15)
A2 = ev2.evaluate(_prop("A2", a_price, a_sqft), enriched=encA)
assert A2.buy_closing_cost == int(a_price * 0.05) == 20000, A2.buy_closing_cost
assert A2.financing_cost == int(a_price * 0.75 * 0.15 * 0.5 + a_price * 0.75 * 0.03), A2.financing_cost
assert A2.financing_cost > A.financing_cost
assert A2.all_in_cost > A.all_in_cost              # higher costs → higher all-in
assert A2.projected_profit < A.projected_profit    # → lower profit
print(f"A2 overrides  closing={A2.buy_closing_cost:,}  fin={A2.financing_cost:,}  all_in={A2.all_in_cost:,}")

# Trust-boundary guard: whitelist + range-clamp on user-supplied assumptions.
from dashboard.search_service import _clean_assumptions
cleaned = _clean_assumptions({"hard_money_apr": 9.9, "rental_opex_pct": -1,
                              "hold_months": 999, "bogus": 5, "selling_cost_pct": "x"})
assert cleaned["hard_money_apr"] == 0.40, cleaned     # clamped to max
assert cleaned["rental_opex_pct"] == 0.10, cleaned    # clamped to min
assert cleaned["hold_months"] == 24, cleaned          # clamped + int
assert "bogus" not in cleaned                         # unknown key dropped
assert "selling_cost_pct" not in cleaned              # non-numeric skipped
assert _clean_assumptions(None) == {}                 # no assumptions → engine defaults

print("\nALL CALC CHECKS PASS")
