# scripts/diagnose_db.py
import os
import sys
import subprocess
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, func
from app.database.session import AsyncSessionLocal
from app.database.models import UserSession, Card, UserSetting
from app.api.endpoints.train import get_session_cards
from app.services.graph_service import get_all_subject_aliases, resolve_subject_alias

async def main():
    print("=" * 60)
    print("  DATA GRINDER SERVER DIAGNOSTIC TOOL")
    print("=" * 60)

    # 1. Git status check
    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], text=True).strip()
        print(f"Git commit: {commit} (branch: {branch})")
    except Exception as e:
        print(f"Git check error: {e}")

    # 2. Database checks
    async with AsyncSessionLocal() as db:
        # Users
        users = (await db.execute(select(UserSession.telegram_id, UserSession.user_id, UserSession.is_experiment_participant, UserSession.experiment_phase))).all()
        print(f"\n[1] Registered Telegram Users in DB: {len(users)}")
        user_ids = []
        for u in users:
            print(f"  - Telegram ID: {u[0]} | User ID: {u[1]} | Experiment: {u[2]} (Phase {u[3]})")
            user_ids.append(str(u[0]))
            if str(u[1]) not in user_ids:
                user_ids.append(str(u[1]))

        if not user_ids:
            user_ids = ["default_user"]

        # Cards summary
        cards_summary = (await db.execute(
            select(Card.user_id, Card.subject, Card.state, func.count(Card.id))
            .group_by(Card.user_id, Card.subject, Card.state)
        )).all()

        print(f"\n[2] Cards in DB (total groups: {len(cards_summary)}):")
        if not cards_summary:
            print("  [!] WARNING: NO CARDS FOUND IN DATABASE!")
        for row in cards_summary:
            state_desc = {0: "0 (NEW)", 1: "1 (LEARNING)", 2: "2 (REVIEW)", 3: "3 (RELEARNING)"}.get(row[2], str(row[2]))
            print(f"  - User: {row[0]} | Subject: {row[1]} | State: {state_desc} | Count: {row[3]}")

        # Aliases test
        print("\n[3] Subject Aliases Test for 'sudoust':")
        aliases = get_all_subject_aliases("sudoust")
        canonical = resolve_subject_alias("sudoust")
        print(f"  Aliases for 'sudoust': {aliases}")
        print(f"  Canonical for 'sudoust': {canonical}")
        if "sudoustr" not in aliases:
            print("  [!] ERROR: 'sudoustr' is NOT in aliases of 'sudoust'! Server is running outdated code!")
        else:
            print("  [OK] Aliases mapping includes 'sudoustr' and 'sudoustroystvo'.")

        # Session simulation
        print("\n[4] Training Session Simulation:")
        active_uids = set([r[0] for r in cards_summary] + [u for u in user_ids if u.isdigit()] + ["default_user"])
        for uid in sorted(active_uids):
            for subj in ["sudoust", "sudoustr", "all"]:
                try:
                    cards = await get_session_cards(subject=subj, mode="new", current_user=uid, db=db)
                    print(f"  User '{uid}' -> subject='{subj}' [mode=new]: returned {len(cards)} cards")
                except Exception as ex:
                    print(f"  User '{uid}' -> subject='{subj}' [mode=new]: ERROR {ex}")

    print("\n" + "=" * 60)
    print("  DIAGNOSTIC FINISHED")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
