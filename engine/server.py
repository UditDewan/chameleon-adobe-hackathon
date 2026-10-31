"""HTTP front for the engine: what the Express add-on (and the in-browser viewer) talk to.

    python -m engine.server [--host 127.0.0.1] [--port 8787]

    GET  /health                    liveness
    GET  /formats                   catalogue of real-world canvas sizes
    GET  /samples, /samples/<name>  bundled designs (input/ and tests/designs/)
    POST /resize   {"source": design, "targets": [{"width", "height", "name"?}], "render"?: true}
                   -> {"results": [{"target", "design", "notes", "ms", "png"?}]}
    POST /render   {"design": design} -> {"png"}   (the official renderer)
    GET  /viewer/                   the add-on UI, usable in any browser

Stdlib only, CORS open (the add-on runs on another origin).
"""

import argparse
import base64
import json
import os
import tempfile
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from engine.resize import ROOT, resize

ASSETS = ROOT / "brand_kit" / "assets"
VIEWER = ROOT / "addon" / "src"
SAMPLE_DIRS = (ROOT / "input", ROOT / "tests" / "designs")
TYPES = {".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
         ".png": "image/png", ".svg": "image/svg+xml"}  # explicit: Windows' registry can map .js to text/plain
FORMATS = [  # the kind of catalogue the scorer draws from: named platform formats
    {"group": "Social", "name": "Instagram Post", "width": 1080, "height": 1080},
    {"group": "Social", "name": "Instagram Portrait", "width": 1080, "height": 1350},
    {"group": "Social", "name": "Story / Reel", "width": 1080, "height": 1920},
    {"group": "Social", "name": "Facebook Post", "width": 1200, "height": 630},
    {"group": "Social", "name": "Facebook Cover", "width": 820, "height": 312},
    {"group": "Social", "name": "X Post", "width": 1600, "height": 900},
    {"group": "Social", "name": "X Header", "width": 1500, "height": 500},
    {"group": "Social", "name": "LinkedIn Banner", "width": 1584, "height": 396},
    {"group": "Social", "name": "Pinterest Pin", "width": 1000, "height": 1500},
    {"group": "Social", "name": "YouTube Thumbnail", "width": 1280, "height": 720},
    {"group": "Ads", "name": "Leaderboard", "width": 728, "height": 90},
    {"group": "Ads", "name": "Billboard", "width": 970, "height": 250},
    {"group": "Ads", "name": "Medium Rectangle", "width": 300, "height": 250},
    {"group": "Ads", "name": "Wide Skyscraper", "width": 160, "height": 600},
    {"group": "Ads", "name": "Half Page", "width": 300, "height": 600},
    {"group": "Ads", "name": "Mobile Banner", "width": 320, "height": 50},
    {"group": "Ads", "name": "Large Mobile Banner", "width": 320, "height": 100},
    {"group": "Web & print", "name": "Web Hero", "width": 1600, "height": 400},
    {"group": "Web & print", "name": "Email Header", "width": 600, "height": 200},
    {"group": "Web & print", "name": "Presentation 16:9", "width": 1920, "height": 1080},
    {"group": "Web & print", "name": "A4 Poster", "width": 2480, "height": 3508},
]


def render_png(design) -> str:
    """Data URL of the design rendered by the official renderer."""
    from renderer.converter.convert import render_design_to_png
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        render_design_to_png(design, ASSETS, path)
        return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()
    finally:
        os.unlink(path)


def samples():
    return {p.stem: p for d in SAMPLE_DIRS for p in sorted(d.glob("*.json"))}


def _design(obj):
    """A design, or a task file wrapping one under "source"."""
    d = obj.get("source", obj) if isinstance(obj, dict) else None
    if not (isinstance(d, dict) and isinstance(d.get("canvas"), dict) and isinstance(d.get("elements"), list)):
        raise ValueError("expected a design: {\"canvas\": {...}, \"elements\": [...]}")
    return d


def _size(t):
    w, h = int(t["width"]), int(t["height"])
    if not (1 <= w <= 10000 and 1 <= h <= 10000):
        raise ValueError(f"canvas size out of range: {w}x{h}")
    return {"width": w, "height": h}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        for k, v in (("Content-Type", ctype), ("Content-Length", str(len(data))),
                     ("Access-Control-Allow-Origin", "*"), ("Access-Control-Allow-Headers", "Content-Type"),
                     ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
                     ("Access-Control-Allow-Private-Network", "true")):  # Chrome private-network preflight
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self._send(204, b"")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/health":
            return self._send(200, {"ok": True})
        if path == "/formats":
            return self._send(200, FORMATS)
        if path == "/samples":
            return self._send(200, list(samples()))  # official samples first, then the stress designs
        if path.startswith("/samples/"):
            p = samples().get(path[len("/samples/"):])
            return self._send(200, json.loads(p.read_text())) if p else self._send(404, {"error": "no such sample"})
        if path in ("/", "/viewer"):
            self.send_response(302)
            self.send_header("Location", "/viewer/")
            self.end_headers()
            return
        if path.startswith("/viewer/"):
            f = (VIEWER / (path[len("/viewer/"):] or "index.html")).resolve()
            if VIEWER.resolve() in f.parents and f.is_file():  # no escaping the viewer folder
                return self._send(200, f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))
        self._send(404, {"error": "not found"})

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            path = self.path.split("?")[0]
            if path == "/render":
                return self._send(200, {"png": render_png(_design(body.get("design", body)))})
            if path == "/resize":
                source = _design(body)
                results = []
                for t in body["targets"]:
                    canvas = _size(t)
                    t0, notes = time.perf_counter(), {}
                    design = resize(source, canvas, notes)
                    ms = round((time.perf_counter() - t0) * 1000, 1)
                    item = {"target": {**t, **canvas}, "design": design, "notes": notes, "ms": ms}
                    if body.get("render", True):
                        item["png"] = render_png(design)
                    results.append(item)
                return self._send(200, {"results": results})
            self._send(404, {"error": "not found"})
        except (ValueError, KeyError, TypeError) as exc:  # bad input is the caller's to fix
            self._send(400, {"error": str(exc)})


def main():
    ap = argparse.ArgumentParser(description="CHAMELEON engine over HTTP")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    args = ap.parse_args()
    # ponytail: single-threaded, one request at a time; ThreadingHTTPServer + a lock if it ever serves a crowd
    server = HTTPServer((args.host, args.port), Handler)
    print(f"CHAMELEON engine on http://{args.host}:{args.port}  (viewer: /viewer/)")
    server.serve_forever()


if __name__ == "__main__":
    main()
