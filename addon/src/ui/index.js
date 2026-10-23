// Panel UI. Inside Adobe Express it resizes the current page natively (or adds rendered layouts as new
// pages); in a plain browser - served by `python -m engine.server` at /viewer/ - it previews and downloads.
const SDK_URL = "https://new.express.adobe.com/static/add-on-sdk/sdk.js";
const DEFAULTS = ["Instagram Post", "Story / Reel", "Facebook Post", "Leaderboard", "Medium Rectangle"];
const $ = id => document.getElementById(id);
const store = {
    get: (k, d) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* storage blocked: preferences just don't persist */ } }
};

const state = {
    engine: location.pathname.startsWith("/viewer") ? location.origin : store.get("chameleon.engine", "http://localhost:8787"),
    express: null, // { sandbox } when running inside Adobe Express
    formats: [],
    picked: new Set(),
    source: null, // { kind: "page" | "sample" | "file", name, design, pageId? }
    results: []
};

async function api(path, body) {
    const res = await fetch(state.engine + path, body === undefined ? undefined : {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body)
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
    return data;
}

function say(text, err = false) {
    $("msg").textContent = text;
    $("msg").classList.toggle("err", err);
}

function fail(err) {
    console.error(err);
    fail(err);
}

async function connectExpress() {
    if (window.self === window.top) return null; // a plain browser tab, not an add-on iframe
    try {
        const { default: sdk } = await import(SDK_URL);
        await Promise.race([sdk.ready, new Promise((_, no) => setTimeout(() => no(new Error("timeout")), 5000))]);
        return { sandbox: await sdk.instance.runtime.apiProxy("documentSandbox") };
    } catch (err) {
        console.warn("Not running inside Adobe Express:", err);
        return null;
    }
}

async function connectEngine() {
    $("engine").value = state.engine;
    try {
        await api("/health");
        $("dot").className = "dot ok";
        $("status").textContent = "engine connected";
        return true;
    } catch {
        $("dot").className = "dot bad";
        $("status").textContent = "engine offline";
        say(`Start the engine with "python -m engine.server" (expected at ${state.engine}).`, true);
        return false;
    }
}

// ---------------------------------------------------------------- sources
async function loadSources() {
    const sel = $("source");
    sel.innerHTML = "";
    if (state.express) sel.append(new Option("This page (live resize)", "page"));
    for (const name of await api("/samples")) sel.append(new Option(`Sample: ${name.replace("chameleon_sample_", "")}`, `sample:${name}`));
    await pickSource(sel.value);
}

async function pickSource(value) {
    state.results = [];
    renderResults();
    try {
        if (value === "page") {
            const page = await state.express.sandbox.readPage();
            if (!page.design.elements.length) throw new Error("This page is empty - add some content first.");
            state.source = { kind: "page", name: page.name, design: page.design, pageId: page.pageId };
        } else {
            const name = value.slice("sample:".length);
            const task = await api(`/samples/${encodeURIComponent(name)}`);
            state.source = { kind: "sample", name, design: task.source ?? task };
        }
        await showSource();
    } catch (err) {
        fail(err);
    }
}

async function showSource() {
    const { design, kind, name } = state.source;
    const pic = kind === "page" ? wireframe(design) : await png(design);
    const info = document.createElement("div");
    info.className = "meta";
    info.append(Object.assign(document.createElement("b"), { textContent: name }),
        document.createElement("br"), `${design.canvas.width}×${design.canvas.height} · ${design.elements.length} elements`);
    $("preview").replaceChildren(pic, info);
    say("");
    updateGo();
}

async function png(design) {
    return Object.assign(new Image(), { alt: "preview", src: (await api("/render", { design })).png });
}

// Schematic preview for designs whose pictures live in Express (the engine can't render them).
function wireframe(design, max = 240) {
    const { width: W, height: H } = design.canvas;
    const s = Math.min(max / W, max / H);
    const c = Object.assign(document.createElement("canvas"), { width: Math.max(1, Math.round(W * s)), height: Math.max(1, Math.round(H * s)) });
    const g = c.getContext("2d");
    g.fillStyle = "#f6f5f9";
    g.fillRect(0, 0, c.width, c.height);
    for (const e of [...design.elements].sort((a, b) => (a.z_index ?? 0) - (b.z_index ?? 0))) {
        if (e.opacity === 0) continue;
        const w = e.width * s, h = e.height * s;
        g.save();
        g.translate(e.x * s + w / 2, e.y * s + h / 2);
        g.rotate(((e.rotation || 0) * Math.PI) / 180);
        g.translate(-w / 2, -h / 2);
        if (e.type === "image") {
            g.fillStyle = "#cfc9dc";
            g.fillRect(0, 0, w, h);
            g.strokeStyle = "#a59fb5";
            g.beginPath(); g.moveTo(0, 0); g.lineTo(w, h); g.moveTo(w, 0); g.lineTo(0, h); g.stroke();
        } else if (e.type === "text") {
            const lh = Math.max(1, e.font_size * 1.2 * s);
            g.fillStyle = e.color || "#333";
            for (let y = 0; y + lh * 0.6 <= h + 0.5; y += lh) g.fillRect(0, y + lh * 0.2, w, lh * 0.55);
        } else {
            g.fillStyle = e.fill_color || "#ddd";
            if (e.shape_type === "ellipse") { g.beginPath(); g.ellipse(w / 2, h / 2, w / 2, h / 2, 0, 0, 2 * Math.PI); g.fill(); }
            else g.fillRect(0, 0, w, h);
        }
        g.restore();
    }
    return c;
}

// ---------------------------------------------------------------- formats
async function loadFormats() {
    state.formats = await api("/formats");
    let saved = null;
    try { saved = JSON.parse(store.get("chameleon.formats", "null")); } catch { /* corrupt: use defaults */ }
    state.picked = new Set(Array.isArray(saved) ? saved : DEFAULTS);
    const groups = [...new Set(state.formats.map(f => f.group))];
    const packs = [...groups.map(gr => [gr, f => f.group === gr]), ["All", () => true], ["None", () => false]];
    $("packs").replaceChildren(...packs.map(([label, pick]) => {
        const b = Object.assign(document.createElement("button"), { className: "ghost", textContent: label });
        b.onclick = () => { state.picked = new Set(state.formats.filter(pick).map(f => f.name)); drawFormats(); };
        return b;
    }));
    drawFormats();
}

function drawFormats() {
    $("formats").replaceChildren(...state.formats.map(f => {
        const b = Object.assign(document.createElement("button"), { className: "chip" });
        b.setAttribute("aria-pressed", String(state.picked.has(f.name)));
        b.append(f.name, Object.assign(document.createElement("small"), { textContent: `${f.width}×${f.height}` }));
        b.onclick = () => { state.picked.has(f.name) ? state.picked.delete(f.name) : state.picked.add(f.name); drawFormats(); };
        return b;
    }));
    store.set("chameleon.formats", JSON.stringify([...state.picked]));
    updateGo();
}

function updateGo() {
    $("go").disabled = !state.source || !state.picked.size;
}

// ---------------------------------------------------------------- resize + results
async function run() {
    const targets = state.formats.filter(f => state.picked.has(f.name));
    $("go").disabled = true;
    say(`Laying out ${targets.length} format${targets.length === 1 ? "" : "s"}…`);
    try {
        const { results } = await api("/resize", { source: state.source.design, targets, render: state.source.kind !== "page" });
        state.results = results;
        renderResults();
        say(`${results.length} layouts · ${Math.round(results.reduce((t, r) => t + r.ms, 0))} ms of engine time`);
    } catch (err) {
        fail(err);
    }
    updateGo();
}

function summary(n) {
    const parts = [n.layout];
    if (n.dropped?.length) parts.push(`${n.dropped.length} secondary element${n.dropped.length === 1 ? "" : "s"} set aside`);
    if (n.hidden?.length) parts.push(`${n.hidden.length} missing asset${n.hidden.length === 1 ? "" : "s"} skipped`);
    return parts.join(" · ");
}

const slug = s => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

function download(href, filename) {
    Object.assign(document.createElement("a"), { href, download: filename }).click();
}

function actionsFor(r) {
    const name = `${r.target.name} ${r.target.width}×${r.target.height}`;
    const sandbox = state.express?.sandbox;
    if (sandbox && state.source.kind === "page") {
        return [["Create page", async () => {
            const { failed } = await sandbox.applyLayout(state.source.pageId, r.design, name);
            if (failed) say(`${name}: ${failed} element(s) could not be moved - adjust them by hand.`, true);
        }]];
    }
    if (sandbox) {
        return [["Add as page", async () => sandbox.addImagePage(await (await fetch(r.png)).blob(), r.target.width, r.target.height, name)]];
    }
    const json = () => URL.createObjectURL(new Blob([JSON.stringify(r.design, null, 2)], { type: "application/json" }));
    return [["PNG", () => download(r.png, `${slug(name)}.png`)], ["JSON", () => download(json(), `${slug(name)}.json`)]];
}

function renderResults() {
    $("results").replaceChildren(...state.results.map(r => {
        const card = Object.assign(document.createElement("div"), { className: "card" });
        const thumb = Object.assign(document.createElement("div"), { className: "thumb" });
        thumb.append(r.png ? Object.assign(new Image(), { src: r.png, alt: r.target.name }) : wireframe(r.design));
        const meta = Object.assign(document.createElement("div"), { className: "meta" });
        meta.append(Object.assign(document.createElement("b"), { textContent: r.target.name }), ` · ${r.target.width}×${r.target.height}`);
        const note = Object.assign(document.createElement("div"), { className: "note", textContent: summary(r.notes) });
        const actions = Object.assign(document.createElement("div"), { className: "actions" });
        for (const [label, act] of actionsFor(r)) {
            const b = Object.assign(document.createElement("button"), { className: "ghost", textContent: label });
            b.onclick = async () => {
                b.disabled = true;
                try { await act(); b.textContent = "Done ✓"; } catch (err) { fail(err); b.disabled = false; }
            };
            actions.append(b);
        }
        card.append(thumb, meta, note, actions);
        return card;
    }));
    const all = $("all");
    all.hidden = !state.results.length;
    all.textContent = state.express ? "Add all as pages" : "Download all PNGs";
    all.onclick = async () => {
        for (const r of state.results) await actionsFor(r)[0][1]();
    };
}

// ---------------------------------------------------------------- wiring
$("source").onchange = e => pickSource(e.target.value);
$("go").onclick = run;
$("file").onchange = async ev => {
    const f = ev.target.files[0];
    ev.target.value = "";
    if (!f) return;
    try {
        const obj = JSON.parse(await f.text());
        const design = obj.source ?? obj;
        if (!design.canvas || !Array.isArray(design.elements)) throw new Error("Not a CHAMELEON design (needs canvas + elements).");
        state.source = { kind: "file", name: f.name, design };
        state.results = [];
        renderResults();
        await showSource();
    } catch (err) {
        fail(err);
    }
};
$("reconnect").onclick = async () => {
    state.engine = $("engine").value.trim().replace(/\/+$/, "");
    store.set("chameleon.engine", state.engine);
    await start();
};

async function start() {
    say("");
    if (!(await connectEngine())) return;
    try {
        await loadFormats();
        await loadSources();
        const link = new URLSearchParams(location.hash.slice(1)); // deep link: #sample=<name>&run
        if (link.get("sample")) {
            $("source").value = `sample:${link.get("sample")}`;
            await pickSource($("source").value);
        }
        if (link.has("run")) await run();
    } catch (err) {
        fail(err);
    }
}

state.express = await connectExpress();
await start();
