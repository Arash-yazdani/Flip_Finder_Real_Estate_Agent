"""
FlipperEvaluator v2 — investor-grade analysis for fix-and-flip + BRRRR rental plays.

Pipeline:
  1) Build comp set from Bright Data's nearbyHomes + subject zestimate
  2) Estimate ARV (zestimate > comps > fallback) with confidence
  3) Estimate rehab from year built + description signal
  4) Detect tear-down/development listings from description
  5) Run flip P&L (all-in cost, net resale, profit, 70% rule, MAO)
  6) Run rental/BRRRR P&L (rent, cap rate, monthly cash flow, refi cash-out)
  7) Issue a verdict and flag risks
  8) Score 0-100
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from agents.comp_analyzer import CompSet, analyze_comps, estimate_arv, estimate_rent


# --- Rehab base cost ($/sqft) ---
# This table is the HEAVY/FIXER (near-gut) renovation cost by age. The default case —
# a 'neutral' listing with no fixer/turnkey signal — is assumed to need only a
# cosmetic-to-moderate refresh, so it pays REHAB_NEUTRAL_MULT × this (most listed homes
# are livable, not gut jobs). Only a description that flags a fixer pays the full amount.
REHAB_PSF_BY_AGE = [
    (1950, 90),    # pre-1950 (heaviest)
    (1980, 60),
    (2000, 35),
    (9999, 20),
]
DEFAULT_REHAB_PSF = 50  # used when yearBuilt is None
REHAB_NEUTRAL_MULT = 0.5   # unknown-condition / no signal → cosmetic-moderate (~half of fixer)
REHAB_FIXER_MULT = 1.0     # description flags a fixer → full age-based (gut) cost
REHAB_TURNKEY_MULT = 0.25  # already updated → light touch-up only

# --- Description signal vocab ---
FIXER_KEYWORDS = (
    "fixer", "fix and flip", "fix-and-flip", "tlc", "needs work", "as-is", "as is",
    "investor special", "rehab", "potential", "handyman", "diamond in the rough",
    "good bones", "needs love", "bring your contractor", "cash only", "value-add",
)
TURNKEY_KEYWORDS = (
    "renovated", "remodeled", "updated", "turnkey", "turn-key",
    "move-in ready", "move in ready", "newly", "fully refreshed",
    "designer", "fully renovated", "brand new",
)
TEARDOWN_KEYWORDS = (
    "build your dream", "build your own", "design and build",
    "tear-down", "tear down", "teardown",
    "approved plans", "approved permits", "shovel ready", "shovel-ready",
    "build to suit", "vacant lot", "raw land",
    "blank canvas",
)

# --- Financial defaults ---
DEFAULT_HOLD_MONTHS = 6
HARD_MONEY_APR = 0.12
HARD_MONEY_LTV = 0.75
SELLING_COST_PCT = 0.08
INSURANCE_ANNUAL = 1200
UTILITIES_MONTHLY = 250
REFI_LTV = 0.75       # cash-out refi LTV at ARV
RENTAL_OPEX_PCT = 0.45  # vacancy + maintenance + mgmt + capex reserve


@dataclass
class FlipReport:
    property_id: str
    address: str

    # Verdict
    verdict: str
    verdict_reason: str
    flip_score: float

    # Subject
    purchase_price: int
    sqft: int
    year_built: Optional[int]
    home_type: str
    days_on_market: int
    list_psf: float

    # ARV & confidence
    arv: int
    arv_source: str         # 'zestimate' | 'comps' | 'fallback'
    arv_confidence: str     # 'high' | 'medium' | 'low' | 'none'
    comp_count: int
    comp_psf_range: Tuple[Optional[float], Optional[float]]

    # Rehab
    rehab_estimate: int
    rehab_psf: int
    rehab_signal: str

    # Flip P&L
    holding_cost_6mo: int
    financing_cost: int
    selling_cost: int
    all_in_cost: int
    net_resale: int
    projected_profit: int
    profit_margin_pct: float
    mao_70_rule: int
    passes_70_rule: bool

    # Rental P&L
    monthly_rent_est: Optional[int]
    monthly_noi: int
    cap_rate_pct: float
    monthly_cash_flow: int   # at all-in cost with hard-money refi to permanent
    brrrr_refi_proceeds: int  # ARV*0.75 - all_in_cost (negative = cash left in)

    # Rental verdict (independent of flip verdict)
    rental_verdict: str           # 'GOOD_RENTAL' | 'DECENT_RENTAL' | 'POOR_RENTAL' | 'NO_RENT_DATA'
    rental_verdict_reason: str
    rental_score: float           # 0-100 based on cap rate + cash flow

    # Risks
    risk_flags: List[str]

    # Comps (for display)
    comps_summary: List[str] = field(default_factory=list)


def _rehab_psf(year_built: Optional[int]) -> int:
    if not year_built:
        return DEFAULT_REHAB_PSF
    for cutoff, psf in REHAB_PSF_BY_AGE:
        if year_built < cutoff:
            return psf
    return REHAB_PSF_BY_AGE[-1][1]


# --- ARV post-rehab uplift over comp/as-is value, by renovation signal ---
# A fully renovated home commands a premium over the median (un-renovated) comp.
# Turnkey ≈ already at market (no flip upside); fixer ≈ meaningful transformation.
ARV_UPLIFT_BY_SIGNAL = {
    "fixer":    1.13,
    "neutral":  1.07,
    "turnkey":  1.02,
    "teardown": 1.05,  # teardown short-circuits to its own verdict; uplift unused
}


def _arv_uplift(rehab_signal: str) -> float:
    return ARV_UPLIFT_BY_SIGNAL.get(rehab_signal, 1.07)


# Sanity ceiling: ARV may not exceed the subject's own as-is value by more than this
# (a renovated home should land between its as-is value and the renovated-comp ceiling,
# not implausibly above it). Bounds the "$40k rehab adds $214k" failure mode.
AS_IS_MAX_PREMIUM = {
    "fixer":    1.25,   # heavier transformation justifies a bigger lift
    "neutral":  1.15,
    "turnkey":  1.06,
    "teardown": 1.10,   # teardown short-circuits its own verdict; ceiling rarely used
}


def _as_is_ceiling_premium(rehab_signal: str) -> float:
    return AS_IS_MAX_PREMIUM.get(rehab_signal, 1.12)


def _classify_description(desc: str) -> str:
    if not desc:
        return "neutral"
    lower = desc.lower()
    if any(k in lower for k in TEARDOWN_KEYWORDS):
        return "teardown"
    if any(k in lower for k in FIXER_KEYWORDS):
        return "fixer"
    if any(k in lower for k in TURNKEY_KEYWORDS):
        return "turnkey"
    return "neutral"


def _price_history_signals(enriched: dict) -> List[str]:
    """Detect price cuts, relistings, and volatility from priceHistory."""
    ph = enriched.get("priceHistory") or []
    flags: List[str] = []
    cuts = 0
    relistings = 0
    prices = []
    for e in ph[:8]:  # most recent 8 events
        ev = (e.get("event") or "").lower()
        pr = e.get("price")
        if pr:
            prices.append(pr)
        if "price change" in ev or "price reduced" in ev:
            cuts += 1
        if "listed for sale" in ev:
            relistings += 1
    if cuts >= 1:
        flags.append(f"{cuts} price cut(s) in recent history — motivated seller")
    if relistings >= 3:
        flags.append(f"{relistings} relistings — listing has failed before")
    if len(prices) >= 2 and max(prices) >= 2 * min(prices):
        flags.append(f"Pricing volatile: ${min(prices):,} – ${max(prices):,} range")
    return flags


def _no_price_report(base_prop, price: int, sqft: int) -> "FlipReport":
    """Minimal NO_DEAL report for a listing with no usable price or sqft — these can never be
    a real opportunity, so we never run the deal math on them (guards against a missing or
    fabricated price producing a phantom profit)."""
    reason = ("No list price available — cannot analyze" if price <= 0
              else "No square-footage data — cannot analyze")
    return FlipReport(
        property_id=getattr(base_prop, "property_id", ""),
        address=getattr(base_prop, "address", ""),
        verdict="NO_DEAL", verdict_reason=reason, flip_score=0.0,
        purchase_price=max(int(price), 0), sqft=max(int(sqft), 0),
        year_built=None, home_type=getattr(base_prop, "property_type", "?") or "?",
        days_on_market=0, list_psf=0.0,
        arv=0, arv_source="none", arv_confidence="none", comp_count=0, comp_psf_range=(None, None),
        rehab_estimate=0, rehab_psf=0, rehab_signal="neutral",
        holding_cost_6mo=0, financing_cost=0, selling_cost=0, all_in_cost=0,
        net_resale=0, projected_profit=0, profit_margin_pct=0.0, mao_70_rule=0, passes_70_rule=False,
        monthly_rent_est=None, monthly_noi=0, cap_rate_pct=0.0, monthly_cash_flow=0, brrrr_refi_proceeds=0,
        rental_verdict="NO_RENT_DATA", rental_verdict_reason=reason, rental_score=0.0,
        risk_flags=[reason], comps_summary=[],
    )


class FlipperEvaluator:
    def __init__(self,
                 hold_months: int = DEFAULT_HOLD_MONTHS,
                 hard_money_apr: float = HARD_MONEY_APR,
                 ltv: float = HARD_MONEY_LTV,
                 selling_cost_pct: float = SELLING_COST_PCT,
                 fallback_psf: float = 600.0):
        # fallback_psf: only used when both zestimate AND comps are absent
        self.hold_months = hold_months
        self.hard_money_apr = hard_money_apr
        self.ltv = ltv
        self.selling_cost_pct = selling_cost_pct
        self.fallback_psf = fallback_psf

    def evaluate(self, base_prop, enriched: Optional[dict] = None) -> FlipReport:
        enriched = enriched or {}
        risks: List[str] = []

        # Prefer the Bright Data list price (authoritative) over the discovery price; never
        # analyze a listing with no real price — return an explicit NO_DEAL so a missing/
        # fabricated price can never surface as an opportunity.
        price = int(enriched.get("price") or enriched.get("unformattedPrice") or base_prop.price or 0)
        sqft = int(enriched.get("livingArea") or base_prop.sqft or 0)
        if price <= 0 or sqft <= 0:
            return _no_price_report(base_prop, price, sqft)
        home_type = enriched.get("homeType") or base_prop.property_type or "?"
        year_built = enriched.get("yearBuilt")
        if not year_built and base_prop.year_built and base_prop.year_built != 1990:
            year_built = base_prop.year_built
        dom = int(enriched.get("daysOnZillow") or getattr(base_prop, "days_on_zillow", 0) or 0)
        list_psf = round(price / sqft, 2) if sqft else 0.0
        description = (enriched.get("description") or "").strip()

        if not year_built:
            risks.append("yearBuilt unknown — rehab estimate uncertain")
        if dom >= 180:
            risks.append(f"Stale listing ({dom} days on market)")
        if enriched.get("is_flipped_within_12_months"):
            risks.append("Flipped within last 12 months — likely overpriced")
        risks.extend(_price_history_signals(enriched))

        # --- Comp analysis ---
        # zpid + address let analyze_comps drop the subject from its own nearbyHomes;
        # sqft drives the size-adjustment of each comp's $/sqft to the subject.
        subj_zpid = enriched.get("zpid") or (
            base_prop.property_id.split("-")[-1] if getattr(base_prop, "property_id", "") else "")
        cs: CompSet = analyze_comps(
            enriched, subject_home_type=home_type, subject_sqft=sqft,
            subject_zpid=subj_zpid, subject_address=getattr(base_prop, "address", None),
        )

        # --- Renovation signal (classified before ARV: the ARV uplift depends on it) ---
        rehab_signal = _classify_description(description)
        arv_uplift = _arv_uplift(rehab_signal)

        # --- ARV (After-Repair Value) ---
        # IMPORTANT: Zillow's zestimate is the CURRENT, as-is market value — NOT the
        # post-renovation value. Using it directly as ARV (the old behavior) made
        # ARV ≈ list price → no spread → NO_DEAL for almost everything.
        # We now treat the zestimate as the *as-is* value (discount signal + rental),
        # and build ARV from renovated comps, or from the as-is value × a rehab uplift
        # when comps are unavailable.
        zest = enriched.get("zestimate") or getattr(base_prop, "zestimate", 0) or 0
        # As-is value anchor: the zestimate, else the subject's own value if it surfaced
        # in its own nearbyHomes, else the list price.
        as_is_value = int(zest) if zest else (cs.subject_self_value or price)

        arv: int = 0
        arv_source = "fallback"
        arv_confidence = "none"

        comp_arv = estimate_arv(cs, sqft, post_rehab_uplift=arv_uplift) if sqft else None
        if comp_arv and cs.comps:
            arv = comp_arv
            arv_source = "comps"
            arv_confidence = cs.confidence
        elif zest:
            # No usable comps — approximate ARV from the as-is zestimate + rehab uplift.
            arv = int(zest * arv_uplift)
            arv_source = "zestimate_uplift"
            arv_confidence = "low"  # 0 comps ⇒ never better than low confidence
            risks.append("ARV from Zestimate (no comparable sales) — unverified against comps")
        elif sqft:
            arv = int(self.fallback_psf * sqft * arv_uplift)
            arv_source = "fallback"
            arv_confidence = "low"
            risks.append(f"ARV used fallback ${int(self.fallback_psf)}/sqft — no zestimate, no comps")
        else:
            arv = int(price * 1.10)
            arv_confidence = "none"
            risks.append("No sqft data — ARV is a flat 10% bump (unreliable)")

        # Guard ranking trust: cap the ARV at a confidence-scaled multiple of list price so
        # NO source can manufacture a fake "STRONG_FLIP". Applies to EVERY arv_source — a
        # comp-less fallback ($600/sqft default) or a Zestimate-uplift must be capped too,
        # or a 4,500-sqft mislabeled-commercial listing fabricates a $1.7M "profit". The cap
        # adds a risk flag, denting the score so capped deals don't unfairly top the ranking.
        if price:
            cap_mult = {"high": 2.0, "medium": 1.6, "low": 1.4}.get(arv_confidence, 1.4)
            if arv > price * cap_mult:
                arv = int(price * cap_mult)
                risks.append(
                    f"ARV spread capped at {cap_mult}x list ({arv_confidence}-confidence ARV)"
                )

        # As-is sanity ceiling: a renovated home should sit between its as-is value and the
        # renovated ceiling — never implausibly above it. Applies to every source (for a
        # fallback/no-data ARV the anchor is the list price, so it can't run away from list).
        if as_is_value:
            as_is_ceiling = int(as_is_value * _as_is_ceiling_premium(rehab_signal))
            if arv > as_is_ceiling:
                arv = as_is_ceiling
                risks.append(
                    f"ARV capped at {_as_is_ceiling_premium(rehab_signal):.2f}x as-is value "
                    f"(${as_is_value:,}) — implied an unrealistic post-rehab jump"
                )

        # Motivated-seller / discount signal: list price below the as-is value.
        # This is an OPPORTUNITY (negotiation/equity upside), not a risk — it feeds a
        # small score bonus below rather than the risk penalty.
        listed_below_as_is = bool(zest and price <= as_is_value * 0.92)

        # --- Rehab cost (rehab_signal already classified above) ---
        # Base = age-based FIXER (gut) cost; scale DOWN for the common case where the
        # listing isn't flagged as a fixer (a livable home needs cosmetic-moderate work).
        psf = _rehab_psf(year_built)
        rehab = int((sqft or 1500) * psf)
        if rehab_signal == "teardown":
            rehab = int((sqft or 1500) * 250)  # tear-down + new build is ~$250+/sqft
            risks.append("Description suggests TEAR-DOWN / development — not a traditional flip")
        elif rehab_signal == "fixer":
            rehab = int(rehab * REHAB_FIXER_MULT)     # full gut cost
        elif rehab_signal == "turnkey":
            rehab = int(rehab * REHAB_TURNKEY_MULT)   # light touch-up
        else:
            rehab = int(rehab * REHAB_NEUTRAL_MULT)   # neutral → cosmetic-moderate (default)

        # --- Holding cost (6 months) ---
        tax_rate = float(enriched.get("propertyTaxRate") or 1.25) / 100.0
        annual_tax = price * tax_rate
        hoa_details = enriched.get("hoa_details") or {}
        monthly_hoa = 0
        if isinstance(hoa_details, dict):
            # Bright Data nests HOA — try common keys
            for k in ("hoaFee", "monthly_hoa_fee", "fee", "monthlyFee", "hoa"):
                v = hoa_details.get(k)
                if isinstance(v, (int, float)) and v > 0:
                    monthly_hoa = int(v); break
        if not monthly_hoa:
            monthly_hoa = int(enriched.get("monthlyHoaFee") or base_prop.hoa_fees or 0)

        loan_amt = price * self.ltv
        financing_6mo = int(loan_amt * self.hard_money_apr * (self.hold_months / 12))
        holding = (
            int(annual_tax * (self.hold_months / 12))
            + int(INSURANCE_ANNUAL * (self.hold_months / 12))
            + monthly_hoa * self.hold_months
            + UTILITIES_MONTHLY * self.hold_months
        )

        selling = int(arv * self.selling_cost_pct)
        all_in = price + rehab + holding + financing_6mo
        net_resale = arv - selling
        profit = net_resale - all_in
        profit_margin_pct = round((profit / all_in) * 100, 1) if all_in else 0.0

        # --- 70% rule ---
        mao = int(arv * 0.70 - rehab)
        passes_70 = price <= mao

        # --- Rental P&L (BRRRR) ---
        rent_est = estimate_rent(cs, sqft) if sqft else None
        if rent_est:
            gross_rent_annual = rent_est * 12
            noi_annual = gross_rent_annual * (1 - RENTAL_OPEX_PCT) - annual_tax - INSURANCE_ANNUAL - (monthly_hoa * 12)
            monthly_noi = int(noi_annual / 12)
            cap_rate = round((noi_annual / all_in) * 100, 2) if all_in else 0.0
            # Permanent financing assumption: 30yr at 7% on 75% of all-in (post-rehab refi)
            refi_amount = arv * REFI_LTV
            r_monthly = 0.07 / 12
            n = 360
            mortgage_payment = refi_amount * (r_monthly * (1 + r_monthly) ** n) / ((1 + r_monthly) ** n - 1) if r_monthly else 0
            monthly_cash_flow = int(monthly_noi - mortgage_payment)
            brrrr_refi_proceeds = int(refi_amount - all_in)  # negative = cash left in deal
        else:
            monthly_noi = 0
            cap_rate = 0.0
            monthly_cash_flow = 0
            brrrr_refi_proceeds = 0
            risks.append("No rent comps — rental analysis skipped")

        # --- Rental verdict (independent of flip verdict) ---
        rental_verdict = "NO_RENT_DATA"
        rental_verdict_reason = "No rent comps available"
        rental_score = 0.0
        if rent_est:
            if cap_rate >= 7 and monthly_cash_flow >= 200:
                rental_verdict = "GOOD_RENTAL"
                rental_verdict_reason = f"Strong rental: {cap_rate}% cap, ${monthly_cash_flow}/mo cash flow"
                rental_score = min(100, 60 + cap_rate * 3 + max(0, monthly_cash_flow / 50))
            elif cap_rate >= 5 and monthly_cash_flow >= 0:
                rental_verdict = "DECENT_RENTAL"
                rental_verdict_reason = f"Marginal rental: {cap_rate}% cap, ${monthly_cash_flow}/mo cash flow"
                rental_score = 40 + cap_rate * 4 + max(0, monthly_cash_flow / 100)
            else:
                rental_verdict = "POOR_RENTAL"
                rental_verdict_reason = (
                    f"Cap rate {cap_rate}% / cash flow ${monthly_cash_flow}/mo — "
                    f"won't cover financing without significant down payment"
                )
                rental_score = max(0, 25 + cap_rate * 3 + (monthly_cash_flow / 200))
            rental_score = round(min(100, rental_score), 1)

        # --- Verdict ---
        verdict = "NO_DEAL"
        verdict_reason = ""
        if rehab_signal == "teardown":
            verdict = "TEAR_DOWN"
            verdict_reason = "Listing framed as land/development — not a value-add rehab"
        elif arv < price * 1.05:
            verdict = "NO_DEAL"
            verdict_reason = f"ARV (${arv:,}) is at or below list (${price:,}); no spread for profit"
        elif passes_70 and profit_margin_pct >= 15:
            verdict = "STRONG_FLIP"
            verdict_reason = f"Passes 70% rule with {profit_margin_pct}% margin (${profit:,})"
        elif profit > 20_000 and profit_margin_pct >= 7:
            # Balanced calibration: a real (if slim) spread still surfaces as a flip.
            verdict = "MARGINAL_FLIP"
            verdict_reason = f"Slim margin {profit_margin_pct}% — flip works but needs tight execution"
        elif monthly_cash_flow >= 250 and cap_rate >= 5:
            verdict = "RENTAL_PLAY"
            verdict_reason = f"Doesn't flip but cash-flows ${monthly_cash_flow}/mo at {cap_rate}% cap"
        else:
            verdict = "NO_DEAL"
            verdict_reason = f"No flip spread (${profit:,} profit) and weak rental (cash flow ${monthly_cash_flow}/mo)"

        # --- Score (0-100) — calibrated to verdict ---
        score = 0.0
        if verdict == "STRONG_FLIP":
            score = 80 + min(20, profit_margin_pct / 2)
        elif verdict == "MARGINAL_FLIP":
            score = 55 + min(15, profit_margin_pct)
        elif verdict == "RENTAL_PLAY":
            score = 45 + min(15, cap_rate * 2)
        elif verdict == "TEAR_DOWN":
            score = 15
        else:
            score = max(0, 30 + profit_margin_pct)  # negative margin pushes score down

        # Adjust for risk count
        score = max(0, score - 3 * len(risks))
        # Adjust for motivated-seller signals (DOM, price cuts, listed below as-is value)
        if dom >= 90 and verdict in ("MARGINAL_FLIP", "NO_DEAL"):
            score += 5  # negotiation upside
        if listed_below_as_is and verdict in ("STRONG_FLIP", "MARGINAL_FLIP", "NO_DEAL"):
            score += 5  # priced below as-is value = equity/negotiation upside
        score = round(min(100, score), 1)

        # --- Comp summary lines for display ---
        comp_lines: List[str] = []
        for c in cs.comps[:5]:
            comp_lines.append(f"{c.address}: ${c.price:,} / {c.sqft}sqft = ${int(c.psf)}/sqft")

        return FlipReport(
            property_id=base_prop.property_id,
            address=base_prop.address,
            verdict=verdict,
            verdict_reason=verdict_reason,
            flip_score=score,
            purchase_price=price,
            sqft=sqft,
            year_built=year_built if isinstance(year_built, int) else None,
            home_type=home_type,
            days_on_market=dom,
            list_psf=list_psf,
            arv=arv,
            arv_source=arv_source,
            arv_confidence=arv_confidence,
            comp_count=len(cs.comps),
            comp_psf_range=(cs.psf_low, cs.psf_high),
            rehab_estimate=rehab,
            rehab_psf=(round(rehab / sqft) if sqft else psf) if rehab_signal != "teardown" else 250,
            rehab_signal=rehab_signal,
            holding_cost_6mo=holding,
            financing_cost=financing_6mo,
            selling_cost=selling,
            all_in_cost=all_in,
            net_resale=net_resale,
            projected_profit=profit,
            profit_margin_pct=profit_margin_pct,
            mao_70_rule=mao,
            passes_70_rule=passes_70,
            monthly_rent_est=rent_est,
            monthly_noi=monthly_noi,
            cap_rate_pct=cap_rate,
            monthly_cash_flow=monthly_cash_flow,
            brrrr_refi_proceeds=brrrr_refi_proceeds,
            rental_verdict=rental_verdict,
            rental_verdict_reason=rental_verdict_reason,
            rental_score=rental_score,
            risk_flags=risks,
            comps_summary=comp_lines,
        )
