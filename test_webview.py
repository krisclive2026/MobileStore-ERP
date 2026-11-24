
import webview

webview.create_window(
    "WebView Test",
    html="<h1>WebView is working</h1><p>Testing the Windows renderer.</p>",
    width=800,
    height=600,
)

webview.start(gui="edgechromium")
