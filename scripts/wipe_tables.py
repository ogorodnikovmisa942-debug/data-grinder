# scripts/wipe_tables.py
"""
Скрипт полной очистки всех тестовых данных из SQLite базы Data Grinder.
Очищает все таблицы, сбрасывает автоинкременты sqlite_sequence и выполняет VACUUM.
"""

import sys
import os
import sqlite3

DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data_grinder.db"))

TABLES_TO_WIPE = [
    "cards",
    "phrases",
    "categories",
    "review_logs",
    "daily_sessions",
    "generation_jobs",
    "ai_telemetry_logs",
    "topic_knowledge_graphs",
    "practice_items",
    "practice_session_logs",
    "user_sessions",
    "user_settings",
    "invite_codes"
]

def wipe_data():
    if not os.path.exists(DB_PATH):
        print(f"База данных не найдена по пути: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    print("=== ПОЛНАЯ ОЧИСТКА ТАБЛИЦ БАЗЫ ДАННЫХ ===")
    
    # Отключаем внешние ключи на время массовой очистки
    cur.execute("PRAGMA foreign_keys = OFF;")

    for table in TABLES_TO_WIPE:
        try:
            cur.execute(f"DELETE FROM {table};")
            print(f"[OK] Таблица '{table}' очищена.")
        except sqlite3.OperationalError as e:
            print(f"[SKIP] Таблица '{table}' отсутствует или недоступна: {e}")

    # Сбрасываем счетчики autoincrement
    try:
        cur.execute("DELETE FROM sqlite_sequence;")
        print("[OK] Счетчики автоинкремента сброшены.")
    except Exception:
        pass

    conn.commit()
    
    # Включаем внешние ключи обратно
    cur.execute("PRAGMA foreign_keys = ON;")
    
    # Сжимаем файл базы данных
    cur.execute("VACUUM;")
    conn.close()

    print("\n=== ВСЕ ТЕСТОВЫЕ ДАННЫЕ УСПЕШНО УДАЛЕНЫ (БАЗА ЧИСТАЯ) ===")

if __name__ == "__main__":
    wipe_data()
