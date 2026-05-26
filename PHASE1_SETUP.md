# Phase 1: Data Download App - Setup Instructions

## What This Does

A desktop application where you can:
1. Enter multiple case IDs (1-500)
2. Click Download
3. App automatically gets modules and downloads all graph data
4. Shows progress as it works
5. Saves Excel files when done

That's it for Phase 1. Simple and clean.

---

## Installation

### Step 1: Install Python Packages
```bash
pip install PyQt5 pandas openpyxl requests
```

### Step 2: Run the App
```bash
python app.py
```

---

## First Time Using the App

1. **App opens** → Asks for auth token
2. **Get your token:**
   - Go to https://mc.us.oneconnect.net
   - Press F12 (DevTools)
   - Click Console tab
   - Paste: `console.log(localStorage.getItem('TOKEN'))`
   - Copy the token (long string starting with `eyJ...`)
3. **Paste it into the app**
4. Token is saved locally - won't ask again!

---

## How to Use

1. **Enter case IDs:**
   - Paste in the text box (one per line OR comma-separated)
   - Click "Add Case IDs"

2. **Review the list:**
   - Case IDs appear in the table
   - Click "Remove" to delete any

3. **Download:**
   - Click "Download Data" button
   - Watch progress as it processes each case

4. **Files saved:**
   - Location: `C:\Users\silam\OneC\downloads\`
   - Files: `MY20D029022_20260525_143022.xlsx`, etc.
   - Each file has sheets for each module's data

---

## Features in This Version

✓ Enter 1-500 case IDs  
✓ Table view of cases  
✓ Remove individual cases  
✓ Real-time progress (X of Y cases)  
✓ Current step display  
✓ Status messages  
✓ Auto-save token  
✓ Error handling  

---

## Next Phase (When You're Ready)

Once you test Phase 1 and approve it, we'll add:
- **Case selector**: Pick a case from downloaded data
- **Diagnostics tab**: Analyze the data
- etc.

One feature at a time!

---

## Troubleshooting

**"Token is invalid"**
- Make sure you copied the ENTIRE token
- It's very long (200+ characters)

**"No modules found"**
- Check the case ID spelling
- Must match exactly as shown in portal

**App crashes**
- Check Python packages are installed: `pip install PyQt5 pandas openpyxl requests`

**Permission denied saving files**
- Make sure `C:\Users\silam\OneC\downloads\` folder exists
- Or create it manually first

---

## Test It Out!

Try downloading MY20D029022 (the example case) to make sure everything works!
