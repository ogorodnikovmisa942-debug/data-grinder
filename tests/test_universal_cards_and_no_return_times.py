"""Регрессионные тесты универсальных карточек, Partial Blur, TTS, уроков и отсутствия времени возврата."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.frontend_bundler import bundle_all

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "app" / "static" / "index.html"
APP_JS = ROOT / "app" / "static" / "js" / "app.js"
MAIN_CSS = ROOT / "app" / "static" / "css" / "main.css"
CORE_JS = ROOT / "app" / "static" / "js" / "modules" / "01_core.js"
TRAIN_JS = ROOT / "app" / "static" / "js" / "modules" / "02_train.js"
STAGING_JS = ROOT / "app" / "static" / "js" / "modules" / "04_staging.js"
STAGING_HTML = ROOT / "app" / "static" / "components" / "screen_staging.html"


def test_frontend_bundle_sync_and_new_ui_primitives():
    bundle_all()
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")
    css = MAIN_CSS.read_text(encoding="utf-8")
    staging_html = STAGING_HTML.read_text(encoding="utf-8")
    train_js = TRAIN_JS.read_text(encoding="utf-8")
    staging_js = STAGING_JS.read_text(encoding="utf-8")

    # Новые элементы адаптивного дизайна присутствуют в собранном фронтенде
    for token in (
        'id="lesson-visual-slot"',
        'id="card-front-speak-pill"',
        'id="card-sec-scaffold-btn"',
        'id="card-sec-reveal-btn"',
        'id="card-example-speak-btn"',
        'id="card-view-speak-btn"',
        'id="staging-card-speak-btn"',
        '<option value="language">',
        'data-no-swipe',
    ):
        assert token in html, f"Отсутствует элемент {token!r} в index.html"

    for cls in (
        ".blur-chip",
        ".blur-token",
        ".blur-action-btn",
        ".dg-example-box",
        ".dg-speak-pill",
        ".lesson-visual-slot",
        ".lesson-contrast-grid",
    ):
        assert cls in css, f"Отсутствует CSS-класс {cls!r} в main.css"

    # Старые desktop hover-blur и синяя рамка права удалены
    assert ".template-chinese #card-secondary-text" not in css
    assert ".template-law #card-main-text" not in css

    # Защита от конфликта свайпа с tap-чипами и кнопками озвучки
    assert "[data-no-swipe]" in train_js
    assert "[data-no-swipe]" in staging_js

    # Песочница не использует янтарную подсветку мнемоник ни в компоненте, ни в JS-шаблоне
    assert "bg-amber-500" not in staging_html
    assert "text-amber-700" not in staging_html
    assert 'class="mnemonic-box' in staging_html
    assert 'id="staging-card-speak-btn"' in staging_html and 'id="staging-card-speak-btn"' in train_js

    # Универсальные JS-функции доступны в бандле
    for symbol in (
        "window.detectCardDomain =",
        "window.detectSpeechLang =",
        "window.isSpeakable =",
        "window.executeVoiceSynthesis =",
        "window.renderPartialBlurChipsHTML =",
        "window.renderExampleTokensHTML =",
        "function renderLessonVisualSlot(",
    ):
        assert symbol in js, f"Отсутствует символ {symbol!r} в app.js"


def test_no_card_return_time_or_interval_clutter_anywhere_in_ui():
    bundle_all()
    html = INDEX_HTML.read_text(encoding="utf-8")
    js = APP_JS.read_text(encoding="utf-8")

    for forbidden_html in (
        "data-cnt-evening",
        "debrief-stat-evening",
        "К ВЕЧЕРУ",
        "К вечеру",
        "card-details-accordion",
        "Норма закона и пример",
        "СТАТЬЯ ЗАКОНА / НОРМА",
        "Описание и правовая норма",
    ):
        assert forbidden_html not in html, f"Лишний/устаревший UI-фрагмент остался в index.html: {forbidden_html!r}"

    for forbidden_js in (
        "describeNextReview",
        "следующее повторение:",
        "прочность ≈",
        "debrief-stat-evening",
        "data-cnt-evening",
        "eveningDueCount",
        "повторим пораньше",
        "Эту вернём в повторениях",
        "Детальное описание и законодательное основание отсутствуют",
    ):
        assert forbidden_js not in js, f"Лишняя логика времени возврата / доменного перекоса осталась в app.js: {forbidden_js!r}"

    # Кнопки оценки FSRS остаются качественными, без прогноза минут/дней возврата
    rate_block_match = re.search(r'id="action-buttons"[^>]*>(.*?)</div>', html, re.DOTALL)
    assert rate_block_match is not None
    rate_block = rate_block_match.group(1)
    for label in ("Забыл", "Трудно", "Помню", "Легко"):
        assert label in rate_block
    assert not re.search(r"\b\d+\s*(?:мин|ч|дн)\b", rate_block)


def test_core_js_domain_partial_blur_and_tts_behavior_in_node():
    node_bin = shutil.which("node")
    if not node_bin:
        pytest.skip("Node.js недоступен")

    core_code = CORE_JS.read_text(encoding="utf-8")
    node_script = """
const vm = require('vm');
const fs = require('fs');

const spoken = [];
const _esc = (s) => String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
const sandbox = {
    console,
    setTimeout: () => 0,
    clearTimeout: () => {},
    URLSearchParams,
    Intl,
    fetch: () => Promise.resolve({ ok: false, status: 500 }),
    currentSubject: 'all',
    window: {
        alert: () => {},
        confirm: () => true,
        addEventListener: () => {},
        location: { protocol: 'http:', hostname: 'localhost', port: '8000', search: '', hash: '' },
        speechSynthesis: {
            cancel: () => {},
            speak: (u) => spoken.push({ text: u.text, lang: u.lang, rate: u.rate }),
        },
    },
    document: {
        readyState: 'complete',
        activeElement: null,
        body: { appendChild: () => {} },
        head: { appendChild: () => {} },
        documentElement: { style: { setProperty: () => {} } },
        addEventListener: () => {},
        querySelectorAll: () => [],
        getElementById: () => null,
        createElement: () => ({
            _txt: '',
            style: {},
            set textContent(v) { this._txt = String(v ?? ''); },
            get textContent() { return this._txt; },
            get innerHTML() { return _esc(this._txt); },
            setAttribute: () => {},
            appendChild: () => {},
        }),
    },
    localStorage: {
        _s: {},
        getItem(k) { return this._s[k] ?? null; },
        setItem(k, v) { this._s[k] = String(v); },
    },
    location: { protocol: 'http:', hostname: 'localhost', port: '8000', search: '', hash: '' },
    SpeechSynthesisUtterance: function(text) { this.text = text; this.lang = ''; this.rate = 1; },
};
sandbox.window.window = sandbox.window;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf-8'), sandbox);

const w = sandbox.window;

// 1. Определение домена карточки (в т.ч. при currentSubject === 'all')
const dCjk = w.detectCardDomain({ subject: 'all', text: '休息', secondary_text: 'xiū xī | глагол', translation: 'отдыхать' });
if (dCjk.key !== 'language' || !dCjk.isChinese || dCjk.langCode !== 'zh-CN') {
    throw new Error('CJK domain failed: ' + JSON.stringify(dCjk));
}
if (dCjk.labels.main !== 'ПЕРЕВОД И ЗНАЧЕНИЕ' || dCjk.labels.sec !== 'ЧТЕНИЕ (ПИНЬИНЬ)') {
    throw new Error('CJK labels failed: ' + JSON.stringify(dCjk.labels));
}

const dEng = w.detectCardDomain({ subject: 'english_b2', text: 'Resilience', translation: 'устойчивость' });
if (dEng.key !== 'language' || dEng.langCode !== 'en-US' || dEng.labels.sec !== 'ЧТЕНИЕ / ФОРМА') {
    throw new Error('English domain failed: ' + JSON.stringify(dEng));
}

const dLaw = w.detectCardDomain({ subject: 'sudoustroystvo', text: 'Полномочия суда', secondary_text: 'ч. 1 ст. 118 Конституции РФ', translation: 'правовое правило' });
if (dLaw.key !== 'law' || dLaw.labels.main !== 'ПРАВОВОЕ ПРАВИЛО' || dLaw.labels.sec !== 'НОРМА И УСЛОВИЕ') {
    throw new Error('Law domain failed: ' + JSON.stringify(dLaw));
}

const dStem = w.detectCardDomain({ subject: 'physics_10', text: 'Второй закон Ньютона', secondary_text: 'F = m · a | единица: Н', translation: 'Сила равна произведению массы на ускорение' });
if (dStem.key !== 'stem' || dStem.labels.main !== 'СУТЬ И ЗАКОН' || dStem.labels.sec !== 'ФОРМУЛА / СИ') {
    throw new Error('STEM domain failed: ' + JSON.stringify(dStem));
}

const dGen = w.detectCardDomain({ subject: 'art_history', text: 'Импрессионизм', secondary_text: 'XIX век | Франция', translation: 'направление в живописи' });
if (dGen.key !== 'general' || dGen.labels.main !== 'ОТВЕТ / СУТЬ') {
    throw new Error('General domain failed: ' + JSON.stringify(dGen));
}

// 2. Универсальный TTS: языки работают при currentSubject === 'all', а чисто русский вопрос в англ. курсе не озвучивается английским
if (!w.isSpeakable('我要休息一下。(Мне нужно немного отдохнуть.)', dCjk)) {
    throw new Error('Mixed CJK+RU example must be speakable');
}
w.executeVoiceSynthesis('我要休息一下。(Мне нужно немного отдохнуть.)', { subject: 'all', text: '休息' });
if (!spoken.length || spoken[0].lang !== 'zh-CN' || spoken[0].text.includes('отдохнуть')) {
    throw new Error('TTS failed to strip Russian parenthetical: ' + JSON.stringify(spoken));
}
if (w.isSpeakable('Как образуется прошедшее время?', { subject: 'english_b2' })) {
    throw new Error('Pure Cyrillic prompt should not trigger English TTS');
}
if (w.isSpeakable('Когда используется Past Simple?', { subject: 'english_b2' })) {
    throw new Error('Mostly Cyrillic prompt should not trigger English TTS');
}
spoken.length = 0;
w.executeVoiceSynthesis('to give up — сдаваться', { subject: 'english_b2', text: 'to give up' });
if (!spoken.length || spoken[0].lang !== 'en-US' || spoken[0].text !== 'to give up') {
    throw new Error('Bilingual English TTS extraction failed: ' + JSON.stringify(spoken));
}

// 3. Каркас и Partial Blur чипы (безопасное экранирование + data-no-swipe)
const scPin = w.makeScaffoldHint('xiū xī', 'language');
if (!scPin.startsWith('x') || scPin.includes('xiū')) {
    throw new Error('Bad pinyin scaffold: ' + scPin);
}
const scStem = w.makeScaffoldHint('F = m · a', 'stem');
if (!scStem.startsWith('F =')) {
    throw new Error('Bad stem scaffold: ' + scStem);
}
const chips = w.renderPartialBlurChipsHTML('xiū xī | ключ: <script>x</script>', dCjk, { masked: true });
if (!chips.includes('data-no-swipe') || !chips.includes('blur-chip-masked') || chips.includes('<script>')) {
    throw new Error('Bad blur chips HTML: ' + chips);
}

// 4. Частичное скрытие слова в примере (и для CJK, и для кириллицы без ложного совпадения внутри слов)
const cjkEx = w.renderExampleTokensHTML('我要休息一下。', { text: '休息' }, { masked: true });
if (!cjkEx.includes('blur-token is-masked') || !cjkEx.includes('data-no-swipe') || !cjkEx.includes('我要')) {
    throw new Error('Bad CJK example masking: ' + cjkEx);
}
const ruEx = w.renderExampleTokensHTML('Договор действует бессрочно.', { text: 'Договор' }, { masked: true });
if (!ruEx.includes('blur-token is-masked')) {
    throw new Error('Cyrillic whole-word example masking failed: ' + ruEx);
}
const ruNoFalsePositive = w.renderExampleTokensHTML('Стороны решили передоговориться.', { text: 'договор' }, { masked: true });
if (ruNoFalsePositive.includes('blur-token')) {
    throw new Error('Cyrillic substring matched inside another word: ' + ruNoFalsePositive);
}

console.log(JSON.stringify({ ok: true }));
"""
    res = subprocess.run(
        [node_bin, "-e", node_script, str(CORE_JS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert res.returncode == 0, f"Node failed:\nSTDERR:\n{res.stderr}\nSTDOUT:\n{res.stdout}"
    data = json.loads(res.stdout.strip())
    assert data["ok"] is True
