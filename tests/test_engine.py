"""Every design (samples + tests/designs) x a catalogue of real formats must satisfy the
hard rules. Uses the renderer's own wrap as the oracle for text.

    python -m tests.test_engine        (or: pytest tests/)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import engine.resize as R  # noqa: E402
from renderer.converter.brand_fonts import load_brand_font_manager  # noqa: E402
from renderer.converter.wrap import char_widths, wrap_text  # noqa: E402

FORMATS = [(1080, 1080), (1080, 1350), (1080, 1920), (1200, 630), (820, 312), (1600, 900), (1500, 500),
           (1280, 720), (1584, 396), (1000, 1500), (728, 90), (970, 250), (300, 250), (160, 600),
           (300, 600), (320, 50), (320, 100), (1920, 1080), (600, 200), (1600, 400)]
FM = load_brand_font_manager(str(ROOT / "brand_kit/fonts"))


def rendered_lines(e):
    bold = str(e.get("font_weight")) == "bold"
    italic = e.get("font_style") == "italic"
    style = "bolditalic" if bold and italic else "bold" if bold else "italic" if italic else "regular"
    widths = char_widths(FM, e["font_family"], "bold" if bold else "regular", style, float(e["font_size"]), e["content"])
    return wrap_text(e["content"], widths, float(e["width"])).split("\n"), widths


def check(src, W, H):
    out = R.resize(src, {"width": W, "height": H})
    assert out["canvas"] == {"width": W, "height": H}
    assert sorted(e["id"] for e in out["elements"]) == sorted(e["id"] for e in src["elements"]), "ids changed"
    by_id = {e["id"]: e for e in src["elements"]}
    for e in out["elements"]:
        s, where = by_id[e["id"]], f"{e['id']} @ {W}x{H}"
        x0, y0, x1, y1 = R._union([R._box(e), R._aabb(e)])
        assert x0 >= -0.5 and y0 >= -0.5 and x1 <= W + 0.5 and y1 <= H + 0.5, f"off-canvas: {where}"
        if e["type"] == "image" and e.get("fit") != "cover" and min(s["width"], s["height"], e["height"]) > 0:
            ratio = (e["width"] / e["height"]) / (s["width"] / s["height"])
            assert abs(ratio - 1) <= 0.05, f"image out of proportion: {where}"
        if e["type"] == "text":
            assert e["font_size"] >= 1 and e["font_size"] == int(e["font_size"]), f"font size: {where}"
            assert e["content"] == s["content"] and e["font_family"] == s["font_family"], f"copy changed: {where}"
            if not e["content"].strip() or str(e.get("color", "")).replace(" ", "").endswith(",0)"):
                continue  # empty or dropped
            lines, widths = rendered_lines(e)
            pos, longest = 0, 0.0
            for line in lines:
                longest = max(longest, sum(widths[pos:pos + len(line)]))
                pos += len(line) + 1
            unbreakable = max(len(w) for w in e["content"].split()) > 30
            assert longest <= e["width"] + 0.5 or unbreakable, f"word overflows its box: {where}"
            block = (len(lines) - 1) * e["font_size"] * float(e.get("line_height") or 1.2) + e["font_size"] * 1.1
            assert block <= e["height"] + 1, f"text taller than its box: {where}"


def test_all():
    fallbacks = []
    real = R._letterbox
    R._letterbox = lambda *a: fallbacks.append(a) or real(*a)
    try:
        files = sorted((ROOT / "input").glob("*.json")) + sorted((ROOT / "tests/designs").glob("*.json"))
        assert files, "no designs found"
        for f in files:
            src = json.loads(f.read_text())["source"]
            for W, H in FORMATS:
                check(src, W, H)
        assert not fallbacks, f"{len(fallbacks)} layouts fell back to letterboxing"
        return len(files) * len(FORMATS)
    finally:
        R._letterbox = real


if __name__ == "__main__":
    n = test_all()
    print(f"ok: {n} resizes satisfy every hard rule")
