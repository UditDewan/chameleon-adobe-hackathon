# Chameleon — any design, every canvas

**The problem.** A brand idea has to live as a square post, a tall story, a wide web banner, a
728×90 ad and a dozen more shapes. Today a designer rebuilds it by hand for each one — or uses a
resize that stretches or crops, and loses the logo, the headline or the call to action.

**The insight.** Resizing is not a picture problem, it's a *furniture* problem: keep the pieces,
re-place them. To do that well you need three things a stretch never has — what belongs together,
what matters most, and exactly how text will wrap.

**What we built.**
1. **An engine** (`engine/resize.py`) that turns any design into a new layout for any canvas in
   milliseconds. It recovers structure with no labels (buttons, stickers on products, captions,
   cards, corner badges, reading order), chooses a layout family for the target shape — stacked,
   split, or banner row — and solves the largest legible scale, measuring every line with the
   renderer's own fonts. When space runs out, the least important element steps aside — only if
   that keeps everything else readable.
2. **An Adobe Express add-on** that runs it on the page you're working on: pick formats, and each
   becomes a real, editable page — text stays text, nothing is flattened, set-aside elements are
   hidden, not deleted.
3. **An HTTP service + browser viewer**, so any tool can call the engine.

**Proof.**
- 3 official samples, 7 adversarial stress designs, 20 real formats → 200 resizes, every hard rule
  asserted (ids, in-canvas incl. rotation, image proportions, unchanged copy, text fits its box,
  using the renderer's own wrap as the oracle).
- 1,500 randomly generated designs → no crash, no rule violation, no fallback.
- 1–150 ms per layout, deterministic, offline, no model, no pixels generated.

**Why it matters for Express.** "Resize" is the feature every Express user reaches for and the one
that disappoints most on real designs. Chameleon makes it trustworthy: one click, every format,
on-brand — and it explains itself ("banner row · 3 secondary elements set aside").

**Next.** Several candidate layouts per format to choose from; lock / pin elements; a vision model
for focal-point crops and role hints; copy that shortens itself for tiny ad slots — all on top of
the same engine.

*Demo: `demo/chameleon_demo.mp4` (1 min).*
