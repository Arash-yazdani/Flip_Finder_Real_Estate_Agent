#!/bin/bash
# Source .env then run Python to enrich one Sausalito listing and dump every field.
set -e
cd "$(dirname "$0")"
set -a
source .env
set +a

venv/bin/python <<'PY' 2>&1
import json, sys
sys.path.insert(0, '.')
from scrapers.bright_data_enricher import BrightDataZillowEnricher

url = "https://www.zillow.com/homedetails/715-Drake-Ave-Sausalito-CA-94965/346899293_zpid/"
print(f"Enriching: {url}")
print("Note: Bright Data is async; this can take 30-120s...")

enricher = BrightDataZillowEnricher()
result = enricher.enrich([url], use_cache=False)

for zpid, rec in result.items():
    print(f"\n=== zpid={zpid} ===")
    print(f"Total fields: {len(rec)}\n")
    print("All field names (sorted):")
    for k in sorted(rec.keys()):
        v = rec[k]
        if isinstance(v, (dict, list)):
            preview = f"<{type(v).__name__}, len={len(v)}>"
        else:
            s = str(v)
            preview = (s[:100] + "...") if len(s) > 100 else s
        print(f"  {k:35s}: {preview}")

    print("\n--- Key flip-relevant fields ---")
    for k in ("zpid", "url", "streetAddress", "city", "state", "zipcode",
              "price", "homeType", "homeStatus", "yearBuilt", "livingArea",
              "lotSize", "bedrooms", "bathrooms",
              "zestimate", "rentZestimate", "taxAssessedValue",
              "propertyTaxRate", "monthlyHoaFee", "hoaFee",
              "daysOnZillow", "description", "priceHistory", "taxHistory"):
        if k in rec:
            v = rec[k]
            if isinstance(v, list):
                print(f"  {k}: list of {len(v)} items")
                if v:
                    print(f"    first item keys: {list(v[0].keys()) if isinstance(v[0], dict) else type(v[0]).__name__}")
            elif isinstance(v, str) and len(v) > 200:
                print(f"  {k}: {v[:200]}...")
            else:
                print(f"  {k}: {v}")
        else:
            print(f"  {k}: <MISSING>")
PY
