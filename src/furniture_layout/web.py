from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .optimizer import NoValidLayoutError
from .service import generate_layouts


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WEB_ROOT = PROJECT_ROOT / "web"
SAMPLE_FILE = PROJECT_ROOT / "examples" / "living_room.json"
PRESETS_FILE = PROJECT_ROOT / "examples" / "room_presets.json"


class DemoHandler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/sample":
            self._send(200, SAMPLE_FILE.read_bytes(), "application/json; charset=utf-8")
            return
        if path == "/api/presets":
            self._send(200, PRESETS_FILE.read_bytes(), "application/json; charset=utf-8")
            return
        if path in ("/", "/index.html"):
            self._send(200, (WEB_ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/layouts":
            self._json(404, {"error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 1_000_000:
                raise ValueError("Request body must be between 1 byte and 1 MB")
            payload = json.loads(self.rfile.read(length))
            self._json(200, generate_layouts(payload))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError, NoValidLayoutError) as error:
            self._json(400, {"error": str(error)})

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Sparkshift layout module test UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), DemoHandler)
    print(f"Sparkshift test UI: http://{args.host}:{args.port}", flush=True)
    print("Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
