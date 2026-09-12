# app/services/graph_service.py
"""
Knowledge Graph Consolidation and Seed Graph Service for Data Grinder.
Fulfills Requirements R1 (Tree Mindmap + Obsidian Graph) & R2 (Knowledge Graph Schema).
"""

import re
import json
from typing import Optional, Any, List, Dict, Tuple

# Valid Schema Invariants (from ORIGINAL_REQUEST.md & PROJECT.md)
VALID_CATEGORIES = {"authority", "instance", "condition", "exception", "legal_status"}
VALID_RELATIONS = {"appealed_to", "excludes_application", "demarcated_from", "subject_to_jurisdiction"}

# Category Specificity Ranking for attribute resolution (higher number = more specific)
CATEGORY_SPECIFICITY = {
    "exception": 5,
    "condition": 4,
    "instance": 3,
    "legal_status": 2,
    "authority": 1,
    "generic": 0,
}

# Synonyms for edge relation normalization
RELATION_SYNONYMS = {
    "appealed_to": "appealed_to",
    "appealed": "appealed_to",
    "appeal": "appealed_to",
    "обжалуется": "appealed_to",
    "обжалуется_в": "appealed_to",
    "апелляция": "appealed_to",
    "апелляция_в": "appealed_to",
    "кассация": "appealed_to",
    "кассация_в": "appealed_to",
    "excludes_application": "excludes_application",
    "excludes": "excludes_application",
    "исключает": "excludes_application",
    "исключает_применение": "excludes_application",
    "исключает_совмещение": "excludes_application",
    "запрет": "excludes_application",
    "demarcated_from": "demarcated_from",
    "demarcated": "demarcated_from",
    "разграничивается": "demarcated_from",
    "разграничивается_с": "demarcated_from",
    "подведомственность": "demarcated_from",
    "subject_to_jurisdiction": "subject_to_jurisdiction",
    "jurisdiction": "subject_to_jurisdiction",
    "подсудно": "subject_to_jurisdiction",
    "подсудность": "subject_to_jurisdiction",
    "условие": "subject_to_jurisdiction",
    "условие_статуса": "subject_to_jurisdiction",
}


def normalize_entity_name(name: str) -> str:
    """Normalizes an entity name for fuzzy deduplication across chunks.
    Strips punctuation, lowercases, and removes plural/case/adjectival inflections per word.
    """
    if not name:
        return ""
    words = re.findall(r'[a-zA-Zа-яА-Я0-9]+', name.lower())
    norm_words = []
    for w in words:
        # First remove compound adjectival and plural inflections
        w = re.sub(r'(?:ый|ий|ой|ая|яя|ое|ее|ые|ие|ого|его|ому|ему|ых|их|ым|им|ом|ем|ами|ями|ях|ах|ов|ев|ей|ам|ям)$', '', w)
        # Then remove single-letter case/plural endings
        w = re.sub(r'(?:ы|и|а|я|у|ю|е|о)$', '', w)
        if w:
            norm_words.append(w)
    return " ".join(norm_words)


def normalize_id(id_val: str, name: str = "") -> str:
    """Produces a clean slug ID from id_val or entity name."""
    target = id_val or name or "node"
    slug = re.sub(r'[^a-zA-Z0-9_]', '_', target.lower()).strip('_')
    slug = re.sub(r'_+', '_', slug)
    return slug or "entity_node"


def normalize_relation(rel: str) -> str:
    """Maps relation string to one of the 4 strict schema relations."""
    if not rel:
        return "subject_to_jurisdiction"
    cleaned = rel.strip().lower().replace(" ", "_").replace("-", "_")
    return RELATION_SYNONYMS.get(cleaned, "subject_to_jurisdiction")


def build_hierarchical_tree(
    nodes: list[dict],
    edges: list[dict] = None,
    root_title: str = "Каркас дисциплины"
) -> dict:
    """Builds a cycle-free, strictly hierarchical tree structure (Tree Mindmap).
    
    Subordination rules:
    - Level 0: Trunk (Deck Root or top Authority)
    - Level 1: Branches (Instances and Authorities)
    - Level 2: Forks/Leaves (Conditions, Exceptions, Legal Status)
    """
    if not nodes:
        return {
            "id": "root",
            "name": root_title,
            "category": "authority",
            "summary": "Каркас дисциплины.",
            "level": 0,
            "children": []
        }

    # Deep copy node objects to prevent modifying original inputs
    node_map = {
        str(n.get("id")): {
            "id": str(n.get("id")),
            "name": n.get("name", ""),
            "category": n.get("category", "authority"),
            "summary": n.get("summary", ""),
            "level": n.get("level", 0),
            "parent_id": str(n.get("parent_id")) if n.get("parent_id") else None,
            "children": []
        }
        for n in nodes if n.get("id")
    }

    # Infer parents for nodes lacking parent_id if edges are provided
    if edges:
        for edge in edges:
            src = str(edge.get("source", ""))
            tgt = str(edge.get("target", ""))
            rel = edge.get("relation", "")

            # If subordinate instance appeals to higher instance, link higher as parent
            if rel == "appealed_to":
                if src in node_map and tgt in node_map:
                    if not node_map[src].get("parent_id") and node_map[tgt]["category"] in ("authority", "instance"):
                        node_map[src]["parent_id"] = tgt

            # If instance connects to condition/exception, make instance parent
            elif rel in ("subject_to_jurisdiction", "excludes_application", "demarcated_from"):
                if src in node_map and tgt in node_map:
                    tgt_node = node_map[tgt]
                    if tgt_node["category"] in ("condition", "exception", "legal_status"):
                        if not tgt_node.get("parent_id"):
                            tgt_node["parent_id"] = src

    # Build tree links with cycle prevention
    visited = set()
    parent_child_pairs = []

    for n_id, n_obj in node_map.items():
        p_id = n_obj.get("parent_id")
        if p_id and p_id in node_map and p_id != n_id:
            # Check for immediate or multi-hop cycle and cap tree depth at 15
            curr = p_id
            is_cycle = False
            path = {n_id}
            depth = 1
            max_allowed_depth = 15  # Prevents deep recursion overflow in Pydantic serialization
            while curr and curr in node_map:
                if curr in path or depth >= max_allowed_depth:
                    is_cycle = True
                    break
                path.add(curr)
                curr = node_map[curr].get("parent_id")
                depth += 1

            if not is_cycle:
                parent_child_pairs.append((p_id, n_id))
            else:
                n_obj["parent_id"] = None

    # Attach children
    for p_id, c_id in parent_child_pairs:
        node_map[p_id]["children"].append(node_map[c_id])
        visited.add(c_id)

    # Remaining top-level nodes
    top_level_nodes = [n for n_id, n in node_map.items() if n_id not in visited]

    # If there is exactly one top-level node and it's at level 0, it serves as root
    if len(top_level_nodes) == 1 and top_level_nodes[0].get("level") == 0:
        return top_level_nodes[0]

    # Otherwise synthesize a deck-level root container
    return {
        "id": "deck_root",
        "name": root_title,
        "category": "authority",
        "summary": f"Ментальный каркас предмета «{root_title}».",
        "level": 0,
        "children": top_level_nodes
    }


def build_tree_from_nodes(nodes: list[dict], subject_title: str = "") -> dict:
    """Synthesizes a hierarchical tree structure from a flat list of nodes using parent_id."""
    return build_hierarchical_tree(nodes=nodes, edges=[], root_title=subject_title or "Каркас знаний")


def clean_graph_data(nodes: list[dict], edges: list[dict]) -> tuple[list[dict], list[dict]]:
    """Cleans graph data by:
    1. Validating node categories and fields.
    2. Purging dangling edges whose source or target does not exist.
    3. Purging self-loops.
    4. Normalizing edge relations.
    5. Deduplicating edges.
    """
    valid_node_ids = {str(n.get("id")) for n in nodes if n.get("id")}
    clean_nodes = []
    for n in nodes:
        node_id = str(n.get("id", "")).strip()
        if not node_id:
            continue
        cat = str(n.get("category", "authority")).strip().lower()
        if cat not in VALID_CATEGORIES:
            cat = "authority"
        parent_id = str(n.get("parent_id")).strip() if n.get("parent_id") else None
        if parent_id not in valid_node_ids or parent_id == node_id:
            parent_id = None
        try:
            level = int(n.get("level", 0))
        except (ValueError, TypeError):
            level = 0

        name_val = str(n.get("name") or n.get("label") or node_id).strip()
        clean_nodes.append({
            "id": node_id,
            "name": name_val,
            "label": name_val,
            "category": cat,
            "summary": str(n.get("summary", "")).strip(),
            "parent_id": parent_id,
            "level": max(0, level),
        })

    seen_edges = set()
    clean_edges = []
    for e in edges:
        src = str(e.get("source", "")).strip()
        tgt = str(e.get("target", "")).strip()
        if not src or not tgt:
            continue
        if src not in valid_node_ids or tgt not in valid_node_ids:
            continue
        if src == tgt:
            continue
        rel = normalize_relation(str(e.get("relation", "")))
        label = e.get("label")
        edge_key = (src, tgt, rel)
        if edge_key not in seen_edges:
            seen_edges.add(edge_key)
            edge_obj = {
                "source": src,
                "target": tgt,
                "relation": rel,
            }
            if label:
                edge_obj["label"] = str(label).strip()
            clean_edges.append(edge_obj)

    return clean_nodes, clean_edges


def consolidate_knowledge_graphs(
    chunk_graphs: list[dict],
    fallback_title: str = "Каркас дисциплины"
) -> dict:
    """Consolidates multiple chunk-level knowledge graphs into a single unified graph.
    
    Operations:
    1. Deduplicates nodes by canonical ID or normalized entity name.
    2. Merges attributes (preserves the most complete/informative summary and most specific category).
    3. Deduplicates directed edges and remaps source/target through the alias table.
    4. Purges dangling edges whose source or target does not exist.
    5. Builds a hierarchical tree mindmap structure (root -> branches -> forks).
    
    Returns:
    {
        "nodes": [...],
        "edges": [...],
        "tree_data": {...}
    }
    """
    if not chunk_graphs:
        return {
            "nodes": [],
            "edges": [],
            "tree_data": {
                "id": "root",
                "name": fallback_title,
                "category": "authority",
                "summary": "Каркас дисциплины.",
                "level": 0,
                "children": []
            }
        }

    raw_nodes = []
    raw_edges = []

    # 1. Unpack all nodes and edges from chunk dicts (supporting minified and standard formats)
    for g in chunk_graphs:
        if not isinstance(g, dict):
            continue
        c_nodes = g.get("nodes") or g.get("n") or []
        c_edges = g.get("edges") or g.get("e") or []
        if isinstance(c_nodes, list):
            raw_nodes.extend(c_nodes)
        if isinstance(c_edges, list):
            raw_edges.extend(c_edges)

    # 2. Deduplicate nodes and build alias lookup table
    canonical_nodes: dict[str, dict] = {}
    alias_to_canonical: dict[str, str] = {}
    norm_name_to_canonical: dict[str, str] = {}

    for item in raw_nodes:
        if not isinstance(item, dict):
            continue

        raw_name = (item.get("name") or item.get("label") or item.get("n") or item.get("title") or "").strip()
        raw_id = (item.get("id") or "").strip()
        raw_cat = (item.get("category") or item.get("c") or "authority").strip().lower()
        raw_summary = (item.get("summary") or item.get("s") or item.get("desc") or "").strip()
        raw_parent = (item.get("parent_id") or item.get("p") or None)
        raw_level = item.get("level") if item.get("level") is not None else item.get("l", 1)

        if not raw_name and not raw_id:
            continue
        if not raw_name:
            raw_name = raw_id.replace("_", " ").title()

        norm_name = normalize_entity_name(raw_name)
        norm_id = normalize_id(raw_id, raw_name)

        # Determine if this node already exists
        target_canonical_id = None
        if norm_id in canonical_nodes:
            target_canonical_id = norm_id
        elif raw_id in alias_to_canonical:
            target_canonical_id = alias_to_canonical[raw_id]
        elif norm_name and norm_name in norm_name_to_canonical:
            target_canonical_id = norm_name_to_canonical[norm_name]

        if target_canonical_id and target_canonical_id in canonical_nodes:
            # Merge attributes
            existing = canonical_nodes[target_canonical_id]

            # Preserve most detailed summary
            new_score = len(raw_summary) + (50 if raw_summary.endswith('.') else 0)
            cur_score = len(existing["summary"]) + (50 if existing["summary"].endswith('.') else 0)
            if new_score > cur_score and raw_summary:
                existing["summary"] = raw_summary

            # Preserve most specific category
            new_cat = raw_cat if raw_cat in VALID_CATEGORIES else "authority"
            cur_cat = existing["category"]
            if CATEGORY_SPECIFICITY.get(new_cat, 0) > CATEGORY_SPECIFICITY.get(cur_cat, 0):
                existing["category"] = new_cat

            # Preserve highest hierarchy level (lowest integer)
            try:
                lvl_val = int(raw_level)
                if lvl_val < existing["level"]:
                    existing["level"] = lvl_val
            except (ValueError, TypeError):
                pass

            # Update parent if missing
            if not existing.get("parent_id") and raw_parent:
                existing["parent_id"] = str(raw_parent).strip()

            # Record aliases
            if raw_id:
                alias_to_canonical[raw_id] = target_canonical_id
            if norm_id:
                alias_to_canonical[norm_id] = target_canonical_id
            if norm_name:
                norm_name_to_canonical[norm_name] = target_canonical_id

        else:
            # New canonical node
            assigned_id = norm_id
            if assigned_id in canonical_nodes:
                assigned_id = f"{assigned_id}_{len(canonical_nodes)}"

            clean_category = raw_cat if raw_cat in VALID_CATEGORIES else "authority"
            try:
                level_int = int(raw_level)
            except (ValueError, TypeError):
                level_int = 1

            node_record = {
                "id": assigned_id,
                "name": raw_name,
                "label": raw_name,
                "category": clean_category,
                "summary": raw_summary or f"{raw_name}.",
                "parent_id": str(raw_parent).strip() if raw_parent else None,
                "level": max(0, level_int)
            }
            canonical_nodes[assigned_id] = node_record

            if raw_id:
                alias_to_canonical[raw_id] = assigned_id
            if norm_id:
                alias_to_canonical[norm_id] = assigned_id
            if norm_name:
                norm_name_to_canonical[norm_name] = assigned_id

    # 3. Deduplicate directed edges and remap endpoints through alias table
    valid_node_ids = set(canonical_nodes.keys())
    seen_edges = set()
    consolidated_edges = []

    for edge in raw_edges:
        if not isinstance(edge, dict):
            continue
        raw_src = str(edge.get("source") or edge.get("s") or "").strip()
        raw_tgt = str(edge.get("target") or edge.get("t") or "").strip()
        raw_rel = str(edge.get("relation") or edge.get("r") or "").strip()

        if not raw_src or not raw_tgt:
            continue

        src_canonical = alias_to_canonical.get(raw_src, alias_to_canonical.get(normalize_entity_name(raw_src), raw_src))
        tgt_canonical = alias_to_canonical.get(raw_tgt, alias_to_canonical.get(normalize_entity_name(raw_tgt), raw_tgt))

        # Purge dangling edges and self-loops
        if src_canonical not in valid_node_ids or tgt_canonical not in valid_node_ids:
            continue
        if src_canonical == tgt_canonical:
            continue

        clean_rel = normalize_relation(raw_rel)
        edge_key = (src_canonical, tgt_canonical, clean_rel)

        if edge_key not in seen_edges:
            seen_edges.add(edge_key)
            edge_obj = {
                "source": src_canonical,
                "target": tgt_canonical,
                "relation": clean_rel
            }
            label = edge.get("label")
            if label:
                edge_obj["label"] = str(label).strip()
            consolidated_edges.append(edge_obj)

    # Remap parent_id in nodes to canonical IDs or None
    for n in canonical_nodes.values():
        p_id = n.get("parent_id")
        if p_id:
            resolved_p = alias_to_canonical.get(p_id, p_id)
            if resolved_p in valid_node_ids and resolved_p != n["id"]:
                n["parent_id"] = resolved_p
            else:
                n["parent_id"] = None

    consolidated_nodes_list = list(canonical_nodes.values())

    # 4. Synthesize Hierarchical Tree Mindmap (tree_data)
    tree_data = build_hierarchical_tree(
        nodes=consolidated_nodes_list,
        edges=consolidated_edges,
        root_title=fallback_title
    )

    return {
        "nodes": consolidated_nodes_list,
        "edges": consolidated_edges,
        "tree_data": tree_data
    }


def generate_sudoustroystvo_seed_graph() -> dict:
    """Generates the authoritative seed knowledge graph and tree mindmap for 'sudoustroystvo'.
    
    Contains:
    - Exactly 15 nodes across all 5 categories (authority, instance, condition, exception, legal_status).
    - Exactly 14 edges across all 4 relations (appealed_to, demarcated_from, excludes_application, subject_to_jurisdiction).
    - 3-level tree mindmap structure.
    """
    nodes = [
        # --- LEVEL 0: TRUNK (AUTHORITY) ---
        {
            "id": "court_system_rf",
            "name": "Судебная система РФ",
            "category": "authority",
            "summary": "Единая государственная система правосудия, состоящая из Конституционного Суда РФ и судов общей юрисдикции и арбитражных судов во главе с Верховным Судом РФ.",
            "parent_id": None,
            "level": 0
        },
        # --- LEVEL 1: BRANCHES (AUTHORITIES & INSTANCES) ---
        {
            "id": "constitutional_court",
            "name": "Конституционный Суд РФ",
            "category": "authority",
            "summary": "Высший орган судебного конституционного контроля, проверяющий соответствие законов и нормативных актов Конституции РФ.",
            "parent_id": "court_system_rf",
            "level": 1
        },
        {
            "id": "supreme_court_rf",
            "name": "Верховный Суд РФ",
            "category": "authority",
            "summary": "Высший судебный орган по гражданским, уголовным делам и экономическим спорам, а также последняя инстанция судебного надзора.",
            "parent_id": "court_system_rf",
            "level": 1
        },
        {
            "id": "cassation_courts",
            "name": "Кассационные суды общей юрисдикции",
            "category": "instance",
            "summary": "Экстерриториальные суды кассационной инстанции, проверяющие законность вступивших в силу судебных актов судов общей юрисдикции.",
            "parent_id": "supreme_court_rf",
            "level": 1
        },
        {
            "id": "appellate_courts",
            "name": "Апелляционные суды общей юрисдикции",
            "category": "instance",
            "summary": "Суды по повторному рассмотрению не вступивших в силу решений верховных судов республик и областных судов.",
            "parent_id": "cassation_courts",
            "level": 1
        },
        {
            "id": "regional_courts",
            "name": "Областные и краевые суды",
            "category": "instance",
            "summary": "Суды среднего звена субъектов РФ, действующие как суд первой инстанции по сложным делам и апелляция на решения районных судов.",
            "parent_id": "appellate_courts",
            "level": 1
        },
        {
            "id": "district_courts",
            "name": "Районные суды",
            "category": "instance",
            "summary": "Основное звено судов общей юрисдикции, рассматривающее большинство дел по первой инстанции и апелляции на решения мировых судей.",
            "parent_id": "regional_courts",
            "level": 1
        },
        {
            "id": "magistrate_courts",
            "name": "Мировые судьи",
            "category": "instance",
            "summary": "Низовое звено правосудия, единолично рассматривающее несложные уголовные, гражданские и административные дела первой инстанции.",
            "parent_id": "district_courts",
            "level": 1
        },
        {
            "id": "arbitration_circuits",
            "name": "Арбитражные суды округов (кассация)",
            "category": "instance",
            "summary": "Суды кассационной инстанции по проверке решений арбитражных судов субъектов и арбитражных апелляционных судов.",
            "parent_id": "supreme_court_rf",
            "level": 1
        },
        {
            "id": "arbitration_appeals",
            "name": "Арбитражные апелляционные суды",
            "category": "instance",
            "summary": "Суды по проверке законности и обоснованности не вступивших в силу решений арбитражных судов субъектов РФ.",
            "parent_id": "arbitration_circuits",
            "level": 1
        },
        {
            "id": "arbitration_first",
            "name": "Арбитражные суды субъектов РФ",
            "category": "instance",
            "summary": "Суды первой инстанции по разрешению экономических споров между предпринимателями и юридическими лицами.",
            "parent_id": "arbitration_appeals",
            "level": 1
        },
        # --- LEVEL 2: FORKS & CONDITIONS (LEGAL STATUS, CONDITIONS, EXCEPTIONS) ---
        {
            "id": "judge_status_tenure",
            "name": "Статус судьи: несменяемость и иммунитет",
            "category": "legal_status",
            "summary": "Конституционные гарантии независимости судьи: несменяемость, неприкосновенность и материальное обеспечение за счет государства.",
            "parent_id": "court_system_rf",
            "level": 2
        },
        {
            "id": "judge_qualification_req",
            "name": "Квалификационные цензы кандидата в судьи",
            "category": "condition",
            "summary": "Обязательные требования: гражданство РФ, возраст от 25 лет (для районного суда), стаж по специальности от 5 лет и сдача квалификационного экзамена.",
            "parent_id": "judge_status_tenure",
            "level": 2
        },
        {
            "id": "magistrate_limits",
            "name": "Пределы компетенции мирового судьи",
            "category": "condition",
            "summary": "Ограничения: уголовные дела с наказанием до 3 лет лишения свободы, имущественные споры до 50 000 руб., расторжение брака без спора о детях.",
            "parent_id": "magistrate_courts",
            "level": 2
        },
        {
            "id": "emergency_courts_ban",
            "name": "Запрет чрезвычайных судов",
            "category": "exception",
            "summary": "Категорический конституционный запрет на создание любых чрезвычайных или внесудебных трибуналов в Российской Федерации.",
            "parent_id": "court_system_rf",
            "level": 2
        }
    ]

    edges = [
        # 1-5: Общая юрисдикция - вертикаль обжалования (appealed_to)
        {"source": "magistrate_courts", "target": "district_courts", "relation": "appealed_to", "label": "обжалуется в"},
        {"source": "district_courts", "target": "regional_courts", "relation": "appealed_to", "label": "обжалуется в"},
        {"source": "regional_courts", "target": "appellate_courts", "relation": "appealed_to", "label": "обжалуется в"},
        {"source": "appellate_courts", "target": "cassation_courts", "relation": "appealed_to", "label": "обжалуется в"},
        {"source": "cassation_courts", "target": "supreme_court_rf", "relation": "appealed_to", "label": "обжалуется в"},

        # 6-8: Арбитражная вертикаль (appealed_to)
        {"source": "arbitration_first", "target": "arbitration_appeals", "relation": "appealed_to", "label": "обжалуется в"},
        {"source": "arbitration_appeals", "target": "arbitration_circuits", "relation": "appealed_to", "label": "обжалуется в"},
        {"source": "arbitration_circuits", "target": "supreme_court_rf", "relation": "appealed_to", "label": "обжалуется в"},

        # 9-11: Разграничение юрисдикций и компетенций (demarcated_from)
        {"source": "arbitration_first", "target": "district_courts", "relation": "demarcated_from", "label": "разграничивается с"},
        {"source": "arbitration_first", "target": "regional_courts", "relation": "demarcated_from", "label": "разграничивается с"},
        {"source": "constitutional_court", "target": "supreme_court_rf", "relation": "demarcated_from", "label": "разграничивается с"},

        # 12: Исключение действия (excludes_application)
        {"source": "emergency_courts_ban", "target": "court_system_rf", "relation": "excludes_application", "label": "исключает применение"},

        # 13-14: Условия и подсудность (subject_to_jurisdiction)
        {"source": "magistrate_courts", "target": "magistrate_limits", "relation": "subject_to_jurisdiction", "label": "подсудно"},
        {"source": "judge_qualification_req", "target": "judge_status_tenure", "relation": "subject_to_jurisdiction", "label": "условие статуса"}
    ]

    tree = build_hierarchical_tree(nodes=nodes, edges=edges, root_title="Судебная система РФ")

    return {
        "subject": "sudoustroystvo",
        "graph_data": {
            "nodes": nodes,
            "edges": edges
        },
        "tree_data": tree
    }


def resolve_subject_alias(subject_slug: str) -> str:
    """Нормализует альтернативные и сокращенные названия предметов."""
    s = (subject_slug or "").strip().lower()
    if s in ("sudoustr", "sudoustroystvo", "court_system", "судоустройство", "sud", "суд"):
        return "sudoustroystvo"
    if s in ("civil_law", "гражданское", "гк_рф", "гражданское_право"):
        return "civil_law"
    return s


def get_all_subject_aliases(subject_slug: str) -> list[str]:
    """Возвращает все известные синонимы и сокращения предмета для полноты выборки."""
    canonical = resolve_subject_alias(subject_slug)
    if canonical == "sudoustroystvo":
        return ["sudoustroystvo", "sudoustr", "court_system", "судоустройство", "sud", "суд"]
    if canonical == "civil_law":
        return ["civil_law", "гражданское", "гк_рф", "гражданское_право"]
    return [subject_slug] if subject_slug == canonical else [subject_slug, canonical]


def get_preset_seed_graph(subject_slug: str) -> Optional[dict]:
    """Returns the pre-computed seed knowledge graph for standard preset decks."""
    normalized = resolve_subject_alias(subject_slug)
    if normalized in ("sudoustroystvo", "court_system", "судоустройство"):
        res = generate_sudoustroystvo_seed_graph()
        res["subject"] = subject_slug
        return res
    return None


def synthesize_graph_from_cards(cards: list, fallback_title: str = "Каркас дисциплины") -> dict:
    """
    Автоматически синтезирует семантический граф знаний и иерархическое дерево
    из реальных карточек колоды, если граф еще не был сохранен в БД.
    Обеспечивает 100% работоспособность дерева и графа для любых пользовательских колод.
    """
    if not cards:
        return {
            "graph_data": {"nodes": [], "edges": []},
            "tree_data": None
        }

    clean_title = (fallback_title or "Дисциплина").strip()
    root_id = normalize_id(clean_title)
    
    root_node = {
        "id": root_id,
        "name": clean_title.upper() if len(clean_title) <= 12 else clean_title.capitalize(),
        "category": "authority",
        "summary": f"Ментальный каркас и ключевые институты курса «{clean_title}».",
        "parent_id": None,
        "level": 0
    }

    nodes_map: Dict[str, dict] = {root_id: root_node}
    edges_list: List[dict] = []
    seen_edges: set = set()

    # Группируем карточки по темам (Phrase или secondary_text)
    theme_cards: Dict[str, list] = {}
    for c in cards:
        theme = ""
        if hasattr(c, "phrase") and c.phrase and c.phrase.text:
            theme = c.phrase.text.strip()
        elif hasattr(c, "theme") and c.theme:
            theme = c.theme.strip()
        elif isinstance(c, dict):
            theme = (c.get("theme") or (c.get("phrase") or {}).get("text") or "").strip()

        if not theme and hasattr(c, "secondary_text") and c.secondary_text:
            parts = [p.strip() for p in c.secondary_text.split("|") if p.strip()]
            if parts:
                theme = parts[0]
        elif not theme and isinstance(c, dict) and c.get("secondary_text"):
            parts = [p.strip() for p in c["secondary_text"].split("|") if p.strip()]
            if parts:
                theme = parts[0]

        theme = theme or "Ключевые институты"
        theme_cards.setdefault(theme, []).append(c)

    # Формируем ветви 1-го уровня (Темы / Институты)
    for t_name, c_list in theme_cards.items():
        t_id = normalize_id(t_name)
        if t_id == root_id:
            t_id = f"{t_id}_branch"

        summary_text = f"Институт «{t_name}» ({len(c_list)} ключевых правил и развилок)."
        if t_id not in nodes_map:
            nodes_map[t_id] = {
                "id": t_id,
                "name": t_name,
                "category": "instance",
                "summary": summary_text,
                "parent_id": root_id,
                "level": 1
            }
            edge_key = (t_id, root_id)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                edges_list.append({
                    "source": t_id,
                    "target": root_id,
                    "relation": "subject_to_jurisdiction",
                    "label": "входит в систему"
                })

        # Формируем развилки и условия 2-го уровня из карточек (до 4 ключевых узлов на тему)
        sub_added = 0
        for c in c_list:
            if sub_added >= 4:
                break
            sec = getattr(c, "secondary_text", "") if hasattr(c, "secondary_text") else (c.get("secondary_text", "") if isinstance(c, dict) else "")
            front = getattr(c, "text", "") if hasattr(c, "text") else (c.get("text", "") if isinstance(c, dict) else "")
            trans = getattr(c, "translation", "") if hasattr(c, "translation") else (c.get("translation", "") if isinstance(c, dict) else "")

            # Извлекаем сущность из подсказки или вопроса
            leaf_name = ""
            if sec and "|" in sec:
                leaf_name = sec.split("|")[-1].strip()
            elif sec:
                leaf_name = sec.strip()
            elif "?" in front:
                q_part = front.split("?")[0].strip()
                if len(q_part) <= 40:
                    leaf_name = q_part

            if leaf_name and len(leaf_name) >= 3:
                leaf_id = normalize_id(f"{t_id}_{leaf_name}")
                if leaf_id not in nodes_map:
                    cat = "condition"
                    rel = "subject_to_jurisdiction"
                    lbl = "условие"
                    if any(w in leaf_name.lower() or w in front.lower() for w in ("отлич", "разгранич", " vs ", "разниц")):
                        cat = "condition"
                        rel = "demarcated_from"
                        lbl = "разграничивается с"
                    elif any(w in leaf_name.lower() or w in front.lower() for w in ("обжал", "инстанци", "кассац", "апелляц", "надзор")):
                        cat = "instance"
                        rel = "appealed_to"
                        lbl = "обжалуется в"
                    elif any(w in leaf_name.lower() or w in front.lower() for w in ("статус", "иммунитет", "гаранти")):
                        cat = "legal_status"
                        lbl = "статус"
                    elif any(w in leaf_name.lower() or w in front.lower() for w in ("запрет", "исключ", "не допускает")):
                        cat = "exception"
                        rel = "excludes_application"
                        lbl = "исключает применение"

                    nodes_map[leaf_id] = {
                        "id": leaf_id,
                        "name": leaf_name[:50],
                        "category": cat,
                        "summary": trans[:120] if trans else leaf_name,
                        "parent_id": t_id,
                        "level": 2
                    }
                    edge_key = (leaf_id, t_id)
                    if edge_key not in seen_edges:
                        seen_edges.add(edge_key)
                        edges_list.append({
                            "source": leaf_id,
                            "target": t_id,
                            "relation": rel,
                            "label": lbl
                        })
                    sub_added += 1

    clean_nodes = list(nodes_map.values())
    tree = build_hierarchical_tree(nodes=clean_nodes, edges=edges_list, root_title=clean_title)

    return {
        "subject": fallback_title,
        "graph_data": {
            "nodes": clean_nodes,
            "edges": edges_list
        },
        "tree_data": tree
    }

