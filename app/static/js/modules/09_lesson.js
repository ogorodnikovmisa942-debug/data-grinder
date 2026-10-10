
// ============================================================================
// УРОК УЗЛА «ПУТИ ЗНАНИЙ»: КОТ-РАССКАЗЧИК, ВОПРОСЫ НА ПОНИМАНИЕ, ЗАВЕРШЕНИЕ
// Озвучки нет: кот «говорит» только текстом (TTS работает лишь в тренировке языков).
// ============================================================================

// Кадры кота: 3 строки по 9 символов (усы по бокам морды). Первые шесть эмоций приходят из урока (поле emo);
// sleepy, wink и proud выбирает только клиент — в промпт урока и VALID_EMOTIONS их добавлять не нужно.
const LESSON_CAT = {
    idle:      { frames: [['  /\\_/\\  ', '=( o.o )=', '  > ^ <  ']], blink: ['  /\\_/\\  ', '=( -.- )=', '  > ^ <  '] },
    talk:      { frames: [['  /\\_/\\  ', '=( o.o )=', '  > o <  '], ['  /\\_/\\  ', '=( o.o )=', '  > - <  ']] },
    happy:     { frames: [['  /\\_/\\  ', '=( ^.^ )=', '  > w <  ']] },
    think:     { frames: [['  /\\_/\\ ?', '=( -.- )=', '  > ~ <  '], ['  /\\_/\\? ', '=( -.- )=', '  > ~ <  ']] },
    surprised: { frames: [['  /\\_/\\ !', '=( O.O )=', '  > o <  ']] },
    confused:  { frames: [['  /\\_/\\ ?', '=( o.O )=', '  > ~ <  ']] },
    sleepy:    { frames: [['  /\\_/\\ z', '=( u.u )=', '  > ~ <  '], ['  /\\_/\\zZ', '=( u.u )=', '  > o <  ']] },
    wink:      { frames: [['  /\\_/\\  ', '=( o.- )=', '  > w <  ']] },
    proud:     { frames: [['  /\\_/\\ *', '=( *.* )=', '  > w <  '], ['  /\\_/\\* ', '=( *.* )=', '  > w <  ']] }
};
const LESSON_TIER_NAMES = ['Основы', 'Тема', 'Подтема', 'Кейс на различение'];
const LESSON_TYPE_MS = 18;

let lessonState = null;
let lessonCatTimer = null;
let lessonTypeTimer = null;

function prefersReducedMotion() {
    return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function setLessonCat(emotion, reaction) {
    const el = document.getElementById('lesson-cat');
    if (!el) return;
    const cat = LESSON_CAT[emotion] || LESSON_CAT.idle;
    clearInterval(lessonCatTimer);

    const draw = (frame) => { el.textContent = frame.join('\n'); };
    draw(cat.frames[0]);

    // Микроанимации: рот/знак вопроса сменяют кадры, спокойный кот иногда моргает
    if (!prefersReducedMotion()) {
        if (cat.frames.length > 1) {
            let i = 0;
            lessonCatTimer = setInterval(() => { i = (i + 1) % cat.frames.length; draw(cat.frames[i]); }, emotion === 'talk' ? 170 : 600);
        } else if (cat.blink) {
            lessonCatTimer = setInterval(() => {
                draw(cat.blink);
                setTimeout(() => draw(cat.frames[0]), 160);
            }, 3200);
        }
    }

    el.className = `lesson-cat font-mono text-primary cat-emo-${emotion}`;
    if (reaction && !prefersReducedMotion()) {
        void el.offsetWidth; // перезапуск CSS-анимации реакции
        el.classList.add(`cat-react-${reaction}`);
    }
}

// Печать реплики по буквам; кот «говорит», пока идёт текст, потом принимает эмоцию реплики
function sayLesson(text, emotionAfter, reaction) {
    const textEl = document.getElementById('lesson-text');
    clearInterval(lessonTypeTimer);
    if (!textEl) return;
    if (prefersReducedMotion()) {
        textEl.textContent = text;
        setLessonCat(emotionAfter, reaction);
        return;
    }
    setLessonCat('talk');
    let pos = 0;
    textEl.textContent = '';
    lessonState.typingDone = () => {
        clearInterval(lessonTypeTimer);
        textEl.textContent = text;
        lessonState.typingDone = null;
        setLessonCat(emotionAfter, reaction);
    };
    lessonTypeTimer = setInterval(() => {
        pos += 1;
        textEl.textContent = text.slice(0, pos);
        if (pos >= text.length && lessonState.typingDone) lessonState.typingDone();
    }, LESSON_TYPE_MS);
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
    ['lesson-focus', 'lesson-options', 'lesson-facts'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = '';
    });
    const factsEl = document.getElementById('lesson-facts');
    if (factsEl) factsEl.classList.add('hidden');
    const fb = document.getElementById('lesson-feedback');
    if (fb) fb.classList.add('hidden');
}

function renderLessonScreen() {
    const s = lessonState.screens[lessonState.idx];
    clearLessonExtras();
    renderLessonProgress();
    sayLesson(s.say, s.emo || 'idle');

    // Понятия, о которых говорит кот, — чипы с названиями узлов графа
    const focusEl = document.getElementById('lesson-focus');
    const nodesByKey = new Map(((typeof currentKgGraphData !== 'undefined' && currentKgGraphData && currentKgGraphData.nodes) || []).map(n => [n.id, n]));
    (s.focus || []).forEach(key => {
        const n = nodesByKey.get(key);
        if (!n || !focusEl) return;
        const chip = document.createElement('span');
        chip.className = `px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase badge-${n.status || 'open'}`;
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
        facts.forEach(text => {
            const li = document.createElement('li');
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
    if (lessonState.phase === 'pretest') renderLessonPretest();
    else renderLessonScreen();
};

window.closeLesson = function() {
    clearInterval(lessonCatTimer);
    clearInterval(lessonTypeTimer);
    lessonState = null;
    const modal = document.getElementById('lesson-modal');
    if (modal) modal.classList.add('hidden');
};
