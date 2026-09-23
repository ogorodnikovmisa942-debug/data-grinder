# app/api/endpoints/graph.py
"""
FastAPI Router for Knowledge Graph & Mindmap Persistence.
Supports O(1) retrieval, upsert, tree synthesis, and preset seed fallbacks (R1, R2).
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status, Request
from pydantic import BaseModel, Field, ConfigDict
from app.core.limiter import limiter
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func
from sqlalchemy.orm import selectinload

from app.database.session import get_db
from app.database.models import TopicKnowledgeGraph, Card, utc_now
from app.core.auth import get_current_user_id
from collections import defaultdict
from app.services.graph_service import (
    clean_graph_data,
    build_hierarchical_tree,
    get_preset_seed_graph,
    resolve_subject_alias,
    get_all_subject_aliases,
    synthesize_graph_from_cards,
)

router = APIRouter()


def enrich_graph_with_user_learning_state(
    graph_data: dict,
    tree_data: Optional[dict],
    user_cards: list
) -> tuple[dict, Optional[dict]]:
    """Enriches graph and tree nodes with real-time user learning status (FSRS card states)."""
    if not user_cards or not graph_data:
        return graph_data, tree_data

    card_by_id = {c.id: c for c in user_cards if hasattr(c, "id")}
    
    card_tuples = []
    for c in user_cards:
        c_id = getattr(c, "id", None)
        c_state = getattr(c, "state", 0) or 0
        c_reps = getattr(c, "reps", 0) or 0
        front = (getattr(c, "text", "") or "").lower()
        trans = (getattr(c, "translation", "") or "").lower()
        sec = (getattr(c, "secondary_text", "") or "").lower()
        ch = (getattr(c, "chapter", "") or getattr(c, "phrase_text", "") or "").lower()
        slug = (getattr(c, "organ_slug", "") or "").lower()
        card_tuples.append((c_id, c_state, c_reps, front, trans, sec, ch, slug))

    nodes = graph_data.get("nodes", [])
    node_state_map = {}

    for node in nodes:
        node_id = str(node.get("id", ""))
        lvl = node.get("level", 2)
        if lvl == 0:
            continue
        
        assigned_card_id = node.get("card_id")
        matched_card = None

        if assigned_card_id and assigned_card_id in card_by_id:
            c = card_by_id[assigned_card_id]
            matched_card = (c.id, getattr(c, "state", 0) or 0, getattr(c, "reps", 0) or 0)
        else:
            n_name = (node.get("name", "") or "").lower().strip()
            n_id = node_id.lower()
            if n_name and len(n_name) >= 3:
                for c_id, c_state, c_reps, front, trans, sec, ch, slug in card_tuples:
                    if slug and slug in (n_id, n_name):
                        matched_card = (c_id, c_state, c_reps)
                        break
                    if n_name in trans or n_name in front or (len(n_name) >= 5 and n_name[:5] in trans):
                        matched_card = (c_id, c_state, c_reps)
                        break

        if matched_card:
            c_id, c_state, c_reps = matched_card
            is_learned = bool(c_state > 0 or c_reps > 0)
            node["card_id"] = c_id
            node["card_state"] = c_state
            node["reps"] = c_reps
            node["is_learned"] = is_learned
            node_state_map[node_id] = {
                "is_learned": is_learned,
                "card_state": c_state,
                "reps": c_reps,
                "card_id": c_id
            }
        else:
            node["card_state"] = 0
            node["reps"] = 0
            node["is_learned"] = False
            node_state_map[node_id] = {
                "is_learned": False,
                "card_state": 0,
                "reps": 0
            }

    branch_leaves = defaultdict(list)
    for node in nodes:
        if node.get("level") == 2 and node.get("parent_id"):
            branch_leaves[str(node.get("parent_id"))].append(node)

    for node in nodes:
        if node.get("level") == 1:
            b_id = str(node.get("id", ""))
            leaves = branch_leaves.get(b_id, [])
            total_leaves = len(leaves)
            learned_leaves = sum(1 for leaf in leaves if leaf.get("is_learned"))
            mastered_leaves = sum(1 for leaf in leaves if leaf.get("card_state") == 2)
            
            node["total_leaves"] = total_leaves
            node["learned_count"] = learned_leaves
            node["mastered_count"] = mastered_leaves
            node["is_learned"] = learned_leaves > 0
            if total_leaves > 0 and mastered_leaves == total_leaves:
                node["card_state"] = 2
            elif learned_leaves > 0:
                node["card_state"] = 1
            else:
                node["card_state"] = 0
            node_state_map[b_id] = {
                "is_learned": node["is_learned"],
                "card_state": node["card_state"],
                "learned_count": learned_leaves,
                "total_leaves": total_leaves
            }

    def enrich_tree_node(tnode):
        if not tnode or not isinstance(tnode, dict):
            return
        tid = str(tnode.get("id", ""))
        if tid in node_state_map:
            tnode.update(node_state_map[tid])
        for child in tnode.get("children", []):
            enrich_tree_node(child)

    if tree_data:
        enrich_tree_node(tree_data)

    return graph_data, tree_data


# --- PYDANTIC SCHEMAS ---

class NodeItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(..., description="Unique node identifier (slug/uuid)")
    name: str = Field(..., description="Concise entity name")
    category: str = Field(default="authority", description="authority | instance | condition | exception | legal_status")
    summary: str = Field(default="", description="1 complete factual sentence")
    parent_id: Optional[str] = Field(default=None, description="Parent node id for tree hierarchy")
    level: Optional[int] = Field(default=0, description="0=trunk/branch, 1=instance, 2=fork/condition")


class EdgeItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    source: str = Field(..., description="Source node id")
    target: str = Field(..., description="Target node id")
    relation: str = Field(default="appealed_to", description="appealed_to | excludes_application | demarcated_from | subject_to_jurisdiction")
    label: Optional[str] = Field(default=None, description="Optional relation label")


class GraphDataPayload(BaseModel):
    nodes: List[NodeItem] = Field(default_factory=list)
    edges: List[EdgeItem] = Field(default_factory=list)


class KnowledgeGraphUpsertIn(BaseModel):
    subject: str = Field(..., min_length=1, max_length=128, description="Deck subject slug e.g. sudoustroystvo")
    graph_data: GraphDataPayload = Field(..., description="Graph containing nodes and edges")
    tree_data: Optional[Dict[str, Any]] = Field(default=None, description="Optional precomputed hierarchical tree mindmap")


class KnowledgeGraphResponse(BaseModel):
    subject: str
    graph_data: Dict[str, Any]
    tree_data: Optional[Dict[str, Any]] = None
    updated_at: Optional[str] = None
    is_seed: bool = False
    is_empty: bool = False


# --- ENDPOINTS ---

@router.get("/knowledge-graph", response_model=KnowledgeGraphResponse)
async def get_knowledge_graph(
    subject: Optional[str] = Query(default=None, max_length=128, description="Subject/deck identifier"),
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Retrieves the knowledge graph and tree mindmap for the current user and subject.
    Supports unified alias resolution, conflict elimination, automatic synchronization of stale
    snapshots from current user deck, and preset seed fallbacks.
    """
    clean_sub = (subject or "").strip()
    if not clean_sub or clean_sub.lower() in ("all", "*", "generic"):
        stmt = select(Card.subject).where(
            Card.user_id == current_user
        ).order_by(Card.next_review.desc()).limit(1)
        res = await db.execute(stmt)
        active_sub = res.scalar()
        clean_sub = active_sub
        if not clean_sub:
            # Проверяем, есть ли хотя бы одна сохраненная запись графа у пользователя
            top_kg_stmt = select(TopicKnowledgeGraph.subject).where(
                TopicKnowledgeGraph.user_id == current_user
            ).order_by(TopicKnowledgeGraph.updated_at.desc()).limit(1)
            clean_sub = (await db.execute(top_kg_stmt)).scalar()

    if not clean_sub:
        return KnowledgeGraphResponse(
            subject="",
            graph_data={"nodes": [], "edges": [], "links": []},
            tree_data=None,
            is_empty=True
        )

    all_aliases = get_all_subject_aliases(clean_sub)
    if clean_sub not in all_aliases:
        all_aliases.insert(0, clean_sub)
    now = utc_now()

    # 1. Извлекаем актуальные карточки пользователя по всей группе алиасов
    card_stmt = select(Card).options(selectinload(Card.phrase)).where(
        Card.user_id == current_user,
        Card.subject.in_(all_aliases)
    )
    card_res = await db.execute(card_stmt)
    user_cards = card_res.scalars().all()

    # 2. Поиск записей графа в БД строго для текущего пользователя по всей группе алиасов
    stmt = select(TopicKnowledgeGraph).where(
        TopicKnowledgeGraph.user_id == current_user,
        TopicKnowledgeGraph.subject.in_(all_aliases)
    ).order_by(TopicKnowledgeGraph.updated_at.desc())
    result = await db.execute(stmt)
    records = result.scalars().all()

    # Fallback на пресетный сид-граф только для тестовых профилей или первого знакомства при отсутствии колод
    if not records and not user_cards:
        if current_user in ("default_user", "dev_user") or not current_user.isdigit():
            seed = get_preset_seed_graph(clean_sub)
            if seed:
                return KnowledgeGraphResponse(
                    subject=clean_sub,
                    graph_data=seed["graph_data"],
                    tree_data=seed.get("tree_data"),
                    updated_at=None,
                    is_seed=True
                )

    # Устраняем конфликты записей по алиасам одного и того же предмета для одного пользователя
    if len(records) > 1:
        primary_record = records[0]
        for dup in records[1:]:
            if dup.user_id == current_user:
                await db.delete(dup)
        await db.commit()
        records = [primary_record]

    record = records[0] if records else None

    # 3. Синхронизация устаревших снапшотов:
    # Если у пользователя сформирована реальная колода (15+ карт, напр. 300+ карт),
    # а сохраненный граф отсутствует, содержит < 20 узлов (старый баг 6 узлов)
    # или является вчерашним снапшотом (updated_at раньше сегодняшнего дня):
    is_stale = False
    if user_cards and len(user_cards) >= 15:
        if not record:
            is_stale = True
        elif record.graph_data:
            node_count = len(record.graph_data.get("nodes", []))
            if node_count < 20:
                is_stale = True
            elif not record.updated_at:
                is_stale = True
            elif record.updated_at.date() < now.date():
                is_stale = True
            elif record.graph_data.get("deck_size", 0) != len(user_cards):
                is_stale = True
            elif len(user_cards) >= 100 and node_count < 40 and clean_sub.lower() not in ("sudoustr", "sudoustroystvo", "sudoust"):
                is_stale = True

    if is_stale and user_cards:
        syn = synthesize_graph_from_cards(user_cards, fallback_title=clean_sub)
        g_data = syn.get("graph_data", {"nodes": [], "edges": []})
        g_data["deck_size"] = len(user_cards)
        t_data = syn.get("tree_data")

        if record and record.user_id == current_user:
            record.subject = clean_sub
            record.graph_data = g_data
            record.tree_data = t_data
            record.updated_at = now
        else:
            record = TopicKnowledgeGraph(
                user_id=current_user,
                subject=clean_sub,
                graph_data=g_data,
                tree_data=t_data,
                created_at=now,
                updated_at=now
            )
            db.add(record)

        try:
            await db.commit()
            await db.refresh(record)
        except Exception:
            await db.rollback()

        resp_g, resp_t = enrich_graph_with_user_learning_state(record.graph_data, record.tree_data, user_cards)
        return KnowledgeGraphResponse(
            subject=clean_sub,
            graph_data=resp_g,
            tree_data=resp_t,
            updated_at=record.updated_at.isoformat() if record.updated_at else now.isoformat(),
            is_seed=False
        )

    if record:
        g_data = record.graph_data or {"nodes": [], "edges": []}
        r_nodes = g_data.get("nodes", [])
        r_edges = g_data.get("edges", [])
        if r_nodes:
            from app.services.graph_service import ensure_connected_spiderweb
            r_nodes, r_edges = ensure_connected_spiderweb(r_nodes, r_edges, fallback_title=clean_sub)
            g_data = {"nodes": r_nodes, "edges": r_edges}

        resp_g, resp_t = enrich_graph_with_user_learning_state(g_data, record.tree_data, user_cards)
        return KnowledgeGraphResponse(
            subject=clean_sub,
            graph_data=resp_g,
            tree_data=resp_t,
            updated_at=record.updated_at.isoformat() if record.updated_at else None,
            is_seed=False
        )

    # 4. Динамический синтез графа и дерева из карточек текущего пользователя
    if user_cards:
        syn = synthesize_graph_from_cards(user_cards, fallback_title=clean_sub)
        if syn and syn.get("graph_data", {}).get("nodes"):
            new_kg = TopicKnowledgeGraph(
                user_id=current_user,
                subject=clean_sub,
                graph_data=syn["graph_data"],
                tree_data=syn["tree_data"],
                created_at=now,
                updated_at=now
            )
            db.add(new_kg)
            try:
                await db.commit()
            except Exception:
                await db.rollback()

            resp_g, resp_t = enrich_graph_with_user_learning_state(syn["graph_data"], syn["tree_data"], user_cards)
            return KnowledgeGraphResponse(
                subject=clean_sub,
                graph_data=resp_g,
                tree_data=resp_t,
                updated_at=now.isoformat(),
                is_seed=False
            )

    # 5. Проверка пресетного сид-графа (только для тестовых/демо колод без пользовательских карточек)
    seed = get_preset_seed_graph(clean_sub)
    if seed:
        return KnowledgeGraphResponse(
            subject=clean_sub,
            graph_data=seed["graph_data"],
            tree_data=seed.get("tree_data"),
            updated_at=None,
            is_seed=True
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Knowledge graph for subject '{clean_sub}' not found."
    )


@router.post("/knowledge-graph", response_model=KnowledgeGraphResponse)
async def upsert_knowledge_graph(
    payload: KnowledgeGraphUpsertIn,
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Upserts knowledge graph data for the given subject.
    Automatically purges dangling edges and generates hierarchical tree mindmap if omitted.
    """
    raw_nodes = [n.model_dump() for n in payload.graph_data.nodes]
    raw_edges = [e.model_dump() for e in payload.graph_data.edges]

    clean_nodes, clean_edges = clean_graph_data(raw_nodes, raw_edges)

    # Synthesize tree if omitted or empty
    tree_data = payload.tree_data
    if not tree_data:
        tree_data = build_hierarchical_tree(clean_nodes, clean_edges, root_title=payload.subject)

    final_graph_data = {
        "nodes": clean_nodes,
        "edges": clean_edges
    }

    all_aliases = get_all_subject_aliases(payload.subject)
    await db.execute(delete(TopicKnowledgeGraph).where(
        TopicKnowledgeGraph.user_id == current_user,
        TopicKnowledgeGraph.subject.in_(all_aliases),
        TopicKnowledgeGraph.subject != payload.subject
    ))

    stmt = select(TopicKnowledgeGraph).where(
        TopicKnowledgeGraph.user_id == current_user,
        TopicKnowledgeGraph.subject == payload.subject
    )
    result = await db.execute(stmt)
    record = result.scalars().first()

    if record:
        record.graph_data = final_graph_data
        record.tree_data = tree_data
        record.updated_at = utc_now()
    else:
        record = TopicKnowledgeGraph(
            user_id=current_user,
            subject=payload.subject,
            graph_data=final_graph_data,
            tree_data=tree_data,
            created_at=utc_now(),
            updated_at=utc_now()
        )
        db.add(record)

    try:
        await db.commit()
    except Exception as commit_err:
        await db.rollback()
        # Handle concurrent upsert race condition
        stmt = select(TopicKnowledgeGraph).where(
            TopicKnowledgeGraph.user_id == current_user,
            TopicKnowledgeGraph.subject == payload.subject
        )
        result = await db.execute(stmt)
        record = result.scalars().first()
        if record:
            record.graph_data = final_graph_data
            record.tree_data = tree_data
            record.updated_at = utc_now()
            await db.commit()
        else:
            raise commit_err

    await db.refresh(record)

    return KnowledgeGraphResponse(
        subject=record.subject,
        graph_data=record.graph_data,
        tree_data=record.tree_data,
        updated_at=record.updated_at.isoformat() if record.updated_at else utc_now().isoformat(),
        is_seed=False
    )


@router.delete("/knowledge-graph")
async def delete_knowledge_graph(
    subject: str = Query(..., min_length=1, max_length=128),
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Deletes custom knowledge graph for the given subject across all aliases."""
    all_aliases = get_all_subject_aliases(subject)
    stmt = select(TopicKnowledgeGraph).where(
        TopicKnowledgeGraph.user_id == current_user,
        TopicKnowledgeGraph.subject.in_(all_aliases)
    )
    result = await db.execute(stmt)
    records = result.scalars().all()
    if records:
        for r in records:
            await db.delete(r)
        await db.commit()
        return {"status": "deleted", "subject": subject}
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Knowledge graph for subject '{subject}' not found."
    )


@limiter.limit("3/minute")
@router.post("/knowledge-graph/rebuild", response_model=KnowledgeGraphResponse)
async def rebuild_knowledge_graph(
    request: Request,
    subject: str = Query(..., min_length=1, max_length=128),
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Принудительно перестраивает граф знаний и дерево напрямую из актуальных карточек пользователя в БД."""
    clean_sub = (subject or "").strip()
    if not clean_sub or clean_sub.lower() in ("all", "*", "generic"):
        top_stmt = select(Card.subject).where(
            Card.user_id == current_user
        ).order_by(Card.next_review.desc()).limit(1)
        res = await db.execute(top_stmt)
        active_sub = res.scalar()
        clean_sub = active_sub
        if not clean_sub:
            def_stmt = select(Card.subject).where(
                Card.user_id.in_(["default_user", "dev_user"])
            ).order_by(Card.next_review.desc()).limit(1)
            clean_sub = (await db.execute(def_stmt)).scalar()

    if not clean_sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Нет карточек для перестроения графа."
        )

    all_aliases = get_all_subject_aliases(clean_sub)
    if clean_sub not in all_aliases:
        all_aliases.insert(0, clean_sub)

    stmt = select(Card).options(selectinload(Card.phrase)).where(
        Card.user_id == current_user,
        Card.subject.in_(all_aliases)
    )
    cards_res = await db.execute(stmt)
    user_cards = cards_res.scalars().all()

    if not user_cards:
        if current_user in ("default_user", "dev_user"):
            stmt_def = select(Card).options(selectinload(Card.phrase)).where(Card.subject.in_(all_aliases))
            res_def = await db.execute(stmt_def)
            user_cards = res_def.scalars().all()

    if not user_cards:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Нет карточек для перестроения графа по предмету '{clean_sub}'."
        )

    syn = synthesize_graph_from_cards(user_cards, fallback_title=clean_sub)
    g_data = syn.get("graph_data", {"nodes": [], "edges": []})
    g_data["deck_size"] = len(user_cards)
    t_data = syn.get("tree_data")

    # Исключаем конфликты записей по алиасам одного и того же предмета для одного пользователя
    await db.execute(delete(TopicKnowledgeGraph).where(
        TopicKnowledgeGraph.user_id == current_user,
        TopicKnowledgeGraph.subject.in_(all_aliases),
        TopicKnowledgeGraph.subject != clean_sub
    ))

    rec_stmt = select(TopicKnowledgeGraph).where(
        TopicKnowledgeGraph.user_id == current_user,
        TopicKnowledgeGraph.subject == clean_sub
    )
    rec_res = await db.execute(rec_stmt)
    record = rec_res.scalars().first()

    now = utc_now()
    if record:
        record.graph_data = g_data
        record.tree_data = t_data
        record.updated_at = now
    else:
        record = TopicKnowledgeGraph(
            user_id=current_user,
            subject=clean_sub,
            graph_data=g_data,
            tree_data=t_data,
            created_at=now,
            updated_at=now
        )
        db.add(record)
    # Синхронизируем интерактивные практические задания по предмету (R2)
    try:
        from app.services.practice_service import generate_practice_session
        await generate_practice_session(user_id=current_user, subject=clean_sub, count=10, db=db)
    except Exception as pe:
        print(f"[Graph Rebuild] Предупреждение: сбой синхронизации практики: {pe}")

    await db.commit()
    await db.refresh(record)

    resp_g, resp_t = enrich_graph_with_user_learning_state(record.graph_data, record.tree_data, user_cards)
    return KnowledgeGraphResponse(
        subject=clean_sub,
        graph_data=resp_g,
        tree_data=resp_t,
        updated_at=record.updated_at.isoformat() if record.updated_at else now.isoformat(),
        is_seed=False
    )

