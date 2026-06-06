"""
Hybrid flipper finder:
  1) RapidAPI (real-estate101) discovers candidates by city (cheap)
  2) Bright Data enriches each with full Zillow detail (rich, slower)
  3) FlipperEvaluator scores each
  4) Print top-N ranked by flip score

Usage:
  venv/bin/python run_flippers.py "San Francisco, CA" --top 10 --enrich 20
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from models.property import Property
from agents.flipper_evaluator import FlipperEvaluator
from scrapers.bright_data_enricher import BrightDataZillowEnricher


def discover(location: str) -> list:
    """Run RapidAPI scraper subprocess, return Property objects with .link."""
    result = subprocess.run(
        [sys.executable, "scrapers/zillow_api_scraper.py", location],
        capture_output=True, text=True, timeout=60
    )
    out = result.stdout
    if "JSON_START:" not in out:
        print("Scraper failed:", result.stderr[-500:], file=sys.stderr)
        return []
    raw = json.loads(out.split("JSON_START:")[1].split(":JSON_END")[0])
    props = []
    for p in raw:
        prop = Property(
            property_id=p["property_id"], address=p["address"], city=p["city"],
            state=p["state"], price=p["price"], bedrooms=p["bedrooms"],
            bathrooms=p["bathrooms"], sqft=p["sqft"], year_built=p["year_built"],
            property_type=p["property_type"], estimated_rent=p["estimated_rent"],
            hoa_fees=p["hoa_fees"], property_tax_annual=p["property_tax_annual"],
            insurance_annual=p["insurance_annual"],
        )
        prop.link = p.get("link", "")
        props.append(prop)
    return props


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("location", help="City, ST (e.g. 'San Francisco, CA')")
    ap.add_argument("--top", type=int, default=10, help="How many flippers to print")
    ap.add_argument("--enrich", type=int, default=20,
                    help="How many candidates to enrich via Bright Data (cost knob)")
    ap.add_argument("--no-enrich", action="store_true",
                    help="Skip Bright Data; score from base API data only")
    args = ap.parse_args()

    print(f"[1/3] Discovering candidates in {args.location} via RapidAPI...")
    candidates = discover(args.location)
    print(f"      Got {len(candidates)} candidates")
    if not candidates:
        sys.exit(1)

    # Pre-filter: drop properties with sqft=0 (caused div-by-zero earlier)
    candidates = [c for c in candidates if c.sqft and c.sqft > 0]
    print(f"      {len(candidates)} after dropping sqft=0 listings")

    enriched_map = {}
    if not args.no_enrich:
        # Pre-filter by cheap heuristic: cheaper-than-median + has a real URL
        candidates.sort(key=lambda p: p.price)
        to_enrich = [c for c in candidates if c.link][: args.enrich]
        urls = [c.link for c in to_enrich]
        print(f"[2/3] Enriching top {len(urls)} candidates via Bright Data...")
        enricher = BrightDataZillowEnricher()
        enriched_map = enricher.enrich(urls)
        print(f"      Enriched {len(enriched_map)} records")
    else:
        print("[2/3] Skipping Bright Data enrichment")

    print(f"[3/3] Scoring flip potential...")
    evaluator = FlipperEvaluator()
    analyses = []
    for c in candidates:
        zpid = c.property_id.replace("ZILLOW-", "")
        enriched = enriched_map.get(zpid)
        a = evaluator.evaluate(c, enriched=enriched)
        analyses.append((c, a, enriched))

    analyses.sort(key=lambda x: x[1].flip_score, reverse=True)

    print("\n" + "=" * 100)
    print(f"TOP {args.top} FLIPPERS IN {args.location.upper()}")
    print("=" * 100)
    for i, (prop, a, enr) in enumerate(analyses[: args.top], 1):
        print(f"\n#{i}  {prop.address}, {prop.city}, {prop.state}")
        print(f"    List: ${a.purchase_price:,}  |  {prop.bedrooms}BD/{prop.bathrooms}BA/{prop.sqft}sqft  |  built {enr.get('yearBuilt') if enr else 'unknown'}")
        print(f"    Flip Score: {a.flip_score}/100  |  Signal: {a.rehab_signal}  |  DOM: {a.days_on_market}d")
        print(f"    ARV: ${a.arv:,} ({a.arv_source})  Rehab: ${a.rehab_estimate:,}  Hold(6mo): ${a.holding_cost_6mo:,}  Sell: ${a.selling_cost:,}")
        print(f"    Projected Profit: ${a.projected_profit:,}  Margin: {a.profit_margin_pct}%  70%-rule MAO: ${a.mao_70_rule:,}  Passes: {a.passes_70_rule}")
        if a.notes:
            print(f"    Notes: {'; '.join(a.notes)}")
        print(f"    Link: {prop.link}")


if __name__ == "__main__":
    main()
