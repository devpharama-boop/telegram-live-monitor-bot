"""Telegram Live Monitor - Health server + bot"""

import os, sys, logging, threading, subprocess, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
Path("data").mkdir(exist_ok=True)
Path("logs").mkdir(exist_ok=True)
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)
PORT = int(os.environ.get('PORT', 8080))

def health():
    from http.server import HTTPServer, BaseHTTPRequestHandler
    class H(BaseHTTPRequestHandler):
        def do_GET(s): s.send_response(200); s.end_headers(); s.wfile.write(b"ok")
        def log_message(*a): pass
    log.info(f"Health :{PORT}")
    HTTPServer(('', PORT), H).serve_forever()

if __name__ == "__main__":
    threading.Thread(target=health, daemon=True).start()
    time.sleep(1)
    log.info("Launching bot...")
    subprocess.run([sys.executable, str(Path(__file__).parent / "run_bot.py")])
