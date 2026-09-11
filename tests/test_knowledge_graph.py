# tests/test_knowledge_graph.py
"""
Unit and Integration Tests for Milestone 1: Knowledge Graph Backend & Database Persistence (R2).
Covers:
1. TopicKnowledgeGraph model definition, schema constraints, and database persistence.
2. Preset seed graph generation for 'sudoustroystvo' (15 nodes, 14 edges, 5 categories, 4 relations, 3-level tree).
3. Multi-chunk knowledge graph consolidation (deduplication, attribute merging, dangling edge removal, tree synthesis).
4. FastAPI endpoints: GET & POST /api/knowledge-graph with seed fallback, upsert, tree synthesis, dangling edge cleaning, and user isolation.
"""

import unittest
import asyncio
from datetime import datetime
from fastapi.testclient import TestClient
from sqlalchemy import select, delete, inspect

from main import app
from app.database.session import AsyncSessionLocal, engine
from app.database.models import TopicKnowledgeGraph, Base
from app.services.graph_service import (
    generate_sudoustroystvo_seed_graph,
    get_preset_seed_graph,
    consolidate_knowledge_graphs,
    build_hierarchical_tree,
    clean_graph_data,
    VALID_CATEGORIES,
    VALID_RELATIONS
)


class TestKnowledgeGraph(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_cm = TestClient(app)
        cls.client = cls.client_cm.__enter__()
        cls.user_a = "test_graph_user_a"
        cls.user_b = "test_graph_user_b"

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)

    def run_async(self, coro):
        return asyncio.run(coro)

    def setUp(self):
        # Clean up test rows for test users
        async def cleanup():
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(TopicKnowledgeGraph).filter(
                        TopicKnowledgeGraph.user_id.in_([self.user_a, self.user_b, "default_user"])
                    )
                )
                await db.commit()
        self.run_async(cleanup())

    def tearDown(self):
        async def cleanup():
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(TopicKnowledgeGraph).filter(
                        TopicKnowledgeGraph.user_id.in_([self.user_a, self.user_b, "default_user"])
                    )
                )
                await db.commit()
        self.run_async(cleanup())

    # =========================================================================
    # 1. MODEL DEFINITION & DATABASE PERSISTENCE TESTS
    # =========================================================================

    def test_01_model_definition_and_table_structure(self):
        """Verifies TopicKnowledgeGraph model attributes, column types, and constraints."""
        self.assertEqual(TopicKnowledgeGraph.__tablename__, "topic_knowledge_graphs")

        mapper = inspect(TopicKnowledgeGraph)
        column_names = {c.key for c in mapper.columns}
        expected_columns = {"id", "user_id", "subject", "graph_data", "tree_data", "created_at", "updated_at"}
        self.assertTrue(expected_columns.issubset(column_names), f"Missing columns: {expected_columns - column_names}")

        # Verify user_id is String and has NO ForeignKey (crucial architectural requirement)
        user_id_col = mapper.columns["user_id"]
        self.assertEqual(user_id_col.type.python_type, str)
        self.assertEqual(len(user_id_col.foreign_keys), 0, "user_id must not have a ForeignKey to users table")

        # Verify table constraints: UniqueConstraint("user_id", "subject")
        table = TopicKnowledgeGraph.__table__
        unique_constraints = [c for c in table.constraints if hasattr(c, "columns")]
        uq_col_sets = [{col.name for col in c.columns} for c in unique_constraints]
        self.assertIn({"user_id", "subject"}, uq_col_sets, "Table must have UniqueConstraint on (user_id, subject)")

    def test_02_model_persistence_and_to_dict(self):
        """Verifies direct DB insertion, retrieval, to_dict serialization, and update."""
        async def db_ops():
            async with AsyncSessionLocal() as db:
                graph = TopicKnowledgeGraph(
                    user_id=self.user_a,
                    subject="test_db_persistence",
                    graph_data={"nodes": [{"id": "n1", "name": "Node 1"}], "edges": []},
                    tree_data={"id": "root", "name": "Root", "children": []}
                )
                db.add(graph)
                await db.commit()
                await db.refresh(graph)

                graph_id = graph.id
                d = graph.to_dict()
                self.assertEqual(d["id"], graph_id)
                self.assertEqual(d["user_id"], self.user_a)
                self.assertEqual(d["subject"], "test_db_persistence")
                self.assertIsNotNone(d["created_at"])
                self.assertEqual(len(d["graph_data"]["nodes"]), 1)

                # Query back
                stmt = select(TopicKnowledgeGraph).where(TopicKnowledgeGraph.id == graph_id)
                res = await db.execute(stmt)
                loaded = res.scalars().first()
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded.subject, "test_db_persistence")

                # Update
                loaded.graph_data = {"nodes": [{"id": "n1", "name": "Node 1 Updated"}], "edges": []}
                await db.commit()
                await db.refresh(loaded)
                self.assertEqual(loaded.graph_data["nodes"][0]["name"], "Node 1 Updated")

        self.run_async(db_ops())

    # =========================================================================
    # 2. SEED GRAPH GENERATOR TESTS
    # =========================================================================

    def test_03_sudoustroystvo_seed_graph_structure(self):
        """Verifies generate_sudoustroystvo_seed_graph() satisfies all R2 requirements:
        - Exactly 15 nodes across all 5 categories
        - Exactly 14 edges across all 4 relations
        - Valid 3-level tree mindmap
        - No dangling edges or self-loops
        """
        seed = generate_sudoustroystvo_seed_graph()
        self.assertEqual(seed["subject"], "sudoustroystvo")

        graph_data = seed["graph_data"]
        nodes = graph_data["nodes"]
        edges = graph_data["edges"]

        # 1. Exactly 15 nodes
        self.assertEqual(len(nodes), 15, f"Expected exactly 15 nodes, got {len(nodes)}")

        # 2. All 5 categories present
        node_categories = {n["category"] for n in nodes}
        self.assertEqual(node_categories, VALID_CATEGORIES, f"All 5 categories must be present: {VALID_CATEGORIES}")

        # 3. Exactly 14 edges
        self.assertEqual(len(edges), 14, f"Expected exactly 14 edges, got {len(edges)}")

        # 4. All 4 relations present
        edge_relations = {e["relation"] for e in edges}
        self.assertEqual(edge_relations, VALID_RELATIONS, f"All 4 relations must be present: {VALID_RELATIONS}")

        # 5. Integrity: No dangling edges, no self-loops
        node_ids = {n["id"] for n in nodes}
        for edge in edges:
            self.assertIn(edge["source"], node_ids, f"Dangling source: {edge['source']}")
            self.assertIn(edge["target"], node_ids, f"Dangling target: {edge['target']}")
            self.assertNotEqual(edge["source"], edge["target"], f"Self loop on {edge['source']}")

        # 6. Tree Mindmap structure
        tree = seed["tree_data"]
        self.assertIsNotNone(tree)
        self.assertEqual(tree["id"], "court_system_rf")
        self.assertEqual(tree["level"], 0)
        self.assertTrue(len(tree["children"]) > 0, "Root must have children")

        # Check max depth >= 2 (at least 3 levels: 0, 1, 2)
        def get_max_depth(node):
            if not node.get("children"):
                return 1
            return 1 + max(get_max_depth(c) for c in node["children"])

        tree_depth = get_max_depth(tree)
        self.assertGreaterEqual(tree_depth, 3, f"Tree mindmap must have at least 3 levels, got {tree_depth}")

    def test_04_preset_seed_graph_lookup(self):
        """Verifies get_preset_seed_graph resolves known preset aliases and rejects unknown slugs."""
        seed_std = get_preset_seed_graph("sudoustroystvo")
        self.assertIsNotNone(seed_std)
        self.assertEqual(len(seed_std["graph_data"]["nodes"]), 15)

        seed_alias1 = get_preset_seed_graph("court_system")
        self.assertIsNotNone(seed_alias1)

        seed_alias2 = get_preset_seed_graph("судоустройство")
        self.assertIsNotNone(seed_alias2)

        unknown = get_preset_seed_graph("quantum_mechanics_deck")
        self.assertIsNone(unknown)

    # =========================================================================
    # 3. KNOWLEDGE GRAPH CONSOLIDATION & CLEANING TESTS
    # =========================================================================

    def test_05_clean_graph_data(self):
        """Verifies clean_graph_data purges dangling edges, self loops, and invalid categories."""
        raw_nodes = [
            {"id": "n1", "name": "Node 1", "category": "authority", "summary": "Sum 1."},
            {"id": "n2", "name": "Node 2", "category": "invalid_cat_xyz", "summary": "Sum 2."},
        ]
        raw_edges = [
            {"source": "n1", "target": "n2", "relation": "appealed_to"},
            {"source": "n1", "target": "ghost_node", "relation": "appealed_to"},  # dangling target
            {"source": "missing_src", "target": "n2", "relation": "demarcated_from"},  # dangling source
            {"source": "n1", "target": "n1", "relation": "appealed_to"},  # self-loop
            {"source": "n1", "target": "n2", "relation": "обжалуется"},  # duplicate relation synonym
        ]
        clean_nodes, clean_edges = clean_graph_data(raw_nodes, raw_edges)

        self.assertEqual(len(clean_nodes), 2)
        self.assertEqual(clean_nodes[1]["category"], "authority")  # fallback to authority

        # Only 1 edge should remain (n1 -> n2 with relation 'appealed_to')
        self.assertEqual(len(clean_edges), 1)
        self.assertEqual(clean_edges[0]["source"], "n1")
        self.assertEqual(clean_edges[0]["target"], "n2")
        self.assertEqual(clean_edges[0]["relation"], "appealed_to")

    def test_06_multi_chunk_consolidation(self):
        """Verifies consolidate_knowledge_graphs merges chunks, deduplicates entities, and creates tree."""
        chunk1 = {
            "nodes": [
                {
                    "id": "district_court",
                    "name": "Районный суд",
                    "category": "authority",
                    "summary": "Основное звено судов общей юрисдикции.",
                    "level": 2
                },
                {
                    "id": "magistrate_court",
                    "name": "Мировой судья",
                    "category": "instance",
                    "summary": "Низшее звено правосудия.",
                    "level": 2
                }
            ],
            "edges": [
                {"source": "magistrate_court", "target": "district_court", "relation": "appealed_to"}
            ]
        }

        chunk2 = {
            "nodes": [
                # Overlapping entity with plural name and more detailed summary
                {
                    "id": "district_court_alias",
                    "name": "Районные суды",
                    "category": "instance",  # More specific category than authority
                    "summary": "Основное звено судов общей юрисдикции, рассматривающее большинство дел по первой инстанции.",
                    "level": 1  # Higher in hierarchy
                },
                {
                    "id": "regional_court",
                    "name": "Областной суд",
                    "category": "instance",
                    "summary": "Суд субъекта РФ.",
                    "level": 1
                }
            ],
            "edges": [
                {"source": "district_court_alias", "target": "regional_court", "relation": "обжалуется_в"},
                {"source": "district_court_alias", "target": "non_existent_node", "relation": "appealed_to"}  # Dangling edge
            ]
        }

        consolidated = consolidate_knowledge_graphs([chunk1, chunk2], fallback_title="Судопроизводство")

        # 3 unique nodes should result
        nodes = consolidated["nodes"]
        self.assertEqual(len(nodes), 3, f"Expected 3 consolidated nodes, got {len(nodes)}")

        node_map = {n["name"]: n for n in nodes}
        # Verify attribute merging for district court
        # Either found under 'Районный суд' or 'Районные суды'
        dc_node = next(n for n in nodes if "районн" in n["name"].lower())
        self.assertEqual(dc_node["category"], "instance", "Should adopt more specific category 'instance'")
        self.assertIn("большинство дел", dc_node["summary"], "Should adopt longer, more complete summary")
        self.assertEqual(dc_node["level"], 1, "Should adopt higher hierarchy level 1")

        # Dangling edge to non_existent_node must be purged
        edges = consolidated["edges"]
        self.assertEqual(len(edges), 2, f"Expected 2 valid edges, got {len(edges)}")
        for e in edges:
            self.assertNotEqual(e["target"], "non_existent_node")

        # Tree mindmap must be generated
        tree = consolidated["tree_data"]
        self.assertIsNotNone(tree)
        self.assertIn("children", tree)

    # =========================================================================
    # 4. FASTAPI ENDPOINT INTEGRATION TESTS
    # =========================================================================

    def test_07_api_get_preset_seed_fallback(self):
        """GET /api/knowledge-graph?subject=sudoustroystvo returns seed graph with is_seed=True."""
        res = self.client.get(
            "/api/knowledge-graph?subject=sudoustroystvo",
            headers={"X-User-Id": self.user_a}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["subject"], "sudoustroystvo")
        self.assertTrue(data["is_seed"])
        self.assertEqual(len(data["graph_data"]["nodes"]), 15)
        self.assertEqual(len(data["graph_data"]["edges"]), 14)
        self.assertIsNotNone(data["tree_data"])

    def test_08_api_get_unknown_subject_404(self):
        """GET /api/knowledge-graph for an unknown subject with no seed returns 404."""
        res = self.client.get(
            "/api/knowledge-graph?subject=unknown_nonexistent_deck_xyz",
            headers={"X-User-Id": self.user_a}
        )
        self.assertEqual(res.status_code, 404)

    def test_09_api_post_upsert_and_dangling_edge_clean(self):
        """POST /api/knowledge-graph creates record, cleans dangling edges, and auto-generates tree."""
        payload = {
            "subject": "custom_law_deck",
            "graph_data": {
                "nodes": [
                    {"id": "node_a", "name": "Суд А", "category": "authority", "summary": "Описание А."},
                    {"id": "node_b", "name": "Суд Б", "category": "instance", "summary": "Описание Б.", "parent_id": "node_a"},
                ],
                "edges": [
                    {"source": "node_b", "target": "node_a", "relation": "appealed_to"},
                    {"source": "node_b", "target": "ghost_target", "relation": "appealed_to"}  # Dangling!
                ]
            },
            "tree_data": None  # Omitted: backend must synthesize it
        }

        res = self.client.post(
            "/api/knowledge-graph",
            headers={"X-User-Id": self.user_a},
            json=payload
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["subject"], "custom_law_deck")
        self.assertFalse(data["is_seed"])

        # Dangling edge must be removed
        returned_edges = data["graph_data"]["edges"]
        self.assertEqual(len(returned_edges), 1)
        self.assertEqual(returned_edges[0]["source"], "node_b")
        self.assertEqual(returned_edges[0]["target"], "node_a")

        # Tree data must be automatically synthesized
        tree = data["tree_data"]
        self.assertIsNotNone(tree)
        self.assertTrue(tree["id"] in ("node_a", "deck_root"))

        # Subsequent GET retrieves the persisted custom graph with is_seed=False
        get_res = self.client.get(
            "/api/knowledge-graph?subject=custom_law_deck",
            headers={"X-User-Id": self.user_a}
        )
        self.assertEqual(get_res.status_code, 200)
        get_data = get_res.json()
        self.assertFalse(get_data["is_seed"])
        self.assertEqual(len(get_data["graph_data"]["nodes"]), 2)
        self.assertEqual(len(get_data["graph_data"]["edges"]), 1)

    def test_10_api_post_upsert_updates_existing_record(self):
        """POST /api/knowledge-graph on existing subject updates the existing row (no duplicates)."""
        # 1. Initial insert
        initial_payload = {
            "subject": "upsert_deck",
            "graph_data": {
                "nodes": [{"id": "n1", "name": "V1", "category": "authority", "summary": "Version 1."}],
                "edges": []
            }
        }
        res1 = self.client.post("/api/knowledge-graph", headers={"X-User-Id": self.user_a}, json=initial_payload)
        self.assertEqual(res1.status_code, 200)

        # 2. Update insert
        update_payload = {
            "subject": "upsert_deck",
            "graph_data": {
                "nodes": [
                    {"id": "n1", "name": "V2", "category": "authority", "summary": "Version 2."},
                    {"id": "n2", "name": "V2_New", "category": "instance", "summary": "New Node."}
                ],
                "edges": [{"source": "n2", "target": "n1", "relation": "appealed_to"}]
            }
        }
        res2 = self.client.post("/api/knowledge-graph", headers={"X-User-Id": self.user_a}, json=update_payload)
        self.assertEqual(res2.status_code, 200)
        updated_data = res2.json()
        self.assertEqual(len(updated_data["graph_data"]["nodes"]), 2)
        self.assertEqual(updated_data["graph_data"]["nodes"][0]["name"], "V2")

        # 3. Verify exactly 1 DB row exists for (user_a, 'upsert_deck')
        async def count_rows():
            async with AsyncSessionLocal() as db:
                stmt = select(TopicKnowledgeGraph).where(
                    TopicKnowledgeGraph.user_id == self.user_a,
                    TopicKnowledgeGraph.subject == "upsert_deck"
                )
                res = await db.execute(stmt)
                records = res.scalars().all()
                self.assertEqual(len(records), 1, "Must update existing row, not create duplicates")

        self.run_async(count_rows())

    def test_11_api_user_isolation(self):
        """Knowledge graphs are strictly isolated per user."""
        # User A saves custom graph
        payload = {
            "subject": "isolated_deck",
            "graph_data": {
                "nodes": [{"id": "priv1", "name": "Private Node", "category": "authority", "summary": "Secret."}],
                "edges": []
            }
        }
        res_a = self.client.post("/api/knowledge-graph", headers={"X-User-Id": self.user_a}, json=payload)
        self.assertEqual(res_a.status_code, 200)

        # User B requests the same subject -> must receive 404 (not User A's graph)
        res_b = self.client.get("/api/knowledge-graph?subject=isolated_deck", headers={"X-User-Id": self.user_b})
        self.assertEqual(res_b.status_code, 404)

    def test_12_api_canvas_runtime_attributes_tolerance(self):
        """POST /api/knowledge-graph accepts Canvas force-graph physics properties without 422 errors."""
        payload = {
            "subject": "canvas_tolerant_deck",
            "graph_data": {
                "nodes": [
                    {
                        "id": "cn1",
                        "name": "Canvas Node 1",
                        "category": "authority",
                        "summary": "Canvas test.",
                        # Runtime canvas physics attributes added by force-graph:
                        "x": 124.5,
                        "y": -52.3,
                        "vx": 0.002,
                        "vy": -0.001,
                        "index": 0,
                        "__bckgDimensions": [100, 20]
                    }
                ],
                "edges": []
            }
        }
        res = self.client.post("/api/knowledge-graph", headers={"X-User-Id": self.user_a}, json=payload)
        self.assertEqual(res.status_code, 200, f"Expected 200, got {res.status_code}: {res.text}")
        data = res.json()
        self.assertEqual(data["graph_data"]["nodes"][0]["id"], "cn1")

    def test_13_api_delete_custom_graph(self):
        """DELETE /api/knowledge-graph removes custom graph; subsequent GET falls back to seed or 404."""
        payload = {
            "subject": "delete_test_deck",
            "graph_data": {
                "nodes": [{"id": "del_n", "name": "To Delete", "category": "authority", "summary": "Delete me."}],
                "edges": []
            }
        }
        post_res = self.client.post("/api/knowledge-graph", headers={"X-User-Id": self.user_a}, json=payload)
        self.assertEqual(post_res.status_code, 200)

        del_res = self.client.delete("/api/knowledge-graph?subject=delete_test_deck", headers={"X-User-Id": self.user_a})
        self.assertEqual(del_res.status_code, 200)
        self.assertEqual(del_res.json()["status"], "deleted")

        get_res = self.client.get("/api/knowledge-graph?subject=delete_test_deck", headers={"X-User-Id": self.user_a})
        self.assertEqual(get_res.status_code, 404)


if __name__ == "__main__":
    unittest.main()
