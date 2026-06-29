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
assert buy_closing == 8000, buy_closing
# HIGH-1: all_in = price + buy_closing + rehab + holding + financing (identity)
assert A.all_in_cost == (A.purchase_price + buy_closing + A.rehab_estimate
                         + A.holding_cost_6mo + A.financing_cost), A.all_in_cost
# HIGH-2: financing = interest carry + origination points
expect_fin = int(a_price * 0.75 * 0.12 * 0.5 + a_price * 0.75 * HARD_MONEY_POINTS_PCT)
assert A.financing_cost == expect_fin == 24000, (A.financing_cost, expect_fin)
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
noi_annual = 4500 * 12 * (1 - 0.45) - b_price * 0.0125 - 1200
assert B.cap_rate_pct == round(noi_annual / rental_basis * 100, 2), B.cap_rate_pct
assert 40 <= B.rental_score <= 100
print(f"B  RENTAL_PLAY  cap={B.cap_rate_pct}%  cf=${B.monthly_cash_flow}/mo  score={B.flip_score}")

# ── Pure-formula invariants ────────────────────────────────────────────────────
# MEDIUM-7: DECENT and POOR rental-score curves are CONTINUOUS at the cap=5, cf=0 boundary.
decent = lambda cap, cf: 40 + cap * 4 + max(0, cf / 100)
poor   = lambda cap, cf: max(0, 40 + cap * 4 + cf / 200)
assert decent(5, 0) == poor(5, 0) == 60, (decent(5, 0), poor(5, 0))
# Step across the old cliff is now ~0, not ~20
assert abs(decent(5.0, 0) - poor(4.99, 0)) < 0.5

# LOW-9: profit floor scales with price (flat $20k on cheap, 5% on expensive)
assert max(20_000, int(0.05 * 300_000)) == 20_000
assert max(20_000, int(0.05 * 1_000_000)) == 50_000

# ── Buy-closing as its own field + user-tunable assumptions ─────────────────────
assert A.buy_closing_cost == int(a_price * BUY_CLOSING_PCT) == 8000, A.buy_closing_cost

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
