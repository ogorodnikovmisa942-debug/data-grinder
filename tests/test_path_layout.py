"""Раскладка графа «Пути знаний» (app/static/js/modules/07a_path_layout.js), проверка через node.

Критерии из RFC: линии дерева не пересекаются, подписи основ и тем не накладываются,
ни один узел не теряется, вертикальная раскладка не вытягивается в «колбасу».
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECK_SCRIPT = ROOT / "tests" / "js" / "path_layout_check.js"


@pytest.fixture(scope="module")
def report():
    node = shutil.which("node")
    if not node:
        pytest.skip("node не установлен — проверка JS-раскладки пропущена")
    out = subprocess.run([node, str(CHECK_SCRIPT)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_every_node_is_placed(report):
    for name, layouts in report.items():
        for layout, m in layouts.items():
            assert m["missing"] == [], (name, layout)


def test_tree_lines_never_cross(report):
    for name, layouts in report.items():
        for layout, m in layouts.items():
            assert m["treeCrossings"] == 0, (name, layout, m["treeCrossings"])


def test_foundation_and_topic_labels_do_not_overlap(report):
    for name, layouts in report.items():
        for layout, m in layouts.items():
            assert m["labelOverlapCount"] == 0, (name, layout, m["labelOverlaps"])


def test_real_subject_layouts_are_not_stretched_into_a_strip(report):
    for name in ("obshteorprava_v1", "obshteorprava_v3"):
        for layout in ("tree", "horizontal", "radial"):
            m = report[name][layout]
            ratio = max(m["width"], m["height"]) / max(1, min(m["width"], m["height"]))
            assert ratio <= 2.5, (name, layout, m["width"], m["height"])
