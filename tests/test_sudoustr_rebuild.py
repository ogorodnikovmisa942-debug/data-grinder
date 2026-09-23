import unittest
from fastapi.testclient import TestClient

from main import app
from app.services.graph_service import resolve_subject_alias, get_all_subject_aliases


class TestSudoustrRebuild(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.headers = {
            "X-Telegram-User-Id": "test_sudoustr_user",
            "X-User-Role": "student"
        }

    def tearDown(self):
        import asyncio
        from sqlalchemy import text
        from app.database.session import engine
        async def _cleanup():
            async with engine.begin() as conn:
                await conn.execute(text("DELETE FROM topic_knowledge_graphs WHERE user_id = 'test_sudoustr_user'"))
        asyncio.run(_cleanup())

    def test_alias_preserves_sudoustr(self):
        self.assertEqual(resolve_subject_alias("sudoustr"), "sudoustr")
        self.assertEqual(resolve_subject_alias("sudoustroystvo"), "sudoustroystvo")
        self.assertEqual(resolve_subject_alias("sudoust"), "sudoust")
        aliases = get_all_subject_aliases("sudoustr")
        self.assertIn("sudoustr", aliases)
        self.assertIn("sudoustroystvo", aliases)
        self.assertIn("sudoust", aliases)
        
        aliases_sudoust = get_all_subject_aliases("sudoust")
        self.assertIn("sudoustr", aliases_sudoust)
        self.assertIn("sudoustroystvo", aliases_sudoust)
        self.assertIn("sudoust", aliases_sudoust)

    def test_rebuild_and_fetch_sudoustr_graph(self):
        # Trigger rebuild
        res_rebuild = self.client.post("/api/knowledge-graph/rebuild?subject=sudoustr", headers=self.headers)
        self.assertEqual(res_rebuild.status_code, 200)
        data = res_rebuild.json()
        
        # Must return user's subject, NOT sudoustroystvo
        self.assertEqual(data["subject"], "sudoustr")
        self.assertIn("graph_data", data)
        self.assertIn("tree_data", data)
        
        nodes = data["graph_data"]["nodes"]
        self.assertGreater(len(nodes), 20)
        
        # Root node must be SUDOUSTR
        root_node = next((n for n in nodes if n["level"] == 0), None)
        self.assertIsNotNone(root_node)
        self.assertEqual(root_node["name"], "SUDOUSTR")
        
        # Tree root must be SUDOUSTR
        self.assertEqual(data["tree_data"]["name"], "SUDOUSTR")
        
        # Now fetch via GET
        res_get = self.client.get("/api/knowledge-graph?subject=sudoustr", headers=self.headers)
        self.assertEqual(res_get.status_code, 200)
        get_data = res_get.json()
        self.assertEqual(get_data["subject"], "sudoustr")
        self.assertEqual(get_data["graph_data"]["nodes"][0]["name"], "SUDOUSTR")
        self.assertEqual(get_data["tree_data"]["name"], "SUDOUSTR")


if __name__ == "__main__":
    unittest.main()
