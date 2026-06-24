#!/usr/bin/env python3
"""Run the FULL county pipeline (discovery → enrich → evaluate → rank) and print the top-N
report as text, for reassessing report quality after a fix. Mirrors the PDF's fields.

Usage:  python scripts/gen_county_report.py "Sacramento County, CA" 20
Writes the text to /tmp/sac_report_new.txt and prints a quick integrity summary.
"""
import asyncio
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dashboard.search_service import stream_search  # noqa: E402


async def run(city: str, count: int):
    props = {}
    market = None
    async for ev in stream_search(city=city, count=count, intent="both", deep=True):
        e, d = ev["event"], ev["data"]
        if e == "property":
            props[d.get("zpid") or d.get("address")] = d
        elif e == "complete":
            market = d
        elif e in ("error", "quota"):
            print(f"[{e}] {d.get('message')}")
    return list(props.values()), market


def _price(p):
    # Enriched cards key the list price as `purchase_price`; base cards use `price`.
    return p.get("purchase_price") or p.get("price") or 0


def fmt(p, rank):
    price = _price(p)
    prof = p.get("projected_profit")
    arv = p.get("arv")
    return (
        f"#{rank} {p.get('address','?')}\n"
        f"   ${price:,} · {p.get('bedrooms','?')}bd/{p.get('bathrooms','?')}ba · {p.get('sqft','?')} sqft · {p.get('home_type','?')}\n"
        f"   Flip: {p.get('verdict','?')} ({p.get('flip_score','?')})  Rent: {p.get('rental_verdict','?')} ({p.get('rental_score','?')})\n"
        f"   ARV ${arv:,} ({p.get('arv_source','?')}, {p.get('arv_confidence','?')}, {p.get('comp_count','?')} comps) · "
        f"profit {('$'+format(prof,',')) if isinstance(prof,(int,float)) else '?'} ({p.get('profit_margin_pct','?')}%) · "
        f"70% {'PASS' if p.get('passes_70_rule') else 'fail'}"
    )


def main():
    city = sys.argv[1] if len(sys.argv) > 1 else "Sacramento County, CA"
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    enriched, market = asyncio.run(run(city, count))
    # Sort by flip score desc, then rental score desc (approximates the displayed ranking).
    enriched.sort(key=lambda p: (p.get("flip_score") or 0, p.get("rental_score") or 0), reverse=True)
    top = enriched[:count]

    lines = [f"{city} — top {len(top)} of {len(enriched)} enriched"]
    if market:
        lines.append(f"market: {market}")
    for i, p in enumerate(top, 1):
        lines.append(fmt(p, i))
    out = "\n".join(lines)
    Path("/tmp/sac_report_new.txt").write_text(out)
    import json as _json
    Path("/tmp/sac_report_new.json").write_text(_json.dumps(enriched))
    print(out)

    # Integrity summary (uses the correct purchase_price key)
    prices = [_price(p) for p in enriched]
    scores = [p.get("flip_score") or 0 for p in enriched]
    print("\n================ INTEGRITY SUMMARY ================")
    print(f"enriched total: {len(enriched)}")
    print(f"exactly $400,000 (old fallback): {sum(1 for x in prices if x == 400000)}")
    print(f"price <= 0 (no price leaked through): {sum(1 for x in prices if x <= 0)}")
    print(f"price < $80,000 (manufactured/outlier candidates): {sum(1 for x in prices if 0 < x < 80000)}")
    print(f"distinct prices (all enriched): {len(set(prices))}/{len(prices)}")
    print(f"verdict mix (ALL enriched): {dict(Counter(p.get('verdict') for p in enriched))}")
    print(f"max flip_score: {max(scores) if scores else 0} | strong(>=80): {sum(1 for s in scores if s>=80)} | marginal(55-79): {sum(1 for s in scores if 55<=s<80)}")
    print(f"verdict mix (top {len(top)}): {dict(Counter(p.get('verdict') for p in top))}")


if __name__ == "__main__":
    main()
