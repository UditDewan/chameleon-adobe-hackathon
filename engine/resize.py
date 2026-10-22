"""
CHAMELEON resize engine: move the furniture, don't stretch the photo.

  analyse  split the source into backgrounds, decorations and content units
           (explicit groups, text sitting on shapes, panels, rotated stickers,
           corner pins, missing assets) and infer importance without labels
  order    an XY-cut recovers reading order and side-by-side rows
  layout   stack (portrait/square), split (landscape: hero beside a text
           column) or row (banners). Each searches the largest scale that
           fits, measuring text with the renderer's own fonts so every wrap
           matches the rendered PNG
  degrade  while some text would be illegible, the least important unit
           drops out (opacity 0) - only when that actually buys legibility
  finish   backgrounds re-crop full-bleed, decorations keep their relative
           anchor, images scale uniformly, everything ends inside the canvas

Deterministic, offline, no model. See README.md for the full write-up.
"""

from __future__ import annotations

import copy
import math
import re
import sys
import traceback
from collections import namedtuple
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = ROOT / "brand_kit" / "assets"
FONTS_DIR = ROOT / "brand_kit" / "fonts"

_WEIGHTS = {"thin", "light", "extralight", "regular", "medium", "semibold", "bold", "extrabold", "black"}
_BOLD = {"bold", "semibold", "extrabold", "black"}
_RGB = re.compile(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)")


def resize(source: dict[str, Any], target_canvas: dict[str, Any]) -> dict[str, Any]:
    canvas = {"width": int(target_canvas["width"]), "height": int(target_canvas["height"])}
    try:
        elements = _Engine(source, canvas).run()
    except Exception:  # never crash a batch: a letterbox is valid output, just less designed
        traceback.print_exc()
        elements = _letterbox(source, canvas)
    return {"canvas": canvas, "elements": elements}


# --------------------------------------------------------------------------- geometry
def _box(e):
    return (e["x"], e["y"], e["x"] + e["width"], e["y"] + e["height"])


def _aabb(e):
    """Bounds after rotating about the element centre (how the renderer draws it)."""
    x0, y0, x1, y1 = _box(e)
    a = math.radians(e.get("rotation") or 0)
    if not a:
        return x0, y0, x1, y1
    w, h = x1 - x0, y1 - y0
    hw = (abs(w * math.cos(a)) + abs(h * math.sin(a))) / 2
    hh = (abs(w * math.sin(a)) + abs(h * math.cos(a))) / 2
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    return cx - hw, cy - hh, cx + hw, cy + hh


def _area(b):
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter(a, b):
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def _union(boxes):
    boxes = list(boxes)
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _alpha(e):
    """Effective opacity: element opacity x the alpha inside its fill colour."""
    a = float(e.get("opacity", 1.0) if e.get("opacity") is not None else 1.0)
    m = re.match(r"rgba\([^)]*,\s*([\d.]+)\s*\)", str(e.get("fill_color") or ""))
    return a * (float(m.group(1)) if m else 1.0)


def _transparent(color):
    m = _RGB.match(str(color or "").strip())
    if m:
        return f"rgba({m[1]},{m[2]},{m[3]},0)"
    h = str(color or "").lstrip("#")
    if len(h) == 6:
        return f"rgba({int(h[:2], 16)},{int(h[2:4], 16)},{int(h[4:], 16)},0)"
    return "rgba(0,0,0,0)"


# --------------------------------------------------------------------------- assets
@lru_cache(maxsize=None)
def _asset(asset_id):
    """(w, h, alpha-bbox as fractions) of an asset, or None when it is missing."""
    path = ASSETS_DIR / str(asset_id or "")
    if not asset_id or not path.is_file():
        return None
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
            bb = im.convert("RGBA").getchannel("A").point(lambda v: 255 if v > 12 else 0).getbbox()
    except Exception:
        return 1, 1, (0.0, 0.0, 1.0, 1.0)
    bb = bb or (0, 0, w, h)
    return w, h, (bb[0] / w, bb[1] / h, bb[2] / w, bb[3] / h)


def _image_ink(e):
    """Where the visible pixels of an image element land (transparent padding excluded)."""
    info = _asset(e.get("asset_id"))
    fit = e.get("fit", "contain")
    if info is None or e.get("rotation") or fit == "cover":
        return _aabb(e)
    aw, ah, (fx0, fy0, fx1, fy1) = info
    x0, y0, x1, y1 = _box(e)
    w, h = x1 - x0, y1 - y0
    if fit == "contain":
        sc = min(w / aw, h / ah)
        cw, ch = aw * sc, ah * sc
        x0, y0 = x0 + (w - cw) / 2, y0 + (h - ch) / 2
    else:
        cw, ch = w, h
    return x0 + fx0 * cw, y0 + fy0 * ch, x0 + fx1 * cw, y0 + fy1 * ch


# --------------------------------------------------------------------------- text
_Lay = namedtuple("_Lay", "n w top bot box_h space over")  # ink box is (0..w, top..bot) from box top
_RowM = namedtuple("_RowM", "lines h gx gy anchor tp bp")  # a measured row; tp/bp = padding at its edges


def _wrap(text, widths, max_width):
    """The renderer's greedy wrap (renderer/converter/wrap.py), verbatim behaviour."""
    if not text or max_width <= 0:
        return text
    chars = list(text)
    line_width, last_space = 0.0, None
    for i, c in enumerate(chars):
        if c == "\n":
            line_width, last_space = 0.0, None
            continue
        w = widths[i] if i < len(widths) else 0.0
        if line_width + w > max_width and last_space is not None:
            chars[last_space] = "\n"
            line_width = sum(widths[j] for j in range(last_space + 1, i + 1) if chars[j] != "\n")
            last_space = None
        else:
            line_width += w
        if c == " ":
            last_space = i
    return "".join(chars)


class _Text:
    """Measures text exactly like the renderer does (skia + brand fonts)."""

    def __init__(self):
        self.fm = None
        try:
            import skia
            if str(ROOT) not in sys.path:
                sys.path.insert(0, str(ROOT))
            from renderer.converter.brand_fonts import load_brand_font_manager
            self.fm, self.skia = load_brand_font_manager(str(FONTS_DIR)), skia
        except Exception:  # ponytail: no renderer -> glyph-width estimates, output stays valid
            self.fm = None
        self.faces, self.cache, self.glyphs = {}, {}, {}

    def _font(self, e, size):
        weight = str(e.get("font_weight") or "regular").lower()
        weight = weight if weight in _WEIGHTS else "regular"
        it = e.get("font_style") or e.get("italic")
        italic = it if isinstance(it, bool) else it == "italic"
        bold = weight in _BOLD
        style = "bolditalic" if bold and italic else "bold" if bold else "italic" if italic else "regular"
        key = (e.get("font_family", "Montserrat"), weight, style)
        if key not in self.faces:
            self.faces[key] = self.skia.Typeface.MakeFromData(self.fm.lookup(*key))
        return self.skia.Font(self.faces[key], size)

    def layout(self, e, fs, width):
        text = str(e.get("content", ""))
        lh = float(e.get("line_height") or 1.2)
        ls = float(e.get("letter_spacing") or 0.0)
        # exact width in the key: balanced wraps sit right on a line-break boundary
        key = (text, e.get("font_family"), e.get("font_weight"), e.get("font_style"), e.get("italic"), fs, width, lh, ls)
        hit = self.cache.get(key)
        if hit:
            return hit
        if len(self.cache) > 200_000:  # ponytail: bounded by clearing, an LRU if batches get huge
            self.cache.clear()
            self.glyphs.clear()
        widths, tops, bots, asc, space = self._glyphs(e, text, fs)
        wrapped = _wrap(text, widths, width)
        lines, start = [], 0
        for i, c in enumerate(wrapped + "\n"):
            if c == "\n":
                lines.append(range(start, i))
                start = i + 1
        lws, top, bot = [], math.inf, -math.inf
        for li, idx in enumerate(lines):
            lws.append(sum(widths[j] for j in idx) + ls * max(0, len(idx) - 1))
            base = li * fs * lh + asc
            for j in idx:
                if not wrapped[j].isspace():
                    top, bot = min(top, base + tops[j]), max(bot, base + bots[j])
        if top > bot:
            top = bot = 0.0
        w = max(lws)
        lay = _Lay(len(lines), w, top, bot, max(bot, len(lines) * fs * lh), space, w > width + 0.01)
        self.cache[key] = lay
        return lay

    def _glyphs(self, e, text, fs):
        """Per-character advance and ink extents; the expensive part, shared by every wrap width."""
        key = (text, e.get("font_family"), e.get("font_weight"), e.get("font_style"), e.get("italic"), fs)
        hit = self.glyphs.get(key)
        if hit:
            return hit
        n = len(text)
        if self.fm:
            font = self._font(e, fs)
            glyphs = font.textToGlyphs(text)
            widths = list(font.getWidths(glyphs))
            rects = font.getBounds(glyphs)
            tops, bots = [r.top() for r in rects], [r.bottom() for r in rects]
            asc, space = -font.getMetrics().fAscent, font.measureText(" ")
            if len(widths) != n:  # exotic code points; keep indices aligned with the string
                widths, tops, bots = [(v + [0.0] * n)[:n] for v in (widths, tops, bots)]
        else:
            k = 0.6 if str(e.get("font_weight")).lower() in _BOLD else 0.55
            widths = [fs * (0.28 if c == " " else k) for c in text]
            tops, bots, asc, space = [-0.72 * fs] * n, [0.22 * fs] * n, 1.0 * fs, 0.28 * fs
        self.glyphs[key] = res = (widths, tops, bots, asc, space)
        return res

    def fit(self, e, fs, width, max_lines=None):
        """Largest size <= fs whose words all fit `width` (and max_lines, if set)."""
        fs = max(1, int(fs))
        while True:
            lay = self.layout(e, fs, width)
            if fs <= 1 or (not lay.over and (not max_lines or lay.n <= max_lines)):
                return fs, lay
            fs = max(1, min(fs - 1, int(fs * 0.93)))

    def balance(self, e, fs, width, lay):
        """Narrowest width keeping the same line count: even lines, no one-word widows."""
        if lay.n < 2 or lay.over:
            return width, lay
        lo, hi = 0.3 * lay.w, width
        for _ in range(12):
            mid = (lo + hi) / 2
            t = self.layout(e, fs, mid)
            if t.n <= lay.n and not t.over:
                hi = mid
            else:
                lo = mid
        hi = min(width, hi + 0.5)  # half a pixel of slack off the break boundary
        return hi, self.layout(e, fs, hi)


_TEXT = None


def _text():
    global _TEXT
    if _TEXT is None:
        _TEXT = _Text()
    return _TEXT


# --------------------------------------------------------------------------- analysis
class _Unit:
    """Elements that travel together; `ink` is their visible bounds in the source."""

    def __init__(self, els, ink):
        self.els, self.ink = els, ink
        self.text = len(els) == 1 and els[0]["type"] == "text" and not els[0].get("rotation")
        self.has_text = any(e["type"] == "text" for e in els)
        self.has_img = any(e["type"] == "image" for e in els)
        self.rot = any(abs(e.get("rotation") or 0) > 0.5 for e in els)
        self.role, self.imp, self.pin, self.anchor, self.kids = "", 0.5, None, "c", []
        self.btn, self.src_n = None, 1  # btn: (shape, label, pad_x, pad_top, pad_bottom) of a one-label button
        # invisible margin between ink and element boxes (transparent asset padding); it must
        # still fit inside the canvas. Reflowed text gets a fresh, tight box instead.
        b = _union(_aabb(e) for e in els)
        self.pad = (0.0,) * 4 if self.text else (max(0.0, ink[0] - b[0]), max(0.0, ink[1] - b[1]),
                                                 max(0.0, b[2] - ink[2]), max(0.0, b[3] - ink[3]))

    @property
    def w(self):
        return max(self.ink[2] - self.ink[0], 1e-6)

    @property
    def h(self):
        return max(self.ink[3] - self.ink[1], 1e-6)

    def fs0(self):
        return min(e["font_size"] for e in self.els if e["type"] == "text")


class _Layout:
    def __init__(self, place, dropped):
        self.place, self.dropped = place, set(dropped)  # place: unit -> (x, y, w, h, payload)


def _split_axis(units, axis, tol):
    us = sorted(units, key=lambda u: u.ink[axis])
    parts, end = [[us[0]]], us[0].ink[axis + 2]
    for u in us[1:]:
        if u.ink[axis] >= end - tol:
            parts.append([u])
            end = u.ink[axis + 2]
        else:
            parts[-1].append(u)
            end = max(end, u.ink[axis + 2])
    return parts


def _xycut(units, tol, col, cols):
    """Reading order as rows; units side by side in the source share a row.
    cols[u] receives the column box u sits in, so alignment is judged within its column."""
    if len(units) <= 1:
        cols.update({u: col for u in units})
        return [list(units)] if units else []
    for axis in (1, 0):
        parts = _split_axis(units, axis, tol)
        if len(parts) > 1:
            if axis == 0 and all(len(p) == 1 for p in parts):
                cols.update({u: col for u in units})
                return [[p[0] for p in parts]]
            return [row for p in parts
                    for row in _xycut(p, tol, col if axis == 1 else _union(u.ink for u in p), cols)]
    cols.update({u: col for u in units})
    return [[u] for u in sorted(units, key=lambda u: (u.ink[1], u.ink[0]))]


def _anchor(ink, col):
    dl, dr = ink[0] - col[0], col[2] - ink[2]
    return "c" if abs(dl - dr) <= 0.1 * (col[2] - col[0]) else "l" if dl < dr else "r"


class _Engine:
    def __init__(self, src, canvas):
        self.src, self.T = src, _text()
        self.W0, self.H0 = float(src["canvas"]["width"]), float(src["canvas"]["height"])
        self.W, self.H = float(canvas["width"]), float(canvas["height"])
        self.s_fit = min(self.W / self.W0, self.H / self.H0)
        self.s_geo = math.sqrt(self.W * self.H / (self.W0 * self.H0))
        self.m = max(4.0, 0.055 * min(self.W, self.H))
        # smallest comfortable text size on this canvas (px)
        self.floor = min(16.0, max(9.0, 0.009 * max(self.W, self.H) + 0.012 * min(self.W, self.H)))
        self._capc = {}
        self._analyse()

    # ------------------------------------------------------------------ analyse
    def _ink(self, e):
        if e["type"] == "text" and not e.get("rotation"):
            lay = self.T.layout(e, max(1, int(e["font_size"])), e["width"])
            if lay.bot <= lay.top:
                return _box(e)
            al = e.get("text_align", "left")
            off = (e["width"] - lay.w) / 2 if al == "center" else e["width"] - lay.w if al == "right" else 0.0
            return e["x"] + off, e["y"] + lay.top, e["x"] + off + lay.w, e["y"] + lay.bot
        if e["type"] == "image":
            return _image_ink(e)
        return _aabb(e)

    def _analyse(self):
        W0, H0 = self.W0, self.H0
        cov, canvas = W0 * H0, (0.0, 0.0, W0, H0)
        self.bg, self.hidden, self.passive, rest = [], [], [], []
        for e in self.src["elements"]:
            if e["type"] == "image" and _asset(e.get("asset_id")) is None:
                self.hidden.append(e)  # missing asset: skip it rather than show a broken placeholder
            elif (e["width"] <= 0 or e["height"] <= 0 or (e["type"] == "text" and not str(e.get("content", "")).strip())
                  or (e["type"] != "text" and _alpha(e) <= 0.01)):
                self.passive.append(e)  # nothing visible to lay out: it just keeps its relative place
            elif _inter(_aabb(e), canvas) <= 0:
                self.hidden.append(e)  # entirely off-canvas in the source: it was never visible
            elif e["type"] != "text" and _inter(_aabb(e), canvas) >= 0.85 * cov:
                self.bg.append(e)
            else:
                rest.append(e)

        ink = [self._ink(e) for e in rest]
        parent = list(range(len(rest)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        first = {}
        for i, e in enumerate(rest):  # explicit groups travel together
            g = e.get("group_id")
            if g is not None:
                parent[find(i)] = find(first.setdefault(g, i))
        for i, a in enumerate(rest):  # ungrouped things sitting on an opaque shape/image travel with it
            ai = _area(ink[i])
            if a.get("group_id") is not None or ai <= 0:
                continue
            best = None
            for j, b in enumerate(rest):
                bb = _area(_box(b))
                if (j == i or b["type"] == "text" or _alpha(b) < 0.5 or bb > 0.35 * cov or bb <= ai * 1.05
                        or _inter(ink[i], ink[j]) < 0.85 * ai):
                    continue
                if best is None or bb < _area(_box(rest[best])):
                    best = j
            if best is not None:
                parent[find(i)] = find(best)

        comps = {}
        for i in range(len(rest)):
            comps.setdefault(find(i), []).append(i)
        units = [_Unit([rest[i] for i in idx], _union(ink[i] for i in idx)) for idx in comps.values()]

        content = [u for u in units if u.has_text or u.has_img]
        shapes_only = [u for u in units if not (u.has_text or u.has_img)]
        for u in shapes_only:  # dividers flow with the content
            if not u.rot and min(u.w, u.h) <= max(3.0, 0.012 * min(W0, H0)) and max(u.w, u.h) >= 6 * min(u.w, u.h):
                u.role, u.imp = "rule", 0.2
                content.append(u)

        # a sticker riding on a picture (price tag on a bottle) travels with it
        for s in sorted(content, key=lambda u: _area(u.ink)):
            if s not in content or s.role == "rule" or s.rot or all(e["type"] == "text" for e in s.els):
                continue
            zs = min(e.get("z_index", 0) for e in s.els)
            for b in content:
                if (b is s or not b.has_img or _area(s.ink) > 0.35 * _area(b.ink)
                        or zs <= max(e.get("z_index", 0) for e in b.els)):
                    continue
                gx, gy = 0.15 * b.w, 0.15 * b.h
                if _inter(s.ink, (b.ink[0] - gx, b.ink[1] - gy, b.ink[2] + gx, b.ink[3] + gy)) >= 0.25 * _area(s.ink):
                    content[content.index(b)] = _Unit(b.els + s.els, _union([b.ink, s.ink]))
                    content.remove(s)
                    break

        # a short caption right under a picture belongs to it (a product name under its bottle)
        fs_max = max([e["font_size"] for u in content for e in u.els if e["type"] == "text"] or [1.0])
        for t in [u for u in content if u.text and u.els[0]["font_size"] <= 0.6 * fs_max]:
            cx = (t.ink[0] + t.ink[2]) / 2
            for b in content:
                if b is t or not b.has_img or not b.ink[0] <= cx <= b.ink[2] or t.w > 1.6 * (b.w + b.pad[0] + b.pad[2]):
                    continue
                gap = t.ink[1] - b.ink[3]
                between = any(v not in (t, b) and b.ink[3] < v.ink[1] < t.ink[1] and _inter(
                    (b.ink[0], b.ink[3], b.ink[2], t.ink[1]), v.ink) > 0 for v in content)
                if 0 <= gap <= 0.5 * b.h and not between:
                    content[content.index(b)] = _Unit(b.els + t.els, _union([b.ink, t.ink]))
                    content.remove(t)
                    break

        self.decor, self.panels = [], []
        for u in shapes_only:
            if u.role == "rule":
                continue
            kids = [c for c in content if _inter(c.ink, u.ink) >= 0.85 * _area(c.ink)]
            if kids and all(e.get("shape_type", "rectangle") == "rectangle" for e in u.els):
                u.role, u.kids = "panel", kids  # a card behind content: re-wrapped around it later
                self.panels.append(u)
            else:
                u.role = "decor"
                self.decor.append(u)

        # importance, inferred from type / size / position (there are no semantic labels)
        for u in content:
            if u.role == "rule":
                continue
            img_area = sum(_area(_image_ink(e)) for e in u.els if e["type"] == "image")
            if u.has_text and all(e["type"] == "text" for e in u.els):
                u.role, u.imp = "text", 0.35 + 0.65 * max(e["font_size"] for e in u.els) / fs_max
                if u.fs0() <= 0.5 * fs_max and u.ink[1] >= 0.8 * H0:
                    u.role, u.imp = "fine", 0.15  # small print hugging the bottom edge
            elif u.has_img and (not u.has_text or img_area >= 0.15 * _area(u.ink)):
                u.role = "image"
            else:
                u.role = "button"

        def cy(u):
            return (u.ink[1] + u.ink[3]) / 2

        def logoish(u):
            return _area(u.ink) <= 0.06 * cov and not 0.25 * H0 < cy(u) < 0.75 * H0

        imgs = sorted((u for u in content if u.role == "image" and not u.rot), key=lambda u: -_area(u.ink))
        self.hero = next((u for u in imgs if not logoish(u) and _area(u.ink) >= 0.02 * cov), None)
        self.logo = next((u for u in sorted(imgs, key=lambda u: u.ink[1]) if logoish(u) and u is not self.hero), None)
        for u in imgs:
            u.role, u.imp = ("hero", 0.75) if u is self.hero else ("logo", 0.95) if u is self.logo else ("image", 0.55)
        btns = sorted((u for u in content if u.role == "button" and not u.rot), key=lambda u: -_area(u.ink))
        for i, u in enumerate(btns):
            u.role, u.imp = ("cta", 0.85) if i == 0 else ("badge", 0.45)
        for u in content:
            if u.role in ("button", "image") and u.rot:
                u.role = "badge"
            if u.rot:
                u.imp = min(u.imp, 0.4)

        for u in content:
            labels = [e for e in u.els if e["type"] == "text"]
            if u.text:
                e = u.els[0]
                u.src_n = self.T.layout(e, max(1, int(e["font_size"])), e["width"]).n
            shapes = [e for e in u.els if e["type"] == "shape" and e.get("shape_type") != "line"]
            if len(u.els) == 2 and len(shapes) == 1 and len(labels) == 1 and not u.rot:
                li, sb = self._ink(labels[0]), _box(shapes[0])
                if _area(li) > 0 and _inter(li, sb) >= 0.85 * _area(li):
                    u.btn = (shapes[0], labels[0], max(0.0, min(li[0] - sb[0], sb[2] - li[2])),
                             max(0.0, li[1] - sb[1]), max(0.0, sb[3] - li[3]))

        texts = [u for u in content if u.role == "text"]
        self.headline = max(texts, key=lambda u: (u.imp, -u.ink[1]), default=None)
        self.fs_head0 = max(e["font_size"] for e in self.headline.els) if self.headline else fs_max
        self.essential = {u for u in (self.headline, self.logo) if u is not None}

        tol = max(2.0, 0.004 * max(W0, H0))
        self.cols = {}
        self.all_rows = _xycut(content, tol, canvas, self.cols)
        for u in content:  # alignment within its source column; text says so explicitly
            al = u.els[0].get("text_align") if u.text else None
            u.anchor = {"left": "l", "right": "r", "center": "c"}.get(al) or _anchor(u.ink, self.cols[u])

        # corner pins: small off-centre things hugging two edges, and rotated stickers
        for u in content:
            cx = (u.ink[0] + u.ink[2]) / 2
            dl, dr, dt, db = u.ink[0], W0 - u.ink[2], u.ink[1], H0 - u.ink[3]
            corner = ("t" if cy(u) < H0 / 2 else "b") + ("l" if cx < W0 / 2 else "r")
            row = next(r for r in self.all_rows if u in r)
            if u.role in ("hero", "rule") or any(v is not u and v.role == u.role for v in row):
                continue  # the end of a row of badges is not a corner sticker
            off_x, off_y = abs(cx / W0 - 0.5), abs(cy(u) / H0 - 0.5)
            if u.rot and off_x > 0.15 and off_y > 0.15:
                u.pin = corner
            elif (not u.rot and _area(u.ink) <= 0.06 * cov and off_x > 0.18
                  and min(dl, dr) <= 0.12 * W0 and min(dt, db) <= 0.12 * H0):
                u.pin = corner

        self.content = content
        self.pins = [u for u in content if u.pin]
        self.flow_rows = _xycut([u for u in content if not u.pin], tol, canvas, {})
        self.row_of = {u: i for i, r in enumerate(self.flow_rows) for u in r}

    # ------------------------------------------------------------------ measuring
    def _floor(self, u):
        f = self.floor * (1.3 if u is self.headline else 0.85 if u.role == "fine" else 1.0)
        return max(1.0, min(u.fs0(), f))

    def _kfloor(self, u):
        return self._floor(u) / u.fs0() if u.has_text else 0.0

    def _fs(self, u, p):
        return p[1] if p[0] == "t" else p[2] if p[0] == "b" else u.fs0() * p[1]

    def _max_lines(self, u):
        """Banner text may wrap, but not into a tower of one-word lines."""
        words = len(str(u.els[0].get("content", "")).split())
        return max(2 if u is self.headline else 3, u.src_n, math.ceil(words / 3))

    def _cap_lines(self, u, rw):
        """Stacked text wraps no more than ~2.5 words a line, unless even legible-minimum
        type in this width needs more lines."""
        key = (u, round(rw))
        if key not in self._capc:
            e = u.els[0]
            words = len(str(e.get("content", "")).split())
            floor_n = self.T.fit(e, math.ceil(self._floor(u)), rw)[1].n
            self._capc[key] = max(u.src_n, math.ceil(words / 2.5), floor_n)
        return self._capc[key]

    def _kmax(self, u):
        """Largest scale at which the unit's element boxes (padding included) fit the canvas."""
        return min(self.W / (u.w + u.pad[0] + u.pad[2]), self.H / (u.h + u.pad[1] + u.pad[3]))

    def _size(self, u, s, max_w, full=False, bal=False):
        """Visible (w, h) of unit u at scale s within max_w, plus how to emit it."""
        if u.text:
            e = u.els[0]
            ww = max(1.0, max_w if full else min(e["width"] * s, max_w))
            fs = round(e["font_size"] * s)
            if u.role == "fine":  # small print is already at its minimum: hold it there
                fs = max(fs, math.ceil(self._floor(u)))
            fs, lay = self.T.fit(e, fs, ww, e.get("max_lines"))
            if not full:  # stacked: bigger type that only adds lines is not better
                cap, lo = self._cap_lines(u, max_w), math.ceil(self._floor(u))
                while lay.n > cap and fs > lo:
                    fs = max(lo, min(fs - 1, int(fs * 0.95)))
                    lay = self.T.layout(e, fs, ww)
            if bal:
                ww, lay = self.T.balance(e, fs, ww, lay)
            return lay.w, lay.bot - lay.top, ("t", fs, lay, ww)
        k = min(s, self._kmax(u))
        if u is self.logo:  # a wordmark is text too: keep it readable
            k = max(k, min(3 * self.floor / u.h, self._kmax(u)))
        if u.btn and self._kfloor(u) > k:  # label would be illegible: a compact pill around a legible label
            return self._reflow_btn(u, k, max_w, bal, fs=math.ceil(self._floor(u)))
        if u.w * k > max_w and u.btn:
            return self._reflow_btn(u, k, max_w, bal)
        k = min(k, max_w / u.w)
        return u.w * k, u.h * k, ("r", k)

    def _reflow_btn(self, u, k, bw, bal=False, max_h=math.inf, fs=None):
        """A button off its natural proportions keeps a readable label: the padding tightens,
        the label wraps if it must and the shape follows (shapes may change aspect)."""
        _, e, padx, pt, pb = u.btn
        fs = fs or max(1, round(e["font_size"] * k))
        px = max(0.5 * fs, min(padx * k, max(0.6 * fs, 0.12 * bw)))
        bw = min(bw, self.T.layout(e, fs, math.inf).w + 2 * px + 1)  # never wider than one line needs
        inner = max(1.0, bw - 2 * px)
        fs, lay = self.T.fit(e, fs, inner)
        if bal:
            inner, lay = self.T.balance(e, fs, inner, lay)
        ink = lay.bot - lay.top
        pad = max((pt + pb) * k, 0.7 * fs)
        if ink + pad > max_h:  # short banner: a flatter pill rather than a smaller label
            pad = max(0.5 * fs, max_h - ink)
        return bw, ink + pad, ("b", pt / max(pt + pb, 1e-6) * pad, fs, lay, inner)

    @staticmethod
    def _pad(it, side):
        u, _, _, p = it
        return u.pad[side] * p[1] if p[0] == "r" else 0.0

    def _rows(self, rows, s, rw, full=False, hero=None, beta=1.0, bal=False):
        """Measure rows at scale s; a row too wide for rw wraps like flexbox."""
        built = []
        for row in rows:
            ss = s * (beta if hero and row[0] in hero else 1.0)
            items = [(u, *self._size(u, ss, rw, full, bal)) for u in row]
            src_gap = 0.0
            if len(row) > 1:
                xs = sorted(row, key=lambda u: u.ink[0])
                src_gap = sum(max(0.0, b.ink[0] - a.ink[2]) for a, b in zip(xs, xs[1:])) / (len(xs) - 1)
            gx = max(0.02 * rw, src_gap * s) if len(items) > 1 else 0.0
            span = sum(it[1] for it in items) + gx * (len(items) - 1)
            if len(items) > 1 and span > rw and (rw - gx * (len(items) - 1)) / (span - gx * (len(items) - 1)) >= 0.6:
                f = (rw - gx * (len(items) - 1)) / (span - gx * (len(items) - 1))  # shrink a little rather than wrap
                items = [(u, *self._size(u, ss * f * 0.995, rw, full, bal)) for u in row]
            lines, cur, cw = [], [], 0.0
            for it in items:
                need = it[1] + (gx if cur else 0.0)
                if cur and cw + need > rw:
                    lines.append((cur, cw))
                    cur, cw = [it], it[1]
                else:
                    cur.append(it)
                    cw += need
            lines.append((cur, cw))
            heights = [max(it[2] for it in ln) for ln, _ in lines]
            gy = 0.3 * min(heights) if len(lines) > 1 else 0.0
            anchor = row[0].anchor if len(row) == 1 else _anchor(_union(u.ink for u in row), self.cols[row[0]])
            tp = max(self._pad(it, 1) for it in lines[0][0])
            bp = max(self._pad(it, 3) for it in lines[-1][0])
            built.append(_RowM(lines, sum(heights) + gy * (len(lines) - 1), gx, gy, anchor, tp, bp))
        return built

    @staticmethod
    def _gmin(ha, hb, rh):
        return max(2.0, 0.012 * rh, 0.3 * min(ha, hb))

    def _need(self, built, rh, edges=(math.inf, math.inf)):
        """Height the rows need; edges = room between the rect and the canvas top/bottom."""
        return (sum(r.h for r in built) + sum(self._gmin(a.h, b.h, rh) for a, b in zip(built, built[1:]))
                + max(0.0, built[0].tp - edges[0]) + max(0.0, built[-1].bp - edges[1]))

    def _leg_built(self, built):
        return min([self._fs(u, p) / self._floor(u) for r in built for ln, _ in r.lines
                    for u, _, _, p in ln if u.has_text and u.role not in ("hero", "logo", "image")] or [1.0])

    @staticmethod
    def _search(ok, hi, it=22):
        if ok(hi):
            return hi
        lo = 0.0
        for _ in range(it):
            mid = (lo + hi) / 2
            if ok(mid):
                lo = mid
            else:
                hi = mid
        return lo

    def _rhythm(self, rows):
        """Source whitespace above, between and below rows: the designer's vertical rhythm.
        Where rows were taken out (hero moved aside, units dropped) the gaps around them average."""
        ref = self.flow_rows
        tops = [min(u.ink[1] for u in r) for r in ref]
        bots = [max(u.ink[3] for u in r) for r in ref]
        G = ([max(0.0, tops[0])] + [max(0.0, t - b) for b, t in zip(bots, tops[1:])]
             + [max(0.0, self.H0 - bots[-1])])
        idx = [self.row_of[r[0]] for r in rows]
        inner = []
        for a, b in zip(idx, idx[1:]):
            seg = G[min(a, b) + 1: max(a, b) + 1]
            inner.append(sum(seg) / len(seg) if seg else 0.0)
        return [G[0]] + inner + [G[-1]]

    def _place_rows(self, built, x0, rw, y, gaps):
        place = {}
        for i, r in enumerate(built):
            yy = y
            for items, lw in r.lines:
                lh = max(it[2] for it in items)
                x = x0 if r.anchor == "l" else x0 + rw - lw if r.anchor == "r" else x0 + (rw - lw) / 2
                x = max(min(x, self.W - self._pad(items[-1], 2) - lw), self._pad(items[0], 0))
                for u, w, hh, p in items:
                    place[u] = (x, yy + (lh - hh) / 2, w, hh, p)
                    x += w + r.gx
                yy += lh + r.gy
            y += r.h + (gaps[i] if i < len(gaps) else 0.0)
        return place

    # ------------------------------------------------------------------ layouts
    def _stack(self, rows, rect, s_cap, hero=None):
        """Rows stacked vertically at the largest scale that fits, spaced in the source rhythm."""
        x0, y0, x1, y1 = rect
        rw, rh = x1 - x0, y1 - y0
        if not rows or rw < 1 or rh < 1:
            return None
        edges = (y0, self.H - y1)
        hero = next((set(r) for r in rows if hero in r), None)  # the hero's whole row scales as one

        def fits(s, beta, room=1.0):
            return self._need(self._rows(rows, s, rw, hero=hero, beta=beta), rh, edges) <= room * rh

        best = None
        for beta in ((1.0, 0.75, 0.55) if hero else (1.0,)):  # shrink the hero before the text
            s = self._search(lambda v: fits(v, beta), s_cap)
            if s <= 0:
                continue
            leg = self._leg_built(self._rows(rows, s, rw, hero=hero, beta=beta))
            if best is None or leg > best[2] + 1e-6:
                best = (s, beta, leg)
            if leg >= 0.95:
                break
        if best is None:
            return None
        s, beta, _ = best
        if hero and beta == 1.0 and s >= s_cap * 0.999:  # a taller canvas has room: let the hero grow
            grow = min(1.5, max(1.0, (self.H / self.W) / (self.H0 / self.W0)) ** 0.5)
            beta = max(1.0, self._search(lambda b: fits(s, b, 0.9), grow))
        built = self._rows(rows, s, rw, hero=hero, beta=beta, bal=True)

        G = self._rhythm(rows)
        T = [max(0.0, G[0] * s - self.m)] + [g * s for g in G[1:-1]] + [max(0.0, G[-1] * s - self.m)]
        gmin = ([max(0.0, built[0].tp - edges[0])] + [self._gmin(a.h, b.h, rh) for a, b in zip(built, built[1:])]
                + [max(0.0, built[-1].bp - edges[1])])
        free = rh - sum(r.h for r in built)
        lam = self._search(lambda v: sum(max(g, v * t) for g, t in zip(gmin, T)) <= free, 1.8)
        gaps = [max(g, lam * t) for g, t in zip(gmin, T)]
        spare = max(0.0, free - sum(gaps)) / 2
        return self._place_rows(built, x0, rw, y0 + gaps[0] + spare, gaps[1:]), s

    def _rect(self):
        return self.m, self.m, self.W - self.m, self.H - self.m

    def _cap(self, rows):
        """Largest content scale worth trying: the area ratio; more on tall narrow canvases
        (text can wrap); never less than what keeps the text legible. Space decides the rest."""
        tall = min(0.8 * self.H / self.H0, 3 * self.W / self.W0)
        legible = max([self._floor(u) / u.fs0() for r in rows for u in r if u.text and u.role != "fine"] or [0.0])
        return max(self.s_geo, tall, legible)

    @staticmethod
    def _active(rows, gone):
        return [r for r in ([u for u in row if u not in gone] for row in rows) if r]

    def _flow(self, rows, rect, pins, extra=None):
        """Stack the flow in rect; corner pins keep their corner and push the flow away."""
        place, s = {}, self.s_geo
        for _ in range(3):
            res = self._stack(rows, rect, self._cap(rows), self.hero) if rows else ({}, self.s_geo)
            if res is None:
                return None
            place, s = res
            place.update(extra or {})
            pp = self._pins(pins, s)
            top = [b for u, b in pp.items() if u.pin[0] == "t" and self._clash(u, b, place)]
            bot = [b for u, b in pp.items() if u.pin[0] == "b" and self._clash(u, b, place)]
            if not (top or bot) or not rows:
                break
            x0, y0, x1, y1 = rect
            gap = 0.5 * self.m
            y0 = max([y0] + [b[1] + b[3] + gap for b in top])
            y1 = min([y1] + [b[1] - gap for b in bot])
            if y1 - y0 < 0.45 * (rect[3] - rect[1]):
                break
            rect = (x0, y0, x1, y1)
        pp = self._pins(pins, s)
        for u in list(pp):
            for _ in range(4):  # still in the way: shrink toward its corner
                if not self._clash(u, pp[u], place):
                    break
                pp[u] = self._shrink_pin(u, pp[u], 0.8)
        place.update(pp)
        return place

    def _pins(self, pins, s):
        out = {}
        for u in pins:
            k = min(s, 0.4 * self.W / u.w, 0.25 * self.H / u.h, self._kmax(u))
            if u.btn:
                w, h, p = self._size(u, k, 0.4 * self.W)
            else:
                k = min(max(k, self._kfloor(u)), 0.4 * self.W / u.w, 0.25 * self.H / u.h, self._kmax(u))
                w, h, p = u.w * k, u.h * k, ("r", k)
            pk = p[1] if p[0] == "r" else 0.0  # transparent padding only exists on rigid units
            dx = (u.ink[0] if u.pin[1] == "l" else self.W0 - u.ink[2]) * k
            dy = (u.ink[1] if u.pin[0] == "t" else self.H0 - u.ink[3]) * k
            x = max(dx, u.pad[0] * pk) if u.pin[1] == "l" else self.W - max(dx, u.pad[2] * pk) - w
            y = max(dy, u.pad[1] * pk) if u.pin[0] == "t" else self.H - max(dy, u.pad[3] * pk) - h
            out[u] = (x, y, w, h, p)
        return out

    def _shrink_pin(self, u, b, f):
        if b[4][0] != "r":
            return b
        x, y, w, h, (_, k) = b
        nw, nh = w * f, h * f
        nx = x if u.pin[1] == "l" else x + w - nw
        ny = y if u.pin[0] == "t" else y + h - nh
        return nx, ny, nw, nh, ("r", k * f)

    @staticmethod
    def _clash(u, b, place):
        """Pins may overlap pictures (a sticker on a product) but never text."""
        bx = (b[0], b[1], b[0] + b[2], b[1] + b[3])
        return any(v is not u and (v.has_text or u.has_text and not v.has_img) and
                   _inter(bx, (p[0], p[1], p[0] + p[2], p[1] + p[3])) > 0 for v, p in place.items())

    def _stack_all(self, gone):
        place = self._flow(self._active(self.flow_rows, gone), self._rect(),
                           [u for u in self.pins if u not in gone])
        return None if place is None else _Layout(place, gone)

    def _split(self, gone, side):
        hero = self.hero
        if hero is None or hero in gone:
            return None
        x0, y0, x1, y1 = self._rect()
        rw = x1 - x0
        row = next(r for r in self.flow_rows if hero in r)  # a row of products moves aside together
        media = [u for u in row if u not in gone] if all(u.role in ("hero", "image") for u in row) else [hero]
        rows = self._active(self.flow_rows, gone | set(media))
        if not rows:
            return None
        pins = [u for u in self.pins if u not in gone]
        best, best_sc = None, -math.inf
        for frac in (0.5, 0.42, 0.34):
            mw = frac * rw
            res = self._stack([media], (x0, y0, x0 + mw, y1) if side == "l" else (x1 - mw, y0, x1, y1),
                              max(1.0, 1.4 * self.s_geo))
            if res is None:
                continue
            mplace = self._flush(res[0], side, x0, x1)
            gut = max(self.m, 0.05 * rw)
            col = ((max(p[0] + p[2] for p in mplace.values()) + gut, y0, x1, y1) if side == "l"
                   else (x0, y0, min(p[0] for p in mplace.values()) - gut, y1))
            if col[2] - col[0] < 0.25 * rw:
                continue
            place = self._flow(rows, col, pins, mplace)
            if place is None:
                continue
            lay = _Layout(place, gone)
            sc = self._score(lay)
            if sc > best_sc:
                best, best_sc = lay, sc
        return best

    def _flush(self, place, side, x0, x1):
        """Slide a placed block against the outer margin, as far as its transparent padding allows."""
        pad = [(p, u.pad[0] * p[4][1] if p[4][0] == "r" else 0.0, u.pad[2] * p[4][1] if p[4][0] == "r" else 0.0)
               for u, p in place.items()]
        if side == "l":
            dx = max([x0 - min(p[0] for p, _, _ in pad)] + [pl - p[0] for p, pl, _ in pad])
        else:
            dx = min([x1 - max(p[0] + p[2] for p, _, _ in pad)] + [self.W - pr - p[0] - p[2] for p, _, pr in pad])
        return {u: (p[0] + dx, *p[1:]) for u, p in place.items()}

    def _row(self, gone):
        """Banner: pictures | text stack | call-to-action, all vertically centred."""
        W, H = self.W, self.H
        mx, my = max(self.m, 0.03 * W), max(3.0, 0.1 * H)
        x0, y0, rw, rh = mx, my, W - 2 * mx, H - 2 * my
        order = [u for r in self.all_rows for u in r if u not in gone]
        imgs = [u for u in order if u.role in ("logo", "hero", "image") and not u.rot]
        ends = [u for u in order if u.role == "cta" and not u.rot] + [u for u in order if u.rot]
        mid = self._active(self.all_rows, gone | set(imgs) | set(ends))
        seq = imgs + ([None] if mid else []) + ends
        n = len(seq)
        if n == 0:
            return _Layout({}, gone)
        cap = max(1.0, 1.3 * self.s_geo)
        ik = {u: min((1.0 if u.role == "hero" else 0.8) * rh / u.h, cap, 0.4 * rw / u.w, self._kmax(u)) for u in imgs}
        gap = max(0.015 * W, 0.1 * rh)
        legible_t = max([self._floor(u) / u.fs0() for r in mid for u in r if u.has_text] or [0.0])
        res = None
        for share in (0.5, 0.36, 0.25):  # pictures give up width before anything is dropped
            f = min(1.0, share * rw / max(sum(u.w * k for u, k in ik.items()), 1e-6))
            fixed = {u: (u.w * k * f, u.h * k * f, ("r", k * f)) for u, k in ik.items()}
            r = self._row_fit(seq, mid, fixed, ends, x0, rw, rh, gap)
            if r is not None and (res is None or r[0] > res[0]):
                res = r
            if res is not None and res[0] >= 0.95 * legible_t:
                break
        if res is None:
            return None
        t, lo_x, avail, (sz, built, need) = res
        widths = [sz[u][0] if u else max(lw for r in built for _, lw in r.lines) for u in seq]
        g = gap + (min((avail - sum(widths) - gap * (n - 1)) / (n - 1), 0.06 * W) if n > 1 else 0.0)
        x = lo_x + (avail - sum(widths) - g * (n - 1)) / 2
        align = (self.headline.els[0].get("text_align", "left") if self.headline else "center")
        anchor = {"center": "c", "right": "r"}.get(align, "l")
        place = {}
        for u, w in zip(seq, widths):
            if u is None:
                gaps = [self._gmin(a.h, b.h, rh) for a, b in zip(built, built[1:])]
                place.update(self._place_rows([r._replace(anchor=anchor) for r in built], x, w,
                                              y0 + (rh - need) / 2, gaps))
            else:
                _, h, p = sz[u]
                k = p[1]
                y = min(max(y0 + (rh - h) / 2, u.pad[1] * k if p[0] == "r" else 0.0),
                        H - (u.pad[3] * k if p[0] == "r" else 0.0) - h)
                place[u] = (x, y, w, h, p)
            x += w + g
        return _Layout(place, gone)

    def _row_fit(self, seq, mid, fixed, ends, x0, rw, rh, gap):
        """Largest text scale t at which pictures + text stack + ends fit the banner."""
        W, H, n = self.W, self.H, len(seq)
        cap = max(1.0, 1.3 * self.s_geo)

        def end(u, t):
            kf = self._kfloor(u)
            hi = (0.62 if u.role == "cta" else 0.8) * rh / u.h
            k = min(max(t, kf), max(hi, min(kf, 0.95 * rh / u.h)), self._kmax(u))
            if u.btn and u.w * k > 0.28 * rw:
                w, h, p = self._reflow_btn(u, k, 0.28 * rw, max_h=rh)
                if h <= rh:
                    return w, h, p
            k = min(k, 0.28 * rw / u.w, rh / u.h)
            return u.w * k, u.h * k, ("r", k)

        def sizes(t):
            return {**fixed, **{u: end(u, t) for u in ends}}

        t_cap = min(cap, 0.45 * H / self.fs_head0)
        sz = sizes(t_cap)
        first, last = seq[0], seq[-1]
        lo_x = max(x0, self._pad((first, *sz[first]), 0)) if first else x0
        hi_x = min(x0 + rw, W - self._pad((last, *sz[last]), 2)) if last else x0 + rw
        avail = hi_x - lo_x

        def build(t, bal=False):
            sz = sizes(t)
            room = avail - sum(v[0] for v in sz.values()) - gap * (n - 1)
            if not mid:
                return (sz, [], 0.0) if room >= 0 else None
            if room < 0.12 * rw:
                return None
            b = self._rows(mid, t, room, full=True, bal=bal)
            if any(p[0] == "t" and p[2].n > self._max_lines(u) for r in b for ln, _ in r.lines for u, _, _, p in ln):
                return None  # banners read in one glance: no towers of one-word lines
            need = self._need(b, rh)
            return (sz, b, need) if need <= rh else None

        t = self._search(lambda v: build(v) is not None, t_cap)
        # balancing can move a descender onto the last line: at the very edge, keep the plain wrap
        res = (build(t, bal=True) or build(t)) if t > 0 else None
        return None if res is None else (t, lo_x, avail, res)

    # ------------------------------------------------------------------ choose
    def _legibility(self, lay):
        worst, bad = math.inf, []
        for u, p in lay.place.items():
            if u.has_text and u.role not in ("hero", "logo", "image"):  # text on a picture is secondary
                r = self._fs(u, p[4]) / self._floor(u)
                worst = min(worst, r)
                if r < 0.95:
                    bad.append(u)
        return (1.0 if worst == math.inf else worst), bad

    def _score(self, lay):
        """Legible, big headline, big hero, nothing dropped. Soft caps let a layout that uses
        a wide canvas better (bigger type and picture than the area ratio implies) win."""
        leg = min(1.0, self._legibility(lay)[0])
        head = 1.0
        if self.headline in lay.place:
            head = min(1.25, self._fs(self.headline, lay.place[self.headline][4]) / (self.fs_head0 * self.s_geo))
        hero = 1.0
        if self.hero is not None:
            p = lay.place.get(self.hero)
            hero = min(1.25, p[4][1] / self.s_geo) if p else 0.0
        return 0.4 * leg + 0.35 * head + 0.25 * hero - 0.3 * sum(u.imp for u in lay.dropped)

    def _solve(self, fn):
        """Drop the least important unit while text is illegible - if dropping helps."""
        gone = set()
        lay = fn(gone)
        budget = 40  # ponytail: greedy with a trial budget; bounded time on very dense designs
        while budget > 0:
            leg, bad = self._legibility(lay) if lay else (0.0, [])
            if lay is not None and not bad:
                return lay
            limit = max(u.imp for u in bad) if lay is not None else math.inf
            pool = sorted((u for u in self.content if u not in gone and u not in self.essential and u.imp <= limit),
                          key=lambda u: (u.imp, _area(u.ink)))
            tried = set()
            for victim in pool:  # cheapest first; take the first drop that actually helps
                if victim in tried:
                    continue
                if budget <= 0:
                    return lay
                budget -= 1
                row = next((r for r in self.all_rows if victim in r), [victim])
                batch = {u for u in row if u.role == victim.role and u in pool}  # a row of badges goes together
                tried |= batch
                trial = fn(gone | batch)
                if trial is None and lay is not None:
                    continue
                if lay is None or batch & set(bad) or self._legibility(trial)[0] > leg + 1e-3:
                    gone, lay = gone | batch, trial
                    break
            else:
                return lay
        return lay

    def run(self):
        r = self.W / self.H
        if not self.content:
            lay = _Layout({}, set())
        elif r >= 2.4:
            lay = self._solve(self._row)
        else:
            cands = [self._solve(self._stack_all)]
            if r > 1.25 and self.hero is not None:
                cx = (self.hero.ink[0] + self.hero.ink[2]) / 2 / self.W0
                sides = ["l"] if cx < 0.42 else ["r"] if cx > 0.58 else ["r", "l"]
                split = [self._solve(lambda d, sd=sd: self._split(d, sd)) for sd in sides]
                cands = split + cands if r > 1.5 else cands + split  # ties go to the shape-native layout
            cands = [c for c in cands if c is not None]
            lay = max(cands, key=self._score) if cands else None
        if lay is None:
            return _letterbox(self.src, {"width": int(self.W), "height": int(self.H)})
        return self._emit(lay)

    # ------------------------------------------------------------------ emit
    def _emit(self, lay):
        out = {id(e): copy.deepcopy(e) for e in self.src["elements"]}
        for u, (x, y, w, h, p) in lay.place.items():
            if p[0] == "t":
                e = u.els[0]
                self._put_text(e, out[id(e)], x, y, *p[1:])
            elif p[0] == "b":
                self._emit_btn(u, x, y, w, h, p, out)
            else:
                self._emit_rigid(u, x, y, p[1], out)
        for u in self.content:
            if u not in lay.place:  # dropped (or, defensively, never placed)
                self._ghost(u.els, out, hide=u in lay.dropped)
        for e in self.hidden:
            self._ghost([e], out, hide=True)
        for e in self.passive:
            self._ghost([e], out, hide=False)
        for e in self.bg:
            o = out[id(e)]
            o.update(x=0, y=0, width=self.W, height=self.H)
            if o.get("rotation"):
                o["rotation"] = 0
            if e["type"] == "image" and e.get("fit", "contain") != "cover":
                o["fit"] = "cover"  # full-bleed art crops; it never letterboxes or stretches
        texts = [(x, y, x + w, y + h) for u, (x, y, w, h, _) in lay.place.items() if u.has_text]
        for u in self.decor:
            self._decor(u, out, texts)
        for u in self.panels:
            self._panel(u, lay, out)
        result = [out[id(e)] for e in self.src["elements"]]
        for o in result:
            self._clamp(o)
        return result

    def _emit_rigid(self, u, x, y, k, out):
        # a sticker on a picture that has become unreadably small is dropped, the picture stays
        specks = (u.role in ("hero", "logo", "image") and u.has_text
                  and round(u.fs0() * k) < 0.85 * min(self.floor, u.fs0()))
        for e in u.els:
            o = out[id(e)]
            o.update(x=x + (e["x"] - u.ink[0]) * k, y=y + (e["y"] - u.ink[1]) * k,
                     width=e["width"] * k, height=e["height"] * k)
            if e["type"] == "text":
                src = self.T.layout(e, max(1, int(e["font_size"])), e["width"])
                fs = max(1, round(e["font_size"] * k))
                while True:  # rounding must never add a line the designer didn't have
                    lay = self.T.layout(o, fs, o["width"])
                    if fs <= 1 or lay.n <= src.n and (src.over or not lay.over):
                        break
                    fs -= 1
                o["font_size"] = fs
                o["height"] = max(o["height"], lay.box_h)  # top-aligned: growing the box moves nothing
                self._spacing(o, e, fs / e["font_size"])
            if specks and e["type"] != "image":
                self._hide(o)
            elif e["type"] == "shape":
                for f in ("corner_radius", "stroke_width"):
                    if e.get(f):
                        o[f] = e[f] * k

    def _put_text(self, e, o, x, y, fs, lay, ww):
        """Text whose ink lands at (x, y). The box is shrink-wrapped; one space of slack keeps
        the renderer's greedy wrap identical to the measured one."""
        bw = max(1.0, min(ww, lay.w + lay.space + 1.0))
        al = e.get("text_align", "left")
        bx = x + (lay.w - bw) / 2 if al == "center" else x + lay.w - bw if al == "right" else x
        o.update(x=bx, y=y - lay.top, width=bw, height=lay.box_h, font_size=fs)
        self._spacing(o, e, fs / e["font_size"])

    def _emit_btn(self, u, x, y, w, h, p, out):
        _, top, fs, lay, inner = p
        sh, e = u.btn[:2]
        k = fs / e["font_size"]
        o = out[id(sh)]
        o.update(x=x, y=y, width=w, height=h)
        if sh.get("corner_radius"):
            o["corner_radius"] = min(sh["corner_radius"] * k, h / 2)
        if sh.get("stroke_width"):
            o["stroke_width"] = sh["stroke_width"] * k
        self._put_text(e, out[id(e)], x + (w - lay.w) / 2, y + top, fs, lay, inner)

    @staticmethod
    def _spacing(o, e, f):
        if e.get("letter_spacing"):
            o["letter_spacing"] = e["letter_spacing"] * f

    def _ghost(self, els, out, hide, k=None):
        """Keep the relative position (uniformly scaled, clamped in-canvas)."""
        b = _union(_aabb(e) for e in els)
        k = self.s_fit if k is None else k
        w, h = (b[2] - b[0]) * k, (b[3] - b[1]) * k
        if w > self.W or h > self.H:
            k *= min(self.W / max(w, 1e-6), self.H / max(h, 1e-6))
            w, h = (b[2] - b[0]) * k, (b[3] - b[1]) * k
        cx = (b[0] + b[2]) / 2 / self.W0 * self.W
        cy = (b[1] + b[3]) / 2 / self.H0 * self.H
        x = min(max(cx - w / 2, 0.0), self.W - w)
        y = min(max(cy - h / 2, 0.0), self.H - h)
        for e in els:
            o = out[id(e)]
            o.update(x=x + (e["x"] - b[0]) * k, y=y + (e["y"] - b[1]) * k,
                     width=e["width"] * k, height=e["height"] * k)
            if e["type"] == "text":
                o["font_size"] = max(1, round(e["font_size"] * k))
            elif e["type"] == "shape" and e.get("corner_radius"):
                o["corner_radius"] = e["corner_radius"] * k
            if hide:
                self._hide(o)

    @staticmethod
    def _hide(o):
        """Dropped means invisible. The renderer ignores text opacity, so text also goes transparent."""
        o["opacity"] = 0
        if o["type"] == "text":
            o["color"] = _transparent(o.get("color"))
            for run in o.get("style_runs") or []:
                if "color" in run:
                    run["color"] = _transparent(run["color"])

    def _decor(self, u, out, texts):
        k = max(self.s_fit, 0.5 * self.s_geo)
        opaque = max(_alpha(e) for e in u.els) >= 0.5
        for _ in range(5):
            self._ghost(u.els, out, hide=False, k=k)
            b = _union(_aabb(out[id(e)]) for e in u.els)
            if not opaque or not any(_inter(b, t) > 0 for t in texts):
                break
            k *= 0.7  # an opaque shape must not sit under text it never touched

    def _panel(self, u, lay, out):
        kids = [c for c in u.kids if c in lay.place]
        if not kids:
            self._ghost(u.els, out, hide=True)
            return
        kb = _union((x, y, x + w, y + h) for x, y, w, h, _ in (lay.place[c] for c in kids))
        sb = _union(c.ink for c in kids)
        k = ((kb[2] - kb[0]) + (kb[3] - kb[1])) / max((sb[2] - sb[0]) + (sb[3] - sb[1]), 1e-6)
        tb = [max(0.0, kb[0] - (sb[0] - u.ink[0]) * k), max(0.0, kb[1] - (sb[1] - u.ink[1]) * k),
              min(self.W, kb[2] + (u.ink[2] - sb[2]) * k), min(self.H, kb[3] + (u.ink[3] - sb[3]) * k)]
        g = 0.5 * self.m
        for v, (x, y, w, h, _) in lay.place.items():  # a white logo never sat on the card: keep it off
            vb = (x, y, x + w, y + h)
            if v in kids or _inter(v.ink, u.ink) > 0 or _inter(vb, tb) <= 0:
                continue
            if vb[2] <= kb[0]:
                tb[0] = min(max(tb[0], vb[2] + g), kb[0])
            elif vb[0] >= kb[2]:
                tb[2] = max(min(tb[2], vb[0] - g), kb[2])
            elif vb[3] <= kb[1]:
                tb[1] = min(max(tb[1], vb[3] + g), kb[1])
            elif vb[1] >= kb[3]:
                tb[3] = max(min(tb[3], vb[1] - g), kb[3])
        fx, fy = (tb[2] - tb[0]) / u.w, (tb[3] - tb[1]) / u.h  # shapes may stretch to wrap their content
        for e in u.els:
            out[id(e)].update(x=tb[0] + (e["x"] - u.ink[0]) * fx, y=tb[1] + (e["y"] - u.ink[1]) * fy,
                              width=e["width"] * fx, height=e["height"] * fy)

    def _clamp(self, o):
        """Last line of defence: inside the canvas, rotation included."""
        W, H = self.W, self.H
        b = _union([_box(o), _aabb(o)])
        bw, bh = b[2] - b[0], b[3] - b[1]
        if bw > W + 0.01 or bh > H + 0.01:
            f = min(W / bw, H / bh) * 0.999
            cx, cy = o["x"] + o["width"] / 2, o["y"] + o["height"] / 2
            o["width"], o["height"] = o["width"] * f, o["height"] * f
            o["x"], o["y"] = cx - o["width"] / 2, cy - o["height"] / 2
            if o["type"] == "text":
                o["font_size"] = max(1, int(o["font_size"] * f))
            b = _union([_box(o), _aabb(o)])
        o["x"] += -b[0] if b[0] < 0 else min(0.0, W - b[2])
        o["y"] += -b[1] if b[1] < 0 else min(0.0, H - b[3])
        text = o["type"] == "text"
        for f in ("x", "y", "width", "height"):  # never round a wrap width down onto a line break
            o[f] = math.ceil(o[f] * 100) / 100 if text and f == "width" else round(o[f], 2)


def _letterbox(source, canvas):
    """Safety net only: uniform fit, centred; backgrounds still go full-bleed."""
    W, H = canvas["width"], canvas["height"]
    W0, H0 = source["canvas"]["width"], source["canvas"]["height"]
    s = min(W / W0, H / H0)
    ox, oy = (W - W0 * s) / 2, (H - H0 * s) / 2
    out = []
    for e in source["elements"]:
        o = copy.deepcopy(e)
        if e["type"] != "text" and _inter(_aabb(e), (0, 0, W0, H0)) >= 0.85 * W0 * H0:
            o.update(x=0, y=0, width=W, height=H)
            if e["type"] == "image":
                o["fit"] = "cover"
        else:
            o.update(x=ox + e["x"] * s, y=oy + e["y"] * s, width=e["width"] * s, height=e["height"] * s)
            if e["type"] == "text":
                o["font_size"] = max(1, round(e["font_size"] * s))
        x0, y0, x1, y1 = _union([_box(o), _aabb(o)])
        o["x"] += -x0 if x0 < 0 else min(0.0, W - x1)
        o["y"] += -y0 if y0 < 0 else min(0.0, H - y1)
        out.append(o)
    return out
