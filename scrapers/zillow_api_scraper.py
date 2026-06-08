import requests
import json
import sys
import os
import re
from pathlib import Path

# Add project root to path so we can import models
sys.path.insert(0, str(Path(__file__).parent.parent))
from models.property import Property

# ---------------------------------------------------------------------------
# Host is injected via env var so it can be changed without a code push.
# Set ZILLOW_RAPIDAPI_HOST in .env / Render env vars to the host string shown
# in the Skolit "Zillow.com Live Data Scraper" API page code examples, e.g.:
#   ZILLOW_RAPIDAPI_HOST=zillow-com-live-data-scraper.p.rapidapi.com
# ---------------------------------------------------------------------------
_DEFAULT_HOST = "REPLACE_WITH_SKOLIT_HOST"   # ← paste host here or set env var
ZILLOW_RAPIDAPI_HOST = os.environ.get("ZILLOW_RAPIDAPI_HOST", _DEFAULT_HOST)


def _parse_price(item: dict) -> int:
    """Extract a clean integer price from whichever field the API returns it in."""
    # Try numeric fields first
    for key in ("unformattedPrice", "price", "listPrice", "soldPrice", "askingPrice"):
        val = item.get(key)
        if isinstance(val, (int, float)) and val > 0:
            return int(val)
    # Try string/formatted fields
    for key in ("price", "listPrice", "formattedPrice"):
        val = item.get(key)
        if val:
            cleaned = re.sub(r"[^0-9.]", "", str(val))
            try:
                result = int(float(cleaned))
                if result > 0:
                    return result
            except (ValueError, TypeError):
                pass
    return 0


def _split_address_string(addr_str: str):
    """Parse a flat address string into (street, city, state).

    Handles the Skolit format: '25305 Prado De La Felicidad, Calabasas, CA 91302'
    Also handles:  '22815 Sparrowdell Dr, Calabasas, CA'
    """
    # Match: everything up to the last ", CITY, ST[ ZIP]"
    m = re.match(r'^(.+),\s*([^,]+),\s*([A-Z]{2})\b', addr_str.strip())
    if m:
        return m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
    # Fallback: rsplit on comma
    parts = [p.strip() for p in addr_str.rsplit(",", 2)]
    if len(parts) == 3:
        state_tok = parts[2].split()
        return parts[0], parts[1], state_tok[0] if state_tok else ""
    if len(parts) == 2:
        return parts[0], parts[1], ""
    return addr_str, "Unknown City", ""


def _parse_address(item: dict):
    """Return (street, city, state) from whatever address shape the API uses."""
    addr = item.get("address") or item.get("streetAddress") or {}
    if isinstance(addr, dict):
        street = (addr.get("streetAddress") or addr.get("street") or
                  addr.get("line1") or addr.get("address") or "Unknown Address")
        city   = addr.get("city") or "Unknown City"
        state  = addr.get("state") or addr.get("stateCode") or ""
    elif isinstance(addr, str) and addr:
        # Skolit returns a single flat string: "STREET, CITY, ST ZIP"
        street, city, state = _split_address_string(addr)
        # Allow item-level city/state to override if address parse missed them
        city  = city  or item.get("city")  or item.get("addressCity")  or "Unknown City"
        state = state or item.get("state") or item.get("addressState") or item.get("stateCode") or ""
    else:
        street = "Unknown Address"
        city   = item.get("city") or item.get("addressCity") or "Unknown City"
        state  = item.get("state") or item.get("addressState") or item.get("stateCode") or ""
    return street, city, state


def _parse_zpid(item: dict) -> str:
    """Return the Zillow property ID from whichever field the API uses."""
    for key in ("zpid", "id", "propertyId", "mlsid", "zillowId"):
        val = item.get(key)
        if val is not None:
            return str(val)
    return ""


class ZillowAPIScraper:
    def __init__(self, api_key: str, host: str = ZILLOW_RAPIDAPI_HOST):
        self.api_key = api_key
        self.host = host
        self.base_url = f"https://{self.host}"

    @staticmethod
    def clean_location(raw_location: str):
        """
        Cleans a raw location string into a URL slug for the API.
        Examples:
            'Tarzana, CA'     -> ('tarzana-ca',  'Tarzana, CA')
            'Los Angeles'     -> ('los-angeles', 'Los Angeles')
            'in Tarzana, CA'  -> ('tarzana-ca',  'Tarzana, CA')
        """
        loc = raw_location.strip()
        for prefix in ("in ", "at ", "near ", "around ", "for "):
            if loc.lower().startswith(prefix):
                loc = loc[len(prefix):]
        loc = loc.strip()
        slug = loc.lower()
        slug = slug.replace(",", " ")
        slug = re.sub(r"\s+", "-", slug.strip())
        return slug, loc

    def fetch_properties(self, location: str, max_pages: int = 4):
        """
        Fetch for-sale properties from Zillow via the Skolit Live Data Scraper API.

        Uses /bylocation with explicit pagination (pagination.has_next).
        Stops early if the API reports no more pages or we hit max_pages.

        argv[2] / RAPIDAPI_MAX_PAGES controls max_pages at runtime.
        On the free tier (20 calls/month) keep max_pages=1.
        On Pro (10 000 calls/month) max_pages=4 is safe.
        """
        properties: list = []
        seen_ids: set = set()

        loc_slug, clean_name = self.clean_location(location)

        print(f"DEBUG: Input location:  '{location}'", file=sys.stderr)
        print(f"DEBUG: Cleaned slug:    '{loc_slug}'", file=sys.stderr)

        headers = {
            "X-RapidAPI-Key":  self.api_key,
            "X-RapidAPI-Host": self.host,
        }

        for page in range(1, max_pages + 1):
            params = {
                "location": loc_slug,
                "listType": "for-sale",
                "page": str(page),
            }

            print(f"Searching Zillow ({clean_name}) page {page}/{max_pages}…", file=sys.stderr)

            try:
                r = requests.get(
                    f"{self.base_url}/bylocation",
                    headers=headers, params=params, timeout=90,
                )

                # Always save raw page-1 response for debugging
                if page == 1:
                    debug_path = Path(__file__).parent.parent / "debug_last_api_response.json"
                    try:
                        with open(debug_path, "w") as df:
                            json.dump({
                                "host": self.host,
                                "params": params,
                                "status_code": r.status_code,
                                "response": r.json() if r.status_code == 200 else {"error": r.text},
                            }, df, indent=2)
                    except Exception:
                        pass

                if r.status_code != 200:
                    print(f"API Error ({r.status_code}): {r.text[:300]}", file=sys.stderr)
                    break

                data = r.json()

                # ── Pagination metadata ──────────────────────────────────
                pagination = data.get("pagination") or {}
                total_results = pagination.get("total_results") or data.get("totalCount") or 0
                has_next = pagination.get("has_next", False)

                results = (data.get("results") or data.get("data") or
                           data.get("properties") or data.get("listings") or [])

                if page == 1:
                    print(
                        f"DEBUG: Market total={total_results}  "
                        f"pages={pagination.get('total_pages','?')}  "
                        f"per_page={pagination.get('per_page', len(results))}",
                        file=sys.stderr,
                    )
                    # Log raw keys of first result so we can see the field shape
                    if results:
                        print(f"DEBUG: First result keys: {sorted(results[0].keys())}", file=sys.stderr)

                if not results:
                    print(f"DEBUG: Page {page} empty — stopping.", file=sys.stderr)
                    break

                print(f"DEBUG: Page {page}: {len(results)} results", file=sys.stderr)

                for item in results:
                    # Filter out land/mobile — handle both string and list homeType
                    home_type = str(item.get("homeType") or item.get("propertyType") or "").upper()
                    if any(t in home_type for t in ("LOT", "LAND", "MANUFACTURED", "MOBILE")):
                        continue

                    # ── Identity ────────────────────────────────────────
                    zpid = _parse_zpid(item)
                    if zpid and zpid in seen_ids:
                        continue
                    if zpid:
                        seen_ids.add(zpid)

                    # ── Price ───────────────────────────────────────────
                    price = _parse_price(item)

                    # ── Address ─────────────────────────────────────────
                    street, city, state = _parse_address(item)

                    # ── Numeric fields — try multiple names ─────────────
                    beds  = (item.get("beds") or item.get("bedrooms") or
                             item.get("bedroomsCount") or 3)
                    baths = (item.get("baths") or item.get("bathrooms") or
                             item.get("bathroomsCount") or 2.0)
                    sqft  = (item.get("area") or item.get("sqft") or
                             item.get("livingArea") or item.get("squareFootage") or
                             item.get("size") or 1500)
                    yr    = (item.get("yearBuilt") or item.get("year_built") or
                             item.get("builtYear") or 1990)

                    # ── Rent / zestimate ────────────────────────────────
                    rent_zest = (item.get("rentZestimate") or item.get("rentEstimate") or
                                 item.get("rent") or 0)
                    rent_est  = rent_zest or (max(2000, int(price * 0.007)) if price > 0 else 2500)

                    # ── Link / photo ────────────────────────────────────
                    link = (item.get("url") or item.get("detailUrl") or
                            item.get("hdpUrl") or item.get("link") or
                            (f"https://www.zillow.com/homedetails/{zpid}_zpid/" if zpid else ""))
                    photo = (item.get("photo_url") or item.get("imgSrc") or
                             item.get("image") or item.get("thumbnail") or
                             item.get("coverPhoto") or item.get("primaryPhoto"))
                    if isinstance(photo, dict):
                        photo = photo.get("url") or photo.get("src")

                    prop_id = f"ZILLOW-{zpid or len(properties) + 1}"

                    prop = Property(
                        property_id=prop_id,
                        address=street,
                        city=city,
                        state=state,
                        price=price if price > 0 else 400000,
                        bedrooms=int(beds) if beds else 3,
                        bathrooms=float(baths) if baths else 2.0,
                        sqft=int(sqft) if sqft else 1500,
                        year_built=int(yr) if yr else 1990,
                        property_type=item.get("homeType") or item.get("propertyType") or "Single Family",
                        estimated_rent=int(rent_est),
                        hoa_fees=0,
                        property_tax_annual=int(price * 0.0125) if price > 0 else 5000,
                        insurance_annual=1200,
                    )
                    prop.link = link
                    prop.img_src = photo
                    # Discovery-phase signals (used by evaluator as fallback when BD fails)
                    prop.zestimate = (item.get("zestimate") or item.get("zestimateAmount") or 0)
                    prop.days_on_zillow = (item.get("daysOnZillow") or item.get("daysOnMarket") or
                                           item.get("dom") or 0)
                    prop.tax_assessed_value = (item.get("taxAssessedValue") or item.get("assessedValue") or 0)
                    properties.append(prop)

                # ── Stop conditions ──────────────────────────────────────
                if not has_next:
                    print(f"DEBUG: No next page (pagination.has_next=false). Done.", file=sys.stderr)
                    break

            except Exception as e:
                print(f"API Request failed: {e}", file=sys.stderr)
                break

        if properties:
            p = properties[0]
            print(f"DEBUG: First property → {p.address}, {p.city}, {p.state} (${p.price:,})", file=sys.stderr)
            print(f"DEBUG: Total collected: {len(properties)}", file=sys.stderr)
        else:
            print("DEBUG: No properties returned.", file=sys.stderr)

        return properties


if __name__ == "__main__":
    # argv[1] = location string, argv[2] = max_pages (default 1)
    query = sys.argv[1] if len(sys.argv) > 1 else "Calabasas, CA"
    try:
        max_pages_arg = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    except ValueError:
        max_pages_arg = 1

    # Key candidates — try env first, then .env file under several possible names
    KEY_NAMES  = ["ZILLOW_RAPIDAPI_KEY", "RAPIDAPI_KEY", "ZILLOW_API_KEY", "RAPIDAPI_TOKEN"]
    HOST_NAMES = ["ZILLOW_RAPIDAPI_HOST", "RAPIDAPI_HOST", "ZILLOW_HOST"]

    API_KEY  = next((os.environ.get(n) for n in KEY_NAMES  if os.environ.get(n)), "")
    API_HOST = next((os.environ.get(n) for n in HOST_NAMES if os.environ.get(n)), "")

    if not API_KEY or not API_HOST:
        env_path = Path(__file__).parent.parent / ".env"
        if env_path.exists():
            env_values: dict = {}
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env_values[k.strip()] = v.strip().strip('"').strip("'")
            if not API_KEY:
                API_KEY  = next((env_values[n] for n in KEY_NAMES  if n in env_values), "")
            if not API_HOST:
                API_HOST = next((env_values[n] for n in HOST_NAMES if n in env_values), "")

    if not API_KEY:
        print(f"ERROR: No RapidAPI key found. Set one of: {KEY_NAMES}", file=sys.stderr)
        print("JSON_START:[]" + ":JSON_END")
        sys.exit(1)

    if not API_HOST or API_HOST == _DEFAULT_HOST:
        print(f"ERROR: No Skolit host found. Set one of: {HOST_NAMES}", file=sys.stderr)
        print("JSON_START:[]" + ":JSON_END")
        sys.exit(1)

    print(f"DEBUG: Using host={API_HOST}  key=...{API_KEY[-6:]}", file=sys.stderr)
    scraper = ZillowAPIScraper(API_KEY, host=API_HOST)
    props = scraper.fetch_properties(query, max_pages=max_pages_arg)

    property_list = [p.to_dict() if hasattr(p, "to_dict") else p.__dict__ for p in props]
    print("JSON_START:" + json.dumps(property_list) + ":JSON_END")
