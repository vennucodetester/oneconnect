import json
import re
html = open('test_chart.html', encoding='utf-8').read()
match = re.search(r'"shapes":\s*(\[.*?\])', html, re.DOTALL)
if match:
    shapes = json.loads(match.group(1))
    for i, s in enumerate(shapes):
        print(f"Shape {i}: xref={s.get('xref')} yref={s.get('yref')} type={s.get('type')}")
else:
    print("No shapes found")
