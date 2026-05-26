#!/usr/bin/env python3
"""
Helper script to get your auth token from the browser and test API connection.
"""

import json
import requests
from datetime import datetime, timedelta

def get_token_instructions():
    """Print instructions for getting auth token."""
    print("\n" + "="*70)
    print("HOW TO GET YOUR AUTH TOKEN")
    print("="*70)
    print("""
1. Go to the StoreConnect Pulse portal: https://mc.us.oneconnect.net

2. Open Browser DevTools:
   - Press F12 (or right-click → Inspect)

3. Go to the Console tab:
   - Click on the Console tab at the top of DevTools

4. Run this command:
   console.log(localStorage.getItem('TOKEN'))

5. Copy the long token string that appears
   (It starts with something like 'eyJ...')

6. Paste it into the TOKEN variable below
""")
    print("="*70 + "\n")


def test_api_connection(token: str, project_id: int = 136):
    """Test if the token works by making a simple API call."""

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # Try a simple query to get latest telemetry
    url = f"https://api.us.oneconnect.net/oneconnect-api/projects/{project_id}/telemetry/v1/telemetry-attributes:latest"

    test_payload = {
        "attributes": [
            {
                "names": ["ControlTemp-LTTB"],
                "filters": [
                    {"key": "module", "values": ["7594"]}  # Test with example module
                ]
            }
        ]
    }

    print("Testing API connection...")
    try:
        response = requests.post(url, json=test_payload, headers=headers, timeout=10)
        print(f"Status Code: {response.status_code}")

        if response.status_code == 200:
            data = response.json()
            print("✓ API connection SUCCESS!")
            print(f"\nResponse sample: {json.dumps(data, indent=2)[:500]}...")
            return True
        else:
            print(f"✗ API returned status {response.status_code}")
            print(f"Response: {response.text}")
            return False

    except requests.exceptions.RequestException as e:
        print(f"✗ API connection FAILED: {e}")
        return False


def main():
    """Main function."""

    get_token_instructions()

    # Get token from user
    token = input("Paste your TOKEN here: ").strip()

    if not token:
        print("ERROR: No token provided!")
        return

    if len(token) < 50:
        print("ERROR: Token looks too short. Make sure you copied the full string!")
        return

    print(f"\nToken length: {len(token)} characters")
    print(f"Token preview: {token[:50]}...")

    # Test the connection
    print("\n" + "-"*70)
    success = test_api_connection(token)

    if success:
        print("\n✓ Your token is valid and working!")
        print("\nNext steps:")
        print("1. Edit extract_graph_data.py")
        print("2. Replace 'YOUR_TOKEN_HERE' with your token")
        print("3. Set MODULE_ID to your module ID")
        print("4. Run: python extract_graph_data.py")

        # Save token to file (optional)
        save = input("\nSave token to token.txt? (y/n): ").strip().lower()
        if save == 'y':
            with open("C:\\Users\\silam\\OneC\\token.txt", "w") as f:
                f.write(token)
            print("Token saved to token.txt")
            print("WARNING: Keep this file secure! Do not commit to git.")
    else:
        print("\n✗ Token validation failed. Check your token and try again.")


if __name__ == "__main__":
    main()
