#!/usr/bin/env python3
"""
Discover available telemetry attributes for a module.
This helps identify the exact attribute names to use in queries.
"""

import json
import requests
from typing import List, Dict

def discover_attributes(token: str, module_id: int, project_id: int = 136):
    """
    Query available attributes for a module.
    """

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # Get latest values - returns all available attributes
    url = f"https://api.us.oneconnect.net/oneconnect-api/projects/{project_id}/telemetry/v1/telemetry-attributes:latest"

    payload = {
        "attributes": [
            {
                "names": ["*"],  # Wildcard to get all attributes
                "filters": [
                    {"key": "module", "values": [str(module_id)]}
                ]
            }
        ]
    }

    print(f"\nDiscovering attributes for Module ID: {module_id}")
    print("=" * 70)

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()

        data = response.json()

        # Extract attribute names
        attributes = data.get("attributes", [])

        if not attributes:
            print("No attributes found. Check your module_id.")
            return

        print(f"\nFound {len(attributes)} attributes:\n")

        attribute_names = []
        for attr in attributes:
            name = attr.get("name", "")
            value = attr.get("value", "N/A")
            attribute_names.append(name)
            print(f"  • {name}")
            if value != "N/A":
                print(f"    Last value: {value}")

        print("\n" + "=" * 70)
        print("Use these attribute names in extract_graph_data.py:")
        print(f"\nattribute_names = [")
        for name in attribute_names:
            print(f'    "{name}",')
        print("]")

        # Save to file
        with open("C:\\Users\\silam\\OneC\\available_attributes.txt", "w") as f:
            f.write(f"Module ID: {module_id}\n")
            f.write(f"Available Attributes:\n\n")
            for name in attribute_names:
                f.write(f"{name}\n")

        print(f"\nAttributes saved to: available_attributes.txt")
        return attribute_names

    except requests.exceptions.RequestException as e:
        print(f"ERROR: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Response: {e.response.text}")
        return None


def main():
    """Main function."""

    print("""
DISCOVER MODULE ATTRIBUTES
===========================

This script finds all available telemetry attributes for your module.
""")

    # Get inputs
    token = input("Paste your TOKEN: ").strip()
    module_id = input("Enter your MODULE_ID (e.g., 7594): ").strip()

    if not token or not module_id:
        print("ERROR: Token and module_id are required!")
        return

    try:
        module_id = int(module_id)
    except ValueError:
        print("ERROR: Module ID must be a number!")
        return

    # Discover
    attributes = discover_attributes(token, module_id)

    if attributes:
        print("\nNext: Copy the attribute names you want to query")
        print("and update extract_graph_data.py accordingly.")


if __name__ == "__main__":
    main()
