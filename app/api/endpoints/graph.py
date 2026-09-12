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
from sqlalchemy import select

from app.database.session import get_db
from app.database.models import TopicKnowledgeGraph, Card
from app.core.auth import get_current_user_id
from app.services.graph_service import (
    clean_graph_data,
    build_hierarchical_tree,
    get_preset_seed_graph,
    resolve_subject_alias,
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
    Supports alias resolution, preset seed fallbacks, and on-demand synthesis from existing cards.
    """
    alias_subject = resolve_subject_alias(subject)

    # 1. Поиск в БД строго для текущего пользователя (изоляция пользователей)
    stmt = select(TopicKnowledgeGraph).where(
        TopicKnowledgeGraph.user_id == current_user,
        TopicKnowledgeGraph.subject.in_([subject, alias_subject])
    )
    result = await db.execute(stmt)
    record = result.scalars().first()

    if record:
        g_data = record.graph_data or {"nodes": [], "edges": []}
        r_nodes = g_data.get("nodes", [])
        r_edges = g_data.get("edges", [])
        if r_nodes:
            from app.services.graph_service import ensure_connected_spiderweb
            r_nodes, r_edges = ensure_connected_spiderweb(r_nodes, r_edges, fallback_title=subject)
            g_data = {"nodes": r_nodes, "edges": r_edges}

        return KnowledgeGraphResponse(
            subject=record.subject,
            graph_data=g_data,
            tree_data=record.tree_data,
            updated_at=record.updated_at.isoformat() if record.updated_at else None,
            is_seed=False
        )

    # 2. Проверка пресетного сид-графа (с авто-разрешением алиасов sudoustr -> sudoustroystvo)
    seed = get_preset_seed_graph(subject)
    if seed:
        return KnowledgeGraphResponse(
            subject=subject,
            graph_data=seed["graph_data"],
            tree_data=seed.get("tree_data"),
            updated_at=None,
            is_seed=True
        )

    # 3. Динамический синтез графа и дерева из карточек текущего пользователя
    card_stmt = select(Card).where(
        Card.user_id == current_user,
        Card.subject.in_([subject, alias_subject])
    )
    card_res = await db.execute(card_stmt)
    user_cards = card_res.scalars().all()
    if user_cards:
        syn = synthesize_graph_from_cards(user_cards, fallback_title=subject)
        if syn and syn.get("graph_data", {}).get("nodes"):
            new_kg = TopicKnowledgeGraph(
                user_id=current_user,
                subject=subject,
                graph_data=syn["graph_data"],
                tree_data=syn["tree_data"],
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.add(new_kg)
            try:
                await db.commit()
            except Exception:
                await db.rollback()

            return KnowledgeGraphResponse(
                subject=subject,
                graph_data=syn["graph_data"],
                tree_data=syn["tree_data"],
                updated_at=datetime.utcnow().isoformat(),
                is_seed=False
            )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Knowledge graph for subject '{subject}' not found."
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
    """Deletes custom knowledge graph for the given subject."""
    stmt = select(TopicKnowledgeGraph).where(
        TopicKnowledgeGraph.user_id == current_user,
        TopicKnowledgeGraph.subject == subject
    )
    result = await db.execute(stmt)
    record = result.scalars().first()
    if record:
        await db.delete(record)
        await db.commit()
        return {"status": "deleted", "subject": subject}
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Knowledge graph for subject '{subject}' not found."
    )
