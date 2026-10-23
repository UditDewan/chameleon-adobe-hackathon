# Chameleon — Adobe Express add-on

A panel that runs the CHAMELEON engine on real Express documents.

- **This page (live resize)** — reads the current page (text, shapes, images, groups),
  asks the engine for a layout per chosen format, then for each one clones the page at the
  new size and moves / resizes the *real* nodes. Text stays editable; nothing is flattened.
  Elements the engine sets aside are hidden (opacity 0), not deleted — one click brings them back.
- **Sample designs / Open JSON…** — any CHAMELEON design: the engine lays it out and renders it,
  and *Add as page* drops each layout into the document as a new page of the right size.
- Every result card says what the engine decided ("banner row · 3 secondary elements set aside").

Built from Adobe's official `javascript-with-document-sandbox` template
(`@adobe/create-ccweb-add-on` 3.11): `src/ui` is the panel, `src/sandbox/code.js` is the only
code that touches the document (Express Document API: `PageNode.cloneInPlace`, page `width`/`height`,
`Node.resize`, `setPositionInParent`, `TextNode.fullContent.applyCharacterStyles`,
`editor.loadBitmapImage` + `createImageContainer` inside `keepContentActiveDuringAsync`).

## Run it

```bash
# 1. the engine (from the repo root)
python -m engine.server                 # http://localhost:8787

# 2. the add-on dev server
cd addon
npm install
npx @adobe/ccweb-add-on-ssl setup --hostname localhost   # once per machine: trusted local certificate
npm start                               # serves the add-on over https://localhost:5241
```

3. In Adobe Express: *Settings → enable Add-on Development*, then *Add-ons → Your add-ons →
   Add-on Testing*, and connect to `https://localhost:5241`.

`npm run build` / `npm run package` (→ `dist.zip`, the bundle you upload to Adobe) both pass with
Adobe's tooling; append `-- --analytics off` to skip its analytics prompt.

The same panel also works in any browser without Express: start the engine and open
<http://localhost:8787/viewer/> — the cards then offer PNG / JSON downloads instead.
Deep links preselect a sample and run it, e.g. `/viewer/#sample=chameleon_sample_level2_core&run`.

## Status

The browser mode is tested end to end (headless Chrome against the running engine). The
Express-side calls were written against the official type definitions
(`@adobe/ccweb-add-on-sdk-types` 1.40) but have not been run inside Express from this machine;
treat *live resize* as beta. Fonts in an Express page that aren't in the brand kit are measured
with a fallback, so Express may wrap a line differently from the engine's preview.
