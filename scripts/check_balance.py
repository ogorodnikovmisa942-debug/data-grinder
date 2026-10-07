#!/usr/bin/env python3
"""Реальный остаток на балансе DeepSeek (GET /user/balance, бесплатно). Ключ берётся из .env, наружу не печатается.
Платформа может отставать на несколько минут от последних запросов."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.core.config import settings  # noqa: E402


def main() -> int:
    key = (settings.DEEPSEEK_API_KEY or "").strip()
    if not key:
        print("DEEPSEEK_API_KEY не задан в .env")
        return 1
    r = httpx.get(f"{settings.DEEPSEEK_BASE_URL.rstrip('/')}/user/balance", headers={"Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    data = r.json()
    for b in data.get("balance_infos", []):
        print(f"{b.get('currency')}: всего {b.get('total_balance')} (пополнено {b.get('topped_up_balance')}, бонусы {b.get('granted_balance')})")
    print("доступен для API:", data.get("is_available"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
