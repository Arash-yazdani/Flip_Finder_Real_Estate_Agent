"""
Re-enrich a city's history file in-place.

Use when a saved query has lots of `enriched: False` records (Bright Data hung
on the original snapshot). This re-triggers Bright Data on just those listings'
URLs and rebuilds the FlipReport for each.

Usage:
    venv/bin/python -m dashboard.reenrich studio-city-ca
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from models.property import Property
from agents.flipper_evaluator import FlipperEvaluator
from scrapers.bright_data_enricher import BrightDataZillowEnricher
from dashboard.search_service import _flip_report_to_dict
from dashboard.storage import save_history, load_history


def _prop_from_history(r: dict) -> Property:
    p = Property(
        property_id=f"ZILLOW-{r['zpid']}",
        address=r["address"], city=r["city"], state=r["state"],
        price=r["purchase_price"], bedrooms=0, bathrooms=0.0,
        sqft=r["sqft"] or 0, year_built=r["year_built"] or 1990,
        property_type=r["home_type"] or "?",
        estimated_rent=0, hoa_fees=0, property_tax_annual=0, insurance_annual=1200,
    )
    p.link = r["link"]
    p.img_src = r.get("photo")
    return p


def reenrich(slug: str) -> None:
    payload = load_history(slug)
    if not payload:
        print(f"No history for slug={slug}", file=sys.stderr); sys.exit(1)
    results = payload["results"]
    needs = [r for r in results if not r.get("enriched")]
    if not needs:
        print(f"All {len(results)} listings already enriched."); return
    urls = [r["link"] for r in needs if r.get("link")]
    print(f"Re-enriching {len(urls)} unenriched listings for {payload['city']}…")

    enricher = BrightDataZillowEnricher()
    enriched_map = enricher.enrich(urls)
    print(f"Bright Data returned {len(enriched_map)} records")

    evaluator = FlipperEvaluator()
    updated = 0
    for i, r in enumerate(results):
        zpid = r["zpid"]
        enriched = enriched_map.get(zpid)
        if not enriched and r.get("enriched"):
            # already enriched previously — leave alone
            continue
        if not enriched:
            continue
        prop = _prop_from_history(r)
        a = evaluator.evaluate(prop, enriched=enriched)
        results[i] = _flip_report_to_dict(a, prop, enriched)
        updated += 1

    payload["enrichment_requested"] = (payload.get("enrichment_requested") or 0) + len(urls)
    payload["enrichment_returned"] = (payload.get("enrichment_returned") or 0) + len(enriched_map)
    payload["enrichment_error"] = None if updated else "still no records"
    payload["queried_at"] = datetime.now(timezone.utc).isoformat()
    # Re-sort by flip score (then rental score as tiebreaker)
    results.sort(key=lambda r: (r.get("flip_score") or 0, r.get("rental_score") or 0), reverse=True)
    payload["results"] = results

    if updated:
        save_history(payload["city"], payload)
        print(f"Saved with {updated} records updated, sorted by flip_score desc.")
    else:
        print("No records updated; not overwriting history.")


if __name__ == "__main__":
    slug = sys.argv[1] if len(sys.argv) > 1 else None
    if not slug:
        print("Usage: python -m dashboard.reenrich <city-slug>", file=sys.stderr); sys.exit(2)
    reenrich(slug)
