"""
CompAnalyzer — extracts comparable-sale data from a Bright Data enriched record.

Input: a Bright Data Zillow detail record (dict).
Output: a CompSet with median $/sqft, rent estimate, confidence rating.

Notes:
  - Bright Data returns `nearbyHomes` as a list of JSON STRINGS (one per home),
    not dicts. We parse them defensively.
  - Some nearby entries have price=0 (off-market with no sold price); we drop those.
  - We also fold in the subject's own zestimate and rentZestimate when present.
"""
import json
import math
import re
import statistics
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Comp:
    address: str
    price: int
    sqft: int
    psf: float
    beds: Optional[int] = None
    baths: Optional[float] = None
    rent_zestimate: Optional[int] = None
    adj_psf: Optional[float] = None  # size-adjusted $/sqft (to the subject's size)


@dataclass
class CompSet:
    comps: List[Comp] = field(default_factory=list)
    median_psf: Optional[float] = None       # size-adjusted median — drives ARV
    raw_median_psf: Optional[float] = None    # unadjusted median (telemetry)
    psf_low: Optional[float] = None           # raw comp range (for display)
    psf_high: Optional[float] = None
    median_rent: Optional[int] = None
    median_rent_psf: Optional[float] = None
    confidence: str = "none"  # 'high' | 'medium' | 'low' | 'none'
    subject_self_value: Optional[int] = None  # subject's own value if it appears in its
                                              # own nearbyHomes — used as an as-is anchor


# psf ∝ (subject_sqft / comp_sqft) ** (-SIZE_PSF_EXPONENT): larger homes carry a LOWER
# $/sqft, so a small comp's psf is scaled DOWN when applied to a bigger subject (and
# vice-versa). Exponent 0.30 ≈ a price-size elasticity of 0.70, a moderate market norm.
SIZE_PSF_EXPONENT = 0.30


def _norm_addr(s: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _size_adjust_psf(comp_psf: float, comp_sqft: int, subject_sqft: int) -> float:
    """Adjust a comp's $/sqft to the subject's size, bounded to ±20% so a single odd
    comp can't wildly swing ARV. Without subject_sqft, returns the raw psf unchanged."""
    if not (comp_sqft and subject_sqft) or comp_sqft <= 0 or subject_sqft <= 0:
        return comp_psf
    factor = (subject_sqft / comp_sqft) ** (-SIZE_PSF_EXPONENT)
    factor = max(0.80, min(1.20, factor))
    return comp_psf * factor


def _parse_nearby(entries) -> List[dict]:
    """Bright Data may return nearbyHomes as list of JSON strings OR list of dicts."""
    out = []
    for e in entries or []:
        if isinstance(e, str):
            try:
                out.append(json.loads(e))
            except Exception:
                continue
        elif isinstance(e, dict):
            out.append(e)
    return out


def analyze_comps(enriched: dict, subject_home_type: Optional[str] = None,
                  subject_sqft: Optional[int] = None,
                  subject_zpid: Optional[str] = None,
                  subject_address: Optional[str] = None) -> CompSet:
    """Build a CompSet from an enriched Zillow record.

    subject_sqft, when provided, size-adjusts each comp's $/sqft to the subject (so a
    2000-sqft home isn't valued at a 1200-sqft comp's inflated $/sqft). subject_zpid /
    subject_address exclude the subject from its own nearbyHomes (its value is captured
    as an as-is anchor instead)."""
    cs = CompSet()
    nearby = _parse_nearby(enriched.get("nearbyHomes"))

    # Property types that should never be used as comps for a residential flip
    # (they distort $/sqft badly — verified: MULTI_FAMILY listings were polluting
    # SINGLE_FAMILY subjects in real Bright Data records).
    NON_COMP_TYPES = {"MULTI_FAMILY", "LOT", "LAND", "MANUFACTURED", "MOBILE", "APARTMENT"}

    subj_zpid = str(subject_zpid or enriched.get("zpid") or "").strip()
    _enr_addr = enriched.get("streetAddress")
    if not _enr_addr and isinstance(enriched.get("address"), dict):
        _enr_addr = enriched["address"].get("streetAddress")
    subj_addr_norm = _norm_addr(subject_address or _enr_addr)

    for h in nearby:
        price = h.get("price") or h.get("unformattedPrice") or 0
        sqft = h.get("livingArea") or h.get("area") or 0
        if not (price and sqft and price > 50_000 and sqft > 200):
            continue
        # Exclude the subject from its own comp set; keep its value as an as-is anchor.
        h_zpid = str(h.get("zpid") or "").strip()
        h_addr = h.get("streetAddress") or (
            h["address"].get("streetAddress") if isinstance(h.get("address"), dict) else "")
        is_subject = ((subj_zpid and h_zpid and h_zpid == subj_zpid)
                      or (subj_addr_norm and _norm_addr(h_addr) == subj_addr_norm))
        if is_subject:
            if not cs.subject_self_value:
                cs.subject_self_value = int(price)
            continue
        ht = (h.get("homeType") or "").upper()
        subj = (subject_home_type or "").upper()
        # Drop fundamentally different property types outright.
        if ht in NON_COMP_TYPES and subj not in NON_COMP_TYPES:
            continue
        # CONDO comps only for CONDO subjects, and vice-versa.
        if subj == "CONDO" and ht and ht != "CONDO":
            continue
        if subj in ("SINGLE_FAMILY", "TOWNHOUSE") and ht == "CONDO":
            continue
        # Size mismatch: a comp far larger/smaller than the subject isn't comparable
        # (e.g. a 14,000-sqft estate as a comp for a 720-sqft home). Keep a 0.5x–2.0x band.
        if subject_sqft and not (0.5 <= sqft / subject_sqft <= 2.0):
            continue
        addr = h.get("streetAddress") or h.get("address", {}).get("streetAddress") if isinstance(h.get("address"), dict) else h.get("streetAddress", "?")
        cs.comps.append(Comp(
            address=addr or "?",
            price=int(price),
            sqft=int(sqft),
            psf=round(price / sqft, 2),
            beds=h.get("bedrooms") or h.get("beds"),
            baths=h.get("bathrooms") or h.get("baths"),
            rent_zestimate=h.get("rentZestimate"),
        ))

    # Trim $/sqft outliers vs the comp-set median — rejects polluted comps (a $66/sqft
    # parcel/teardown, or a $700/sqft anomaly) that make the range meaningless. Fires at
    # 3+ comps and keeps at least 2 clean ones (2 good comps beat 3 with a parcel).
    if len(cs.comps) >= 3:
        _med = statistics.median([c.psf for c in cs.comps])
        _kept = [c for c in cs.comps if _med * 0.5 <= c.psf <= _med * 2.0]
        if len(_kept) >= 2:
            cs.comps = _kept

    raw_psfs = [c.psf for c in cs.comps]
    rents = [c.rent_zestimate for c in cs.comps if c.rent_zestimate]

    # Size-adjust each comp's $/sqft to the subject; the ARV median uses adjusted values,
    # while the displayed range stays raw (actual sale data).
    for c in cs.comps:
        c.adj_psf = round(_size_adjust_psf(c.psf, c.sqft, subject_sqft), 2) if subject_sqft else c.psf
    adj_psfs = [c.adj_psf for c in cs.comps]

    if raw_psfs:
        # ARV basis = SIZE-WEIGHTED mean of the size-adjusted $/sqft: comps closer to the
        # subject's footprint carry more weight, so a 0.6x or 1.6x comp can't drag the
        # midpoint as hard as a same-size neighbor. Falls back to the plain median.
        if subject_sqft:
            _num = _den = 0.0
            for c in cs.comps:
                w = 1.0 / (1.0 + abs(math.log((c.sqft or subject_sqft) / subject_sqft))) if c.sqft else 0.5
                _num += c.adj_psf * w
                _den += w
            cs.median_psf = round(_num / _den, 2) if _den else round(statistics.median(adj_psfs), 2)
        else:
            cs.median_psf = round(statistics.median(adj_psfs), 2)
        cs.raw_median_psf = round(statistics.median(raw_psfs), 2)
        cs.psf_low = round(min(raw_psfs), 2)
        cs.psf_high = round(max(raw_psfs), 2)

    if rents:
        cs.median_rent = int(statistics.median(rents))
        # Per-sqft rent (avg) for cross-application
        rent_psfs = [c.rent_zestimate / c.sqft for c in cs.comps if c.rent_zestimate and c.sqft]
        if rent_psfs:
            cs.median_rent_psf = round(statistics.median(rent_psfs), 3)
    else:
        # Fall back to subject's own rentZestimate
        rz = enriched.get("rentZestimate")
        if rz:
            cs.median_rent = int(rz)

    # Confidence: count + tightness (spread measured on raw comps)
    n = len(raw_psfs)
    if n >= 5:
        spread = (cs.psf_high - cs.psf_low) / cs.raw_median_psf if cs.raw_median_psf else 1.0
        cs.confidence = "high" if spread < 0.4 else "medium"
    elif n >= 3:
        cs.confidence = "medium"
    elif n >= 1:
        cs.confidence = "low"
    else:
        cs.confidence = "none"

    return cs


def estimate_arv(cs: CompSet, subject_sqft: int, post_rehab_uplift: float = 1.05) -> Optional[int]:
    """ARV = median comp $/sqft × subject sqft × small uplift for post-rehab quality."""
    if not (cs.median_psf and subject_sqft):
        return None
    return int(cs.median_psf * subject_sqft * post_rehab_uplift)


def estimate_rent(cs: CompSet, subject_sqft: int) -> Optional[int]:
    """Monthly rent estimate: prefer median rent if comps had rent data; else median rent_psf × sqft."""
    if cs.median_rent_psf and subject_sqft:
        return int(cs.median_rent_psf * subject_sqft)
    if cs.median_rent:
        return cs.median_rent
    return None
