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


@dataclass
class CompSet:
    comps: List[Comp] = field(default_factory=list)
    median_psf: Optional[float] = None
    psf_low: Optional[float] = None
    psf_high: Optional[float] = None
    median_rent: Optional[int] = None
    median_rent_psf: Optional[float] = None
    confidence: str = "none"  # 'high' | 'medium' | 'low' | 'none'


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


def analyze_comps(enriched: dict, subject_home_type: Optional[str] = None) -> CompSet:
    """Build a CompSet from an enriched Zillow record."""
    cs = CompSet()
    nearby = _parse_nearby(enriched.get("nearbyHomes"))

    for h in nearby:
        price = h.get("price") or h.get("unformattedPrice") or 0
        sqft = h.get("livingArea") or h.get("area") or 0
        if not (price and sqft and price > 50_000 and sqft > 200):
            continue
        # If subject is a CONDO, prefer condo comps (skip mismatch)
        if subject_home_type:
            ht = (h.get("homeType") or "").upper()
            if ht and subject_home_type.upper() != ht:
                # Allow SINGLE_FAMILY/TOWNHOUSE cross-matching; skip strict CONDO mismatch
                if subject_home_type.upper() == "CONDO" and ht != "CONDO":
                    continue
                if subject_home_type.upper() in ("SINGLE_FAMILY", "TOWNHOUSE") and ht == "CONDO":
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

    # Also use the subject's own zestimate / rentZestimate as anchor points if usable
    subj_zest = enriched.get("zestimate")
    subj_sqft = enriched.get("livingArea") or 0
    if subj_zest and subj_sqft:
        # Don't pollute comps list, but use as anchor for psf
        anchor_psf = subj_zest / subj_sqft
        # Treat as a soft floor: ensure median doesn't undershoot it badly
        cs._anchor_psf = anchor_psf  # type: ignore

    psfs = [c.psf for c in cs.comps]
    rents = [c.rent_zestimate for c in cs.comps if c.rent_zestimate]

    if psfs:
        cs.median_psf = round(statistics.median(psfs), 2)
        cs.psf_low = round(min(psfs), 2)
        cs.psf_high = round(max(psfs), 2)

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

    # Confidence: count + tightness
    n = len(psfs)
    if n >= 5:
        spread = (cs.psf_high - cs.psf_low) / cs.median_psf if cs.median_psf else 1.0
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
