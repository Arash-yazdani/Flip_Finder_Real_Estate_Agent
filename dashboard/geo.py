"""
Geographic scope resolution for searches.

Turns a free-text location ("Sacramento County, CA", "Sacramento, CA") into the COMPLETE,
authoritative set of ZIPs to scan — from the bundled public crosswalk (data/geo/), not from whatever
ZIPs a single capped API scan happens to surface. This is what lets a county report cover every
neighborhood (incl. zero-inventory-in-top-800 areas like Arden-Arcade) instead of silently missing them.

Used by dashboard/search_service.py to drive the deep-scan fan-out.
"""
import gzip
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

_CROSSWALK_PATH = Path(__file__).resolve().parent.parent / "data" / "geo" / "zip_county_crosswalk.json.gz"
_CROSSWALK: Optional[dict] = None

# Full state name → USPS abbreviation (so "California" and "CA" both resolve).
_STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "puerto rico": "PR", "guam": "GU",
}


@dataclass
class Scope:
    kind: str                       # 'county' | 'city' | 'raw'
    label: str                      # human label, e.g. "Sacramento County, CA"
    state: str = ""                 # USPS abbr, e.g. "CA"
    zips: List[str] = field(default_factory=list)
    query: str = ""                 # the original search string (used as the seed/citywide query)


def load_crosswalk() -> dict:
    """Lazily load + cache the gzipped ZIP↔county crosswalk."""
    global _CROSSWALK
    if _CROSSWALK is None:
        try:
            with gzip.open(_CROSSWALK_PATH, "rb") as f:
                _CROSSWALK = json.loads(f.read().decode("utf-8"))
        except Exception:
            # Missing/corrupt crosswalk must never break search — fall back to raw scope.
            _CROSSWALK = {"counties": {}, "county_index": {}, "city_index": {}, "zip_meta": {}}
    return _CROSSWALK


def _norm_state(s: str) -> str:
    s = (s or "").strip().lower()
    if len(s) == 2:
        return s.upper()
    return _STATE_ABBR.get(s, "")


def resolve_scope(query: str) -> Scope:
    """Resolve a search string to a geographic scope with a complete ZIP list.

    'Sacramento County, CA' → county scope (every residential ZIP in the county).
    'Sacramento, CA'        → city scope (every ZIP whose primary place is that city).
    anything else           → raw scope (zips=[]), i.e. today's exact single-location behavior.
    """
    q = (query or "").strip()
    cw = load_crosswalk()

    # --- County: "<name> County, <ST>" ---
    m = re.search(r"\bcounty\b", q, re.IGNORECASE)
    if m:
        name = q[:m.start()].strip().rstrip(",").strip()
        rest = q[m.end():].strip().lstrip(",").strip()
        state = _norm_state(rest)
        if name and state:
            fips = cw.get("county_index", {}).get(f"{state.lower()}|{name.lower()}")
            if fips:
                zips = cw["counties"][fips]["zips"]
                return Scope(kind="county", label=f"{name.title()} County, {state}",
                             state=state, zips=list(zips), query=q)
        # Named a county we don't recognize → fall through to raw (don't guess).

    # --- City: "<city>, <ST>" ---
    if "," in q:
        city, _, st = q.rpartition(",")
        city = city.strip()
        state = _norm_state(st)
        if city and state:
            zips = cw.get("city_index", {}).get(f"{state.lower()}|{city.lower()}")
            if zips:
                return Scope(kind="city", label=f"{city.title()}, {state}",
                             state=state, zips=list(zips), query=q)

    return Scope(kind="raw", label=q, query=q)
