# AI help: written with the help of Claude Code (Anthropic), an AI coding assistant. See README.
"""Serve the web pages (dashboard, designer, breadboard sim) without browser caching.

    python3 dashboard/serve.py          # http://localhost:8250

Like `python3 -m http.server`, but every response says "do not cache", so a new tab always gets
the current version of a page instead of one the browser saved earlier.
"""

import functools
import http.server
import sys
from pathlib import Path


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8250
    handler = functools.partial(NoCacheHandler, directory=str(Path(__file__).parent))
    print(f"Serving the Bench Check pages on http://localhost:{port}")
    http.server.ThreadingHTTPServer(("", port), handler).serve_forever()


if __name__ == "__main__":
    main()
