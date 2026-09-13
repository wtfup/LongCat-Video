#!/usr/bin/env python3
"""Serve the live swarm dashboard on a local/LAN/Tailscale URL.

Reads /Users/vishalnigammacminioffice/wtf-avatar-factory/army_dashboard.html
FRESH on every request (the generator refreshes it every 10s; the page also
meta-refreshes itself). Only this one file is exposed.

Usage: python3 serve_dashboard.py [port]   (default 8788)
"""
import http.server
import pathlib
import socketserver
import sys

ROOT = pathlib.Path("/Users/vishalnigammacminioffice/wtf-avatar-factory")
PAGE = ROOT / "army_dashboard.html"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8788


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path in ("/", "/index.html", "/army_dashboard.html"):
            try:
                body = PAGE.read_bytes()
            except FileNotFoundError:
                body = b"<html><body><h2>dashboard not generated yet - wait 10s and refresh</h2></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")

    def log_message(self, *args):  # quiet
        pass


socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
    print(f"serving dashboard on http://0.0.0.0:{PORT}", flush=True)
    httpd.serve_forever()
