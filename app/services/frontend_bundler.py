"""
Frontend Asset Bundler for Data Grinder.
Splits monolithic app.js into domain modules, or bundles modules into app.js.
"""

import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
STATIC_DIR = BASE_DIR / "app" / "static"
STATIC_JS_DIR = STATIC_DIR / "js"
MODULES_DIR = STATIC_JS_DIR / "modules"
APP_JS_PATH = STATIC_JS_DIR / "app.js"

COMPONENTS_DIR = STATIC_DIR / "components"
INDEX_HTML_PATH = STATIC_DIR / "index.html"
INDEX_TEMPLATE_PATH = COMPONENTS_DIR / "index_template.html"

MODULE_SLICES = [
    ("01_core.js", 1, 205, "Telegram SDK, Auth, Global State & Formatting"),
    ("02_train.js", 206, 2150, "FSRS Algorithm, Train Queue, Rating, Gestures & Pomodoro"),
    ("03_cards_archive.js", 2151, 3489, "Cards Archive, List Rendering, Filters & Batch Operations"),
    ("04_staging.js", 3490, 4176, "Staging Sandbox, Moderation Swipes & Queue"),
    ("05_card_editor.js", 4177, 4605, "Card Editor Modal, Manual Card Creation & Actions"),
    ("06_subjects.js", 4606, 4988, "Subjects Manager & Statistics"),
    ("07_graph.js", 4989, 6526, "Knowledge Graph (Force-Directed 2D, Mindmap Tree & Node Drawer)"),
    ("08_practice.js", 6527, 6854, "Interactive Socratic Practice, Quizzes & Debrief"),
]


def split_app_js():
    if not APP_JS_PATH.exists():
        return

    MODULES_DIR.mkdir(parents=True, exist_ok=True)
    full_content = APP_JS_PATH.read_text(encoding="utf-8")
    lines = full_content.splitlines(keepends=True)

    for filename, start_l, end_l, description in MODULE_SLICES:
        chunk = lines[start_l - 1 : end_l]
        module_path = MODULES_DIR / filename
        module_path.write_text("".join(chunk), encoding="utf-8")


def bundle_modules():
    if not MODULES_DIR.exists():
        return

    module_files = sorted(MODULES_DIR.glob("*.js"))
    if not module_files:
        return

    bundled_chunks = []
    for mf in module_files:
        content = mf.read_text(encoding="utf-8")
        bundled_chunks.append(content)

    full_bundle = "".join(bundled_chunks)
    APP_JS_PATH.write_text(full_bundle, encoding="utf-8")


def bundle_html():
    if not INDEX_TEMPLATE_PATH.exists():
        return

    template_content = INDEX_TEMPLATE_PATH.read_text(encoding="utf-8")

    def replacer(match: re.Match) -> str:
        component_name = match.group(1).strip()
        comp_path = COMPONENTS_DIR / component_name
        if not comp_path.exists():
            raise FileNotFoundError(f"Component file not found: {comp_path}")
        return comp_path.read_text(encoding="utf-8")

    bundled_html = re.sub(r"<!--\s*@include\s+([a-zA-Z0-9_\-\.]+)\s*-->", replacer, template_content)
    INDEX_HTML_PATH.write_text(bundled_html, encoding="utf-8")


def bundle_all():
    bundle_modules()
    bundle_html()

