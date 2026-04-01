import sys
from pathlib import Path
import re
from rich.console import Console

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from models.property import Property
from agents.market_analyzer import MarketAnalyzer
from agents.property_evaluator import PropertyEvaluator
from agents.financial_calculator import FinancialCalculator
from agents.decision_engine import DecisionEngine
from main import display_summary_table, display_detailed_report, save_results

console = Console()

def parse_sacramento_data(file_path):
    properties = []
    with open(file_path, 'r') as f:
        lines = f.readlines()
        
    for i, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        
        line = re.sub(r'\$(\d+),(\d+)', r'$\1\2', line)
        
        parts = [p.strip() for p in line.split(',')]
        if len(parts) >= 8:
            address = parts[0]
            city = parts[1]
            zipcode = parts[2]
            
            # Clean price string
            price_str = re.sub(r'[^\d]', '', parts[3])
            price = int(price_str) if price_str else 0
            
            bedrooms = int(parts[4])
            bathrooms = float(parts[5])
            sqft = int(parts[6])
            link = parts[7]
            
            prop_id = f"SAC-{i+1:03d}"
            
            property_obj = Property(
                property_id=prop_id,
                address=address,
                city=city,
                state="CA", 
                price=price,
                bedrooms=bedrooms,
                bathrooms=bathrooms,
                sqft=sqft,
                year_built=1980, # Assumption
                property_type="Single Family",
                estimated_rent=max(2000, int(price * 0.007)), # Estimation
                hoa_fees=0,
                property_tax_annual=int(price * 0.0125),
                insurance_annual=1200
            )
            # Monkey patch the link onto the object so we can print it
            property_obj.link = link
            properties.append(property_obj)
            
    return properties

def run_analysis(properties):
    agents = {
        "market": MarketAnalyzer(),
        "evaluator": PropertyEvaluator(),
        "financial": FinancialCalculator(),
        "decision": DecisionEngine()
    }
    
    all_results = []
    
    with console.status("[bold green]Analyzing Sacramento Properties...") as status:
        for prop in properties:
            market = agents["market"].analyze_market(prop)
            eval_result = agents["evaluator"].evaluate_property(prop)
            finance = agents["financial"].calculate_metrics(prop, market.appreciation_rate)
            decision = agents["decision"].make_decision(prop, market, eval_result, finance)
            
            result = {
                "property": prop,
                "market": market,
                "evaluation": eval_result,
                "financial": finance,
                "decision": decision
            }
            all_results.append(result)
            
    # Sort results
    sorted_results = sorted(all_results, 
                           key=lambda x: x["decision"].overall_score, 
                           reverse=True)
                           
    return sorted_results

def display_top_10(sorted_results):
    console.print("\n[bold magenta]=== TOP 10 PROPERTIES TO TOUR TOMORROW ===[/bold magenta]")
    for i, res in enumerate(sorted_results[:10]):
        prop = res["property"]
        decision = res["decision"]
        fin = res["financial"]
        
        console.print(f"\n[bold cyan]#{i+1}. {prop.address}, {prop.city} {prop.state}[/bold cyan]")
        console.print(f"Price: [green]${prop.price:,}[/green] | Grade: [bold]{decision.investment_grade}[/bold] | Score: {decision.overall_score}")
        console.print(f"Cap Rate: {fin.cap_rate}% | Cash Flow: ${fin.monthly_cash_flow} | 5-Yr ROI: {fin.roi_5_year}%")
        console.print(f"[blue][link={prop.link}]View on Redfin -> {prop.link}[/link][/blue]")
        
if __name__ == "__main__":
    file_path = "data/sacramento_data.txt"
    properties = parse_sacramento_data(file_path)
    console.print(f"[bold]Parsed {len(properties)} properties from {file_path}.[/bold]")
    
    sorted_results = run_analysis(properties)
    
    # Save results as usual
    save_results(sorted_results)
    
    # Print the custom Top 10
    display_top_10(sorted_results)
