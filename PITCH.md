# Chameleon

Every brand idea ends up in a dozen shapes: a square post, a phone story, a wide banner, a 728 × 90 ad.
Someone rebuilds the design by hand for each one, or uses a one-click resize that crops or stretches it.
We tried that on the starter kit's poster. Cropped to a 1200 × 630 Facebook post, it lost the logo, the
button and half the bottle. Stretched into a banner, it was unreadable.

So we stopped treating a design as a picture. Chameleon keeps every element and places it again for the
new canvas, the way you'd move furniture into a room with a different shape.

## What it does

The input has no labels, so the engine works them out from geometry and type: text on a shape is a
button, the biggest type is the headline, a small picture near the top is probably the logo, and a badge
and its label move together. Then it picks a layout for the target shape (rows for square and tall
canvases, product beside text for landscape, a single row for banners) and searches for the largest size
where every line of text still fits. It measures text with the renderer's own fonts, so the line breaks
in our JSON are the ones the PNG shows.

When there isn't room, the least important pieces step aside one at a time (small print, stickers,
badges, subheads), but only if that makes the rest more readable. The logo and the headline always stay.
Nothing is deleted; it's hidden.

## What we built

- The engine, `engine/resize.py`: offline, deterministic, no model, 1 to 150 ms per layout.
- An Adobe Express add-on that turns the page you're editing into new, editable pages, one per format.
- A small HTTP service and browser viewer, so anything can call the engine.

## How we know it works

Ten designs (the three samples plus seven we wrote to break it) at twenty real formats, 200 resizes,
with every hard rule asserted after every change. Text fit is checked with the renderer's own line
wrapping. We also threw 1,500 random designs at it, and none of them crashed it or broke a rule. CI builds the Docker
image and runs it with the network off, the way the scorer will.

It isn't perfect. In our last round of checks we found the level 3 "SUNSET SALE" ribbon sitting on the
logo at 336 × 280, because stickers were allowed over pictures and the logo is a picture of a word. Now
stickers treat the logo like text, and the ribbon steps aside when there's no room.

## Next

Showing the best three layouts per format instead of one (the engine already scores them), locking an
element in place, a small vision model for photo focal points, and shorter approved copy for tiny ads.

Demo video: [`demo/chameleon_demo.mp4`](demo/chameleon_demo.mp4) (2 minutes, captions and music), or
[`demo/chameleon_demo_narrated.mp4`](demo/chameleon_demo_narrated.mp4) with a voiceover.
Deck: [`demo/chameleon_pitch.pdf`](demo/chameleon_pitch.pdf).
