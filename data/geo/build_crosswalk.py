#!/usr/bin/env python3
"""
Offline one-shot generator for data/geo/zip_county_crosswalk.json.

Source: GeoNames postal-code dump for the US (https://download.geonames.org/export/zip/US.zip),
licensed CC-BY 4.0 (attribution required — see data/geo/README.md). One TSV row per ZIP with:
  country, postal_code, place(city), state_name, state_abbr, county_name, county_fips3,
  admin3, admin3_code, lat, lng, accuracy

Output schema (denormalized for O(1) scope lookup at runtime):
  {
    "version": "...",
    "attribution": "...",
    "counties":     { "<5-digit FIPS>": {"name","state","zips":[...]} },
    "county_index": { "<state_lower>|<county_lower>": "<FIPS>" },
    "city_index":   { "<state_lower>|<city_lower>": [zips...] },
    "zip_meta":     { "<zip>": {"city","state","county_fips"} }
  }

Coverage-first: a ZIP that GeoNames lists under multiple counties appears in EVERY such county's
list (never dropped). Run:  python data/geo/build_crosswalk.py   (downloads US.zip automatically),
or pass a local US.txt:      python data/geo/build_crosswalk.py /path/to/US.txt
"""
import gzip
import io
import json
import sys
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Optional

GEONAMES_URL = "https://download.geonames.org/export/zip/US.zip"
OUT_PATH = Path(__file__).parent / "zip_county_crosswalk.json.gz"

# 2-digit Census state FIPS by USPS abbreviation (50 states + DC + territories).
STATE_FIPS = {
    "AL": "01", "AK": "02", "AZ": "04", "AR": "05", "CA": "06", "CO": "08", "CT": "09",
    "DE": "10", "DC": "11", "FL": "12", "GA": "13", "HI": "15", "ID": "16", "IL": "17",
    "IN": "18", "IA": "19", "KS": "20", "KY": "21", "LA": "22", "ME": "23", "MD": "24",
    "MA": "25", "MI": "26", "MN": "27", "MS": "28", "MO": "29", "MT": "30", "NE": "31",
    "NV": "32", "NH": "33", "NJ": "34", "NM": "35", "NY": "36", "NC": "37", "ND": "38",
    "OH": "39", "OK": "40", "OR": "41", "PA": "42", "RI": "44", "SC": "45", "SD": "46",
    "TN": "47", "TX": "48", "UT": "49", "VT": "50", "VA": "51", "WA": "53", "WV": "54",
    "WI": "55", "WY": "56", "AS": "60", "GU": "66", "MP": "69", "PR": "72", "VI": "78",
}


def _load_us_txt(arg: Optional[str]) -> str:
    if arg:
        return Path(arg).read_text(encoding="utf-8")
    print(f"Downloading {GEONAMES_URL} …", file=sys.stderr)
    with urllib.request.urlopen(GEONAMES_URL, timeout=120) as r:
        blob = r.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return z.read("US.txt").decode("utf-8")


# A ZIP whose GeoNames centroid is shared by this many+ other ZIPs in the same county is
# a PO-box / government / placeholder cluster (e.g. Sacramento's 942xx range all sit on one
# downtown point), not a residential market. Excluded so the fan-out doesn't burn API calls
# on ZIPs that never carry for-sale listings.
EXCLUDE_SHARED_CENTROID = 3


def build(us_txt: str) -> dict:
    counties: dict = {}                       # fips -> {name, state, zips:set, _cen:{zip:centroid}}
    county_index: dict = {}                   # "st|county" -> fips
    city_index: dict = defaultdict(set)       # "st|city" -> {zips}
    zip_meta: dict = {}                        # zip -> {city, state, county_fips}

    for line in us_txt.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        if len(cols) < 11:
            continue
        zipc = cols[1].strip()
        city = cols[2].strip()
        state = cols[4].strip().upper()
        county_name = cols[5].strip()
        county_fips3 = cols[6].strip()
        centroid = f"{cols[9].strip()},{cols[10].strip()}"
        if not (zipc and state and county_name and county_fips3):
            continue
        sfips = STATE_FIPS.get(state)
        if not sfips:
            continue
        fips = sfips + county_fips3.zfill(3)

        c = counties.setdefault(fips, {"name": county_name, "state": state, "zips": set(), "_cen": {}})
        c["zips"].add(zipc)
        c["_cen"][zipc] = centroid
        county_index[f"{state.lower()}|{county_name.lower()}"] = fips
        if city:
            city_index[f"{state.lower()}|{city.lower()}"].add(zipc)
        # First sighting of a ZIP wins as its primary city label.
        if zipc not in zip_meta:
            zip_meta[zipc] = {"city": city, "state": state, "county_fips": fips}

    # Drop PO-box / placeholder ZIPs (shared-centroid clusters) from every structure.
    excluded: set = set()
    for fips, c in counties.items():
        cen_count: dict = defaultdict(int)
        for cen in c["_cen"].values():
            cen_count[cen] += 1
        bad = {z for z, cen in c["_cen"].items() if cen_count[cen] >= EXCLUDE_SHARED_CENTROID}
        c["zips"] -= bad
        excluded |= bad
    for c in counties.values():
        c.pop("_cen", None)
    for key in list(city_index):
        city_index[key] -= excluded
        if not city_index[key]:
            del city_index[key]
    for z in excluded:
        zip_meta.pop(z, None)
    # Drop counties left with no residential ZIPs, and prune the name index to survivors.
    counties = {f: c for f, c in counties.items() if c["zips"]}
    county_index = {k: f for k, f in county_index.items() if f in counties}
    print(f"Excluded {len(excluded)} PO-box/placeholder ZIPs (shared centroid)", file=sys.stderr)

    return {
        "version": "GeoNames US postal dump (export/zip/US.zip)",
        "attribution": "Geographic data © GeoNames (geonames.org), licensed CC-BY 4.0.",
        "counties": {
            f: {"name": c["name"], "state": c["state"], "zips": sorted(c["zips"])}
            for f, c in sorted(counties.items())
        },
        "county_index": dict(sorted(county_index.items())),
        "city_index": {k: sorted(v) for k, v in sorted(city_index.items())},
        "zip_meta": dict(sorted(zip_meta.items())),
    }


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    data = build(_load_us_txt(arg))
    payload = json.dumps(data, separators=(",", ":")).encode("utf-8")
    with gzip.open(OUT_PATH, "wb", compresslevel=9) as f:
        f.write(payload)
    n_zips = len(data["zip_meta"])
    n_counties = len(data["counties"])
    size_mb = OUT_PATH.stat().st_size / 1e6
    print(f"Wrote {OUT_PATH}  ({n_counties} counties, {n_zips} zips, {size_mb:.2f} MB)", file=sys.stderr)


if __name__ == "__main__":
    main()
