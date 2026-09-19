let currentKgSubject = '';
let currentKgGraphData = null;
let currentKgTreeData = null;
let currentKgView = 'tree'; // 'tree' | 'graph'
let currentKgLayout = 'force'; // 'force' | 'radial' | 'tree'
let currentForceGraphInstance = null;

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
        color: '#3b82f6',
        label: 'входит в структуру',
        icon: 'schema',
        borderClass: 'border-blue-500/30 hover:border-blue-500',
        textClass: 'text-blue-600 dark:text-blue-400',
        bgClass: 'bg-blue-50/70 dark:bg-blue-950/40'
    },
    'demarcated_from': {
        color: '#f59e0b',
        label: 'разграничивается с',
        icon: 'compare_arrows',
        borderClass: 'border-amber-500/30 hover:border-amber-500',
        textClass: 'text-amber-600 dark:text-amber-400',
        bgClass: 'bg-amber-50/70 dark:bg-amber-950/40'
    },
    'appealed_to': {
        color: '#06b6d4',
        label: 'обжалуется в',
        icon: 'upgrade',
        borderClass: 'border-cyan-500/30 hover:border-cyan-500',
        textClass: 'text-cyan-600 dark:text-cyan-400',
        bgClass: 'bg-cyan-50/70 dark:bg-cyan-950/40'
    },
    'excludes_application': {
        color: '#f43f5e',
        label: 'исключает применение',
        icon: 'block',
        borderClass: 'border-rose-500/30 hover:border-rose-500',
        textClass: 'text-rose-600 dark:text-rose-400',
        bgClass: 'bg-rose-50/70 dark:bg-rose-950/40'
    },
    'default': {
        color: '#64748b',
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

function getCleanGraphData() {
    if (!currentKgGraphData || !currentKgGraphData.nodes || currentKgGraphData.nodes.length === 0) {
        return null;
    }

    // 1. Clean nodes: preserve parent_id and hierarchy levels
    const nodes = currentKgGraphData.nodes.map(n => {
        const lvl = n.level !== undefined ? Number(n.level) : (n.parent_id ? 2 : 1);
        return {
            id: String(n.id),
            name: n.name || n.id,
            category: n.category || 'authority',
            summary: n.summary || '',
            level: lvl,
            parent_id: n.parent_id ? String(n.parent_id) : undefined,
            val: lvl === 0 ? 14 : (lvl === 1 ? 8 : 4.5)
        };
    });

    const nodeMap = new Map(nodes.map(n => [n.id, n]));
    const nodeIds = new Set(nodes.map(n => n.id));

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
                label: e.label || ''
            };
        })
        .filter(e => nodeIds.has(e.source) && nodeIds.has(e.target) && e.source !== e.target);

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
    if (!sub || sub === 'all') sub = 'sudoustr';
    currentKgSubject = sub;

    const badge = document.getElementById('kg-subject-badge');
    if (badge) badge.textContent = sub.toUpperCase();

    const loading = document.getElementById('kg-loading');
    const emptyState = document.getElementById('kg-empty-state');
    const treeView = document.getElementById('kg-tree-view');
    const countBadge = document.getElementById('kg-node-count-badge');

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

        if (nodesCount === 0) {
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
    if (!sub || sub === 'all') sub = 'sudoustr';
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
    if (!sub || sub === 'all') sub = 'sudoustr';

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

    const card = document.createElement('div');
    card.className = "tree-node-card p-3 rounded-xl bg-surface-container-lowest border border-neutral-200 dark:border-neutral-800 hover:border-neutral-400 transition-all flex items-start justify-between gap-2.5 cursor-pointer shadow-xs select-none";
    
    card.innerHTML = `
        <div class="flex items-start gap-2.5 min-w-0">
            ${hasChildren ? `
                <button class="tree-toggle-btn text-neutral-400 hover:text-primary p-0.5 mt-0.5 rounded transition-transform duration-200" title="Свернуть/Развернуть">
                    <span class="material-symbols-outlined text-[16px]">arrow_drop_down</span>
                </button>
            ` : `
                <span class="w-1.5 h-1.5 rounded-full bg-neutral-400 dark:bg-neutral-600 mt-2 ml-1 shrink-0"></span>
            `}
            <div class="flex flex-col min-w-0">
                <div class="flex items-center gap-1.5 flex-wrap">
                    <span class="font-bold text-xs sm:text-sm text-neutral-900 dark:text-neutral-100 font-mono">${escapeHTML(node.name || node.id)}</span>
                    <span class="px-1.5 py-0.2 rounded text-[9px] font-mono font-bold uppercase ${badgeClass}">${escapeHTML(catLabel)}</span>
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
let searchHighlightNodes = new Set();
let searchHighlightLinkKeys = new Set();
let searchBackboneNodes = new Set();
let searchBackboneLinkKeys = new Set();
let activeSearchTargetId = null;
let currentLinksFilter = 'all';

function getGraphLinkKey(source, target) {
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

    // 1. Сила отталкивания (Charge Repulsion)
    if (graphInstance.d3Force('charge')) {
        graphInstance.d3Force('charge')
            .strength(node => {
                const lvl = (node.level !== undefined) ? node.level : 1;
                if (layoutType === 'tree' || layoutType === 'horizontal' || layoutType === 'radial') {
                    return -25; // Деликатное отталкивание в геометрических режимах
                }
                // Органический режим: мощное распределение веток от центра
                if (lvl === 0) return -2500;
                if (lvl === 1) return -600;
                return -150;
            })
            .distanceMax(2200);
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
                    return Math.max(300, Math.min(480, 220 + branchCount * 3.5));
                }
                if ((sLvl === 1 && tLvl === 2) || (sLvl === 2 && tLvl === 1)) {
                    return 75;
                }
                return 120;
            })
            .strength(link => {
                const s = (typeof link.source === 'object' && link.source !== null) ? link.source : (nodeMap.get(String(link.source)) || {});
                const t = (typeof link.target === 'object' && link.target !== null) ? link.target : (nodeMap.get(String(link.target)) || {});
                const sLvl = (s.level !== undefined) ? s.level : 1;
                const tLvl = (t.level !== undefined) ? t.level : 1;

                if (layoutType === 'tree' || layoutType === 'horizontal' || layoutType === 'radial') {
                    return 0.12; // Мягкая связность, геометрия управляется целевыми позициями
                }

                if (sLvl === 0 || tLvl === 0) return 0.90;
                if ((sLvl === 1 && tLvl === 2) || (sLvl === 2 && tLvl === 1)) return 0.85;
                return 0.04; // Деликатные кросс-связи
            });
    }

    // 3. Сила коллизии (Collide) — полное исключение наложения кружков
    if (window.d3 && window.d3.forceCollide) {
        graphInstance.d3Force('collide', window.d3.forceCollide()
            .radius(node => {
                const lvl = (node.level !== undefined) ? node.level : 2;
                if (lvl === 0) return 42;
                if (lvl === 1) return 26;
                return 16;
            })
            .strength(0.85)
            .iterations(2)
        );
    }

    // 4. Позиционные силы для чистых упорядоченных раскладок
    const targetXMap = new Map();
    const targetYMap = new Map();

    if (layoutType === 'tree') {
        // Дерево: СВЕРХУ ВНИЗ (Четкие параллельные вертикальные колонки, без пересечений)
        rootNodes.forEach(r => {
            r.fx = 0;
            r.fy = -480;
            targetXMap.set(String(r.id), 0);
            targetYMap.set(String(r.id), -480);
        });

        const branchSpacingX = 85; // Просторное горизонтальное расстояние между ветками
        branches.forEach((b, idx) => {
            const bX = (idx - (branchCount - 1) / 2) * branchSpacingX;
            const bY = (idx % 2 === 0) ? -220 : -100; // Чередование эшелонов по высоте
            targetXMap.set(String(b.id), bX);
            targetYMap.set(String(b.id), bY);

            const leaves = branchLeavesMap.get(String(b.id)) || [];
            leaves.forEach((leaf, lIdx) => {
                const leafX = bX; // Строго в вертикальной колонке родительского института
                const leafY = bY + 120 + (lIdx * 48);
                targetXMap.set(String(leaf.id), leafX);
                targetYMap.set(String(leaf.id), leafY);
            });
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
        // Горизонтально: СЛЕВА НАПРАВО (Четкие параллельные горизонтальные линии)
        rootNodes.forEach(r => {
            r.fx = -480;
            r.fy = 0;
            targetXMap.set(String(r.id), -480);
            targetYMap.set(String(r.id), 0);
        });

        const branchSpacingY = 70;
        branches.forEach((b, idx) => {
            const bY = (idx - (branchCount - 1) / 2) * branchSpacingY;
            const bX = (idx % 2 === 0) ? -220 : -100;
            targetXMap.set(String(b.id), bX);
            targetYMap.set(String(b.id), bY);

            const leaves = branchLeavesMap.get(String(b.id)) || [];
            leaves.forEach((leaf, lIdx) => {
                const leafX = bX + 120 + (lIdx * 48);
                const leafY = bY; // Строго на горизонтальной линии родителя
                targetXMap.set(String(leaf.id), leafX);
                targetYMap.set(String(leaf.id), leafY);
            });
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
        // Радиальный: Аккуратный просторный цветок с расходящимися лучами (STARBURST)
        rootNodes.forEach(r => {
            r.fx = 0;
            r.fy = 0;
            targetXMap.set(String(r.id), 0);
            targetYMap.set(String(r.id), 0);
        });

        // 71 институт на двух ступенях радиуса: четные 340px, нечетные 520px
        branches.forEach((b, idx) => {
            const angle = (idx / branchCount) * 2 * Math.PI;
            const radius = (idx % 2 === 0) ? 340 : 520;
            const bX = Math.cos(angle) * radius;
            const bY = Math.sin(angle) * radius;
            targetXMap.set(String(b.id), bX);
            targetYMap.set(String(b.id), bY);

            const leaves = branchLeavesMap.get(String(b.id)) || [];
            leaves.forEach((leaf, lIdx) => {
                const leafRadius = radius + 130 + (lIdx * 45);
                const angleOffset = leaves.length > 1 ? ((lIdx - (leaves.length - 1) / 2) * 0.015) : 0;
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

    const inactiveClass = "px-2.5 py-1 rounded-lg font-bold uppercase transition-all text-neutral-500 hover:text-primary hover:bg-neutral-100 dark:hover:bg-neutral-800 flex items-center gap-1 cursor-pointer";
    const activeClass = "px-2.5 py-1 rounded-lg font-bold uppercase transition-all bg-primary text-on-primary shadow-xs flex items-center gap-1 cursor-pointer";

    if (btnForce) btnForce.className = currentKgLayout === 'force' ? activeClass : inactiveClass;
    if (btnRadial) btnRadial.className = currentKgLayout === 'radial' ? activeClass : inactiveClass;
    if (btnTree) btnTree.className = currentKgLayout === 'tree' ? activeClass : inactiveClass;
    if (btnLr) btnLr.className = currentKgLayout === 'horizontal' ? activeClass : inactiveClass;

    if (!currentForceGraphInstance) return;

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

window.clearKgSearch = function() {
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

    closeKgNodeDrawer();

    if (currentForceGraphInstance) {
        currentForceGraphInstance.refresh();
        currentForceGraphInstance.zoomToFit(400, 40);
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

window.selectSearchResult = function(targetNode, closeDropdown = true) {
    if (!targetNode) return;
    const resBox = document.getElementById('kg-search-results');
    if (closeDropdown && resBox) resBox.classList.add('hidden');

    const cleanData = getCleanGraphData();
    if (!cleanData || !cleanData.nodes) return;

    activeSearchTargetId = String(targetNode.id);
    searchHighlightNodes.clear();
    searchHighlightLinkKeys.clear();
    searchBackboneNodes.clear();
    searchBackboneLinkKeys.clear();

    const rootNode = cleanData.nodes.find(n => n.level === 0);
    const rootId = rootNode ? String(rootNode.id) : null;

    // 1. Построение магистрали от центра: Корень -> Институт -> Искомый узел
    searchBackboneNodes.add(activeSearchTargetId);
    searchHighlightNodes.add(activeSearchTargetId);

    let parentInstituteId = null;
    if (targetNode.level === 2 && targetNode.parent_id) {
        parentInstituteId = String(targetNode.parent_id);
    } else if (targetNode.level === 1) {
        parentInstituteId = activeSearchTargetId;
    }

    if (parentInstituteId) {
        searchBackboneNodes.add(parentInstituteId);
        searchHighlightNodes.add(parentInstituteId);

        // Все соседние понятия этого института для сохранения контекста ветви
        cleanData.nodes.forEach(n => {
            if (String(n.parent_id) === parentInstituteId) {
                searchHighlightNodes.add(String(n.id));
            }
        });
    }

    if (rootId) {
        searchBackboneNodes.add(rootId);
        searchHighlightNodes.add(rootId);
    }

    // 2. Классификация ребер по строковым ключам
    const allLinks = (cleanData.links && cleanData.links.length > 0)
        ? cleanData.links
        : ((currentKgGraphData && currentKgGraphData.edges) ? currentKgGraphData.edges : []);

    allLinks.forEach(edge => {
        const sId = String((typeof edge.source === 'object' && edge.source !== null) ? edge.source.id : edge.source);
        const tId = String((typeof edge.target === 'object' && edge.target !== null) ? edge.target.id : edge.target);
        const lKey = getGraphLinkKey(sId, tId);

        // Ребро магистрали (Корень <-> Институт или Институт <-> Целевой узел)
        const isBackbone = 
            (searchBackboneNodes.has(sId) && searchBackboneNodes.has(tId)) &&
            ((sId === rootId || tId === rootId) || (sId === activeSearchTargetId || tId === activeSearchTargetId));

        if (isBackbone) {
            searchBackboneLinkKeys.add(lKey);
            searchHighlightLinkKeys.add(lKey);
        } else if (searchHighlightNodes.has(sId) && searchHighlightNodes.has(tId)) {
            // Ребро внутри ветви института
            searchHighlightLinkKeys.add(lKey);
        } else if (sId === activeSearchTargetId || tId === activeSearchTargetId) {
            // Прямое ребро связи целевого узла (например, разграничение)
            searchHighlightNodes.add(sId);
            searchHighlightNodes.add(tId);
            searchHighlightLinkKeys.add(lKey);
        }
    });

    if (currentForceGraphInstance) {
        currentForceGraphInstance.refresh();
        if (typeof targetNode.x === 'number' && typeof targetNode.y === 'number') {
            const isMobile = window.innerWidth <= 768;
            const targetZoom = isMobile ? 1.5 : 1.8;
            const wrapper = document.getElementById('kg-graph-canvas-wrapper');
            const h = wrapper ? wrapper.clientHeight : (window.innerHeight - 150);
            // Вариант А: смещаем центр холста вниз, чтобы узел оказался в верхней половине видимой зоны над шторкой
            const yOffset = isMobile ? (h * 0.22 / targetZoom) : 0;
            currentForceGraphInstance.centerAt(targetNode.x, targetNode.y + yOffset, 750);
            currentForceGraphInstance.zoom(targetZoom, 750);
        }
    }

    // Отображаем плавающую плашку быстрого сброса
    const pill = document.getElementById('kg-selection-pill');
    const pillName = document.getElementById('kg-selection-pill-name');
    if (pill && pillName) {
        pillName.textContent = targetNode.name || targetNode.id;
        pill.classList.remove('hidden');
    }

    // Показываем карточку найденного понятия
    showKgNodeDrawer(targetNode);
};

window.focusNodeInGraph = function(nodeId) {
    const cleanData = getCleanGraphData();
    if (!cleanData || !cleanData.nodes) return;
    const target = cleanData.nodes.find(n => String(n.id) === String(nodeId));
    if (target) {
        selectSearchResult(target, true);
    }
};

window.initForceGraph = function(graphData) {
    const wrapper = document.getElementById('kg-graph-canvas-wrapper');
    if (!wrapper || !window.ForceGraph) return;

    const container = wrapper.parentElement || wrapper;
    const width = wrapper.clientWidth || container.clientWidth || window.innerWidth;
    const height = wrapper.clientHeight || container.clientHeight || (window.innerHeight - 150);

    const cleanData = getCleanGraphData();
    if (!cleanData || !cleanData.nodes || cleanData.nodes.length === 0) return;

    const isDark = document.documentElement.classList.contains('dark');
    const bgColor = isDark ? '#0e0e0e' : '#fbfbfb';

    // If graph already initialized, resize, update styling, and re-apply current layout
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

    wrapper.innerHTML = '';
    isInitialLayoutFit = true;
    wrapper.addEventListener('pointerdown', () => { isInitialLayoutFit = false; }, { passive: true });
    wrapper.addEventListener('touchstart', () => { isInitialLayoutFit = false; }, { passive: true });
    wrapper.addEventListener('wheel', () => { isInitialLayoutFit = false; }, { passive: true });

    currentForceGraphInstance = ForceGraph()(wrapper)
        .width(width)
        .height(height)
        .backgroundColor(bgColor)
        .nodeId('id')
        .nodeVal('val')
        .nodeLabel(node => `${node.name} (${KG_CATEGORY_NAMES[node.category] || node.category})`)
        .linkColor(link => {
            const lKey = getGraphLinkKey(link.source, link.target);
            if (searchHighlightNodes.size > 0) {
                if (searchBackboneLinkKeys.has(lKey)) {
                    // Чистый монохромный контрастный путь (Apple / Notion)
                    return isDark ? '#ffffff' : '#0f172a';
                }
                if (searchHighlightLinkKeys.has(lKey)) {
                    // Мягкий деликатный контекст ветви
                    return isDark ? 'rgba(255, 255, 255, 0.40)' : 'rgba(15, 23, 42, 0.40)';
                }
                return isDark ? 'rgba(255, 255, 255, 0.04)' : 'rgba(0, 0, 0, 0.04)';
            }
            // Спокойный нейтральный монохром без пестроты
            return isDark ? 'rgba(255, 255, 255, 0.12)' : 'rgba(0, 0, 0, 0.10)';
        })
        .linkWidth(link => {
            const lKey = getGraphLinkKey(link.source, link.target);
            if (searchHighlightNodes.size > 0) {
                if (searchBackboneLinkKeys.has(lKey)) return 2.2; // Четкая аккуратная магистраль
                if (searchHighlightLinkKeys.has(lKey)) return 1.2;
                return 0.5;
            }
            return 0.8;
        })
        .linkDirectionalParticles(() => 0) // Без вырвиглазных бегущих частиц!
        .warmupTicks(15)
        .cooldownTicks(70)
        .d3VelocityDecay(0.42) // Быстрый и плавный разогрев на мобильных устройствах
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
            const hasActiveSelection = searchHighlightNodes.size > 0;
            const isHighlighted = !hasActiveSelection || searchHighlightNodes.has(String(node.id));
            const isTarget = activeSearchTargetId === String(node.id);
            const isBackboneNode = searchBackboneNodes.has(String(node.id));
            const label = node.name || node.id;
            const baseR = Math.max(3.0, (node.val || 4) * 0.75);
            const radius = baseR * (isTarget ? 1.4 : (isBackboneNode ? 1.15 : 1.0));
            const currentDark = isDark;

            ctx.save();
            if (hasActiveSelection && !isHighlighted) {
                ctx.globalAlpha = 0.05; // Мягкое затемнение фона
            }

            // 1. Спокойный монохром узлов (Apple / Notion)
            let nodeFill;
            let nodeStroke;
            let strokeWidth = 1.0;

            if (isTarget) {
                // Целевой узел: чистый высокий контраст без неона
                nodeFill = currentDark ? '#ffffff' : '#0f172a';
                nodeStroke = currentDark ? '#000000' : '#ffffff';
                strokeWidth = 2.0;

                // Аккуратная тонкая окантовка без пульсаций и кислотного сияния
                ctx.beginPath();
                ctx.arc(node.x, node.y, radius + 3.5, 0, 2 * Math.PI, false);
                ctx.strokeStyle = currentDark ? 'rgba(255, 255, 255, 0.45)' : 'rgba(15, 23, 42, 0.35)';
                ctx.lineWidth = 1.0;
                ctx.stroke();

            } else if (isBackboneNode) {
                // Узлы магистрали (Корень и Родительский институт): благородный платиновый / угольный тон
                nodeFill = currentDark ? '#e2e8f0' : '#334155';
                nodeStroke = currentDark ? '#000000' : '#ffffff';
                strokeWidth = 1.4;

            } else if (hasActiveSelection && isHighlighted) {
                // Соседние понятия ветви
                nodeFill = currentDark ? '#94a3b8' : '#64748b';
                nodeStroke = currentDark ? 'rgba(255, 255, 255, 0.3)' : 'rgba(0, 0, 0, 0.15)';
                strokeWidth = 1.0;

            } else {
                // ОБЫЧНОЕ СОСТОЯНИЕ (Минимализм)
                if (node.level === 0) {
                    nodeFill = currentDark ? '#f8fafc' : '#0f172a';
                    nodeStroke = currentDark ? 'rgba(255, 255, 255, 0.6)' : '#ffffff';
                    strokeWidth = 1.8;
                } else if (node.level === 1) {
                    nodeFill = currentDark ? '#94a3b8' : '#475569';
                    nodeStroke = currentDark ? 'rgba(255, 255, 255, 0.2)' : 'rgba(0, 0, 0, 0.12)';
                    strokeWidth = 1.0;
                } else {
                    nodeFill = currentDark ? '#475569' : '#94a3b8';
                    nodeStroke = currentDark ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.08)';
                    strokeWidth = 0.6;
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

            // 4. Текстовая плашка-метка узла (Obsidian-Style Semantic Zoom LOD)
            let shouldShowLabel = false;
            if (isTarget || isBackboneNode) {
                // Выделенный узел и его опорная магистраль (родитель и корень) видны всегда
                shouldShowLabel = true;
            } else if (hasActiveSelection && isHighlighted) {
                // Соседние понятия активной ветки показываются при комфортном масштабе
                shouldShowLabel = globalScale >= 0.70;
            } else if (node.level === 0) {
                // Заголовок курса (корень) виден всегда
                shouldShowLabel = true;
            } else if (node.level === 1) {
                // Институты появляются при приближении (когда в кадре несколько институтов)
                shouldShowLabel = globalScale >= 0.85;
            } else {
                // Понятия появляются при глубоком приближении
                shouldShowLabel = globalScale >= 1.35;
            }

            if (isHighlighted && shouldShowLabel) {
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
            }

            ctx.restore();
        })
        .nodePointerAreaPaint((node, color, ctx) => {
            const baseR = Math.max(3.0, (node.val || 4) * 0.75);
            // Увеличиваем сенсорный радиус касания для мобильных пальцев (минимум 26px)
            const radius = Math.max(26, baseR + 10);
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI, false);
            ctx.fill();

            // Также регистрируем область текстовой плашки понятия под узлом,
            // чтобы нажатие на текст на телефоне не считалось фоновым кликом!
            const label = node.name || node.id;
            if (label) {
                const baseFontSize = (node.level === 0 ? 12 : (node.level === 1 ? 9.5 : 8));
                const lineHeight = baseFontSize + 3.0;
                const lines = node.__lines || wrapNodeText(label, node.level === 0 ? 22 : 16);
                const startY = node.y + (baseR * 1.4) + 4.5 + (lineHeight / 2);
                
                lines.forEach((line, i) => {
                    const lineY = startY + (i * lineHeight);
                    const pillW = Math.max(60, line.length * (baseFontSize * 0.7) + 20);
                    const pillH = baseFontSize + 9.0;
                    ctx.beginPath();
                    ctx.rect(node.x - pillW / 2, lineY - pillH / 2, pillW, pillH);
                    ctx.fill();
                });
            }
        })
        .onNodeClick(node => {
            if (!node || !node.id) return;
            const now = Date.now();
            if (activeSearchTargetId === String(node.id)) {
                // Если клик повторный в пределах 600мс — это мобильный дубль тапа, не сбрасываем
                if (now - (window.__lastKgNodeClickTime || 0) < 600) return;
                // Иначе просто подтверждаем открытие карточки понятия
                showKgNodeDrawer(node);
                return;
            }
            window.__lastKgNodeClickTime = now;
            focusNodeInGraph(node.id);
        })
        .onBackgroundClick(() => {
            const resBox = document.getElementById('kg-search-results');
            if (resBox) resBox.classList.add('hidden');

            // На десктопе сбрасываем поиск кликом по фону
            if (window.innerWidth > 768) {
                window.clearKgSearch();
            } else {
                // На мобильных: если шторка понятия закрыта и есть активное выделение,
                // касание свободного холста аккуратно сбрасывает изоляцию
                const drawer = document.getElementById('kg-node-drawer');
                const isDrawerOpen = drawer && !drawer.classList.contains('hidden');
                if (!isDrawerOpen && (activeSearchTargetId || searchHighlightNodes.size > 0)) {
                    window.clearKgSearch();
                }
            }
        });

    setGraphLayout(currentKgLayout || 'force');

    // Resize on window resize
    window.addEventListener('resize', () => {
        if (currentForceGraphInstance && currentKgView === 'graph') {
            const w = wrapper.clientWidth || (wrapper.parentElement ? wrapper.parentElement.clientWidth : 0) || window.innerWidth;
            const h = wrapper.clientHeight || (wrapper.parentElement ? wrapper.parentElement.clientHeight : 0) || (window.innerHeight - 150);
            currentForceGraphInstance.width(w).height(h);
        }
    });
};

window.zoomGraph = function(factor) {
    if (!currentForceGraphInstance) return;
    const currentZoom = currentForceGraphInstance.zoom();
    currentForceGraphInstance.zoom(currentZoom * factor, 300);
};

window.resetGraphZoom = function() {
    if (!currentForceGraphInstance) return;
    if (activeSearchTargetId || searchHighlightNodes.size > 0) {
        window.clearKgSearch();
    } else {
        currentForceGraphInstance.zoomToFit(400, 40);
        triggerHaptic('light');
    }
};

window.showKgNodeDrawer = function(node) {
    const drawer = document.getElementById('kg-node-drawer');
    const badge = document.getElementById('kg-drawer-badge');
    const title = document.getElementById('kg-drawer-title');
    const summary = document.getElementById('kg-drawer-summary');
    const linksContainer = document.getElementById('kg-drawer-links');
    const linksSection = document.getElementById('kg-drawer-links-section');
    const linksCountEl = document.getElementById('kg-drawer-links-count');
    const linksFilterEl = document.getElementById('kg-drawer-links-filter');

    if (!drawer) return;

    const cat = node.category || 'authority';
    if (badge) {
        badge.className = `px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase shrink-0 badge-${cat}`;
        badge.textContent = KG_CATEGORY_NAMES[cat] || cat.toUpperCase();
    }
    if (title) title.textContent = node.name || node.id;
    if (summary) summary.textContent = node.summary || 'Детальное описание и законодательное основание отсутствуют.';

    // Рендер связей и разграничений
    if (linksContainer && currentKgGraphData && currentKgGraphData.edges) {
        linksContainer.innerHTML = '';
        const connectedEdges = (currentKgGraphData.edges || []).filter(e => {
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
                            showKgNodeDrawer(node);
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

    if (drawer && !drawer.__touchIsolated) {
        drawer.__touchIsolated = true;
        drawer.addEventListener('touchstart', e => e.stopPropagation(), { passive: true });
        drawer.addEventListener('touchmove', e => e.stopPropagation(), { passive: true });
        drawer.addEventListener('pointerdown', e => e.stopPropagation(), { passive: true });
    }

    drawer.classList.remove('hidden');
};

window.closeKgNodeDrawer = function() {
    const drawer = document.getElementById('kg-node-drawer');
    if (drawer) drawer.classList.add('hidden');
};


