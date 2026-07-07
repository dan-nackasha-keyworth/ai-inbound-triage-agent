"""
A restricted static file server for previewing progress.html and the
outputs/ dashboard files during this build - deliberately narrower than
`python -m http.server`, which would happily serve (and list) .env,
.git, and __pycache__ from the project root.

Blocks serving and directory-listing of anything under .env, .git, or
__pycache__. Everything else in the project folder is served normally.
"""

import http.server
import os
import sys

PORT = 8756
ROOT = os.path.dirname(os.path.abspath(__file__))

BLOCKED_NAMES = {".env", ".git", "__pycache__"}


def is_blocked(url_path):
    path = url_path.split("?")[0].lstrip("/")
    parts = [p for p in path.split("/") if p]
    return any(p in BLOCKED_NAMES or p.startswith(".env.") for p in parts)


class RestrictedHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def do_GET(self):
        if is_blocked(self.path):
            self.send_error(403, "Forbidden")
            return
        super().do_GET()

    def do_HEAD(self):
        if is_blocked(self.path):
            self.send_error(403, "Forbidden")
            return
        super().do_HEAD()

    def list_directory(self, path):
        # Filter blocked names out of directory listings entirely so
        # they don't even show up as (403'd) links.
        try:
            entries = os.listdir(path)
        except OSError:
            self.send_error(404, "No permission to list directory")
            return None
        filtered = [e for e in entries if e not in BLOCKED_NAMES]
        original_listdir = os.listdir
        os.listdir = lambda p: filtered
        try:
            return super().list_directory(path)
        finally:
            os.listdir = original_listdir


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    # ThreadingHTTPServer, not the plain single-threaded HTTPServer - a
    # single slow/kept-alive connection would otherwise block every
    # other request (including the progress.html polling loop).
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), RestrictedHandler) as httpd:
        print(f"Serving {ROOT} on http://127.0.0.1:{port} (.env / .git / __pycache__ blocked)")
        httpd.serve_forever()
