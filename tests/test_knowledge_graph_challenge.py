# tests/test_knowledge_graph_challenge.py
"""
Empirical Challenge & Stress Test Suite for Milestone 1: Knowledge Graph Backend & Database Persistence (R2).
Challenger: teamwork_preview_challenger_m1_1

Vectors tested:
1. Cyclic graphs (2-node reciprocal, multi-node loops, self-loops, and 100 random directed cycles).
2. Deeply nested trees (100+ levels) - verification of recursion limits and serialization behavior.
3. Disjoint graphs (multiple isolated subgraphs and components).
4. Conflicting and duplicate edges (opposing relations, redundant duplicates).
5. Special characters, emojis, unicode, HTML/XSS, and SQL injection strings.
6. Malformed and invalid payloads to POST /api/knowledge-graph (schema fuzzing).
7. Canvas force-graph runtime attributes tolerance and large scale payload handling.
8. Concurrent requests and user isolation data integrity.
9. Multi-chunk consolidation edge cases (empty, non-dict, minified, fuzzy aliases).
"""

import unittest
import asyncio
import json
import random
from fastapi.testclient import TestClient
from sqlalchemy import select, delete

from main import app
from app.database.session import AsyncSessionLocal
from app.database.models import TopicKnowledgeGraph
from app.services.graph_service import (
    build_hierarchical_tree,
    clean_graph_data,
    consolidate_knowledge_graphs,
    generate_sudoustroystvo_seed_graph,
    get_preset_seed_graph,
    normalize_entity_name,
    normalize_id,
    normalize_relation
)


class TestKnowledgeGraphEmpiricalChallenge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_cm = TestClient(app)
        cls.client = cls.client_cm.__enter__()
        cls.user_c1 = "challenger_1_user_a"
        cls.user_c2 = "challenger_1_user_b"

    @classmethod
    def tearDownClass(cls):
        cls.client_cm.__exit__(None, None, None)

    def run_async(self, coro):
        return asyncio.run(coro)

    def setUp(self):
        async def cleanup():
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(TopicKnowledgeGraph).filter(
                        TopicKnowledgeGraph.user_id.in_([self.user_c1, self.user_c2, "concurrent_user", "emoji_user", "test_deep_user", "race_user"])
                    )
                )
                await db.commit()
        self.run_async(cleanup())

    def tearDown(self):
        async def cleanup():
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(TopicKnowledgeGraph).filter(
                        TopicKnowledgeGraph.user_id.in_([self.user_c1, self.user_c2, "concurrent_user", "emoji_user", "test_deep_user", "race_user"])
                    )
                )
                await db.commit()
        self.run_async(cleanup())

    # =========================================================================
    # 1. CYCLIC GRAPHS
    # =========================================================================

    def test_challenge_01_cyclic_graphs_handling(self):
        """Tests 2-node reciprocal cycles, 3-node loops, self-loops, and random cycles.
        Verifies that clean_graph_data purges self-loops and build_hierarchical_tree breaks all cycles
        without raising CircularReferenceError, RecursionError, or infinite loops.
        """
        # 1. Self-loop: A -> A
        nodes_self = [{"id": "node_a", "name": "Node A", "parent_id": "node_a"}]
        edges_self = [{"source": "node_a", "target": "node_a", "relation": "appealed_to"}]
        cn, ce = clean_graph_data(nodes_self, edges_self)
        self.assertEqual(len(ce), 0, "Self-loop edge must be purged")
        self.assertIsNone(cn[0]["parent_id"], "Self-referencing parent_id must be cleared")

        # 2. Reciprocal 2-node cycle: A -> B and B -> A
        nodes_2 = [
            {"id": "node_a", "name": "Node A", "parent_id": "node_b"},
            {"id": "node_b", "name": "Node B", "parent_id": "node_a"}
        ]
        edges_2 = [
            {"source": "node_a", "target": "node_b", "relation": "appealed_to"},
            {"source": "node_b", "target": "node_a", "relation": "appealed_to"}
        ]
        tree_2 = build_hierarchical_tree(nodes_2, edges_2, "Reciprocal Tree")
        # json.dumps will fail if there is any circular pointer reference
        json_str_2 = json.dumps(tree_2)
        self.assertIsNotNone(json_str_2)

        # 3. 5-node cycle: n0 -> n1 -> n2 -> n3 -> n4 -> n0
        nodes_5 = [{"id": f"n{i}", "name": f"Node {i}", "parent_id": f"n{(i+1)%5}"} for i in range(5)]
        edges_5 = [{"source": f"n{i}", "target": f"n{(i+1)%5}", "relation": "appealed_to"} for i in range(5)]
        tree_5 = build_hierarchical_tree(nodes_5, edges_5, "5-Node Loop")
        json_str_5 = json.dumps(tree_5)
        self.assertIsNotNone(json_str_5)

        # 4. Fuzz with 100 random directed cyclic graphs
        rnd = random.Random(42)
        for trial in range(100):
            n_count = rnd.randint(3, 25)
            fuzz_nodes = [{"id": f"fn_{i}", "name": f"Fuzz {i}", "parent_id": f"fn_{rnd.randint(0, n_count-1)}"} for i in range(n_count)]
            fuzz_edges = [{"source": f"fn_{rnd.randint(0, n_count-1)}", "target": f"fn_{rnd.randint(0, n_count-1)}", "relation": "appealed_to"} for _ in range(n_count * 2)]
            clean_fn, clean_fe = clean_graph_data(fuzz_nodes, fuzz_edges)
            fuzz_tree = build_hierarchical_tree(clean_fn, clean_fe, f"Fuzz Tree {trial}")
            # Guarantee valid serialization without circular recursion
            json.dumps(fuzz_tree)

    # =========================================================================
    # 2. DEEPLY NESTED TREES (100+ LEVELS) — EMPIRICAL VULNERABILITY CHECK
    # =========================================================================

    def test_challenge_02_deeply_nested_trees_limit(self):
        """Stress-tests deeply nested trees (100+ levels).
        Discovers whether FastAPI/Pydantic serialization handles trees with 100+ levels.
        """
        # Depth 100 as specified in requirement: 'deeply nested trees (100+ levels)'
        depth = 100
        nodes_deep = [
            {"id": f"deep_{i}", "name": f"Deep Node {i}", "parent_id": f"deep_{i-1}" if i > 0 else None, "level": i}
            for i in range(depth)
        ]
        edges_deep = [
            {"source": f"deep_{i}", "target": f"deep_{i-1}", "relation": "appealed_to"}
            for i in range(1, depth)
        ]

        # 1. Service layer building tree directly
        tree = build_hierarchical_tree(nodes_deep, edges_deep, "Deep 100 Tree")
        json_serialized = json.dumps(tree)
        self.assertTrue(len(json_serialized) > 0)

        # 2. API Endpoint submission via POST /api/knowledge-graph
        payload = {
            "subject": "deep_tree_100",
            "graph_data": {
                "nodes": nodes_deep,
                "edges": edges_deep
            }
        }
        # Note: If Pydantic circular reference / depth limit is exceeded (>50 depth),
        # this will fail with ValueError: Circular reference detected (depth exceeded).
        try:
            res = self.client.post(
                "/api/knowledge-graph",
                headers={"X-User-Id": self.user_c1},
                json=payload
            )
            # If server succeeds, verify 200
            self.assertEqual(res.status_code, 200)
        except ValueError as e:
            # Document empirical failure on 100-level trees
            self.assertIn("Circular reference detected (depth exceeded)", str(e))
            # Re-raise or record finding
            raise AssertionError(f"VULNERABILITY CONFIRMED: Deeply nested tree (100 levels) crashed FastAPI with: {e}")

    # =========================================================================
    # 3. DISJOINT GRAPHS
    # =========================================================================

    def test_challenge_03_disjoint_graphs(self):
        """Tests handling of completely disjoint graphs with multiple isolated subgraphs."""
        # 5 separate components of 2 nodes each (10 nodes total, no edges between components)
        nodes = []
        edges = []
        for comp in range(5):
            n1 = f"c{comp}_parent"
            n2 = f"c{comp}_child"
            nodes.append({"id": n1, "name": f"Component {comp} Parent", "category": "authority", "level": 1})
            nodes.append({"id": n2, "name": f"Component {comp} Child", "category": "instance", "parent_id": n1, "level": 2})
            edges.append({"source": n2, "target": n1, "relation": "appealed_to"})

        # Build tree: all components must be attached under synthetic 'deck_root'
        tree = build_hierarchical_tree(nodes, edges, "Disjoint Subjects")
        self.assertEqual(tree["id"], "deck_root")
        self.assertEqual(len(tree["children"]), 5, "Root must contain all 5 disjoint components as top-level children")

        # Test POST and GET
        payload = {
            "subject": "disjoint_deck",
            "graph_data": {"nodes": nodes, "edges": edges}
        }
        res_post = self.client.post("/api/knowledge-graph", headers={"X-User-Id": self.user_c1}, json=payload)
        self.assertEqual(res_post.status_code, 200)
        self.assertEqual(len(res_post.json()["graph_data"]["edges"]), 5)

        res_get = self.client.get("/api/knowledge-graph?subject=disjoint_deck", headers={"X-User-Id": self.user_c1})
        self.assertEqual(res_get.status_code, 200)
        data = res_get.json()
        self.assertEqual(len(data["graph_data"]["nodes"]), 10)
        # ensure_connected_spiderweb safely connects all 5 disjoint components into a single reachable spiderweb (5 original + 4 connecting edges = 9)
        self.assertEqual(len(data["graph_data"]["edges"]), 9)
        self.assertEqual(len(data["tree_data"]["children"]), 5)

    # =========================================================================
    # 4. CONFLICTING & DUPLICATE EDGES
    # =========================================================================

    def test_challenge_04_conflicting_and_duplicate_edges(self):
        """Tests duplicate edges, inverted relations, and multiple conflicting relations between identical nodes."""
        nodes = [
            {"id": "court_x", "name": "Суд X", "category": "authority"},
            {"id": "court_y", "name": "Суд Y", "category": "instance"}
        ]
        edges = [
            {"source": "court_x", "target": "court_y", "relation": "appealed_to", "label": "Label 1"},
            {"source": "court_x", "target": "court_y", "relation": "appealed_to", "label": "Duplicate 1"},  # exact duplicate relation
            {"source": "court_x", "target": "court_y", "relation": "demarcated_from", "label": "Conflicting relation 1"},
            {"source": "court_x", "target": "court_y", "relation": "subject_to_jurisdiction", "label": "Conflicting relation 2"},
            {"source": "court_y", "target": "court_x", "relation": "appealed_to", "label": "Inverted relation"},
        ]

        cn, ce = clean_graph_data(nodes, edges)
        # Exact duplicate (court_x, court_y, appealed_to) must be removed
        # Distinct relations (demarcated_from, subject_to_jurisdiction) and inverted relation must be preserved
        self.assertEqual(len(ce), 4, f"Expected 4 unique (src, tgt, rel) edges, got {len(ce)}")

        # Tree synthesis must not crash
        tree = build_hierarchical_tree(cn, ce, "Conflict Deck")
        self.assertIsNotNone(tree)

        # POST upsert and verify persistence
        payload = {
            "subject": "conflict_deck",
            "graph_data": {"nodes": cn, "edges": ce}
        }
        res = self.client.post("/api/knowledge-graph", headers={"X-User-Id": self.user_c1}, json=payload)
        self.assertEqual(res.status_code, 200)

    # =========================================================================
    # 5. SPECIAL CHARACTERS, EMOJIS, XSS & SQL INJECTION
    # =========================================================================

    def test_challenge_05_special_characters_emojis_and_injections(self):
        """Tests node names, summaries, and subjects with emojis, HTML, quotes, and SQL injection strings."""
        nodes = [
            {
                "id": "node_emoji_1",
                "name": "🏛️ Конституционный Суд РФ ⚖️ & \"Высшая Инстанция\"",
                "category": "authority",
                "summary": "Резюме с кавычками \" ' ` и HTML <script>alert('xss')</script> и эмодзи 🚀🔥.",
                "level": 0
            },
            {
                "id": "node_sql_1",
                "name": "Robert'); DROP TABLE topic_knowledge_graphs; --",
                "category": "condition",
                "summary": "Проверка параметризованных запросов SQLAlchemy.",
                "parent_id": "node_emoji_1",
                "level": 1
            },
            {
                "id": "node_whitespace_1",
                "name": "  Многострочное  \n  название  \t  с пробелами  ",
                "category": "exception",
                "summary": "Summary\r\nwith\r\nnewlines and tabs\t\t.",
                "parent_id": "node_emoji_1",
                "level": 2
            }
        ]
        edges = [
            {"source": "node_sql_1", "target": "node_emoji_1", "relation": "subject_to_jurisdiction", "label": "подсудно & ⚖️"},
            {"source": "node_whitespace_1", "target": "node_emoji_1", "relation": "excludes_application"}
        ]

        subject_test = "⚖️_судебная_система_xss_<script>_deck"
        payload = {
            "subject": subject_test,
            "graph_data": {"nodes": nodes, "edges": edges}
        }

        # POST
        res_post = self.client.post("/api/knowledge-graph", headers={"X-User-Id": self.user_c1}, json=payload)
        self.assertEqual(res_post.status_code, 200)

        # GET
        res_get = self.client.get(f"/api/knowledge-graph?subject={subject_test}", headers={"X-User-Id": self.user_c1})
        self.assertEqual(res_get.status_code, 200)
        data = res_get.json()
        self.assertEqual(data["subject"], subject_test)
        self.assertEqual(len(data["graph_data"]["nodes"]), 3)
        self.assertEqual(data["graph_data"]["nodes"][0]["name"], "🏛️ Конституционный Суд РФ ⚖️ & \"Высшая Инстанция\"")

        # Verify DB table was NOT dropped by injection
        async def verify_table_alive():
            async with AsyncSessionLocal() as db:
                count_res = await db.execute(select(TopicKnowledgeGraph))
                self.assertIsNotNone(count_res.scalars().first())
        self.run_async(verify_table_alive())

    # =========================================================================
    # 6. MALFORMED PAYLOADS TO POST /api/knowledge-graph
    # =========================================================================

    def test_challenge_06_malformed_payloads_post_api(self):
        """Verifies endpoint returns 422 Unprocessable Entity (no 500 crashes) for all malformed payloads."""
        headers = {"X-User-Id": self.user_c1}

        # 1. Empty body
        r = self.client.post("/api/knowledge-graph", headers=headers, json={})
        self.assertEqual(r.status_code, 422)

        # 2. Missing subject
        r = self.client.post("/api/knowledge-graph", headers=headers, json={"graph_data": {"nodes": [], "edges": []}})
        self.assertEqual(r.status_code, 422)

        # 3. Empty subject string
        r = self.client.post("/api/knowledge-graph", headers=headers, json={"subject": "", "graph_data": {"nodes": [], "edges": []}})
        self.assertEqual(r.status_code, 422)

        # 4. Subject exceeding max_length=128
        r = self.client.post("/api/knowledge-graph", headers=headers, json={"subject": "a" * 150, "graph_data": {"nodes": [], "edges": []}})
        self.assertEqual(r.status_code, 422)

        # 5. Missing graph_data
        r = self.client.post("/api/knowledge-graph", headers=headers, json={"subject": "test_deck"})
        self.assertEqual(r.status_code, 422)

        # 6. graph_data is integer or string instead of object
        r = self.client.post("/api/knowledge-graph", headers=headers, json={"subject": "test_deck", "graph_data": 12345})
        self.assertEqual(r.status_code, 422)
        r = self.client.post("/api/knowledge-graph", headers=headers, json={"subject": "test_deck", "graph_data": "not_an_object"})
        self.assertEqual(r.status_code, 422)

        # 7. nodes is not a list
        r = self.client.post("/api/knowledge-graph", headers=headers, json={"subject": "test_deck", "graph_data": {"nodes": "invalid"}})
        self.assertEqual(r.status_code, 422)

        # 8. Node item missing 'id'
        r = self.client.post("/api/knowledge-graph", headers=headers, json={
            "subject": "test_deck",
            "graph_data": {"nodes": [{"name": "Missing ID"}], "edges": []}
        })
        self.assertEqual(r.status_code, 422)

        # 9. Node item missing 'name'
        r = self.client.post("/api/knowledge-graph", headers=headers, json={
            "subject": "test_deck",
            "graph_data": {"nodes": [{"id": "n1"}], "edges": []}
        })
        self.assertEqual(r.status_code, 422)

        # 10. Edge item missing 'source'
        r = self.client.post("/api/knowledge-graph", headers=headers, json={
            "subject": "test_deck",
            "graph_data": {"nodes": [{"id": "n1", "name": "N1"}], "edges": [{"target": "n1"}]}
        })
        self.assertEqual(r.status_code, 422)

        # 11. Edge item missing 'target'
        r = self.client.post("/api/knowledge-graph", headers=headers, json={
            "subject": "test_deck",
            "graph_data": {"nodes": [{"id": "n1", "name": "N1"}], "edges": [{"source": "n1"}]}
        })
        self.assertEqual(r.status_code, 422)

        # 12. tree_data is not an object (e.g. integer or string)
        r = self.client.post("/api/knowledge-graph", headers=headers, json={
            "subject": "test_deck",
            "graph_data": {"nodes": [], "edges": []},
            "tree_data": "string_tree"
        })
        self.assertEqual(r.status_code, 422)

    # =========================================================================
    # 7. CANVAS RUNTIME ATTRIBUTES & SCALE TOLERANCE
    # =========================================================================

    def test_challenge_07_canvas_attributes_and_scale(self):
        """Verifies graph tolerates client-side physics properties and processes a 200-node graph without lag."""
        # 200 nodes, 300 edges, with depth capped at 10 to avoid Pydantic depth bug
        nodes = []
        for i in range(200):
            p = f"node_{i % 10}" if i >= 10 else None
            nodes.append({
                "id": f"node_{i}",
                "name": f"Scale Node {i}",
                "category": "authority" if i < 10 else "instance",
                "summary": f"Summary for scale node {i}.",
                "parent_id": p,
                "level": 0 if i < 10 else 1,
                # Canvas 2D runtime force-graph properties
                "x": random.uniform(-500.0, 500.0),
                "y": random.uniform(-500.0, 500.0),
                "vx": random.uniform(-0.1, 0.1),
                "vy": random.uniform(-0.1, 0.1),
                "index": i,
                "__bckgDimensions": [120, 24],
                "color": "#00f0ff"
            })

        edges = []
        for i in range(10, 200):
            edges.append({
                "source": f"node_{i}",
                "target": f"node_{i % 10}",
                "relation": "appealed_to",
                "label": "обжалуется в"
            })

        payload = {
            "subject": "scale_canvas_deck",
            "graph_data": {"nodes": nodes, "edges": edges}
        }
        res = self.client.post("/api/knowledge-graph", headers={"X-User-Id": self.user_c1}, json=payload)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(len(data["graph_data"]["nodes"]), 200)
        self.assertEqual(len(data["graph_data"]["edges"]), 190)

    # =========================================================================
    # 8. CONCURRENCY & DATA INTEGRITY
    # =========================================================================

    def test_challenge_08_concurrency_and_user_isolation(self):
        """Tests concurrent upserts and strict cross-user data isolation."""
        import httpx

        async def run_concurrent():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                payload_c1 = {
                    "subject": "isolation_subject",
                    "graph_data": {"nodes": [{"id": "c1_node", "name": "User 1 Graph"}], "edges": []}
                }
                payload_c2 = {
                    "subject": "isolation_subject",
                    "graph_data": {"nodes": [{"id": "c2_node", "name": "User 2 Graph"}], "edges": []}
                }

                # Run both requests concurrently
                t1 = c.post("/api/knowledge-graph", headers={"X-User-Id": self.user_c1}, json=payload_c1)
                t2 = c.post("/api/knowledge-graph", headers={"X-User-Id": self.user_c2}, json=payload_c2)
                r1, r2 = await asyncio.gather(t1, t2)

                self.assertEqual(r1.status_code, 200)
                self.assertEqual(r2.status_code, 200)

                # Verify User 1 sees User 1's data
                get1 = await c.get("/api/knowledge-graph?subject=isolation_subject", headers={"X-User-Id": self.user_c1})
                self.assertEqual(get1.status_code, 200)
                self.assertEqual(get1.json()["graph_data"]["nodes"][0]["id"], "c1_node")

                # Verify User 2 sees User 2's data
                get2 = await c.get("/api/knowledge-graph?subject=isolation_subject", headers={"X-User-Id": self.user_c2})
                self.assertEqual(get2.status_code, 200)
                self.assertEqual(get2.json()["graph_data"]["nodes"][0]["id"], "c2_node")

                # Verify deleting User 1 does not affect User 2
                del1 = await c.delete("/api/knowledge-graph?subject=isolation_subject", headers={"X-User-Id": self.user_c1})
                self.assertEqual(del1.status_code, 200)

                get1_after = await c.get("/api/knowledge-graph?subject=isolation_subject", headers={"X-User-Id": self.user_c1})
                self.assertEqual(get1_after.status_code, 404)

                get2_after = await c.get("/api/knowledge-graph?subject=isolation_subject", headers={"X-User-Id": self.user_c2})
                self.assertEqual(get2_after.status_code, 200)
                self.assertEqual(get2_after.json()["graph_data"]["nodes"][0]["id"], "c2_node")

        self.run_async(run_concurrent())

    # =========================================================================
    # 9. MULTI-CHUNK CONSOLIDATION EDGE CASES
    # =========================================================================

    def test_challenge_09_multi_chunk_consolidation_edge_cases(self):
        """Tests consolidate_knowledge_graphs against degenerate and adversarial inputs."""
        # 1. Empty list
        res_empty = consolidate_knowledge_graphs([])
        self.assertEqual(len(res_empty["nodes"]), 0)
        self.assertEqual(res_empty["tree_data"]["id"], "root")

        # 2. Corrupt chunks (None, strings, empty dicts)
        corrupt_chunks = [None, "invalid_str", 42, {}, {"nodes": None, "edges": None}]
        res_corrupt = consolidate_knowledge_graphs(corrupt_chunks)
        self.assertEqual(len(res_corrupt["nodes"]), 0)

        # 3. Minified chunk keys ('n', 'e', 'c', 's', 'p', 'l')
        minified_chunk = {
            "n": [
                {"id": "min_1", "n": "Мировой суд", "c": "instance", "s": "Суд первой инстанции.", "l": 2},
                {"id": "min_2", "n": "Районный суд", "c": "authority", "s": "Вышестоящий суд.", "l": 1}
            ],
            "e": [
                {"s": "min_1", "t": "min_2", "r": "обжалуется"}
            ]
        }
        res_mini = consolidate_knowledge_graphs([minified_chunk])
        self.assertEqual(len(res_mini["nodes"]), 2)
        self.assertEqual(len(res_mini["edges"]), 1)
        self.assertEqual(res_mini["edges"][0]["relation"], "appealed_to")

        # 4. Russian morphology entity deduplication: "Арбитражный суд" vs "Арбитражные суды"
        chunk_a = {"nodes": [{"id": "arb_1", "name": "Арбитражный суд города Москвы", "category": "instance", "summary": "Кратко."}]}
        chunk_b = {"nodes": [{"id": "arb_2", "name": "Арбитражные суды городов Москвы", "category": "instance", "summary": "Очень подробное описание суда."}]}
        res_morph = consolidate_knowledge_graphs([chunk_a, chunk_b])
        self.assertEqual(len(res_morph["nodes"]), 1, "Morphologically equivalent Russian entities must be consolidated")
        self.assertIn("Очень подробное описание", res_morph["nodes"][0]["summary"])

    # =========================================================================
    # 10. CONCURRENT SAME-USER UPSERT RACE CONDITION
    # =========================================================================

    def test_challenge_10_concurrent_upsert_same_user_race(self):
        """Stress-tests concurrent POST requests for the same user and same subject.
        Discovers whether TOCTOU race condition between SELECT and INSERT raises IntegrityError (500).
        """
        import httpx

        async def run_race():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
                payload = {
                    "subject": "race_subject",
                    "graph_data": {"nodes": [{"id": "n1", "name": "Node 1"}], "edges": []}
                }
                tasks = [
                    c.post("/api/knowledge-graph", headers={"X-User-Id": "race_user"}, json=payload)
                    for _ in range(10)
                ]
                responses = await asyncio.gather(*tasks, return_exceptions=True)

                integrity_errors = []
                for idx, r in enumerate(responses):
                    if isinstance(r, Exception):
                        integrity_errors.append(f"Request {idx}: {type(r).__name__}: {r}")
                    elif r.status_code == 500:
                        integrity_errors.append(f"Request {idx}: HTTP 500: {r.text}")

                if integrity_errors:
                    raise AssertionError(
                        f"VULNERABILITY CONFIRMED: Concurrent upsert race condition triggered IntegrityError / 500: {integrity_errors}"
                    )

        self.run_async(run_race())


if __name__ == "__main__":
    unittest.main()
