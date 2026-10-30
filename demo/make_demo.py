"""Renders the narrated demo video from real engine output (official renderer, brand fonts).

    uv run --with edge-tts python demo/voiceover.py    # once: narration.json -> demo/voice/*.mp3
    python demo/make_demo.py                           # -> demo/chameleon_demo.mp4 + demo/stills/

Every layout on screen is the engine's own output. The morphs re-render the design at each
frame while its elements travel from one layout to the next. The music bed is synthesised here,
so there is nothing to license. Needs ffmpeg on PATH (or imageio-ffmpeg).
Dev tooling only; the engine does not depend on it.
"""
import copy
import json
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import engine.resize as R  # noqa: E402
from renderer.converter.convert import render_design_to_png  # noqa: E402

W, H, FPS = 1920, 1080, 30
DEMO, FONTS = ROOT / "demo", ROOT / "brand_kit" / "fonts"
TMP = Path(tempfile.mkdtemp(prefix="chameleon_demo_"))
WHITE, MUTED, ACCENT, RED = (245, 243, 249), (160, 154, 180), (232, 106, 58), (240, 76, 76)
LEAD, TAIL, FADE = 0.35, 0.9, 0.4  # voice starts LEAD into its scene; scenes end TAIL after it
ROLE_COLORS = [(240, 180, 60), (110, 200, 160), (120, 170, 250), (232, 106, 58), (220, 130, 220), (200, 200, 120)]


def font(size, bold=False, serif=False):
    name = ("SolsticeSerif-" if serif else "SolsticeSans-") + ("Bold" if bold else "Regular")
    return ImageFont.truetype(str(FONTS / f"{name}.ttf"), size)


def task(name):
    p = next(p for d in ("input", "tests/designs") for p in (ROOT / d).glob(f"{name}.json"))
    return json.loads(p.read_text())["source"]


def resize(src, w, h):
    return R.resize(src, {"width": w, "height": h})


def ease(t, t0, t1):
    """0 before t0, 1 after t1, smooth in between."""
    x = min(1.0, max(0.0, (t - t0) / (t1 - t0)))
    return x * x * (3 - 2 * x)


def lerp(a, b, t):
    return a + (b - a) * t


# --------------------------------------------------------------------------- rendering
PX = ("x", "y", "width", "height", "font_size", "corner_radius", "stroke_width", "letter_spacing")
_n = 0


def render(design, box_w, box_h):
    """The design drawn by the official renderer at exactly the size it is shown (crisp at any scale)."""
    global _n
    cw, ch = design["canvas"]["width"], design["canvas"]["height"]
    f = min(box_w / cw, box_h / ch)
    d = copy.deepcopy(design)
    d["canvas"] = {"width": max(1, round(cw * f)), "height": max(1, round(ch * f))}
    for e in d["elements"]:
        for k in PX:
            if isinstance(e.get(k), (int, float)):
                e[k] *= f
        for run in e.get("style_runs") or []:
            if isinstance(run.get("font_size"), (int, float)):
                run["font_size"] *= f
    _n += 1
    path = TMP / f"r{_n % 8}.png"
    render_design_to_png(d, ROOT / "brand_kit" / "assets", path)
    return Image.open(path).convert("RGB")


def scale_of(design, box_w, box_h):
    return min(box_w / design["canvas"]["width"], box_h / design["canvas"]["height"])


_RGBA = re.compile(r"rgba?\(([^)]*)\)")


def _mix(a, b, t):
    ma, mb = _RGBA.match(str(a).replace(" ", "")), _RGBA.match(str(b).replace(" ", ""))
    if a == b or not (ma and mb):
        return b if t > 0.5 else a
    pa, pb = ([float(v) for v in m.group(1).split(",")] + [1.0] for m in (ma, mb))
    c = [lerp(u, v, t) for u, v in zip(pa[:4], pb[:4])]
    return "rgba(%d,%d,%d,%.3f)" % (c[0], c[1], c[2], c[3])


def tween(a, b, t):
    """The design part-way between layouts a and b: every element travels, resizes and fades."""
    if t <= 0 or t >= 1:
        return a if t <= 0 else b
    base = {"rotation": 0, "opacity": 1}
    by_id = {e["id"]: e for e in a["elements"]}
    els = []
    for eb in b["elements"]:
        ea, e = by_id[eb["id"]], dict(eb)
        for k in ("x", "y", "width", "height", "font_size", "rotation", "opacity", "corner_radius"):
            va, vb = ea.get(k, base.get(k)), eb.get(k, base.get(k))
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                e[k] = lerp(va, vb, t)
        if "color" in ea:
            e["color"] = _mix(ea["color"], eb.get("color", ea["color"]), t)
        els.append(e)
    cw = lerp(a["canvas"]["width"], b["canvas"]["width"], t)
    ch = lerp(a["canvas"]["height"], b["canvas"]["height"], t)
    return {"canvas": {"width": round(cw), "height": round(ch)}, "elements": els}


class Morph:
    """A design travelling through layouts: keys = [(t_start, t_end, design_to)], first design held at t=0."""

    def __init__(self, first, keys, box):
        self.first, self.keys, self.box = first, keys, box
        self.cache = {}

    def design(self, t):
        cur = self.first
        for t0, t1, nxt in self.keys:
            if t < t1:
                return tween(cur, nxt, ease(t, t0, t1)) if t > t0 else cur
            cur = nxt
        return cur

    def image(self, t):
        d = self.design(t)
        key = id(d)  # a held layout is the same object: render it once
        if key not in self.cache:
            img = render(d, *self.box)
            if any(d is k[2] for k in self.keys) or d is self.first:
                self.cache[key] = img
            return img
        return self.cache[key]


# --------------------------------------------------------------------------- frame drawing
def _backdrop():
    y, x = np.mgrid[0:H, 0:W]
    r = np.clip(np.hypot((x - W / 2) / W, (y - H * 0.42) / H) * 1.5, 0, 1)[..., None]
    return Image.fromarray(((1 - r) * [42, 36, 56] + r * [14, 12, 20]).astype(np.uint8), "RGB")


BACK = _backdrop()


def card(fr, img, cx, cy, alpha=1.0, shadow=True):
    """Paste img centred on (cx, cy) with a soft drop shadow; returns its top-left corner."""
    x, y = round(cx - img.width / 2), round(cy - img.height / 2)
    if shadow:
        sw, sh = img.width // 4 + 20, img.height // 4 + 20
        m = Image.new("L", (sw, sh), 0)
        ImageDraw.Draw(m).rectangle([10, 10, sw - 10, sh - 10], fill=int(150 * alpha))
        m = m.filter(ImageFilter.GaussianBlur(5)).resize((sw * 4, sh * 4))
        fr.paste((0, 0, 0), (x - 40, y - 28, x - 40 + m.width, y - 28 + m.height), m)
    fr.paste(img, (x, y), None if alpha >= 1 else Image.new("L", img.size, int(255 * alpha)))
    return x, y


def label(fr, xy, text, size=26, color=MUTED, anchor="la", bold=False):
    ImageDraw.Draw(fr).text(xy, text, font=font(size, bold), fill=color, anchor=anchor)


def mark(fr, box, color, text=None, width=4, alpha=1.0, beside=False):
    """Outline a region and tag it (above it, or beside it on the right), like a reviewer's annotation."""
    d = ImageDraw.Draw(fr, "RGBA")
    c = color + (int(255 * alpha),)
    d.rounded_rectangle(box, radius=8, outline=c, width=width)
    if text:
        f = font(22, True)
        tw = d.textlength(text, font=f)
        x, y = (box[2] + 10, box[1]) if beside else (box[0], max(4, box[1] - 34))
        d.rounded_rectangle([x, y, x + tw + 20, y + 30], radius=6, fill=c)
        d.text((x + 10, y + 15), text, font=f, fill=(20, 18, 28, int(255 * alpha)), anchor="lm")


def to_screen(origin, k, b):
    return (origin[0] + b[0] * k, origin[1] + b[1] * k, origin[0] + b[2] * k, origin[1] + b[3] * k)


def el_box(design, ids):
    return R._union(R._aabb(e) for e in design["elements"] if e["id"] in ids)


def wrap(text, f, max_w):
    lines, cur = [], ""
    for word in text.split():
        nxt = f"{cur} {word}".strip()
        if cur and f.getlength(nxt) > max_w:
            lines.append(cur)
            cur = word
        else:
            cur = nxt
    return lines + [cur]


def caption(fr, text):
    f = font(36)
    d = ImageDraw.Draw(fr, "RGBA")
    lines, lh = wrap(text, f, 1400), 50
    y0 = H - 64 - len(lines) * lh
    half = max(f.getlength(s) for s in lines) / 2
    d.rounded_rectangle([W / 2 - half - 28, y0 - 14, W / 2 + half + 28, y0 + len(lines) * lh + 6], radius=14,
                        fill=(8, 6, 14, 175))
    for i, s in enumerate(lines):
        d.text((W / 2, y0 + i * lh), s, font=f, fill=WHITE, anchor="ma")


# --------------------------------------------------------------------------- scenes
# Each scene: (voice id, minimum length, draw(frame, t)). Timings are seconds into the scene and
# follow the narration; a longer recording only lengthens the holds.
def scene_crop():
    src = task("chameleon_sample_level3_advanced")
    big = render(src, 560, 700)
    k = big.width / src["canvas"]["width"]
    keep = 1080 * 630 / 1200  # a centre crop to 1200 x 630 keeps this many of the 1350 rows
    cut = round((1350 - keep) / 2 * k)
    wide = render(src, 900, 1125)
    kw = wide.width / 1080
    wide = wide.crop((0, round((1350 - keep) / 2 * kw), wide.width, round((1350 + keep) / 2 * kw)))

    def draw(fr, t):
        s = ease(t, 4.4, 5.6)
        x, y = card(fr, big, lerp(W / 2, 470, s), 470)
        label(fr, (x, y + big.height + 22), "Source poster, 1080 × 1350")
        a = ease(t, 2.8, 3.8)
        if a:
            d = ImageDraw.Draw(fr, "RGBA")
            for y0, y1 in ((y, y + cut), (y + big.height - cut, y + big.height)):
                d.rectangle([x, y0, x + big.width, y1], fill=(0, 0, 0, int(160 * a)))
            for i in range(x, x + big.width, 24):  # dashed crop line
                for yy in (y + cut, y + big.height - cut):
                    d.line([(i, yy), (i + 12, yy)], fill=(255, 255, 255, int(230 * a)), width=3)
        for t0, ids, text in ((7.6, {"logo"}, "logo: cut"), (8.6, {"cta_bg"}, "button: cut"),
                              (9.6, {"product"}, "bottle: half gone")):
            if t > t0:
                mark(fr, to_screen((x, y), k, el_box(src, ids)), RED, text, alpha=ease(t, t0, t0 + 0.4),
                     beside="product" in ids)
        if s:
            qx, qy = card(fr, wide, lerp(W + wide.width, 1290, s), 470)
            label(fr, (qx, qy + wide.height + 22), "Centre-cropped to 1200 × 630")
    return "01_crop", 0, draw


def scene_stretch():
    src = task("chameleon_sample_level2_core")
    left = render(src, 400, 400)
    sx, sy = 1600 / 1080, 400 / 1080
    els = [dict(e, x=e["x"] * sx, y=e["y"] * sy, width=e["width"] * sx, height=e["height"] * sy,
                **({"font_size": max(1, e["font_size"] * min(sx, sy))} if e["type"] == "text" else {}))
           for e in src["elements"]]
    right = render({"canvas": {"width": 1600, "height": 400}, "elements": els}, 1120, 280)

    def draw(fr, t):
        x, y = card(fr, left, 370, 470)
        label(fr, (x, y + left.height + 22), "Source post, 1080 × 1080")
        a = ease(t, 1.0, 1.8)
        if a:
            rx, ry = card(fr, right, 1290, 470, alpha=a)
            label(fr, (rx, ry + right.height + 22), "Stretched to a 1600 × 400 banner",
                  color=tuple(int(lerp(14, c, a)) for c in RED))
    return "02_stretch", 0, draw


def scene_morph():
    src = task("chameleon_sample_level2_core")
    m = Morph(src, [(3.6, 5.8, resize(src, 1600, 400))], (1500, 640))

    def draw(fr, t):
        img = m.image(t)
        x, y = card(fr, img, W / 2, 470)
        done = t > 5.8
        label(fr, (x, y + img.height + 22), "Chameleon, 1600 × 400" if done else "Source post, 1080 × 1080",
              color=ACCENT if done else MUTED)
    return "03_morph", 0, draw


def scene_tour():
    src = task("chameleon_sample_level2_core")
    banner, story, ad, square = (resize(src, w, h) for w, h in ((1600, 400), (1080, 1920), (728, 90), (1080, 1080)))
    m = Morph(banner, [(0.5, 1.7, story), (3.0, 4.2, ad), (5.0, 6.2, square)], (1500, 680))
    names = ["Web banner, 1600 × 400", "Phone story, 1080 × 1920", "Leaderboard ad, 728 × 90",
             "Instagram post, 1080 × 1080"]

    def draw(fr, t):
        img = m.image(t)
        x, y = card(fr, img, W / 2, 470)
        if not any(t0 < t < t1 for t0, t1, _ in m.keys):
            label(fr, (x, y + img.height + 22), names[sum(t >= t1 for _, t1, _ in m.keys)], color=ACCENT)
    return "04_tour", 7.6, draw


def scene_how():
    src, canvas = task("chameleon_sample_level2_core"), {"width": 1600, "height": 400}
    eng = R._Engine(src, canvas)
    out = R.resize(src, canvas)
    names = {"hero": "product", "cta": "button", "text": "subhead", "badge": "badge", "logo": "logo",
             "image": "picture", "fine": "small print"}
    units = sorted((u for u in eng.content if u.role in names or u is eng.headline), key=lambda u: u.ink[1])
    tags = [("headline" if u is eng.headline else names[u.role], {e["id"] for e in u.els}, u) for u in units]
    left, right = render(src, 560, 560), render(out, 1060, 265)
    kl, kr = scale_of(src, 560, 560), scale_of(out, 1060, 265)

    def draw(fr, t):
        lx, ly = card(fr, left, 380, 450)
        rx, ry = card(fr, right, 1330, 450)
        label(fr, (lx, ly + left.height + 22), "What the engine sees in the source")
        label(fr, (rx, ry + right.height + 22), "Where each piece goes at 1600 × 400")
        d = ImageDraw.Draw(fr, "RGBA")
        f = font(26, True)
        lx0 = W / 2 - sum(f.getlength(n) + 70 for n, _, _ in tags) / 2  # a legend, so tags never collide
        for i, (name, ids, u) in enumerate(tags):
            a = ease(t, 1.0 + i * 0.7, 1.4 + i * 0.7)
            c = ROLE_COLORS[i % len(ROLE_COLORS)]
            if a:
                mark(fr, to_screen((lx, ly), kl, u.ink), c, None, width=3, alpha=a)
                mark(fr, to_screen((rx, ry), kr, el_box(out, ids)), c, None, width=3, alpha=a)
                d.rounded_rectangle([lx0, 852, lx0 + 22, 874], radius=5, fill=c + (int(255 * a),))
                d.text((lx0 + 34, 863), name, font=f, fill=WHITE + (int(255 * a),), anchor="lm")
            lx0 += f.getlength(name) + 70
    return "05_how", 0, draw


def scene_hard():
    src = task("chameleon_sample_level3_advanced")
    m = Morph(src, [(10.4, 12.8, resize(src, 728, 90))], (1600, 660))
    k = scale_of(src, 1600, 660)
    notes = [(3.0, "headline", {"headline"}, "150-character headline"),
             (5.6, "badges", {"badge1_bg", "badge2_bg", "badge3_bg"}, "three badges"),
             (6.6, "ribbon", {"ribbon_bg", "ribbon_label"}, "rotated ribbon"),
             (7.6, "missing", {"holiday_watermark"}, "missing image file")]

    def draw(fr, t):
        img = m.image(t)
        x, y = card(fr, img, W / 2, 470)
        if t < 10.4:
            fade = 1 - ease(t, 9.6, 10.3)
            for i, (t0, _, ids, text) in enumerate(notes):
                a = ease(t, t0, t0 + 0.4) * fade
                if a:
                    mark(fr, to_screen((x, y), k, el_box(src, ids)), ROLE_COLORS[i], text, alpha=a)
        elif t > 12.8:
            label(fr, (x, y + img.height + 22), "Leaderboard ad, 728 × 90: the badges, fine print and ribbon stepped aside",
                  color=ACCENT)
    return "06_hard", 0, draw


def scene_bug():
    before = Image.open(DEMO / "bug_before.png").convert("RGB")
    after = render(resize(task("chameleon_sample_level3_advanced"), 336, 280), 336, 280)
    before, after = (im.resize((im.width * 2, im.height * 2), Image.LANCZOS) for im in (before, after))

    def draw(fr, t):
        bx, by = card(fr, before, 560, 450)
        label(fr, (bx, by - 44), "Before the fix", size=28, color=WHITE, bold=True)
        label(fr, (bx, by + before.height + 22), "Medium rectangle, 336 × 280")
        a = ease(t, 2.4, 2.9)
        if a:
            ImageDraw.Draw(fr, "RGBA").ellipse([bx + 230, by - 22, bx + before.width + 26, by + 128],
                                               outline=RED + (int(255 * a),), width=6)
        a = ease(t, 6.0, 6.6)
        if a:
            ax, ay = card(fr, after, 1360, 450, alpha=a)
            label(fr, (ax, ay - 44), "After", size=28, color=tuple(int(lerp(40, c, a)) for c in WHITE), bold=True)
            label(fr, (ax, ay + after.height + 22), "Ribbon steps aside; the logo stays clear",
                  color=tuple(int(lerp(40, c, a)) for c in ACCENT))
    return "07_bug", 0, draw


def scene_grid():
    src = task("chameleon_sample_level2_core")
    formats = [("Instagram", 1080, 1080), ("Story", 1080, 1920), ("Facebook", 1200, 630), ("X header", 1500, 500),
               ("YouTube", 1280, 720), ("LinkedIn", 1584, 396), ("Pinterest", 1000, 1500), ("Leaderboard", 728, 90),
               ("Billboard", 970, 250), ("Med. rectangle", 300, 250), ("Half page", 300, 600), ("Email", 600, 200)]
    cw, chh = 380, 196
    cells = [(f"{n}  {w} × {h}", render(resize(src, w, h), cw, chh)) for n, w, h in formats]

    def draw(fr, t):
        for i, (text, img) in enumerate(cells):
            a = ease(t, 0.4 + i * 0.3, 0.8 + i * 0.3)
            if a:
                cx, cy = 300 + (i % 4) * 440, 190 + (i // 4) * 262
                x, y = card(fr, img, cx, cy, alpha=a)
                label(fr, (cx, cy + chh / 2 + 14), text, size=20, anchor="ma",
                      color=tuple(int(lerp(14, c, a)) for c in MUTED))
    return "08_grid", 0, draw


def scene_addon():
    shot = Image.open(DEMO / "addon_panel.png").convert("RGB")

    def draw(fr, t):
        z = lerp(0.70, 0.76, t / 10)
        img = shot.resize((round(shot.width * z), round(shot.height * z)), Image.BICUBIC)
        card(fr, img, W / 2, 470 - lerp(0, 24, t / 10))
    return "09_addon", 0, draw


def scene_outro():
    def draw(fr, t):
        ImageDraw.Draw(fr).text((W / 2, 400), "Chameleon", font=font(150, True, serif=True), fill=WHITE, anchor="mm")
        ImageDraw.Draw(fr).text((W / 2, 520), "Any design. Every canvas.", font=font(54, serif=True), fill=ACCENT,
                                anchor="mm")
        label(fr, (W / 2, 640), "github.com/UditDewan/chameleon-adobe-hackathon", size=28, anchor="mm")
    return "10_outro", 6.0, draw


SCENES = [scene_crop, scene_stretch, scene_morph, scene_tour, scene_how, scene_hard, scene_bug, scene_grid,
          scene_addon, scene_outro]
STILLS = [("01_crop", 10.2, "crop"), ("02_stretch", 5.0, "stretch"), ("03_morph", 8.0, "banner"),
          ("05_how", 9.0, "how"), ("06_hard", 9.0, "hard"), ("06_hard", 15.0, "hard_result"),
          ("07_bug", 9.0, "bug"), ("08_grid", 6.0, "grid")]  # caption-free frames for the README and deck


# --------------------------------------------------------------------------- audio
def ffmpeg_exe():
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return shutil.which("ffmpeg") or sys.exit("needs ffmpeg on PATH (or pip install imageio-ffmpeg)")


def duration(ff, path):
    err = subprocess.run([ff, "-i", str(path)], capture_output=True, text=True).stderr
    h, m, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def music(seconds, path, sr=44100):
    """A quiet Am-F-C-G pad with a soft arpeggio on top."""
    n = int(seconds * sr)
    tt = np.arange(n) / sr
    out = np.zeros(n)
    hz = lambda m: 440 * 2 ** ((m - 69) / 12)  # noqa: E731
    chords = [[45, 60, 64, 67, 69], [41, 57, 60, 64, 65], [48, 60, 64, 67, 71], [43, 59, 62, 67, 69]]
    bar = 4.0
    for i, start in enumerate(np.arange(0, seconds, bar)):
        ch = chords[i % 4]
        s0, s1 = int(start * sr), min(n, int((start + bar + 1.5) * sr))
        t = tt[s0:s1] - start
        env = np.minimum(1, t / 1.5) * np.clip((bar + 1.5 - t) / 1.5, 0, 1)
        for note in ch[1:4]:
            for det in (-0.08, 0.08):  # two slightly detuned voices per note: a soft chorus
                out[s0:s1] += 0.05 * env * np.sin(2 * np.pi * hz(note + det) * t)
        out[s0:s1] += 0.07 * env * np.sin(2 * np.pi * hz(ch[0]) * t)
        for j, idx in enumerate([1, 2, 3, 4, 3, 2, 4, 3]):
            a0 = int((start + j * 0.5) * sr)
            if a0 >= n:
                break
            a1 = min(n, a0 + int(1.8 * sr))
            ta = tt[a0:a1] - tt[a0]
            f = hz(ch[idx] + 12)
            out[a0:a1] += (0.03 * np.minimum(1, ta / 0.006) * np.exp(-ta * 2.6) *
                           (np.sin(2 * np.pi * f * ta) + 0.25 * np.sin(4 * np.pi * f * ta)))
    out *= np.minimum(1, tt / 2.0) * np.clip((seconds - tt) / 3.0, 0, 1)
    out = (out / np.abs(out).max() * 0.3 * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(np.repeat(out, 2).tobytes())


# --------------------------------------------------------------------------- main
def main():
    ff = ffmpeg_exe()
    lines = {x["id"]: x["say"] for x in json.loads((DEMO / "narration.json").read_text(encoding="utf-8"))}
    plan, start = [], 0.0
    for make in SCENES:
        vid, min_len, draw = make()
        voice = DEMO / "voice" / f"{vid}.mp3"
        vlen = duration(ff, voice)
        length = max(min_len, LEAD + vlen + TAIL)
        sents = re.split(r"(?<=[.!?])\s+", lines[vid])
        weights = np.cumsum([len(s) + 12 for s in sents])  # sentence timing ~ its length plus a pause
        cues = [(LEAD + vlen * (wt - (len(s) + 12)) / weights[-1], s) for wt, s in zip(weights, sents)]
        plan.append((vid, start, length, draw, voice, cues))
        start += length
    total = start

    (DEMO / "stills").mkdir(exist_ok=True)
    video = TMP / "video.mp4"
    proc = subprocess.Popen([ff, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
                             "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "21",
                             "-preset", "medium", str(video)], stdin=subprocess.PIPE)
    prev = None
    for vid, s0, length, draw, _, cues in plan:
        for i in range(round(length * FPS)):
            t = i / FPS
            fr = BACK.copy()
            draw(fr, t)
            for _, _, name in (s for s in STILLS if s[0] == vid and round(s[1] * FPS) == i):
                fr.save(DEMO / "stills" / f"{name}.png")
            text = next((s for c, s in reversed(cues) if t >= c), None)
            if text and t < length - 0.25:
                caption(fr, text)
            if vid == "10_outro":
                fr = Image.blend(fr, Image.new("RGB", (W, H)), ease(t, length - 1.2, length))
            if prev is not None and t < FADE:
                fr = Image.blend(prev, fr, ease(t, 0, FADE))
            proc.stdin.write(fr.tobytes())
            last = fr
        prev = last
        print(f"{vid}: {length:.1f}s")
    proc.stdin.close()
    if proc.wait() != 0:
        raise SystemExit("ffmpeg failed")

    bed = TMP / "music.wav"
    music(total, bed)
    inputs, chains = ["-i", str(video), "-i", str(bed)], []
    for i, (_, s0, _, _, voice, _) in enumerate(plan):
        inputs += ["-i", str(voice)]
        ms = int((s0 + LEAD) * 1000)
        chains.append(f"[{i + 2}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={ms}:all=1[v{i}]")
    vox = "".join(f"[v{i}]" for i in range(len(plan)))
    graph = ";".join(chains + [
        f"{vox}amix=inputs={len(plan)}:normalize=0,volume=1.4,asplit[vo1][vo2]",
        # the bed sits ~16 dB under the voice and ducks further while someone is talking
        "[1:a]volume=0.22[mus];[mus][vo1]sidechaincompress=threshold=0.015:ratio=6:attack=40:release=700[bed]",
        "[bed][vo2]amix=inputs=2:normalize=0,alimiter=limit=0.95[a]"])
    out = DEMO / "chameleon_demo.mp4"
    subprocess.run([ff, "-y", "-loglevel", "error", *inputs, "-filter_complex", graph, "-map", "0:v", "-map", "[a]",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-t", f"{total:.2f}", "-movflags", "+faststart",
                    str(out)], check=True)
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB, {total:.0f}s)")


if __name__ == "__main__":
    main()
