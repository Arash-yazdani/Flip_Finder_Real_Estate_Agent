"""
Search pipeline as an async generator yielding SSE events.

Events:
  - status:     {message: str}
  - quota:      {api: 'rapidapi'|'bright_data', message: str}
  - error:      {message: str}
  - discovery:  {city, count, properties: [base_card,...]}  (immediate)
  - enrich_tick:{elapsed: int, requested: int}
  - property:   {zpid, photo, report}  (one per scored property)
  - complete:   {city, slug, total, queried_at, summary: {...}}

Notes on quota: we let the existing scrapers/enricher do their thing; we wrap their
exceptions and surface 'quota'/'error' events. Quota is detected from RapidAPI
rate-limit headers (when discovery returns) and Bright Data 402/429 responses.
"""
import asyncio
import json
import logging
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Dict, Iterable, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from models.property import Property
from agents.flipper_evaluator import FlipperEvaluator
from scrapers.bright_data_enricher import BrightDataZillowEnricher

logger = logging.getLogger(__name__)

_EXECUTOR = ThreadPoolExecutor(max_workers=4)


def _zpid_of(prop) -> str:
    return prop.property_id.replace("ZILLOW-", "")


def _base_card(prop) -> dict:
    """Card data available immediately from RapidAPI discovery (no Bright Data yet)."""
    return {
        "zpid": _zpid_of(prop),
        "address": prop.address,
        "city": prop.city,
        "state": prop.state,
        "price": prop.price,
        "bedrooms": prop.bedrooms,
        "bathrooms": prop.bathrooms,
        "sqft": prop.sqft,
        "home_type": prop.property_type,
        "photo": getattr(prop, "img_src", None),  # may be None; frontend uses placeholder
        "link": getattr(prop, "link", ""),
        "enriched": False,
    }


def _flip_report_to_dict(a, prop, enriched) -> dict:
    """Serialize a FlipReport + cover photo into a UI-friendly dict."""
    photos_list = []
    if enriched:
        raw_photos = enriched.get("photos") or []
        for item in raw_photos:
            if isinstance(item, dict):
                jpegs = item.get("mixedSources", {}).get("jpeg") or []
                if jpegs:
                    # Prefer 960px width for a good balance of quality vs size
                    best = max(jpegs, key=lambda x: x.get("width", 0))
                    target = next((j for j in jpegs if j.get("width") == 960), best)
                    url = target.get("url")
                    if url:
                        photos_list.append(url)
                elif item.get("url"):
                    photos_list.append(item["url"])
            elif isinstance(item, str):
                photos_list.append(item)

    photo = photos_list[0] if photos_list else getattr(prop, "img_src", None)
    if photo and not photos_list:
        photos_list = [photo]

    return {
        "zpid": _zpid_of(prop),
        "address": prop.address,
        "city": prop.city,
        "state": prop.state,
        "link": getattr(prop, "link", ""),
        "photo": photo,
        "photos": photos_list,
        "enriched": bool(enriched),
        "verdict": a.verdict,
        "verdict_reason": a.verdict_reason,
        "flip_score": a.flip_score,
        "purchase_price": a.purchase_price,
        "sqft": a.sqft,
        "year_built": a.year_built,
        "home_type": a.home_type,
        "days_on_market": a.days_on_market,
        "list_psf": a.list_psf,
        "arv": a.arv,
        "arv_source": a.arv_source,
        "arv_confidence": a.arv_confidence,
        "comp_count": a.comp_count,
        "comp_psf_range": list(a.comp_psf_range) if a.comp_psf_range else [None, None],
        "rehab_estimate": a.rehab_estimate,
        "rehab_psf": a.rehab_psf,
        "rehab_signal": a.rehab_signal,
        "holding_cost_6mo": a.holding_cost_6mo,
        "financing_cost": a.financing_cost,
        "selling_cost": a.selling_cost,
        "all_in_cost": a.all_in_cost,
        "net_resale": a.net_resale,
        "projected_profit": a.projected_profit,
        "profit_margin_pct": a.profit_margin_pct,
        "mao_70_rule": a.mao_70_rule,
        "passes_70_rule": a.passes_70_rule,
        "monthly_rent_est": a.monthly_rent_est,
        "monthly_noi": a.monthly_noi,
        "cap_rate_pct": a.cap_rate_pct,
        "monthly_cash_flow": a.monthly_cash_flow,
        "brrrr_refi_proceeds": a.brrrr_refi_proceeds,
        "rental_verdict": a.rental_verdict,
        "rental_verdict_reason": a.rental_verdict_reason,
        "rental_score": a.rental_score,
        "risk_flags": a.risk_flags,
        "comps_summary": a.comps_summary,
    }


def _discover(location: str):
    """Run the RapidAPI scraper as a subprocess and return Property list.

    Returns (props, quota_signal). quota_signal is non-None if RapidAPI is at/near limit.
    """
    result = subprocess.run(
        [sys.executable, "scrapers/zillow_api_scraper.py", location],
        capture_output=True, text=True, timeout=60,
        cwd=str(PROJECT_ROOT),
    )
    out = result.stdout
    stderr = result.stderr
    quota_signal = None
    # Cheap quota detect from stderr (we don't have headers exposed to subprocess output)
    if "429" in stderr or "rate limit" in stderr.lower() or "quota" in stderr.lower():
        quota_signal = "RapidAPI rate limit hit"
    if "JSON_START:" not in out:
        return [], quota_signal or f"discovery failed: {stderr[-300:]}"
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
        prop.img_src = p.get("img_src") or p.get("photo")  # may be None from current scraper
        props.append(prop)
    return props, quota_signal


def _detect_bd_quota_error(exc: Exception) -> Optional[str]:
    msg = str(exc)
    if "402" in msg or "Payment Required" in msg or "quota" in msg.lower() or "credit" in msg.lower():
        return msg
    return None


async def stream_search(city: str, count: int = 10, intent: str = "flip",
                         enrich_limit: Optional[int] = None,
                         user_email: Optional[str] = None) -> AsyncIterator[dict]:
    """Async generator producing SSE-ready dicts.

    intent: 'flip' | 'rent' | 'both' — same analysis, different ranking.
    user_email: if set, runs are logged to the admin DB for cost telemetry + quota.
    """
    if intent not in ("flip", "rent", "both"):
        yield {"event": "error", "data": {"message": f"Unknown intent: {intent}"}}
        return

    # Cost knobs (overridable via .env)
    import os as _os
    bd_cost_per_record = float(_os.environ.get("BRIGHT_DATA_COST_PER_RECORD_USD", "0.0015"))
    rapidapi_cost_per_call = float(_os.environ.get("RAPIDAPI_COST_PER_CALL_USD", "0"))

    # Log run start if we have an authed user
    run_id: Optional[int] = None
    if user_email:
        try:
            from dashboard import db as _db
            run_id = _db.log_run_start(user_email, city, intent, count)
        except Exception as e:
            logger.warning("log_run_start failed: %s", e)

    yield {"event": "status", "data": {"message": f"Searching {city} ({intent})…"}}

    loop = asyncio.get_running_loop()
    props, quota = await loop.run_in_executor(_EXECUTOR, _discover, city)

    if quota and not props:
        yield {"event": "quota", "data": {
            "api": "rapidapi",
            "message": "API capacity reached — please contact your administrator to add credits."
        }}
        return

    if not props:
        yield {"event": "error", "data": {"message": "No properties returned for that location."}}
        return

    # Filter sqft=0
    props = [p for p in props if p.sqft and p.sqft > 0]
    props.sort(key=lambda p: p.price)

    # Widen candidate pool moderately for scoring quality, but with a lower
    # floor than before to avoid 20-record minimum on small "top 5" requests.
    if enrich_limit is not None:
        enrich_n = enrich_limit
    else:
        enrich_n = max(count, 10)
    enrich_n = min(enrich_n, 30, len(props))

    candidates = [p for p in props if getattr(p, "link", "")][:enrich_n]
    urls = [p.link for p in candidates]
    to_enrich = candidates  # alias for legacy name below

    # Discovery emits the candidate pool so the frontend can render skeletons,
    # but the final displayed set will be the top `count` by score (re-sorted client-side).
    yield {"event": "discovery", "data": {
        "city": city,
        "count": len(candidates),
        "display_count": count,
        "properties": [_base_card(p) for p in candidates],
    }}

    enriched_map: Dict[str, dict] = {}
    enrich_error: Optional[str] = None
    enrich_stats = {"attempted": 0, "from_cache": 0, "fresh": 0}

    if urls:
        yield {"event": "status", "data": {"message": f"Enriching {len(urls)} via Bright Data…"}}
        try:
            enricher = BrightDataZillowEnricher()
            fut = loop.run_in_executor(_EXECUTOR, enricher.enrich, urls)
            t0 = time.time()
            while not fut.done():
                await asyncio.sleep(5)
                elapsed = int(time.time() - t0)
                if elapsed and elapsed % 30 == 0:
                    yield {"event": "enrich_tick", "data": {"elapsed": elapsed, "requested": len(urls)}}
            enriched_map = await fut
            enrich_stats = getattr(enricher, "last_stats", enrich_stats) or enrich_stats
        except Exception as e:
            q = _detect_bd_quota_error(e)
            if q:
                yield {"event": "quota", "data": {
                    "api": "bright_data",
                    "message": "API capacity reached — please contact your administrator to add credits."
                }}
            else:
                enrich_error = str(e)
                yield {"event": "error", "data": {
                    "message": (
                        f"Enrichment failed: {e}. "
                        "Results below are unreliable fallbacks — not saving to history."
                    )
                }}

        # Hard fail if NOTHING came back
        if not enriched_map and not enrich_error:
            enrich_error = "Bright Data returned 0 records (snapshot timed out or empty)."
            yield {"event": "error", "data": {
                "message": (
                    "⚠️ Bright Data returned 0 enriched records. "
                    "All listings below use fallback math — ARV, year built, and rent estimates are unreliable. "
                    "Not saving these results to history. Try again in a few minutes."
                )
            }}

    # Score the ENTIRE enriched pool so we can pick the top `count` by score.
    evaluator = FlipperEvaluator()
    scored = []
    for prop in candidates:
        zpid = _zpid_of(prop)
        enriched = enriched_map.get(zpid)
        try:
            a = evaluator.evaluate(prop, enriched=enriched)
            scored.append({"prop": prop, "report": a, "enriched": enriched})
        except Exception as e:
            logger.error(f"score failed for {prop.address}: {e}")

    def _sort_key(item):
        r = item["report"]
        if intent == "rent":
            return r.rental_score
        if intent == "both":
            return max(r.flip_score, r.rental_score)
        return r.flip_score
    scored.sort(key=_sort_key, reverse=True)

    # Trim to top `count` after global ranking
    top_scored = scored[:count]

    serialized = []
    for s in top_scored:
        d = _flip_report_to_dict(s["report"], s["prop"], s["enriched"])
        serialized.append(d)
        yield {"event": "property", "data": d}

    # Tell the frontend which candidates didn't make the cut (so it can remove their skeletons)
    final_zpids = [s["report"].property_id.replace("ZILLOW-", "") for s in top_scored]
    yield {"event": "trim", "data": {"keep": final_zpids}}

    # Final summary
    payload = {
        "city": city,
        "intent": intent,
        "queried_at": datetime.now(timezone.utc).isoformat(),
        "results": serialized,
        "enrichment_requested": len(urls),
        "enrichment_returned": len(enriched_map),
        "enrichment_error": enrich_error,
    }

    # Persist only if the data is reliable. If enrichment completely failed we keep
    # the in-memory render but don't pollute the archive with unreliable cached data.
    reliable = bool(enriched_map) or len(urls) == 0
    if reliable:
        from dashboard.storage import save_history
        slug = save_history(city, payload)
    else:
        slug = None
        yield {"event": "status", "data": {"message": "Result not saved to archive (unreliable)."}}

    # Calculate cost (RapidAPI cost is per-discovery call; Bright Data is per fresh enrichment)
    cost_bd = enrich_stats["fresh"] * bd_cost_per_record
    cost_rapid = rapidapi_cost_per_call  # one discovery call per run
    total_cost = cost_bd + cost_rapid

    # Finalize the run record
    if run_id is not None:
        try:
            from dashboard import db as _db
            _db.log_run_finish(
                run_id,
                enrich_attempted=enrich_stats["attempted"],
                enrich_from_cache=enrich_stats["from_cache"],
                enrich_fresh=enrich_stats["fresh"],
                cost_bright_data_usd=cost_bd,
                cost_rapidapi_usd=cost_rapid,
                status="error" if enrich_error else "ok",
                error=enrich_error,
            )
        except Exception as e:
            logger.warning("log_run_finish failed: %s", e)

    yield {"event": "complete", "data": {
        "city": city,
        "slug": slug,
        "total": len(serialized),
        "queried_at": payload["queried_at"],
        "summary": {
            "enriched": len(enriched_map),
            "requested": len(urls),
            "from_cache": enrich_stats["from_cache"],
            "fresh": enrich_stats["fresh"],
            "cost_usd": round(total_cost, 4),
        },
    }}
