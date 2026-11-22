"""The showcase worker: answers ``GET /ping`` with ``{"message": ...}`` on ``$PORT`` (default 8092)."""

# --- reference: worker ---
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — the http.server name
        body = json.dumps({"message": "The showcase worker is up"}).encode()
        self.send_response(200 if self.path == "/ping" else 404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8092"))), Handler).serve_forever()
# --- end ---
