# AGENTS.md — Data Grinder Project Guide for Antigravity

## Overview
Data Grinder is an asynchronous Python application combining:
1. **FastAPI Backend (`app/`)**: REST API for knowledge graphs, Socratic practice, flashcards, and Telegram WebApp integration.
2. **Telegram Bot (`bot.py`)**: `aiogram`-based bot managing user sessions and WebApp launch buttons.
3. **Database**: SQLite database stored locally in `data_grinder.db` via SQLAlchemy.
4. **Interactive UI (`app/static/`)**: Canvas 2D Obsidian-style knowledge graphs and collapsible DOM tree mindmaps.

## Key Directories and Files
- `app/main.py`: FastAPI entrypoint and middleware.
- `bot.py`: Telegram bot entrypoint and handlers.
- `app/api/`: API endpoints (`/api/knowledge-graph`, `/api/practice`, etc.).
- `app/database/`: Database engine, models, and session managers.
- `app/services/`: NLP extractors, DeepSeek client, practice trainer engine.
- `app/static/`: Frontend scripts, styles, and templates.
- `tests/`: Test suite (`pytest tests/`).

## Agent Performance & Navigation Rules
- **Ignore Bloat**: Never search or read files inside `venv/`, `.mimocode/`, or `backups/`.
- **Database Safety**: `data_grinder.db` is a binary SQLite database. Do not attempt to read or grep it as raw text. Inspect schema via `app/database/models.py`.
- **Integrity**: Always preserve prompt caching strings and atomic rules validated by `tests/test_13_deepseek_prompt_caching_and_atomic_rules.py`.
- **Dependencies**: Managed via `requirements.txt`.
