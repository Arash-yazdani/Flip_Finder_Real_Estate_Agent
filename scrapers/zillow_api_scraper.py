import requests
import json
import sys
import os
import re
from pathlib import Path

# Add project root to path so we can import models
sys.path.insert(0, str(Path(__file__).parent.parent))
from models.property import Property

class ZillowAPIScraper:
    def __init__(self, api_key):
        self.api_key = api_key
        self.host = "real-estate101.p.rapidapi.com"
        self.base_url = f"https://{self.host}/api"

    @staticmethod
    def clean_location(raw_location):
        """
        Cleans a raw location string into a proper slug for the API.
        Examples:
            'Tarzana, CA'   -> 'tarzana-ca'
            'Los Angeles'   -> 'los-angeles'
            'in Tarzana, CA' -> 'tarzana-ca'
        """
        loc = raw_location.strip()
        
        # Strip leading prepositions that might leak through from NLP
        for prefix in ['in ', 'at ', 'near ', 'around ', 'for ']:
            if loc.lower().startswith(prefix):
                loc = loc[len(prefix):]
        
        loc = loc.strip()
        
        # Normalize: lowercase, replace ", " and " " with "-"
        slug = loc.lower()
        slug = slug.replace(',', ' ')           # "tarzana, ca" -> "tarzana  ca"
        slug = re.sub(r'\s+', '-', slug.strip()) # "tarzana  ca" -> "tarzana-ca"
        
        return slug, loc  # return both slug and cleaned human-readable name

    def fetch_properties(self, location, max_pages=4):
        """
        Fetches properties from Zillow via RapidAPI based on location string.
        Fetches up to max_pages pages automatically; stops early when a short page
        (< 35 results) signals the end of results for a smaller market.
        """
        properties = []
        seen_ids = set()  # deduplicate across pages
        filtered_count = 0  # set from page-1 API response; used for early-stop
        headers = {
            "X-RapidAPI-Key": self.api_key,
            "X-RapidAPI-Host": self.host
        }

        url = f"{self.base_url}/search"
        loc_slug, clean_name = self.clean_location(location)

        print(f"DEBUG: Input location: '{location}'", file=sys.stderr)
        print(f"DEBUG: Cleaned slug:   '{loc_slug}'", file=sys.stderr)
        print(f"DEBUG: Clean name:     '{clean_name}'", file=sys.stderr)

        for page in range(1, max_pages + 1):
            querystring = {
                "location": loc_slug,
                "page": str(page),
                "status": "forSale",
                "isManufactured": "false",
                "isLotLand": "false"
            }

            print(f"Searching Zillow API for '{clean_name}' (Page {page}/{max_pages})...", file=sys.stderr)

            try:
                response = requests.get(url, headers=headers, params=querystring, timeout=90)

                # Save the raw response for debugging (page 1 only to avoid overwriting)
                if page == 1:
                    debug_path = Path(__file__).parent.parent / "debug_last_api_response.json"
                    try:
                        with open(debug_path, "w") as df:
                            debug_data = {
                                "request_params": querystring,
                                "status_code": response.status_code,
                                "response": response.json() if response.status_code == 200 else {"error": response.text}
                            }
                            json.dump(debug_data, df, indent=2)
                        print(f"DEBUG: Saved raw API response to {debug_path}", file=sys.stderr)
                    except Exception as save_err:
                        print(f"DEBUG: Could not save debug response: {save_err}", file=sys.stderr)

                if response.status_code != 200:
                    print(f"API Error ({response.status_code}): {response.text}", file=sys.stderr)
                    break

                data = response.json()
                results = data.get('results', [])

                if not results:
                    print(f"DEBUG: Page {page} returned 0 results, stopping.", file=sys.stderr)
                    break

                # On page 1 read the market-wide filtered count so we know when to stop.
                # filteredCount is the total number of results matching our filter params.
                # When collected >= filteredCount we have everything and can skip extra pages.
                if page == 1:
                    total_count = data.get('totalCount') or 0
                    filtered_count = data.get('filteredCount') or 0
                    print(
                        f"DEBUG: Market has ~{total_count} total / {filtered_count} filtered listings;"
                        f" fetching up to {max_pages} pages",
                        file=sys.stderr,
                    )

                print(f"DEBUG: Page {page}: {len(results)} results", file=sys.stderr)

                for item in results:
                    # Python-side filtering to be 100% sure we catch anything the API missed
                    home_type = str(item.get('homeType', '')).upper()
                    if home_type in ['LOT', 'LAND', 'MANUFACTURED', 'MOBILE']:
                        print(f"DEBUG: Filtering out {home_type} at {item.get('address', {}).get('street')}", file=sys.stderr)
                        continue

                    # Deduplicate across pages — handle id=None safely
                    raw_id = item.get('id')
                    item_id = str(raw_id) if raw_id is not None else ''
                    if item_id and item_id in seen_ids:
                        continue
                    if item_id:
                        seen_ids.add(item_id)

                    # Map API fields to our Property dataclass
                    price = item.get('unformattedPrice', 0)
                    if not price and 'price' in item:
                        price_str = str(item['price']).replace('$', '').replace(',', '')
                        try:
                            price = int(float(price_str))
                        except:
                            price = 0

                    addr_data = item.get('address', {})
                    if isinstance(addr_data, dict):
                        address = addr_data.get('street', 'Unknown Address')
                        city = addr_data.get('city', 'Unknown City')
                        state = addr_data.get('state', '')
                    else:
                        address = str(addr_data)
                        city = "Unknown"
                        state = ""

                    prop_id = f"ZILLOW-{item_id or len(properties)+1}"

                    property_obj = Property(
                        property_id=prop_id,
                        address=address,
                        city=city,
                        state=state,
                        price=price if price > 0 else 400000,
                        bedrooms=item.get('beds', 3),
                        bathrooms=item.get('baths', 2.0),
                        sqft=item.get('area', 1500),
                        year_built=item.get('yearBuilt', 1990),
                        property_type=item.get('homeType', 'Single Family'),
                        estimated_rent=item.get('rentZestimate', max(2000, int(price * 0.007)) if price > 0 else 2500),
                        hoa_fees=0,
                        property_tax_annual=int(price * 0.0125) if price > 0 else 5000,
                        insurance_annual=1200
                    )
                    property_obj.link = item.get('detailUrl', f"https://www.zillow.com/homedetails/{item_id}_zpid/")
                    property_obj.img_src = item.get('imgSrc')
                    # Carry discovery-phase signals that are already in the RapidAPI response
                    property_obj.zestimate = item.get('zestimate') or 0
                    property_obj.days_on_zillow = item.get('daysOnZillow') or 0
                    property_obj.tax_assessed_value = item.get('taxAssessedValue') or 0
                    properties.append(property_obj)

                # Stop early if we've collected everything the API says is available —
                # avoids a wasted extra call when all results fit on the first page.
                if filtered_count > 0 and len(properties) >= filtered_count:
                    print(f"DEBUG: Collected all {len(properties)}/{filtered_count} filtered results.", file=sys.stderr)
                    break

                # Also stop on a clearly short page (last page of a market)
                if len(results) < 35:
                    print(f"DEBUG: Page {page} short ({len(results)} results), no more pages.", file=sys.stderr)
                    break

            except Exception as e:
                print(f"API Request failed: {e}", file=sys.stderr)
                break
        
        # Log first result for quick verification
        if properties:
            p = properties[0]
            print(f"DEBUG: First result -> {p.address}, {p.city}, {p.state} (${p.price:,})", file=sys.stderr)
        else:
            print("DEBUG: No properties returned.", file=sys.stderr)
                
        return properties

if __name__ == "__main__":
    # argv[1] = location, argv[2] = max_pages (optional, default 1)
    query = sys.argv[1] if len(sys.argv) > 1 else "Sacramento, CA"
    try:
        max_pages_arg = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    except ValueError:
        max_pages_arg = 1

    # Use the provided API Key
    API_KEY = "69379c2654mshe28db71c0b234a7p148f59jsn6b8c7c501542"

    scraper = ZillowAPIScraper(API_KEY)
    props = scraper.fetch_properties(query, max_pages=max_pages_arg)
    
    # Dump to JSON to stdout for telegram_bot.py to read
    # We use a marker to find the JSON start and end
    # IMPORTANT: Only the JSON marker goes to stdout; all debug goes to stderr
    property_list = [p.to_dict() if hasattr(p, 'to_dict') else p.__dict__ for p in props]
    
    print("JSON_START:" + json.dumps(property_list) + ":JSON_END")
