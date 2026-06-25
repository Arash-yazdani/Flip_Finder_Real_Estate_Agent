"""
HUD / FHA bank-owned (REO) foreclosures via HUD's free public ArcGIS REST API (no key).

These are surfaced as LEADS, not run through the flip pipeline: HUD publishes only
address + location + case data (no price/beds/sqft/zpid), so there's nothing to value or
enrich. ~622 nationwide, so most searches return a handful or none.
# ponytail: location-only feed; if HUD ever exposes price, route these through the evaluator.
"""
import requests

_URL = ("https://services.arcgis.com/VTyQ9soqVukalItT/arcgis/rest/services/"
        "SF_REO/FeatureServer/0/query")


def fetch_hud_reo(state, zips=None, limit=200):
    """REO foreclosures for a state, optionally narrowed to a ZIP set (from resolve_scope).
    Returns [] on any error — a dead leads feed must never break a search."""
    if not state:
        return []
    where = f"STATE_CODE='{state.upper()}'"
    if zips:
        zlist = ",".join(z for z in (str(x).strip() for x in zips) if z.isdigit())
        if zlist:
            where += f" AND DISPLAY_ZIP_CODE IN ({zlist})"
    try:
        r = requests.get(_URL, timeout=20, params={
            "where": where,
            "outFields": "ADDRESS,CITY,STATE_CODE,DISPLAY_ZIP_CODE,MAP_LATITUDE,MAP_LONGITUDE,CASE_NUM,DATE_ACQUIRED",
            "f": "json", "resultRecordCount": limit,
        })
        feats = r.json().get("features", [])
    except Exception:
        return []

    out = []
    for f in feats:
        a = f.get("attributes", {})
        street = " ".join((a.get("ADDRESS") or "").split())
        if not street:
            continue
        city = (a.get("CITY") or "").title()
        zc = a.get("DISPLAY_ZIP_CODE") or ""
        out.append({
            "address": f"{street}, {city}, {a.get('STATE_CODE', '')} {zc}".strip(),
            "city": city,
            "state": a.get("STATE_CODE"),
            "zip": str(zc),
            "latitude": a.get("MAP_LATITUDE"),
            "longitude": a.get("MAP_LONGITUDE"),
            "case_num": (a.get("CASE_NUM") or "").strip(),
            "source": "HUD REO",
            "link": "https://www.google.com/search?q=" + requests.utils.quote(f"{street} {city} HUD home for sale"),
        })
    return out


if __name__ == "__main__":  # ponytail: live self-check against the free endpoint
    rows = fetch_hud_reo("CA")
    print(f"CA HUD REO: {len(rows)}")
    assert all(r["address"] and r["link"].startswith("http") for r in rows), "bad record"
    if rows:
        print("sample:", rows[0]["address"], "|", rows[0]["case_num"])
    assert fetch_hud_reo("") == [], "no-state must return []"
    print("ok")
