#!/usr/bin/env python3
"""
OneConnect Graph Data Downloader
Standalone script to download Case Status and Compressor Status graph data
from StoreConnect Pulse portal.

No Claude or external dependencies required - just run it!
"""

import json
import os
import sys
import requests
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
from typing import List, Dict, Optional

class OneConnectDownloader:
    """Download graph data from OneConnect API"""

    def __init__(self):
        self.base_url = "https://api.us.oneconnect.net/oneconnect-api"
        self.project_id = 136  # Dollar General project
        self.tenant_id = 37    # Dollar General tenant
        self.token_file = Path("C:\\Users\\silam\\OneC\\token.txt")
        self.token = None
        self.headers = None

    def setup_token(self):
        """One-time setup: Save auth token locally"""
        print("\n" + "="*70)
        print("ONE-TIME SETUP: Getting your auth token")
        print("="*70)

        # Check if token already exists
        if self.token_file.exists():
            with open(self.token_file, 'r') as f:
                self.token = f.read().strip()
            print(f"\n✓ Token found in {self.token_file}")
            print(f"Token preview: {self.token[:50]}...")
            use_existing = input("\nUse this token? (y/n): ").strip().lower()
            if use_existing == 'y':
                self._test_token()
                return True

        # Get new token from user
        print("\nTo get your auth token:")
        print("1. Open https://mc.us.oneconnect.net in your browser")
        print("2. Press F12 to open DevTools")
        print("3. Go to Console tab")
        print("4. Run: console.log(localStorage.getItem('TOKEN'))")
        print("5. Copy the token (starts with 'eyJ...')")
        print()

        self.token = input("Paste your TOKEN here: ").strip()

        if not self.token or len(self.token) < 50:
            print("ERROR: Invalid token!")
            return False

        # Save token
        self.token_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.token_file, 'w') as f:
            f.write(self.token)
        print(f"\n✓ Token saved to {self.token_file}")

        # Test token
        if self._test_token():
            return True
        else:
            self.token_file.unlink()
            return False

    def _test_token(self) -> bool:
        """Test if token is valid"""
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        print("\nTesting token...")
        url = f"{self.base_url}/projects/{self.project_id}/tenants/{self.tenant_id}/tenant-api/asset-optimizer/v1/assets/7616"

        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            if response.status_code == 200:
                print("✓ Token is VALID and working!")
                return True
            else:
                print(f"✗ Token test failed (status {response.status_code})")
                return False
        except Exception as e:
            print(f"✗ Token test failed: {e}")
            return False

    def get_asset_modules(self, asset_id: str) -> List[Dict]:
        """Get all modules for an asset"""
        url = f"{self.base_url}/tenants/{self.tenant_id}/asset-optimizer/v1/assets"

        # Search for the asset by ID
        params = {
            "pageNumber": 0,
            "pageSize": 1000
        }

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=10)
            response.raise_for_status()

            assets = response.json().get("content", [])

            # Find matching asset
            matching_asset = None
            for asset in assets:
                if asset.get("id") == asset_id or asset.get("name") == asset_id:
                    matching_asset = asset
                    break

            if not matching_asset:
                print(f"  ✗ Asset {asset_id} not found!")
                return []

            asset_internal_id = matching_asset.get("id")
            asset_name = matching_asset.get("name", asset_id)

            # Get modules for this asset
            url_modules = f"{self.base_url}/tenants/{self.tenant_id}/tenant-api/asset-optimizer/v1/assets/{asset_internal_id}"
            response = requests.get(url_modules, headers=self.headers, timeout=10)
            response.raise_for_status()

            asset_detail = response.json()
            modules = asset_detail.get("modules", [])

            print(f"  ✓ Found {len(modules)} module(s) for {asset_name}")

            return modules

        except Exception as e:
            print(f"  ✗ Error getting modules: {e}")
            return []

    def query_telemetry(self,
                       module_id: int,
                       attributes: List[str],
                       hours: int = 24) -> Dict:
        """Query telemetry data for a module"""

        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours)

        url = f"{self.base_url}/projects/{self.project_id}/telemetry/v1/telemetry-attributes:query"

        payload = {
            "attributes": [
                {
                    "names": attributes,
                    "filters": [
                        {
                            "key": "module",
                            "values": [str(module_id)]
                        }
                    ]
                }
            ],
            "startTime": int(start_time.timestamp() * 1000),
            "endTime": int(end_time.timestamp() * 1000),
            "limit": 10000
        }

        try:
            response = requests.post(url, json=payload, headers=self.headers, timeout=30)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"    ✗ Telemetry query failed: {e}")
            return {}

    def parse_telemetry_response(self, response: Dict) -> pd.DataFrame:
        """Parse telemetry response into DataFrame"""

        all_data = {}

        for attr in response.get("attributes", []):
            attr_name = attr.get("name", "")
            points = attr.get("points", [])

            # Convert to (timestamp, value) pairs
            all_data[attr_name] = [
                (p["timestamp"], p.get("value")) for p in points
            ]

        # Create DataFrames and merge
        dfs = []
        for attr_name, points in all_data.items():
            if points:
                df = pd.DataFrame(points, columns=['timestamp', attr_name])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                dfs.append(df)

        if dfs:
            df_result = dfs[0]
            for other_df in dfs[1:]:
                df_result = df_result.merge(other_df, on='timestamp', how='outer')
            df_result = df_result.sort_values('timestamp')
            return df_result

        return pd.DataFrame()

    def download_case_data(self, asset_id: str, hours: int = 24) -> Dict[str, pd.DataFrame]:
        """Download all graph data for a case"""

        print(f"\nDownloading data for case: {asset_id}")
        print("-" * 70)

        # Get modules
        modules = self.get_asset_modules(asset_id)
        if not modules:
            return {}

        result = {}

        # Query each module
        for i, module in enumerate(modules, 1):
            module_id = module.get("id")
            module_name = module.get("name", f"Module {i}")

            print(f"\n  Module {i}: {module_name} (ID: {module_id})")

            # Case Status data
            print(f"    Querying Case Status...")
            case_status_attrs = [
                "ControlTemp-LTTB",
                "Alarm-LTTB",
                "DefrostTerminate-LTTB",
                "ControlStatus-LTTB"
            ]
            case_data = self.query_telemetry(module_id, case_status_attrs, hours)
            case_df = self.parse_telemetry_response(case_data)

            if len(case_df) > 0:
                print(f"      ✓ Got {len(case_df)} Case Status data points")
            else:
                print(f"      ⚠ No Case Status data found")

            # Compressor Status data
            print(f"    Querying Compressor Status...")
            compressor_attrs = [
                "RefrigerationDO-LTTB",
                "CompressorDischargeTem-LTTB"
            ]
            comp_data = self.query_telemetry(module_id, compressor_attrs, hours)
            comp_df = self.parse_telemetry_response(comp_data)

            if len(comp_df) > 0:
                print(f"      ✓ Got {len(comp_df)} Compressor Status data points")
            else:
                print(f"      ⚠ No Compressor Status data found")

            # Store results
            result[f"{module_name}_case_status"] = case_df
            result[f"{module_name}_compressor_status"] = comp_df

        return result

    def save_to_excel(self, asset_id: str, data: Dict[str, pd.DataFrame]) -> str:
        """Save all data to Excel file"""

        output_dir = Path("C:\\Users\\silam\\OneC\\downloads")
        output_dir.mkdir(parents=True, exist_ok=True)

        # Create filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_file = output_dir / f"{asset_id}_{timestamp}.xlsx"

        # Write to Excel with multiple sheets
        with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
            for sheet_name, df in data.items():
                if len(df) > 0:
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
                    print(f"  ✓ Saved sheet: {sheet_name}")

        return str(output_file)

    def run(self, case_ids: List[str], hours: int = 24):
        """Main execution"""

        print("\n" + "="*70)
        print("OneConnect Graph Data Downloader")
        print("="*70)

        # Setup token if needed
        if not self.token:
            if not self.setup_token():
                print("\nERROR: Could not setup token. Exiting.")
                return False

        # Download data for each case
        print(f"\n{'='*70}")
        print(f"Downloading data for {len(case_ids)} case(s)...")
        print(f"{'='*70}")

        successful = []
        failed = []

        for case_id in case_ids:
            try:
                data = self.download_case_data(case_id, hours)

                if data:
                    output_file = self.save_to_excel(case_id, data)
                    print(f"\n✓ SUCCESS: Data saved to {output_file}")
                    successful.append(case_id)
                else:
                    print(f"\n✗ FAILED: No data found for {case_id}")
                    failed.append(case_id)

            except Exception as e:
                print(f"\n✗ ERROR processing {case_id}: {e}")
                failed.append(case_id)

        # Summary
        print("\n" + "="*70)
        print("SUMMARY")
        print("="*70)
        print(f"Successful: {len(successful)}")
        for case_id in successful:
            print(f"  ✓ {case_id}")

        if failed:
            print(f"\nFailed: {len(failed)}")
            for case_id in failed:
                print(f"  ✗ {case_id}")

        print(f"\nAll files saved to: C:\\Users\\silam\\OneC\\downloads\\")

        return len(failed) == 0


def main():
    """Main entry point"""

    # Example: Download data for MY20D029022
    downloader = OneConnectDownloader()

    # List of case IDs to download
    case_ids = [
        "MY20D029022",  # Example - replace with your actual case IDs
    ]

    # Download last 24 hours of data
    downloader.run(case_ids, hours=24)


if __name__ == "__main__":
    main()
