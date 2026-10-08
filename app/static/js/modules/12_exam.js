
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
    ['setup', 'review', 'sim'].forEach(v => {
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
    clearInterval(examSim.timer);
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
    skipped: { icon: 'block',        cls: 'exam-st-skipped', label: 'пропущен' },
    postponed: { icon: 'pause_circle', cls: 'exam-st-postponed', label: 'отложен' }
};

function ticketState(t) {
    if (t.status === 'postponed') return 'postponed';
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

    renderExamReadiness(data);
    renderExamEmergency(data);
    const simBtn = document.getElementById('exam-sim-btn');
    if (simBtn) simBtn.classList.toggle('hidden', data.phase === 'past' || !(data.tickets && data.tickets.ok));
    renderExamMissing(data.items || []);
    renderExamList(data.items || []);
    setExamButtons(data.phase === 'past' ? 'Закрыть' : 'Начать подготовку', 'Выключить режим', true);
}

// --- Готовность и прогноз ---
function renderExamReadiness(data) {
    const box = document.getElementById('exam-readiness');
    if (!box) return;
    const r = data.readiness;
    box.classList.toggle('hidden', !r || data.phase === 'past');
    if (!r) return;
    const num = document.getElementById('exam-readiness-num');
    const note = document.getElementById('exam-forecast');
    if (num) num.textContent = `${r.percent}%`;
    if (!note) return;
    if (data.phase === 'drill') {
        note.textContent = 'Идёт прогон билетов: закрепляй самые шаткие, новые темы уже не успеть.';
    } else if (r.will_make_it) {
        note.textContent = `При нынешнем темпе успеешь пройти все нужные темы: все ${r.tickets_total} ${examPlural(r.tickets_total, 'билет', 'билета', 'билетов')} будут закрыты до прогона.`;
    } else {
        note.textContent = `При нынешнем темпе (${r.pace} тем в день) успеешь закрыть ${r.tickets_closable} из ${r.tickets_total} ${examPlural(r.tickets_total, 'билета', 'билетов', 'билетов')}. Чтобы успеть всё, нужно ${r.needed_pace} тем в день.`;
    }
}

// --- Аварийный режим: если всё не успевается, отложить билеты, которые закрываются слишком дорого ---
function renderExamEmergency(data) {
    const box = document.getElementById('exam-emergency');
    if (!box) return;
    const e = data.emergency || {};
    const show = (e.needed && e.postpone > 0) || e.postponed_now > 0;
    box.classList.toggle('hidden', !show);
    if (!show) return;
    const text = document.getElementById('exam-emergency-text');
    const apply = document.getElementById('exam-emergency-apply');
    const undo = document.getElementById('exam-emergency-undo');
    const left = data.nodes ? data.nodes.required - data.nodes.done : 0;
    if (text) {
        text.textContent = e.needed && e.postpone > 0
            ? `Новых тем ${left}, а до прогона успеть можно около ${e.capacity}. Предлагаю отложить ${e.postpone} ${examPlural(e.postpone, 'билет', 'билета', 'билетов')}: остальные закрываются заметно меньшим числом тем.`
            : `Отложено билетов: ${e.postponed_now}. Они вне плана и вне расчёта готовности; вернуть можно в любой момент.`;
    }
    if (apply) apply.classList.toggle('hidden', !(e.needed && e.postpone > 0));
    if (undo) undo.classList.toggle('hidden', !(e.postponed_now > 0));
}

window.examEmergency = async function(action) {
    if (action === 'apply' && !confirm('Отложить часть билетов? Их можно вернуть позже, отвеченные остаются в повторениях.')) return;
    try {
        const res = await apiFetch(`/api/exam/${encodeURIComponent(examUi.subject)}/emergency/${action}`, { method: 'POST' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        triggerHaptic('medium');
    } catch (_) {
        alert('Не получилось. Проверь связь и попробуй ещё раз.');
        return;
    }
    renderExam(await loadExamOverview(examUi.subject));
};

// --- Повторный поиск билетов «нет в книге» после добавления материала ---
window.examRematch = async function() {
    try {
        const res = await apiFetch(`/api/exam/${encodeURIComponent(examUi.subject)}/rematch`, { method: 'POST' });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) { alert(body.detail || 'Не получилось.'); return; }
        setExamCat('think', 'Ищу недостающие билеты в обновлённой книге. Это меньше минуты.');
        const reload = async () => { if (examModalOpen() && examUi.view === 'review') renderExam(await loadExamOverview(examUi.subject)); };
        setTimeout(reload, 6000);
        setTimeout(reload, 15000);
    } catch (_) {
        alert('Нет связи с сервером.');
    }
};

// --- Список билетов из файла или с фото ---
function appendExamText(text) {
    const area = document.getElementById('exam-text');
    if (!area || !text) return;
    area.value = area.value.trim() ? `${area.value.trim()}\n${text}` : text;
    area.dispatchEvent(new Event('input'));
}

window.examLoadFile = async function(event) {
    const file = event.target.files && event.target.files[0];
    event.target.value = '';
    if (!file) return;
    showExamError('');
    const note = document.getElementById('exam-count');
    if (note) note.textContent = 'Читаю файл…';
    try {
        const fd = new FormData();
        fd.append('file', file);
        const res = await apiFetch('/api/exam/extract', { method: 'POST', body: fd });
        const body = await res.json().catch(() => ({}));
        if (!res.ok) return showExamError(body.detail || 'Не получилось прочитать файл.');
        appendExamText(body.text);
    } catch (_) {
        showExamError('Нет связи с сервером. Попробуй ещё раз.');
    }
};

window.examLoadPhoto = async function(event) {
    const files = event.target.files ? Array.from(event.target.files) : [];
    event.target.value = '';
    if (!files.length) return;
    showExamError('');
    const note = document.getElementById('exam-count');
    try {
        const text = await window.ocrImagesToText(files, msg => { if (note) note.textContent = msg; });
        if (!text.trim()) return showExamError('На фото не нашёлся читаемый текст. Сфотографируй список прямо и при хорошем свете.');
        appendExamText(text);
    } catch (e) {
        showExamError(`Не получилось распознать фото: ${e.message}`);
    }
};

// --- Симулятор экзамена ---
const examSim = { ticketId: 0, timer: null, deadline: 0, seen: [], submitted: false };

function examSimTick() {
    const el = document.getElementById('exam-sim-timer');
    if (!el) return;
    const left = Math.round((examSim.deadline - Date.now()) / 1000);
    const abs = Math.abs(left);
    el.textContent = `${left < 0 ? '−' : ''}${String(Math.floor(abs / 60)).padStart(2, '0')}:${String(abs % 60).padStart(2, '0')}`;
    el.classList.toggle('over', left < 0);
}

window.examSimStart = async function() {
    clearInterval(examSim.timer);
    let draw;
    try {
        const res = await apiFetch(`/api/exam/${encodeURIComponent(examUi.subject)}/simulator/draw?exclude=${examSim.seen.join(',')}`);
        const body = await res.json().catch(() => ({}));
        if (!res.ok) { alert(body.detail || 'Билет не вытянулся.'); return; }
        draw = body;
    } catch (_) {
        alert('Нет связи с сервером.');
        return;
    }
    examUi.view = 'sim';
    showExamSection('sim');
    examSim.ticketId = draw.ticket_id;
    examSim.submitted = false;
    examSim.seen.push(draw.ticket_id);
    if (examSim.seen.length >= draw.pool) examSim.seen = [draw.ticket_id];
    examSim.deadline = Date.now() + draw.seconds * 1000;
    document.getElementById('exam-sim-question').textContent = `Билет ${draw.n}. ${draw.question}`;
    const area = document.getElementById('exam-sim-answer');
    area.value = '';
    area.disabled = false;
    document.getElementById('exam-sim-result').classList.add('hidden');
    examSimTick();
    examSim.timer = setInterval(examSimTick, 1000);
    setExamCat('think', `Как на экзамене: ${Math.round(draw.seconds / 60)} минут на билет. Пиши ответ своими словами, потом я сверю его с тезисами из твоей книги.`);
    setExamButtons('Сдать ответ', 'Закончить', true);
};

async function examSimSubmit() {
    const area = document.getElementById('exam-sim-answer');
    clearInterval(examSim.timer);
    setExamButtons('Проверяю…', null, false);
    try {
        const res = await apiFetch(`/api/exam/ticket/${examSim.ticketId}/simulate`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ answer: area.value })
        });
        const r = await res.json();
        if (!res.ok) throw new Error(r.detail || `HTTP ${res.status}`);
        examSim.submitted = true;
        area.disabled = true;
        renderExamSimResult(r);
        triggerHaptic(r.percent >= 70 ? 'success' : 'light');
        setExamButtons('Ещё билет', 'Закончить', true);
    } catch (e) {
        setExamButtons('Сдать ответ', 'Закончить', true);
        alert(`Не получилось проверить: ${e.message}`);
    }
}

function renderExamSimResult(r) {
    const box = document.getElementById('exam-sim-result');
    box.replaceChildren();
    const head = document.createElement('p');
    head.className = 'text-sm font-bold';
    head.textContent = r.graded ? `Совпало с эталоном на ${r.percent}%: названо ${r.matched} из ${r.total} тезисов.` : 'У этого билета нет тезисов для проверки, сравни ответ с эталоном сам.';
    box.appendChild(head);
    const block = (title, items, cls) => {
        if (!items || !items.length) return;
        const h = document.createElement('p');
        h.className = `exam-note ${cls}`;
        h.textContent = title;
        const ul = document.createElement('ul');
        ul.className = 'exam-sim-list';
        items.forEach(t => { const li = document.createElement('li'); li.textContent = t; ul.appendChild(li); });
        box.append(h, ul);
    };
    block('Названо:', r.hit, 'exam-sim-hit');
    block('Названо не до конца:', r.partial, '');
    block('Упущено:', r.missed, 'exam-sim-miss');
    const ai = document.createElement('button');
    ai.type = 'button';
    ai.className = 'oq-btn';
    ai.textContent = 'Оценить по смыслу (ИИ)';
    ai.title = 'Платная возможность: ИИ сверит ответ с тезисами по смыслу, а не по словам';
    ai.onclick = () => examSimAiCheck(ai);
    box.appendChild(ai);
    box.classList.remove('hidden');
}

// ИИ-оценка по смыслу: платный тариф, месячная квота
async function examSimAiCheck(btn) {
    btn.disabled = true;
    btn.textContent = 'Оцениваю…';
    try {
        const res = await apiFetch(`/api/exam/ticket/${examSim.ticketId}/ai-check`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ answer: document.getElementById('exam-sim-answer').value })
        });
        const r = await res.json().catch(() => ({}));
        if (!res.ok) {
            alert(typeof apiErrorText === 'function' ? apiErrorText(r, 'Не получилось оценить.') : (r.detail || 'Не получилось оценить.'));
            btn.disabled = false;
            btn.textContent = 'Оценить по смыслу (ИИ)';
            return;
        }
        const box = document.getElementById('exam-sim-result');
        btn.remove();
        const wrap = document.createElement('div');
        wrap.className = 'exam-card';
        const head = document.createElement('p');
        head.className = 'text-sm font-bold';
        head.textContent = `По смыслу: ${r.score}%`;
        wrap.appendChild(head);
        const add = (title, items, cls) => {
            if (!items || !items.length) return;
            const h = document.createElement('p');
            h.className = `exam-note ${cls}`;
            h.textContent = title;
            const ul = document.createElement('ul');
            ul.className = 'exam-sim-list';
            items.forEach(t => { const li = document.createElement('li'); li.textContent = t; ul.appendChild(li); });
            wrap.append(h, ul);
        };
        add('Сказано верно:', r.covered, 'exam-sim-hit');
        add('Сказано не до конца:', r.partial, '');
        add('Упущено:', r.missed, 'exam-sim-miss');
        add('Неверно:', r.wrong, 'exam-sim-miss');
        if (r.advice) {
            const adv = document.createElement('p');
            adv.className = 'exam-note';
            adv.textContent = r.advice;
            wrap.appendChild(adv);
        }
        if (r.checks_left !== null && r.checks_left !== undefined) {
            const left = document.createElement('p');
            left.className = 'exam-note';
            left.textContent = `ИИ-оценок осталось в этом месяце: ${r.checks_left}.`;
            wrap.appendChild(left);
        }
        box.appendChild(wrap);
    } catch (_) {
        alert('Нет связи с сервером.');
        btn.disabled = false;
        btn.textContent = 'Оценить по смыслу (ИИ)';
    }
}

function examSimClose() {
    clearInterval(examSim.timer);
    examUi.view = 'review';
    showExamSection('review');
    setExamButtons('Начать подготовку', 'Выключить режим', true);
    loadExamOverview(examUi.subject).then(renderExam);
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
    if (examUi.view === 'sim') return examSim.submitted ? window.examSimStart() : examSimSubmit();
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
    if (examUi.view === 'sim') return examSimClose();
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
