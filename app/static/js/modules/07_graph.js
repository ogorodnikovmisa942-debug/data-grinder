let currentKgSubject = '';
let currentKgGraphData = null;
let currentKgTreeData = null;
let currentKgView = 'tree'; // 'tree' | 'graph'
let currentKgLayout = 'force'; // 'force' | 'radial' | 'tree'
let currentForceGraphInstance = null;

// Динамический асинхронный загрузчик библиотек физики графа (D3 & ForceGraph)
let graphVendorLoadingPromise = null;
function ensureGraphVendorLoaded() {
    if (window.ForceGraph && window.d3) {
        return Promise.resolve();
    }
    if (graphVendorLoadingPromise) {
        return graphVendorLoadingPromise;
    }
    graphVendorLoadingPromise = new Promise((resolve, reject) => {
        const loadScript = (src) => new Promise((res, rej) => {
            if (document.querySelector(`script[src="${src}"]`)) {
                res();
                return;
            }
            const s = document.createElement('script');
            s.src = src;
            s.async = false;
            s.onload = () => res();
            s.onerror = (e) => rej(new Error(`Failed to load ${src}`));
            document.head.appendChild(s);
        });

        loadScript('/vendor/d3.min.js')
            .then(() => loadScript('/vendor/force-graph.min.js'))
            .then(() => {
                console.log('[Graph] Библиотеки D3 и ForceGraph успешно загружены в фоновом режиме');
                resolve();
            })
            .catch(err => {
                console.error('[Graph Vendor Load Error]', err);
                graphVendorLoadingPromise = null;
                reject(err);
            });
    });
    return graphVendorLoadingPromise;
}

// Цвета категорий узлов (Neon Dark Cyber theme)
const KG_CATEGORY_COLORS = {
    'authority': '#f59e0b',     // Amber / Gold
    'instance': '#06b6d4',      // Cyan
    'condition': '#10b981',     // Emerald
    'exception': '#f43f5e',     // Rose
    'legal_status': '#a855f7',  // Violet / Purple
    'default': '#3b82f6'        // Blue
};

const KG_CATEGORY_NAMES = {
    'authority': 'Орган / Компетенция',
    'instance': 'Инстанция / Звено',
    'condition': 'Условие / Основание',
    'exception': 'Исключение / Изъятие',
    'legal_status': 'Правовой статус'
};

const KG_RELATION_STYLES = {
    'subject_to_jurisdiction': {
        color: '#60a5fa',        // Яркий синий (структура/институт)
        lightColor: '#2563eb',
        label: 'входит в структуру',
        icon: 'schema',
        borderClass: 'border-blue-500/30 hover:border-blue-500',
        textClass: 'text-blue-600 dark:text-blue-400',
        bgClass: 'bg-blue-50/70 dark:bg-blue-950/40'
    },
    'demarcated_from': {
        color: '#fbbf24',        // Яркий янтарный (разграничение компетенции)
        lightColor: '#d97706',
        label: 'разграничивается с',
        icon: 'compare_arrows',
        borderClass: 'border-amber-500/30 hover:border-amber-500',
        textClass: 'text-amber-600 dark:text-amber-400',
        bgClass: 'bg-amber-50/70 dark:bg-amber-950/40'
    },
    'appealed_to': {
        color: '#22d3ee',        // Яркий циан (обжалование / инстанция)
        lightColor: '#0891b2',
        label: 'обжалуется в',
        icon: 'upgrade',
        borderClass: 'border-cyan-500/30 hover:border-cyan-500',
        textClass: 'text-cyan-600 dark:text-cyan-400',
        bgClass: 'bg-cyan-50/70 dark:bg-cyan-950/40'
    },
    'excludes_application': {
        color: '#fb7185',        // Яркий кораллово-розовый (исключение нормы)
        lightColor: '#e11d48',
        label: 'исключает применение',
        icon: 'block',
        borderClass: 'border-rose-500/30 hover:border-rose-500',
        textClass: 'text-rose-600 dark:text-rose-400',
        bgClass: 'bg-rose-50/70 dark:bg-rose-950/40'
    },
    'default': {
        color: '#94a3b8',        // Светло-серебристый сланец
        lightColor: '#64748b',
        label: 'связь',
        icon: 'arrow_forward',
        borderClass: 'border-neutral-300 dark:border-neutral-700 hover:border-primary',
        textClass: 'text-neutral-600 dark:text-neutral-400',
        bgClass: 'bg-neutral-50 dark:bg-neutral-800/60'
    }
};

function getKgRelationStyle(rel) {
    const key = (rel || '').toLowerCase().trim().replace(/[\s-]+/g, '_');
    return KG_RELATION_STYLES[key] || KG_RELATION_STYLES['default'];
}

function getKgRelationChipClass(rel) {
    const key = (rel || '').toLowerCase().trim().replace(/[\s-]+/g, '_');
    if (key === 'subject_to_jurisdiction') return 'chip-relation chip-relation-jurisdiction';
    if (key === 'demarcated_from') return 'chip-relation chip-relation-demarcated';
    if (key === 'appealed_to') return 'chip-relation chip-relation-appealed';
    if (key === 'excludes_application') return 'chip-relation chip-relation-excluded';
    return 'chip-relation chip-relation-default';
}

function getKgNodeColor(category) {
    return KG_CATEGORY_COLORS[category] || KG_CATEGORY_COLORS['default'];
}

function getKgRelationLinkColor(relation, isLit, isDark, softMultiplier = 1.0) {
    const key = (relation || '').toLowerCase().trim().replace(/[\s-]+/g, '_');
    const style = getKgRelationStyle(key);

    if (isLit) {
        // Подсвеченное / активное состояние: сочные чистые цвета как в основном приложении
        return isDark ? (style.color || '#38bdf8') : (style.lightColor || style.color || '#0284c7');
    }

    // Спокойное состояние: яркие, насыщенные цвета приложения без блеклости
    if (key === 'subject_to_jurisdiction') {
        return isDark
            ? `rgba(96, 165, 250, ${Math.min(1.0, 0.52 * softMultiplier)})`
            : `rgba(37, 99, 235, ${Math.min(1.0, 0.45 * softMultiplier)})`;
    }
    if (key === 'demarcated_from') {
        return isDark
            ? `rgba(251, 191, 36, ${Math.min(1.0, 0.65 * softMultiplier)})`
            : `rgba(217, 119, 6, ${Math.min(1.0, 0.55 * softMultiplier)})`;
    }
    if (key === 'appealed_to') {
        return isDark
            ? `rgba(34, 211, 238, ${Math.min(1.0, 0.65 * softMultiplier)})`
            : `rgba(8, 145, 178, ${Math.min(1.0, 0.55 * softMultiplier)})`;
    }
    if (key === 'excludes_application') {
        return isDark
            ? `rgba(251, 113, 133, ${Math.min(1.0, 0.65 * softMultiplier)})`
            : `rgba(225, 29, 72, ${Math.min(1.0, 0.55 * softMultiplier)})`;
    }
    return isDark
        ? `rgba(148, 163, 184, ${Math.min(1.0, 0.42 * softMultiplier)})`
        : `rgba(100, 116, 139, ${Math.min(1.0, 0.38 * softMultiplier)})`;
}

function getCleanGraphData() {
    if (!currentKgGraphData || !currentKgGraphData.nodes || currentKgGraphData.nodes.length === 0) {
        return null;
    }

    // 1. Clean nodes: preserve parent_id, hierarchy levels, and real-time FSRS learning states
    const seenNodeIds = new Set();
    const nodes = [];
    for (const n of currentKgGraphData.nodes) {
        if (!n || !n.id) continue;
        const nId = String(n.id);
        if (seenNodeIds.has(nId)) continue;
        seenNodeIds.add(nId);
        const lvl = n.level !== undefined ? Number(n.level) : (n.parent_id ? 2 : 1);
        const cardState = n.card_state !== undefined ? n.card_state : (n.is_learned ? 2 : 0);
        nodes.push({
            id: nId,
            name: n.name || n.id,
            category: n.category || 'authority',
            summary: n.summary || '',
            level: lvl,
            parent_id: n.parent_id ? String(n.parent_id) : undefined,
            val: lvl === 0 ? 14 : (lvl === 1 ? 8 : 4.5),
            card_id: n.card_id,
            card_state: cardState,
            is_learned: Boolean(n.is_learned || cardState > 0),
            reps: n.reps || 0,
            learned_count: n.learned_count || 0,
            total_leaves: n.total_leaves || 0,
            mastered_count: n.mastered_count || 0
        });
    }

    const nodeMap = new Map(nodes.map(n => [n.id, n]));
    const nodeIds = seenNodeIds;

    // 2. Clean links: D3 converts source/target to node object references.
    // We always extract the underlying ID string to prevent object-in-Set lookup failures.
    const rawEdges = (currentKgGraphData.edges || currentKgGraphData.links || []);
    const links = rawEdges
        .map(e => {
            const s = (typeof e.source === 'object' && e.source !== null) ? String(e.source.id) : String(e.source);
            const t = (typeof e.target === 'object' && e.target !== null) ? String(e.target.id) : String(e.target);
            return {
                source: s,
                target: t,
                relation: e.relation || '',
                label: e.label || '',
                __key: getGraphLinkKey(s, t)
            };
        })
        .filter(e => nodeIds.has(e.source) && nodeIds.has(e.target) && e.source !== e.target);

    // Compute __curvature for reciprocal or parallel links
    const pairMap = new Map();
    links.forEach(l => {
        const pairKey = l.source < l.target ? `${l.source}~${l.target}` : `${l.target}~${l.source}`;
        if (!pairMap.has(pairKey)) {
            pairMap.set(pairKey, []);
        }
        pairMap.get(pairKey).push(l);
    });

    pairMap.forEach(group => {
        if (group.length === 1) {
            group[0].__curvature = 0;
        } else if (group.length === 2) {
            const [l1, l2] = group;
            if (l1.source === l2.target && l1.target === l2.source) {
                // Reciprocal edges A->B and B->A bow away from each other
                l1.__curvature = 0.2;
                l2.__curvature = 0.2;
            } else {
                // Parallel edges in the same direction A->B and A->B
                l1.__curvature = 0.2;
                l2.__curvature = -0.2;
            }
        } else {
            group.forEach((l, idx) => {
                const sign = (idx % 2 === 0) ? 1 : -1;
                const factor = Math.ceil((idx + 1) / 2);
                l.__curvature = sign * factor * 0.18;
            });
        }
    });

    // 3. Fallback: If any level 2 concept lacks parent_id, infer it from connecting edge to a level 1 institute
    links.forEach(l => {
        const sNode = nodeMap.get(l.source);
        const tNode = nodeMap.get(l.target);
        if (sNode && tNode) {
            if (sNode.level === 1 && tNode.level === 2 && !tNode.parent_id) {
                tNode.parent_id = sNode.id;
            } else if (tNode.level === 1 && sNode.level === 2 && !sNode.parent_id) {
                sNode.parent_id = tNode.id;
            }
        }
    });

    return { nodes, links };
}

window.openKnowledgeGraphModal = function(targetSubject) {
    const sub = targetSubject || getActiveDeckSubject();
    currentKgSubject = sub;

    // Фоновая предварительная загрузка библиотек физики графа
    if (!window.ForceGraph || !window.d3) {
        ensureGraphVendorLoaded().catch(() => {});
    }

    const modal = document.getElementById('knowledge-graph-modal');
    if (!modal) return;
    
    modal.classList.remove('hidden');

    const badge = document.getElementById('kg-subject-badge');
    if (badge) badge.textContent = sub.toUpperCase();

    // Preserve selected view or default to tree view initially
    switchKgView(currentKgView || 'tree');
    loadKnowledgeGraph(sub);
};

window.closeKnowledgeGraphModal = function() {
    const modal = document.getElementById('knowledge-graph-modal');
    if (modal) modal.classList.add('hidden');
    closeKgNodeDrawer();
    if (document.fullscreenElement && document.exitFullscreen) {
        document.exitFullscreen().catch(() => {});
    }
    const icon = document.getElementById('kg-fullscreen-icon');
    if (icon) icon.textContent = 'fullscreen';
    if (currentForceGraphInstance && currentForceGraphInstance.pauseAnimation) {
        currentForceGraphInstance.pauseAnimation();
    }
};

window.toggleKgFullscreen = function() {
    const modal = document.getElementById('knowledge-graph-modal');
    const icon = document.getElementById('kg-fullscreen-icon');
    if (!modal) return;

    if (!document.fullscreenElement) {
        if (modal.requestFullscreen) {
            modal.requestFullscreen().catch(() => {});
        }
        if (icon) icon.textContent = 'fullscreen_exit';
    } else {
        if (document.exitFullscreen) {
            document.exitFullscreen().catch(() => {});
        }
        if (icon) icon.textContent = 'fullscreen';
    }

    setTimeout(() => {
        if (currentForceGraphInstance && currentKgView === 'graph') {
            const wrapper = document.getElementById('kg-graph-canvas-wrapper');
            if (wrapper) {
                const w = wrapper.clientWidth || window.innerWidth;
                const h = wrapper.clientHeight || (window.innerHeight - 120);
                currentForceGraphInstance.width(w).height(h);
                currentForceGraphInstance.zoomToFit(400, 30);
            }
        }
    }, 250);
};

document.addEventListener('fullscreenchange', () => {
    const icon = document.getElementById('kg-fullscreen-icon');
    if (icon) {
        icon.textContent = document.fullscreenElement ? 'fullscreen_exit' : 'fullscreen';
    }
    if (currentForceGraphInstance && currentKgView === 'graph') {
        setTimeout(() => {
            const wrapper = document.getElementById('kg-graph-canvas-wrapper');
            if (wrapper) {
                const w = wrapper.clientWidth || window.innerWidth;
                const h = wrapper.clientHeight || (window.innerHeight - 120);
                currentForceGraphInstance.width(w).height(h);
                currentForceGraphInstance.zoomToFit(400, 30);
            }
        }, 200);
    }
});

window.switchKgView = function(viewType) {
    currentKgView = viewType;
    const treeView = document.getElementById('kg-tree-view');
    const graphView = document.getElementById('kg-graph-view');
    const tabTree = document.getElementById('kg-tab-tree');
    const tabGraph = document.getElementById('kg-tab-graph');

    const activeTabClass = "px-3 py-1 text-xs font-mono font-bold uppercase rounded-lg transition-all bg-primary text-on-primary shadow-xs flex items-center gap-1 cursor-pointer";
    const inactiveTabClass = "px-3 py-1 text-xs font-mono font-bold uppercase rounded-lg transition-all text-neutral-500 hover:text-primary flex items-center gap-1 cursor-pointer";

    if (viewType === 'tree') {
        if (treeView) treeView.classList.remove('hidden');
        if (graphView) graphView.classList.add('hidden');
        if (tabTree) tabTree.className = activeTabClass;
        if (tabGraph) tabGraph.className = inactiveTabClass;
    } else {
        if (treeView) treeView.classList.add('hidden');
        if (graphView) graphView.classList.remove('hidden');
        if (tabTree) tabTree.className = inactiveTabClass;
        if (tabGraph) tabGraph.className = activeTabClass;

        // Initialize or resize 2D Canvas Force Graph after reflow
        if (currentKgGraphData) {
            requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                    initForceGraph(currentKgGraphData);
                });
            });
        }
    }
};

window.loadKnowledgeGraph = async function(subject) {
    let sub = subject || currentKgSubject || getActiveDeckSubject();
    if (!sub || sub === 'all') {
        const sel = document.getElementById('subject-selector');
        if (sel && sel.options) {
            for (let i = 0; i < sel.options.length; i++) {
                if (sel.options[i].value && sel.options[i].value !== 'all') {
                    sub = sel.options[i].value;
                    break;
                }
            }
        }
    }

    const badge = document.getElementById('kg-subject-badge');
    const loading = document.getElementById('kg-loading');
    const emptyState = document.getElementById('kg-empty-state');
    const treeView = document.getElementById('kg-tree-view');
    const countBadge = document.getElementById('kg-node-count-badge');

    if (!sub || sub === 'all') {
        if (loading) loading.classList.add('hidden');
        if (emptyState) {
            emptyState.classList.remove('hidden');
            const h4 = emptyState.querySelector('h4');
            if (h4) h4.textContent = 'Колоды пока отсутствуют';
            const p = emptyState.querySelector('p');
            if (p) {
                p.textContent = 'У вас пока нет колод для построения графа знаний. Загрузите учебный материал в профиле или откройте демонстрационный курс.';
            }
            const actions = emptyState.querySelector('#kg-empty-actions') || emptyState.querySelector('.flex.flex-col, .flex.gap-2') || emptyState.querySelector('div:last-child');
            if (actions) {
                actions.innerHTML = `
                    <button onclick="window.loadKnowledgeGraph('sudoustroystvo')" class="px-4 py-2 bg-primary text-on-primary font-mono text-xs font-bold uppercase rounded-xl transition-all shadow-xs cursor-pointer flex items-center justify-center gap-1.5">
                        <span class="material-symbols-outlined text-[16px]">play_lesson</span>
                        <span>[ Открыть демо-курс (Судоустройство РФ) ]</span>
                    </button>
                `;
            }
        }
        if (treeView) treeView.innerHTML = '';
        if (countBadge) countBadge.textContent = '0 узлов';
        if (badge) badge.textContent = '—';
        currentKgGraphData = null;
        currentKgTreeData = null;
        return;
    }

    currentKgSubject = sub;
    if (badge) badge.textContent = sub.toUpperCase();

    if (loading) loading.classList.remove('hidden');
    if (emptyState) emptyState.classList.add('hidden');
    closeKgNodeDrawer();

    try {
        const res = await apiFetch(`/api/knowledge-graph?subject=${encodeURIComponent(sub)}`);
        if (!res.ok) {
            // Check if 404
            if (emptyState) emptyState.classList.remove('hidden');
            if (treeView) treeView.innerHTML = '';
            if (countBadge) countBadge.textContent = '0 узлов';
            currentKgGraphData = null;
            currentKgTreeData = null;
            return;
        }

        const data = await res.json();
        currentKgSubject = data.subject || sub;
        if (badge) badge.textContent = currentKgSubject.toUpperCase();
        currentKgGraphData = data.graph_data;
        currentKgTreeData = data.tree_data;

        const nodesCount = (currentKgGraphData && currentKgGraphData.nodes) ? currentKgGraphData.nodes.length : 0;
        if (countBadge) countBadge.textContent = `${nodesCount} узлов`;

        if (data.is_empty || nodesCount === 0) {
            if (emptyState) emptyState.classList.remove('hidden');
            if (treeView) treeView.innerHTML = '';
            return;
        }

        // Render DOM Mindmap Tree
        if (treeView) {
            treeView.innerHTML = '';
            if (currentKgTreeData) {
                renderKnowledgeTreeNode(currentKgTreeData, treeView, 0);
            } else if (currentKgGraphData.nodes.length > 0) {
                // Fallback: render flat cards if tree_data wasn't generated
                currentKgGraphData.nodes.forEach(node => {
                    renderKnowledgeTreeNode(node, treeView, 0);
                });
            }
        }

        // If currently in graph view, render canvas
        if (currentKgView === 'graph') {
            setTimeout(() => {
                initForceGraph(currentKgGraphData);
            }, 50);
        }

    } catch (e) {
        console.error("Сбой загрузки каркаса знаний:", e);
        if (emptyState) emptyState.classList.remove('hidden');
    } finally {
        if (loading) loading.classList.add('hidden');
    }
};

window.loadSeedOrDemoGraph = async function() {
    let sub = currentKgSubject || getActiveDeckSubject();
    if (!sub || sub === 'all') {
        const sel = document.getElementById('subject-selector');
        if (sel && sel.options) {
            for (let i = 0; i < sel.options.length; i++) {
                if (sel.options[i].value && sel.options[i].value !== 'all') {
                    sub = sel.options[i].value;
                    break;
                }
            }
        }
    }
    if (!sub || sub === 'all') sub = 'sudoustroystvo';
    currentKgSubject = sub;
    const badge = document.getElementById('kg-subject-badge');
    if (badge) badge.textContent = sub.toUpperCase();
    await rebuildKnowledgeGraph();
};

window.rebuildKnowledgeGraph = async function() {
    let sub = currentKgSubject || getActiveDeckSubject();
    if (!sub || sub === 'all') {
        const sel = document.getElementById('subject-selector');
        if (sel && sel.options) {
            for (let i = 0; i < sel.options.length; i++) {
                if (sel.options[i].value && sel.options[i].value !== 'all') {
                    sub = sel.options[i].value;
                    break;
                }
            }
        }
    }
    if (!sub || sub === 'all') {
        if (window.showNotification) {
            window.showNotification('Сначала выберите предмет для построения графа.', 'warning');
        }
        return;
    }

    const loading = document.getElementById('kg-loading');
    const rebuildIcon = document.getElementById('kg-rebuild-icon');
    
    if (rebuildIcon) rebuildIcon.classList.add('animate-spin');
    if (loading) loading.classList.remove('hidden');

    try {
        const res = await apiFetch(`/api/knowledge-graph/rebuild?subject=${encodeURIComponent(sub)}`, {
            method: 'POST'
        });
        
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            window.showNotification(err.detail || 'Ошибка при перестроении графа', 'error');
            return;
        }

        const data = await res.json();
        currentKgSubject = data.subject || sub;
        currentKgGraphData = data.graph_data;
        currentKgTreeData = data.tree_data;

        const badge = document.getElementById('kg-subject-badge');
        if (badge) badge.textContent = currentKgSubject.toUpperCase();

        const countBadge = document.getElementById('kg-node-count-badge');
        const nodesCount = (currentKgGraphData && currentKgGraphData.nodes) ? currentKgGraphData.nodes.length : 0;
        if (countBadge) countBadge.textContent = `${nodesCount} узлов`;

        const emptyState = document.getElementById('kg-empty-state');
        if (emptyState) emptyState.classList.add('hidden');

        const treeView = document.getElementById('kg-tree-view');
        if (treeView) {
            treeView.innerHTML = '';
            if (currentKgTreeData) {
                renderKnowledgeTreeNode(currentKgTreeData, treeView, 0);
            } else if (currentKgGraphData && currentKgGraphData.nodes) {
                currentKgGraphData.nodes.forEach(node => {
                    renderKnowledgeTreeNode(node, treeView, 0);
                });
            }
        }

        if (currentKgView === 'graph') {
            setTimeout(() => {
                if (currentForceGraphInstance) {
                    setGraphLayout(currentKgLayout || 'force');
                } else {
                    initForceGraph(currentKgGraphData);
                }
            }, 50);
        }

        window.showNotification(`Граф знаний перестроен из актуальных карточек (${nodesCount} узлов)`, 'success');
    } catch (e) {
        console.error("Сбой перестроения графа:", e);
        window.showNotification('Сетевая ошибка при перестроении графа', 'error');
    } finally {
        if (rebuildIcon) rebuildIcon.classList.remove('animate-spin');
        if (loading) loading.classList.add('hidden');
    }
};

function renderKnowledgeTreeNode(node, container, depth) {
    if (!node) return;

    const nodeWrapper = document.createElement('div');
    nodeWrapper.className = depth === 0 ? "mb-2.5" : "tree-branch-container my-1.5";

    const hasChildren = node.children && node.children.length > 0;
    const cat = node.category || 'authority';
    const badgeClass = `badge-${cat}`;
    const catLabel = KG_CATEGORY_NAMES[cat] || cat.toUpperCase();

    const cardState = node.card_state !== undefined ? node.card_state : (node.is_learned ? 2 : 0);
    let statusBadgeHTML = '';
    if (cardState === 2) {
        statusBadgeHTML = `<span class="px-1.5 py-0.2 rounded text-[9px] font-mono font-bold uppercase bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20">Изучено</span>`;
    } else if (cardState === 1 || cardState === 3) {
        statusBadgeHTML = `<span class="px-1.5 py-0.2 rounded text-[9px] font-mono font-bold uppercase bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20">В процессе</span>`;
    } else if (depth > 0) {
        statusBadgeHTML = `<span class="px-1.5 py-0.2 rounded text-[9px] font-mono text-neutral-400 dark:text-neutral-500 uppercase">Новое</span>`;
    }

    const card = document.createElement('div');
    card.className = "tree-node-card p-3 rounded-xl bg-surface-container-lowest border border-neutral-200 dark:border-neutral-800 hover:border-neutral-400 transition-all flex items-start justify-between gap-2.5 cursor-pointer shadow-xs select-none";
    
    card.innerHTML = `
        <div class="flex items-start gap-2.5 min-w-0">
            ${hasChildren ? `
                <button class="tree-toggle-btn text-neutral-400 hover:text-primary p-0.5 mt-0.5 rounded transition-transform duration-200" title="Свернуть/Развернуть">
                    <span class="material-symbols-outlined text-[16px]">arrow_drop_down</span>
                </button>
            ` : `
                <span class="w-1.5 h-1.5 rounded-full ${cardState === 2 ? 'bg-emerald-500' : (cardState > 0 ? 'bg-blue-500' : 'bg-neutral-400 dark:bg-neutral-600')} mt-2 ml-1 shrink-0"></span>
            `}
            <div class="flex flex-col min-w-0">
                <div class="flex items-center gap-1.5 flex-wrap">
                    <span class="font-bold text-xs sm:text-sm text-neutral-900 dark:text-neutral-100 font-mono">${escapeHTML(node.name || node.id)}</span>
                    <span class="px-1.5 py-0.2 rounded text-[9px] font-mono font-bold uppercase ${badgeClass}">${escapeHTML(catLabel)}</span>
                    ${statusBadgeHTML}
                    ${node.total_leaves ? `<span class="text-[9px] font-mono text-neutral-400 font-medium">(${node.learned_count || 0}/${node.total_leaves})</span>` : ''}
                </div>
                ${node.summary ? `
                    <p class="text-[11px] text-neutral-600 dark:text-neutral-400 leading-snug mt-1 font-sans line-clamp-2">${escapeHTML(node.summary)}</p>
                ` : ''}
            </div>
        </div>
        <button class="text-neutral-400 hover:text-primary p-1 shrink-0 rounded transition-colors" title="Подробнее">
            <span class="material-symbols-outlined text-[16px]">info</span>
        </button>
    `;

    // Click on node card opens drawer
    card.onclick = (e) => {
        // If clicked on toggle button, handle collapse/expand
        if (e.target.closest('.tree-toggle-btn')) {
            e.stopPropagation();
            const btn = e.target.closest('.tree-toggle-btn');
            const childrenWrapper = nodeWrapper.querySelector('.tree-children-container');
            if (childrenWrapper) {
                const isHidden = childrenWrapper.classList.toggle('hidden');
                btn.style.transform = isHidden ? 'rotate(-90deg)' : 'rotate(0deg)';
            }
            return;
        }
        showKgNodeDrawer(node);
    };

    nodeWrapper.appendChild(card);

    // Recursively render children
    if (hasChildren) {
        const childrenContainer = document.createElement('div');
        childrenContainer.className = "tree-children-container";
        node.children.forEach(child => {
            renderKnowledgeTreeNode(child, childrenContainer, depth + 1);
        });
        nodeWrapper.appendChild(childrenContainer);
    }

    container.appendChild(nodeWrapper);
}

function wrapNodeText(text, maxChars = 16) {
    text = String(text || '');
    if (!text || text.length <= maxChars) return [text || ''];
    const words = text.split(' ');
    const lines = [];
    let cur = '';
    for (const w of words) {
        if ((cur ? (cur + ' ' + w) : w).length <= maxChars) {
            cur = cur ? (cur + ' ' + w) : w;
        } else {
            if (cur) lines.push(cur);
            cur = w;
        }
    }
    if (cur) lines.push(cur);
    if (lines.length > 2) {
        return [lines[0], lines.slice(1).join(' ').substring(0, maxChars - 2) + '..'];
    }
    return lines;
}

let isInitialLayoutFit = true;
let isInitializingGraph = false;
let kgViewportBounds = { minX: -1e6, maxX: 1e6, minY: -1e6, maxY: 1e6 };
let searchHighlightNodes = new Set();
let searchHighlightLinkKeys = new Set();
let searchBackboneNodes = new Set();
let searchBackboneLinkKeys = new Set();
let activeSearchTargetId = null;
let activeSelectedLink = null;
let nodeClickStep = 0; // 0 = idle, 1 = node focused (drawer open), 2 = spotlight backbone & relations
let isDrawerCollapsed = false;
let lastPointerDownPos = null;
let currentLinksFilter = 'all';

let hoveredNode = null;
let hoveredLink = null;
let hoveredLinkKeys = new Set();
let hoveredNeighborNodeIds = new Set();
let kgResizeHandler = null;

function getGraphLinkKey(source, target) {
    const s = String((typeof source === 'object' && source !== null) ? source.id : source);
    const t = String((typeof target === 'object' && target !== null) ? target.id : target);
    return `${s}->${t}`;
}

function getUndirectedLinkKey(source, target) {
    const s = String((typeof source === 'object' && source !== null) ? source.id : source);
    const t = String((typeof target === 'object' && target !== null) ? target.id : target);
    return s < t ? `${s}--${t}` : `${t}--${s}`;
}

function applyLayoutForces(graphInstance, layoutType) {
    if (!graphInstance) return;

    const graphData = (graphInstance.graphData && typeof graphInstance.graphData === 'function') ? graphInstance.graphData() : null;
    const nodes = (graphData && graphData.nodes && graphData.nodes.length > 0)
        ? graphData.nodes
        : ((getCleanGraphData() || {}).nodes || []);
    if (!nodes || nodes.length === 0) return;

    const nodeCount = nodes.length;
    const isLarge = nodeCount > 40;
    const nodeMap = new Map(nodes.map(n => [String(n.id), n]));

    const rootNodes = nodes.filter(n => n.level === 0);
    const branches = nodes.filter(n => n.level === 1);
    const branchCount = Math.max(1, branches.length);

    // Группировка дочерних понятий под их родительскими институтами
    const branchLeavesMap = new Map();
    branches.forEach(b => branchLeavesMap.set(String(b.id), []));
    nodes.filter(n => n.level === 2).forEach(leaf => {
        const pId = String(leaf.parent_id || '');
        if (!branchLeavesMap.has(pId)) {
            branchLeavesMap.set(pId, []);
        }
        branchLeavesMap.get(pId).push(leaf);
    });

    // Сбрасываем позиционные силы перед назначением геометрии
    graphInstance.d3Force('radial', null);
    graphInstance.d3Force('x', null);
    graphInstance.d3Force('y', null);
    graphInstance.d3Force('center', null);

    // Снимаем жесткую фиксацию с не-корневых узлов
    nodes.forEach(n => {
        if (n.level !== 0) {
            n.fx = undefined;
            n.fy = undefined;
        }
    });

    // 1. Сила отталкивания (Charge Repulsion) с адаптивным масштабированием для защиты от зависания и взрыва координат
    if (graphInstance.d3Force('charge')) {
        graphInstance.d3Force('charge')
            .strength(node => {
                const lvl = (node.level !== undefined) ? node.level : 1;
                if (layoutType === 'tree' || layoutType === 'horizontal' || layoutType === 'radial') {
                    return -25; // Деликатное отталкивание в геометрических режимах
                }
                // Органический режим: для больших графов (>50 узлов) используем сбалансированное отталкивание
                if (isLarge) {
                    if (lvl === 0) return -800;
                    if (lvl === 1) return -250;
                    return -60;
                }
                if (lvl === 0) return -1800;
                if (lvl === 1) return -450;
                return -120;
            })
            .distanceMax(isLarge ? Math.min(850, 400 + nodeCount * 1.5) : 1600);
    }

    // 2. Сила связей (Link Force)
    if (graphInstance.d3Force('link')) {
        graphInstance.d3Force('link')
            .distance(link => {
                const s = (typeof link.source === 'object' && link.source !== null) ? link.source : (nodeMap.get(String(link.source)) || {});
                const t = (typeof link.target === 'object' && link.target !== null) ? link.target : (nodeMap.get(String(link.target)) || {});
                const sLvl = (s.level !== undefined) ? s.level : 1;
                const tLvl = (t.level !== undefined) ? t.level : 1;

                if (layoutType === 'tree' || layoutType === 'horizontal' || layoutType === 'radial') {
                    if (sLvl === 0 || tLvl === 0) return 200;
                    return 50;
                }

                // Органический режим (Force)
                if (sLvl === 0 || tLvl === 0) {
                    return Math.max(260, Math.min(420, 200 + branchCount * 3.0));
                }
                if ((sLvl === 1 && tLvl === 2) || (sLvl === 2 && tLvl === 1)) {
                    return 70;
                }
                return 110;
            })
            .strength(link => {
                const s = (typeof link.source === 'object' && link.source !== null) ? link.source : (nodeMap.get(String(link.source)) || {});
                const t = (typeof link.target === 'object' && link.target !== null) ? link.target : (nodeMap.get(String(link.target)) || {});
                const sLvl = (s.level !== undefined) ? s.level : 1;
                const tLvl = (t.level !== undefined) ? t.level : 1;

                if (layoutType === 'tree' || layoutType === 'horizontal' || layoutType === 'radial') {
                    return 0.12; // Мягкая связность, геометрия управляется целевыми позициями
                }

                if (sLvl === 0 || tLvl === 0) return 0.85;
                if ((sLvl === 1 && tLvl === 2) || (sLvl === 2 && tLvl === 1)) return 0.75;
                return 0.04; // Деликатные кросс-связи
            });
    }

    // 3. Сила коллизии (Collide) — 1 итерация для плавной производительности на мобильных WebView
    if (window.d3 && window.d3.forceCollide) {
        graphInstance.d3Force('collide', window.d3.forceCollide()
            .radius(node => {
                const lvl = (node.level !== undefined) ? node.level : 2;
                if (isLarge) {
                    if (lvl === 0) return 36;
                    if (lvl === 1) return 26;
                    return 18;
                }
                if (lvl === 0) return 50;
                if (lvl === 1) return 38;
                return 28;
            })
            .strength(isLarge ? 0.45 : 0.75)
            .iterations(1)
        );
    }

    // 4. Позиционные силы для чистых упорядоченных раскладок
    const targetXMap = new Map();
    const targetYMap = new Map();

    if (layoutType === 'tree') {
        // Дерево: СВЕРХУ ВНИЗ (Иерархический веер институтов с распределением понятий по сетке)
        rootNodes.forEach(r => {
            r.fx = 0;
            r.fy = -480;
            targetXMap.set(String(r.id), 0);
            targetYMap.set(String(r.id), -480);
        });

        // Рассчитываем ширину институтов по количеству их дочерних понятий
        const branchWidths = branches.map(b => {
            const leaves = branchLeavesMap.get(String(b.id)) || [];
            if (leaves.length <= 1) return 150;
            const cols = Math.min(4, Math.max(2, Math.ceil(Math.sqrt(leaves.length * 1.3))));
            return Math.max(160, cols * 76 + 20);
        });
        const totalTreeWidth = branchWidths.reduce((sum, w) => sum + w, 0);
        let curX = -totalTreeWidth / 2;

        branches.forEach((b, idx) => {
            const bWidth = branchWidths[idx];
            const bX = curX + bWidth / 2;
            curX += bWidth;

            const bY = (idx % 2 === 0) ? -230 : -150;
            targetXMap.set(String(b.id), bX);
            targetYMap.set(String(b.id), bY);

            const leaves = branchLeavesMap.get(String(b.id)) || [];
            if (leaves.length === 1) {
                targetXMap.set(String(leaves[0].id), bX);
                targetYMap.set(String(leaves[0].id), bY + 110);
            } else if (leaves.length > 1) {
                // Распределяем дочерние понятия веером по сетке (не в одну колонку!)
                const cols = Math.min(4, Math.max(2, Math.ceil(Math.sqrt(leaves.length * 1.3))));
                const colSpacing = 76;
                const rowSpacing = 52;
                leaves.forEach((leaf, lIdx) => {
                    const col = lIdx % cols;
                    const row = Math.floor(lIdx / cols);
                    const totalRows = Math.ceil(leaves.length / cols);
                    const itemsInRow = (row === totalRows - 1 && leaves.length % cols !== 0)
                        ? (leaves.length % cols)
                        : cols;
                    const leafX = bX + (col - (itemsInRow - 1) / 2) * colSpacing;
                    const leafY = bY + 110 + (row * rowSpacing);
                    targetXMap.set(String(leaf.id), leafX);
                    targetYMap.set(String(leaf.id), leafY);
                });
            }
        });

        nodes.forEach(n => {
            const id = String(n.id);
            if (!targetXMap.has(id)) {
                targetXMap.set(id, 0);
                targetYMap.set(id, 100);
            }
        });

        if (window.d3 && window.d3.forceX && window.d3.forceY) {
            graphInstance.d3Force('x', window.d3.forceX(node => targetXMap.get(String(node.id)) || 0).strength(0.92));
            graphInstance.d3Force('y', window.d3.forceY(node => targetYMap.get(String(node.id)) || 0).strength(0.92));
        }

    } else if (layoutType === 'horizontal') {
        // Горизонтально: СЛЕВА НАПРАВО (Иерархический веер институтов)
        rootNodes.forEach(r => {
            r.fx = -480;
            r.fy = 0;
            targetXMap.set(String(r.id), -480);
            targetYMap.set(String(r.id), 0);
        });

        const branchHeights = branches.map(b => {
            const leaves = branchLeavesMap.get(String(b.id)) || [];
            if (leaves.length <= 1) return 130;
            const rows = Math.min(4, Math.max(2, Math.ceil(Math.sqrt(leaves.length * 1.3))));
            return Math.max(140, rows * 64 + 20);
        });
        const totalTreeHeight = branchHeights.reduce((sum, h) => sum + h, 0);
        let curY = -totalTreeHeight / 2;

        branches.forEach((b, idx) => {
            const bHeight = branchHeights[idx];
            const bY = curY + bHeight / 2;
            curY += bHeight;

            const bX = (idx % 2 === 0) ? -230 : -150;
            targetXMap.set(String(b.id), bX);
            targetYMap.set(String(b.id), bY);

            const leaves = branchLeavesMap.get(String(b.id)) || [];
            if (leaves.length === 1) {
                targetXMap.set(String(leaves[0].id), bX + 110);
                targetYMap.set(String(leaves[0].id), bY);
            } else if (leaves.length > 1) {
                const rows = Math.min(4, Math.max(2, Math.ceil(Math.sqrt(leaves.length * 1.3))));
                const colSpacing = 68;
                const rowSpacing = 52;
                leaves.forEach((leaf, lIdx) => {
                    const row = lIdx % rows;
                    const col = Math.floor(lIdx / rows);
                    const totalCols = Math.ceil(leaves.length / rows);
                    const itemsInCol = (col === totalCols - 1 && leaves.length % rows !== 0)
                        ? (leaves.length % rows)
                        : rows;
                    const leafY = bY + (row - (itemsInCol - 1) / 2) * rowSpacing;
                    const leafX = bX + 110 + (col * colSpacing);
                    targetXMap.set(String(leaf.id), leafX);
                    targetYMap.set(String(leaf.id), leafY);
                });
            }
        });

        nodes.forEach(n => {
            const id = String(n.id);
            if (!targetXMap.has(id)) {
                targetXMap.set(id, 100);
                targetYMap.set(id, 0);
            }
        });

        if (window.d3 && window.d3.forceX && window.d3.forceY) {
            graphInstance.d3Force('x', window.d3.forceX(node => targetXMap.get(String(node.id)) || 0).strength(0.92));
            graphInstance.d3Force('y', window.d3.forceY(node => targetYMap.get(String(node.id)) || 0).strength(0.92));
        }

    } else if (layoutType === 'radial') {
        // Радиальный: Аккуратный просторный цветок с расходящимися секторами (STARBURST)
        rootNodes.forEach(r => {
            r.fx = 0;
            r.fy = 0;
            targetXMap.set(String(r.id), 0);
            targetYMap.set(String(r.id), 0);
        });

        branches.forEach((b, idx) => {
            const angle = (idx / branchCount) * 2 * Math.PI;
            const radius = (idx % 2 === 0) ? 340 : 520;
            const bX = Math.cos(angle) * radius;
            const bY = Math.sin(angle) * radius;
            targetXMap.set(String(b.id), bX);
            targetYMap.set(String(b.id), bY);

            const leaves = branchLeavesMap.get(String(b.id)) || [];
            const angleSpan = Math.min(0.24, (2 * Math.PI / branchCount) * 0.85);
            leaves.forEach((leaf, lIdx) => {
                const row = Math.floor(lIdx / 4);
                const col = lIdx % 4;
                const itemsInRow = (row === Math.floor((leaves.length - 1) / 4))
                    ? ((leaves.length - 1) % 4 + 1)
                    : 4;
                const leafRadius = radius + 110 + (row * 60);
                const angleOffset = itemsInRow > 1
                    ? ((col - (itemsInRow - 1) / 2) * (angleSpan / itemsInRow))
                    : 0;
                const leafX = Math.cos(angle + angleOffset) * leafRadius;
                const leafY = Math.sin(angle + angleOffset) * leafRadius;
                targetXMap.set(String(leaf.id), leafX);
                targetYMap.set(String(leaf.id), leafY);
            });
        });

        nodes.forEach(n => {
            const id = String(n.id);
            if (!targetXMap.has(id)) {
                targetXMap.set(id, 0);
                targetYMap.set(id, 0);
            }
        });

        if (window.d3 && window.d3.forceX && window.d3.forceY) {
            graphInstance.d3Force('x', window.d3.forceX(node => targetXMap.get(String(node.id)) || 0).strength(0.92));
            graphInstance.d3Force('y', window.d3.forceY(node => targetYMap.get(String(node.id)) || 0).strength(0.92));
        }

    } else {
        // Органический (Force)
        rootNodes.forEach(r => {
            r.fx = 0;
            r.fy = 0;
        });
        if (window.d3 && window.d3.forceCenter) {
            graphInstance.d3Force('center', window.d3.forceCenter(0, 0).strength(0.08));
        }
    }
}

window.setGraphLayout = function(layoutType) {
    currentKgLayout = layoutType || 'force';
    isInitialLayoutFit = true;

    const btnForce = document.getElementById('kg-layout-force');
    const btnRadial = document.getElementById('kg-layout-radial');
    const btnTree = document.getElementById('kg-layout-tree');
    const btnLr = document.getElementById('kg-layout-lr');

    const inactiveClass = "p-1.5 sm:px-2.5 sm:py-1 rounded-lg font-bold uppercase transition-all text-neutral-500 hover:text-primary hover:bg-neutral-100 dark:hover:bg-neutral-800 flex items-center gap-1 cursor-pointer";
    const activeClass = "p-1.5 sm:px-2.5 sm:py-1 rounded-lg font-bold uppercase transition-all bg-primary text-on-primary shadow-xs flex items-center gap-1 cursor-pointer";

    if (btnForce) btnForce.className = currentKgLayout === 'force' ? activeClass : inactiveClass;
    if (btnRadial) btnRadial.className = currentKgLayout === 'radial' ? activeClass : inactiveClass;
    if (btnTree) btnTree.className = currentKgLayout === 'tree' ? activeClass : inactiveClass;
    if (btnLr) btnLr.className = currentKgLayout === 'horizontal' ? activeClass : inactiveClass;

    if (!currentForceGraphInstance) return;

    // Сбрасываем зависшие сенсорные выделения связей и узлов при смене раскладки
    hoveredNode = null;
    hoveredLink = null;
    hoveredLinkKeys.clear();
    hoveredNeighborNodeIds.clear();

    const cleanData = getCleanGraphData();
    if (!cleanData) return;

    // Снимаем фиксацию со всех не-корневых узлов
    (cleanData.nodes || []).forEach(n => {
        if (n.level !== 0) {
            n.fx = undefined;
            n.fy = undefined;
        }
    });

    currentForceGraphInstance
        .dagMode(null)
        .onDagError(() => false)
        .graphData(cleanData);

    applyLayoutForces(currentForceGraphInstance, currentKgLayout);

    if (currentForceGraphInstance.d3ReheatSimulation) {
        currentForceGraphInstance.d3ReheatSimulation();
    }
    setTimeout(() => {
        if (currentForceGraphInstance && isInitialLayoutFit) {
            isInitialLayoutFit = false;
            currentForceGraphInstance.zoomToFit(500, 45);
        }
    }, 450);
};

/* ==========================================================================
   ПОИСК ПО ГРАФУ И ПОДСВЕТКА ЛИНИИ СВЯЗЕЙ (SPOTLIGHT SUBGRAPH)
   ========================================================================== */

window.onKgSearchKeyDown = function(e) {
    if (e.key === 'Enter') {
        e.preventDefault();
        window.submitKgSearch();
    }
};

window.submitKgSearch = function() {
    const input = document.getElementById('kg-search-input');
    if (!input) return;
    const q = (input.value || '').trim().toLowerCase();
    input.blur(); // Скрываем виртуальную клавиатуру на мобильных телефонах

    const resBox = document.getElementById('kg-search-results');
    if (resBox) resBox.classList.add('hidden');

    const cleanData = getCleanGraphData();
    if (!cleanData || !cleanData.nodes || cleanData.nodes.length === 0) return;

    if (!q) {
        clearKgSearch();
        return;
    }

    // Ищем лучшее совпадение: точное имя, затем префикс, затем вхождение в имя, затем вхождение в описание
    let match = cleanData.nodes.find(n => (n.name || '').toLowerCase() === q);
    if (!match) {
        match = cleanData.nodes.find(n => (n.name || '').toLowerCase().startsWith(q));
    }
    if (!match) {
        match = cleanData.nodes.find(n => (n.name || '').toLowerCase().includes(q));
    }
    if (!match) {
        match = cleanData.nodes.find(n => (n.summary || '').toLowerCase().includes(q));
    }

    if (match) {
        selectSearchResult(match, true);
    } else {
        if (typeof window.showNotification === 'function') {
            window.showNotification("Понятие не найдено в графе", "info");
        }
    }
};

window.onKgSearchInput = function(query) {
    highlightConceptSearch(query);
};

window.clearKgSearch = function(preserveCamera = false) {
    const input = document.getElementById('kg-search-input');
    if (input) input.value = '';
    const clearBtn = document.getElementById('kg-search-clear');
    if (clearBtn) clearBtn.classList.add('hidden');
    const resBox = document.getElementById('kg-search-results');
    if (resBox) resBox.classList.add('hidden');

    const pill = document.getElementById('kg-selection-pill');
    if (pill) pill.classList.add('hidden');

    searchHighlightNodes.clear();
    searchHighlightLinkKeys.clear();
    searchBackboneNodes.clear();
    searchBackboneLinkKeys.clear();
    activeSearchTargetId = null;
    activeSelectedLink = null;
    nodeClickStep = 0;
    isDrawerCollapsed = false;

    hoveredNode = null;
    hoveredLink = null;
    hoveredLinkKeys.clear();
    hoveredNeighborNodeIds.clear();

    closeKgNodeDrawer();

    if (currentForceGraphInstance) {
        currentForceGraphInstance.refresh();
        if (!preserveCamera) {
            currentForceGraphInstance.zoomToFit(400, 40);
        }
    }
    triggerHaptic('light');
};

window.highlightConceptSearch = function(searchTerm) {
    const clearBtn = document.getElementById('kg-search-clear');
    const resBox = document.getElementById('kg-search-results');

    if (!searchTerm || !searchTerm.trim()) {
        window.clearKgSearch();
        return;
    }

    if (clearBtn) clearBtn.classList.remove('hidden');

    const cleanData = getCleanGraphData();
    if (!cleanData || !cleanData.nodes) return;

    const q = searchTerm.toLowerCase().trim();

    // Поиск по названию узла, описанию и смысловым совпадениям
    const matches = cleanData.nodes.filter(n => {
        const name = (n.name || '').toLowerCase();
        const summary = (n.summary || '').toLowerCase();
        return name.includes(q) || summary.includes(q);
    });

    if (matches.length === 0) {
        if (resBox) {
            resBox.innerHTML = '<div class="p-2 text-neutral-400 text-center">Ничего не найдено</div>';
            resBox.classList.remove('hidden');
        }
        return;
    }

    // Рендер выпадающего списка быстрых совпадений
    if (resBox) {
        resBox.innerHTML = '';
        matches.slice(0, 8).forEach(m => {
            const item = document.createElement('div');
            item.className = 'px-2.5 py-1.5 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg cursor-pointer flex items-center justify-between gap-2 text-neutral-800 dark:text-neutral-200 transition-colors';
            item.innerHTML = `
                <div class="flex items-center gap-1.5 min-w-0">
                    <span class="w-2 h-2 rounded-full shrink-0 bg-neutral-400"></span>
                    <span class="truncate font-medium">${escapeHTML(m.name || m.id)}</span>
                </div>
                <span class="text-[9px] text-neutral-400 uppercase shrink-0 font-mono">${m.level === 0 ? 'КУРС' : (m.level === 1 ? 'ИНСТИТУТ' : 'ПОНЯТИЕ')}</span>
            `;
            item.onclick = () => {
                selectSearchResult(m, true);
            };
            resBox.appendChild(item);
        });
        resBox.classList.remove('hidden');
    }

    // Фокусируемся на первом лучшем совпадении
    selectSearchResult(matches[0], false);
};

function centerCameraOnNode(targetNode) {
    if (!currentForceGraphInstance) return;
    const cleanData = getCleanGraphData();
    const gNodes = (currentForceGraphInstance.graphData && currentForceGraphInstance.graphData().nodes) || (cleanData ? cleanData.nodes : []) || [];
    const liveTarget = gNodes.find(n => String(n.id) === String(targetNode.id)) || targetNode;
    if (typeof liveTarget.x === 'number' && !isNaN(liveTarget.x) && (liveTarget.x !== 0 || liveTarget.y !== 0)) {
        const isMobile = window.innerWidth <= 768;
        const curZoom = currentForceGraphInstance.zoom ? currentForceGraphInstance.zoom() : 1.0;
        const targetZoom = Math.max(curZoom, isMobile ? 1.30 : 1.50);
        const wrapper = document.getElementById('kg-graph-canvas-wrapper');
        const h = wrapper ? wrapper.clientHeight : (window.innerHeight - 150);
        const yOffset = isMobile ? (h * 0.16 / targetZoom) : 0;
        currentForceGraphInstance.centerAt(liveTarget.x, liveTarget.y + yOffset, 500);
        currentForceGraphInstance.zoom(targetZoom, 500);
    }
}

function focusNodeStep1(targetNode) {
    activeSearchTargetId = String(targetNode.id);
    activeSelectedLink = null;
    nodeClickStep = 1;

    searchBackboneNodes.clear();
    searchBackboneLinkKeys.clear();
    searchHighlightNodes.clear();
    searchHighlightNodes.add(activeSearchTargetId);
    searchHighlightLinkKeys.clear();

    // Шаг 1: Фокус исключительно на выбранном узле (шторка развернута, связи еще не подсвечиваются)

    centerCameraOnNode(targetNode);

    const pill = document.getElementById('kg-selection-pill');
    const pillName = document.getElementById('kg-selection-pill-name');
    if (pill && pillName) {
        pillName.textContent = targetNode.name || targetNode.id;
        pill.classList.remove('hidden');
    }

    // Открываем развернутую шторку (полный вид с описанием)
    showKgNodeDrawer(targetNode, false);
    triggerHaptic('light');

    if (currentForceGraphInstance) {
        currentForceGraphInstance.refresh();
    }
}

function focusNodeStep2(targetNode) {
    activeSearchTargetId = String(targetNode.id);
    activeSelectedLink = null;
    nodeClickStep = 2;

    searchBackboneNodes.clear();
    searchBackboneLinkKeys.clear();
    searchHighlightNodes.clear();
    searchHighlightNodes.add(activeSearchTargetId);
    searchHighlightLinkKeys.clear();

    const cleanData = getCleanGraphData();
    const allLinks = (cleanData.links && cleanData.links.length > 0)
        ? cleanData.links
        : ((currentKgGraphData && currentKgGraphData.edges) ? currentKgGraphData.edges : []);

    const nodeMap = new Map(cleanData.nodes.map(n => [String(n.id), n]));
    const rootNode = cleanData.nodes.find(n => n.level === 0);
    const rootId = rootNode ? String(rootNode.id) : null;

    // 1. Магистраль от ядра к институту и целевому узлу
    let currNode = targetNode;
    while (currNode && currNode.parent_id) {
        const pId = String(currNode.parent_id);
        const parentNode = nodeMap.get(pId);
        if (!parentNode) break;

        searchHighlightNodes.add(pId);
        searchBackboneNodes.add(pId);

        const cId = String(currNode.id);
        const pEdge = allLinks.find(e => {
            const s = String((typeof e.source === 'object' && e.source !== null) ? e.source.id : e.source);
            const t = String((typeof e.target === 'object' && e.target !== null) ? e.target.id : e.target);
            return (s === pId && t === cId) || (s === cId && t === pId);
        });

        if (pEdge) {
            const lKey = pEdge.__key || getGraphLinkKey(pEdge.source, pEdge.target);
            searchBackboneLinkKeys.add(lKey);
            searchHighlightLinkKeys.add(lKey);
        }

        currNode = parentNode;
    }

    if (rootId && !searchHighlightNodes.has(rootId)) {
        const topNodeId = currNode ? String(currNode.id) : null;
        if (topNodeId && topNodeId !== rootId) {
            const rootEdge = allLinks.find(e => {
                const s = String((typeof e.source === 'object' && e.source !== null) ? e.source.id : e.source);
                const t = String((typeof e.target === 'object' && e.target !== null) ? e.target.id : e.target);
                return (s === rootId && t === topNodeId) || (s === topNodeId && t === rootId);
            });
            if (rootEdge) {
                searchHighlightNodes.add(rootId);
                searchBackboneNodes.add(rootId);
                const lKey = rootEdge.__key || getGraphLinkKey(rootEdge.source, rootEdge.target);
                searchBackboneLinkKeys.add(lKey);
                searchHighlightLinkKeys.add(lKey);
            }
        }
    }

    // 2. Все связи целевого узла (исходящие и входящие)
    allLinks.forEach(edge => {
        const sId = String((typeof edge.source === 'object' && edge.source !== null) ? edge.source.id : edge.source);
        const tId = String((typeof edge.target === 'object' && edge.target !== null) ? edge.target.id : edge.target);
        if (sId === activeSearchTargetId || tId === activeSearchTargetId) {
            const lKey = edge.__key || getGraphLinkKey(sId, tId);
            searchHighlightLinkKeys.add(lKey);
            searchHighlightNodes.add(sId);
            searchHighlightNodes.add(tId);
        }
    });

    const pill = document.getElementById('kg-selection-pill');
    const pillName = document.getElementById('kg-selection-pill-name');
    if (pill && pillName) {
        pillName.textContent = `${targetNode.name || targetNode.id} • Магистраль и связи (${searchHighlightLinkKeys.size})`;
        pill.classList.remove('hidden');
    }

    // Сворачиваем шторку в компактную полоску (Peek), чтобы весь граф связей был открыт на холсте
    showKgNodeDrawer(targetNode, true);
    triggerHaptic('medium');

    if (currentForceGraphInstance) {
        currentForceGraphInstance.refresh();
    }
}

window.selectSearchResult = function(targetNode, closeDropdown = true) {
    if (!targetNode) return;
    const resBox = document.getElementById('kg-search-results');
    if (closeDropdown && resBox) resBox.classList.add('hidden');
    focusNodeStep1(targetNode);
};

window.focusNodeInGraph = function(nodeId) {
    const cleanData = getCleanGraphData();
    if (!cleanData || !cleanData.nodes) return;
    const target = cleanData.nodes.find(n => String(n.id) === String(nodeId));
    if (!target) return;

    const nId = String(target.id);
    if (activeSearchTargetId !== nId) {
        // Тап по новому узлу -> Шаг 1: Фокус на понятии и развернутая шторка
        focusNodeStep1(target);
    } else if (nodeClickStep === 1) {
        // Повторный тап по тому же узлу -> Шаг 2: Подсветка связей (магистраль от ядра + разграничения)
        focusNodeStep2(target);
    } else {
        // Третий тап по тому же узлу -> Шаг 3: Полный сброс выделения
        window.clearKgSearch(true);
        window.closeKgNodeDrawer();
        triggerHaptic('light');
    }
};

window.highlightSessionInGraph = function(cards) {
    if (!cards || cards.length === 0) return;
    const cleanData = getCleanGraphData();
    if (!cleanData || !cleanData.nodes || cleanData.nodes.length === 0) return;

    searchHighlightNodes.clear();
    searchHighlightLinkKeys.clear();
    searchBackboneNodes.clear();
    searchBackboneLinkKeys.clear();

    const rootNode = cleanData.nodes.find(n => n.level === 0);
    const rootId = rootNode ? String(rootNode.id) : null;
    if (rootId) {
        searchBackboneNodes.add(rootId);
        searchHighlightNodes.add(rootId);
    }

    const matchedNodes = [];
    cards.forEach(card => {
        let node = null;
        if (card.id) {
            node = cleanData.nodes.find(n => n.card_id === card.id || (n.card_ids && n.card_ids.includes(card.id)));
        }
        if (!node && card.organ_slug) {
            node = cleanData.nodes.find(n => String(n.id) === String(card.organ_slug));
        }
        if (!node && card.translation) {
            const tLower = card.translation.toLowerCase().trim();
            node = cleanData.nodes.find(n => n.name && n.name.toLowerCase().trim() === tLower);
            if (!node) {
                node = cleanData.nodes.find(n => n.name && (tLower.includes(n.name.toLowerCase()) || n.name.toLowerCase().includes(tLower)));
            }
        }
        if (!node && card.text) {
            const qLower = card.text.toLowerCase().trim();
            node = cleanData.nodes.find(n => n.name && qLower.includes(n.name.toLowerCase().trim()));
            if (!node) {
                const words = qLower.match(/[a-zа-яё0-9]{4,}/g) || [];
                for (const w of words) {
                    const stem = w.slice(0, 5);
                    node = cleanData.nodes.find(n => n.name && n.name.toLowerCase().includes(stem));
                    if (node) break;
                }
            }
        }
        if (node && !matchedNodes.some(m => String(m.id) === String(node.id))) {
            matchedNodes.push(node);
        }
    });

    matchedNodes.forEach(node => {
        const nId = String(node.id);
        searchHighlightNodes.add(nId);
        searchBackboneNodes.add(nId);
        if (node.parent_id) {
            const pId = String(node.parent_id);
            searchHighlightNodes.add(pId);
            searchBackboneNodes.add(pId);
        }
    });

    const allLinks = (cleanData.links && cleanData.links.length > 0)
        ? cleanData.links
        : ((currentKgGraphData && currentKgGraphData.edges) ? currentKgGraphData.edges : []);

    allLinks.forEach(edge => {
        const sId = String((typeof edge.source === 'object' && edge.source !== null) ? edge.source.id : edge.source);
        const tId = String((typeof edge.target === 'object' && edge.target !== null) ? edge.target.id : edge.target);
        const lKey = edge.__key || getGraphLinkKey(sId, tId);

        if (searchBackboneNodes.has(sId) && searchBackboneNodes.has(tId)) {
            searchBackboneLinkKeys.add(lKey);
            searchHighlightLinkKeys.add(lKey);
        } else if (searchHighlightNodes.has(sId) && searchHighlightNodes.has(tId)) {
            searchHighlightLinkKeys.add(lKey);
        }
    });

    if (matchedNodes.length > 0) {
        activeSearchTargetId = String(matchedNodes[matchedNodes.length - 1].id);
    }

    if (currentForceGraphInstance) {
        currentForceGraphInstance.refresh();
        setTimeout(() => {
            if (currentForceGraphInstance) {
                currentForceGraphInstance.zoomToFit(600, 50);
            }
        }, 250);
    }

    const pill = document.getElementById('kg-selection-pill');
    const pillName = document.getElementById('kg-selection-pill-name');
    if (pill && pillName) {
        pillName.textContent = `Пройдено в блоке: ${matchedNodes.length} понятий`;
        pill.classList.remove('hidden');
    }
};

window.initForceGraph = async function(graphData) {
    const wrapper = document.getElementById('kg-graph-canvas-wrapper');
    if (!wrapper) return;

    const container = wrapper.parentElement || wrapper;
    const width = wrapper.clientWidth || container.clientWidth || window.innerWidth;
    const height = wrapper.clientHeight || container.clientHeight || (window.innerHeight - 150);

    const isDark = document.documentElement.classList.contains('dark');
    const bgColor = isDark ? '#0e0e0e' : '#fbfbfb';

    // 1. Если граф уже инициализирован, только обновляем размеры и возобновляем анимацию
    if (currentForceGraphInstance) {
        currentForceGraphInstance
            .width(width)
            .height(height)
            .backgroundColor(bgColor);
        if (currentForceGraphInstance.resumeAnimation) {
            currentForceGraphInstance.resumeAnimation();
        }
        setGraphLayout(currentKgLayout || 'force');
        return;
    }

    // 2. Защита от параллельного двойного запуска конструктора
    if (isInitializingGraph) return;
    isInitializingGraph = true;

    try {
        if (!window.ForceGraph || !window.d3) {
            wrapper.innerHTML = `
                <div class="flex flex-col items-center justify-center p-6 text-center h-full min-h-[300px] text-neutral-600 dark:text-neutral-400">
                    <span class="material-symbols-outlined text-4xl text-primary animate-spin mb-2">sync</span>
                    <p class="font-mono text-sm font-bold text-neutral-800 dark:text-neutral-200 mb-1">Загрузка 2D-движка графа...</p>
                    <p class="text-xs text-neutral-500">Инициализация физической модели D3 и холста...</p>
                </div>
            `;
            try {
                await ensureGraphVendorLoaded();
            } catch (err) {
                wrapper.innerHTML = `
                    <div class="flex flex-col items-center justify-center p-6 text-center h-full min-h-[300px] text-neutral-600 dark:text-neutral-400">
                        <span class="material-symbols-outlined text-4xl text-amber-500 mb-2">account_tree</span>
                        <p class="font-mono text-sm font-bold text-neutral-800 dark:text-neutral-200 mb-1">2D-движок графа недоступен</p>
                        <p class="text-xs mb-4 max-w-sm">Скрипт 2D-визуализации не смог загрузиться из-за сетевых ограничений. Рекомендуем переключиться на режим ментальной карты.</p>
                        <button onclick="window.switchKgView('tree')" class="px-4 py-2 bg-primary text-on-primary rounded-xl font-mono text-xs font-bold uppercase transition-all shadow-xs cursor-pointer flex items-center gap-1.5">
                            <span class="material-symbols-outlined text-[16px]">account_tree</span>
                            <span>[ Открыть Mindmap (Дерево) ]</span>
                        </button>
                    </div>
                `;
                return;
            }
        }

        if (!window.ForceGraph) return;
        if (currentForceGraphInstance) return;

        const cleanData = getCleanGraphData();
        if (!cleanData || !cleanData.nodes || cleanData.nodes.length === 0) return;

        wrapper.innerHTML = '';
        isInitialLayoutFit = true;
        wrapper.addEventListener('pointerdown', (e) => {
            isInitialLayoutFit = false;
            lastPointerDownPos = { x: e.clientX, y: e.clientY, time: Date.now() };
        }, { passive: true });
        wrapper.addEventListener('touchstart', (e) => {
            isInitialLayoutFit = false;
            if (e.touches && e.touches[0]) {
                lastPointerDownPos = { x: e.touches[0].clientX, y: e.touches[0].clientY, time: Date.now() };
            }
        }, { passive: true });
        wrapper.addEventListener('wheel', () => { isInitialLayoutFit = false; }, { passive: true });

    currentForceGraphInstance = ForceGraph()(wrapper)
        .width(width)
        .height(height)
        .backgroundColor(bgColor)
        .nodeId('id')
        .nodeVal('val')
        .nodeLabel(node => `${node.name} (${KG_CATEGORY_NAMES[node.category] || node.category})`)
        .linkDirectionalArrowLength(link => {
            const lKey = link.__key || getGraphLinkKey(link.source, link.target);
            if (nodeClickStep === 2) {
                if (searchBackboneLinkKeys.has(lKey) || searchHighlightLinkKeys.has(lKey)) return 7.5;
                return 4.0;
            }
            if (nodeClickStep === 1) {
                const sId = (typeof link.source === 'object' && link.source !== null) ? String(link.source.id) : String(link.source);
                const tId = (typeof link.target === 'object' && link.target !== null) ? String(link.target.id) : String(link.target);
                if (sId === activeSearchTargetId || tId === activeSearchTargetId) return 7.0;
                return 5.0;
            }
            return 5.5;
        })
        .linkDirectionalArrowRelPos(0.88)
        .linkCurvature(link => link.__curvature || 0)
        .linkColor(link => {
            const lKey = link.__key || getGraphLinkKey(link.source, link.target);
            const isHovered = hoveredLink === link || hoveredLinkKeys.has(lKey);
            if (isHovered) {
                return getKgRelationLinkColor(link.relation, true, isDark);
            }
            if (nodeClickStep === 2) {
                if (searchBackboneLinkKeys.has(lKey)) {
                    // Магистраль от истока к понятию (Sky Blue)
                    return isDark ? '#38bdf8' : '#0284c7';
                }
                if (searchHighlightLinkKeys.has(lKey)) {
                    // Исходящая или входящая связь/разграничение: яркий сочный цвет из приложения!
                    return getKgRelationLinkColor(link.relation, true, isDark);
                }
                return isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.05)';
            }
            if (nodeClickStep === 1) {
                // Шаг 1: Фокус на выбранном понятии. Прямые связи подсвечиваются ярче!
                const sId = (typeof link.source === 'object' && link.source !== null) ? String(link.source.id) : String(link.source);
                const tId = (typeof link.target === 'object' && link.target !== null) ? String(link.target.id) : String(link.target);
                if (sId === activeSearchTargetId || tId === activeSearchTargetId) {
                    return getKgRelationLinkColor(link.relation, true, isDark);
                }
                // Фоновые связи сохраняют читаемый цвет с комфортным смягчением
                return getKgRelationLinkColor(link.relation, false, isDark, 0.45);
            }
            if (searchHighlightNodes.size > 0 || activeSelectedLink) {
                if (searchBackboneLinkKeys.has(lKey)) {
                    return isDark ? '#38bdf8' : '#0284c7';
                }
                if (searchHighlightLinkKeys.has(lKey)) {
                    return getKgRelationLinkColor(link.relation, true, isDark);
                }
                return isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.05)';
            }
            if (hoveredNode || hoveredLink) {
                return isDark ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.08)';
            }
            // Обычное состояние: яркие, четкие линии с цветами основного приложения
            return getKgRelationLinkColor(link.relation, false, isDark);
        })
        .linkWidth(link => {
            const lKey = link.__key || getGraphLinkKey(link.source, link.target);
            const isHovered = hoveredLink === link || hoveredLinkKeys.has(lKey);
            if (isHovered) return 3.0;
            if (nodeClickStep === 2) {
                if (searchBackboneLinkKeys.has(lKey)) return 3.2; // Магистраль от истока
                if (searchHighlightLinkKeys.has(lKey)) return 2.8; // Связи и разграничения
                return 0.5;
            }
            if (nodeClickStep === 1) {
                const sId = (typeof link.source === 'object' && link.source !== null) ? String(link.source.id) : String(link.source);
                const tId = (typeof link.target === 'object' && link.target !== null) ? String(link.target.id) : String(link.target);
                if (sId === activeSearchTargetId || tId === activeSearchTargetId) {
                    return 2.4; // Прямые связи выбранного понятия выделены заметно
                }
                return 0.8;
            }
            if (searchHighlightNodes.size > 0 || activeSelectedLink) {
                if (searchBackboneLinkKeys.has(lKey)) return 3.0;
                if (searchHighlightLinkKeys.has(lKey)) return 2.8;
                return 0.5;
            }
            // Обычное состояние: 1.2px для структурных связей, 1.6px для разграничений и исключений
            const rel = (link.relation || '').toLowerCase();
            if (rel === 'demarcated_from' || rel === 'excludes_application' || rel === 'appealed_to') {
                return 1.6;
            }
            return 1.2;
        })
        .linkDirectionalParticles(() => 0) // Без вырвиглазных бегущих частиц!
        .onRenderFramePre((ctx, globalScale) => {
            try {
                if (currentForceGraphInstance && typeof currentForceGraphInstance.screen2GraphCoords === 'function') {
                    const pad = 120;
                    const w = currentForceGraphInstance.width ? currentForceGraphInstance.width() : ctx.canvas.width;
                    const h = currentForceGraphInstance.height ? currentForceGraphInstance.height() : ctx.canvas.height;
                    const tl = currentForceGraphInstance.screen2GraphCoords(-pad, -pad);
                    const br = currentForceGraphInstance.screen2GraphCoords(w + pad, h + pad);
                    if (tl && br && Number.isFinite(tl.x) && Number.isFinite(br.x)) {
                        kgViewportBounds.minX = Math.min(tl.x, br.x);
                        kgViewportBounds.maxX = Math.max(tl.x, br.x);
                        kgViewportBounds.minY = Math.min(tl.y, br.y);
                        kgViewportBounds.maxY = Math.max(tl.y, br.y);
                        return;
                    }
                }
                kgViewportBounds.minX = -1e6;
                kgViewportBounds.maxX = 1e6;
                kgViewportBounds.minY = -1e6;
                kgViewportBounds.maxY = 1e6;
            } catch (_) {
                kgViewportBounds.minX = -1e6;
                kgViewportBounds.maxX = 1e6;
                kgViewportBounds.minY = -1e6;
                kgViewportBounds.maxY = 1e6;
            }
        })
        .linkCanvasObjectMode(() => 'after')
        .linkCanvasObject((link, ctx, globalScale) => {
            try {
                if (!link.source || !link.target) return;
                const sx = Number.isFinite(link.source.x) ? link.source.x : null;
                const sy = Number.isFinite(link.source.y) ? link.source.y : null;
                const tx = Number.isFinite(link.target.x) ? link.target.x : null;
                const ty = Number.isFinite(link.target.y) ? link.target.y : null;
                if (sx === null || sy === null || tx === null || ty === null) return;

                const lKey = link.__key || getGraphLinkKey(link.source, link.target);
                const isHovered = hoveredLink === link || hoveredLinkKeys.has(lKey);
                const isBackbone = searchBackboneLinkKeys.has(lKey);
                const isHighlightEdge = searchHighlightLinkKeys.has(lKey);

                // Подписи отображаются ТОЛЬКО на шаге 2 (подсветка связей и магистрали) или при наведении на ПК
                const shouldShowEdgeLabel = (nodeClickStep === 2 && (isBackbone || isHighlightEdge)) || isHovered;
                if (!shouldShowEdgeLabel) return;

                const relStyle = getKgRelationStyle(link.relation);
                const label = link.label || relStyle.label;
                if (!label) return;

                let midX = (sx + tx) / 2;
                let midY = (sy + ty) / 2;

                const curvature = link.__curvature || 0;
                if (curvature !== 0) {
                    const dx = tx - sx;
                    const dy = ty - sy;
                    midX += -dy * curvature * 0.5;
                    midY += dx * curvature * 0.5;
                }
                if (!Number.isFinite(midX) || !Number.isFinite(midY)) return;

                // Viewport Culling для подписи связи без вызова getTransform()
                if (midX < kgViewportBounds.minX || midX > kgViewportBounds.maxX ||
                    midY < kgViewportBounds.minY || midY > kgViewportBounds.maxY) {
                    return;
                }

                ctx.save();
                if (!isHovered && globalScale < 1.40) {
                    ctx.globalAlpha = Math.min(1.0, Math.max(0.1, (globalScale - 1.25) / 0.15));
                }
                if (searchHighlightNodes.size > 0 && !searchHighlightLinkKeys.has(lKey)) {
                    ctx.globalAlpha = 0.05;
                } else if ((hoveredNode || hoveredLink) && !isHovered) {
                    ctx.globalAlpha = 0.15;
                }

                const fontSize = 9;
                ctx.font = `500 ${fontSize}px Inter, "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, sans-serif`;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';

                // Кэширование измерений текста на ребре для ликвидации просадок FPS (0 повторных measureText в кадре)
                if (link.__cachedLabel !== label || !link.__pillW) {
                    link.__cachedLabel = label;
                    const textWidth = ctx.measureText(label).width;
                    link.__textWidth = textWidth;
                    link.__pillW = textWidth + 9.0;
                    link.__pillH = fontSize + 5.0;
                }
                const pillW = link.__pillW;
                const pillH = link.__pillH;

                // Background chip
                ctx.beginPath();
                const pillR = 3.5;
                if (ctx.roundRect) {
                    ctx.roundRect(midX - pillW / 2, midY - pillH / 2, pillW, pillH, pillR);
                } else {
                    ctx.rect(midX - pillW / 2, midY - pillH / 2, pillW, pillH);
                }
                ctx.fillStyle = isDark ? 'rgba(15, 23, 42, 0.88)' : 'rgba(255, 255, 255, 0.94)';
                ctx.fill();

                // Chip border
                const isEdgeLit = (nodeClickStep === 2 && (isBackbone || isHighlightEdge)) || isHovered;
                ctx.strokeStyle = isEdgeLit
                    ? (isBackbone ? (isDark ? '#38bdf8' : '#0284c7') : (isDark ? (relStyle.color || '#38bdf8') : (relStyle.lightColor || '#0284c7')))
                    : (isDark ? 'rgba(255, 255, 255, 0.22)' : 'rgba(0, 0, 0, 0.15)');
                ctx.lineWidth = isEdgeLit ? 1.6 : 0.8;
                ctx.stroke();

                // Text
                ctx.fillStyle = isDark ? '#f1f5f9' : '#0f172a';
                ctx.fillText(label, midX, midY);

                ctx.restore();
            } catch (_) {
                // Предотвращаем падение всего цикла анимации при специфичных сбоях холста
            }
        })
        .onNodeHover(node => {
            const isTouch = ('ontouchstart' in window) || (navigator.maxTouchPoints > 0) || (window.Telegram && window.Telegram.WebApp);
            if (isTouch) return; // На смартфонах узлы выбираются по клику (onNodeClick), чтобы жесты зума не вызывали ложных срабатываний
            if ((!node && !hoveredNode) || (node && hoveredNode && node.id === hoveredNode.id)) return;
            hoveredNode = node || null;
            hoveredLinkKeys.clear();
            hoveredNeighborNodeIds.clear();

            if (node) {
                wrapper.style.cursor = 'pointer';
                const nId = String(node.id);
                hoveredNeighborNodeIds.add(nId);
                const gData = currentForceGraphInstance.graphData ? currentForceGraphInstance.graphData() : getCleanGraphData();
                if (gData && gData.links) {
                    gData.links.forEach(l => {
                        const sId = String((typeof l.source === 'object' && l.source !== null) ? l.source.id : l.source);
                        const tId = String((typeof l.target === 'object' && l.target !== null) ? l.target.id : l.target);
                        if (sId === nId || tId === nId) {
                            hoveredLinkKeys.add(l.__key || getGraphLinkKey(sId, tId));
                            hoveredNeighborNodeIds.add(sId);
                            hoveredNeighborNodeIds.add(tId);
                        }
                    });
                }
            } else {
                wrapper.style.cursor = null;
            }
            if (currentForceGraphInstance) {
                currentForceGraphInstance.refresh();
            }
        })
        .warmupTicks(0)
        .cooldownTicks(45)
        .d3VelocityDecay(0.45) // Быстрый и плавный разогрев на мобильных устройствах без блокировки UI-потока
        .onDagError(() => false)
        .onEngineStop(() => {
            if (isInitialLayoutFit && currentForceGraphInstance && currentKgView === 'graph') {
                isInitialLayoutFit = false;
                currentForceGraphInstance.zoomToFit(400, 35);
            }
        })
        .onNodeDrag(() => {
            isInitialLayoutFit = false;
        })
        .onNodeDragEnd(() => {
            isInitialLayoutFit = false;
        })
        .nodeCanvasObject((node, ctx, globalScale) => {
            try {
                if (!node || !Number.isFinite(node.x) || !Number.isFinite(node.y)) return;

                // 0. Viewport Culling: мгновенно отсекаем узлы за пределами видимого экрана (0 DOMMatrix allocations)
                if (node.x < kgViewportBounds.minX || node.x > kgViewportBounds.maxX ||
                    node.y < kgViewportBounds.minY || node.y > kgViewportBounds.maxY) {
                    return;
                }

                const hasActiveSelection = searchHighlightNodes.size > 0;
                const isHighlighted = !hasActiveSelection || searchHighlightNodes.has(String(node.id));
                const isTarget = activeSearchTargetId === String(node.id);
                const isBackboneNode = searchBackboneNodes.has(String(node.id));
                const label = node.name || node.id;
                const baseR = Math.max(3.0, (node.val || 4) * 0.75);
                const radius = baseR * (isTarget ? 1.4 : (isBackboneNode ? 1.15 : 1.0));
                const currentDark = isDark;

                ctx.save();
            const isHoverActive = Boolean(hoveredNode || hoveredLink);
            const isHovered = (hoveredNode && String(hoveredNode.id) === String(node.id));
            const isHoverNeighbor = hoveredNeighborNodeIds.has(String(node.id));

            if (nodeClickStep === 2) {
                if (!isHighlighted) {
                    ctx.globalAlpha = 0.08;
                } else if (isHoverActive && !isHoverNeighbor) {
                    ctx.globalAlpha = 0.25;
                }
            } else if (nodeClickStep === 1) {
                if (!isTarget) {
                    ctx.globalAlpha = 0.65;
                }
            } else if (hasActiveSelection) {
                if (!isHighlighted) {
                    ctx.globalAlpha = 0.08;
                } else if (isHoverActive && !isHoverNeighbor) {
                    ctx.globalAlpha = 0.25;
                }
            } else if (isHoverActive && !isHoverNeighbor) {
                ctx.globalAlpha = 0.20;
            }

            // 1. Цвет узлов в зависимости от роли, фокуса и FSRS-статуса изучения
            let nodeFill;
            let nodeStroke;
            let strokeWidth = 1.0;
            const cardState = node.card_state !== undefined ? node.card_state : (node.is_learned ? 2 : 0);

            if (isTarget) {
                // Целевой узел: чистый высокий контраст с акцентным фокусом
                nodeFill = currentDark ? '#ffffff' : '#0f172a';
                nodeStroke = currentDark ? '#38bdf8' : '#0284c7';
                strokeWidth = 2.4;

                // Акцентный ореол активного фокуса
                ctx.beginPath();
                ctx.arc(node.x, node.y, radius + 4.5, 0, 2 * Math.PI, false);
                ctx.strokeStyle = currentDark ? '#38bdf8' : '#0284c7';
                ctx.lineWidth = 2.0;
                ctx.stroke();

            } else if (isBackboneNode) {
                // Узлы магистрали (Корень и Родительский институт)
                nodeFill = currentDark ? '#e2e8f0' : '#334155';
                nodeStroke = currentDark ? 'rgba(56, 189, 248, 0.6)' : 'rgba(2, 132, 199, 0.6)';
                strokeWidth = 1.6;

            } else if (hasActiveSelection && isHighlighted) {
                // Соседние понятия ветви
                const catColor = getKgNodeColor(node.category);
                if (cardState === 2) {
                    nodeFill = currentDark ? '#059669' : '#10b981';
                    nodeStroke = currentDark ? '#34d399' : '#047857';
                } else if (cardState === 1 || cardState === 3) {
                    nodeFill = currentDark ? '#1d4ed8' : '#3b82f6';
                    nodeStroke = currentDark ? '#60a5fa' : '#1d4ed8';
                } else {
                    nodeFill = currentDark ? '#181e29' : '#f8fafc';
                    nodeStroke = catColor;
                }
                strokeWidth = 1.5;

            } else {
                // ОБЫЧНОЕ СОСТОЯНИЕ
                if (node.level === 0) {
                    nodeFill = currentDark ? '#ffffff' : '#0f172a';
                    nodeStroke = currentDark ? '#38bdf8' : '#0284c7';
                    strokeWidth = 2.4;
                } else if (node.level === 1) {
                    if (cardState === 2) {
                        nodeFill = currentDark ? '#065f46' : '#059669';
                        nodeStroke = '#10b981';
                        strokeWidth = 2.0;
                    } else if (cardState === 1 || cardState === 3) {
                        nodeFill = currentDark ? '#1e3a8a' : '#2563eb';
                        nodeStroke = '#3b82f6';
                        strokeWidth = 2.0;
                    } else {
                        // Неизученный институт: золотисто-янтарный цвет категории authority (как в приложении)
                        nodeFill = currentDark ? '#292524' : '#fef3c7';
                        nodeStroke = currentDark ? '#fbbf24' : '#d97706';
                        strokeWidth = 1.8;
                    }
                } else {
                    const catColor = getKgNodeColor(node.category);
                    if (cardState === 2) {
                        // Изучено (Mastered): выразительный изумрудный цвет
                        nodeFill = currentDark ? '#059669' : '#10b981';
                        nodeStroke = currentDark ? '#34d399' : '#047857';
                        strokeWidth = 1.4;
                    } else if (cardState === 1 || cardState === 3) {
                        // В процессе изучения: выразительный синий цвет
                        nodeFill = currentDark ? '#1d4ed8' : '#3b82f6';
                        nodeStroke = currentDark ? '#60a5fa' : '#1d4ed8';
                        strokeWidth = 1.4;
                    } else {
                        // Новое понятие: темная сердцевина и окантовка цвета категории из приложения!
                        nodeFill = currentDark ? '#181e29' : '#f8fafc';
                        nodeStroke = catColor;
                        strokeWidth = 1.4;
                    }
                }
            }

            // 2. Тело узла
            ctx.beginPath();
            ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI, false);
            ctx.fillStyle = nodeFill;
            ctx.fill();

            // 3. Окантовка узла
            ctx.lineWidth = strokeWidth;
            ctx.strokeStyle = nodeStroke;
            ctx.stroke();

            // 3.1. Индикация изученности карточки (Learning Status Ring & Glow)
            if (cardState === 2) {
                // Изучено / Mastered: Изумрудное кольцо статуса
                ctx.beginPath();
                ctx.arc(node.x, node.y, radius + (node.level <= 1 ? 3.2 : 2.2), 0, 2 * Math.PI, false);
                ctx.strokeStyle = '#10b981';
                ctx.lineWidth = node.level <= 1 ? 2.2 : 1.8;
                ctx.stroke();
            } else if (cardState === 1 || cardState === 3) {
                // В процессе изучения: Синее кольцо статуса
                ctx.beginPath();
                ctx.arc(node.x, node.y, radius + (node.level <= 1 ? 3.2 : 2.2), 0, 2 * Math.PI, false);
                ctx.strokeStyle = '#3b82f6';
                ctx.lineWidth = node.level <= 1 ? 2.2 : 1.8;
                ctx.stroke();
            }

            // 3.2. Акцентный ореол наведенного узла
            if (!isTarget && isHovered) {
                ctx.beginPath();
                ctx.arc(node.x, node.y, radius + 3.8, 0, 2 * Math.PI, false);
                ctx.strokeStyle = currentDark ? '#38bdf8' : '#0284c7';
                ctx.lineWidth = 1.8;
                ctx.stroke();
            }

            // 3.3. Акцентный ореол ядра курса (корень графа)
            if (node.level === 0 && !isTarget) {
                ctx.beginPath();
                ctx.arc(node.x, node.y, radius + 4.2, 0, 2 * Math.PI, false);
                ctx.strokeStyle = currentDark ? 'rgba(56, 189, 248, 0.40)' : 'rgba(2, 132, 199, 0.35)';
                ctx.lineWidth = 1.6;
                ctx.stroke();
            }

            // 4. Текстовая плашка-метка узла (Obsidian-Style Semantic Zoom LOD)
            let shouldShowLabel = false;
            let labelAlpha = 1.0;
            if (isTarget) {
                // Выделенный целевой узел виден всегда
                shouldShowLabel = true;
                labelAlpha = 1.0;
            } else if (nodeClickStep === 2 && (isBackboneNode || (hasActiveSelection && isHighlighted))) {
                // На шаге 2 магистраль и связанные понятия видны всегда
                shouldShowLabel = true;
                labelAlpha = 1.0;
            } else if (isHovered || isHoverNeighbor) {
                shouldShowLabel = true;
                labelAlpha = 1.0;
            } else if (hasActiveSelection && isHighlighted && nodeClickStep !== 1) {
                // Соседние понятия активной ветки показываются при комфортном масштабе
                shouldShowLabel = globalScale >= 0.70;
                labelAlpha = Math.min(1.0, Math.max(0.0, (globalScale - 0.65) / 0.15));
            } else if (node.level === 0) {
                // Заголовок курса (корень) виден всегда
                shouldShowLabel = true;
                labelAlpha = 1.0;
            } else if (node.level === 1) {
                // Институты появляются при приближении (когда в кадре несколько институтов)
                shouldShowLabel = globalScale >= 0.85;
                labelAlpha = Math.min(1.0, Math.max(0.0, (globalScale - 0.75) / 0.15));
            } else {
                // Понятия появляются при глубоком приближении с плавным переходом 1.80 - 2.10
                shouldShowLabel = globalScale >= 1.80;
                labelAlpha = Math.min(1.0, Math.max(0.0, (globalScale - 1.80) / 0.30));
            }

            if (isHighlighted && shouldShowLabel && labelAlpha > 0.02) {
                const prevLabelAlpha = ctx.globalAlpha;
                ctx.globalAlpha = prevLabelAlpha * labelAlpha;

                // Фиксированный размер шрифта в мировых координатах (без раздувания при отдалении!)
                const baseFontSize = isTarget ? 11 : (node.level === 0 ? 12 : (isBackboneNode || node.level === 1 ? 9.5 : 8));
                ctx.font = `${isTarget || isBackboneNode || node.level <= 1 ? '700' : '500'} ${baseFontSize}px "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, sans-serif`;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';

                // Кэширование разбиения строк и измерений ширины для предотвращения просадки FPS
                if (!node.__lines || node.__lastLabel !== label) {
                    node.__lines = wrapNodeText(label, node.level === 0 ? 22 : 16);
                    node.__lastLabel = label;
                    node.__lineMetrics = null;
                }

                const lineHeight = baseFontSize + 3.0;
                const startY = node.y + radius + 4.5 + (lineHeight / 2);

                if (!node.__lineMetrics || node.__lineMetricsSize !== baseFontSize) {
                    const padX = 4.0;
                    const padY = 2.5;
                    node.__lineMetricsSize = baseFontSize;
                    node.__lineMetrics = node.__lines.map(line => {
                        const textW = ctx.measureText(line).width;
                        return {
                            pillW: textW + (padX * 2),
                            pillH: baseFontSize + (padY * 2)
                        };
                    });
                }

                node.__lines.forEach((line, i) => {
                    const lineY = startY + (i * lineHeight);
                    const metrics = node.__lineMetrics[i] || { pillW: 40, pillH: baseFontSize + 5.0 };
                    const pillW = metrics.pillW;
                    const pillH = metrics.pillH;

                    // Фон плашки
                    if (isTarget) {
                        ctx.fillStyle = currentDark ? '#ffffff' : '#0f172a';
                    } else if (isBackboneNode) {
                        ctx.fillStyle = currentDark ? 'rgba(255, 255, 255, 0.20)' : 'rgba(15, 23, 42, 0.16)';
                    } else {
                        ctx.fillStyle = currentDark ? 'rgba(18, 18, 18, 0.88)' : 'rgba(255, 255, 255, 0.94)';
                    }

                    ctx.beginPath();
                    const pillRadius = 3.0;
                    if (ctx.roundRect) {
                        ctx.roundRect(node.x - pillW / 2, lineY - pillH / 2, pillW, pillH, pillRadius);
                    } else {
                        ctx.rect(node.x - pillW / 2, lineY - pillH / 2, pillW, pillH);
                    }
                    ctx.fill();

                    // Рамка плашки
                    if (isTarget) {
                        ctx.strokeStyle = currentDark ? '#e2e8f0' : '#1e293b';
                        ctx.lineWidth = 1.0;
                    } else if (isBackboneNode) {
                        ctx.strokeStyle = currentDark ? 'rgba(255, 255, 255, 0.35)' : 'rgba(15, 23, 42, 0.25)';
                        ctx.lineWidth = 0.8;
                    } else {
                        ctx.strokeStyle = currentDark ? 'rgba(255, 255, 255, 0.12)' : 'rgba(0, 0, 0, 0.08)';
                        ctx.lineWidth = 0.6;
                    }
                    ctx.stroke();

                    // Текст плашки
                    if (isTarget) {
                        ctx.fillStyle = currentDark ? '#000000' : '#ffffff';
                    } else if (isBackboneNode) {
                        ctx.fillStyle = currentDark ? '#ffffff' : '#0f172a';
                    } else {
                        ctx.fillStyle = currentDark ? '#f8fafc' : '#0f172a';
                    }
                    ctx.fillText(line, node.x, lineY);
                });

                ctx.globalAlpha = prevLabelAlpha;
            }

            ctx.restore();
            } catch (_) {
                // Предотвращаем срыв отрисовки холста
            }
        })
        .nodePointerAreaPaint((node, color, ctx) => {
            try {
                if (!node || !Number.isFinite(node.x) || !Number.isFinite(node.y)) return;
                if (node.x < kgViewportBounds.minX || node.x > kgViewportBounds.maxX ||
                    node.y < kgViewportBounds.minY || node.y > kgViewportBounds.maxY) {
                    return;
                }

                const baseR = Math.max(3.0, (node.val || 4) * 0.75);
                const radius = Math.max(26, baseR + 10);
                ctx.fillStyle = color;
                ctx.beginPath();
                ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI, false);
                ctx.fill();
            } catch (_) {
                // Предотвращаем падение при сбоях сенсорного хит-теста
            }
        })
        .onNodeClick(node => {
            if (!node || !node.id) return;
            const now = Date.now();
            if (now - (window.__lastKgNodeClickTime || 0) < 250) return;
            window.__lastKgNodeClickTime = now;
            focusNodeInGraph(node.id);
        })
        .onBackgroundClick(event => {
            const resBox = document.getElementById('kg-search-results');
            if (resBox) resBox.classList.add('hidden');

            hoveredNode = null;
            hoveredLink = null;
            hoveredLinkKeys.clear();
            hoveredNeighborNodeIds.clear();

            // Защита от случайного сброса при перетаскивании (pan/drag) холста
            if (lastPointerDownPos && event) {
                const dx = Math.abs(event.clientX - lastPointerDownPos.x);
                const dy = Math.abs(event.clientY - lastPointerDownPos.y);
                const dt = Date.now() - (lastPointerDownPos.time || 0);
                if (dx > 7 || dy > 7 || dt > 650) {
                    return; // Пользователь панорамировал или зумил карту, не сбрасываем!
                }
            }

            // Закрываем шторку узла при клике на свободный фон
            const drawer = document.getElementById('kg-node-drawer');
            const isDrawerOpen = drawer && !drawer.classList.contains('hidden');
            if (isDrawerOpen) {
                closeKgNodeDrawer();
            }

            // Сброс выделения с сохранением зума и координат камеры (preserveCamera: true)
            if (activeSearchTargetId || activeSelectedLink || searchHighlightNodes.size > 0 || nodeClickStep > 0) {
                window.clearKgSearch(true);
            } else if (currentForceGraphInstance) {
                currentForceGraphInstance.refresh();
            }
        });

    setGraphLayout(currentKgLayout || 'force');

    // Resize on window resize (remove previous listener to prevent memory leak and duplicate events)
    if (kgResizeHandler) {
        window.removeEventListener('resize', kgResizeHandler);
    }
    kgResizeHandler = () => {
        if (currentForceGraphInstance && currentKgView === 'graph') {
            const w = wrapper.clientWidth || (wrapper.parentElement ? wrapper.parentElement.clientWidth : 0) || window.innerWidth;
            const h = wrapper.clientHeight || (wrapper.parentElement ? wrapper.parentElement.clientHeight : 0) || (window.innerHeight - 150);
            currentForceGraphInstance.width(w).height(h);
        }
    };
    window.addEventListener('resize', kgResizeHandler);
    } finally {
        isInitializingGraph = false;
    }
};

window.zoomGraph = function(factor) {
    if (!currentForceGraphInstance) return;
    const currentZoom = currentForceGraphInstance.zoom();
    currentForceGraphInstance.zoom(currentZoom * factor, 300);
};

window.resetGraphZoom = function() {
    if (!currentForceGraphInstance) return;
    currentForceGraphInstance.zoomToFit(400, 40);
    triggerHaptic('light');
};

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const modal = document.getElementById('knowledge-graph-modal');
        if (modal && !modal.classList.contains('hidden') && currentKgView === 'graph') {
            if (activeSearchTargetId || activeSelectedLink || searchHighlightNodes.size > 0) {
                window.clearKgSearch(true);
            }
        }
    }
});

window.collapseKgNodeDrawer = function() {
    isDrawerCollapsed = true;
    const body = document.getElementById('kg-drawer-body');
    const toggleIcon = document.getElementById('kg-drawer-toggle-icon');
    if (body) {
        body.classList.add('hidden');
    }
    if (toggleIcon) {
        toggleIcon.textContent = 'expand_less';
    }
};

window.expandKgNodeDrawer = function() {
    isDrawerCollapsed = false;
    const body = document.getElementById('kg-drawer-body');
    const toggleIcon = document.getElementById('kg-drawer-toggle-icon');
    if (body) {
        body.classList.remove('hidden');
    }
    if (toggleIcon) {
        toggleIcon.textContent = 'expand_more';
    }
};

window.toggleKgNodeDrawerCollapse = function() {
    if (isDrawerCollapsed) {
        window.expandKgNodeDrawer();
    } else {
        window.collapseKgNodeDrawer();
    }
    triggerHaptic('light');
};

let kgDrawerGesturesInitialized = false;

function initKgDrawerGestures() {
    if (kgDrawerGesturesInitialized) return;
    kgDrawerGesturesInitialized = true;

    const drawer = document.getElementById('kg-node-drawer');
    const handle = document.getElementById('kg-drawer-handle');
    const header = document.getElementById('kg-drawer-header');
    if (!drawer) return;

    if (header) {
        header.addEventListener('click', (e) => {
            if (e.target.closest('button')) return;
            window.toggleKgNodeDrawerCollapse();
        });
    }

    let startY = 0;
    let startX = 0;
    let startTime = 0;

    const onTouchStart = (e) => {
        if (!e.touches || e.touches.length === 0) return;
        startY = e.touches[0].clientY;
        startX = e.touches[0].clientX;
        startTime = Date.now();
    };

    const onTouchEnd = (e) => {
        if (!e.changedTouches || e.changedTouches.length === 0) return;
        const endY = e.changedTouches[0].clientY;
        const endX = e.changedTouches[0].clientX;
        const deltaY = endY - startY;
        const deltaX = Math.abs(endX - startX);
        const dt = Date.now() - startTime;

        if (deltaX > Math.abs(deltaY) || dt > 800) return;

        if (deltaY > 35) {
            // Свайп вниз
            if (!isDrawerCollapsed) {
                window.collapseKgNodeDrawer();
                triggerHaptic('light');
            } else {
                window.clearKgSearch(true);
                window.closeKgNodeDrawer();
                triggerHaptic('medium');
            }
        } else if (deltaY < -35) {
            // Свайп вверх
            if (isDrawerCollapsed) {
                window.expandKgNodeDrawer();
                triggerHaptic('light');
            }
        }
    };

    if (handle) {
        handle.addEventListener('touchstart', onTouchStart, { passive: true });
        handle.addEventListener('touchend', onTouchEnd, { passive: true });
    }
    if (header) {
        header.addEventListener('touchstart', onTouchStart, { passive: true });
        header.addEventListener('touchend', onTouchEnd, { passive: true });
    }
}

window.showKgNodeDrawer = function(node, startCollapsed = false) {
    const drawer = document.getElementById('kg-node-drawer');
    const badge = document.getElementById('kg-drawer-badge');
    const statusBadge = document.getElementById('kg-drawer-status-badge');
    const title = document.getElementById('kg-drawer-title');
    const summary = document.getElementById('kg-drawer-summary');
    const linksContainer = document.getElementById('kg-drawer-links');
    const linksSection = document.getElementById('kg-drawer-links-section');
    const linksCountEl = document.getElementById('kg-drawer-links-count');
    const linksFilterEl = document.getElementById('kg-drawer-links-filter');
    const cardBtn = document.getElementById('kg-drawer-btn-card');

    if (!drawer) return;
    currentKgDrawerNode = node;

    const cat = node.category || 'authority';
    if (badge) {
        badge.className = `px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase shrink-0 badge-${cat}`;
        badge.textContent = KG_CATEGORY_NAMES[cat] || cat.toUpperCase();
    }

    if (statusBadge) {
        const cardState = node.card_state !== undefined ? node.card_state : (node.is_learned ? 2 : 0);
        if (cardState === 2) {
            statusBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase shrink-0 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20';
            statusBadge.textContent = 'Изучено';
            statusBadge.classList.remove('hidden');
        } else if (cardState === 1 || cardState === 3) {
            statusBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase shrink-0 bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20';
            statusBadge.textContent = 'В процессе';
            statusBadge.classList.remove('hidden');
        } else if (node.total_leaves) {
            statusBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase shrink-0 bg-sky-500/10 text-sky-600 dark:text-sky-400 border border-sky-500/20';
            statusBadge.textContent = `Прогресс ${node.learned_count || 0}/${node.total_leaves}`;
            statusBadge.classList.remove('hidden');
        } else if (node.level > 0) {
            statusBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase shrink-0 bg-neutral-500/10 text-neutral-500 border border-neutral-500/20';
            statusBadge.textContent = 'Новое';
            statusBadge.classList.remove('hidden');
        } else {
            statusBadge.classList.add('hidden');
        }
    }

    if (cardBtn) {
        if (node.card_id) {
            cardBtn.classList.remove('hidden');
        } else {
            cardBtn.classList.add('hidden');
        }
    }

    if (title) title.textContent = node.name || node.id;
    if (summary) summary.textContent = node.summary || 'Детальное описание и законодательное основание отсутствуют.';

    // Рендер связей и разграничений
    if (linksContainer && currentKgGraphData) {
        linksContainer.innerHTML = '';
        const allEdges = currentKgGraphData ? (currentKgGraphData.edges || currentKgGraphData.links || []) : [];
        const connectedEdges = allEdges.filter(e => {
            const s = (typeof e.source === 'object' && e.source !== null) ? String(e.source.id) : String(e.source);
            const t = (typeof e.target === 'object' && e.target !== null) ? String(e.target.id) : String(e.target);
            return s === String(node.id) || t === String(node.id);
        });

        if (linksCountEl) linksCountEl.textContent = connectedEdges.length;

        if (connectedEdges.length > 0) {
            if (linksSection) linksSection.classList.remove('hidden');

            // Подсчет связей по группам
            const groups = {
                'all': connectedEdges.length,
                'subject_to_jurisdiction': connectedEdges.filter(e => (e.relation || 'subject_to_jurisdiction') === 'subject_to_jurisdiction').length,
                'demarcated_from': connectedEdges.filter(e => e.relation === 'demarcated_from').length,
                'other': connectedEdges.filter(e => e.relation !== 'subject_to_jurisdiction' && e.relation !== 'demarcated_from').length
            };

            // Если связей больше 8 (как у корня, где их 50+), отображаем табы фильтрации
            if (linksFilterEl) {
                if (connectedEdges.length > 8) {
                    linksFilterEl.innerHTML = '';
                    const tabDefs = [
                        { key: 'all', label: `Все (${groups.all})` },
                        { key: 'subject_to_jurisdiction', label: `Ветви (${groups.subject_to_jurisdiction})` }
                    ];
                    if (groups.demarcated_from > 0) tabDefs.push({ key: 'demarcated_from', label: `Разграничения (${groups.demarcated_from})` });
                    if (groups.other > 0) tabDefs.push({ key: 'other', label: `Иные (${groups.other})` });

                    tabDefs.forEach(td => {
                        const btn = document.createElement('button');
                        const isActive = currentLinksFilter === td.key;
                        btn.className = `px-1.5 py-0.5 rounded text-[9px] font-bold uppercase transition-all cursor-pointer ${
                            isActive ? 'bg-primary text-on-primary' : 'text-neutral-500 hover:text-primary bg-neutral-100 dark:bg-neutral-800'
                        }`;
                        btn.textContent = td.label;
                        btn.onclick = (ev) => {
                            ev.stopPropagation();
                            currentLinksFilter = td.key;
                            showKgNodeDrawer(node, isDrawerCollapsed);
                        };
                        linksFilterEl.appendChild(btn);
                    });
                    linksFilterEl.classList.remove('hidden');
                } else {
                    linksFilterEl.innerHTML = '';
                    linksFilterEl.classList.add('hidden');
                    currentLinksFilter = 'all';
                }
            }

            // Фильтрация связей в соответствии с выбранным табом
            const displayedEdges = connectedEdges.filter(e => {
                if (currentLinksFilter === 'all') return true;
                const rel = e.relation || 'subject_to_jurisdiction';
                if (currentLinksFilter === 'other') return rel !== 'subject_to_jurisdiction' && rel !== 'demarcated_from';
                return rel === currentLinksFilter;
            });

            displayedEdges.forEach(e => {
                const s = (typeof e.source === 'object' && e.source !== null) ? String(e.source.id) : String(e.source);
                const t = (typeof e.target === 'object' && e.target !== null) ? String(e.target.id) : String(e.target);
                const isOut = s === String(node.id);
                const otherId = isOut ? t : s;
                const otherNode = (currentKgGraphData.nodes || []).find(n => String(n.id) === String(otherId));
                const otherName = otherNode ? (otherNode.name || otherNode.id) : otherId;
                const otherCat = otherNode ? (otherNode.category || 'default') : 'default';
                const otherCatColor = getKgNodeColor(otherCat);
                const relationType = e.relation || 'subject_to_jurisdiction';
                const relStyle = getKgRelationStyle(relationType);
                const relationLabel = e.label || relStyle.label;

                const chip = document.createElement('div');
                chip.className = getKgRelationChipClass(relationType);
                chip.innerHTML = `
                    <span class="w-2 h-2 rounded-full shrink-0" style="background-color: ${otherCatColor};" title="${KG_CATEGORY_NAMES[otherCat] || otherCat}"></span>
                    <span class="rel-label font-bold inline-flex items-center gap-0.5">
                        <span class="material-symbols-outlined text-[13px]">${relStyle.icon}</span> ${escapeHTML(relationLabel)}:
                    </span>
                    <span class="font-medium text-neutral-900 dark:text-neutral-100 truncate max-w-[200px]">${escapeHTML(otherName)}</span>
                `;
                chip.onclick = () => {
                    if (otherNode) {
                        focusNodeInGraph(otherNode.id);
                    }
                };
                linksContainer.appendChild(chip);
            });
        } else {
            if (linksSection) linksSection.classList.add('hidden');
        }
    }

    initKgDrawerGestures();

    if (startCollapsed) {
        window.collapseKgNodeDrawer();
    } else {
        window.expandKgNodeDrawer();
    }

    if (drawer && !drawer.__touchIsolated) {
        drawer.__touchIsolated = true;
        drawer.addEventListener('touchstart', e => e.stopPropagation(), { passive: true });
        drawer.addEventListener('touchmove', e => e.stopPropagation(), { passive: true });
        drawer.addEventListener('pointerdown', e => e.stopPropagation(), { passive: true });
    }

    drawer.classList.remove('hidden');
};

let currentKgDrawerNode = null;

window.closeKgNodeDrawer = function() {
    const drawer = document.getElementById('kg-node-drawer');
    if (drawer) drawer.classList.add('hidden');
    isDrawerCollapsed = false;
    currentKgDrawerNode = null;
};

window.openPracticeForKgNode = function() {
    const sub = currentKgSubject || (typeof getActiveDeckSubject === 'function' ? getActiveDeckSubject() : 'all');
    if (window.openPracticeModal) {
        window.openPracticeModal(sub);
    }
};

window.openCardForKgNode = function() {
    if (!currentKgDrawerNode || !currentKgDrawerNode.card_id) return;
    if (window.requestEditCard) {
        window.requestEditCard(currentKgDrawerNode.card_id);
    }
};



