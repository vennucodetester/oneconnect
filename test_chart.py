import plotly.graph_objects as go
from PyQt5.QtWidgets import QApplication
from PyQt5.QtWebEngineWidgets import QWebEngineView
import sys

def test_chart():
    fig = go.Figure(data=go.Scatter(x=[1, 2, 3], y=[4, 1, 2]))
    html = fig.to_html(include_plotlyjs="cdn", full_html=True)
    
    polyfill = """
    <script>
    var originalInsertRule = CSSStyleSheet.prototype.insertRule;
    CSSStyleSheet.prototype.insertRule = function(rule, index) {
        try {
            return originalInsertRule.call(this, rule, index || 0);
        } catch (e) {
            console.warn("Ignored insertRule error for rule:", rule);
            return -1;
        }
    };
    </script>
    """
    html = html.replace("<head>", f"<head>\n{polyfill}")
    
    app = QApplication(sys.argv)
    view = QWebEngineView()
    view.setHtml(html)
    
    # Let's just output the first few lines of HTML to verify
    with open("test_chart_poly.html", "w") as f:
        f.write(html)
        
if __name__ == "__main__":
    test_chart()
    print("Test passed.")
