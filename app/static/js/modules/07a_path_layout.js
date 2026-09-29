
// ============================================================================
// РАСКЛАДКА «ПУТИ ЗНАНИЙ»: СТРУКТУРНОЕ ДЕРЕВО → КООРДИНАТЫ
// Круговая (кольца от центра предмета), «сверху вниз» и «слева направо» (слои).
// Чистые функции без DOM: проверяются автотестом через node (tests/js/path_layout_check.js).
//
// Дерево: корень — предмет, его дети — основы; у остальных узлов ровно один структурный
// родитель (родитель из карты или первый пререквизит ярусом выше). Слой = глубина в дереве,
// поэтому подтема, привязанная прямо к основе, стоит под ней и её линия не перескакивает слои.
// Линии дерева не пересекаются по построению: каждое поддерево занимает свою полосу (сектор).
// ============================================================================

const PATH_COURSE_ID = '__course__';

const PATH_LAYOUT_CONFIG = {
    vertical: {
        depthGap: 150,          // расстояние между слоями
        siblingGap: 22,         // зазор между соседними поддеревьями
        columnThreshold: 4,     // больше стольких листьев у узла — складываем их в столбик
        columnMaxRows: 8,
        columnWidth: 130,
        rowGap: 50,
        leafOffset: 55,         // сдвиг листа вправо от вертикальной «шины» столбика
        rowSpacing: 1.1,        // зазор между рядами при переносе (в долях depthGap)
        targetAspect: 1.3       // желаемое отношение ширины к высоте схемы при переносе рядами
    },
    horizontal: {
        depthGap: 240,
        siblingGap: 10,
        nodeBreadth: 46,        // по вертикали узел с подписью занимает ~46px
        rowSpacing: 0.6,        // зазор между колонками при переносе (в долях depthGap)
        targetAspect: 0.8       // поперечная ось здесь вертикальная: колонки выше, чем шире
    },
    radial: {
        firstRingGap: 220,
        ringGaps: [160, 120, 100],
        minRootArc: 170,        // минимальная дуга между основами на ободе
        minArc: 125,            // минимальная дуга между узлами второго кольца (подписи тем не слипаются)
        minLeafArc: 34,
        minCoreWeight: 2.5,     // минимальный «вес» сектора основы/темы
        leafSubRing: 55         // второй «подкольцевой» ряд для плотных вееров листьев
    }
};

function pathLabelWidth(node) {
    const maxChars = node && node.tier === 0 ? 22 : 16;
    const len = Math.min(String((node && node.name) || '').length, maxChars);
    return Math.max(70, len * 6.4 + 16);
}

// Структурный родитель каждого узла: родитель из карты → первый пререквизит ярусом выше → предмет.
// Циклы разрываются (узел уходит к предмету).
function buildPathTree(nodes) {
    const byId = new Map(nodes.map(n => [String(n.id), n]));
    const parent = new Map();
    nodes.forEach(n => {
        const id = String(n.id);
        let p = PATH_COURSE_ID;
        if (n.tier > 0) {
            const mapParent = n.parent_id !== undefined && n.parent_id !== null ? String(n.parent_id) : '';
            if (mapParent && mapParent !== id && byId.has(mapParent)) {
                p = mapParent;
            } else {
                const prereqs = (n.prereq_keys || [])
                    .map(k => byId.get(String(k)))
                    .filter(x => x && String(x.id) !== id && x.tier < n.tier);
                const primary = prereqs.find(x => x.tier === n.tier - 1) || prereqs.sort((a, b) => b.tier - a.tier)[0];
                if (primary) p = String(primary.id);
            }
        }
        parent.set(id, p);
    });
    nodes.forEach(n => {
        const id = String(n.id);
        const seen = new Set([id]);
        let cur = parent.get(id);
        while (cur && cur !== PATH_COURSE_ID) {
            if (seen.has(cur)) {
                parent.set(id, PATH_COURSE_ID);
                break;
            }
            seen.add(cur);
            cur = parent.get(cur);
        }
    });

    const children = new Map([[PATH_COURSE_ID, []]]);
    nodes.forEach(n => {
        const p = parent.get(String(n.id));
        if (!children.has(p)) children.set(p, []);
        children.get(p).push(String(n.id));
    });

    const depth = new Map([[PATH_COURSE_ID, 0]]);
    const queue = [PATH_COURSE_ID];
    while (queue.length) {
        const u = queue.shift();
        (children.get(u) || []).forEach(c => {
            depth.set(c, depth.get(u) + 1);
            queue.push(c);
        });
    }
    return { byId, parent, children, depth };
}

// Порядок детей: связанные между собой ветки (по смысловым связям) ставим рядом —
// межветочные связи становятся короче и реже пересекаются.
function orderPathChildren(tree, edges) {
    const topUnder = (id, parentId) => {
        let cur = id;
        let guard = 0;
        while (cur && tree.parent.get(cur) !== parentId && guard++ < 64) {
            cur = tree.parent.get(cur);
            if (!cur || cur === PATH_COURSE_ID) return null;
        }
        return tree.parent.get(cur) === parentId ? cur : null;
    };
    tree.children.forEach((kids, parentId) => {
        if (kids.length < 3) return;
        const originalIdx = new Map(kids.map((k, i) => [k, i]));
        const weight = new Map();
        const key = (a, b) => (a < b ? `${a}|${b}` : `${b}|${a}`);
        (edges || []).forEach(e => {
            const a = topUnder(String(e.source), parentId);
            const b = topUnder(String(e.target), parentId);
            if (a && b && a !== b) weight.set(key(a, b), (weight.get(key(a, b)) || 0) + 1);
        });
        if (!weight.size) return;
        const ordered = [kids[0]];
        const rest = new Set(kids.slice(1));
        while (rest.size) {
            const last = ordered[ordered.length - 1];
            let best = null;
            let bestW = -1;
            rest.forEach(c => {
                const w = weight.get(key(last, c)) || 0;
                if (w > bestW || (w === bestW && originalIdx.get(c) < originalIdx.get(best))) {
                    best = c;
                    bestW = w;
                }
            });
            ordered.push(best);
            rest.delete(best);
        }
        tree.children.set(parentId, ordered);
    });
}

// Аккуратное слоистое дерево в координатах (breadth, depth): каждое поддерево — своя полоса.
// Связи рисуются «оргчартом»: от родителя вниз к шине, по шине, вниз к ребёнку.
function tidyPathTree(tree, cfg, breadthOf, wrapRoots) {
    const pos = new Map();
    const bends = new Map();
    const span = new Map();
    const isLeaf = id => !(tree.children.get(id) || []).length;
    const useColumns = cfg.columnThreshold !== undefined;

    const items = id => {
        const kids = tree.children.get(id) || [];
        const leaves = kids.filter(isLeaf);
        if (!useColumns || leaves.length <= cfg.columnThreshold) {
            return kids.map(k => ({ type: 'node', id: k }));
        }
        const branches = kids.filter(k => !isLeaf(k)).map(k => ({ type: 'node', id: k }));
        const column = { type: 'column', leaves, cols: Math.ceil(leaves.length / cfg.columnMaxRows) };
        branches.splice(Math.floor(branches.length / 2), 0, column);
        return branches;
    };
    const itemSpan = it => (it.type === 'column' ? it.cols * cfg.columnWidth : span.get(it.id));

    const measure = id => {
        const its = items(id);
        its.forEach(it => { if (it.type === 'node') measure(it.id); });
        const own = breadthOf(id) + cfg.siblingGap;
        const kidsSpan = its.reduce((sum, it) => sum + itemSpan(it), 0);
        span.set(id, Math.max(own, kidsSpan));
    };

    const place = (id, left, d) => {
        const total = span.get(id);
        const b = left + total / 2;
        pos.set(id, { b, d });
        const its = items(id);
        if (!its.length) return;
        const kidsSpan = its.reduce((sum, it) => sum + itemSpan(it), 0);
        let cur = left + (total - kidsSpan) / 2;
        const busD = d + cfg.depthGap * 0.45;
        its.forEach(it => {
            if (it.type === 'node') {
                place(it.id, cur, d + cfg.depthGap);
                bends.set(it.id, [{ b, d: busD }, { b: pos.get(it.id).b, d: busD }]);
            } else {
                it.leaves.forEach((leafId, idx) => {
                    const col = Math.floor(idx / cfg.columnMaxRows);
                    const row = idx % cfg.columnMaxRows;
                    const spineB = cur + col * cfg.columnWidth + 12;
                    const leafD = d + cfg.depthGap + row * cfg.rowGap;
                    pos.set(leafId, { b: spineB + cfg.leafOffset, d: leafD });
                    bends.set(leafId, [{ b, d: busD }, { b: spineB, d: busD }, { b: spineB, d: leafD }]);
                });
            }
            cur += itemSpan(it);
        });
    };

    measure(PATH_COURSE_ID);
    const roots = tree.children.get(PATH_COURSE_ID) || [];
    const totalSpan = roots.reduce((sum, r) => sum + span.get(r), 0);

    // Высота поддерева основы (по оси слоёв), включая столбики листьев
    const subtreeDepth = rootId => {
        let maxD = 0;
        const walk = id => {
            maxD = Math.max(maxD, pos.get(id).d);
            (tree.children.get(id) || []).forEach(walk);
        };
        walk(rootId);
        return maxD;
    };

    // Ряды: сколько основ с их деревьями в одном ряду, чтобы схема была близка к квадрату
    let rows = [roots];
    if (wrapRoots && roots.length > 1) {
        roots.forEach(r => place(r, 0, 0));
        const height = Math.max(...roots.map(subtreeDepth)) + cfg.depthGap * cfg.rowSpacing;
        const k = Math.max(1, Math.round(Math.sqrt(totalSpan / (cfg.targetAspect * height))));
        if (k > 1) {
            const maxRow = Math.max(...roots.map(r => span.get(r)), totalSpan / k);
            rows = [[]];
            let rowSpan = 0;
            roots.forEach(r => {
                if (rows[rows.length - 1].length && rowSpan + span.get(r) > maxRow * 1.05) {
                    rows.push([]);
                    rowSpan = 0;
                }
                rows[rows.length - 1].push(r);
                rowSpan += span.get(r);
            });
        }
    }

    if (rows.length === 1) {
        place(PATH_COURSE_ID, -span.get(PATH_COURSE_ID) / 2, 0);
        return { pos, bends };
    }

    // Перенос рядами: предмет в левом верхнем углу, от него вниз «ствол» левее всех деревьев,
    // от ствола к каждому ряду — своя шина между рядами. Линии дерева по-прежнему не пересекаются.
    const trunkB = -cfg.depthGap * 0.35;
    pos.set(PATH_COURSE_ID, { b: trunkB, d: 0 });
    let rowTop = cfg.depthGap;
    rows.forEach(row => {
        let left = 0;
        let rowBottom = rowTop;
        const busD = rowTop - cfg.depthGap * 0.45;
        row.forEach(r => {
            place(r, left, rowTop);
            bends.set(r, [{ b: trunkB, d: busD }, { b: pos.get(r).b, d: busD }]);
            rowBottom = Math.max(rowBottom, subtreeDepth(r));
            left += span.get(r);
        });
        rowTop = rowBottom + cfg.depthGap * cfg.rowSpacing;
    });
    return { pos, bends };
}

function layoutPathVertical(tree, horizontal) {
    const cfg = horizontal
        ? Object.assign({}, PATH_LAYOUT_CONFIG.horizontal)
        : Object.assign({}, PATH_LAYOUT_CONFIG.vertical);
    const breadthOf = id => {
        if (id === PATH_COURSE_ID) return horizontal ? 60 : 180;
        return horizontal ? cfg.nodeBreadth : pathLabelWidth(tree.byId.get(id));
    };
    const { pos, bends } = tidyPathTree(tree, cfg, breadthOf, true);
    const toXY = p => (horizontal ? { x: p.d, y: p.b } : { x: p.b, y: p.d });
    const positions = {};
    pos.forEach((p, id) => { positions[id] = toXY(p); });
    const outBends = {};
    bends.forEach((pts, id) => { outBends[id] = pts.map(toXY); });
    return { positions, bends: outBends, course: positions[PATH_COURSE_ID], hubRadius: 0 };
}

// Круговая: то же дерево в полярных координатах. Угол — полоса поддерева, радиус — слой.
function layoutPathRadial(tree) {
    const cfg = PATH_LAYOUT_CONFIG.radial;
    const leafWeight = new Map();
    const weightOf = id => {
        if (leafWeight.has(id)) return leafWeight.get(id);
        const kids = tree.children.get(id) || [];
        const node = tree.byId.get(id);
        // Основа или тема без подтем всё равно получает заметный сектор — иначе соседние темы слипаются
        const minWeight = node && node.tier <= 1 ? cfg.minCoreWeight : 1;
        const w = Math.max(minWeight, kids.length ? kids.reduce((s, k) => s + weightOf(k), 0) : 1);
        leafWeight.set(id, w);
        return w;
    };
    weightOf(PATH_COURSE_ID);

    const angle = new Map();
    const roots = tree.children.get(PATH_COURSE_ID) || [];
    const totalW = roots.reduce((s, r) => s + weightOf(r), 0) || 1;
    const equal = 1 / Math.max(1, roots.length);
    const assign = (id, start, end) => {
        angle.set(id, (start + end) / 2);
        const kids = tree.children.get(id) || [];
        if (!kids.length) return;
        const w = kids.reduce((s, k) => s + weightOf(k), 0);
        let cur = start;
        kids.forEach(k => {
            const share = (end - start) * weightOf(k) / w;
            assign(k, cur, cur + share);
            cur += share;
        });
    };
    // Сектор основы: половина поровну (основы не слипаются), половина — по размеру её ветвей
    let cur = -Math.PI / 2;
    roots.forEach(r => {
        const share = 2 * Math.PI * (0.5 * equal + 0.5 * weightOf(r) / totalW);
        assign(r, cur, cur + share);
        cur += share;
    });

    // Радиусы колец: не меньше, чем нужно, чтобы соседние подписи на кольце не слипались
    const byDepth = new Map();
    tree.depth.forEach((d, id) => {
        if (id === PATH_COURSE_ID) return;
        if (!byDepth.has(d)) byDepth.set(d, []);
        byDepth.get(d).push(id);
    });
    const minGapAngle = ids => {
        const a = ids.map(id => angle.get(id)).sort((x, y) => x - y);
        if (a.length < 2) return 2 * Math.PI;
        let g = 2 * Math.PI - (a[a.length - 1] - a[0]);
        for (let i = 1; i < a.length; i++) g = Math.min(g, a[i] - a[i - 1]);
        return Math.max(g, 1e-3);
    };
    const radius = new Map();
    const maxDepth = Math.max(0, ...byDepth.keys());
    const hubRadius = roots.length > 1 ? Math.max(130, cfg.minRootArc / minGapAngle(roots)) : 0;
    radius.set(1, hubRadius);
    for (let d = 2; d <= maxDepth; d++) {
        const ids = byDepth.get(d) || [];
        const gap = d === 2 ? cfg.firstRingGap : cfg.ringGaps[Math.min(d - 3, cfg.ringGaps.length - 1)];
        // Подписи тем (ярус ≤ 1) видны всегда — им нужна дуга minArc; мелким узлам хватает minLeafArc
        const core = ids.filter(id => (tree.byId.get(id) || {}).tier <= 1);
        const need = Math.max(
            core.length > 1 ? cfg.minArc / minGapAngle(core) : 0,
            cfg.minLeafArc / minGapAngle(ids)
        );
        radius.set(d, Math.min(2600, Math.max(radius.get(d - 1) + gap, need)));
    }

    const positions = { [PATH_COURSE_ID]: { x: 0, y: 0 } };
    tree.depth.forEach((d, id) => {
        if (id === PATH_COURSE_ID) return;
        let r = radius.get(d) || 0;
        // Плотный веер листьев одного родителя — через один на второе «подкольцо»
        const siblings = tree.children.get(tree.parent.get(id)) || [];
        const isLeaf = !(tree.children.get(id) || []).length;
        if (d > 2 && isLeaf && siblings.length > 5 && siblings.indexOf(id) % 2 === 1) r += cfg.leafSubRing;
        const a = angle.get(id);
        positions[id] = { x: Math.cos(a) * r, y: Math.sin(a) * r };
    });
    const ring2 = radius.get(2) || hubRadius + cfg.firstRingGap;
    const routeRadius = hubRadius + (ring2 - hubRadius) * 0.5;
    return { positions, bends: {}, course: { x: 0, y: 0 }, hubRadius, routeRadius };
}

// Главная точка входа: nodes [{id, tier, name, parent_id, prereq_keys}], edges [{source, target}]
function computePathLayout(nodes, edges, layoutType) {
    const tree = buildPathTree(nodes);
    orderPathChildren(tree, edges);
    const result = layoutType === 'radial'
        ? layoutPathRadial(tree)
        : layoutPathVertical(tree, layoutType === 'horizontal');
    result.parent = {};
    tree.parent.forEach((p, id) => { result.parent[id] = p; });
    result.depth = {};
    tree.depth.forEach((d, id) => { result.depth[id] = d; });
    return result;
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { PATH_COURSE_ID, PATH_LAYOUT_CONFIG, buildPathTree, computePathLayout, pathLabelWidth };
}
