// Document sandbox: the only part of the add-on that touches the Express document.
//  - readPage():          the current page as a CHAMELEON design (element ids are tree paths)
//  - applyLayout(...):    clone that page at a new size and move/resize its real nodes as the engine says
//  - addImagePage(...):   a new page holding a rendered layout (for designs that don't live in Express)
import addOnSandboxSdk from "add-on-sdk-document-sandbox";
import { editor, constants } from "express-document-sdk";

const { runtime } = addOnSandboxSdk.instance;
const T = constants.SceneNodeType;
const ALIGN = { 1: "left", 2: "right", 3: "center", 4: "left" }; // constants.TextAlignment values

const rgba = c =>
    c ? `rgba(${Math.round(c.red * 255)},${Math.round(c.green * 255)},${Math.round(c.blue * 255)},${c.alpha ?? 1})`
      : "rgba(0,0,0,0)";

// Leaf nodes in paint order. Ids are tree paths ("3", "3.1") so a cloned page can be walked the same way;
// everything inside a top-level group shares that group's id and moves as one unit.
function flatten(container, prefix = "", group = null, out = []) {
    let i = 0;
    for (const node of container.children) {
        const path = prefix + i++;
        if (node.type === T.group) flatten(node, path + ".", group ?? `g${path}`, out);
        else out.push({ node, path, group });
    }
    return out;
}

function nodeAt(container, path) {
    return path.split(".").reduce((n, i) => n.children.item(Number(i)), container);
}

function describe(node, path, group, artboard, z) {
    const b = node.boundsInNode(artboard); // axis-aligned, artboard coordinates
    const size = node.boundsLocal; // unrotated size
    const cx = b.x + b.width / 2, cy = b.y + b.height / 2;
    const el = {
        id: path, x: cx - size.width / 2, y: cy - size.height / 2, width: size.width, height: size.height,
        rotation: node.rotation || 0, opacity: node.opacity ?? 1, z_index: z
    };
    if (group) el.group_id = group;
    if (node.type === T.text) {
        const s = node.fullContent.characterStyleRanges[0] || {};
        return {
            ...el, type: "text", content: node.fullContent.text, font_family: s.font?.family || "Solstice Sans",
            font_size: s.fontSize || 24, color: rgba(s.color || { red: 0, green: 0, blue: 0, alpha: 1 }),
            text_align: ALIGN[node.textAlignment] || "left",
            font_weight: /bold|black|heavy/i.test(s.font?.postscriptName || "") ? "bold" : "regular"
        };
    }
    if (node.type === T.mediaContainer || node.type === T.imageRectangle) {
        return { ...el, type: "image", fit: "contain" }; // no asset_id: the engine treats it as an opaque picture
    }
    return {
        ...el, type: "shape", shape_type: node.type === T.ellipse ? "ellipse" : "rectangle",
        fill_color: rgba(node.fill?.color)
    };
}

function place(node, e) {
    const dropped = e.opacity === 0 || /,\s*0\)\s*$/.test(e.color || "");
    if (dropped) {
        node.opacity = 0; // hidden, not deleted: the user can bring it back
        return;
    }
    if (node.type === T.text) {
        node.fullContent.applyCharacterStyles({ fontSize: e.font_size });
        try {
            node.layout = { type: constants.TextLayout.autoHeight, width: e.width };
        } catch (err) {
            /* threaded or special text keeps its own layout */
        }
    } else if (node.type === T.rectangle) {
        node.width = e.width;
        node.height = e.height;
    } else if (node.type === T.ellipse) {
        node.rx = e.width / 2;
        node.ry = e.height / 2;
    } else {
        const behavior = e.fit === "cover" ? constants.ResizeBehavior.cover : constants.ResizeBehavior.contain;
        node.resize({ behavior, width: e.width, height: e.height, avoidScalingVisualDetailsIfPossible: false });
    }
    node.setPositionInParent({ x: e.x + e.width / 2, y: e.y + e.height / 2 }, node.centerPointLocal);
}

function union(els) {
    const x0 = Math.min(...els.map(e => e.x)), y0 = Math.min(...els.map(e => e.y));
    const x1 = Math.max(...els.map(e => e.x + e.width)), y1 = Math.max(...els.map(e => e.y + e.height));
    return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 };
}

runtime.exposeApi({
    readPage() {
        const page = editor.context.currentPage;
        const artboard = page.artboards.first;
        const elements = flatten(artboard).map(({ node, path, group }, z) => describe(node, path, group, artboard, z));
        return {
            pageId: page.id,
            name: page.name || "This page",
            design: { canvas: { width: Math.round(page.width), height: Math.round(page.height) }, elements }
        };
    },

    applyLayout(pageId, design, name) {
        const original = editor.documentRoot.pages.toArray().find(p => p.id === pageId);
        if (!original) throw new Error("The source page no longer exists.");
        const page = original.cloneInPlace(); // the copy becomes the active page
        page.width = design.canvas.width;
        page.height = design.canvas.height;
        page.name = name;
        const artboard = page.artboards.first;
        let applied = 0, failed = 0;
        const groups = new Map();
        for (const e of design.elements) {
            if (e.group_id) {
                if (!groups.has(e.group_id)) groups.set(e.group_id, []);
                groups.get(e.group_id).push(e);
                continue;
            }
            try {
                place(nodeAt(artboard, e.id), e);
                applied++;
            } catch (err) {
                console.warn("could not place", e.id, err);
                failed++;
            }
        }
        for (const [gid, els] of groups) { // a group scales uniformly into the box its members now occupy
            try {
                const visible = els.filter(e => e.opacity !== 0);
                const node = nodeAt(artboard, gid.slice(1));
                if (!visible.length) node.opacity = 0;
                else place(node, { ...union(visible), fit: "contain" });
                applied += els.length;
            } catch (err) {
                console.warn("could not place group", gid, err);
                failed += els.length;
            }
        }
        return { applied, failed };
    },

    async addImagePage(blob, width, height, name) {
        const bitmap = await editor.loadBitmapImage(blob);
        await editor.keepContentActiveDuringAsync(
            editor.context.insertionParent,
            async () => {},
            () => {
                const page = editor.documentRoot.pages.addPage({ width, height });
                page.name = name;
                const image = editor.createImageContainer(bitmap, { initialSize: { width, height } });
                page.artboards.first.children.append(image);
            }
        );
    }
});
