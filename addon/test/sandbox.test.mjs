// Runs the add-on's real sandbox/code.js against a simulated Express document (shaped after
// @adobe/ccweb-add-on-sdk-types) and the real engine: read a page, lay it out, apply it.
//   node addon/test/sandbox.test.mjs          (PYTHON="<command>" to choose how Python runs)
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const PYTHON = [".venv/Scripts/python.exe", ".venv/bin/python"].map(p => join(ROOT, p)).find(existsSync) || "python";

// ---------------------------------------------------------------- a tiny Express scenegraph
let ids = 0;
const list = items => ({
    items, get length() { return items.length; }, get first() { return items[0]; },
    item: i => items[i], toArray: () => [...items], [Symbol.iterator]: () => items[Symbol.iterator](),
    append(n) { n.parent = this.owner; items.push(n); }
});

class Node {
    constructor(type, p = {}) { Object.assign(this, { id: `n${++ids}`, type, x: 0, y: 0, w: 1, h: 1, rotation: 0, opacity: 1 }, p); }
    get boundsLocal() { return { x: 0, y: 0, width: this.w, height: this.h }; }
    get centerPointLocal() { return { x: this.w / 2, y: this.h / 2 }; }
    origin() { return this.parent?.origin ? (({ x, y }) => ({ x: x + this.x, y: y + this.y }))(this.parent.origin()) : { x: this.x, y: this.y }; }
    boundsInNode() { // axis-aligned bounds of the rotated box
        const o = this.origin(), a = (this.rotation * Math.PI) / 180;
        const hw = (Math.abs(this.w * Math.cos(a)) + Math.abs(this.h * Math.sin(a))) / 2;
        const hh = (Math.abs(this.w * Math.sin(a)) + Math.abs(this.h * Math.cos(a))) / 2;
        return { x: o.x + this.w / 2 - hw, y: o.y + this.h / 2 - hh, width: 2 * hw, height: 2 * hh };
    }
    setPositionInParent(p, reg) { this.x = p.x - reg.x; this.y = p.y - reg.y; }
    resize({ behavior, width, height }) {
        const s = (behavior === "cover" ? Math.max : Math.min)(width / this.w, height / this.h);
        this.w *= s; this.h *= s;
    }
    clone() {
        const c = new Node(this.type, { ...this, id: undefined, parent: undefined });
        c.id = `n${++ids}`;
        if (this.children) { c.children = list([]); c.children.owner = c; this.children.items.forEach(k => c.children.append(k.clone())); }
        if (this.fullContent) c.fullContent = text(c, this.fullContent.text, { ...this.fullContent.characterStyleRanges[0] });
        return c;
    }
}
const box = (type, extra) => Object.defineProperties(new Node(type, extra), {
    width: { set(v) { this.w = v; }, get() { return this.w; } }, height: { set(v) { this.h = v; }, get() { return this.h; } },
    rx: { set(v) { this.w = 2 * v; } }, ry: { set(v) { this.h = 2 * v; } }
});
function text(node, content, style) {
    return { text: content, characterStyleRanges: [style], applyCharacterStyles(s) { Object.assign(this.characterStyleRanges[0], s); } };
}
const group = () => { const g = new Node("Group"); g.children = list([]); g.children.owner = g; return g; };

function page(w, h, name) {
    const ab = new Node("ab:Artboard", { origin: () => ({ x: 0, y: 0 }) });
    ab.children = list([]); ab.children.owner = ab;
    const p = { id: `p${++ids}`, name, width: w, height: h, artboards: list([ab]) };
    p.cloneInPlace = () => {
        const c = page(p.width, p.height, p.name);
        p.artboards.first.children.items.forEach(k => c.artboards.first.children.append(k.clone()));
        pages.items.splice(pages.items.indexOf(p) + 1, 0, c);
        editor.context.currentPage = c;
        return c;
    };
    return p;
}
const pages = list([]);
pages.addPage = ({ width, height }) => { const p = page(width, height); pages.items.push(p); editor.context.currentPage = p; return p; };

const editor = {
    context: { currentPage: null, get insertionParent() { return this.currentPage.artboards.first; } },
    documentRoot: { pages },
    loadBitmapImage: async blob => ({ bytes: blob.size }),
    keepContentActiveDuringAsync: async (target, work, after) => after(await work()),
    createImageContainer: (bitmap, { initialSize }) => box("MediaContainer", { w: initialSize.width, h: initialSize.height })
};
const constants = {
    SceneNodeType: { text: "Text", group: "Group", rectangle: "Rectangle", ellipse: "Ellipse", mediaContainer: "MediaContainer", imageRectangle: "ImageRectangle" },
    TextLayout: { autoHeight: 2 }, ResizeBehavior: { contain: "contain", cover: "cover" }
};
const rgb = s => { const [r, g, b, a = 1] = s.match(/[\d.]+/g).map(Number); return { red: r / 255, green: g / 255, blue: b / 255, alpha: a }; };

// An Express page holding a sample design: the nodes a designer would have made.
function build(design) {
    const p = page(design.canvas.width, design.canvas.height, "Source");
    const ab = p.artboards.first, groups = {};
    for (const e of design.elements) {
        const geo = { x: e.x, y: e.y, w: e.width, h: e.height, rotation: e.rotation || 0, opacity: e.opacity ?? 1 };
        let n;
        if (e.type === "text") {
            n = new Node("Text", { ...geo, textAlignment: { left: 1, right: 2, center: 3 }[e.text_align] || 1 });
            n.fullContent = text(n, e.content, { fontSize: e.font_size, color: rgb(e.color),
                font: { family: e.font_family, postscriptName: e.font_weight === "bold" ? "X-Bold" : "X-Regular" } });
            Object.defineProperty(n, "layout", { set(l) { this.w = l.width; } });
        } else if (e.type === "image") n = box("MediaContainer", geo);
        else n = box(e.shape_type === "ellipse" ? "Ellipse" : "Rectangle", { ...geo, fill: { color: rgb(e.fill_color) } });
        if (e.group_id) (groups[e.group_id] ??= (() => { const g = group(); ab.children.append(g); return g; })()).children.append(n);
        else ab.children.append(n);
    }
    pages.items.push(p);
    editor.context.currentPage = p;
    return p;
}

// ---------------------------------------------------------------- load the real sandbox code
globalThis.__mock = { editor, constants, sdk: { instance: { runtime: { exposeApi: api => { globalThis.__mock.api = api; } } } } };
const mod = src => `data:text/javascript,${encodeURIComponent(src)}`;
const code = readFileSync(join(ROOT, "addon/src/sandbox/code.js"), "utf8")
    .replace('"add-on-sdk-document-sandbox"', JSON.stringify(mod("export default globalThis.__mock.sdk;")))
    .replace('"express-document-sdk"', JSON.stringify(mod("export const { editor, constants } = globalThis.__mock;")));
await import(mod(code));
const api = globalThis.__mock.api;

function engine(source, target) {
    const py = "import json,sys\nfrom engine.resize import resize\nt=json.load(sys.stdin);n={}\n"
        + "d=resize(t['source'],t['target'],n)\nprint(json.dumps({'design':d,'notes':n}))";
    const opts = { cwd: ROOT, input: JSON.stringify({ source, target }), encoding: "utf8", maxBuffer: 1 << 26 };
    const r = process.env.PYTHON // a whole command, e.g. "docker run -i ... --entrypoint python chameleon"
        ? spawnSync(`${process.env.PYTHON} -c "${py.split("\n").join(";")}"`, { ...opts, shell: true })
        : spawnSync(PYTHON, ["-c", py], opts);
    assert.equal(r.status, 0, r.stderr);
    return JSON.parse(r.stdout);
}

// ---------------------------------------------------------------- the checks
for (const [sample, W, H] of [["chameleon_sample_level2_core", 1600, 400], ["chameleon_sample_level3_advanced", 728, 90],
                              ["chameleon_sample_level3_advanced", 1080, 1920]]) {
    pages.items.length = 0;
    const src = JSON.parse(readFileSync(join(ROOT, "input", `${sample}.json`), "utf8")).source;
    const original = build(src);
    const before = JSON.stringify(original.artboards.first.children.items.map(n => [n.x, n.y, n.w, n.h]));

    const read = api.readPage();
    assert.equal(read.design.elements.length, src.elements.length, "every node becomes an element");
    assert.deepEqual(read.design.canvas, src.canvas);
    for (const [i, e] of src.elements.entries()) { // geometry and content survive the trip
        const r = read.design.elements[i];
        assert.equal(r.type, e.type);
        assert.ok(Math.abs(r.x - e.x) < 1e-6 && Math.abs(r.width - e.width) < 1e-6, `geometry of ${e.id}`);
        if (e.type === "text") assert.equal(r.content, e.content);
        assert.equal(Boolean(r.group_id), Boolean(e.group_id), `grouping of ${e.id}`);
    }

    const { design, notes } = engine(read.design, { width: W, height: H });
    const result = api.applyLayout(read.pageId, design, `${W}x${H}`);
    assert.equal(result.failed, 0, "every node placed");
    assert.equal(pages.length, 2, "one new page");
    const clone = pages.item(1);
    assert.equal(clone.width, W);
    assert.equal(clone.height, H);
    assert.equal(JSON.stringify(original.artboards.first.children.items.map(n => [n.x, n.y, n.w, n.h])), before, "source page untouched");

    const at = path => path.split(".").reduce((n, i) => n.children.item(Number(i)), clone.artboards.first);
    for (const e of design.elements.filter(e => !e.group_id)) {
        const n = at(e.id);
        if (e.opacity === 0) { assert.equal(n.opacity, 0, `${e.id} hidden`); continue; }
        const b = n.boundsInNode();
        assert.ok(Math.abs(b.x + b.width / 2 - (e.x + e.width / 2)) < 0.5, `${e.id} centred where the engine put it`);
        assert.ok(Math.abs(b.y + b.height / 2 - (e.y + e.height / 2)) < 0.5, `${e.id} centred vertically`);
        if (e.type === "text") assert.equal(n.fullContent.characterStyleRanges[0].fontSize, e.font_size, `${e.id} type size`);
    }
    console.log(`ok  ${sample} -> ${W}x${H}: ${result.applied} elements placed (${notes.layout})`);
}

await api.addImagePage(new Blob([new Uint8Array(64)]), 728, 90, "Leaderboard");
const added = pages.item(pages.length - 1);
assert.deepEqual([added.width, added.height, added.name], [728, 90, "Leaderboard"]);
assert.equal(added.artboards.first.children.first.type, "MediaContainer");
console.log("ok  addImagePage -> a 728x90 page holding the rendered layout");
