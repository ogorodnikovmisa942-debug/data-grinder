# tests/test_m1_challenger2_harness.py
"""
Empirical Challenge & Stress Test Suite for Milestone 1 (Challenger 2).
Focus:
1. O(1) query latency (<5ms) and SQLite index plan verification.
2. Seed fallback matrix (known presets, alias resolution, unknown subjects, override & delete cycle).
3. Strict user isolation (cross-user read/write/delete collision prevention).
4. Canvas force-graph runtime attributes persistence and return behavior.
5. Cycle resilience and stress limits (500 nodes, 1000 edges, cycle graphs).
"""

import time
import unittest
import asyncio
from datetime import datetime
from fastapi.testclient import TestClient
from sqlalchemy import select, delete, text

from main import app
from app.database.session import AsyncSessionLocal, engine
from app.database.models import TopicKnowledgeGraph
from app.services.graph_service import (
    generate_sudoustroystvo_seed_graph,
    get_preset_seed_graph,
    build_hierarchical_tree,
    clean_graph_data,
)


class TestM1Challenger2Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_cm = TestClient(app)
        cls.client = cls.client_cm.__enter__()
        cls.user_a = "challenger2_user_alpha"
        cls.user_b = "challenger2_user_beta"
        cls.user_c = "challenger2_user_gamma"

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
                        TopicKnowledgeGraph.user_id.like("challenger2_%")
                    )
                )
                await db.commit()
        self.run_async(cleanup())

    def tearDown(self):
        async def cleanup():
            async with AsyncSessionLocal() as db:
                await db.execute(
                    delete(TopicKnowledgeGraph).filter(
                        TopicKnowledgeGraph.user_id.like("challenger2_%")
                    )
                )
                await db.commit()
        self.run_async(cleanup())

    # =========================================================================
    # TEST 1: INDEX USAGE & O(1) QUERY LATENCY (<5ms)
    # =========================================================================

    def test_c2_01_explain_query_plan_uses_index(self):
        """Verifies SQLite query planner uses ix_topic_knowledge_graphs_user_subject or uq index."""
        async def check_plan():
            async with AsyncSessionLocal() as db:
                query = text(
                    "EXPLAIN QUERY PLAN SELECT id, user_id, subject, graph_data "
                    "FROM topic_knowledge_graphs WHERE user_id = :uid AND subject = :subj"
                )
                result = await db.execute(query, {"uid": self.user_a, "subj": "sudoustroystvo"})
                rows = result.fetchall()
                plan_descriptions = [row[-1] for row in rows]
                full_plan_text = " ".join(plan_descriptions)

                # SQLite must use an INDEX, not SCAN TABLE
                self.assertNotIn("SCAN topic_knowledge_graphs", full_plan_text, "Query must NOT do a full table scan!")
                self.assertTrue(
                    "USING INDEX" in full_plan_text or "USING COVERING INDEX" in full_plan_text,
                    f"Query plan must use an index. Actual plan: {full_plan_text}"
                )
                return full_plan_text

        plan = self.run_async(check_plan())
        self.assertIsNotNone(plan)

    def test_c2_02_query_latency_under_load_o1(self):
        """Verifies TopicKnowledgeGraph lookups execute in <5ms with 1,000 records."""
        async def populate_and_benchmark():
            async with AsyncSessionLocal() as db:
                # Batch insert 1,000 records across different users and subjects
                records = []
                for i in range(1000):
                    records.append(
                        TopicKnowledgeGraph(
                            user_id=f"challenger2_pop_user_{i % 50}",
                            subject=f"subject_deck_{i}",
                            graph_data={"nodes": [{"id": f"n_{i}", "name": f"Node {i}"}], "edges": []},
                            tree_data={"id": "root", "name": "Root", "children": []}
                        )
                    )
                db.add_all(records)
                await db.commit()

            # Benchmark 200 random lookups
            latencies = []
            async with AsyncSessionLocal() as db:
                for i in range(0, 1000, 5):
                    uid = f"challenger2_pop_user_{i % 50}"
                    subj = f"subject_deck_{i}"

                    t0 = time.perf_counter()
                    stmt = select(TopicKnowledgeGraph).where(
                        TopicKnowledgeGraph.user_id == uid,
                        TopicKnowledgeGraph.subject == subj
                    )
                    res = await db.execute(stmt)
                    item = res.scalars().first()
                    t1 = time.perf_counter()

                    latencies.append((t1 - t0) * 1000.0)  # ms
                    self.assertIsNotNone(item)

            return latencies

        latencies = self.run_async(populate_and_benchmark())
        avg_latency = sum(latencies) / len(latencies)
        max_latency = max(latencies)
        sorted_lat = sorted(latencies)
        p95_latency = sorted_lat[int(len(sorted_lat) * 0.95)]
        p99_latency = sorted_lat[int(len(sorted_lat) * 0.99)]

        print(f"\n[BENCHMARK] TopicKnowledgeGraph Lookups (1,000 records):")
        print(f"  Avg latency: {avg_latency:.3f} ms")
        print(f"  P95 latency: {p95_latency:.3f} ms")
        print(f"  P99 latency: {p99_latency:.3f} ms")
        print(f"  Max latency: {max_latency:.3f} ms")

        self.assertLess(avg_latency, 5.0, f"Average latency ({avg_latency:.3f}ms) must be < 5ms")
        self.assertLess(p95_latency, 5.0, f"P95 latency ({p95_latency:.3f}ms) must be < 5ms")

    # =========================================================================
    # TEST 2: PRESET SEED FALLBACK MATRIX & OVERRIDE LIFECYCLE
    # =========================================================================

    def test_c2_03_seed_fallback_matrix(self):
        """Tests seed fallback matrix for known, alias, case-insensitive, and unknown subjects."""
        known_cases = [
            ("sudoustroystvo", 200, True),
            ("court_system", 200, True),
            ("судоустройство", 200, True),
            ("Sudoustroystvo", 200, True),
            ("SUDOUSTROYSTVO", 200, True),
            ("Court_System", 200, True),
        ]
        for subject, expected_status, expected_is_seed in known_cases:
            res = self.client.get(
                f"/api/knowledge-graph?subject={subject}",
                headers={"X-User-Id": self.user_a}
            )
            self.assertEqual(
                res.status_code, expected_status,
                f"Subject '{subject}' should return {expected_status}, got {res.status_code}"
            )
            data = res.json()
            self.assertEqual(data["is_seed"], expected_is_seed)
            self.assertEqual(len(data["graph_data"]["nodes"]), 15)

        unknown_cases = [
            "unknown_legal_theory",
            "civil_code_chapter_1",
            "quantum_mechanics",
            "biology_101",
            "arbitration_unseeded",
        ]
        for subject in unknown_cases:
            res = self.client.get(
                f"/api/knowledge-graph?subject={subject}",
                headers={"X-User-Id": self.user_a}
            )
            self.assertEqual(
                res.status_code, 404,
                f"Unknown subject '{subject}' must return 404, got {res.status_code}"
            )

    def test_c2_04_custom_override_and_seed_restoration_lifecycle(self):
        """Verifies: Seed -> User Custom Override (is_seed=False) -> User Delete -> Seed restored (is_seed=True)."""
        # Step 1: GET preset -> returns seed
        r1 = self.client.get("/api/knowledge-graph?subject=sudoustroystvo", headers={"X-User-Id": self.user_a})
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.json()["is_seed"])
        self.assertEqual(len(r1.json()["graph_data"]["nodes"]), 15)

        # Step 2: User A saves a custom customized version of sudoustroystvo (e.g. 3 nodes)
        custom_payload = {
            "subject": "sudoustroystvo",
            "graph_data": {
                "nodes": [
                    {"id": "custom_rf", "name": "Моя Система", "category": "authority", "summary": "Кастомный каркас."},
                    {"id": "sub_node", "name": "Подсудность", "category": "condition", "summary": "Условие."}
                ],
                "edges": [
                    {"source": "sub_node", "target": "custom_rf", "relation": "subject_to_jurisdiction"}
                ]
            }
        }
        r2 = self.client.post("/api/knowledge-graph", headers={"X-User-Id": self.user_a}, json=custom_payload)
        self.assertEqual(r2.status_code, 200)
        self.assertFalse(r2.json()["is_seed"])

        # Step 3: GET now returns User A's custom graph, NOT the seed!
        r3 = self.client.get("/api/knowledge-graph?subject=sudoustroystvo", headers={"X-User-Id": self.user_a})
        self.assertEqual(r3.status_code, 200)
        self.assertFalse(r3.json()["is_seed"])
        self.assertEqual(len(r3.json()["graph_data"]["nodes"]), 2)
        self.assertEqual(r3.json()["graph_data"]["nodes"][0]["name"], "Моя Система")

        # Step 4: User B requests sudoustroystvo -> STILL gets the seed! (User isolation)
        r_b = self.client.get("/api/knowledge-graph?subject=sudoustroystvo", headers={"X-User-Id": self.user_b})
        self.assertEqual(r_b.status_code, 200)
        self.assertTrue(r_b.json()["is_seed"])
        self.assertEqual(len(r_b.json()["graph_data"]["nodes"]), 15)

        # Step 5: User A deletes custom graph
        r_del = self.client.delete("/api/knowledge-graph?subject=sudoustroystvo", headers={"X-User-Id": self.user_a})
        self.assertEqual(r_del.status_code, 200)

        # Step 6: User A requests sudoustroystvo again -> restores seed fallback!
        r_restored = self.client.get("/api/knowledge-graph?subject=sudoustroystvo", headers={"X-User-Id": self.user_a})
        self.assertEqual(r_restored.status_code, 200)
        self.assertTrue(r_restored.json()["is_seed"])
        self.assertEqual(len(r_restored.json()["graph_data"]["nodes"]), 15)

    # =========================================================================
    # TEST 3: STRICT MULTI-USER ISOLATION
    # =========================================================================

    def test_c2_05_strict_user_isolation_crud(self):
        """Ensures complete CRUD isolation between multiple users on the same subject."""
        subject = "shared_subject_key"

        # 1. User A creates private graph
        payload_a = {
            "subject": subject,
            "graph_data": {
                "nodes": [{"id": "a_1", "name": "User A Secret", "category": "authority", "summary": "Data A."}],
                "edges": []
            }
        }
        res_a1 = self.client.post("/api/knowledge-graph", headers={"X-User-Id": self.user_a}, json=payload_a)
        self.assertEqual(res_a1.status_code, 200)

        # 2. User B cannot read User A's graph (404 since it's not a preset)
        res_b1 = self.client.get(f"/api/knowledge-graph?subject={subject}", headers={"X-User-Id": self.user_b})
        self.assertEqual(res_b1.status_code, 404)

        # 3. User B creates their own graph for the same subject
        payload_b = {
            "subject": subject,
            "graph_data": {
                "nodes": [{"id": "b_1", "name": "User B Data", "category": "instance", "summary": "Data B."}],
                "edges": []
            }
        }
        res_b2 = self.client.post("/api/knowledge-graph", headers={"X-User-Id": self.user_b}, json=payload_b)
        self.assertEqual(res_b2.status_code, 200)

        # 4. User A reads subject -> gets User A's data only
        res_a2 = self.client.get(f"/api/knowledge-graph?subject={subject}", headers={"X-User-Id": self.user_a})
        self.assertEqual(res_a2.status_code, 200)
        self.assertEqual(res_a2.json()["graph_data"]["nodes"][0]["name"], "User A Secret")

        # 5. User B reads subject -> gets User B's data only
        res_b3 = self.client.get(f"/api/knowledge-graph?subject={subject}", headers={"X-User-Id": self.user_b})
        self.assertEqual(res_b3.status_code, 200)
        self.assertEqual(res_b3.json()["graph_data"]["nodes"][0]["name"], "User B Data")

        # 6. User B deletes their graph -> User A's graph remains intact
        res_b_del = self.client.delete(f"/api/knowledge-graph?subject={subject}", headers={"X-User-Id": self.user_b})
        self.assertEqual(res_b_del.status_code, 200)

        # User B now gets 404
        res_b4 = self.client.get(f"/api/knowledge-graph?subject={subject}", headers={"X-User-Id": self.user_b})
        self.assertEqual(res_b4.status_code, 404)

        # User A still gets their graph
        res_a3 = self.client.get(f"/api/knowledge-graph?subject={subject}", headers={"X-User-Id": self.user_a})
        self.assertEqual(res_a3.status_code, 200)
        self.assertEqual(res_a3.json()["graph_data"]["nodes"][0]["name"], "User A Secret")

    # =========================================================================
    # TEST 4: CANVAS FORCE-GRAPH RUNTIME ATTRIBUTES EMPIRICAL CHECK
    # =========================================================================

    def test_c2_06_canvas_runtime_attributes_persistence_and_retrieval(self):
        """Empirically inspects whether Canvas runtime attributes (x, y, vx, vy, index)
        are tolerated without error, and checks whether they are stored/returned or stripped.
        """
        payload = {
            "subject": "canvas_empirical_test",
            "graph_data": {
                "nodes": [
                    {
                        "id": "node_canvas_1",
                        "name": "Canvas Node",
                        "category": "authority",
                        "summary": "Force graph runtime position test.",
                        "x": 350.5,
                        "y": -120.25,
                        "vx": 0.005,
                        "vy": -0.003,
                        "index": 7,
                        "fx": 350.5,
                        "fy": -120.25,
                        "__bckgDimensions": [120, 24]
                    }
                ],
                "edges": [
                    {
                        "source": "node_canvas_1",
                        "target": "node_canvas_1",  # Will be cleaned as self-loop
                        "relation": "appealed_to"
                    }
                ]
            }
        }
        # 1. POST must not return 422
        res = self.client.post("/api/knowledge-graph", headers={"X-User-Id": self.user_a}, json=payload)
        self.assertEqual(res.status_code, 200, f"Expected 200, got {res.status_code}: {res.text}")

        # 2. Inspect returned nodes in POST response
        post_nodes = res.json()["graph_data"]["nodes"]
        self.assertEqual(len(post_nodes), 1)
        returned_node = post_nodes[0]

        print(f"\n[EMPIRICAL] Canvas Runtime Attributes Test:")
        print(f"  Input node had keys: {list(payload['graph_data']['nodes'][0].keys())}")
        print(f"  Returned node has keys: {list(returned_node.keys())}")
        print(f"  Returned node content: {returned_node}")

        # Check DB raw record
        async def check_raw_db():
            async with AsyncSessionLocal() as db:
                stmt = select(TopicKnowledgeGraph).where(
                    TopicKnowledgeGraph.user_id == self.user_a,
                    TopicKnowledgeGraph.subject == "canvas_empirical_test"
                )
                r = await db.execute(stmt)
                rec = r.scalars().first()
                return rec.graph_data if rec else None

        raw_db_graph = self.run_async(check_raw_db())
        print(f"  Raw DB stored graph_data keys: {list(raw_db_graph['nodes'][0].keys())}")

    # =========================================================================
    # TEST 5: COMPLEX GRAPH STRESS & CYCLE RESILIENCE
    # =========================================================================

    def test_c2_07_stress_large_graph_and_cycle_prevention(self):
        """Tests handling of 200 nodes, 300 edges, with intentional cycles and dangling links."""
        nodes = []
        edges = []

        # Create 200 nodes
        for i in range(200):
            cat = ["authority", "instance", "condition", "exception", "legal_status"][i % 5]
            nodes.append({
                "id": f"stress_node_{i}",
                "name": f"Stress Entity {i}",
                "category": cat,
                "summary": f"Factual statement for entity {i}.",
                "level": (i % 3)
            })

        # Create 300 edges with intentional cycles and dangling edges
        for i in range(290):
            edges.append({
                "source": f"stress_node_{i % 190}",
                "target": f"stress_node_{(i + 1) % 190}",
                "relation": ["appealed_to", "demarcated_from", "excludes_application", "subject_to_jurisdiction"][i % 4]
            })

        # Add 10 dangling edges
        for i in range(10):
            edges.append({
                "source": f"stress_node_{i}",
                "target": f"non_existent_ghost_{i}",
                "relation": "appealed_to"
            })

        # Add 5 self-loops
        for i in range(5):
            edges.append({
                "source": f"stress_node_{i}",
                "target": f"stress_node_{i}",
                "relation": "demarcated_from"
            })

        # Synthesize tree mindmap - must not hang in infinite loop and must complete quickly
        t0 = time.perf_counter()
        clean_n, clean_e = clean_graph_data(nodes, edges)
        tree = build_hierarchical_tree(clean_n, clean_e, root_title="Stress Discipline")
        t1 = time.perf_counter()

        duration_ms = (t1 - t0) * 1000.0
        print(f"\n[STRESS] 200 nodes, 300 edges cleaned & tree synthesized in {duration_ms:.2f} ms")

        self.assertLess(duration_ms, 200.0, f"Tree synthesis too slow ({duration_ms:.2f}ms)")
        self.assertEqual(len(clean_n), 200)
        # Dangling (10) and self-loops (5) must be removed
        for e in clean_e:
            self.assertFalse(e["target"].startswith("non_existent_ghost_"))
            self.assertNotEqual(e["source"], e["target"])

        self.assertIsNotNone(tree)
        self.assertEqual(tree["name"], "Stress Discipline")


if __name__ == "__main__":
    unittest.main()
