// ============================================================================
// ВИДЫ КАРТЫ ЗНАНИЙ: ТРОПА, ФОКУС, КРУГИ (+ «СОЗВЕЗДИЕ» — холст графа из 07_graph.js)
// Тропа отвечает на «что дальше», Фокус — «как тема связана с соседями», Круги — «сколько всего и сколько пройдено»,
// Созвездие — граф облаками тем. Все виды открывают одну и ту же шторку узла (showKgNodeDrawer).
// ============================================================================

const KG_VIEWS = ['path', 'focus', 'sun', 'sky'];
let kgViewMode = (() => {
    try {
        const v = localStorage.getItem('dg_kg_view');
        return KG_VIEWS.includes(v) ? v : 'path';
    } catch (_) { return 'path'; }
})();
let kgFocusId = null;     // тема в центре «Фокуса»
let kgSunZoom = null;     // приближённая тема в «Кругах»

const KG_ST_NAMES = { done: 'освоено', part: 'урок пройден', cur: 'ты здесь', open: 'открыто', lock: 'закрыто' };

window.setKgViewMode = function(mode) {
    kgViewMode = KG_VIEWS.includes(mode) ? mode : 'path';
    try { localStorage.setItem('dg_kg_view', kgViewMode); } catch (_) { /* вид просто не запомнится */ }
    kgFocusId = null;
    kgSunZoom = null;
    if (typeof closeKgNodeDrawer === 'function') closeKgNodeDrawer();
    triggerHaptic('light');
    kgRenderCurrentView();
};

window.kgRenderCurrentView = function() {
    const sky = kgViewMode === 'sky';
    document.querySelectorAll('#kg-view-switch [data-kgview]').forEach(b => {
        const on = b.dataset.kgview === kgViewMode;
        b.setAttribute('aria-selected', String(on));
        b.classList.toggle('is-active', on);
    });
    ['kg-graph-canvas-wrapper', 'kg-sky-toolbar', 'kg-sky-zoom'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.classList.toggle('hidden', !sky);
    });
    const alt = document.getElementById('kg-alt-view');
    if (alt) alt.classList.toggle('hidden', sky);
    const data = currentKgGraphData;
    if (!data || !data.nodes || !data.nodes.length) return;
    if (sky) {
        initForceGraph(data);
        return;
    }
    if (currentForceGraphInstance && currentForceGraphInstance.pauseAnimation) currentForceGraphInstance.pauseAnimation();
    if (!alt) return;
    alt.innerHTML = '';
    const model = kgViewModel();
    ({ path: kgRenderPath, focus: kgRenderFocus, sun: kgRenderSun })[kgViewMode](model, alt);
};

// Связь в шторке ведёт внутри текущего вида. false — пусть шторка сама фокусирует холст графа
window.kgAltNavigate = function(id) {
    if (kgViewMode === 'sky') return false;
    const model = kgViewModel();
    const node = model.by.get(String(id));
    if (!node) return false;
    if (kgViewMode === 'focus') {
        kgFocusId = String(id);
        closeKgNodeDrawer();
        kgRenderCurrentView();
    } else {
        showKgNodeDrawer(node);
        const stop = document.querySelector(`#kg-alt-view [data-kgnode="${CSS.escape(String(id))}"]`);
        if (stop && stop.scrollIntoView) stop.scrollIntoView({ block: 'center', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
    }
    return true;
};

// Курс → основы и темы с подтемами (подтема, висящая на основе или на другой подтеме, относится к ближайшей теме вверх)
function kgViewModel() {
    const nodes = currentKgGraphData.nodes;
    const order = new Map(nodes.map((n, i) => [n, i]));
    const by = new Map(nodes.map(n => [String(n.id), n]));
    const parentOf = n => (n.path_parent || n.parent_id) ? String(n.path_parent || n.parent_id) : null;
    const home = n => {
        let p = n;
        for (let g = 0; p && g < 12; g++) {
            if (p.tier === 1) return String(p.id);
            if (p.tier === 0) return '_core';
            p = by.get(parentOf(p) || '');
        }
        return '_core';
    };
    const plan = (currentPathState && currentPathState.plan) || {};
    const curDb = (plan.unfinished && plan.unfinished.node_id) || (plan.next_lesson && plan.next_lesson.node_id);
    const cur = nodes.find(n => n.db_id === curDb) || nodes.find(n => n.status === 'open') || null;
    const byOrder = (a, b) => order.get(a) - order.get(b);
    const deep = nodes.filter(n => n.tier >= 2).sort((a, b) => a.tier - b.tier || byOrder(a, b));
    const topics = nodes.filter(n => n.tier === 1).sort(byOrder).map(t => ({ node: t, kids: deep.filter(n => home(n) === String(t.id)) }));
    const core = [...nodes.filter(n => n.tier === 0).sort(byOrder), ...deep.filter(n => home(n) === '_core')];
    const st = n => n === cur ? 'cur' : ({ mastered: 'done', lesson_done: 'part', open: 'open' }[n.status] || 'lock');
    const edges = currentKgGraphData.edges || currentKgGraphData.links || [];
    const endId = v => String(v && typeof v === 'object' ? v.id : v);
    return { nodes, by, parentOf, topics, core, cur, st, edges, endId, seq: [...core, ...topics.flatMap(t => [t.node, ...t.kids])] };
}

function kgNeighbors(M, n) {
    const out = [];
    const push = (id, rel) => {
        const m = M.by.get(String(id));
        if (m && m !== n && !out.some(x => x.n === m)) out.push({ n: m, rel });
    };
    const p = M.parentOf(n);
    if (p) push(p, 'входит в');
    (n.prereq_keys || []).forEach(k => push(k, 'нужно знать'));
    M.edges.forEach(e => {
        if (e.__structural) return;
        const s = M.endId(e.source), t = M.endId(e.target);
        if (s === String(n.id)) push(t, e.label || 'связано');
        else if (t === String(n.id)) push(s, e.label || 'связано');
    });
    M.nodes.filter(x => M.parentOf(x) === String(n.id)).forEach(x => push(x.id, 'внутри'));
    if (p && out.length < 6) M.nodes.filter(x => M.parentOf(x) === p).forEach(x => { if (out.length < 7) push(x.id, 'рядом'); });
    return out.slice(0, 8);
}

// ---------- Тропа ----------
function kgRenderPath(M, el) {
    const sc = document.createElement('div');
    sc.className = 'kgp-scroll';
    const sections = [{ title: 'Основы', nodes: M.core }, ...M.topics.map(t => ({ title: t.node.name, nodes: [t.node, ...t.kids] }))]
        .filter(s => s.nodes.length);
    const icon = { done: 'check', part: 'more_horiz', lock: 'lock' };
    const cat = (LESSON_CAT && LESSON_CAT.idle) ? LESSON_CAT.idle.frames[0].join('\n') : '';
    let i = 0, step = 0;
    sc.innerHTML = sections.map(sec => {
        const done = sec.nodes.filter(n => n.status === 'mastered').length;
        const here = sec.nodes.includes(M.cur);
        const stops = sec.nodes.map(n => {
            const s = M.st(n);
            const x = 50 + Math.sin(i++ * 0.9) * 26;
            step++;
            const inner = icon[s] ? `<span class="material-symbols-outlined" aria-hidden="true">${icon[s]}</span>` : (n.tier === 3 ? '?' : step);
            const catHtml = s === 'cur' && cat
                ? `<pre class="kgp-cat" aria-hidden="true" style="left:calc(${x}% ${x > 50 ? '- 128px' : '+ 44px'})">${escapeHTML(cat)}</pre><span class="kgp-here" style="left:calc(${x}% ${x > 50 ? '- 128px' : '+ 44px'})">ты здесь</span>`
                : '';
            return `<div class="kgp-stop"><button type="button" class="kgp-node kgp-${s}${n.tier === 1 ? ' kgp-topic' : ''}" style="left:${x}%" data-kgnode="${escapeHTML(String(n.id))}"
                aria-label="${escapeHTML(n.name)}: ${KG_ST_NAMES[s]}">${inner}</button>
                <div class="kgp-cap" style="left:${x}%">${escapeHTML(n.name)}</div>${catHtml}</div>`;
        }).join('');
        return `<div class="kgp-sect${here ? ' kgp-sect-here' : ''}"><b>${escapeHTML(sec.title)}</b><small>${done} из ${sec.nodes.length}${here ? ' · ты здесь' : ''}</small>
            <div class="kgp-bar"><i style="width:${Math.round(100 * done / sec.nodes.length)}%"></i></div></div><div class="kgp-trail">${stops}</div>`;
    }).join('');
    el.appendChild(sc);
    sc.querySelectorAll('[data-kgnode]').forEach(b => b.addEventListener('click', () => showKgNodeDrawer(M.by.get(b.dataset.kgnode))));
    const cur = sc.querySelector('.kgp-cur');
    if (cur) requestAnimationFrame(() => {
        sc.scrollTop = cur.getBoundingClientRect().top - sc.getBoundingClientRect().top - sc.clientHeight / 2 + 30;
    });
}

// ---------- Фокус ----------
function kgRenderFocus(M, el) {
    const c = M.by.get(String(kgFocusId)) || M.cur || M.seq[0];
    if (!c) return;
    kgFocusId = String(c.id);
    const W = el.clientWidth, H = el.clientHeight, cx = W / 2, cy = H / 2 - 6;
    const nb = kgNeighbors(M, c);
    const trail = [];
    for (let p = c, g = 0; p && g < 12; g++) { trail.unshift(p); p = M.by.get(M.parentOf(p) || ''); }
    // Соседи — сеткой по два в ряд над центром и под ним: ни одна плашка не налезает на другую
    const top = nb.slice(0, Math.ceil(nb.length / 2)), bot = nb.slice(top.length);
    const rowGap = Math.max(62, Math.min(96, H - 120 - cy - 82));      // нижний ряд не налезает на подсказку внизу
    const place = (list, dir) => list.map((x, k) => {
        const row = Math.floor(k / 2), alone = k === list.length - 1 && list.length % 2 === 1;
        const px = alone ? cx : (k % 2 ? W * 0.74 : W * 0.26);
        return { ...x, px, py: cy + dir * (82 + row * rowGap) };
    });
    const pos = [...place(top, -1), ...place(bot, 1)];
    const tierName = n => PATH_TIER_NAMES[n.tier] || '';
    let html = `<nav class="kgf-crumbs" aria-label="Где эта тема в курсе">${trail.map((n, k) => k < trail.length - 1
        ? `<button type="button" data-kgfocus="${escapeHTML(String(n.id))}">${escapeHTML(n.name)}</button><span aria-hidden="true">›</span>`
        : `<span>${escapeHTML(n.name)}</span>`).join('')}</nav>`;
    html += `<svg class="kgf-lines" width="${W}" height="${H}" aria-hidden="true">${pos.map(p => `<line x1="${cx}" y1="${cy}" x2="${p.px}" y2="${p.py}"/>`).join('')}</svg>`;
    html += `<button type="button" class="kgf-node kgf-center kgf-${M.st(c)}" style="left:${cx}px;top:${cy}px" data-kgopen="1">${escapeHTML(c.name)}<small>${tierName(c)} · ${KG_ST_NAMES[M.st(c)]}</small></button>`;
    html += pos.map(p => `<button type="button" class="kgf-node kgf-${M.st(p.n)}" style="left:${p.px}px;top:${p.py}px" data-kgfocus="${escapeHTML(String(p.n.id))}">${escapeHTML(p.n.name)}<small>${escapeHTML(p.rel)}</small></button>`).join('');
    html += `<div class="kgf-foot">${nb.length ? 'Тап по соседу — перейти к нему. Тап по центру — урок и карточки.' : 'У этой темы нет связей в карте.'}
        ${M.cur && M.cur !== c ? '<button type="button" class="kgf-home" data-kghome="1">К «ты здесь»</button>' : ''}</div>`;
    el.innerHTML = html;
    el.querySelectorAll('[data-kgfocus]').forEach(b => b.addEventListener('click', () => {
        kgFocusId = b.dataset.kgfocus;
        closeKgNodeDrawer();
        triggerHaptic('light');
        kgRenderCurrentView();
    }));
    el.querySelector('[data-kgopen]').addEventListener('click', () => showKgNodeDrawer(c));
    const home = el.querySelector('[data-kghome]');
    if (home) home.addEventListener('click', () => { kgFocusId = String(M.cur.id); kgRenderCurrentView(); });
}

// ---------- Круги ----------
function kgArc(cx, cy, r0, r1, a0, a1) {
    const p = (r, a) => `${(cx + r * Math.cos(a)).toFixed(1)},${(cy + r * Math.sin(a)).toFixed(1)}`;
    const big = a1 - a0 > Math.PI ? 1 : 0;
    return `M${p(r1, a0)}A${r1},${r1} 0 ${big} 1 ${p(r1, a1)}L${p(r0, a1)}A${r0},${r0} 0 ${big} 0 ${p(r0, a0)}Z`;
}

function kgRenderSun(M, el) {
    const W = el.clientWidth, H = el.clientHeight, cx = W / 2, cy = H / 2 - 12;
    const groupState = (head, kids) => {
        const all = head ? [head, ...kids] : kids;
        if (all.includes(M.cur)) return 'cur';
        if (all.every(n => n.status === 'mastered')) return 'done';
        if (all.some(n => n.status !== 'locked')) return 'open';
        return 'lock';
    };
    let groups = [{ id: '_core', name: 'Основы', head: null, kids: M.core }, ...M.topics.map(t => ({ id: String(t.node.id), name: t.node.name, head: t.node, kids: t.kids }))];
    let zoomed = kgSunZoom ? groups.find(g => g.id === kgSunZoom) : null;
    const items = zoomed ? (zoomed.head ? [zoomed.head, ...zoomed.kids] : zoomed.kids) : M.seq;
    if (zoomed) groups = items.map(n => ({ id: String(n.id), name: n.name, head: n, kids: [], leaf: true }));
    const done = items.filter(n => n.status === 'mastered').length;
    const total = groups.reduce((s, g) => s + Math.max(1, g.kids.length), 0);
    const R = Math.min(W, H) / 2 - 64;
    const r0 = Math.max(44, R * 0.36), r1 = zoomed ? R : R * 0.72, r2 = R;
    let a = -Math.PI / 2;
    let svg = `<svg class="kgs-svg" width="${W}" height="${H}" role="img" aria-label="Курс кругами: темы и подтемы">`;
    groups.forEach(g => {
        const span = 2 * Math.PI * Math.max(1, g.kids.length) / total, a0 = a, a1 = a + span;
        const s = g.leaf ? M.st(g.head) : groupState(g.head, g.kids);
        svg += `<path class="kgs-seg kgs-${s}" d="${kgArc(cx, cy, r0, r1, a0 + 0.004, a1 - 0.004)}" data-kgsun="${escapeHTML(g.id)}" tabindex="0"><title>${escapeHTML(g.name)}</title></path>`;
        if (!zoomed) g.kids.forEach((k, i) => {
            const b0 = a0 + span * i / g.kids.length, b1 = a0 + span * (i + 1) / g.kids.length;
            svg += `<path class="kgs-seg kgs-${M.st(k)}" d="${kgArc(cx, cy, r1 + 3, r2, b0 + 0.006, b1 - 0.006)}" data-kgnode="${escapeHTML(String(k.id))}"><title>${escapeHTML(k.name)}</title></path>`;
        });
        if (span > 0.3) {
            const mid = (a0 + a1) / 2, lr = zoomed ? (r0 + r1) / 2 : r2 + 12;
            const lines = (typeof wrapNodeText === 'function' ? wrapNodeText(g.name, 12) : [g.name]).slice(0, 2);
            const anchor = zoomed ? 'middle' : (Math.cos(mid) > 0.25 ? 'start' : Math.cos(mid) < -0.25 ? 'end' : 'middle');
            const tw = Math.max(...lines.map(l => l.length)) * 6.2;      // подпись не уходит за край экрана
            let x = cx + Math.cos(mid) * lr;
            if (anchor === 'end') x = Math.max(x, tw + 6); else if (anchor === 'start') x = Math.min(x, W - tw - 6);
            const y = cy + Math.sin(mid) * lr - (lines.length - 1) * 6;
            const onFill = zoomed && (s === 'done' || s === 'cur');
            svg += `<text class="kgs-label${onFill ? ' kgs-on-fill' : ''}" x="${x.toFixed(1)}" y="${y.toFixed(1)}" text-anchor="${anchor}">${lines.map((l, i) => `<tspan x="${x.toFixed(1)}" dy="${i ? 12 : 0}">${escapeHTML(l)}</tspan>`).join('')}</text>`;
        }
        a = a1;
    });
    const title = zoomed ? zoomed.name : ((currentPathState && currentPathState.title) || 'Курс');
    svg += `<circle class="kgs-hub${zoomed ? ' kgs-back' : ''}" cx="${cx}" cy="${cy}" r="${r0 - 5}" ${zoomed ? 'data-kgback="1" tabindex="0"' : ''}><title>${zoomed ? 'Назад ко всему курсу' : escapeHTML(title)}</title></circle>`;
    const short = title.length > 18 ? `${title.slice(0, 17)}…` : title;
    svg += `<text class="kgs-hub-title" x="${cx}" y="${cy - 4}" text-anchor="middle">${escapeHTML(short)}</text>`;
    svg += `<text class="kgs-hub-sub" x="${cx}" y="${cy + 13}" text-anchor="middle">${done} из ${items.length} · ${Math.round(100 * done / Math.max(1, items.length))}%</text></svg>`;
    el.innerHTML = svg + `<div class="kgf-foot">${zoomed ? 'Тап по сектору — урок и карточки. Тап по центру — назад ко всему курсу.' : 'Внутри — темы, снаружи — подтемы. Тап по теме — приблизить.'}</div>`;
    el.querySelectorAll('[data-kgsun]').forEach(p => p.addEventListener('click', () => {
        if (zoomed) { showKgNodeDrawer(M.by.get(p.dataset.kgsun)); return; }
        kgSunZoom = p.dataset.kgsun;
        closeKgNodeDrawer();
        kgRenderCurrentView();
    }));
    el.querySelectorAll('[data-kgnode]').forEach(p => p.addEventListener('click', () => showKgNodeDrawer(M.by.get(p.dataset.kgnode))));
    const back = el.querySelector('[data-kgback]');
    if (back) back.addEventListener('click', () => { kgSunZoom = null; closeKgNodeDrawer(); kgRenderCurrentView(); });
}

// Поворот телефона / смена размера окна: виды без холста пересчитываются под новый размер
window.addEventListener('resize', () => {
    const modal = document.getElementById('knowledge-graph-modal');
    if (modal && !modal.classList.contains('hidden') && kgViewMode !== 'sky' && kgViewMode !== 'path') kgRenderCurrentView();
});
