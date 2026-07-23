"""Stub Ollama liveness endpoint for the portable-folder smoke test (issue #197, ADR-0041).

`--smoke-launch-full`'s AC is that the portable folder reaches the proxy's "protected"
state. `/v1/status` only reports "protected" when every dependency probe is healthy
(status.py's `compute_state`), and the l3 probe (`ping_ollama`, `GET {base_url}/api/tags`)
reports unhealthy whenever no real Ollama answers -- true by default on a hosted CI
runner with no Ollama installed. This script stands in for that one network call at its
boundary (the same seam-stub discipline the leak-audit tests use for L3/Transit/upstream),
so the smoke test can prove the tray + proxy wiring reaches Protected without requiring a
real local-LLM backend in CI. It answers any GET with a bare 200 -- `ping_ollama` only
checks the status code, never the response body.
"""

import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    port = int(sys.argv[1])
    HTTPServer(("127.0.0.1", port), _Handler).serve_forever()
