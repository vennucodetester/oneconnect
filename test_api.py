import requests
from datetime import datetime, timezone, timedelta

def run_test():
    with open("token.txt") as f:
        token = f.read().strip()
    
    BASE_URL = "https://api.us.oneconnect.net/oneconnect-api"
    PROJECT_ID = "136"
    url = f"{BASE_URL}/projects/{PROJECT_ID}/telemetry/v1/telemetry-attributes:query"
    
    now = datetime.now(timezone.utc)
    from_dt = now - timedelta(days=30)
    
    payload = {
        "serviceType": "Microblock-sensor-data",
        "aggregatedAttributes": [
            {
                "attribute": "Control Temperature",
                "id": 0,
                "name": "Control Temperature",
                "legend": f"Control Temp",
                "aggregation": "LTTB"
            }
        ],
        "searchSpan": {
            "from": from_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "to":   now.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        },
        "timeSeriesId": {"assetId": 6999},
        "withStep": True,
        "pageSize": 2000,
    }

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    r = requests.post(url, json=payload, headers=headers)
    if r.status_code == 200:
        data = r.json().get('data', [])
        print(f"points: {len(data)}")
        if data:
            print(data[0])
    else:
        print(f"ERROR {r.status_code}")
        print(r.text[:200])

run_test()
