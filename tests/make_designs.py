"""Writes the synthetic stress designs in tests/designs/ (run once; the JSON is committed)."""
import json
from pathlib import Path

OUT = Path(__file__).parent / "designs"
WHITE, PLUM, ORANGE = "rgba(255,255,255,1)", "rgba(43,27,46,1)", "rgba(232,106,58,1)"
BG = {"id": "bg", "type": "image", "x": 0, "y": 0, "z_index": 0, "asset_id": "solstice_bg_gradient.png", "fit": "cover"}


def img(id, x, y, w, h, asset="solstice_bottle.png", z=2, **kw):
    return {"id": id, "type": "image", "x": x, "y": y, "width": w, "height": h, "z_index": z,
            "asset_id": asset, "fit": "contain", **kw}


def text(id, x, y, w, h, content, size, family="Solstice Sans", z=4, color=WHITE, align="center", **kw):
    return {"id": id, "type": "text", "x": x, "y": y, "width": w, "height": h, "z_index": z, "content": content,
            "font_family": family, "font_size": size, "color": color, "text_align": align, **kw}


def button(id, x, y, w, h, label, size, fill=ORANGE, shape="rectangle", **kw):
    ty = y + (h - size * 1.3) / 2
    return [{"id": f"{id}_bg", "type": "shape", "x": x, "y": y, "width": w, "height": h, "z_index": 3,
             "shape_type": shape, "fill_color": fill, "corner_radius": h / 2, "group_id": id, **kw},
            text(f"{id}_label", x, ty, w, size * 1.3, label, size, font_weight="bold", group_id=id,
                 **{k: v for k, v in kw.items() if k == "rotation"})]


def task(name, w, h, elements, target):
    bg = [dict(BG, width=w, height=h)] if elements and elements[0] == "bg" else []
    els = bg + [e for e in elements if e != "bg"]
    flat = []
    for e in els:
        flat += e if isinstance(e, list) else [e]
    (OUT / f"{name}.json").write_text(json.dumps(
        {"task_id": name, "source": {"canvas": {"width": w, "height": h}, "elements": flat},
         "target_canvas": {"width": target[0], "height": target[1]}}, indent=1))


OUT.mkdir(exist_ok=True)

task("landscape_source", 1200, 628, ["bg",
     img("product", 40, 44, 540, 540),
     img("logo", 640, 60, 240, 80, "solstice_logo_lockup.png", 3),
     text("headline", 640, 170, 500, 140, "Golden Hour, Bottled", 58, "Solstice Serif", align="left", font_weight="bold"),
     text("body", 640, 330, 480, 90, "Wild honey, blood orange and smoked rosemary: small-batch sparkling botanicals.",
          22, align="left", color="rgba(255,255,255,0.85)"),
     button("cta", 640, 460, 300, 70, "Find a Store", 22)], (1080, 1920))

task("text_only_left", 1080, 1080, [
     {"id": "bg", "type": "shape", "x": 0, "y": 0, "width": 1080, "height": 1080, "z_index": 0,
      "shape_type": "rectangle", "fill_color": PLUM},
     {"id": "accent", "type": "shape", "x": 80, "y": 150, "width": 120, "height": 8, "z_index": 1,
      "shape_type": "rectangle", "fill_color": ORANGE},
     text("eyebrow", 80, 180, 600, 40, "LIMITED SUMMER RELEASE", 24, align="left", color=ORANGE, font_weight="bold"),
     text("headline", 80, 240, 900, 330, "Three botanicals. One golden hour. Zero compromises.", 84,
          "Solstice Serif", align="left", font_weight="bold"),
     text("body", 80, 620, 760, 120, "Our summer collection blends wild honey, blood orange and smoked rosemary "
          "into a sparkling drink made for long evenings.", 28, align="left", color="rgba(255,255,255,0.8)"),
     text("link", 80, 800, 500, 40, "solstice.com/summer", 26, align="left", color=ORANGE, font_weight="bold"),
     text("footer", 80, 1000, 700, 30, "(c) 2026 Solstice Botanical Co. All rights reserved.", 16, align="left",
          color="rgba(255,255,255,0.5)"),
     img("logo", 820, 960, 210, 70, "solstice_logo_lockup.png", 3)], (1584, 396))

task("multi_product", 1080, 1350, ["bg",
     img("logo", 390, 50, 300, 100, "solstice_logo_lockup.png", 3),
     text("headline", 90, 180, 900, 80, "The Summer Trio", 64, "Solstice Serif", font_weight="bold"),
     text("sub", 140, 270, 800, 40, "Three flavors, one sunset.", 26, color="rgba(255,255,255,0.85)"),
     img("p1", 60, 360, 320, 320), img("p2", 380, 330, 320, 380), img("p3", 700, 360, 320, 320),
     button("price1", 280, 400, 96, 96, "$4.99", 22, shape="ellipse"),
     button("price2", 600, 370, 96, 96, "$5.49", 22, shape="ellipse"),
     button("price3", 920, 400, 96, 96, "$4.99", 22, shape="ellipse"),
     text("n1", 60, 700, 320, 40, "Blood Orange", 26, font_weight="bold"),
     text("n2", 380, 720, 320, 40, "Wild Honey", 26, font_weight="bold"),
     text("n3", 700, 700, 320, 40, "Rosemary", 26, font_weight="bold"),
     button("cta", 340, 1000, 400, 86, "Shop the Trio", 26),
     text("legal", 90, 1290, 900, 24, "Prices valid through Aug 31, 2026 while supplies last.", 14,
          color="rgba(255,255,255,0.6)")], (1200, 630))

task("card_panel", 1080, 1080, ["bg",
     {"id": "panel", "type": "shape", "x": 90, "y": 330, "width": 900, "height": 640, "z_index": 1,
      "shape_type": "rectangle", "fill_color": "rgba(255,248,240,0.94)", "corner_radius": 40},
     img("product", 390, 40, 300, 360, z=2),
     text("headline", 150, 440, 780, 150, "Sparkling, Not Sweet", 64, "Solstice Serif", color=PLUM, font_weight="bold"),
     {"id": "divider", "type": "shape", "x": 440, "y": 610, "width": 200, "height": 4, "z_index": 2,
      "shape_type": "line", "fill_color": ORANGE, "stroke_color": ORANGE, "stroke_width": 4},
     text("body", 170, 650, 740, 110, "Only 40 calories, no added sugar, and a finish of smoked rosemary.", 26,
          color="rgba(43,27,46,0.85)"),
     button("cta", 340, 820, 400, 86, "Taste the Difference", 24),
     img("logo", 840, 990, 200, 70, "solstice_logo_lockup.png", 3)], (728, 90))

task("story_source", 1080, 1920, ["bg",
     img("logo", 390, 80, 300, 100, "solstice_logo_lockup.png", 3),
     img("product", 190, 260, 700, 700),
     text("headline", 90, 1020, 900, 240, "Made for the Golden Hour", 88, "Solstice Serif", font_weight="bold"),
     text("sub", 140, 1290, 800, 90, "Botanical sparkling infusions with wild honey and blood orange.", 30,
          color="rgba(255,255,255,0.85)"),
     button("cta1", 240, 1450, 600, 110, "Shop Now", 34),
     button("cta2", 240, 1590, 600, 110, "Find a Store", 34, fill="rgba(255,255,255,0.12)",
            stroke_color=WHITE, stroke_width=2),
     text("legal", 90, 1840, 900, 30, "Enjoy responsibly.", 18, color="rgba(255,255,255,0.6)")], (1080, 1080))

task("adversarial", 1080, 1080, [
     {"id": "bg", "type": "shape", "x": 0, "y": 0, "width": 1080, "height": 1080, "z_index": 0,
      "shape_type": "rectangle", "fill_color": "rgba(30,20,35,1)"},
     {"id": "ring", "type": "shape", "x": -200, "y": 600, "width": 700, "height": 700, "z_index": 1,
      "shape_type": "ellipse", "fill_color": ORANGE, "opacity": 0.25},
     img("logo", 60, 50, 240, 80, "solstice_logo_lockup.png", 3),
     button("sticker", 820, 40, 200, 200, "50% OFF", 40, shape="ellipse", rotation=15),
     img("hero", 290, 170, 500, 500, "solstice_bottle_MISSING.png"),
     text("headline", 90, 690, 900, 120, "Supercalifragilisticexpialidociousbotanicalsparkling", 60,
          "Solstice Serif", font_weight="bold"),
     text("url", 90, 820, 900, 40, "https://solstice.example.com/summer-collection-2026/limited?utm_source=chameleon", 22),
     text("empty", 100, 870, 300, 40, "", 20),
     {"id": "zero", "type": "shape", "x": 500, "y": 500, "width": 0, "height": 0, "z_index": 2,
      "shape_type": "rectangle", "fill_color": ORANGE},
     {"id": "offcanvas", "type": "shape", "x": 1200, "y": 1200, "width": 100, "height": 100, "z_index": 2,
      "shape_type": "rectangle", "fill_color": ORANGE},
     text("tilted", 340, 900, 400, 50, "Limited time only", 28, rotation=-5),
     {"id": "rule", "type": "shape", "x": 240, "y": 960, "width": 600, "height": 3, "z_index": 2,
      "shape_type": "line", "fill_color": WHITE, "stroke_color": WHITE, "stroke_width": 3},
     *[button(f"b{i}", 40 + 210 * i, 1000, 180, 44, lbl, 16) for i, lbl in
       enumerate(["Vegan", "Gluten Free", "Non-GMO", "Low Calorie", "Organic Certified Ingredients"])]],
     (300, 250))

task("no_background", 800, 800, [
     img("logo", 280, 40, 240, 80, "solstice_logo_lockup.png", 3),
     text("headline", 50, 200, 700, 200, "Chase the Light", 72, "Helvetica Neue", color=PLUM, font_weight="bold"),
     img("product", 250, 400, 300, 300),
     button("cta", 250, 720, 300, 60, "Shop Now", 24)], (970, 250))
print("wrote", len(list(OUT.glob("*.json"))), "designs to", OUT)
