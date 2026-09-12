import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import asyncio
from httpx import AsyncClient, ASGITransport
from main import app

async def test_apis():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Test Knowledge Graph for 'sudoustr'
        res = await ac.get("/api/knowledge-graph?subject=sudoustr", headers={"X-User-Id": "dev_user"})
        print("1. GET /api/knowledge-graph?subject=sudoustr status:", res.status_code)
        assert res.status_code == 200
        kg = res.json()
        nodes = kg.get("graph_data", {}).get("nodes", [])
        edges = kg.get("graph_data", {}).get("edges", [])
        tree = kg.get("tree_data")
        root_name = tree.get("name") if tree else None
        print(f"   Nodes: {len(nodes)}, Edges: {len(edges)}, Root: {root_name}")
        assert len(nodes) > 0
        assert len(edges) > 0
        
        # 2. Test Knowledge Graph for alias 'sudoustroystvo'
        res_alias = await ac.get("/api/knowledge-graph?subject=sudoustroystvo", headers={"X-User-Id": "dev_user"})
        print("2. GET /api/knowledge-graph?subject=sudoustroystvo status:", res_alias.status_code)
        assert res_alias.status_code == 200
        
        # 3. Test Practice Session for 'sudoustr'
        res_prac = await ac.get("/api/practice/session?subject=sudoustr&count=10", headers={"X-User-Id": "dev_user"})
        print("3. GET /api/practice/session?subject=sudoustr status:", res_prac.status_code)
        assert res_prac.status_code == 200
        raw_json = res_prac.json()
        items = raw_json if isinstance(raw_json, list) else raw_json.get("items", [])
        print(f"   Practice items count: {len(items)}")
        assert len(items) == 10
        for i, item in enumerate(items[:3], 1):
            prompt_preview = item.get("prompt", "")[:60]
            opts_count = len(item.get("options", []))
            print(f"   Item {i}: Type={item.get('type')}, Prompt={prompt_preview}..., Options={opts_count}")
            assert opts_count == 4, f"Expected 4 options, got {opts_count}"
            
        # 4. Verify answer on first practice item
        first_item = items[0]
        opt0 = first_item["options"][0]
        res_ver = await ac.post("/api/practice/verify", json={
            "item_id": first_item["id"],
            "selected_answer": opt0
        }, headers={"X-User-Id": "dev_user"})
        print("4. POST /api/practice/verify status:", res_ver.status_code)
        assert res_ver.status_code == 200
        ver_data = res_ver.json()
        expl_preview = ver_data.get("explanation", "")[:60]
        print(f"   Verified: correct={ver_data.get('correct')}, explanation={expl_preview}...")

        # 5. Test Train Session for 'sudoustr'
        res_train = await ac.get("/api/session?subject=sudoustr&mode=new", headers={"X-User-Id": "dev_user"})
        print("5. GET /api/session?subject=sudoustr status:", res_train.status_code)
        assert res_train.status_code == 200
        train_cards = res_train.json()
        print(f"   Train cards returned: {len(train_cards)}")
        assert len(train_cards) > 0
        card_preview = train_cards[0].get("text", "")[:60]
        print(f"   First card text: {card_preview}...")

        # 6. Test Train Session for alias 'sudoustroystvo'
        res_train_alias = await ac.get("/api/session?subject=sudoustroystvo&mode=new", headers={"X-User-Id": "dev_user"})
        print("6. GET /api/session?subject=sudoustroystvo status:", res_train_alias.status_code)
        assert res_train_alias.status_code == 200
        assert len(res_train_alias.json()) > 0

        print("\nALL API CHECKS PASSED PERFECTLY!")

if __name__ == "__main__":
    asyncio.run(test_apis())
