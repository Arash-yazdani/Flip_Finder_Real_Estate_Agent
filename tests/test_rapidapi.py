import requests
import json
import urllib.parse

def test_api():
    """
    Test real-estate101 rapidapi endpoint variants to see which one actively responds
    to the user's provided API key and host.
    """
    api_key = "69379c2654mshe28db71c0b234a7p148f59jsn6b8c7c501542"
    
    # 1. Test real-estate101 properties/list or property/search
    endpoints_to_test = [
        ("https://real-estate101.p.rapidapi.com/property/forSale", {"location": "Sacramento, CA", "city": "Sacramento"}),
        ("https://real-estate101.p.rapidapi.com/properties/search", {"location": "Sacramento, CA", "city": "Sacramento"}),
        ("https://real-estate101.p.rapidapi.com/properties/list", {"location": "Sacramento, CA", "city": "Sacramento", "state": "CA"}),
        ("https://zillow-com1.p.rapidapi.com/propertyExtendedSearch", {"location": "Sacramento, CA"}), # Testing fallback standard Zillow API
    ]

    for url, params in endpoints_to_test:
        host = url.split("://")[1].split("/")[0]
        headers = {
            'x-rapidapi-host': host,
            'x-rapidapi-key': api_key
        }
        
        try:
            print(f"\n--- Testing Endpoint: {url} ---")
            response = requests.get(url, headers=headers, params=params, timeout=10)
            print(f"Status Code: {response.status_code}")
            
            if response.status_code == 200:
                data = response.json()
                print("SUCCESS! Data keys:", list(data.keys()) if isinstance(data, dict) else "List of items")
                # Just print a snippet of the successful response
                print("Snippet:", json.dumps(data, indent=2)[:300])
                break # We found the working endpoint!
            else:
                print(f"Response: {response.text[:200]}")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    test_api()
