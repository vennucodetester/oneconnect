import plotly.graph_objects as go
import pandas as pd
from datetime import datetime

fig = go.Figure()
df = pd.DataFrame({
    't': [pd.Timestamp('2026-05-25 08:00:00'), pd.Timestamp('2026-05-25 10:00:00')],
    'y': [1, 2]
})
fig.add_trace(go.Scatter(x=df['t'], y=df['y']))

# Add vrect with Timestamp
fig.add_vrect(x0=pd.Timestamp('2026-05-25 08:30:00'), x1=pd.Timestamp('2026-05-25 09:30:00'), fillcolor="red")

# Save and see if the JSON contains valid x0/x1
print(fig.to_json()[:500])
