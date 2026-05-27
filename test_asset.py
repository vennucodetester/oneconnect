import requests
import json

def run_test():
    with open("token.txt") as f:
        token = f.read().strip()
    
    BASE_URL = "https://api.us.oneconnect.net/oneconnect-api"
    TENANT_ID = "37"
    url = f"{BASE_URL}/tenants/{TENANT_ID}/tenant-api/asset-optimizer/v1/assets"
    
    params = {"pageNumber": 0, "pageSize": 5, "search": "MY25H061997"}
    headers = {"Authorization": f"Bearer {token}"}
    
    resp = requests.get(url, headers=headers, params=params)
    if resp.status_code == 200:
        assets = resp.json().get("data", [])
        for a in assets:
            print(f"serialNumber: {a.get('serialNumber')}, externalId: {a.get('externalId')}")
    else:
        print("Error", resp.status_code)

run_test()
