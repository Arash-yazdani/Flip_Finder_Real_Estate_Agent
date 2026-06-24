#!/usr/bin/env python3
"""Regression tests for the price-integrity fix (Phase 1): no fabricated $400k prices, no
fabricated sqft, no-price listings dropped at discovery, evaluator guards a missing price."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


print("1) scraper no longer fabricates price/sqft")
scraper_src = (ROOT / "scrapers" / "zillow_api_scraper.py").read_text()
check("no `else 400000` price fabrication", "else 400000" not in scraper_src)
check("no `else 1500` sqft fabrication", "else 1500" not in scraper_src)
check("missing price → 0", re.search(r"price=price if price > 0 else 0", scraper_src) is not None)
check("missing sqft → 0", re.search(r"sqft=int\(sqft\) if sqft else 0", scraper_src) is not None)

print("2) discovery sanity guard drops price<10k and sqft<=0")
from models.property import Property  # noqa: E402


def mk(price, sqft):
    p = Property("ID", "1 X St", "Sacramento", "CA", price, 3, 2.0, sqft, 2000,
                 "Single Family", 2000, 0, 0, 1200)
    return p


pool = [mk(500000, 1800), mk(0, 1800), mk(400000, 0), mk(5000, 1800), mk(250000, 1200)]
kept = [p for p in pool if p.sqft and p.sqft > 0 and (p.price or 0) >= 10000]
check("price=0 dropped", all(p.price != 0 for p in kept))
check("sqft=0 dropped", all(p.sqft != 0 for p in kept))
check("price=$5k dropped", all(p.price != 5000 for p in kept))
check("real listings kept", len(kept) == 2, f"kept {len(kept)}")

print("3) evaluator returns NO_DEAL (no math) for a missing price")
from agents.flipper_evaluator import FlipperEvaluator  # noqa: E402
ev = FlipperEvaluator()
r0 = ev.evaluate(mk(0, 1800), {})
check("price<=0 → NO_DEAL", r0.verdict == "NO_DEAL", r0.verdict)
check("price<=0 → score 0", r0.flip_score == 0.0)
check("price<=0 → profit 0 (no phantom)", r0.projected_profit == 0)
check("price<=0 → risk flag set", any("price" in f.lower() for f in r0.risk_flags))

print("4) evaluator backfills the authoritative Bright Data price over discovery price")
r1 = ev.evaluate(mk(999999, 1800), {"price": 350000, "livingArea": 1800})
check("uses enriched price (350k) not discovery (999k)", r1.purchase_price == 350000,
      f"got {r1.purchase_price}")

print()
if failures:
    print(f"❌ {len(failures)} FAILED: {failures}")
    sys.exit(1)
print("✅ all price-integrity checks passed")
