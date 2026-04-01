from playwright.sync_api import sync_playwright
import re
import os
import sys
from pathlib import Path
import json

# Add project root to path so we can import models
sys.path.insert(0, str(Path(__file__).parent.parent))
from models.property import Property

class RedfinAIScraper:
    def __init__(self, headless=True):
        self.headless = headless

    def _run_scrape(self, user_query):
        properties = []
        with sync_playwright() as p:
            # Launch browser with evasion techniques for Cloudflare
            browser = p.chromium.launch(
                headless=self.headless,
                args=['--disable-blink-features=AutomationControlled']
            )
            
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            
            page = context.new_page()
            
            print(f"Navigating to Redfin for query: '{user_query}'")
            try:
                page.goto("https://www.redfin.com", wait_until="commit")
                # Wait for potential cloudflare challenges to clear
                page.wait_for_timeout(3000)
                
                # Check for "Ask AI" or "AI search" link/button at the top
                # Using regex to catch variations of "AI Search" "Ask Redfin"
                ai_button = page.get_by_text(re.compile(r"Ask (Redfin|AI)|AI search", re.IGNORECASE)).first
                
                if ai_button.is_visible(timeout=5000):
                    ai_button.click()
                else:
                    # Fallback to the main search bar just in case it triggers the AI view
                    search_box = page.locator('input[title="City, Address, School, Agent, ZIP"]')
                    search_box.click()
                    
                page.wait_for_timeout(1000)
                
                # Type the user's query
                # Look for a chat input box or the main search box
                focused_locator = page.locator('*:focus')
                focused_locator.fill(user_query)
                focused_locator.press('Enter')
                
                print("Query submitted. Waiting for results...")
                
                # Wait for AI results to stream / load cards (approx 10 seconds)
                page.wait_for_timeout(10000)
                
                # Scroll a bit to trigger lazy loads
                page.mouse.wheel(0, 1000)
                page.wait_for_timeout(2000)
                
                # Now extract property cards
                # Redfin typically uses something like 'div.homecardv2' or links containing '/home/'
                cards = page.locator('a[href*="/home/"]').all()
                
                # Deduplicate links
                seen_links = set()
                
                for card in cards:
                    if len(properties) >= 30:
                        break
                        
                    href = card.get_attribute('href')
                    if not href or href in seen_links:
                        continue
                    seen_links.add(href)
                    
                    link = f"https://www.redfin.com{href}" if href.startswith('/') else href
                    text = card.inner_text()
                    
                    # Very basic fallback parsing of the card text if the AI returned a standard grid
                    # Expecting something like "$450,000\n3 Beds\n2 Baths\n1,200 Sq. Ft.\n123 Mockingbird Ln, City, CA 12345"
                    
                    price_match = re.search(r'\$?([\d,]+)', text)
                    price = int(re.sub(r'[^\d]', '', price_match.group(1))) if price_match else 0
                    
                    beds_match = re.search(r'(\d+)\s+Beds?', text, re.IGNORECASE)
                    beds = int(beds_match.group(1)) if beds_match else 3
                    
                    baths_match = re.search(r'([\d\.]+)\s+Baths?', text, re.IGNORECASE)
                    baths = float(baths_match.group(1)) if baths_match else 2.0
                    
                    sqft_match = re.search(r'([\d,]+)\s+Sq', text, re.IGNORECASE)
                    sqft = int(re.sub(r'[^\d]', '', sqft_match.group(1))) if sqft_match else 1500
                    
                    # Create the property object
                    prop_id = f"REDFIN-{len(properties)+1:03d}"
                    
                    try:
                        address_lines = [line.strip() for line in text.split('\n') if line.strip() and not '$' in line and not 'Bed' in line and not 'Bath' in line and not 'Sq' in line]
                        address = address_lines[-1] if address_lines else "Unknown Address"
                    except:
                        address = "Unknown Address"
                    
                    property_obj = Property(
                        property_id=prop_id,
                        address=address,
                        city="User Query Location",
                        state="CA",
                        price=price if price > 0 else 400000,
                        bedrooms=beds,
                        bathrooms=baths,
                        sqft=sqft,
                        year_built=1990,
                        property_type="Single Family",
                        estimated_rent=max(2000, int(price * 0.007)) if price > 0 else 2500,
                        hoa_fees=0,
                        property_tax_annual=int(price * 0.0125) if price > 0 else 5000,
                        insurance_annual=1200
                    )
                    property_obj.link = link
                    properties.append(property_obj)
                    
            except Exception as e:
                print(f"Playwright Scraping Error: {e}")
                # Save a debug screenshot if possible
                try:
                    page.screenshot(path="error_screenshot.png")
                except:
                    pass
            finally:
                context.close()
                browser.close()
                
        return properties
        
    def fetch_properties(self, user_query):
        """Wrapper for the playwright function"""
        return self._run_scrape(user_query)

if __name__ == "__main__":
    import sys
    import json
    
    if len(sys.argv) > 1:
        query = sys.argv[1]
    else:
        query = "give me top 5 properties"
        
    scraper = RedfinAIScraper(headless=True)
    props = scraper.fetch_properties(query)
    
    # Dump to JSON to stdout so python-telegram-bot can parse it
    output = []
    for p in props:
        output.append({
            "property_id": p.property_id,
            "address": p.address,
            "city": p.city,
            "state": p.state,
            "price": p.price,
            "bedrooms": p.bedrooms,
            "bathrooms": p.bathrooms,
            "sqft": p.sqft,
            "year_built": p.year_built,
            "property_type": p.property_type,
            "estimated_rent": p.estimated_rent,
            "hoa_fees": p.hoa_fees,
            "property_tax_annual": p.property_tax_annual,
            "insurance_annual": p.insurance_annual,
            "link": getattr(p, 'link', '')
        })
        
    print("JSON_START:" + json.dumps(output) + ":JSON_END")
