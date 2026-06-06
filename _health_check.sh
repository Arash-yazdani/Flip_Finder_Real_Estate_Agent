#!/bin/bash
# Quick health check on both APIs (RapidAPI Zillow + Bright Data)
set -e
cd "$(dirname "$0")"
set -a
source .env
set +a

venv/bin/python <<'PY' 2>&1
import json, os, sys, time
from pathlib import Path

import requests

# --- RapidAPI Zillow (real-estate101) ---
RAPID_KEY = "69379c2654mshe28db71c0b234a7p148f59jsn6b8c7c501542"  # hardcoded in scraper
RAPID_URL = "https://real-estate101.p.rapidapi.com/api/search"
RAPID_HEADERS = {
    "X-RapidAPI-Key": RAPID_KEY,
    "X-RapidAPI-Host": "real-estate101.p.rapidapi.com",
}

print("=" * 70)
print("RAPIDAPI ZILLOW (real-estate101)")
print("=" * 70)
t0 = time.time()
try:
    r = requests.get(
        RAPID_URL,
        headers=RAPID_HEADERS,
        params={"location": "san-francisco-ca", "page": "1", "status": "forSale"},
        timeout=20,
    )
    dt = time.time() - t0
    print(f"  Status:        {r.status_code}")
    print(f"  Latency:       {dt:.2f}s")
    if r.status_code == 200:
        data = r.json()
        results = data.get("results", [])
        print(f"  Results:       {len(results)} listings")
        print(f"  Total count:   {data.get('totalCount')}")
        if results:
            sample_keys = set(results[0].keys())
            print(f"  Fields/listing: {len(sample_keys)}")
        # Show rate-limit headers if present
        rl_headers = {k: v for k, v in r.headers.items()
                      if "rate" in k.lower() or "quota" in k.lower() or "limit" in k.lower()}
        if rl_headers:
            print(f"  Rate limits:")
            for k, v in rl_headers.items():
                print(f"    {k}: {v}")
        else:
            print(f"  Rate limits:   (no headers exposed)")
        print(f"  Verdict:       ✅ HEALTHY")
    else:
        print(f"  Body:          {r.text[:300]}")
        print(f"  Verdict:       ❌ UNHEALTHY")
except Exception as e:
    print(f"  Error:         {type(e).__name__}: {e}")
    print(f"  Verdict:       ❌ DOWN")

# --- Bright Data ---
print()
print("=" * 70)
print("BRIGHT DATA (Zillow detail dataset gd_lfqkr8wm13ixtbd8f5)")
print("=" * 70)
token = os.environ.get("BRIGHT_DATA_API_TOKEN")
if not token:
    print("  No BRIGHT_DATA_API_TOKEN — skipping")
else:
    BD_HEADERS = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    # Trigger a tiny single-URL job; we DON'T wait for completion — just check 200 + snapshot_id
    t0 = time.time()
    try:
        r = requests.post(
            "https://api.brightdata.com/datasets/v3/trigger",
            params={"dataset_id": "gd_lfqkr8wm13ixtbd8f5", "include_errors": "true"},
            headers=BD_HEADERS,
            json=[{"url": "https://www.zillow.com/homedetails/715-Drake-Ave-Sausalito-CA-94965/346899293_zpid/"}],
            timeout=20,
        )
        dt = time.time() - t0
        print(f"  Trigger status:  {r.status_code}")
        print(f"  Trigger latency: {dt:.2f}s")
        if r.status_code == 200:
            data = r.json()
            snapshot_id = data.get("snapshot_id") or data.get("id")
            print(f"  Snapshot ID:     {snapshot_id}")
            # Try one immediate poll just to verify the snapshot endpoint accepts the ID
            t0 = time.time()
            poll = requests.get(
                f"https://api.brightdata.com/datasets/v3/snapshot/{snapshot_id}",
                params={"format": "json"},
                headers=BD_HEADERS,
                timeout=10,
            )
            poll_dt = time.time() - t0
            print(f"  Snapshot poll:   {poll.status_code} ({poll_dt:.2f}s) — {'running' if poll.status_code == 202 else 'ready/other'}")
            print(f"  Verdict:         ✅ HEALTHY (trigger + poll both responded)")
        else:
            print(f"  Body:            {r.text[:300]}")
            print(f"  Verdict:         ❌ UNHEALTHY")
    except Exception as e:
        print(f"  Error:           {type(e).__name__}: {e}")
        print(f"  Verdict:         ❌ DOWN")

# --- Local pipeline health ---
print()
print("=" * 70)
print("LOCAL PIPELINE")
print("=" * 70)
cache_dir = Path("data/bright_data_cache")
cache_files = list(cache_dir.glob("*.json")) if cache_dir.exists() else []
print(f"  Cache entries:   {len(cache_files)}")
if cache_files:
    newest = max(cache_files, key=lambda p: p.stat().st_mtime)
    print(f"  Newest cache:    {newest.name} ({int(time.time() - newest.stat().st_mtime)}s ago)")

import subprocess
bot_ps = subprocess.run(["pgrep", "-f", "telegram_bot.py"], capture_output=True, text=True)
if bot_ps.stdout.strip():
    print(f"  Telegram bot:    ✅ running (PID {bot_ps.stdout.strip()})")
else:
    print(f"  Telegram bot:    ❌ not running")

log_path = Path("nohup_bot.log")
if log_path.exists():
    log_text = log_path.read_text()
    lines = log_text.splitlines()
    bright_lines = [l for l in lines if "Bright Data" in l]
    error_lines = [l for l in lines if "ERROR" in l]
    print(f"  Log lines:       {len(lines)}")
    print(f"  Bright Data events in log: {len(bright_lines)}")
    print(f"  ERROR lines:     {len(error_lines)}")
    if error_lines:
        print("  Most recent errors:")
        for l in error_lines[-3:]:
            print(f"    {l[:160]}")
PY
