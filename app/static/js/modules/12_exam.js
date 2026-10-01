
// ============================================================================
// ПОДГОТОВКА К ЭКЗАМЕНУ ПО БИЛЕТАМ
// Список билетов + дата → сервер (ИИ один раз) находит билеты в графе книги и собирает
// эталоны из карточек → план по дням. Занятие ведёт тот же кот, scope=exam.
// ============================================================================

const examUi = { subject: '', data: null, view: '', pollTimer: null, previewTimer: null };

function examPlural(n, one, few, many) {
    return typeof pathRunPlural === 'function' ? pathRunPlural(n, one, few, many) : many;
}

function examActiveSubject() {
    return typeof getActiveDeckSubject === 'function' ? getActiveDeckSubject() : currentSubject;
}

function setExamCat(emo, text) {
    if (typeof setCatWidget === 'function') setCatWidget('exam-cat', 'exam-say', emo, text);
    const cat = document.getElementById('exam-cat');
    if (cat) cat.classList.remove('intro-cat');
}

function setExamButtons(primary, secondary, primaryEnabled = true) {
    const p = document.getElementById('exam-primary');
    const s = document.getElementById('exam-secondary');
    if (p) { p.textContent = primary; p.disabled = !primaryEnabled; }
    if (s) { s.textContent = secondary || ''; s.classList.toggle('hidden', !secondary); }
}

function showExamSection(name) {
    ['setup', 'review'].forEach(v => {
        const el = document.getElementById(`exam-${v}`);
        if (el) el.classList.toggle('hidden', v !== name);
    });
}

// Состояние плана (используется и стартовым экраном)
window.loadExamOverview = async function(subject) {
    if (!subject || subject === 'all') return { active: false };
    try {
        const res = await apiFetch(`/api/exam/${encodeURIComponent(subject)}`);
        if (!res.ok) return { active: false };
        return await res.json();
    } catch (_) {
        return { active: false };
    }
};

window.openExamModal = async function() {
    const subject = examActiveSubject();
    if (!subject || subject === 'all') {
        alert('Сначала выбери предмет.');
        return;
    }
    examUi.subject = subject;
    const modal = document.getElementById('exam-modal');
    if (modal) modal.classList.remove('hidden');
    triggerHaptic('light');
    showExamSection(null);
    setExamCat('think', 'Секунду, смотрю твои билеты…');
    setExamButtons('…', 'Закрыть', false);
    renderExam(await loadExamOverview(subject));
};

window.closeExamModal = function() {
    clearTimeout(examUi.pollTimer);
    const modal = document.getElementById('exam-modal');
    if (modal) modal.classList.add('hidden');
    if (typeof refreshPathRunButton === 'function') refreshPathRunButton();
};

function examModalOpen() {
    const modal = document.getElementById('exam-modal');
    return modal && !modal.classList.contains('hidden');
}

function renderExam(data) {
    clearTimeout(examUi.pollTimer);
    examUi.data = data;
    if (!data || !data.active) return showExamSetup();
    if (data.status === 'matching') return showExamWait(data);
    if (data.status === 'failed') return showExamFailed(data);
    return showExamReview(data);
}

// --- 1. Список билетов и дата ---
function showExamSetup(error = '') {
    examUi.view = 'setup';
    showExamSection('setup');
    const date = document.getElementById('exam-date');
    if (date) {
        const today = new Date();
        const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
        date.min = iso(today);
        if (!date.value) date.value = iso(new Date(today.getTime() + 14 * 86400000));
    }
    const text = document.getElementById('exam-text');
    if (text && !text._bound) {
        text._bound = true;
        text.addEventListener('input', () => {
            clearTimeout(examUi.previewTimer);
            examUi.previewTimer = setTimeout(previewExamTickets, 400);
        });
    }
    showExamError(error);
    setExamCat('talk', 'Скинь список билетов и дату экзамена. Я найду каждый билет в твоей книге, соберу ответ из карточек и разложу темы по дням — к экзамену успеешь всё.');
    setExamButtons('Разобрать билеты', 'Отмена', !!(text && text.value.trim()));
}

function showExamError(message) {
    const el = document.getElementById('exam-error');
    if (!el) return;
    el.textContent = message || '';
    el.classList.toggle('hidden', !message);
}

async function previewExamTickets() {
    const text = document.getElementById('exam-text');
    const out = document.getElementById('exam-count');
    const value = text ? text.value.trim() : '';
    setExamButtons('Разобрать билеты', 'Отмена', !!value);
    if (!out || !value) return;
    try {
        const res = await apiFetch(`/api/exam/${encodeURIComponent(examUi.subject)}/preview`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: value })
        });
        if (!res.ok) return;
        const p = await res.json();
        out.textContent = p.count
            ? `Вижу ${p.count} ${examPlural(p.count, 'билет', 'билета', 'билетов')}${p.with_answers ? `, у ${p.with_answers} есть твой эталон` : ''}.`
            : 'Пока не вижу ни одного билета. Пиши по билету на строку.';
        setExamButtons('Разобрать билеты', 'Отмена', p.count > 0);
    } catch (_) { /* подсчёт не критичен */ }
}

async function submitExamPlan() {
    const date = document.getElementById('exam-date');
    const text = document.getElementById('exam-text');
    if (!date || !date.value) return showExamError('Укажи дату экзамена.');
    if (!text || !text.value.trim()) return showExamError('Вставь список билетов.');
    setExamButtons('Отправляю…', 'Отмена', false);
    try {
        const res = await apiFetch(`/api/exam/${encodeURIComponent(examUi.subject)}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ title: 'Билеты', exam_date: date.value, text: text.value })
        });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) {
            setExamButtons('Разобрать билеты', 'Отмена', true);
            return showExamError(body.detail || 'Не получилось сохранить билеты.');
        }
        triggerHaptic('success');
        text.value = '';
        renderExam(await loadExamOverview(examUi.subject));
    } catch (_) {
        setExamButtons('Разобрать билеты', 'Отмена', true);
        showExamError('Нет связи с сервером. Попробуй ещё раз.');
    }
}

// --- Разбор идёт в фоне ---
function showExamWait(data) {
    examUi.view = 'wait';
    showExamSection(null);
    const n = (data.tickets && data.tickets.total) || 0;
    setExamCat('think', `Читаю ${n} ${examPlural(n, 'билет', 'билета', 'билетов')} и ищу их в твоей книге. Обычно это меньше минуты — можно закрыть окно, я доделаю сам.`);
    setExamButtons('Разбираю…', 'Закрыть', false);
    examUi.pollTimer = setTimeout(async () => {
        if (!examModalOpen()) return;
        renderExam(await loadExamOverview(examUi.subject));
    }, 3000);
}

function showExamFailed(data) {
    examUi.view = 'failed';
    showExamSection(null);
    setExamCat('confused', `${data.error || 'Разбор не удался.'} Повторим?`);
    setExamButtons('Повторить', 'Новый список', true);
}

async function retryExamPlan() {
    setExamButtons('…', null, false);
    try {
        await apiFetch(`/api/exam/${encodeURIComponent(examUi.subject)}/retry`, { method: 'POST' });
    } catch (_) { /* покажем состояние ниже */ }
    renderExam(await loadExamOverview(examUi.subject));
}

// --- 2. Готовый план ---
const EXAM_TICKET_STATE = {
    strong:  { icon: 'verified',     cls: 'exam-st-strong',  label: 'закреплён' },
    answered:{ icon: 'edit_note',    cls: 'exam-st-answered', label: 'отвечен' },
    ready:   { icon: 'assignment',   cls: 'exam-st-ready',   label: 'готов к ответу' },
    waiting: { icon: 'schedule',     cls: 'exam-st-waiting', label: 'ждёт своих тем' },
    missing: { icon: 'help',         cls: 'exam-st-missing', label: 'нет в книге' },
    skipped: { icon: 'block',        cls: 'exam-st-skipped', label: 'пропущен' }
};

function ticketState(t) {
    if (t.status === 'missing') return 'missing';
    if (t.status === 'skipped') return 'skipped';
    if (t.strong) return 'strong';
    if (t.answered) return 'answered';
    return t.ready ? 'ready' : 'waiting';
}

function showExamReview(data) {
    examUi.view = 'review';
    showExamSection('review');
    const t = data.tickets || {};
    const d = data.days_left;
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
    set('exam-stat-days', d < 0 ? '—' : d);
    set('exam-stat-ready', `${t.strong || 0}/${t.ok || 0}`);
    set('exam-stat-today', data.today ? `${data.today.lessons}/${data.today.quota}` : '—');
    const fill = document.getElementById('exam-progress-fill');
    if (fill) fill.style.width = data.nodes && data.nodes.required ? `${Math.round(100 * data.nodes.done / data.nodes.required)}%` : '0%';

    let say;
    if (data.phase === 'past') {
        say = 'Экзамен уже прошёл. Как всё прошло? Режим можно выключить — отвеченные билеты останутся в повторениях.';
    } else if (t.missing) {
        say = `Нашёл в книге ${t.ok} из ${t.total}. Для ${t.missing} ${examPlural(t.missing, 'билета', 'билетов', 'билетов')} материала в книге нет — впиши свой эталон или пропусти, выдумывать ответ я не буду.`;
    } else {
        const q = data.today ? data.today.quota : 0;
        const pace = q ? `План: ${q} ${examPlural(q, 'тема', 'темы', 'тем')} в день, ` : 'Все нужные темы пройдены, ';
        say = data.phase === 'drill'
            ? `До экзамена ${d} ${examPlural(d, 'день', 'дня', 'дней')} — время прогона: гоняем билеты, начиная с самых шатких.`
            : `До экзамена ${d} ${examPlural(d, 'день', 'дня', 'дней')}. ${pace}после каждой темы — её билет письменно, последние 2 дня — прогон всех билетов.`;
    }
    setExamCat(t.missing ? 'think' : 'happy', say);

    renderExamMissing(data.items || []);
    renderExamList(data.items || []);
    setExamButtons(data.phase === 'past' ? 'Закрыть' : 'Начать подготовку', 'Выключить режим', true);
}

function renderExamMissing(items) {
    const wrap = document.getElementById('exam-missing-wrap');
    const box = document.getElementById('exam-missing');
    if (!wrap || !box) return;
    const missing = items.filter(x => x.status === 'missing');
    wrap.classList.toggle('hidden', !missing.length);
    box.replaceChildren();
    missing.forEach(item => {
        const el = document.createElement('div');
        el.className = 'exam-missing-item';
        const q = document.createElement('p');
        q.className = 'exam-missing-q';
        q.textContent = `${item.n}. ${item.question}`;
        const area = document.createElement('textarea');
        area.className = 'oq-modal-field select-text';
        area.rows = 3;
        area.maxLength = 3000;
        area.placeholder = '- первый тезис / как ещё можно сказать\n- второй тезис';
        const row = document.createElement('div');
        row.className = 'oq-row';
        const skip = document.createElement('button');
        skip.type = 'button';
        skip.className = 'oq-btn';
        skip.textContent = 'Пропустить';
        const save = document.createElement('button');
        save.type = 'button';
        save.className = 'oq-btn oq-btn-primary';
        save.textContent = 'Сохранить';
        save.disabled = true;
        area.addEventListener('input', () => { save.disabled = !area.value.trim(); });
        skip.onclick = () => examTicketAction(item.id, 'skip', null, [skip, save]);
        save.onclick = () => examTicketAction(item.id, 'answer', area.value, [skip, save]);
        row.append(skip, save);
        el.append(q, area, row);
        box.appendChild(el);
    });
}

async function examTicketAction(id, action, answer, buttons) {
    buttons.forEach(b => { b.disabled = true; });
    try {
        const res = await apiFetch(`/api/exam/ticket/${id}/${action}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: action === 'answer' ? JSON.stringify({ answer }) : '{}'
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        triggerHaptic(action === 'answer' ? 'success' : 'light');
    } catch (_) {
        buttons.forEach(b => { b.disabled = false; });
        return;
    }
    renderExam(await loadExamOverview(examUi.subject));
}

function renderExamList(items) {
    const list = document.getElementById('exam-list');
    if (!list) return;
    list.replaceChildren();
    items.forEach(item => {
        const st = EXAM_TICKET_STATE[ticketState(item)];
        const li = document.createElement('li');
        li.className = 'exam-item';
        const icon = document.createElement('span');
        icon.className = `material-symbols-outlined exam-item-icon ${st.cls}`;
        icon.textContent = st.icon;
        icon.title = st.label;
        const body = document.createElement('div');
        body.className = 'exam-item-body';
        const q = document.createElement('span');
        q.className = 'exam-item-q';
        q.textContent = `${item.n}. ${item.question}`;
        const meta = document.createElement('span');
        meta.className = 'exam-item-meta';
        meta.textContent = item.nodes && item.nodes.length ? `${st.label} · ${item.nodes.join(', ')}` : st.label;
        body.append(q, meta);
        li.append(icon, body);
        list.appendChild(li);
    });
}

// --- Кнопки ---
window.examPrimary = function() {
    if (examUi.view === 'setup') return submitExamPlan();
    if (examUi.view === 'failed') return retryExamPlan();
    if (examUi.view === 'review') {
        const subject = examUi.subject;
        const past = examUi.data && examUi.data.phase === 'past';
        closeExamModal();
        if (!past) window.pathRun.start(subject, { scope: 'exam' });
    }
};

window.examSecondary = async function() {
    if (examUi.view === 'failed') return showExamSetup();
    if (examUi.view === 'review') {
        if (!confirm('Выключить режим экзамена? Отвеченные билеты останутся в повторениях.')) return;
        try {
            await apiFetch(`/api/exam/${encodeURIComponent(examUi.subject)}/off`, { method: 'POST' });
        } catch (_) { /* закроем всё равно */ }
        triggerHaptic('medium');
    }
    closeExamModal();
};
