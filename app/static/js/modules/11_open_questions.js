// ============================================================================
// ВОПРОСЫ С ОТКРЫТЫМ ОТВЕТОМ: загрузка списка вопросов и панель ответа на тренировке.
// Проверка идёт на сервере по ключевым тезисам (без ИИ); оценку всегда выбирает пользователь.
// ============================================================================

// Мини-конструктор DOM: пользовательский текст попадает только через textContent (без innerHTML)
function oqEl(tag, props = {}, children = []) {
    const el = document.createElement(tag);
    Object.entries(props).forEach(([k, v]) => {
        if (k === 'class') el.className = v;
        else if (k === 'text') el.textContent = v;
        else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
        else if (v !== false && v != null) el.setAttribute(k, v === true ? '' : v);
    });
    [].concat(children).forEach(c => { if (c) el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c); });
    return el;
}

function oqErrorText(data, fallback) {
    const d = data && (data.detail || data.message);
    if (!d) return fallback;
    if (typeof d === 'string') return d;
    if (Array.isArray(d)) return d.map(e => e.msg || JSON.stringify(e)).join(', ');
    return d.message || fallback;
}

// ---------------------------------------------------------------------------
// Модалка загрузки вопросов
// ---------------------------------------------------------------------------
let oqPreviewTimer = null;
let oqPreviewSeq = 0;

window.openOpenQuestionsModal = function() {
    const modal = document.getElementById('oq-modal');
    if (!modal) return;
    const list = document.getElementById('oq-subject-list');
    const subjectInput = document.getElementById('oq-subject');
    if (list) {
        list.innerHTML = '';
        const seen = new Set();
        document.querySelectorAll('#import-target-subject option').forEach(opt => {
            if (!opt.value || opt.value === '__new__' || seen.has(opt.value)) return;
            seen.add(opt.value);
            list.appendChild(oqEl('option', { value: opt.value }));
        });
    }
    if (subjectInput && !subjectInput.value) {
        const cur = (typeof currentSubject !== 'undefined' && currentSubject && currentSubject !== 'all') ? currentSubject : '';
        subjectInput.value = cur;
    }
    modal.classList.remove('hidden');
    const text = document.getElementById('oq-text');
    if (text && !text.dataset.bound) {
        text.dataset.bound = '1';
        text.addEventListener('input', () => {
            clearTimeout(oqPreviewTimer);
            oqPreviewTimer = setTimeout(previewOpenQuestions, 450);
        });
        if (subjectInput) subjectInput.addEventListener('input', updateOqImportState);
    }
    updateOqImportState();
};

window.closeOpenQuestionsModal = function() {
    const modal = document.getElementById('oq-modal');
    if (modal) modal.classList.add('hidden');
};

let oqPreviewCount = 0;

function updateOqImportState() {
    const btn = document.getElementById('oq-import-btn');
    const subject = (document.getElementById('oq-subject')?.value || '').trim();
    if (btn) btn.disabled = !(oqPreviewCount > 0 && subject);
}

async function previewOpenQuestions() {
    const text = (document.getElementById('oq-text')?.value || '').trim();
    const box = document.getElementById('oq-preview');
    if (!box) return;
    if (!text) { oqPreviewCount = 0; box.textContent = ''; updateOqImportState(); return; }
    const seq = ++oqPreviewSeq;
    try {
        const res = await apiFetch('/api/open/preview', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text })
        });
        const data = await res.json();
        if (seq !== oqPreviewSeq) return; // устаревший ответ
        if (!res.ok) { box.textContent = oqErrorText(data, 'Не удалось разобрать текст.'); oqPreviewCount = 0; updateOqImportState(); return; }
        oqPreviewCount = data.count;
        const parts = [`Найдено вопросов: ${data.count}`];
        if (data.without_answer) parts.push(`без ответа: ${data.without_answer} (их придётся оценивать самому)`);
        box.textContent = parts.join(', ') + '.';
        const shown = (data.items || []).slice(0, 5);
        if (shown.length) {
            const ul = oqEl('ul', { class: 'oq-points', style: 'margin-top:8px' });
            shown.forEach(it => ul.appendChild(oqEl('li', { class: 'oq-point' },
                `${it.question.slice(0, 80)}${it.question.length > 80 ? '…' : ''} — тезисов: ${it.points}`)));
            box.appendChild(ul);
        }
    } catch (e) {
        if (seq === oqPreviewSeq) box.textContent = 'Нет связи с сервером. Предпросмотр недоступен.';
    }
    updateOqImportState();
}

window.importOpenQuestions = async function() {
    const btn = document.getElementById('oq-import-btn');
    const subject = (document.getElementById('oq-subject')?.value || '').trim().toLowerCase();
    const title = (document.getElementById('oq-title')?.value || '').trim() || 'Вопросы';
    const text = (document.getElementById('oq-text')?.value || '').trim();
    if (!subject || !text) return;
    if (btn) { btn.disabled = true; btn.textContent = 'Добавляю…'; }
    try {
        const res = await apiFetch('/api/open/import', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ subject, title, text })
        });
        const data = await res.json();
        if (!res.ok) { showToast(oqErrorText(data, 'Не удалось добавить вопросы.'), 'error'); return; }
        let msg = `Добавлено вопросов: ${data.created}.`;
        if (data.skipped_duplicates) msg += ` Уже были: ${data.skipped_duplicates}.`;
        if (data.without_answer) msg += ` Без ответа: ${data.without_answer}.`;
        showToast(msg + ' Они появятся в тренировке.', 'success');
        document.getElementById('oq-text').value = '';
        oqPreviewCount = 0;
        document.getElementById('oq-preview').textContent = '';
        closeOpenQuestionsModal();
        if (typeof loadDynamicSubjects === 'function') { try { await loadDynamicSubjects(); } catch (_) {} }
        if (typeof updateGlobalBadges === 'function') { try { updateGlobalBadges(); } catch (_) {} }
    } catch (e) {
        showToast('Нет связи с сервером. Попробуйте ещё раз.', 'error');
    } finally {
        if (btn) { btn.textContent = 'Добавить'; }
        updateOqImportState();
    }
};

// ---------------------------------------------------------------------------
// Панель ответа на тренировке
// ---------------------------------------------------------------------------
const OQ_RATINGS = [
    { r: 1, label: 'Снова', hint: 'мимо' },
    { r: 2, label: 'Трудно', hint: 'с трудом' },
    { r: 3, label: 'Хорошо', hint: 'вспомнил' },
    { r: 4, label: 'Легко', hint: 'без усилий' }
];

window.teardownOpenPanel = function() {
    const panel = document.getElementById('open-answer-panel');
    if (panel) panel.remove();
    const fc = document.getElementById('flashcard');
    if (fc) fc.classList.remove('hidden');
};

function oqFinish(rating) {
    window.teardownOpenPanel();
    window.submitCardRating(rating);
}

window.renderOpenCard = function(card) {
    const fc = document.getElementById('flashcard');
    if (!fc) return;
    window.teardownOpenPanel();
    fc.classList.add('hidden');
    const actions = document.getElementById('action-buttons');
    if (actions) { actions.classList.add('hidden'); actions.classList.remove('flex'); }
    if (typeof cardShowTimestamp !== 'undefined') cardShowTimestamp = Date.now();

    const panel = oqEl('div', { id: 'open-answer-panel', class: 'oq-panel', role: 'group', 'aria-label': 'Вопрос с открытым ответом' });
    fc.parentNode.insertBefore(panel, fc.nextSibling);
    renderOpenAsk(panel, card);
};

function renderOpenAsk(panel, card) {
    panel.replaceChildren();
    const sub = card.subject_title || '';
    panel.appendChild(oqEl('span', { class: 'oq-badge', text: sub ? `Открытый вопрос · ${sub}` : 'Открытый вопрос' }));
    panel.appendChild(oqEl('div', { class: 'oq-question', text: card.text }));

    const textarea = oqEl('textarea', {
        class: 'oq-textarea', maxlength: '3000', rows: '5', 'aria-label': 'Ваш ответ',
        placeholder: 'Напишите ответ своими словами…'
    });
    const checkBtn = oqEl('button', { type: 'button', class: 'oq-btn oq-btn-primary', text: 'Проверить', disabled: true });
    const showBtn = oqEl('button', { type: 'button', class: 'oq-btn', text: 'Не помню' });
    textarea.addEventListener('input', () => { checkBtn.disabled = !textarea.value.trim(); });
    textarea.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && textarea.value.trim()) checkBtn.click();
    });

    const run = async (skip) => {
        checkBtn.disabled = showBtn.disabled = true;
        checkBtn.textContent = skip ? checkBtn.textContent : 'Проверяю…';
        let result = null;
        try {
            const res = await apiFetch('/api/open/check', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ card_id: card.id, answer: skip ? '' : textarea.value })
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            result = await res.json();
        } catch (e) {
            // Нет связи: эталон уже в карточке, поэтому не блокируем занятие — разбор вручную, оценка своя
            result = { graded: false, offline: true, reference: card.translation || '', points: [], suggested_rating: 1 };
        }
        renderOpenResult(panel, card, textarea.value, result, skip);
    };
    checkBtn.addEventListener('click', () => run(false));
    showBtn.addEventListener('click', () => run(true));

    panel.appendChild(textarea);
    panel.appendChild(oqEl('div', { class: 'oq-row' }, [showBtn, checkBtn]));
    setTimeout(() => { try { textarea.focus(); } catch (_) {} }, 50);
}

function renderOpenResult(panel, card, userAnswer, result, skipped) {
    panel.replaceChildren();
    panel.appendChild(oqEl('span', { class: 'oq-badge', text: 'Разбор ответа' }));
    panel.appendChild(oqEl('div', { class: 'oq-question', text: card.text }));

    const live = oqEl('div', { 'aria-live': 'polite' });
    panel.appendChild(live);

    let suggested = result.suggested_rating || 1;
    if (result.graded && !skipped) {
        live.appendChild(oqEl('div', { class: 'oq-note', text: `Совпало тезисов: ${result.matched} из ${result.total} (${result.percent}%)` }));
        const meter = oqEl('div', { class: 'oq-meter', role: 'img', 'aria-label': `Совпадение ${result.percent}%` }, [oqEl('span')]);
        meter.firstChild.style.width = `${result.percent}%`;
        live.appendChild(meter);

        const ul = oqEl('ul', { class: 'oq-points', style: 'margin-top:8px' });
        (result.points || []).forEach(p => {
            const cls = p.matched ? 'oq-point-ok' : (p.partial ? 'oq-point-part' : 'oq-point-miss');
            const mark = p.matched ? '✓' : (p.partial ? '≈' : '✗');
            const sr = p.matched ? 'есть' : (p.partial ? 'частично' : (p.negated ? 'сказано наоборот' : 'нет'));
            ul.appendChild(oqEl('li', { class: `oq-point ${cls}` }, [
                oqEl('span', { 'aria-hidden': 'true', text: mark }),
                oqEl('span', { text: p.text }),
                oqEl('span', { class: 'oq-note', style: 'margin-left:auto;white-space:nowrap', text: sr })
            ]));
        });
        panel.appendChild(ul);
    } else if (skipped) {
        suggested = 1;
        live.appendChild(oqEl('div', { class: 'oq-note', text: 'Ответ ниже. Прочитайте и оцените, насколько он был вам знаком.' }));
    } else if (result.offline) {
        live.appendChild(oqEl('div', { class: 'oq-note', text: 'Нет связи, автоматическая проверка недоступна. Сверьте свой ответ с эталоном и оцените сами.' }));
        if (userAnswer && userAnswer.trim()) {
            panel.appendChild(oqEl('div', { class: 'oq-note', text: 'Ваш ответ' }));
            panel.appendChild(oqEl('div', { class: 'oq-reference', text: userAnswer }));
        }
    } else {
        live.appendChild(oqEl('div', { class: 'oq-note', text: 'Для этого вопроса нет ключевых тезисов, автоматическая проверка невозможна. Оцените сами.' }));
    }

    if (result.reference) {
        panel.appendChild(oqEl('div', { class: 'oq-note', text: 'Эталонный ответ' }));
        panel.appendChild(oqEl('div', { class: 'oq-reference', text: result.reference }));
    } else {
        panel.appendChild(oqEl('div', { class: 'oq-note', text: 'Эталонного ответа нет: у этой карточки его не задали.' }));
    }

    panel.appendChild(oqEl('div', { class: 'oq-note', text: 'Оценку выбираете вы: программа сверяет слова и не понимает пересказ без общих слов.' }));
    const rate = oqEl('div', { class: 'oq-rate', role: 'group', 'aria-label': 'Оценка ответа' });
    OQ_RATINGS.forEach(({ r, label, hint }) => {
        const b = oqEl('button', { type: 'button', class: `oq-btn${r === suggested && result.graded ? ' oq-suggested' : ''}`,
            title: r === suggested && result.graded ? 'Рекомендуем' : hint }, [label]);
        b.addEventListener('click', () => oqFinish(r));
        rate.appendChild(b);
    });
    panel.appendChild(rate);
}
