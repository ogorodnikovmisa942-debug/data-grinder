"""Собранные app.js/index.html обязаны содержать новые модули и быть синхронны с исходниками."""
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def test_app_js_is_in_sync_with_modules():
    bundled = "".join(p.read_text(encoding="utf-8") for p in sorted((STATIC / "js" / "modules").glob("*.js")))
    assert (STATIC / "js" / "app.js").read_text(encoding="utf-8") == bundled, "запустите приложение/бандлер: app.js устарел"


def test_bundle_contains_reliability_and_open_question_features():
    js = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    for needle in ("window.queueAnswer", "flushAnswerQueue", "window.showToast", "window.renderOpenCard",
                   "fetchWithReplaceConfirm", "renderSessionError", "syncUserTimezone"):
        assert needle in js, needle
    # все отправки ответов идут через очередь
    assert js.count("apiFetch('/api/answer'") == 1
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="oq-modal"' in html and "openOpenQuestionsModal()" in html
