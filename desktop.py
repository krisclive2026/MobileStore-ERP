
import threading
import time
import urllib.request
import webview
import sys

from app import app


HOST = "127.0.0.1"
PORT = 5001
URL = f"http://{HOST}:{PORT}"


def run_flask():
    app.run(
        host=HOST,
        port=PORT,
        debug=False,
        use_reloader=False,
        threaded=True
    )


def wait_for_server():
    for _ in range(60):
        try:
            urllib.request.urlopen(URL, timeout=2)
            return True
        except Exception:
            time.sleep(1)

    return False


if __name__ == "__main__":

    flask_thread = threading.Thread(
        target=run_flask,
        daemon=True
    )
    flask_thread.start()

    if not wait_for_server():
        print("Mobile Store ERP server failed to start.")
        sys.exit(1)

    window = webview.create_window(
        "Mobile Store ERP",
        URL,
        width=1400,
        height=900,
        min_size=(1100, 700),
        resizable=True
    )

    webview.start(gui="edgechromium")
