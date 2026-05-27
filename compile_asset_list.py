import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE_URL = "https://mc.us.oneconnect.net"
API_BASE = "https://api.us.oneconnect.net/oneconnect-api"
TENANT_ID = 37
AUTH_STATE_FILE = Path(__file__).parent / "auth_state.json"
NUM_ASSETS = 1000

def main():
    print("Fetching full asset list grouped by nomenclature...")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        
        if AUTH_STATE_FILE.exists():
            context = browser.new_context(storage_state=str(AUTH_STATE_FILE))
        else:
            context = browser.new_context()
            
        page = context.new_page()
        page.goto(f"{BASE_URL}/login")
        
        # Wait for token
        for _ in range(300):
            try:
                token = page.evaluate("localStorage.getItem('TOKEN')")
                if token and token.startswith("Bearer"):
                    break
            except:
                pass
            time.sleep(1)
        else:
            print("Login timed out.")
            return
            
        print("Authenticated. Fetching cases...")
        
        # Fetch assets
        asset_list_json = page.evaluate(f"""
            fetch('{API_BASE}/tenants/{TENANT_ID}/tenant-api/asset-optimizer/v1/assets?pageNumber=0&pageSize={NUM_ASSETS}', {{
                headers: {{'Authorization': '{token}'}}
            }}).then(r => r.text())
        """)
        
        try:
            asset_list = json.loads(asset_list_json)
            assets = asset_list.get("data", []) if isinstance(asset_list, dict) else asset_list
            
            models = {}
            for a in assets:
                model_name = a.get("model", {}).get("name", "Unknown")
                serial = a.get("serialNumber", str(a.get("id")))
                models.setdefault(model_name, []).append(serial)
                
            out_path = Path(__file__).parent / "case_list_by_model.txt"
            with open(out_path, "w") as out:
                for model, serials in sorted(models.items()):
                    out.write(f"\n--- {model} ({len(serials)} units) ---\n")
                    out.write("\n".join(sorted(serials)) + "\n")
                    
            print(f"\nSuccess! Saved {len(assets)} cases to {out_path.name}")
            
        except Exception as e:
            print(f"Error parsing assets: {e}")
            
        context.storage_state(path=str(AUTH_STATE_FILE))
        browser.close()

if __name__ == "__main__":
    main()
