"""
Unit and integration tests for UI template modularization and frontend bundler (Phase 9).
"""

import os
from pathlib import Path
import pytest

from app.services.frontend_bundler import (
    COMPONENTS_DIR,
    INDEX_HTML_PATH,
    INDEX_TEMPLATE_PATH,
    bundle_html,
    bundle_all,
)

EXPECTED_COMPONENTS = [
    "header.html",
    "screen_train.html",
    "screen_data.html",
    "screen_stats.html",
    "screen_config.html",
    "screen_staging.html",
    "bottom_nav.html",
    "modal_card_editor.html",
    "modal_subjects.html",
    "modal_knowledge_graph.html",
    "modal_practice.html",
    "modal_filters.html",
    "index_template.html",
]

CRITICAL_DOM_IDS = [
    "subject-selector",
    "screen-train",
    "screen-data",
    "screen-stats",
    "screen-config",
    "staging-overlay",
    "bottom-nav",
    "rest-overlay",
    "card-editor-modal",
    "subjects-manager-modal",
    "subject-rename-modal",
    "knowledge-graph-modal",
    "kg-empty-state",
    "kg-graph-canvas-wrapper",
    "kg-tree-view",
    "kg-node-drawer",
    "practice-modal",
    "practice-card-container",
    "practice-options-list",
    "practice-finish-screen",
    "data-container",
    "bulk-action-bar",
    "session-starter",
    "session-debrief-container",
    "cards-filter-modal",
]


def test_all_component_files_exist():
    """Verify all required component partials and master template exist in app/static/components/."""
    assert COMPONENTS_DIR.exists(), f"Components directory does not exist: {COMPONENTS_DIR}"
    for filename in EXPECTED_COMPONENTS:
        component_path = COMPONENTS_DIR / filename
        assert component_path.exists(), f"Missing component partial: {filename}"
        assert component_path.stat().st_size > 0, f"Component file is empty: {filename}"


def test_bundle_html_executes_and_generates_index():
    """Verify bundle_html runs cleanly and populates index.html."""
    bundle_html()
    assert INDEX_HTML_PATH.exists(), f"Bundled index.html missing at {INDEX_HTML_PATH}"
    content = INDEX_HTML_PATH.read_text(encoding="utf-8")
    assert len(content) > 10000, "Bundled index.html seems too short"
    assert "<!-- @include" not in content, "Unresolved <!-- @include directives found in index.html"


def test_bundled_index_contains_all_critical_dom_ids():
    """Verify all critical screens, modals, and elements exist in the generated index.html."""
    bundle_html()
    content = INDEX_HTML_PATH.read_text(encoding="utf-8")
    for dom_id in CRITICAL_DOM_IDS:
        expected_attr = f'id="{dom_id}"'
        assert expected_attr in content, f"Critical DOM element missing in bundled index.html: {expected_attr}"


def test_bundle_all_executes_without_errors():
    """Verify unified bundle_all successfully bundles both JS modules and HTML templates."""
    bundle_all()
    assert INDEX_HTML_PATH.exists()
    js_path = INDEX_HTML_PATH.parent / "js" / "app.js"
    assert js_path.exists()
    assert js_path.stat().st_size > 0


def test_bundle_html_raises_on_missing_component(tmp_path, monkeypatch):
    """Verify bundle_html raises FileNotFoundError if an included component is missing."""
    bad_template = tmp_path / "index_template.html"
    bad_template.write_text("<html><!-- @include non_existent_component_12345.html --></html>", encoding="utf-8")
    monkeypatch.setattr("app.services.frontend_bundler.INDEX_TEMPLATE_PATH", bad_template)
    monkeypatch.setattr("app.services.frontend_bundler.COMPONENTS_DIR", tmp_path)

    with pytest.raises(FileNotFoundError) as exc_info:
        bundle_html()
    assert "non_existent_component_12345.html" in str(exc_info.value)
