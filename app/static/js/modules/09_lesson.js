
// ============================================================================
// УРОК УЗЛА «ПУТИ ЗНАНИЙ»: КОТ-РАССКАЗЧИК, ВОПРОСЫ НА ПОНИМАНИЕ, ЗАВЕРШЕНИЕ
// Озвучки нет: кот «говорит» только текстом (TTS работает лишь в тренировке языков).
// ============================================================================

// Кадры кота: 3 строки ровно по 9 знаков — уши / морда с усами / рот.
// Ширина строк строго одинаковая, иначе <pre> «прыгает» при смене эмоции.
// Первые шесть эмоций приходят из урока (поле emo); sleepy, wink, proud, cheer и sad
// выбирает только клиент — в промпт урока и VALID_EMOTIONS их добавлять не нужно.
const LESSON_CAT = {
    idle: {
        frames: [['  /\_/\  ', ' ( \'w\' ) ', ' (") (") ']],
        blink: ['  /\_/\  ', ' ( -w- ) ', ' (") (") '],
        // редкие «вздохи» между морганиями: тихое мяу и прищур
        idleFrames: [
            ['  /\_/\  ', ' ( \'o\' ) ', ' (") (") '],
            ['  /\_/\  ', ' ( -w- ) ', ' (") (") ']
        ]
    },
    talk: {
        frames: [
            ['  /\_/\  ', ' ( \'o\' ) ', ' (") (") '],
            ['  /\_/\  ', ' ( \'w\' ) ', ' (") (") '],
            ['  /\_/\  ', ' ( \'v\' ) ', ' (") (") ']
        ]
    },
    happy:     { frames: [['  /\_/\  ', ' ( ^w^ ) ', ' (") (") ']], blink: ['  /\_/\  ', ' ( ^-^ ) ', ' (") (") '] },
    think:     { frames: [['  /\_/\ ?', ' ( -.- ) ', ' (") (") '], ['  /\_/\? ', ' ( -.- ) ', ' (") (") ']] },
    surprised: { frames: [['  /\_/\ !', ' ( O.O ) ', ' (") (") ']] },
    confused:  { frames: [['  /\_/\ ?', ' ( o.O ) ', ' (") (") ']] },
    sleepy:    { frames: [['  /\_/\ z', ' ( u.u ) ', ' (") (") '], ['  /\_/\zZ', ' ( u.u ) ', ' (") (") ']] },
    wink:      { frames: [['  /\_/\  ', ' ( ^.~ ) ', ' (") (") ']] },
    proud:     { frames: [['* /\_/\ *', ' ( *.* ) ', ' (") (") '], [' */\_/\* ', ' ( *.* ) ', ' (") (") ']] },
    // Только клиент: радость за правильный ответ и сочувствие при ошибке
    cheer:     { frames: [['* /\_/\ *', ' ( ^o^ ) ', ' (") (") ']], blink: ['* /\_/\ *', ' ( ^-^ ) ', ' (") (") '] },
    sad:       { frames: [['  /\_/\  ', ' ( T.T )\'', ' (") (") ']] }
};
const LESSON_TIER_NAMES = ['Основы', 'Тема', 'Подтема', 'Кейс на различение'];
const LESSON_TYPE_MS = 18;

let lessonState = null;
let lessonTypeTimer = null;

function prefersReducedMotion() {
    return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

// ============================================================================
// ОБЩИЙ ДВИЖОК ASCII-МАСКОТА
// Кадры, моргание, редкие «вздохи» и одноразовые реакции. Таймеры живут на самом
// <pre>, поэтому экраны и карточки не наследуют чужие интервалы.
// ============================================================================
const CAT_MASCOT_TIMERS = new WeakMap();

// Медленный цикл спокойного кота: дыхание → моргание → тихое мяу → моргание.
// Паузы подобраны так, чтобы кот казался живым, но не отвлекал от текста.
const CAT_IDLE_PLAN = [
    { frame: 'base', hold: 2500 },
    { frame: 'blink', hold: 140 },
    { frame: 'base', hold: 850 },
    { frame: 'alt1', hold: 900 },
    { frame: 'base', hold: 2200 },
    { frame: 'blink', hold: 130 },
    { frame: 'base', hold: 1700 },
    { frame: 'alt2', hold: 700 }
];

function catFrameFor(cat, kind) {
    if (kind === 'blink' && cat.blink) return cat.blink;
    if (kind === 'alt1' && cat.idleFrames && cat.idleFrames[0]) return cat.idleFrames[0];
    if (kind === 'alt2' && cat.idleFrames && cat.idleFrames[1]) return cat.idleFrames[1];
    return cat.frames[0];
}

// Перезапуск одноразовой CSS-анимации: снять класс, форсировать reflow, повесить снова
function restartCssAnimation(el, cls) {
    if (!el || !cls) return;
    el._dgAnimTimers = el._dgAnimTimers || {};
    clearTimeout(el._dgAnimTimers[cls]);
    el.classList.remove(cls);
    void el.offsetWidth;
    el.classList.add(cls);
    const clear = () => el.classList.remove(cls);
    el.addEventListener('animationend', clear, { once: true });
    // страховка: у скрытого элемента animationend может не прийти
    el._dgAnimTimers[cls] = setTimeout(clear, 1000);
}

function stopMascot(el) {
    if (!el) return;
    const token = CAT_MASCOT_TIMERS.get(el);
    if (!token) return;
    token.stopped = true;
    clearTimeout(token.timer);
    CAT_MASCOT_TIMERS.delete(el);
}

function startMascot(el, emotion, options = {}) {
    if (!el || typeof LESSON_CAT === 'undefined') return;
    const cat = LESSON_CAT[emotion] || LESSON_CAT.idle;
    stopMascot(el);
    el.textContent = cat.frames[0].join('\n');
    el.className = `${options.baseClass || 'lesson-cat font-mono text-primary'} cat-emo-${emotion}`;
    if (!prefersReducedMotion()) {
        if (options.enter) restartCssAnimation(el, 'cat-enter');
        if (options.reaction) restartCssAnimation(el, `cat-react-${options.reaction}`);
    }
    if (prefersReducedMotion()) return;

    const token = { stopped: false, timer: null };
    CAT_MASCOT_TIMERS.set(el, token);
    const draw = frame => { if (!token.stopped) el.textContent = frame.join('\n'); };

    // Несколько кадров — кот говорит, сопит или сияет; один кадр — живёт медленным циклом.
    // Во время печати урока рот меняется быстро (170мс), на статичных репликах — мягко (620мс).
    if (cat.frames.length > 1) {
        const talkDelay = options.talkDelay || 620;
        const delay = emotion === 'talk' ? talkDelay : (emotion === 'sleepy' ? 900 : 680);
        let i = 0;
        const tick = () => {
            i = (i + 1) % cat.frames.length;
            draw(cat.frames[i]);
            token.timer = setTimeout(tick, delay);
        };
        token.timer = setTimeout(tick, delay);
        return;
    }

    let step = 0;
    const tick = () => {
        const item = CAT_IDLE_PLAN[step % CAT_IDLE_PLAN.length];
        draw(catFrameFor(cat, item.frame));
        step += 1;
        token.timer = setTimeout(tick, item.hold);
    };
    tick();
}

// Общая точка входа для любого <pre>-кота: урок, карточка, практика, старт, экзамен
function setMascotFor(elementId, emotion, options = {}) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const enter = options.enter === undefined ? !el.textContent.trim() : !!options.enter;
    startMascot(el, emotion, { ...options, enter });
}

function setLessonCat(emotion, reaction) {
    // В уроке кот «говорит» синхронно с печатью текста, поэтому рот меняется чаще
    setMascotFor('lesson-cat', emotion, { reaction, baseClass: 'lesson-cat font-mono text-primary', talkDelay: 170 });
}

window.startMascot = startMascot;
window.stopMascot = stopMascot;
window.setMascotFor = setMascotFor;
window.restartCssAnimation = restartCssAnimation;

// Печать реплики по буквам с паузами на знаках препинания; кот «говорит», пока идёт
// текст, потом принимает эмоцию реплики. Курсор-«▍» рисует CSS-класс is-typing.
function lessonTypeDelay(ch) {
    if (!ch) return LESSON_TYPE_MS;
    if ('.!?…:;'.includes(ch)) return LESSON_TYPE_MS + 95;
    if (',—–-'.includes(ch)) return LESSON_TYPE_MS + 45;
    if (ch === ' ') return LESSON_TYPE_MS + 6;
    return LESSON_TYPE_MS;
}

function sayLesson(text, emotionAfter, reaction) {
    const textEl = document.getElementById('lesson-text');
    clearTimeout(lessonTypeTimer);
    lessonTypeTimer = null;
    if (!textEl) return;

    if (!prefersReducedMotion()) {
        restartCssAnimation(document.getElementById('lesson-bubble') || textEl, 'bubble-swap');
    }
    if (prefersReducedMotion() || !lessonState) {
        textEl.textContent = text;
        textEl.classList.remove('is-typing');
        setLessonCat(emotionAfter, reaction);
        return;
    }

    setLessonCat('talk');
    let pos = 0;
    textEl.textContent = '';
    textEl.classList.add('is-typing');
    const finish = () => {
        clearTimeout(lessonTypeTimer);
        lessonTypeTimer = null;
        textEl.textContent = text;
        textEl.classList.remove('is-typing');
        if (lessonState) lessonState.typingDone = null;
        setLessonCat(emotionAfter, reaction);
    };
    lessonState.typingDone = finish;
    const typeNext = () => {
        if (!lessonState) { clearTimeout(lessonTypeTimer); return; }
        pos += 1;
        textEl.textContent = text.slice(0, pos);
        if (pos >= text.length) { finish(); return; }
        lessonTypeTimer = setTimeout(typeNext, lessonTypeDelay(text[pos]));
    };
    lessonTypeTimer = setTimeout(typeNext, lessonTypeDelay(text[0]));
}

window.skipLessonTyping = function() {
    if (lessonState && lessonState.typingDone) lessonState.typingDone();
};

function renderLessonProgress() {
    const el = document.getElementById('lesson-progress');
    if (!el || !lessonState) return;
    const pre = lessonState.hasPretest ? 1 : 0;
    const total = pre + lessonState.screens.length + lessonState.checks.length;
    const current = lessonState.phase === 'pretest' ? 0
        : lessonState.phase === 'screens' ? pre + lessonState.idx
        : lessonState.phase === 'check' ? pre + lessonState.screens.length + lessonState.idx
        : total;
    el.innerHTML = '';
    for (let i = 0; i < total; i++) {
        const dot = document.createElement('span');
        dot.className = `lesson-dot ${i < current ? 'lesson-dot-done' : (i === current ? 'lesson-dot-current' : '')}`;
        el.appendChild(dot);
    }
}

function setLessonButtons(nextLabel, nextEnabled, secondaryLabel) {
    const next = document.getElementById('lesson-next-btn');
    const secondary = document.getElementById('lesson-secondary-btn');
    if (next) {
        next.textContent = nextLabel;
        next.disabled = !nextEnabled;
    }
    if (secondary) {
        secondary.classList.toggle('hidden', !secondaryLabel);
        if (secondaryLabel) secondary.textContent = secondaryLabel;
    }
}

function clearLessonExtras() {
    ['lesson-visual-slot', 'lesson-focus', 'lesson-options', 'lesson-facts'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = '';
    });
    const visualEl = document.getElementById('lesson-visual-slot');
    if (visualEl) visualEl.classList.add('hidden');
    const factsEl = document.getElementById('lesson-facts');
    if (factsEl) factsEl.classList.add('hidden');
    const fb = document.getElementById('lesson-feedback');
    if (fb) fb.classList.add('hidden');
}

function renderLessonVisualSlot(s, nodesByKey) {
    const slot = document.getElementById('lesson-visual-slot');
    if (!slot || !s) return;
    const kgSub = (typeof currentKgSubject !== 'undefined' && currentKgSubject)
        ? currentKgSubject
        : (typeof currentSubject !== 'undefined' ? currentSubject : '');
    const focusNodes = (s.focus || []).map(k => nodesByKey.get(k)).filter(Boolean);
    const primaryNode = (lessonState && nodesByKey.get(lessonState.nodeId)) || focusNodes[0] || null;
    const shortNote = (txt, maxLen = 110) => {
        const t = String(txt || '').trim();
        if (!t) return '';
        return t.length > maxLen ? `${t.slice(0, maxLen - 1).trim()}…` : t;
    };

    // 1) Явная визуальная схема экрана от сервера (если есть)
    if (s.visual && typeof s.visual === 'object') {
        const v = s.visual;
        if (v.kind === 'contrast' && v.left && v.right) {
            slot.innerHTML = `
                <div class="lesson-visual-card">
                    <div class="lesson-contrast-grid">
                        <div class="lesson-contrast-col is-primary">
                            <span class="lesson-contrast-kicker">${escapeHTML(v.left.kicker || 'Разбираем')}</span>
                            <span class="lesson-contrast-name">${escapeHTML(v.left.name || '')}</span>
                            ${v.left.note ? `<span class="lesson-contrast-note">${escapeHTML(v.left.note)}</span>` : ''}
                        </div>
                        <div class="lesson-contrast-col is-vs">
                            <span class="lesson-contrast-kicker">${escapeHTML(v.right.kicker || 'Не путать с')}</span>
                            <span class="lesson-contrast-name">${escapeHTML(v.right.name || '')}</span>
                            ${v.right.note ? `<span class="lesson-contrast-note">${escapeHTML(v.right.note)}</span>` : ''}
                        </div>
                    </div>
                </div>`;
            slot.classList.remove('hidden');
            return;
        }
        if (v.term) {
            const canSpeakV = window.isSpeakable(v.term, kgSub);
            const isCompact = !/[\u4e00-\u9fa5]/.test(v.term) || v.term.length > 8;
            slot.innerHTML = `
                <div class="lesson-visual-card">
                    <div class="lesson-hanzi-row">
                        <div class="lesson-hanzi-glyph"${isCompact ? ' style="font-size:1.35rem;"' : ''}>${escapeHTML(v.term)}</div>
                        ${canSpeakV ? '<button type="button" data-no-swipe class="dg-speak-pill"><span class="material-symbols-outlined">volume_up</span><span>Слушать</span></button>' : ''}
                    </div>
                    ${v.note ? `<p class="lesson-visual-note">${escapeHTML(v.note)}</p>` : ''}
                </div>`;
            if (canSpeakV) {
                const btn = slot.querySelector('.dg-speak-pill');
                if (btn) btn.addEventListener('click', (e) => { e.stopPropagation(); window.replayAudioForText(v.term, kgSub); });
            }
            slot.classList.remove('hidden');
            return;
        }
    }

    // 2) Авто-карточка разграничения («не путать с») на основании графа связей
    const graphEdges = (typeof currentKgGraphData !== 'undefined' && currentKgGraphData)
        ? (currentKgGraphData.edges || currentKgGraphData.links || [])
        : [];
    if (focusNodes.length >= 2 && graphEdges.length > 0) {
        const focusIds = new Set(focusNodes.map(n => String(n.id)));
        const demEdge = graphEdges.find(e => {
            if (e.relation !== 'demarcated_from') return false;
            const sid = String(typeof e.source === 'object' && e.source ? e.source.id : e.source);
            const tid = String(typeof e.target === 'object' && e.target ? e.target.id : e.target);
            return focusIds.has(sid) && focusIds.has(tid) && sid !== tid;
        });
        if (demEdge) {
            const sid = String(typeof demEdge.source === 'object' && demEdge.source ? demEdge.source.id : demEdge.source);
            const tid = String(typeof demEdge.target === 'object' && demEdge.target ? demEdge.target.id : demEdge.target);
            const nA = nodesByKey.get(sid);
            const nB = nodesByKey.get(tid);
            if (nA && nB) {
                const noteA = shortNote(nA.summary, 90);
                const noteB = shortNote(nB.summary, 90);
                slot.innerHTML = `
                    <div class="lesson-visual-card">
                        <div class="lesson-contrast-grid">
                            <div class="lesson-contrast-col is-primary">
                                <span class="lesson-contrast-kicker">Разбираем</span>
                                <span class="lesson-contrast-name">${escapeHTML(nA.name)}</span>
                                ${noteA ? `<span class="lesson-contrast-note">${escapeHTML(noteA)}</span>` : ''}
                            </div>
                            <div class="lesson-contrast-col is-vs">
                                <span class="lesson-contrast-kicker">Не путать с</span>
                                <span class="lesson-contrast-name">${escapeHTML(nB.name)}</span>
                                ${noteB ? `<span class="lesson-contrast-note">${escapeHTML(noteB)}</span>` : ''}
                            </div>
                        </div>
                    </div>`;
                slot.classList.remove('hidden');
                return;
            }
        }
    }

    // 3) Крупный иероглиф / языковая единица + кнопка озвучки для языковых узлов
    const langCandidate = focusNodes.find(n => window.isSpeakable(n.name, kgSub))
        || (lessonState && lessonState.idx === 0 && primaryNode && window.isSpeakable(primaryNode.name, kgSub) ? primaryNode : null);
    if (langCandidate) {
        const term = String(langCandidate.name || '').trim();
        const isCjkShort = /[\u4e00-\u9fa5]/.test(term) && term.length <= 8;
        const noteTxt = (langCandidate.summary && langCandidate.summary !== s.say) ? shortNote(langCandidate.summary, 140) : '';
        slot.innerHTML = `
            <div class="lesson-visual-card">
                <div class="lesson-hanzi-row">
                    <div class="lesson-hanzi-glyph"${isCjkShort ? '' : ' style="font-size:1.35rem;"'}>${escapeHTML(term)}</div>
                    <button type="button" data-no-swipe class="dg-speak-pill"><span class="material-symbols-outlined">volume_up</span><span>Слушать</span></button>
                </div>
                ${noteTxt ? `<p class="lesson-visual-note">${escapeHTML(noteTxt)}</p>` : ''}
            </div>`;
        const btn = slot.querySelector('.dg-speak-pill');
        if (btn) btn.addEventListener('click', (e) => { e.stopPropagation(); window.replayAudioForText(term, kgSub); });
        slot.classList.remove('hidden');
    }
}

function renderLessonScreen() {
    const s = lessonState.screens[lessonState.idx];
    clearLessonExtras();
    renderLessonProgress();
    sayLesson(s.say, s.emo || 'idle');

    // Понятия, о которых говорит кот, — чипы с названиями узлов графа
    const focusEl = document.getElementById('lesson-focus');
    const nodesByKey = new Map(((typeof currentKgGraphData !== 'undefined' && currentKgGraphData && currentKgGraphData.nodes) || []).map(n => [n.id, n]));
    renderLessonVisualSlot(s, nodesByKey);
    (s.focus || []).forEach((key, i) => {
        const n = nodesByKey.get(key);
        if (!n || !focusEl) return;
        const chip = document.createElement('span');
        chip.className = `lesson-focus-chip px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase badge-${n.status || 'open'}`;
        chip.style.animationDelay = prefersReducedMotion() ? '' : `${i * 45}ms`;
        chip.textContent = n.name;
        focusEl.appendChild(chip);
    });

    const isLast = lessonState.idx === lessonState.screens.length - 1;
    const nextLabel = isLast ? (lessonState.checks.length ? 'Проверим себя' : 'Завершить урок') : 'Далее';
    setLessonButtons(nextLabel, true, lessonState.idx > 0 ? 'Назад' : null);
}

// Вопрос ДО урока (эффект предтеста, Richland, Kornell & Kao, 2009): попытка угадать,
// даже неудачная, настраивает внимание на главное. Верный ответ не раскрываем — его даст урок.
function renderLessonPretest() {
    const c = lessonState.checks[0];
    clearLessonExtras();
    renderLessonProgress();
    lessonState.answered = false;
    sayLesson(`Прежде чем начнём — угадай. Ошибиться не страшно, так лучше запомнится. ${c.q}`, 'think');

    const optionsEl = document.getElementById('lesson-options');
    c.options.forEach((opt, i) => {
        const btn = document.createElement('button');
        btn.className = 'lesson-option w-full text-left rounded-xl text-sm transition-all cursor-pointer';
        btn.style.animationDelay = prefersReducedMotion() ? '' : `${i * 45}ms`;
        btn.textContent = opt;
        btn.onclick = () => answerLessonPretest(i);
        optionsEl.appendChild(btn);
    });
    setLessonButtons('Выбери вариант', false, null);
}

function answerLessonPretest(choice) {
    if (!lessonState || lessonState.answered) return;
    lessonState.answered = true;
    lessonState.pretestChoice = choice;
    document.querySelectorAll('#lesson-options .lesson-option').forEach((btn, i) => {
        btn.disabled = true;
        btn.classList.remove('cursor-pointer');
        btn.classList.add(i === choice ? 'lesson-option-picked' : 'lesson-option-dim');
    });
    sayLesson('Запомнил твой вариант. Сейчас разберёмся, а в конце спрошу ещё раз.', 'happy', 'bounce');
    setLessonButtons('Начать урок', true, null);
}

function renderLessonCheck() {
    const c = lessonState.checks[lessonState.idx];
    clearLessonExtras();
    renderLessonProgress();
    lessonState.answered = false;
    sayLesson(c.q, 'think');

    const optionsEl = document.getElementById('lesson-options');
    c.options.forEach((opt, i) => {
        const btn = document.createElement('button');
        btn.className = 'lesson-option w-full text-left rounded-xl text-sm transition-all cursor-pointer';
        btn.style.animationDelay = prefersReducedMotion() ? '' : `${i * 45}ms`;
        btn.textContent = opt;
        btn.onclick = () => answerLessonCheck(i);
        optionsEl.appendChild(btn);
    });
    setLessonButtons('Ответь на вопрос', false, null);
}

function answerLessonCheck(choice) {
    if (!lessonState || lessonState.answered) return;
    lessonState.answered = true;
    const c = lessonState.checks[lessonState.idx];
    const correct = choice === c.answer;
    if (correct) lessonState.score += 1;

    document.querySelectorAll('#lesson-options .lesson-option').forEach((btn, i) => {
        btn.disabled = true;
        btn.classList.remove('cursor-pointer');
        if (i === c.answer) btn.classList.add('lesson-option-correct');
        else if (i === choice) btn.classList.add('lesson-option-wrong');
        else btn.classList.add('lesson-option-dim');
    });

    // Реакция кота на ответ — главный момент эмоций
    let verdict = correct ? 'Верно!' : 'Не совсем.';
    const pre = lessonState.idx === 0 ? lessonState.pretestChoice : null;
    if (correct && pre !== null && pre !== c.answer) verdict = 'Верно! До урока ты выбрал другое — вот это прогресс.';
    sayLesson(c.why ? `${verdict} ${c.why}` : verdict, correct ? 'happy' : 'confused', correct ? 'bounce' : 'shake');

    const isLast = lessonState.idx === lessonState.checks.length - 1;
    setLessonButtons(isLast ? 'Завершить урок' : 'Следующий вопрос', true, null);
}

async function finishLesson() {
    clearLessonExtras();
    lessonState.phase = 'done';
    renderLessonProgress();
    const hasChecks = lessonState.checks.length > 0;

    // Нужен хотя бы один верный ответ, иначе предлагаем пройти урок ещё раз
    if (hasChecks && lessonState.score === 0) {
        sayLesson('Хм, пока не сложилось. Давай ещё раз пробежимся по уроку — это быстро.', 'confused', 'shake');
        setLessonButtons('Пройти ещё раз', true, 'Позже');
        lessonState.onNext = () => restartLesson();
        lessonState.onSecondary = () => closeLesson();
        return;
    }

    setLessonButtons('Сохраняю…', false, null);
    try {
        const res = await apiFetch(`/api/path/node/${lessonState.nodeId}/complete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ checkpoint_score: lessonState.score })
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (e) {
        console.error('Не удалось сохранить прохождение урока:', e);
        sayLesson('Не получилось сохранить прогресс. Проверь связь и попробуй ещё раз.', 'confused', 'shake');
        setLessonButtons('Повторить', true, 'Закрыть');
        lessonState.onNext = () => finishLesson();
        lessonState.onSecondary = () => closeLesson();
        return;
    }

    const scoreLine = hasChecks ? ` ${lessonState.score} из ${lessonState.checks.length} с первого раза.` : '';
    // Конспект темы: факты книги, которых нет в карточках. Читаются один раз сразу после урока, дальше доступны из графа
    if (!lessonState.intro && (lessonState.facts || []).length && !lessonState.factsShown) {
        lessonState.factsShown = true;
        showLessonFacts(lessonState.facts, `Урок пройден!${scoreLine} Ещё конспект темы: пробеги глазами. Карточки этого не спрашивают, а на экзамене может встретиться.`,
            'Дальше', () => { lessonState.onNext = null; clearLessonExtras(); lessonTail(scoreLine); });
        return;
    }
    lessonTail(scoreLine);
}

// Конспект темы: список фактов под репликой кота; nextLabel и onNext — что делает главная кнопка после прочтения
function showLessonFacts(facts, say, nextLabel, onNext, secondaryLabel, onSecondary) {
    clearLessonExtras();
    renderLessonProgress();
    sayLesson(say, 'talk');
    const list = document.getElementById('lesson-facts');
    if (list) {
        facts.forEach((text, i) => {
            const li = document.createElement('li');
            li.style.animationDelay = prefersReducedMotion() ? '' : `${i * 55}ms`;
            li.textContent = text;
            list.appendChild(li);
        });
        list.classList.remove('hidden');
    }
    setLessonButtons(nextLabel, true, secondaryLabel || null);
    lessonState.onNext = onNext;
    lessonState.onSecondary = onSecondary || null;
}

// Конспект из графа: без урока и без сохранения прохождения
window.openFactSheet = async function(nodeId) {
    const modal = document.getElementById('lesson-modal');
    if (!modal) return;
    let data;
    try {
        const res = await apiFetch(`/api/path/node/${nodeId}/facts`);
        data = await res.json();
        if (!res.ok) {
            alert(data.detail || 'Конспект пока недоступен.');
            return;
        }
    } catch (e) {
        console.error('Сбой загрузки конспекта:', e);
        alert('Не удалось загрузить конспект. Проверь связь.');
        return;
    }
    if (!(data.facts || []).length) {
        alert('Для этой темы конспекта нет: все её факты уже в карточках.');
        return;
    }
    lessonState = {
        nodeId, intro: false, screens: [], checks: [], hasPretest: false, pretestChoice: null, phase: 'done', idx: 0, score: 0,
        answered: false, typingDone: null, onNext: null, onSecondary: null, facts: data.facts, factsShown: true
    };
    const nameEl = document.getElementById('lesson-node-name');
    if (nameEl) nameEl.textContent = data.node.name;
    const tierEl = document.getElementById('lesson-tier-badge');
    if (tierEl) tierEl.textContent = 'Конспект темы';
    modal.classList.remove('hidden');
    showLessonFacts(data.facts, `Конспект темы: ${data.facts.length} фактов из учебника в порядке изложения. Удобно перечитать перед экзаменом.`,
        'Закрыть', () => closeLesson());
};

function lessonTail(scoreLine) {
    // Вводный урок: карточек у него нет, дальше — первая тема пути
    if (lessonState.intro) {
        const inRun = window.pathRun && window.pathRun.active;
        sayLesson('Теперь ты знаешь карту. Дальше будем идти по ней шаг за шагом. Начнём с первой темы?', 'happy', 'bounce');
        setLessonButtons(inRun ? 'К первой теме' : 'Закрыть', true, null);
        lessonState.onNext = () => {
            closeLesson();
            if (inRun) window.pathRun.stepFinished();
            else if (window.loadKnowledgeGraph) window.loadKnowledgeGraph(typeof currentKgSubject !== 'undefined' ? currentKgSubject : undefined);
        };
        return;
    }
    // В режиме «Продолжить путь» карточки темы идут сразу: вспоминание сразу после урока
    if (window.pathRun && window.pathRun.active) {
        sayLesson(`Урок пройден!${scoreLine} Теперь закрепим на карточках, пока свежо.`, 'happy', 'bounce');
        setLessonButtons('К карточкам', true, null);
        lessonState.onNext = () => {
            setLessonButtons('…', false, null);
            window.pathRun.afterLesson();
        };
        return;
    }
    // Урок и его карточки неделимы: сразу ведём к карточкам темы, пока свежо
    const kgSubject = typeof currentKgSubject !== 'undefined' ? currentKgSubject : undefined;
    sayLesson(`Урок пройден!${scoreLine} Теперь закрепим на карточках этой темы, пока свежо.`, 'happy', 'bounce');
    setLessonButtons('К карточкам', true, 'К графу');
    lessonState.onNext = () => {
        closeLesson();
        if (typeof closeKnowledgeGraphModal === 'function') closeKnowledgeGraphModal();
        if (typeof startTopicRun === 'function') startTopicRun(false, kgSubject);
    };
    lessonState.onSecondary = () => {
        closeLesson();
        if (window.loadKnowledgeGraph) window.loadKnowledgeGraph(kgSubject);
    };
}

function restartLesson() {
    lessonState.phase = 'screens';
    lessonState.idx = 0;
    lessonState.score = 0;
    lessonState.onNext = null;
    lessonState.onSecondary = null;
    renderLessonScreen();
}

window.lessonNext = function() {
    if (!lessonState) return;
    if (lessonState.typingDone) {
        // Первое нажатие во время печати — просто допечатать реплику
        lessonState.typingDone();
        return;
    }
    if (lessonState.onNext) {
        lessonState.onNext();
        return;
    }
    if (lessonState.phase === 'pretest') {
        lessonState.phase = 'screens';
        lessonState.idx = 0;
        renderLessonScreen();
    } else if (lessonState.phase === 'screens') {
        if (lessonState.idx < lessonState.screens.length - 1) {
            lessonState.idx += 1;
            renderLessonScreen();
        } else if (lessonState.checks.length) {
            lessonState.phase = 'check';
            lessonState.idx = 0;
            renderLessonCheck();
        } else {
            finishLesson();
        }
    } else if (lessonState.phase === 'check') {
        if (lessonState.idx < lessonState.checks.length - 1) {
            lessonState.idx += 1;
            renderLessonCheck();
        } else {
            finishLesson();
        }
    }
};

window.lessonSecondaryAction = function() {
    if (!lessonState) return;
    if (lessonState.onSecondary) {
        lessonState.onSecondary();
        return;
    }
    if (lessonState.phase === 'screens' && lessonState.idx > 0) {
        lessonState.idx -= 1;
        renderLessonScreen();
    }
};

window.openLesson = async function(nodeId) {
    const modal = document.getElementById('lesson-modal');
    if (!modal) return;
    let data;
    try {
        const res = await apiFetch(`/api/path/node/${nodeId}/lesson`);
        data = await res.json();
        if (!res.ok) {
            alert(data.detail || 'Урок пока недоступен.');
            return;
        }
    } catch (e) {
        console.error('Сбой загрузки урока:', e);
        alert('Не удалось загрузить урок. Проверь связь.');
        return;
    }
    if (!data.lesson || !(data.lesson.screens || []).length) {
        // Урок не сгенерировался — не блокируем путь: сразу открываем карточки узла
        try {
            await apiFetch(`/api/path/node/${nodeId}/complete`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ checkpoint_score: 0 })
            });
        } catch (e) { console.error('Сбой открытия узла без урока:', e); }
        alert('Урока для этой темы нет — её карточки уже открыты в тренировке.');
        if (window.pathRun && window.pathRun.active) window.pathRun.stepFinished();
        else if (window.loadKnowledgeGraph) window.loadKnowledgeGraph(typeof currentKgSubject !== 'undefined' ? currentKgSubject : undefined);
        return;
    }

    lessonState = {
        nodeId,
        intro: !!data.intro,
        screens: data.lesson.screens,
        checks: data.lesson.check || [],
        facts: data.facts || [],
        factsShown: false,
        hasPretest: (data.lesson.check || []).length > 0,
        pretestChoice: null,
        phase: (data.lesson.check || []).length > 0 ? 'pretest' : 'screens',
        idx: 0,
        score: 0,
        answered: false,
        typingDone: null,
        onNext: null,
        onSecondary: null
    };
    const nameEl = document.getElementById('lesson-node-name');
    if (nameEl) nameEl.textContent = data.node.name;
    const tierEl = document.getElementById('lesson-tier-badge');
    if (tierEl) tierEl.textContent = data.intro ? 'Вводный урок' : (LESSON_TIER_NAMES[data.node.tier] || '');

    modal.classList.remove('hidden');
    // Кот появляется вместе с уроком — один раз, а не на каждой реплике
    if (!prefersReducedMotion()) restartCssAnimation(document.getElementById('lesson-cat'), 'cat-enter');
    if (lessonState.phase === 'pretest') renderLessonPretest();
    else renderLessonScreen();
};

window.closeLesson = function() {
    clearTimeout(lessonTypeTimer);
    lessonTypeTimer = null;
    stopMascot(document.getElementById('lesson-cat'));
    const textEl = document.getElementById('lesson-text');
    if (textEl) {
        textEl.textContent = '';
        textEl.classList.remove('is-typing');
    }
    lessonState = null;
    const modal = document.getElementById('lesson-modal');
    if (modal) modal.classList.add('hidden');
};
