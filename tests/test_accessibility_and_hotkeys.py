# tests/test_accessibility_and_hotkeys.py
import os
import unittest
import re

class TestAccessibilityAndHotkeys(unittest.TestCase):
    def setUp(self):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.index_path = os.path.join(self.base_dir, "app", "static", "index.html")
        self.app_js_path = os.path.join(self.base_dir, "app", "static", "js", "app.js")
        self.core_js_path = os.path.join(self.base_dir, "app", "static", "js", "modules", "01_core.js")
        self.train_js_path = os.path.join(self.base_dir, "app", "static", "js", "modules", "02_train.js")

    def test_01_core_or_train_defines_keyboard_navigation(self):
        """Test that 01_core.js or 02_train.js defines the keyboard navigation event listener."""
        self.assertTrue(os.path.exists(self.core_js_path), "01_core.js must exist")
        with open(self.core_js_path, "r", encoding="utf-8") as f:
            core_js = f.read()

        has_keydown_in_core = "addEventListener('keydown'" in core_js or 'addEventListener("keydown"' in core_js
        
        with open(self.train_js_path, "r", encoding="utf-8") as f:
            train_js = f.read()
        has_keydown_in_train = "addEventListener('keydown'" in train_js or 'addEventListener("keydown"' in train_js

        self.assertTrue(
            has_keydown_in_core or has_keydown_in_train,
            "Either 01_core.js or 02_train.js must register a keydown event listener"
        )
        # Check active element guards
        self.assertIn("INPUT", core_js)
        self.assertIn("TEXTAREA", core_js)
        self.assertIn("SELECT", core_js)
        self.assertIn("isContentEditable", core_js)

    def test_app_js_contains_hotkey_handlers(self):
        """Test that app.js contains handlers for Space, Enter, Escape, Digit1-Digit4."""
        self.assertTrue(os.path.exists(self.app_js_path), "app.js must exist")
        with open(self.app_js_path, "r", encoding="utf-8") as f:
            app_js = f.read()

        # Escape handlers for modals
        self.assertIn("Escape", app_js)
        self.assertIn("closePracticeModal", app_js)
        self.assertIn("closeKnowledgeGraphModal", app_js)
        self.assertIn("closeCardEditorModal", app_js)
        self.assertIn("closeSubjectsManagerModal", app_js)
        self.assertIn("closeFilterModal", app_js)
        self.assertIn("exitBulkMode", app_js)

        # Space and Enter handlers for flipping card
        self.assertIn("Space", app_js)
        self.assertIn("Enter", app_js)
        self.assertIn("flipCard", app_js)
        self.assertIn("isAnswerRevealed", app_js)

        # Digit1 - Digit4 handlers
        self.assertIn("Digit1", app_js)
        self.assertIn("Digit2", app_js)
        self.assertIn("Digit3", app_js)
        self.assertIn("Digit4", app_js)
        self.assertIn("rateCard", app_js)

    def test_modal_templates_have_dialog_role_and_aria_modal(self):
        """Test that all modal root dialogs have role='dialog' and aria-modal='true'."""
        self.assertTrue(os.path.exists(self.index_path), "index.html must exist")
        with open(self.index_path, "r", encoding="utf-8") as f:
            html = f.read()

        modals = [
            ("knowledge-graph-modal", "Каркас знаний"),
            ("practice-modal", "Практический тренажер"),
            ("card-editor-modal", "Редактор карточек"),
            ("subjects-manager-modal", "Управление предметами"),
            ("staging-rejected-modal", "Отклоненные карточки"),
            ("subject-rename-modal", "Переименовать предмет"),
        ]

        for modal_id, aria_label in modals:
            # Check presence of modal container with role="dialog" and aria-modal="true"
            pattern = rf'id="{modal_id}"[^>]*role="dialog"[^>]*aria-modal="true"'
            pattern_alt = rf'role="dialog"[^>]*aria-modal="true"[^>]*id="{modal_id}"'
            pattern_label = rf'id="{modal_id}"[^>]*aria-label="{aria_label}"'
            pattern_label_alt = rf'aria-label="{aria_label}"[^>]*id="{modal_id}"'
            
            matched_role = re.search(pattern, html, re.DOTALL) or re.search(pattern_alt, html, re.DOTALL)
            self.assertIsNotNone(
                matched_role,
                f"Modal #{modal_id} must have role='dialog' and aria-modal='true'"
            )
            
            matched_label = re.search(pattern_label, html, re.DOTALL) or re.search(pattern_label_alt, html, re.DOTALL)
            self.assertIsNotNone(
                matched_label,
                f"Modal #{modal_id} must have descriptive aria-label='{aria_label}'"
            )

    def test_icon_buttons_have_aria_labels(self):
        """Test that icon-only buttons have descriptive aria-label attributes."""
        with open(self.index_path, "r", encoding="utf-8") as f:
            html = f.read()

        # Focus toggle button
        self.assertIn('id="focus-toggle"', html)
        self.assertIn('aria-label="Режим фокуса"', html)

        # Knowledge graph fullscreen button
        self.assertIn('id="kg-fullscreen-btn"', html)
        self.assertIn('aria-label="Полноэкранный режим графа"', html)

        # Layout buttons
        self.assertIn('aria-label="Сетка / Радиальный / Дерево"', html)
        self.assertIn('id="kg-layout-force"', html)
        self.assertIn('id="kg-layout-radial"', html)
        self.assertIn('id="kg-layout-tree"', html)

        # Close buttons
        self.assertIn('aria-label="Закрыть"', html)

if __name__ == "__main__":
    unittest.main()
