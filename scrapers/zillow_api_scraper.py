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

    def fetch_properties(self, location, pages=1):
        """
        Fetches properties from Zillow via RapidAPI based on location string.
        """
        properties = []
        headers = {
            "X-RapidAPI-Key": self.api_key,
            "X-RapidAPI-Host": self.host
        }

        # The Zillow scraper endpoint for location search is /api/search
        url = f"{self.base_url}/search"
        
        for page in range(1, pages + 1):
            # Clean up location string (the API seems to prefer lowercase slugs like 'sacramento-ca')
            loc_slug = location.lower().replace(', ', '-').replace(' ', '-')
            querystring = {"location": loc_slug, "page": str(page), "status": "forSale"}
            
            print(f"Searching Zillow API for '{location}' (Page {page})...")
            try:
                response = requests.get(url, headers=headers, params=querystring, timeout=30)
                
                if response.status_code != 200:
                    print(f"API Error ({response.status_code}): {response.text}")
                    # If bylocation fails, try a simple search endpoint fallback if known, or just return empty
                    break
                
                data = response.json()
                results = data.get('results', [])
                
                if not results:
                    print("No results found in API response.")
                    break
                
                for item in results:
                    # Map API fields to our Property dataclass
                    # Based on the user's sample JSON:
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
                        state = addr_data.get('state', 'CA')
                    else:
                        address = str(addr_data)
                        city = "Unknown"
                        state = "CA"
                        
                    # Create the property object
                    prop_id = f"ZILLOW-{item.get('id', len(properties)+1)}"
                    
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
                    property_obj.link = item.get('detailUrl', f"https://www.zillow.com/homedetails/{item.get('id')}_zpid/")
                    properties.append(property_obj)
                    
            except Exception as e:
                print(f"API Request failed: {e}")
                break
                
        return properties

if __name__ == "__main__":
    # Get location from arguments
    query = sys.argv[1] if len(sys.argv) > 1 else "Sacramento, CA"
    
    # Use the provided API Key
    API_KEY = "69379c2654mshe28db71c0b234a7p148f59jsn6b8c7c501542"
    
    scraper = ZillowAPIScraper(API_KEY)
    props = scraper.fetch_properties(query)
    
    # Dump to JSON to stdout for telegram_bot.py to read
    # We use a marker to find the JSON start and end
    property_list = [p.to_dict() if hasattr(p, 'to_dict') else p.__dict__ for p in props]
    
    print("\nJSON_START:" + json.dumps(property_list) + ":JSON_END\n")
