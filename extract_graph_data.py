#!/usr/bin/env python3
"""
Extract graph data (x,y coordinates) from StoreConnect Pulse portal
for a single module/cassette.
"""

import json
import requests
from datetime import datetime, timedelta
import pandas as pd
from typing import Dict, List, Any
import time

class TelemetryExtractor:
    def __init__(self, token: str, project_id: int = 136, tenant_id: int = 37):
        """
        Initialize the extractor with authentication token.

        Args:
            token: Bearer token from localStorage (get from browser)
            project_id: Project ID (default 136 for DG Dollar General)
            tenant_id: Tenant ID (default 37 for DG)
        """
        self.base_url = "https://api.us.oneconnect.net/oneconnect-api"
        self.project_id = project_id
        self.tenant_id = tenant_id
        self.token = token
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    def query_telemetry(self,
                       attribute_names: List[str],
                       start_time: datetime,
                       end_time: datetime,
                       module_id: int = None,
                       asset_id: int = None) -> Dict[str, Any]:
        """
        Query telemetry data for specified attributes and time range.

        Args:
            attribute_names: List of telemetry attributes to query
                            (e.g., ["ControlTemp-LTTB", "Alarm-LTTB", "DefrostTerminate-LTTB", "ControlStatus-LTTB"])
            start_time: Start datetime
            end_time: End datetime
            module_id: Module ID (required)
            asset_id: Asset ID (optional, derived from module if not provided)

        Returns:
            Dictionary with telemetry data points
        """

        # Build query request
        query_payload = {
            "attributes": [
                {
                    "names": attribute_names,
                    "filters": [
                        {
                            "key": "module",
                            "values": [str(module_id)]
                        }
                    ]
                }
            ],
            "startTime": int(start_time.timestamp() * 1000),  # milliseconds
            "endTime": int(end_time.timestamp() * 1000),      # milliseconds
            "limit": 10000  # Max points per query
        }

        url = f"{self.base_url}/projects/{self.project_id}/telemetry/v1/telemetry-attributes:query"

        print(f"Querying {', '.join(attribute_names)}...")
        print(f"Time range: {start_time} to {end_time}")

        response = requests.post(url, json=query_payload, headers=self.headers)
        response.raise_for_status()

        return response.json()

    def extract_case_status_data(self, module_id: int, hours: int = 24) -> pd.DataFrame:
        """
        Extract Case Status graph data (4 lines).

        Returns DataFrame with columns: timestamp, control_status, alarm_status, defrost_terminate, control_temp
        """

        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours)

        attribute_names = [
            "ControlTemp-LTTB",
            "Alarm-LTTB",
            "DefrostTerminate-LTTB",
            "ControlStatus-LTTB"
        ]

        data = self.query_telemetry(attribute_names, start_time, end_time, module_id)

        # Parse response into DataFrame
        df = self._parse_telemetry_response(data)
        return df

    def extract_compressor_status_data(self, module_id: int, hours: int = 24) -> pd.DataFrame:
        """
        Extract Compressor Status graph data (2 lines).

        Returns DataFrame with columns: timestamp, refrigeration_do, compressor_discharge_temp
        """

        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours)

        attribute_names = [
            "RefrigerationDO-LTTB",
            "CompressorDischargeTem-LTTB"  # Note: may need to verify exact name
        ]

        data = self.query_telemetry(attribute_names, start_time, end_time, module_id)

        # Parse response into DataFrame
        df = self._parse_telemetry_response(data)
        return df

    def _parse_telemetry_response(self, response: Dict) -> pd.DataFrame:
        """
        Parse telemetry API response into a pandas DataFrame.

        Response structure:
        {
            "attributes": [
                {
                    "name": "attribute_name",
                    "points": [
                        {"timestamp": ms, "value": numeric}
                    ]
                }
            ]
        }
        """

        all_points = {}

        # Extract all points from all attributes
        for attr in response.get("attributes", []):
            attr_name = attr["name"]
            points = attr.get("points", [])

            # Convert points to x,y pairs (timestamp, value)
            all_points[attr_name] = [
                (p["timestamp"], p.get("value")) for p in points
            ]

            print(f"  {attr_name}: {len(points)} data points")

        # Merge all attributes into single DataFrame
        dfs = []
        for attr_name, points in all_points.items():
            df_attr = pd.DataFrame(points, columns=['timestamp', attr_name])
            df_attr['timestamp'] = pd.to_datetime(df_attr['timestamp'], unit='ms')
            dfs.append(df_attr)

        # Merge on timestamp
        if dfs:
            df = dfs[0]
            for other_df in dfs[1:]:
                df = df.merge(other_df, on='timestamp', how='outer')
            df = df.sort_values('timestamp')
            return df

        return pd.DataFrame()

    def export_to_excel(self, case_status_df: pd.DataFrame,
                       compressor_status_df: pd.DataFrame,
                       output_file: str = "graph_data.xlsx"):
        """
        Export both datasets to Excel with separate sheets.
        """
        with pd.ExcelWriter(output_file) as writer:
            case_status_df.to_excel(writer, sheet_name="Case Status", index=False)
            compressor_status_df.to_excel(writer, sheet_name="Compressor Status", index=False)

        print(f"\nData exported to: {output_file}")


def main():
    """
    Example usage - customize with your values.
    """

    # TODO: Get this from browser localStorage
    TOKEN = "YOUR_TOKEN_HERE"
    MODULE_ID = 7594  # Example: "Left" module from MY25L086318

    if TOKEN == "YOUR_TOKEN_HERE":
        print("ERROR: Set TOKEN to your auth token from localStorage")
        print("\nTo get your token:")
        print("1. Open browser DevTools (F12)")
        print("2. Go to Console tab")
        print("3. Run: console.log(localStorage.getItem('TOKEN'))")
        print("4. Copy the token value here")
        return

    # Initialize extractor
    extractor = TelemetryExtractor(token=TOKEN)

    print(f"\nExtracting data for Module ID: {MODULE_ID}")
    print("=" * 60)

    try:
        # Extract both graph datasets
        print("\n1. Extracting Case Status data...")
        case_status_df = extractor.extract_case_status_data(MODULE_ID, hours=24)
        print(f"   Got {len(case_status_df)} data points")
        print(f"   Columns: {list(case_status_df.columns)}")
        print(case_status_df.head())

        print("\n2. Extracting Compressor Status data...")
        compressor_status_df = extractor.extract_compressor_status_data(MODULE_ID, hours=24)
        print(f"   Got {len(compressor_status_df)} data points")
        print(f"   Columns: {list(compressor_status_df.columns)}")
        print(compressor_status_df.head())

        # Export to Excel
        print("\n3. Exporting to Excel...")
        extractor.export_to_excel(case_status_df, compressor_status_df)

        print("\n" + "=" * 60)
        print("SUCCESS: Data extraction complete!")

    except requests.exceptions.RequestException as e:
        print(f"\nERROR: API request failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response: {e.response.text}")
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
