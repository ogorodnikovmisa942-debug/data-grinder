// Проверка раскладки «Пути знаний»: пересечения линий дерева и наложение подписей основ и тем.
// Запуск: node tests/js/path_layout_check.js → JSON с метриками по каждому набору и раскладке.
const fs = require('fs');
const path = require('path');
const layout = require(path.join(__dirname, '..', '..', 'app', 'static', 'js', 'modules', '07a_path_layout.js'));

const LABEL_HEIGHT = 26;

function fromFixture(file) {
    const m = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'fixtures', file), 'utf8'));
    return {
        nodes: m.nodes.map(n => ({ id: n.key, name: n.name, tier: n.tier, parent_id: n.parent, prereq_keys: n.prereqs })),
        edges: m.edges.map(e => ({ source: e.from, target: e.to }))
    };
}

function node(id, tier, parent, name) {
    return { id, tier, parent_id: parent || null, prereq_keys: parent ? [parent] : [], name: name || `Узел ${id}` };
}

// Синтетические предметы: крайние случаи из RFC
function synthetic() {
    const sets = {};
    sets.tiny = { nodes: [node('a', 0, null, 'Единственная основа')], edges: [] };
    const wide = [node('root', 0, null, 'Основа'), node('t', 1, 'root', 'Тема')];
    for (let i = 0; i < 60; i++) wide.push(node(`leaf${i}`, 2, 't', `Подтема номер ${i}`));
    sets.wide = { nodes: wide, edges: [] };
    const onRoot = [node('r1', 0), node('r2', 0), node('t1', 1, 'r1'), node('t2', 1, 'r2')];
    for (let i = 0; i < 6; i++) onRoot.push(node(`s${i}`, 2, 'r1'));
    for (let i = 0; i < 5; i++) onRoot.push(node(`u${i}`, 2, 't2'));
    sets.subtopics_on_foundation = { nodes: onRoot, edges: [{ source: 't1', target: 't2' }] };
    sets.broken_parents = {
        nodes: [node('r', 0), node('x', 2, 'ghost'), node('c1', 1, 'c2'), node('c2', 1, 'c1'), node('ok', 2, 'r')],
        edges: [{ source: 'x', target: 'ghost' }]
    };
    const big = [];
    for (let r = 0; r < 8; r++) {
        big.push(node(`r${r}`, 0, null, `Основа предмета ${r}`));
        for (let t = 0; t < 3; t++) {
            big.push(node(`t${r}_${t}`, 1, `r${r}`, `Тема ${r}.${t} с длинным названием`));
            for (let s = 0; s < 1 + ((r + t) % 7); s++) big.push(node(`s${r}_${t}_${s}`, 2, `t${r}_${t}`, `Подтема ${s}`));
        }
    }
    const bigEdges = [];
    for (let i = 0; i < 20; i++) bigEdges.push({ source: `t${i % 8}_${i % 3}`, target: `t${(i * 3 + 1) % 8}_${(i + 1) % 3}` });
    sets.big_synthetic = { nodes: big, edges: bigEdges };
    return sets;
}

function segmentsIntersect(p1, p2, p3, p4) {
    const d = (a, b, c) => (c.x - a.x) * (b.y - a.y) - (b.x - a.x) * (c.y - a.y);
    const d1 = d(p3, p4, p1), d2 = d(p3, p4, p2), d3 = d(p1, p2, p3), d4 = d(p1, p2, p4);
    const eps = 1e-6;
    return ((d1 > eps && d2 < -eps) || (d1 < -eps && d2 > eps)) && ((d3 > eps && d4 < -eps) || (d3 < -eps && d4 > eps));
}

function analyse(set, layoutType) {
    const res = layout.computePathLayout(set.nodes, set.edges, layoutType);
    const pos = res.positions;
    const missing = set.nodes.filter(n => !pos[n.id] || !Number.isFinite(pos[n.id].x) || !Number.isFinite(pos[n.id].y)).map(n => n.id);

    // Линии дерева: ломаные родитель → изгибы → ребёнок (в круговой линии к основам не рисуются)
    const segs = [];
    set.nodes.forEach(n => {
        const p = res.parent[n.id];
        if (!p || !pos[p] || !pos[n.id]) return;
        if (layoutType === 'radial' && p === layout.PATH_COURSE_ID) return;
        const pts = [pos[p], ...(res.bends[n.id] || []), pos[n.id]];
        for (let i = 1; i < pts.length; i++) segs.push({ a: pts[i - 1], b: pts[i], parent: p, child: n.id });
    });
    let treeCrossings = 0;
    for (let i = 0; i < segs.length; i++) {
        for (let j = i + 1; j < segs.length; j++) {
            const s = segs[i], t = segs[j];
            if (s.parent === t.parent || s.child === t.parent || t.child === s.parent || s.child === t.child) continue;
            if (segmentsIntersect(s.a, s.b, t.a, t.b)) treeCrossings++;
        }
    }

    // Подписи основ и тем (ярусы 0–1): прямоугольник под узлом не должен накрывать соседний
    const labels = set.nodes.filter(n => n.tier <= 1 && pos[n.id]).map(n => {
        const w = layout.pathLabelWidth(n);
        const c = pos[n.id];
        return { id: n.id, x1: c.x - w / 2, x2: c.x + w / 2, y1: c.y + 6, y2: c.y + 6 + LABEL_HEIGHT };
    });
    const labelOverlaps = [];
    for (let i = 0; i < labels.length; i++) {
        for (let j = i + 1; j < labels.length; j++) {
            const a = labels[i], b = labels[j];
            if (a.x1 < b.x2 && b.x1 < a.x2 && a.y1 < b.y2 && b.y1 < a.y2) labelOverlaps.push([a.id, b.id]);
        }
    }

    // Межветочные связи прямыми отрезками — только измеряем
    const cross = set.edges.filter(e => pos[e.source] && pos[e.target]).map(e => ({ a: pos[e.source], b: pos[e.target], s: e.source, t: e.target }));
    let crossLinkCrossings = 0;
    for (let i = 0; i < cross.length; i++) {
        for (let j = i + 1; j < cross.length; j++) {
            const s = cross[i], t = cross[j];
            if ([s.s, s.t].some(x => x === t.s || x === t.t)) continue;
            if (segmentsIntersect(s.a, s.b, t.a, t.b)) crossLinkCrossings++;
        }
    }

    const xs = set.nodes.map(n => pos[n.id]).filter(Boolean).map(p => p.x);
    const ys = set.nodes.map(n => pos[n.id]).filter(Boolean).map(p => p.y);
    return {
        missing,
        treeCrossings,
        labelOverlaps: labelOverlaps.slice(0, 10),
        labelOverlapCount: labelOverlaps.length,
        crossLinkCrossings,
        width: Math.round(Math.max(...xs) - Math.min(...xs)),
        height: Math.round(Math.max(...ys) - Math.min(...ys))
    };
}

const sets = Object.assign({
    obshteorprava_v1: fromFixture('path_map_obshteorprava_v1.json'),
    obshteorprava_v3: fromFixture('path_map_obshteorprava_v3.json')
}, synthetic());

const report = {};
Object.entries(sets).forEach(([name, set]) => {
    report[name] = {};
    ['radial', 'tree', 'horizontal'].forEach(t => { report[name][t] = analyse(set, t); });
});
process.stdout.write(JSON.stringify(report, null, 1));
