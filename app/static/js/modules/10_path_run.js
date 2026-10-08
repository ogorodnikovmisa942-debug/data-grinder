
// ============================================================================
// «ПРОДОЛЖИТЬ ПУТЬ»: ЗАНЯТИЕ ОДНОЙ КНОПКОЙ
// Сервер выбирает следующий шаг (/api/path/{subject}/next): разминка-повторение →
// урок → его карточки → практика вперемешку → итог дня. Кот объявляет каждый шаг,
// а в конце удачного дня — большой ASCII-кот на весь экран.
// ============================================================================

const PATH_RUN_STEP_VIEW = {
    intro:    { icon: 'explore',        kind: 'Знакомство' },
    review:   { icon: 'history',        kind: 'Разминка' },
    cards:    { icon: 'style',          kind: 'Закрепление' },
    lesson:   { icon: 'school',         kind: 'Новая тема' },
    practice: { icon: 'psychology_alt', kind: 'Практика на различение' },
    recap:    { icon: 'replay',         kind: 'Закрепление' },
    ticket:   { icon: 'assignment',     kind: 'Билеты' },
    drill:    { icon: 'replay',         kind: 'Прогон билетов' }
};

const pathRun = {
    active: false,
    subject: '',
    done: [],
    step: null,
    stepStarted: false,
    note: '',
    practiceCount: null,
    scope: 'day',   // day — «Продолжить путь», topic — кнопка «Новая тема» (одна порция урок → карточки)
    extra: false,   // тема сверх дневной нормы по явному выбору
    onDoneGo: null
};
window.pathRun = pathRun;

let pathRunCatTimer = null;

function pathRunPlural(n, one, few, many) {
    const m10 = n % 10, m100 = n % 100;
    if (m10 === 1 && m100 !== 11) return one;
    if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few;
    return many;
}

function drawPathRunCat(emotion) {
    const el = document.getElementById('path-run-cat');
    if (!el) return;
    const cat = LESSON_CAT[emotion] || LESSON_CAT.idle;
    clearInterval(pathRunCatTimer);
    el.textContent = cat.frames[0].join('\n');
    el.className = `lesson-cat font-mono text-primary cat-emo-${emotion}`;
    if (prefersReducedMotion()) return;
    if (emotion === 'happy') {
        void el.offsetWidth;
        el.classList.add('cat-react-bounce');
    }
    const idle = LESSON_CAT.idle;
    pathRunCatTimer = setInterval(() => {
        el.textContent = idle.blink.join('\n');
        setTimeout(() => { el.textContent = cat.frames[0].join('\n'); }, 160);
    }, 3200);
}

function describePathStep(step, isFirst) {
    const n = step.count || 0;
    switch (step.type) {
        case 'review':
            return {
                name: `${n} ${pathRunPlural(n, 'карточка', 'карточки', 'карточек')} на повторение`,
                say: isFirst
                    ? 'Начнём с разминки: вспомним то, что пора повторить. Это быстро.'
                    : 'Ещё один короткий подход повторений — и дальше.',
                emo: 'talk'
            };
        case 'cards':
            return {
                name: `«${step.node_name}» · ${n} ${pathRunPlural(n, 'карточка', 'карточки', 'карточек')}`,
                say: 'Закрепим тему на карточках: вспоминать сразу после урока полезнее, чем перечитывать.',
                emo: 'happy'
            };
        case 'intro':
            return {
                name: step.node_name || 'Знакомство с курсом',
                say: 'Прежде чем начнём — короткая экскурсия: сколько здесь материала, из каких частей он состоит и в каком порядке мы пойдём. Пара минут, без вопросов.',
                emo: 'talk'
            };
        case 'lesson':
            return {
                name: step.cards ? `${step.node_name} · ${step.cards} ${pathRunPlural(step.cards, 'карточка', 'карточки', 'карточек')}` : step.node_name,
                say: 'Новая тема! Сначала угадай ответ, потом я объясню. Минуты три.',
                emo: 'surprised'
            };
        case 'practice':
            return {
                name: `${n} ${pathRunPlural(n, 'задание', 'задания', 'заданий')} вперемешку`,
                say: step.plan_id
                    ? 'Практика по темам билетов: учимся отличать похожее. Ошибаться здесь нормально.'
                    : 'Практика вперемешку: учимся отличать похожее. Ошибаться здесь нормально.',
                emo: 'think'
            };
        case 'recap':
            return {
                name: `${n} ${pathRunPlural(n, 'карточка', 'карточки', 'карточек')} из выученного сегодня`,
                say: 'Последний подход: вспомним ещё раз то, что учили сегодня. После паузы это запоминается заметно крепче.',
                emo: 'happy'
            };
        case 'ticket':
            return {
                name: `${n} ${pathRunPlural(n, 'билет', 'билета', 'билетов')} письменно`,
                say: 'Темы для билета пройдены — теперь сам билет. Ответь своими словами, как на экзамене, а я сверю с ключевыми тезисами.',
                emo: 'think'
            };
        case 'drill':
            return {
                name: `${n} ${pathRunPlural(n, 'билет', 'билета', 'билетов')} на прогон`,
                say: 'Экзамен скоро — прогоняем билеты. Начнём с тех, что держатся слабее всего.',
                emo: 'surprised'
            };
        default:
            return { name: '', say: '', emo: 'idle' };
    }
}

function renderPathRunSteps() {
    const el = document.getElementById('path-run-steps');
    if (!el) return;
    el.innerHTML = '';
    pathRun.done.forEach((type, i) => {
        const isCurrent = i === pathRun.done.length - 1 && pathRun.step && pathRun.step.type === type && pathRun.stepStarted;
        const chip = document.createElement('span');
        chip.className = `path-run-step ${isCurrent ? 'path-run-step-current' : 'path-run-step-done'}`;
        chip.innerHTML = `<span class="material-symbols-outlined">${(PATH_RUN_STEP_VIEW[type] || {}).icon || 'check'}</span>`;
        el.appendChild(chip);
    });
}

function setPathRunView({ say, emo, card, goLabel, goEnabled = true, secondaryLabel = 'Хватит на сегодня' }) {
    const text = document.getElementById('path-run-text');
    if (text) text.textContent = say;
    drawPathRunCat(emo || 'idle');

    const cardEl = document.getElementById('path-run-card');
    if (cardEl) {
        cardEl.classList.toggle('hidden', !card);
        if (card) {
            document.getElementById('path-run-card-icon').textContent = card.icon;
            document.getElementById('path-run-card-kind').textContent = card.kind;
            document.getElementById('path-run-card-name').textContent = card.name;
        }
    }
    const go = document.getElementById('path-run-go');
    if (go) {
        go.textContent = goLabel;
        go.disabled = !goEnabled;
    }
    const secondary = document.getElementById('path-run-secondary');
    if (secondary) {
        secondary.classList.toggle('hidden', !secondaryLabel);
        if (secondaryLabel) secondary.textContent = secondaryLabel;
    }
    renderPathRunSteps();
}

function showPathRunOverlay(visible) {
    const el = document.getElementById('path-run-overlay');
    if (el) el.classList.toggle('hidden', !visible);
    if (!visible) clearInterval(pathRunCatTimer);
    else setRunMode(false);
}

// Режим занятия на экране карточек: шапка, нижнее меню и счётчики уходят,
// остаются крестик, название шага и прогресс — как в уроке, без «системных» цифр
function setRunMode(on, stepType) {
    document.body.classList.toggle('path-run-mode', !!on);
    if (!on) return;
    const kind = document.getElementById('run-bar-kind');
    if (kind) kind.textContent = (PATH_RUN_STEP_VIEW[stepType] || {}).kind || '';
    window.updateRunBar(0, 0);
}

window.updateRunBar = function(index, total) {
    const fill = document.getElementById('run-bar-fill');
    const count = document.getElementById('run-bar-count');
    if (fill) fill.style.width = total ? `${Math.min(100, (index / total) * 100)}%` : '0%';
    if (count) count.textContent = total ? `${Math.min(index + 1, total)}/${total}` : '';
};

async function fetchPathStep(done) {
    const q = `done=${encodeURIComponent(done.join(','))}&scope=${pathRun.scope}${pathRun.extra ? '&extra=true' : ''}`;
    const res = await apiFetch(`/api/path/${encodeURIComponent(pathRun.subject)}/next?${q}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

pathRun.start = async function(subject, opts = {}) {
    if (!subject || subject === 'all') {
        alert('Сначала выбери предмет.');
        return;
    }
    triggerHaptic('medium');
    Object.assign(pathRun, {
        active: true, subject, done: [], step: null, stepStarted: false, note: '', practiceCount: null,
        scope: opts.scope || 'day', extra: !!opts.extra, onDoneGo: null
    });
    showPathRunOverlay(true);
    if (opts.step && opts.step.type !== 'done') {
        // Шаг уже объявлен котом на стартовом экране — сразу к делу.
        // Оверлей с этим шагом остаётся под уроком/практикой: закрыл их — вернулся к коту.
        pathRun.showStep(opts.step);
        pathRun.go();
        return;
    }
    await pathRun.loadNext();
};

pathRun.loadNext = async function(prefetched = null) {
    let step = prefetched;
    if (!step) {
        setPathRunView({ say: pathRun.note ? `${pathRun.note} Смотрю, что дальше…` : 'Смотрю, с чего начать…', emo: 'think', goLabel: '…', goEnabled: false });
        try {
            step = await fetchPathStep(pathRun.done);
        } catch (e) {
            console.error('Сбой выбора следующего шага:', e);
            setPathRunView({ say: 'Не получилось связаться с сервером. Попробуем ещё раз?', emo: 'confused', goLabel: 'Повторить' });
            pathRun.step = null;
            return;
        }
    }
    if (step.type === 'done') {
        pathRun.step = step;
        pathRun.stepStarted = false;
        pathRun.finish(step);
        return;
    }
    pathRun.showStep(step);
};

pathRun.showStep = function(step) {
    pathRun.step = step;
    pathRun.stepStarted = false;
    const d = describePathStep(step, pathRun.done.length === 0);
    const view = PATH_RUN_STEP_VIEW[step.type];
    setPathRunView({
        say: pathRun.note ? `${pathRun.note} ${d.say}` : d.say,
        emo: pathRun.note ? 'happy' : d.emo,
        card: { icon: view.icon, kind: view.kind, name: d.name },
        goLabel: 'Поехали'
    });
    pathRun.note = '';
};

pathRun.go = function() {
    const step = pathRun.step;
    if (!pathRun.active) return;
    if (!step) {
        pathRun.loadNext();
        return;
    }
    if (step.type === 'done') {
        const next = pathRun.onDoneGo;
        pathRun.stop();
        if (next) next();
        return;
    }
    triggerHaptic('light');
    if (!pathRun.stepStarted) {
        pathRun.done.push(step.type);
        pathRun.stepStarted = true;
    }
    if (['review', 'recap', 'cards', 'ticket', 'drill'].includes(step.type)) {
        showPathRunOverlay(false);
        if (typeof switchTab === 'function') switchTab('train');
        setRunMode(true, step.type);
        startSession(['review', 'recap', 'drill'].includes(step.type) ? 'review' : 'new');
    } else if (step.type === 'lesson' || step.type === 'intro') {
        openLesson(step.node_id);
    } else if (step.type === 'practice') {
        pathRun.practiceCount = step.count;
        openPracticeModal(pathRun.subject);
    }
};

// Параметры /api/session для текущего шага: короткий подход повторений или карточки одного узла
pathRun.sessionQuery = function() {
    const step = pathRun.step || {};
    let params = '';
    if (step.type === 'review') params = `&limit_cards=${step.count}&due_only=true`;
    if (step.type === 'recap') params = `&limit_cards=${step.count}`;
    if (step.type === 'cards') params = `&node_id=${step.node_id}`;
    if (step.type === 'ticket') params = `&exam_plan=${step.plan_id}&limit_cards=${step.count}`;
    if (step.type === 'drill') params = `&exam_plan=${step.plan_id}&exam_drill=true`;
    return { subject: pathRun.subject, params };
};

pathRun.stepFinished = function(prefetched = null) {
    if (!pathRun.active) return;
    showPathRunOverlay(true);
    pathRun.loadNext(prefetched);
};

// Урок пройден: если дальше его карточки (а так почти всегда), идём к ним сразу —
// кот уже сказал это в конце урока, второй экран с тем же смыслом только тормозит
pathRun.afterLesson = async function() {
    let step = null;
    try { step = await fetchPathStep(pathRun.done); } catch (_) { /* покажем шаг через оверлей */ }
    if (typeof closeLesson === 'function') closeLesson();
    if (step && step.type === 'cards') {
        showPathRunOverlay(true);
        pathRun.showStep(step);
        pathRun.go();
        return;
    }
    pathRun.stepFinished(step);
};

pathRun.trainFinished = function(stats) {
    if (!pathRun.active) return;
    if (typeof showSessionStarter === 'function') showSessionStarter();
    if (stats && stats.totalAnswered) {
        const n = stats.totalAnswered;
        pathRun.note = `Есть! ${n} ${pathRunPlural(n, 'карточка', 'карточки', 'карточек')}.`;
    }
    pathRun.stepFinished();
};

// Пользователь вышел из тренировки посреди шага — возвращаемся к коту, шаг можно продолжить
pathRun.trainInterrupted = function() {
    if (!pathRun.active) return;
    showPathRunOverlay(true);
    setPathRunView({
        say: 'Прервались — ничего страшного. Продолжим с того же места?',
        emo: 'confused',
        card: pathRun.step ? { icon: PATH_RUN_STEP_VIEW[pathRun.step.type].icon, kind: PATH_RUN_STEP_VIEW[pathRun.step.type].kind, name: describePathStep(pathRun.step, false).name } : null,
        goLabel: 'Продолжить'
    });
};

pathRun.stop = function() {
    pathRun.active = false;
    pathRun.step = null;
    showPathRunOverlay(false);
    setRunMode(false);
    if (typeof showSessionStarter === 'function') showSessionStarter();
};

// Победная мордочка — один раз за учебный день (день считает сервер, по Москве)
function dayCelebratedKey(subject, day) {
    return `dg_day_celebrated_${subject}_${day || new Date().toISOString().slice(0, 10)}`;
}

// Цель дня выполнена и мордочку сегодня ещё не показывали → показываем. Возвращает true, если показали.
function celebrateIfGoalMet(subject, plan, result) {
    if (!plan || !plan.goal_met) return false;
    const key = dayCelebratedKey(subject, plan.day);
    try {
        if (localStorage.getItem(key) === '1') return false;
        localStorage.setItem(key, '1');
    } catch (_) { /* без хранилища просто покажем */ }
    showDayCelebration(result);
    return true;
}

// Проверка цели дня после любого действия вне «Продолжить путь»: урок, карточки, практика
window.checkDayGoal = async function(subject) {
    subject = subject || (typeof getActiveDeckSubject === 'function' ? getActiveDeckSubject() : currentSubject);
    if (!subject || subject === 'all' || pathRun.active) return;
    try {
        const res = await apiFetch(`/api/path/${encodeURIComponent(subject)}/day`);
        if (!res.ok) return;
        const plan = await res.json();
        if (!plan.is_path) return;
        const nextUp = plan.next_lesson ? plan.next_lesson.node_name : null;
        celebrateIfGoalMet(subject, plan, { today: plan.today, next_up: nextUp });
    } catch (_) { /* награда не критична */ }
};

pathRun.finish = function(result) {
    const didSomething = pathRun.done.length > 0;
    const subject = pathRun.subject;
    if (celebrateIfGoalMet(subject, result.plan, result)) {
        pathRun.stop();
        return;
    }

    const plan = result.plan || {};

    if (pathRun.scope === 'topic') {
        let say, goLabel = 'Отлично', secondaryLabel = null;
        if (result.reason === 'topic_done') {
            say = 'Тема закрыта! Её карточки ушли в повторение — я напомню, когда пора.';
            if (plan.next_lesson && plan.can_start_lesson) {
                say += ` Место на сегодня ещё есть: «${plan.next_lesson.node_name}».`;
                goLabel = 'Следующая тема';
                secondaryLabel = 'Хватит на сегодня';
                pathRun.onDoneGo = () => pathRun.start(subject, { scope: 'topic' });
            }
        } else if (result.reason === 'limit') {
            say = `Норма новых на сегодня закрыта (${plan.learned_today} из ${plan.limit}). Можно взять ещё тему сверх нормы — но мозгу нужно время, чтобы всё улеглось.`;
            goLabel = 'Ещё тема';
            secondaryLabel = 'Хватит на сегодня';
            pathRun.onDoneGo = () => pathRun.start(subject, { scope: 'topic', extra: true });
        } else {
            say = 'Новых тем пока нет: следующие откроются, когда освоишь пройденные в повторениях.';
        }
        setPathRunView({ say, emo: 'happy', goLabel, secondaryLabel });
        return;
    }

    if (pathRun.scope === 'exam') {
        const ex = examDoneView(result);
        if (ex.extra) pathRun.onDoneGo = () => pathRun.start(subject, { scope: 'exam', extra: true });
        else if (ex.openPlan) pathRun.onDoneGo = () => openExamModal();
        setPathRunView({
            say: `${didSomething ? 'На сегодня всё! ' : ''}${ex.say}`,
            emo: 'happy',
            goLabel: ex.goLabel,
            secondaryLabel: ex.extra || ex.openPlan ? 'Хватит на сегодня' : null
        });
        return;
    }

    const reasonText = {
        reviews_left: 'Остальные повторения лучше оставить на потом — короткие подходы работают лучше марафона.',
        limit: 'Норма новых тем на сегодня закрыта — мозгу нужно время, чтобы всё улеглось.',
        waiting: 'Новые темы откроются, когда пройденные закрепятся в повторениях. Загляни завтра.'
    }[result.reason] || '';
    const nextUp = result.next_up ? ` Дальше по пути: «${result.next_up}».` : '';
    const canExtra = result.reason === 'limit' && result.next_up;
    if (canExtra) pathRun.onDoneGo = () => pathRun.start(subject, { scope: 'topic', extra: true });
    setPathRunView({
        say: `${didSomething ? 'На сегодня всё!' : 'Сегодня всё уже сделано.'} ${reasonText}${nextUp}`,
        emo: 'happy',
        goLabel: canExtra ? 'Ещё тема' : 'Отлично',
        secondaryLabel: canExtra ? 'Хватит на сегодня' : null
    });
};

// Что кот показал на стартовом экране: по нажатию «Начать» запускаем именно это, без второго экрана
let starterPreview = null;

function activeRunSubject() {
    return typeof getActiveDeckSubject === 'function' ? getActiveDeckSubject() : currentSubject;
}

// Итог в режиме экзамена: что сказать и что предложить кнопкой
function examDoneView(step) {
    const d = step.days_left;
    const days = d >= 0 ? `До экзамена ${d} ${pathRunPlural(d, 'день', 'дня', 'дней')}.` : '';
    switch (step.reason) {
        case 'exam_quota':
            return {
                say: `${days} Норма на сегодня выполнена: ${step.lessons_today} из ${step.quota}. Дальше — «${step.next_up}», её можно взять сверх плана.`,
                goLabel: 'Ещё тема', extra: true
            };
        case 'exam_ready':
            return { say: `${days} Все нужные темы пройдены и билеты отвечены — дальше их держат повторения.`, goLabel: 'Билеты', openPlan: true };
        case 'exam_past':
            return { say: 'Экзамен уже прошёл. Режим можно выключить в «Билетах».', goLabel: 'Билеты', openPlan: true };
        case 'exam_off':
            return { say: 'Режим экзамена выключен.', goLabel: 'Отлично' };
        case 'reviews_left':
            return { say: `${days} Главное сделано. Остались повторения — можно ещё подход.`, goLabel: 'Ещё подход' };
        default:
            return { say: `${days} Следующие темы откроются, когда пройденные закрепятся в повторениях.`, goLabel: 'Билеты', openPlan: true };
    }
}

window.startPathRun = function() {
    const sub = activeRunSubject();
    const p = starterPreview;
    const step = p && p.subject === sub && Date.now() - p.at < 5 * 60 * 1000 ? p.step : null;
    if (p && p.subject === sub && p.scope === 'exam') {
        if (step && step.type === 'done') {
            const ex = examDoneView(step);
            if (ex.extra) pathRun.start(sub, { scope: 'exam', extra: true });
            else if (ex.openPlan) openExamModal();
            else pathRun.start(sub, { scope: 'exam' });
            return;
        }
        pathRun.start(sub, { scope: 'exam', step });
        return;
    }
    if (step && step.type === 'done') {
        // День закрыт: кнопка предлагает следующее разумное действие
        if (step.reason === 'limit' && step.next_up) startTopicRun(true, sub);
        else if (step.reason === 'reviews_left') pathRun.start(sub);
        else if (typeof openKnowledgeGraphModal === 'function') openKnowledgeGraphModal(sub);
        return;
    }
    pathRun.start(sub, { step });
};

// Одна порция «урок → все его карточки» (в том числе тема сверх нормы)
window.startTopicRun = function(extra = false, subject = null) {
    const sub = subject || activeRunSubject();
    pathRun.start(sub, { scope: 'topic', extra });
};

// Строка про экзамен под главной кнопкой: приглашение загрузить билеты или прогресс подготовки
function renderStarterExam(exam) {
    const el = document.getElementById('starter-exam');
    const text = document.getElementById('starter-exam-text');
    if (!el || !text) return;
    el.classList.toggle('starter-exam-on', !!exam.active);
    if (!exam.active) {
        text.textContent = 'Готовишься к экзамену? Загрузи билеты';
    } else if (exam.status === 'matching') {
        text.textContent = 'Разбираю билеты…';
    } else if (exam.status === 'failed') {
        text.textContent = 'Разбор билетов не удался — открыть';
    } else {
        const t = exam.tickets || {};
        const d = exam.days_left;
        const ready = exam.readiness ? ` · готовность ${exam.readiness.percent}%` : '';
        text.textContent = d < 0
            ? 'Экзамен прошёл — выключить режим'
            : `Экзамен через ${d} ${pathRunPlural(d, 'день', 'дня', 'дней')}${ready} · закреплено ${t.strong || 0} из ${t.ok || 0}`;
    }
}

// Несколько предметов с экзаменом: подсказка, куда сегодня идти в первую очередь (ближе срок и ниже готовность — выше)
let starterPrioritySubject = '';
async function refreshStarterPriority(subject) {
    const el = document.getElementById('starter-priority');
    const text = document.getElementById('starter-priority-text');
    if (!el || !text) return;
    el.classList.add('hidden');
    try {
        const res = await apiFetch('/api/exams/priorities');
        if (!res.ok) return;
        const items = (await res.json()).items || [];
        if (items.length < 2 || items[0].subject === subject) return;
        const top = items[0];
        starterPrioritySubject = top.subject;
        text.textContent = `Срочнее: «${top.title && top.title !== 'Билеты' ? top.title : top.subject}» — экзамен через ${top.days_left} ${pathRunPlural(top.days_left, 'день', 'дня', 'дней')}, готовность ${top.readiness}%`;
        el.classList.remove('hidden');
    } catch (_) { /* подсказка не критична */ }
}

window.switchToPrioritySubject = function() {
    const sel = document.getElementById('subject-selector');
    if (!sel || !starterPrioritySubject) return;
    sel.value = starterPrioritySubject;
    if (sel.onchange) sel.onchange({ target: sel });
    if (typeof refreshPathRunButton === 'function') refreshPathRunButton();
};

function renderStarter({ say, emo, step, goLabel, goEnabled = true }) {
    if (typeof setCatWidget === 'function') setCatWidget('starter-cat', 'starter-say', emo || 'idle', say);
    const cat = document.getElementById('starter-cat');
    if (cat) cat.classList.remove('intro-cat');
    const card = document.getElementById('starter-step');
    if (card) {
        card.classList.toggle('hidden', !step);
        if (step) {
            document.getElementById('starter-step-icon').textContent = step.icon;
            document.getElementById('starter-step-kind').textContent = step.kind;
            document.getElementById('starter-step-name').textContent = step.name;
        }
    }
    const btn = document.getElementById('btn-path-run');
    const text = document.getElementById('btn-path-run-text');
    if (text) text.textContent = goLabel;
    if (btn) btn.disabled = !goEnabled;
}

// Стартовый экран: кот говорит, что будет первым шагом, кнопка запускает его одним нажатием
window.refreshPathRunButton = async function(attempt = 0) {
    if (!document.getElementById('btn-path-run')) return;
    const subject = activeRunSubject();
    if (!subject || subject === 'all') {
        // Список предметов мог ещё не загрузиться — пробуем чуть позже
        if (attempt < 3) setTimeout(() => refreshPathRunButton(attempt + 1), 700);
        else renderStarter({ say: 'Выбери предмет вверху — и начнём.', emo: 'idle', goLabel: 'Начать', goEnabled: false });
        return;
    }
    try {
        const exam = typeof loadExamOverview === 'function' ? await loadExamOverview(subject) : { active: false };
        renderStarterExam(exam);
        refreshStarterPriority(subject);
        const scope = exam.active && exam.status === 'ready' && exam.phase !== 'past' ? 'exam' : 'day';
        const res = await apiFetch(`/api/path/${encodeURIComponent(subject)}/next?scope=${scope}`);
        if (!res.ok) return;
        const step = await res.json();
        starterPreview = { subject, step, scope, at: Date.now() };
        if (scope === 'exam' && step.type === 'done') {
            const ex = examDoneView(step);
            renderStarter({ say: ex.say, emo: 'happy', goLabel: ex.goLabel });
            return;
        }
        if (step.type === 'done') {
            const say = {
                limit: step.next_up
                    ? `Норма на сегодня закрыта. Дальше по пути — «${step.next_up}». Мозгу полезно дать всё уложить, но можно взять тему сверх нормы.`
                    : 'Норма на сегодня закрыта. Мозгу нужно время, чтобы всё улеглось.',
                reviews_left: 'Главное на сегодня сделано. Остались повторения — можно ещё короткий подход.'
            }[step.reason] || 'Сегодня всё сделано. Новые темы откроются, когда пройденные закрепятся в повторениях.';
            const goLabel = step.reason === 'limit' && step.next_up ? 'Ещё тема'
                : (step.reason === 'reviews_left' ? 'Ещё подход' : 'Посмотреть путь');
            renderStarter({ say, emo: 'happy', goLabel });
            return;
        }
        const d = describePathStep(step, true);
        const view = PATH_RUN_STEP_VIEW[step.type];
        renderStarter({
            say: d.say,
            emo: d.emo,
            step: { icon: view.icon, kind: view.kind, name: d.name },
            goLabel: 'Начать'
        });
    } catch (_) { /* стартовый экран не критичен: кнопка всё равно запустит путь */ }
};

// ----------------------------------------------------------------------------
// Итог дня: довольная круглая мордочка покачивается (CSS), а из-за неё волнами
// вылетают конфетти. Два слоя <pre>: сзади конфетти, спереди морда. Там, где морда,
// конфетти не рисуются — поэтому они «выпрыгивают» из-за неё.
// ----------------------------------------------------------------------------

const DAY_W = 44;
const DAY_H = 22;
const DAY_SWAP = { '/': '\\', '\\': '/', '(': ')', ')': '(', '[': ']', ']': '[', '<': '>', '>': '<' };
// Левая половина морды (14 знаков); правая получается зеркалом — морда всегда симметрична
const DAY_FACE_HALF = [
    '    /\\',
    '   /  \\',
    "  /    '-.____",
    ' /',
    '|',
    '|',
    '|',
    '|   ~~       \\',
    '|          \\_/',
    ' \\',
    "  '.",
    "    '-._______"
];
const DAY_EYES = {
    happy: ['  /\\  ', ' /  \\ '],
    blink: ['      ', ' ---- '],
    open:  [' (  ) ', '  ()  ']
};
const DAY_FACE_X = 8;
const DAY_FACE_Y = DAY_H - DAY_FACE_HALF.length - 1;
const DAY_CONFETTI = ['*', '+', 'o', '.', "'", '`', '^', '~'];

function dayMirror(half) {
    const left = half.padEnd(14, ' ');
    return left + [...left].reverse().map(ch => DAY_SWAP[ch] || ch).join('');
}

function dayBlankGrid() {
    return Array.from({ length: DAY_H }, () => Array(DAY_W).fill(' '));
}

function dayStamp(grid, x, y, lines) {
    lines.forEach((line, dy) => {
        const row = grid[y + dy];
        if (!row) return;
        [...line].forEach((ch, dx) => {
            if (ch !== ' ' && x + dx >= 0 && x + dx < DAY_W) row[x + dx] = ch;
        });
    });
}

function dayFaceGrid(eyes) {
    const grid = dayBlankGrid();
    dayStamp(grid, DAY_FACE_X, DAY_FACE_Y, DAY_FACE_HALF.map(dayMirror));
    const e = DAY_EYES[eyes] || DAY_EYES.happy;
    dayStamp(grid, DAY_FACE_X + 4, DAY_FACE_Y + 5, e);
    dayStamp(grid, DAY_FACE_X + 28 - 4 - 6, DAY_FACE_Y + 5, e);
    // Усы за контуром морды
    dayStamp(grid, DAY_FACE_X - 3, DAY_FACE_Y + 7, ['---', ' --']);
    dayStamp(grid, DAY_FACE_X + 28, DAY_FACE_Y + 7, ['---', '-- ']);
    return grid;
}

// Маска «за мордой»: в каждой строке всё от первого до последнего знака морды (+1 на покачивание)
function dayFaceMask() {
    const grid = dayFaceGrid('happy');
    return grid.map(row => {
        const first = row.findIndex(ch => ch !== ' ');
        if (first < 0) return null;
        let last = row.length - 1;
        while (row[last] === ' ') last--;
        return [first - 1, last + 1];
    });
}

let dayCelebration = null;

function dayConfettiWave(st) {
    const cx = DAY_FACE_X + 14;
    for (let k = 0; k < 28; k++) {
        const x = DAY_FACE_X + 3 + Math.random() * 22;
        st.confetti.push({
            x,
            y: DAY_FACE_Y + 3 + Math.random() * 5,
            vx: (x - cx) / 14 * 1.1 + (Math.random() - 0.5) * 0.6,
            vy: -(1.0 + Math.random() * 0.9),
            ch: DAY_CONFETTI[k % DAY_CONFETTI.length]
        });
    }
}

function dayRenderConfetti(st) {
    const grid = dayBlankGrid();
    st.confetti.forEach(p => {
        const x = Math.round(p.x), y = Math.round(p.y);
        if (y < 0 || y >= DAY_H || x < 0 || x >= DAY_W) return;
        const span = st.mask[y];
        if (span && x >= span[0] && x <= span[1]) return; // за мордочкой
        grid[y][x] = p.ch;
    });
    return grid.map(r => r.join('')).join('\n');
}

function showDayCelebration(result) {
    const t = result.today || {};
    const overlay = document.getElementById('day-celebration');
    const faceEl = document.getElementById('day-cat');
    const confettiEl = document.getElementById('day-confetti');
    if (!overlay || !faceEl || !confettiEl) return;

    const answered = t.answered || 0;
    const title = document.getElementById('day-title');
    if (title) title.textContent = (t.accuracy || 0) >= 85 ? 'День закрыт на отлично' : 'День закрыт';

    const lines = [];
    if (answered) lines.push(`Карточек: ${answered}${t.accuracy !== null && t.accuracy !== undefined ? ` · точность ${t.accuracy}%` : ''}`);
    if (t.lessons) lines.push(`Новых тем: ${t.lessons}`);
    if (t.practice) lines.push(`Практика: ${t.practice.score}/${t.practice.total}`);
    if (result.next_up) lines.push(`Завтра по пути: «${result.next_up}»`);
    const statsEl = document.getElementById('day-stats');
    if (statsEl) {
        statsEl.innerHTML = '';
        lines.forEach((l, i) => {
            const p = document.createElement('p');
            p.className = 'day-stat-line';
            p.style.animationDelay = `${1.2 + i * 0.3}s`;
            p.textContent = l;
            statsEl.appendChild(p);
        });
    }
    const closeBtn = document.getElementById('day-close');
    if (closeBtn) {
        closeBtn.style.animationDelay = '';
        closeBtn.classList.remove('day-close-in');
        void closeBtn.offsetWidth;
        closeBtn.classList.add('day-close-in');
    }

    overlay.classList.remove('hidden');
    triggerHaptic('success');
    faceEl.textContent = dayFaceGrid('happy').map(r => r.join('')).join('\n');

    if (prefersReducedMotion()) {
        confettiEl.textContent = '';
        return;
    }

    const st = { confetti: [], mask: dayFaceMask(), tick: 0, timer: null };
    dayCelebration = st;
    dayConfettiWave(st);

    st.timer = setInterval(() => {
        st.tick++;
        // Волна конфетти каждые ~2.5 с
        if (st.tick % 36 === 0) dayConfettiWave(st);
        st.confetti.forEach(p => { p.x += p.vx; p.y += p.vy; p.vy += 0.09; p.vx *= 0.985; });
        st.confetti = st.confetti.filter(p => p.y < DAY_H && p.x > -2 && p.x < DAY_W + 2);
        confettiEl.textContent = dayRenderConfetti(st);
        // Изредка моргает, иногда удивлённо распахивает глаза
        const phase = st.tick % 60;
        const eyes = phase === 20 || phase === 21 ? 'blink' : (phase >= 44 && phase < 50 ? 'open' : 'happy');
        if (eyes !== st.eyes) {
            st.eyes = eyes;
            faceEl.textContent = dayFaceGrid(eyes).map(r => r.join('')).join('\n');
        }
    }, 70);
}

// Тап по экрану — сразу показать итоги и кнопку
window.dayCelebrationSkip = function() {
    document.querySelectorAll('#day-stats .day-stat-line').forEach(p => { p.style.animationDelay = '0s'; });
    const closeBtn = document.getElementById('day-close');
    if (closeBtn) closeBtn.style.animationDelay = '0s';
};

window.closeDayCelebration = function() {
    if (dayCelebration) clearInterval(dayCelebration.timer);
    dayCelebration = null;
    const overlay = document.getElementById('day-celebration');
    if (overlay) overlay.classList.add('hidden');
    if (typeof refreshPathRunButton === 'function') refreshPathRunButton();
};

window.showDayCelebration = showDayCelebration;
