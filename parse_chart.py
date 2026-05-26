import json
from bs4 import BeautifulSoup
import sys

html = open('test_chart.html', encoding='utf-8').read()
# Plotly outputs script tags like <script type="text/javascript">window.PLOTLYENV=...Plotly.newPlot("...", [...], {...}, {"responsive": true})
import re
match = re.search(r'Plotly\.newPlot\([^,]*, (.*?), (.*?), \{', html)
if match:
    layout = json.loads(match.group(2))
    print("SHAPES:")
    for s in layout.get("shapes", []):
        print(s)
else:
    # Just grab anything inside {"shapes": ...}
    match2 = re.search(r'("shapes":\s*\[.*?\])', html, re.DOTALL)
    if match2:
        print(match2.group(1))
    else:
        print("No shapes found")
