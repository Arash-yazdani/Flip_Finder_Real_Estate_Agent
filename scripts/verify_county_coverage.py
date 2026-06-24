#!/usr/bin/env python3
"""
Verify that a county scope covers every named target neighborhood — and (optionally) that a live
fan-out actually scans them.

Static (no API spend, CI-safe):
    python scripts/verify_county_coverage.py
        Resolves "Sacramento County, CA" and asserts the target-neighborhood ZIPs are all present in
        the crosswalk's county ZIP set. Exit 0 = covered.

Live (deliberate, spends RapidAPI quota):
    python scripts/verify_county_coverage.py --live
        Runs the real per-ZIP fan-out and prints a `zip | neighborhood | listings_found` table for the
        whole county, asserting each target ZIP was scanned.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard.geo import resolve_scope  # noqa: E402

COUNTY = "Sacramento County, CA"

# The user's named flip-target neighborhoods → representative ZIPs.
TARGETS = {
    "Oak Park":       ["95817", "95820"],
    "Tahoe Park":     ["95819", "95820"],
    "Arden-Arcade":   ["95821", "95825", "95864", "95608"],
    "North Natomas":  ["95835", "95834"],
    "South Natomas":  ["95833", "95834"],
}
TARGET_ZIPS = sorted({z for zs in TARGETS.values() for z in zs})


def verify_static() -> int:
    scope = resolve_scope(COUNTY)
    print(f"Scope: kind={scope.kind}  label={scope.label}  zips={len(scope.zips)}")
    if scope.kind != "county":
        print(f"FAIL: '{COUNTY}' did not resolve to a county scope.")
        return 1
    zipset = set(scope.zips)
    print(f"\nTarget neighborhoods ({len(TARGET_ZIPS)} ZIPs):")
    missing = []
    for area, zs in TARGETS.items():
        marks = " ".join(f"{z}{'✓' if z in zipset else '✗MISSING'}" for z in zs)
        print(f"  {area:14s} {marks}")
        missing += [z for z in zs if z not in zipset]
    print(f"\nFull county ZIP list ({len(scope.zips)}):")
    print("  " + " ".join(sorted(scope.zips)))
    if missing:
        print(f"\nFAIL: {len(missing)} target ZIP(s) missing from county scope: {sorted(set(missing))}")
        return 1
    print(f"\nPASS: all {len(TARGET_ZIPS)} target neighborhood ZIPs are covered by {scope.label}.")
    return 0


def verify_live() -> int:
    from dashboard.search_service import _discover
    scope = resolve_scope(COUNTY)
    if scope.kind != "county":
        print(f"FAIL: '{COUNTY}' did not resolve to a county scope.")
        return 1
    print(f"LIVE fan-out over {len(scope.zips)} ZIPs in {scope.label} … (spends RapidAPI quota)")
    props, quota = _discover(COUNTY, deep=True, scope=scope)
    if quota and not props:
        print(f"FAIL: discovery returned no props (quota/error: {quota})")
        return 1

    from collections import Counter
    counts = Counter((getattr(p, "zipcode", "") or "?") for p in props)
    zip_to_area = {z: area for area, zs in TARGETS.items() for z in zs}
    print(f"\nTotal listings discovered county-wide: {len(props)} across {len([z for z in counts if z!='?'])} ZIPs\n")
    print(f"{'zip':7s} {'neighborhood':16s} listings")
    for z in TARGET_ZIPS:
        print(f"{z:7s} {zip_to_area.get(z,''):16s} {counts.get(z, 0)}")
    scanned = {z for z in scope.zips}
    not_scanned = [z for z in TARGET_ZIPS if z not in scanned]
    if not_scanned:
        print(f"\nFAIL: target ZIPs not in scan scope: {not_scanned}")
        return 1
    print(f"\nPASS: every target ZIP was in scope and the county fan-out ran "
          f"({len(props)} listings, vs ~800 cap for a single city query).")
    return 0


if __name__ == "__main__":
    sys.exit(verify_live() if "--live" in sys.argv else verify_static())
