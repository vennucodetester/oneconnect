# OneConnect Graph Data Downloader

A standalone Python script to download Case Status and Compressor Status graph data from StoreConnect Pulse.

**No Claude dependency - completely standalone!**

## Quick Start

### 1. Install Python packages (one time)
```bash
pip install pandas openpyxl requests
```

### 2. Run the script
```bash
python download_graph_data.py
```

### 3. First time only: Enter your auth token
- The script will ask you to enter your auth token
- To get it:
  1. Go to https://mc.us.oneconnect.net
  2. Press F12 (open DevTools)
  3. Click Console tab
  4. Run: `console.log(localStorage.getItem('TOKEN'))`
  5. Copy the token and paste it into the script

Token is saved to `token.txt` - you won't need to do this again!

---

## How to use it

### Edit the script to add your case IDs:

Open `download_graph_data.py` and find this section (around line 300):

```python
def main():
    """Main entry point"""
    downloader = OneConnectDownloader()

    # List of case IDs to download
    case_ids = [
        "MY20D029022",        # Example case
        # "MY25L086318",      # Add more cases here
        # "MY26B013808",      # Just add to the list
    ]

    # Download last 24 hours of data
    downloader.run(case_ids, hours=24)
```

Replace the case IDs with your own:
```python
case_ids = [
    "MY20D029022",
    "MY25L086318",
    "MY26B013808",
    # ... add as many as you want
]
```

### Run it:
```bash
python download_graph_data.py
```

---

## What it does

For each case ID:
1. ✓ Finds all modules (units) on that case (usually 1-2 modules)
2. ✓ Downloads **Case Status** graph data:
   - Control Temperature
   - Alarm Status
   - Defrost Terminate
   - Control Status
3. ✓ Downloads **Compressor Status** graph data:
   - Refrigeration DO
   - Compressor Discharge Temperature
4. ✓ Saves everything to Excel with timestamp

---

## Output

Files are saved to: `C:\Users\silam\OneC\downloads\`

Example: `MY20D029022_20260525_143022.xlsx`

Each Excel file has multiple sheets (one per module, one per graph type)

---

## Need more hours of data?

Change this line:
```python
downloader.run(case_ids, hours=24)  # Change 24 to 48, 72, etc.
```

---

## Troubleshooting

**"Token is invalid"**
- Make sure you copied the ENTIRE token from the console
- It starts with `eyJ...` and is very long

**"Asset not found"**
- Double-check the case ID spelling
- Must be exactly as it appears in the portal (e.g., MY20D029022)

**"No data found"**
- The case might not have recent data
- Check in the portal if the case has active data

---

## That's it!

Just add case IDs and run. Everything else is automatic.
