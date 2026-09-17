# app/api/endpoints/graph.py
"""
FastAPI Router for Knowledge Graph & Mindmap Persistence.
Supports O(1) retrieval, upsert, tree synthesis, and preset seed fallbacks (R1, R2).
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload

from app.database.session import get_db
from app.database.models import TopicKnowledgeGraph, Card
from app.core.auth import get_current_user_id
from app.services.graph_service import (
    clean_graph_data,
    build_hierarchical_tree,
    get_preset_seed_graph,
    resolve_subject_alias,
    get_all_subject_aliases,
    synthesize_graph_from_cards,
)

router = APIRouter()


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


# --- ENDPOINTS ---

@router.get("/knowledge-graph", response_model=KnowledgeGraphResponse)
async def get_knowledge_graph(
    subject: str = Query(..., min_length=1, max_length=128, description="Subject/deck identifier"),
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Retrieves the knowledge graph and tree mindmap for the current user and subject.
    Supports unified alias resolution, conflict elimination, automatic synchronization of stale
    snapshots from current user deck, and preset seed fallbacks.
    """
    clean_sub = subject.strip()
    if clean_sub.lower() in ("all", "*", "", "generic"):
        top_stmt = select(Card.subject).where(
            Card.user_id == current_user
        ).group_by(Card.subject).order_by(func.count(Card.id).desc()).limit(1)
        top_sub = (await db.execute(top_stmt)).scalar()
        if not top_sub:
            def_stmt = select(Card.subject).where(
                Card.user_id.in_(["default_user", "dev_user"])
            ).group_by(Card.subject).order_by(func.count(Card.id).desc()).limit(1)
            top_sub = (await db.execute(def_stmt)).scalar()
        clean_sub = top_sub if top_sub else "sudoustr"

    all_aliases = get_all_subject_aliases(clean_sub)
    if clean_sub not in all_aliases:
        all_aliases.insert(0, clean_sub)
    now = datetime.utcnow()

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

    # Fallback на пресетный сид-граф при отсутствии персональных карточек и графа
    if not records and not user_cards:
        seed = get_preset_seed_graph(clean_sub)
        if seed:
            return KnowledgeGraphResponse(
                subject=clean_sub,
                graph_data=seed["graph_data"],
                tree_data=seed.get("tree_data"),
                updated_at=None,
                is_seed=True
            )
        # Для несеменированных предметов у реальных Telegram-пользователей проверяем системный демо-профиль
        if current_user.isdigit():
            card_def_stmt = select(Card).options(selectinload(Card.phrase)).where(
                Card.user_id.in_(["default_user", "dev_user"]),
                Card.subject.in_(all_aliases)
            )
            user_cards = (await db.execute(card_def_stmt)).scalars().all()

            stmt_def = select(TopicKnowledgeGraph).where(
                TopicKnowledgeGraph.user_id.in_(["default_user", "dev_user"]),
                TopicKnowledgeGraph.subject.in_(all_aliases)
            ).order_by(TopicKnowledgeGraph.updated_at.desc())
            records = (await db.execute(stmt_def)).scalars().all()

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
            elif len(user_cards) >= 100 and node_count < 40 and clean_sub.lower() not in ("sudoustr", "sudoustroystvo"):
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

        return KnowledgeGraphResponse(
            subject=clean_sub,
            graph_data=record.graph_data,
            tree_data=record.tree_data,
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

        return KnowledgeGraphResponse(
            subject=clean_sub,
            graph_data=g_data,
            tree_data=record.tree_data,
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

            return KnowledgeGraphResponse(
                subject=clean_sub,
                graph_data=syn["graph_data"],
                tree_data=syn["tree_data"],
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
        record.updated_at = datetime.utcnow()
    else:
        record = TopicKnowledgeGraph(
            user_id=current_user,
            subject=payload.subject,
            graph_data=final_graph_data,
            tree_data=tree_data,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
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
            record.updated_at = datetime.utcnow()
            await db.commit()
        else:
            raise commit_err

    await db.refresh(record)

    return KnowledgeGraphResponse(
        subject=record.subject,
        graph_data=record.graph_data,
        tree_data=record.tree_data,
        updated_at=record.updated_at.isoformat() if record.updated_at else datetime.utcnow().isoformat(),
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


@router.post("/knowledge-graph/rebuild", response_model=KnowledgeGraphResponse)
async def rebuild_knowledge_graph(
    subject: str = Query(..., min_length=1, max_length=128),
    current_user: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db)
):
    """Принудительно перестраивает граф знаний и дерево напрямую из актуальных карточек пользователя в БД."""
    clean_sub = subject.strip()
    if clean_sub.lower() in ("all", "*", "", "generic"):
        top_stmt = select(Card.subject).where(
            Card.user_id == current_user
        ).group_by(Card.subject).order_by(func.count(Card.id).desc()).limit(1)
        top_sub = (await db.execute(top_stmt)).scalar()
        if not top_sub:
            def_stmt = select(Card.subject).where(
                Card.user_id.in_(["default_user", "dev_user"])
            ).group_by(Card.subject).order_by(func.count(Card.id).desc()).limit(1)
            top_sub = (await db.execute(def_stmt)).scalar()
        clean_sub = top_sub if top_sub else "sudoustr"

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

    now = datetime.utcnow()
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

    return KnowledgeGraphResponse(
        subject=clean_sub,
        graph_data=record.graph_data,
        tree_data=record.tree_data,
        updated_at=record.updated_at.isoformat() if record.updated_at else now.isoformat(),
        is_seed=False
    )

