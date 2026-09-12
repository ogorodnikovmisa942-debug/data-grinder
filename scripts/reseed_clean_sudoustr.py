# scripts/reseed_clean_sudoustr.py
"""
Script to clean, filter, and reseed the user's uploaded deck for 'sudoustr'.
Purges tautologies, academic methodology fluff, binary yes/no trivia, and historical USSR ballast,
while retaining all legitimate legal institutes and concepts.
Then synthesizes the Knowledge Graph / Mindmap and creates Practice Items.
"""

import sys
import os
import json
import asyncio
from datetime import datetime, timedelta
from collections import Counter

# Ensure root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select, delete
from app.database.session import AsyncSessionLocal
from app.database.models import Phrase, Card, TopicKnowledgeGraph, PracticeItem, UserSetting, UserSession
from app.services.ai_gateway import is_blacklisted_card, semantic_normalize_front
from app.services.graph_service import synthesize_graph_from_cards
from app.services.practice_service import generate_practice_session

DEFAULT_PAYLOAD_PATH = r"C:\Users\Morpheas\.gemini\antigravity\brain\25cb00de-e59a-49b8-a046-17f5b1e2892c\.user_uploaded\media_1789232902821.json"


async def clean_and_seed(payload_path: str = DEFAULT_PAYLOAD_PATH, target_users: list[str] = None):
    if not os.path.exists(payload_path):
        print(f"Error: Payload not found at {payload_path}")
        return

    with open(payload_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    raw_cards = data.get("cards", [])
    raw_subject = data.get("subject_slug", "sudoustr")
    phrase_title = data.get("phrase_title", "Правоохранительная функция государства")

    print(f"=== PROCESSING DECK: '{phrase_title}' (slug: {raw_subject}) ===")
    print(f"Total input cards: {len(raw_cards)}")

    # 1. Pipeline filtering
    kept = []
    filtered = []
    reasons = Counter()
    seen_fronts = set()

    for c in raw_cards:
        is_bl, reason = is_blacklisted_card(c, subject_domain=raw_subject)
        if is_bl:
            filtered.append((c, reason))
            reasons[reason] += 1
            continue

        front = c.get("text") or c.get("front") or ""
        fp = semantic_normalize_front(front)
        if fp in seen_fronts:
            filtered.append((c, "semantic_duplicate"))
            reasons["semantic_duplicate"] += 1
            continue
        seen_fronts.add(fp)
        kept.append(c)

    print(f"\n[Filter Results]")
    print(f"  Retained (High-Yield): {len(kept)} cards")
    print(f"  Purged (Low-Quality):  {len(filtered)} cards")
    print(f"  Filter Breakdown:")
    for reason, count in reasons.most_common():
        print(f"    - {reason}: {count}")

    if target_users is None:
        target_users = ["dev_user", "default_user"]

    async with AsyncSessionLocal() as db:
        for user_id in target_users:
            print(f"\n--- Seeding user: '{user_id}' ---")

            # Ensure user setting & session exist
            res = await db.execute(select(UserSetting).filter(UserSetting.user_id == user_id))
            if not res.scalar_one_or_none():
                db.add(UserSetting(user_id=user_id, daily_limit=20, target_retention=0.9, subject_limits={"all": 20}))
            
            res_sess = await db.execute(select(UserSession).filter(UserSession.telegram_id == user_id))
            if not res_sess.scalar_one_or_none():
                db.add(UserSession(telegram_id=user_id, user_id=user_id))

            # Remove old cards for this subject to prevent stale dirty cards
            old_phrases = (await db.execute(
                select(Phrase).filter(Phrase.user_id == user_id, Phrase.subject.in_([raw_subject, "sudoustroystvo"]))
            )).scalars().all()
            for op in old_phrases:
                await db.delete(op)
            
            await db.execute(
                delete(Card).where(Card.user_id == user_id, Card.subject.in_([raw_subject, "sudoustroystvo"]))
            )
            await db.execute(
                delete(TopicKnowledgeGraph).where(TopicKnowledgeGraph.user_id == user_id, TopicKnowledgeGraph.subject.in_([raw_subject, "sudoustroystvo"]))
            )
            await db.execute(
                delete(PracticeItem).where(PracticeItem.user_id == user_id, PracticeItem.subject.in_([raw_subject, "sudoustroystvo"]))
            )
            await db.commit()

            # Create Phrase
            phrase = Phrase(
                text=phrase_title,
                subject=raw_subject,
                user_id=user_id
            )
            db.add(phrase)
            await db.flush()

            # Insert Clean Cards
            now = datetime.utcnow()
            for i, c in enumerate(kept):
                front = (c.get("text") or c.get("front") or "").strip()
                back = (c.get("translation") or c.get("back") or "").strip()
                sec = (c.get("secondary_text") or c.get("secondary") or "").strip()
                
                card_obj = Card(
                    phrase_id=phrase.id,
                    user_id=user_id,
                    subject=raw_subject,
                    text=front,
                    secondary_text=sec,
                    translation=back,
                    difficulty=5.0,
                    stability=1.0,
                    state=0,
                    next_review=now + timedelta(minutes=i * 2),
                    lapses=0
                )
                db.add(card_obj)
            await db.commit()
            print(f"  Inserted {len(kept)} cards into DB for subject '{raw_subject}' (Phrase ID: {phrase.id}).")

            # Synthesize and persist TopicKnowledgeGraph
            syn_graph = synthesize_graph_from_cards(kept, fallback_title=phrase_title)
            
            # Persist for both slugs raw_subject and 'sudoustroystvo'
            for sub_slug in [raw_subject, "sudoustroystvo"]:
                tkg = TopicKnowledgeGraph(
                    user_id=user_id,
                    subject=sub_slug,
                    graph_data=syn_graph["graph_data"],
                    tree_data=syn_graph["tree_data"],
                    created_at=now,
                    updated_at=now
                )
                db.add(tkg)
            await db.commit()
            print(f"  Persisted TopicKnowledgeGraph: {len(syn_graph['graph_data']['nodes'])} nodes, {len(syn_graph['graph_data']['edges'])} edges.")

            # Generate and verify 10 practice items
            practice_items = await generate_practice_session(
                user_id=user_id,
                subject=raw_subject,
                count=10,
                db=db
            )
            print(f"  Generated {len(practice_items)} interactive Practice Items.")

    print("\n=== RESEED AND SANITIZATION COMPLETED SUCCESSFULLY ===")


if __name__ == "__main__":
    asyncio.run(clean_and_seed())
