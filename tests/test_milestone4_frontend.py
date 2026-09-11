# tests/test_milestone4_frontend.py
import os
import sys
import unittest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from main import app

client = TestClient(app)

class TestMilestone4Frontend(unittest.TestCase):
    def setUp(self):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.index_path = os.path.join(self.base_dir, "app", "static", "index.html")
        self.css_path = os.path.join(self.base_dir, "app", "static", "css", "main.css")
        self.js_path = os.path.join(self.base_dir, "app", "static", "js", "app.js")

    def test_index_html_contains_milestone4_components(self):
        self.assertTrue(os.path.exists(self.index_path), "index.html must exist")
        with open(self.index_path, "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn("force-graph", html)
        self.assertIn("openKnowledgeGraphModal()", html)
        self.assertIn("openPracticeModal()", html)
        self.assertIn('id="knowledge-graph-modal"', html)
        self.assertIn('id="kg-tree-view"', html)
        self.assertIn('id="kg-graph-view"', html)
        self.assertIn('id="kg-graph-canvas-wrapper"', html)
        self.assertIn('id="kg-node-drawer"', html)
        self.assertIn('id="practice-modal"', html)
        self.assertIn('id="practice-card-container"', html)
        self.assertIn('id="practice-options-list"', html)
        self.assertIn('id="practice-feedback-container"', html)
        self.assertIn('id="practice-gold-standard-box"', html)
        self.assertIn('id="practice-finish-screen"', html)

    def test_main_css_contains_neon_and_tree_classes(self):
        self.assertTrue(os.path.exists(self.css_path), "main.css must exist")
        with open(self.css_path, "r", encoding="utf-8") as f:
            css = f.read()

        self.assertIn(".badge-authority", css)
        self.assertIn(".badge-instance", css)
        self.assertIn(".badge-condition", css)
        self.assertIn(".badge-exception", css)
        self.assertIn(".badge-legal_status", css)
        self.assertIn(".tree-branch-container", css)
        self.assertIn(".practice-option-correct", css)
        self.assertIn(".practice-option-wrong", css)

    def test_app_js_contains_controllers(self):
        self.assertTrue(os.path.exists(self.js_path), "app.js must exist")
        with open(self.js_path, "r", encoding="utf-8") as f:
            js = f.read()

        self.assertIn("window.openKnowledgeGraphModal", js)
        self.assertIn("window.closeKnowledgeGraphModal", js)
        self.assertIn("window.switchKgView", js)
        self.assertIn("window.loadKnowledgeGraph", js)
        self.assertIn("renderKnowledgeTreeNode", js)
        self.assertIn("window.initForceGraph", js)
        self.assertIn("window.showKgNodeDrawer", js)
        self.assertIn("window.openPracticeModal", js)
        self.assertIn("window.closePracticeModal", js)
        self.assertIn("window.startPracticeSession", js)
        self.assertIn("selectPracticeOption", js)
        self.assertIn("showPracticeFinish", js)

    def test_api_routes_available(self):
        headers = {"X-User-Id": "test_m4_user"}
        res_kg = client.get("/api/knowledge-graph?subject=sudoustroystvo", headers=headers)
        self.assertEqual(res_kg.status_code, 200)
        kg_data = res_kg.json()
        self.assertIn("graph_data", kg_data)
        self.assertIn("tree_data", kg_data)

        res_pr = client.get("/api/practice/session?subject=sudoustroystvo&count=3", headers=headers)
        self.assertEqual(res_pr.status_code, 200)
        pr_data = res_pr.json()
        self.assertIsInstance(pr_data, list)
        self.assertGreaterEqual(len(pr_data), 1)

if __name__ == "__main__":
    unittest.main()
