
// ============================================================================
// «ПРОДОЛЖИТЬ ПУТЬ»: ЗАНЯТИЕ ОДНОЙ КНОПКОЙ
// Сервер выбирает следующий шаг (/api/path/{subject}/next): разминка-повторение →
// урок → его карточки → практика вперемешку → итог дня. Кот объявляет каждый шаг,
// а в конце удачного дня — большой ASCII-кот на весь экран.
// ============================================================================

const PATH_RUN_STEP_VIEW = {
    review:   { icon: 'history',        kind: 'Разминка' },
    cards:    { icon: 'style',          kind: 'Закрепление' },
    lesson:   { icon: 'school',         kind: 'Новая тема' },
    practice: { icon: 'psychology_alt', kind: 'Практика на различение' }
};

const pathRun = {
    active: false,
    subject: '',
    done: [],
    step: null,
    stepStarted: false,
    note: '',
    practiceCount: null
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
        case 'lesson':
            return {
                name: step.node_name,
                say: 'Новая тема! Сначала угадай ответ, потом я объясню. Минуты три.',
                emo: 'surprised'
            };
        case 'practice':
            return {
                name: `${n} ${pathRunPlural(n, 'задание', 'задания', 'заданий')} вперемешку`,
                say: 'Практика вперемешку: учимся отличать похожее. Ошибаться здесь нормально.',
                emo: 'think'
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
}

async function fetchPathStep(done) {
    const res = await apiFetch(`/api/path/${encodeURIComponent(pathRun.subject)}/next?done=${encodeURIComponent(done.join(','))}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
}

pathRun.start = async function(subject) {
    if (!subject || subject === 'all') {
        alert('Сначала выбери предмет.');
        return;
    }
    triggerHaptic('medium');
    Object.assign(pathRun, { active: true, subject, done: [], step: null, stepStarted: false, note: '', practiceCount: null });
    showPathRunOverlay(true);
    await pathRun.loadNext();
};

pathRun.loadNext = async function() {
    setPathRunView({ say: pathRun.note ? `${pathRun.note} Смотрю, что дальше…` : 'Смотрю, с чего начать…', emo: 'think', goLabel: '…', goEnabled: false });
    let step;
    try {
        step = await fetchPathStep(pathRun.done);
    } catch (e) {
        console.error('Сбой выбора следующего шага:', e);
        setPathRunView({ say: 'Не получилось связаться с сервером. Попробуем ещё раз?', emo: 'confused', goLabel: 'Повторить' });
        pathRun.step = null;
        return;
    }
    pathRun.step = step;
    pathRun.stepStarted = false;
    if (step.type === 'done') {
        pathRun.finish(step);
        return;
    }
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
        pathRun.stop();
        return;
    }
    triggerHaptic('light');
    if (!pathRun.stepStarted) {
        pathRun.done.push(step.type);
        pathRun.stepStarted = true;
    }
    if (step.type === 'review' || step.type === 'cards') {
        showPathRunOverlay(false);
        if (typeof switchTab === 'function') switchTab('train');
        startSession(step.type === 'review' ? 'review' : 'new');
    } else if (step.type === 'lesson') {
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
    if (step.type === 'cards') params = `&node_id=${step.node_id}`;
    return { subject: pathRun.subject, params };
};

pathRun.stepFinished = function() {
    if (!pathRun.active) return;
    showPathRunOverlay(true);
    pathRun.loadNext();
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
    if (typeof showSessionStarter === 'function') showSessionStarter();
};

function dayCelebratedKey(subject) {
    return `dg_day_celebrated_${subject}_${new Date().toISOString().slice(0, 10)}`;
}

pathRun.finish = function(result) {
    const t = result.today || {};
    const didSomething = pathRun.done.length > 0 && ((t.answered || 0) > 0 || (t.lessons || 0) > 0);
    let celebrated = false;
    try { celebrated = localStorage.getItem(dayCelebratedKey(pathRun.subject)) === '1'; } catch (_) {}

    if (didSomething && !celebrated) {
        try { localStorage.setItem(dayCelebratedKey(pathRun.subject), '1'); } catch (_) {}
        pathRun.stop();
        showDayCelebration(result);
        return;
    }

    const reasonText = {
        reviews_left: 'Остальные повторения лучше оставить на потом — короткие подходы работают лучше марафона.',
        limit: 'Лимит новых карточек на сегодня исчерпан — мозгу нужно время, чтобы всё улеглось.',
        waiting: 'Новые темы откроются, когда пройденные закрепятся в повторениях. Загляни завтра.'
    }[result.reason] || '';
    const nextUp = result.next_up ? ` Дальше по пути: «${result.next_up}».` : '';
    setPathRunView({
        say: `${didSomething ? 'На сегодня всё!' : 'Сегодня всё уже сделано.'} ${reasonText}${nextUp}`,
        emo: 'happy',
        goLabel: 'Отлично',
        secondaryLabel: null
    });
};

window.startPathRun = function() {
    const sub = typeof getActiveDeckSubject === 'function' ? getActiveDeckSubject() : currentSubject;
    pathRun.start(sub);
};

// Подпись под кнопкой: что будет первым шагом
window.refreshPathRunButton = async function(attempt = 0) {
    const btn = document.getElementById('btn-path-run');
    const sub = document.getElementById('btn-path-run-sub');
    if (!btn || !sub) return;
    const subject = typeof getActiveDeckSubject === 'function' ? getActiveDeckSubject() : currentSubject;
    if (!subject || subject === 'all') {
        // Список предметов мог ещё не загрузиться — пробуем чуть позже
        if (attempt < 3) setTimeout(() => refreshPathRunButton(attempt + 1), 700);
        else sub.textContent = 'Выбери предмет';
        return;
    }
    try {
        const res = await apiFetch(`/api/path/${encodeURIComponent(subject)}/next`);
        if (!res.ok) return;
        const step = await res.json();
        if (step.type === 'done') {
            sub.textContent = 'На сегодня всё сделано';
        } else {
            const d = describePathStep(step, true);
            sub.textContent = `${PATH_RUN_STEP_VIEW[step.type].kind}: ${d.name}`;
        }
    } catch (_) { /* подпись не критична */ }
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
