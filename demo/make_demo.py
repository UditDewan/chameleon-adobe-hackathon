"""Renders the demo video from real engine output (official renderer, brand fonts).

    python demo/make_demo.py            -> demo/chameleon_demo.mp4  (needs: pip install imageio-ffmpeg)
                                           demo/chameleon_demo.gif  (fallback, Pillow only)
Dev tooling only; the engine does not depend on it.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.resize import resize  # noqa: E402
from renderer.converter.convert import render_design_to_png  # noqa: E402

W, H, FPS = 1920, 1080, 30
INK, MUTED, PAPER, ACCENT, GOOD, BAD = "#1f1d2b", "#6b6880", "#f6f4f9", "#e86a3a", "#2e9e5b", "#c63b3b"
FONTS = ROOT / "brand_kit" / "fonts"
TMP = Path(tempfile.mkdtemp(prefix="chameleon_demo_"))


def font(size, bold=False, serif=False):
    name = ("SolsticeSerif-" if serif else "SolsticeSans-") + ("Bold" if bold else "Regular")
    return ImageFont.truetype(str(FONTS / f"{name}.ttf"), size)


def task(name):
    p = next(p for d in ("input", "tests/designs") for p in (ROOT / d).glob(f"{name}.json"))
    return json.loads(p.read_text())["source"]


_n = 0


def render(design):
    global _n
    _n += 1
    path = TMP / f"r{_n}.png"
    render_design_to_png(design, ROOT / "brand_kit" / "assets", path)
    return Image.open(path).convert("RGB")


def stretched(src, w, h):
    """The naive baseline the brief warns about: every box scaled by x and y separately."""
    sx, sy = w / src["canvas"]["width"], h / src["canvas"]["height"]
    els = []
    for e in src["elements"]:
        e = dict(e, x=e["x"] * sx, y=e["y"] * sy, width=e["width"] * sx, height=e["height"] * sy)
        if e["type"] == "text":
            e["font_size"] = max(1, e["font_size"] * min(sx, sy))
        els.append(e)
    return {"canvas": {"width": w, "height": h}, "elements": els}


def fit(img, box_w, box_h):
    s = min(box_w / img.width, box_h / img.height)
    return img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)


def slide(title=None, sub=None):
    im = Image.new("RGB", (W, H), PAPER)
    d = ImageDraw.Draw(im)
    if title:
        d.text((96, 70), title, font=font(54, True, serif=True), fill=INK)
    if sub:
        d.text((98, 146), sub, font=font(28), fill=MUTED)
    d.text((W - 96, H - 56), "CHAMELEON  ·  any design, every canvas", font=font(22), fill=MUTED, anchor="rs")
    return im, d


def paste_card(im, d, img, x, y, w, h, label=None, color=MUTED, hug=False):
    pic = fit(img, w, h)
    px, py = x + (w - pic.width) // 2, y + (h - pic.height) // 2
    if hug:  # label right under the picture instead of under the cell
        y, h = py, pic.height
    d.rectangle([px - 2, py - 2, px + pic.width + 1, py + pic.height + 1], outline="#d9d5e3", width=2)
    im.paste(pic, (px, py))
    if label:  # a coloured dot rather than a check/cross glyph: the brand font has neither
        f = font(24, True)
        tw = d.textlength(label, font=f)
        lx = x + (w - tw) // 2 + (12 if color in (GOOD, BAD) else 0)
        if color in (GOOD, BAD):
            d.ellipse([lx - 26, y + h + 18, lx - 10, y + h + 34], fill=color)
        d.text((lx, y + h + 12), label, font=f, fill=color)


def title_slide():
    im, d = slide()
    d.text((W // 2, 400), "CHAMELEON", font=font(150, True, serif=True), fill=INK, anchor="mm")
    d.text((W // 2, 520), "Any design. Every canvas.", font=font(56, serif=True), fill=ACCENT, anchor="mm")
    d.text((W // 2, 620), "A layout engine that moves the furniture instead of stretching the photo",
           font=font(32), fill=MUTED, anchor="mm")
    d.text((W // 2, 690), "Adobe University Hackathon 2026  ·  Stage one", font=font(26), fill=MUTED, anchor="mm")
    return im


def problem_slide():
    src = task("chameleon_sample_level2_core")
    im, d = slide("Stretching breaks a design", "Same 1080×1080 post, resized to a 1600×400 web banner")
    paste_card(im, d, render(src), 96, 230, 420, 420, "source 1080×1080", INK)
    paste_card(im, d, render(stretched(src, 1600, 400)), 590, 250, 1230, 308, "Naive stretch", BAD)
    paste_card(im, d, render(resize(src, {"width": 1600, "height": 400})), 590, 640, 1230, 308,
               "Chameleon: re-laid-out", GOOD)
    return im


def pair_slide(name, w, h, headline, note):
    src = task(name)
    notes = {}
    out = render(resize(src, {"width": w, "height": h}, notes))
    im, d = slide(headline, note)
    sw, sh = src["canvas"]["width"], src["canvas"]["height"]
    paste_card(im, d, render(src), 96, 230, 560, 680, f"source {sw}×{sh}", INK, hug=True)
    paste_card(im, d, out, 740, 230, 1084, 680, f"{w}×{h}  ·  {notes.get('layout', '')}", GOOD, hug=True)
    d.line([(664, 570), (706, 570)], fill=ACCENT, width=7)  # drawn: the brand font has no arrow glyph
    d.polygon([(704, 554), (724, 570), (704, 586)], fill=ACCENT)
    return im


def mosaic_slide(name, headline, formats, cols=4, reveal=None):
    src = task(name)
    im, d = slide(headline, f"One source design, {len(formats)} formats, zero manual edits")
    cell_w, cell_h = (W - 192 - (cols - 1) * 28) // cols, 186
    for i, (label, w, h) in enumerate(formats[: reveal if reveal is not None else len(formats)]):
        x = 96 + (i % cols) * (cell_w + 28)
        y = 214 + (i // cols) * (cell_h + 62)
        paste_card(im, d, render(resize(src, {"width": w, "height": h})), x, y, cell_w, cell_h, f"{label} {w}×{h}")
    return im


def text_slide(headline, lines, sub=None):
    im, d = slide(headline, sub)
    y = 250
    for n, (head, body) in enumerate(lines, 1):
        d.ellipse([96, y, 146, y + 50], fill=ACCENT)
        d.text((121, y + 25), str(n), font=font(28, True), fill="white", anchor="mm")
        d.text((176, y + 2), head, font=font(36, True), fill=INK)
        d.text((176, y + 50), body, font=font(26), fill=MUTED)
        y += 132
    return im


def stats_slide():
    im, d = slide("Holds up everywhere", "Checked against every hard rule, with the renderer's own text wrap as the oracle")
    stats = [("200", "resizes in the test suite\n10 designs × 20 real formats"),
             ("1,500", "random designs fuzzed\nno crash, no rule violation"),
             ("1–150 ms", "per layout\ndeterministic, offline, no model"),
             ("0", "pixels generated\nonly geometry and type size change")]
    for i, (big, small) in enumerate(stats):
        x = 96 + i * 444
        d.rounded_rectangle([x, 300, x + 404, 720], radius=24, fill="white", outline="#e4e1ec", width=2)
        d.text((x + 202, 430), big, font=font(84, True, serif=True), fill=ACCENT, anchor="mm")
        d.multiline_text((x + 202, 560), small, font=font(28), fill=INK, anchor="mm", align="center", spacing=10)
    return im


def closing_slide():
    im, d = slide()
    d.text((W // 2, 330), "Try it", font=font(80, True, serif=True), fill=INK, anchor="mm")
    lines = ["python -m engine.main          # stage-one batch: input/ → output/",
             "python -m engine.server        # HTTP engine + browser viewer at /viewer/",
             "cd addon && npm install && npm start   # Adobe Express add-on"]
    for i, line in enumerate(lines):
        d.text((W // 2, 470 + i * 70), line, font=font(34), fill=INK, anchor="mm")
    d.text((W // 2, 760), "Any design. Every canvas.", font=font(48, serif=True), fill=ACCENT, anchor="mm")
    return im


def build_slides():
    social = [("Instagram", 1080, 1080), ("Story", 1080, 1920), ("Facebook", 1200, 630), ("X header", 1500, 500),
              ("YouTube", 1280, 720), ("LinkedIn", 1584, 396), ("Pinterest", 1000, 1500), ("Leaderboard", 728, 90),
              ("Billboard", 970, 250), ("Med. rectangle", 300, 250), ("Half page", 300, 600), ("Email", 600, 200)]
    hold = []  # (image, seconds)
    hold.append((title_slide(), 3.5))
    hold.append((problem_slide(), 5.0))
    hold.append((pair_slide("chameleon_sample_level1_warmup", 1080, 1350, "Level 1 · a nearby shape",
                            "Keeps the designer's vertical rhythm on the taller canvas"), 3.5))
    hold.append((pair_slide("chameleon_sample_level2_core", 1600, 400, "Level 2 · square to wide banner",
                            "Logo | product | headline stack | call to action - groups and badges travel together"), 4.0))
    hold.append((pair_slide("chameleon_sample_level3_advanced", 728, 90, "Level 3 · poster to a 728×90 leaderboard",
                            "Run-on headline balanced into 4 lines; secondary copy set aside; missing asset skipped"), 4.5))
    for k in range(4, len(social) + 1, 4):  # the mosaic fills in, four at a time
        hold.append((mosaic_slide("chameleon_sample_level2_core", "One design, every canvas", social, reveal=k),
                     1.6 if k < len(social) else 3.5))
    hold.append((mosaic_slide("chameleon_sample_level3_advanced", "Even the hardest sample", social), 4.0))
    for name, headline in (("multi_product", "Three products, price stickers and captions"),
                           ("card_panel", "A card panel re-wrapped around its content"),
                           ("text_only_left", "Left-aligned typography keeps its edge")):
        hold.append((mosaic_slide(name, headline, [social[i] for i in (0, 1, 2, 5, 7, 9, 10, 11)]), 3.5))
    hold.append((text_slide("How it works", [
        ("Analyse", "Groups, buttons, stickers, captions, panels, corner pins - no semantic labels needed"),
        ("Order", "An XY-cut recovers reading order, side-by-side rows and each unit's column"),
        ("Lay out", "Stack, split or banner row at the largest scale that fits - text measured with the renderer's fonts"),
        ("Degrade gracefully", "Least important units step aside only when that keeps the rest legible"),
        ("Finish", "Full-bleed crops, decoration anchoring, everything in canvas, images in proportion")]), 6.0))
    hold.append((stats_slide(), 4.5))
    hold.append((closing_slide(), 4.0))
    return hold


def frames(hold, fade=0.4):
    for i, (im, secs) in enumerate(hold):
        for _ in range(int(secs * FPS)):
            yield im
        if i + 1 < len(hold):
            nxt = hold[i + 1][0]
            n = int(fade * FPS)
            for k in range(1, n + 1):
                yield Image.blend(im, nxt, k / (n + 1))


def main():
    out_dir = ROOT / "demo"
    hold = build_slides()
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        out = out_dir / "chameleon_demo.mp4"
        cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
               "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", "-preset", "medium",
               "-movflags", "+faststart", str(out)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        for f in frames(hold):
            proc.stdin.write(f.tobytes())
        proc.stdin.close()
        if proc.wait() != 0:
            raise SystemExit("ffmpeg failed")
    else:  # ponytail: GIF fallback when no ffmpeg; bigger and 960 px wide, but Pillow-only
        out = out_dir / "chameleon_demo.gif"
        small = [(im.resize((W // 2, H // 2), Image.LANCZOS), s) for im, s in hold]
        small[0][0].save(out, save_all=True, append_images=[im for im, _ in small[1:]],
                         duration=[int(s * 1000) for _, s in small], loop=0)
    for i, (im, _) in enumerate(hold):  # stills for the pitch
        if i in (1, 3, 4, 8):
            im.save(out_dir / f"still_{i:02d}.png")
    shutil.rmtree(TMP, ignore_errors=True)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB, {sum(s for _, s in hold):.0f}s of slides)")


if __name__ == "__main__":
    main()
