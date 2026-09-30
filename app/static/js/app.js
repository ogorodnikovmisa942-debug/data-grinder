// ============================================================================
// ИНИЦИАЛИЗАЦИЯ TELEGRAM MINI APP SDK И СБОР ТЕЛЕМЕТРИИ
// ============================================================================
let tgId = 'default_user'; 
if (window.Telegram && window.Telegram.WebApp) {
    try {
        const tg = window.Telegram.WebApp;
        if (typeof tg.ready === 'function') tg.ready(); 
        if (typeof tg.expand === 'function') tg.expand(); 
        if (typeof tg.disableVerticalSwipes === 'function') {
            try { tg.disableVerticalSwipes(); } catch (_) {}
        }
        
        if (typeof tg.requestFullscreen === 'function') {
            try { 
                const p = tg.requestFullscreen(); 
                if (p && typeof p.catch === 'function') p.catch(() => {});
            } catch (_) {}
        }
        
        try {
            if (typeof tg.setHeaderColor === 'function') tg.setHeaderColor('#fbfbfb'); 
            if (typeof tg.setBackgroundColor === 'function') tg.setBackgroundColor('#fbfbfb');
        } catch (_) {}
        
        if (tg.initDataUnsafe && tg.initDataUnsafe.user) {
            tgId = tg.initDataUnsafe.user.id.toString();
        }

        const updateSafeArea = () => {
            try {
                const safeArea = tg.safeAreaInset || { top: 0, bottom: 0 };
                const contentSafeArea = tg.contentSafeAreaInset || { top: 0, bottom: 0 };
                
                const safeTop = Math.max(safeArea.top || 0, contentSafeArea.top || 0, 0);
                const safeBottom = Math.max(safeArea.bottom || 0, contentSafeArea.bottom || 0, 0);
                
                document.documentElement.style.setProperty('--tg-safe-top', `${safeTop}px`);
                document.documentElement.style.setProperty('--tg-safe-bottom', `${safeBottom}px`);
            } catch (_) {}
        };
        
        updateSafeArea();
        
        if (typeof tg.onEvent === 'function') {
            tg.onEvent('safeAreaChanged', updateSafeArea);
            tg.onEvent('contentSafeAreaChanged', updateSafeArea);
        }
    } catch (tgInitErr) {
        console.warn('[Telegram WebApp Init Warning]', tgInitErr);
    }
}

// Динамическое получение активного ID пользователя (Telegram SDK или URL параметр)
function getActiveUserId() {
    if (window.Telegram && window.Telegram.WebApp) {
        if (window.Telegram.WebApp.initDataUnsafe && window.Telegram.WebApp.initDataUnsafe.user) {
            const uid = window.Telegram.WebApp.initDataUnsafe.user.id;
            if (uid) {
                tgId = uid.toString();
                return tgId;
            }
        }
        if (window.Telegram.WebApp.initData) {
            try {
                const params = new URLSearchParams(window.Telegram.WebApp.initData);
                const userRaw = params.get('user');
                if (userRaw) {
                    const uObj = JSON.parse(userRaw);
                    if (uObj && uObj.id) {
                        tgId = uObj.id.toString();
                        return tgId;
                    }
                }
            } catch (_) {}
        }
    }
    try {
        const urlParams = new URLSearchParams(window.location.search);
        const queryTgId = urlParams.get('tg_id') || urlParams.get('user_id');
        if (queryTgId) {
            tgId = queryTgId.trim();
            return tgId;
        }
    } catch (_) {}
    return tgId || 'default_user';
}

// Универсальная обертка для HTTP-запросов с передачей авторизации Telegram
async function apiFetch(url, options = {}) {
    const opts = { ...options };
    opts.headers = { ...(opts.headers || {}) };
    
    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) {
        const initDataTrimmed = window.Telegram.WebApp.initData.trim();
        if (initDataTrimmed) {
            opts.headers['Authorization'] = `tma ${initDataTrimmed}`;
            opts.headers['X-Telegram-Init-Data'] = initDataTrimmed;
        }
    }
    opts.headers['X-User-Id'] = getActiveUserId();

    // Защита от вечного зависания сети на смартфонах (12 секунд таймаут)
    if (!opts.signal && typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function') {
        opts.signal = AbortSignal.timeout(12000);
    }
    
    return fetch(url, opts);
}

// ============================================================================
// НАДЁЖНАЯ ОТПРАВКА ОТВЕТОВ: очередь в localStorage, повторы, идемпотентность (client_id)
// Ответ ставится в очередь сразу, уходит на сервер по порядку; при сбое сети остаётся и уходит позже.
// ============================================================================
const ANSWER_QUEUE_KEY = 'dg_answer_queue_v1';
const ANSWER_QUEUE_MAX = 500;
let answerQueue = [];
let answerFlushing = false;
let answerRetryTimer = null;
let answerRetryDelay = 4000;
let answerLastFailed = false;

function newClientId() {
    try { if (window.crypto && crypto.randomUUID) return crypto.randomUUID(); } catch (_) {}
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function persistAnswerQueue() {
    try { localStorage.setItem(ANSWER_QUEUE_KEY, JSON.stringify(answerQueue)); } catch (_) {}
}

function restoreAnswerQueue() {
    try {
        const raw = localStorage.getItem(ANSWER_QUEUE_KEY);
        const parsed = raw ? JSON.parse(raw) : [];
        if (Array.isArray(parsed)) answerQueue = parsed.filter(x => x && x.card_id && x.client_id).slice(-ANSWER_QUEUE_MAX);
    } catch (_) { answerQueue = []; }
}

function renderAnswerSyncBadge() {
    let el = document.getElementById('answer-sync-badge');
    const pending = answerQueue.length;
    if (!pending || !answerLastFailed) {
        if (el) el.remove();
        return;
    }
    if (!el) {
        el = document.createElement('button');
        el.id = 'answer-sync-badge';
        el.type = 'button';
        el.setAttribute('role', 'status');
        el.setAttribute('aria-live', 'polite');
        el.style.cssText = 'position:fixed;left:50%;transform:translateX(-50%);bottom:calc(var(--tg-safe-bottom,0px) + 72px);z-index:9999;' +
            'padding:8px 14px;border-radius:999px;border:1px solid #f59e0b;background:#1c1917;color:#fbbf24;font:600 12px/1.2 monospace;cursor:pointer;';
        el.onclick = () => flushAnswerQueue(true);
        document.body.appendChild(el);
    }
    el.textContent = `Не отправлено ответов: ${pending}. Нажмите, чтобы повторить`;
}

function scheduleAnswerRetry() {
    answerLastFailed = true;
    renderAnswerSyncBadge();
    if (answerRetryTimer) return;
    answerRetryTimer = setTimeout(() => {
        answerRetryTimer = null;
        flushAnswerQueue();
    }, answerRetryDelay);
    answerRetryDelay = Math.min(answerRetryDelay * 2, 60000);
}

async function flushAnswerQueue(manual = false) {
    if (answerFlushing) return;
    if (manual && answerRetryTimer) { clearTimeout(answerRetryTimer); answerRetryTimer = null; }
    answerFlushing = true;
    let sentAny = false;
    try {
        while (answerQueue.length) {
            const item = answerQueue[0];
            let res;
            try {
                res = await apiFetch('/api/answer', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(item)
                });
            } catch (_) {
                scheduleAnswerRetry();
                return;
            }
            if (res.ok) {
                answerQueue.shift();
                persistAnswerQueue();
                sentAny = true;
                answerRetryDelay = 4000;
            } else if (res.status >= 400 && res.status < 500 && ![401, 408, 429].includes(res.status)) {
                // Ответ невозможно принять (карточка удалена и т.п.) — повторять бессмысленно
                console.warn('[Data Grinder] Ответ отклонён сервером, пропускаю:', res.status, item.card_id);
                answerQueue.shift();
                persistAnswerQueue();
            } else {
                scheduleAnswerRetry();
                return;
            }
        }
        answerLastFailed = false;
    } finally {
        answerFlushing = false;
        renderAnswerSyncBadge();
        if (sentAny && typeof updateGlobalBadges === 'function') {
            try { updateGlobalBadges(); } catch (_) {}
        }
    }
}

// Единая точка отправки ответа: ставит в очередь и запускает отправку
window.queueAnswer = function(payload) {
    answerQueue.push({ ...payload, client_id: newClientId(), answered_at: Date.now() });
    if (answerQueue.length > ANSWER_QUEUE_MAX) answerQueue.shift();
    persistAnswerQueue();
    return flushAnswerQueue();
};

restoreAnswerQueue();

// Часовой пояс пользователя: сервер считает по нему «сегодня», лимит новых карточек и время уведомлений
async function syncUserTimezone() {
    try {
        const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
        if (!tz || localStorage.getItem('dg_tz_sent') === tz) return;
        const res = await apiFetch('/api/config/timezone', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ timezone: tz })
        });
        if (res.ok) localStorage.setItem('dg_tz_sent', tz);
    } catch (_) {}
}
window.addEventListener('load', () => { setTimeout(syncUserTimezone, 1500); });
window.addEventListener('online', () => flushAnswerQueue(true));
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') flushAnswerQueue(); });
window.addEventListener('load', () => { if (answerQueue.length) flushAnswerQueue(); });

// ============================================================================
// ВСТРОЕННАЯ МОБИЛЬНАЯ КОНСОЛЬ ОТЛАДКИ (Eruda DevTools)
// ============================================================================
window.enableEruda = function() {
    if (window.eruda) {
        try { window.eruda.show(); } catch (_) {}
        return;
    }
    const script = document.createElement('script');
    script.src = '/vendor/eruda.min.js';
    script.onload = () => {
        try {
            if (window.eruda) {
                window.eruda.init();
                window.eruda.show();
            }
        } catch (e) {
            console.warn('[Eruda Init Warning]', e);
        }
    };
    document.head.appendChild(script);
};

try {
    const debugParam = new URLSearchParams(window.location.search);
    if (debugParam.get('debug') === '1' || debugParam.get('eruda') === '1') {
        window.enableEruda();
    }
} catch (_) {}

let debugTapCount = 0;
document.addEventListener('click', (e) => {
    if (e.target && (e.target.id === 'session-timer' || e.target.closest('#session-timer-container'))) {
        debugTapCount++;
        if (debugTapCount >= 5) {
            debugTapCount = 0;
            window.enableEruda();
            if (typeof window.showNotification === 'function') {
                window.showNotification("Консоль отладки Eruda активирована", "info");
            }
        }
    }
});

// Global Application State
let cardsQueue = []; let currentIndex = 0; let isFlipped = false; let currentTab = 'train';
let localCardsArchive = []; let currentDataFilter = 'all';
let currentSubject = localStorage.getItem('selected_subject') || 'all';
let currentSessionMode = 'mixed';
let pomodoroInterval = null; let timeRemaining = 52 * 60; let isRestPhase = false; let isTimerRunning = false;  
let currentSessionCounters = { new: 0, learning: 0, review: 0 };
let cardShowTimestamp = 0;
let isSelectionMode = false;
let pressTimer = null;

// Экранирование HTML для защиты от XSS
function escapeHTML(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

// Тактильный отклик (Haptic Feedback) для Telegram WebApp SDK и Web Vibration API
function triggerHaptic(type = 'light') {
    try {
        const haptic = window.Telegram?.WebApp?.HapticFeedback;
        if (haptic) {
            if (['light', 'medium', 'heavy', 'rigid', 'soft'].includes(type)) {
                haptic.impactOccurred(type);
                return;
            } else if (['error', 'success', 'warning'].includes(type)) {
                haptic.notificationOccurred(type);
                return;
            }
        }
        // Fallback для мобильных браузеров (Safari / Chrome на смартфонах)
        if (typeof navigator !== 'undefined' && navigator.vibrate) {
            if (type === 'light') {
                navigator.vibrate(12);
            } else if (type === 'medium') {
                navigator.vibrate(24);
            } else if (type === 'heavy' || type === 'error') {
                navigator.vibrate([30, 40, 30]);
            } else if (type === 'warning') {
                navigator.vibrate([20, 30, 20]);
            } else if (type === 'success') {
                navigator.vibrate([15, 30, 25]);
            }
        }
    } catch (e) {
        // Игнорируем в браузерах без поддержки вибрации
    }
}

// Всплывающие уведомления (Toast Notifications)
window.showNotification = function(message, type = 'info') {
    console.log(`[Notification ${type}]`, message);
    try {
        let container = document.getElementById('toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'toast-container';
            container.className = 'fixed top-4 right-4 z-[9999] flex flex-col gap-2 pointer-events-none max-w-sm w-full px-3';
            document.body.appendChild(container);
        }

        const toast = document.createElement('div');
        const colorClasses = {
            success: 'bg-emerald-950/95 border-emerald-500/60 text-emerald-200 shadow-emerald-950/40',
            error: 'bg-rose-950/95 border-rose-500/60 text-rose-200 shadow-rose-950/40',
            warning: 'bg-amber-950/95 border-amber-500/60 text-amber-200 shadow-amber-950/40',
            info: 'bg-neutral-900/95 border-cyan-500/60 text-cyan-200 shadow-cyan-950/40'
        }[type] || 'bg-neutral-900/95 border-neutral-700 text-neutral-200 shadow-black/40';

        const iconName = {
            success: 'check_circle',
            error: 'error',
            warning: 'warning',
            info: 'info'
        }[type] || 'info';

        toast.className = `flex items-center gap-2.5 px-4 py-3 rounded-2xl border backdrop-blur-md shadow-2xl text-xs font-mono transition-all duration-300 transform translate-y-[-10px] opacity-0 pointer-events-auto ${colorClasses}`;
        toast.innerHTML = `
            <span class="material-symbols-outlined text-lg shrink-0">${iconName}</span>
            <span class="flex-1 leading-snug">${escapeHTML(message)}</span>
        `;

        container.appendChild(toast);

        // Плавное появление
        requestAnimationFrame(() => {
            toast.classList.remove('translate-y-[-10px]', 'opacity-0');
            toast.classList.add('translate-y-0', 'opacity-100');
        });

        // Плавное автоисчезновение
        setTimeout(() => {
            toast.classList.add('opacity-0', 'translate-y-[-10px]');
            setTimeout(() => {
                if (toast.parentNode) toast.parentNode.removeChild(toast);
            }, 300);
        }, 3500);
    } catch (err) {
        alert(message);
    }
};

// Форматирование карточки с пропусками (Cloze Deletion)
// Синтаксис: {{c1::ответ::подсказка}} или {{c1::ответ}}
function formatClozeHTML(rawText, isRevealed = false) {
    if (!rawText) return '';
    const regex = /\{\{c\d+::(.*?)(?:::([^}]*))?\}\}/g;
    if (!regex.test(rawText)) {
        return escapeHTML(rawText);
    }
    let result = '';
    let lastIndex = 0;
    regex.lastIndex = 0;
    let match;
    while ((match = regex.exec(rawText)) !== null) {
        result += escapeHTML(rawText.slice(lastIndex, match.index));
        const answer = match[1] || '';
        const hint = match[2];
        if (isRevealed) {
            result += `<span class="cloze-revealed font-bold">${escapeHTML(answer)}</span>`;
        } else {
            const label = hint ? `[${hint.trim()}]` : '[...]';
            result += `<span class="cloze-blank">${escapeHTML(label)}</span>`;
        }
        lastIndex = regex.lastIndex;
    }
    result += escapeHTML(rawText.slice(lastIndex));
    return result;
}

// Плоский текст без тегов для превью в списках/таблицах
function formatClozePlain(rawText, showAnswer = false) {
    if (!rawText) return '';
    return rawText.replace(/\{\{c\d+::(.*?)(?:::([^}]*))?\}\}/g, (match, answer, hint) => {
        if (showAnswer) return answer;
        return hint ? `[${hint.trim()}]` : '[...]';
    });
}

// ============================================================================
// ГЛОБАЛЬНАЯ НАВИГАЦИЯ С КЛАВИАТУРЫ (Accessibility & Keyboard Hotkeys - Phase 11)
// ============================================================================
window.isAnswerRevealed = false;

window.addEventListener('keydown', (e) => {
    // Check if input element is focused
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;
    if (document.activeElement?.isContentEditable) return;

    // Escape key
    if (e.key === 'Escape' || e.code === 'Escape') {
        const practiceModal = document.getElementById('practice-modal');
        if (practiceModal && !practiceModal.classList.contains('hidden')) {
            if (typeof window.closePracticeModal === 'function') window.closePracticeModal();
            return;
        }
        const kgModal = document.getElementById('kg-modal') || document.getElementById('knowledge-graph-modal');
        if (kgModal && !kgModal.classList.contains('hidden')) {
            if (typeof window.closeKnowledgeGraphModal === 'function') window.closeKnowledgeGraphModal();
            return;
        }
        const cardEditorModal = document.getElementById('card-editor-modal');
        if (cardEditorModal && !cardEditorModal.classList.contains('hidden')) {
            if (typeof window.closeCardEditorModal === 'function') window.closeCardEditorModal();
            return;
        }
        const subjectsModal = document.getElementById('subjects-modal') || document.getElementById('subjects-manager-modal');
        if (subjectsModal && !subjectsModal.classList.contains('hidden')) {
            if (typeof window.closeSubjectsManagerModal === 'function') window.closeSubjectsManagerModal();
            return;
        }
        const filterModal = document.getElementById('filter-modal') || document.getElementById('cards-filter-modal');
        if (filterModal && !filterModal.classList.contains('hidden')) {
            if (typeof window.closeFilterModal === 'function') window.closeFilterModal();
            return;
        }
        const bulkActionBar = document.getElementById('bulk-action-bar');
        if (bulkActionBar && !bulkActionBar.classList.contains('hidden')) {
            if (typeof window.exitBulkMode === 'function') window.exitBulkMode();
            return;
        }
        return;
    }

    // Space or Enter key
    if (e.code === 'Space' || e.key === ' ' || e.key === 'Enter' || e.code === 'Enter') {
        const screenTrain = document.getElementById('screen-train');
        const isTrainActive = screenTrain && !screenTrain.classList.contains('hidden') && (typeof currentTab === 'undefined' || currentTab === 'train');
        if (isTrainActive) {
            const isAnswerHidden = (document.getElementById('train-answer')?.classList.contains('hidden')) ?? (!window.isAnswerRevealed);
            if (isAnswerHidden) {
                e.preventDefault();
                if (typeof window.flipCard === 'function') {
                    window.flipCard();
                } else if (typeof window.showAnswer === 'function') {
                    window.showAnswer();
                }
            }
        }
        return;
    }

    // Number keys ('1', '2', '3', '4')
    if (['1', '2', '3', '4'].includes(e.key) || ['Digit1', 'Digit2', 'Digit3', 'Digit4', 'Numpad1', 'Numpad2', 'Numpad3', 'Numpad4'].includes(e.code)) {
        const keyNum = parseInt(e.key, 10) || parseInt(e.code.replace('Digit', '').replace('Numpad', ''), 10);
        
        const practiceModal = document.getElementById('practice-modal');
        if (practiceModal && !practiceModal.classList.contains('hidden')) {
            const optionBtns = document.querySelectorAll('#practice-container button.practice-option-btn, #practice-container button, #practice-options-list button');
            const targetBtn = optionBtns[keyNum - 1];
            if (targetBtn && !targetBtn.disabled) {
                e.preventDefault();
                targetBtn.click();
            }
            return;
        }

        const screenTrain = document.getElementById('screen-train');
        const isTrainActive = screenTrain && !screenTrain.classList.contains('hidden') && (typeof currentTab === 'undefined' || currentTab === 'train');
        if (isTrainActive) {
            const isAnswerVisible = (window.isAnswerRevealed === true) 
                || (document.getElementById('flashcard')?.classList.contains('rotate-y-180'))
                || (document.getElementById('action-buttons') && !document.getElementById('action-buttons').classList.contains('hidden'));
            
            if (isAnswerVisible) {
                e.preventDefault();
                if (typeof window.rateCard === 'function') {
                    window.rateCard(keyNum);
                } else if (typeof window.submitCardRating === 'function') {
                    window.submitCardRating(keyNum);
                }
            }
        }
    }
});

if (typeof window.closeFilterModal !== 'function') {
    window.closeFilterModal = function() {
        const fm = document.getElementById('filter-modal');
        if (fm) fm.classList.add('hidden');
    };
}
if (typeof window.exitBulkMode !== 'function') {
    window.exitBulkMode = function() {
        if (typeof deactivateSelectionMode === 'function') {
            deactivateSelectionMode();
        } else {
            const bar = document.getElementById('bulk-action-bar');
            if (bar) bar.classList.add('hidden');
        }
    };
}


// ============================================================================
// НАСТРОЙКА АДАПТИВНЫХ ПОДСКАЗОК FSRS
// ============================================================================
const FSRS_LABELS = {
    'default': {
        1: { label: 'Again', hint: 'Не вспомнил' },
        2: { label: 'Hard', hint: 'С трудом' },
        3: { label: 'Good', hint: 'Вспомнил' },
        4: { label: 'Easy', hint: 'Легко' }
    },
    'law_civil': {
        1: { label: 'Again', hint: 'Не вспомнил определение' },
        2: { label: 'Hard', hint: 'Вспомнил с подсказкой' },
        3: { label: 'Good', hint: 'Вспомнил полностью' },
        4: { label: 'Easy', hint: 'Знаю наизусть' }
    },
    'law_civil_rb': {
        1: { label: 'Again', hint: 'Не вспомнил определение' },
        2: { label: 'Hard', hint: 'Вспомнил с подсказкой' },
        3: { label: 'Good', hint: 'Вспомнил полностью' },
        4: { label: 'Easy', hint: 'Знаю наизусть' }
    },
    'python_pro': {
        1: { label: 'Again', hint: 'Не помню синтаксис' },
        2: { label: 'Hard', hint: 'Вспомнил с ошибкой' },
        3: { label: 'Good', hint: 'Написал бы верно' },
        4: { label: 'Easy', hint: 'Пишу на автомате' }
    },
    'chinese_hsk3': {
        1: { label: 'Again', hint: 'Не помню ни иероглиф, ни значение' },
        2: { label: 'Hard', hint: 'Помню значение, забыл иероглиф' },
        3: { label: 'Good', hint: 'Вспомнил иероглиф и значение' },
        4: { label: 'Easy', hint: 'Читаю свободно' }
    }
};

function renderFSRSButtons(cardSubject) {
    const subject = cardSubject || currentSubject;
    let labelsKey = 'default';
    if (FSRS_LABELS[subject]) {
        labelsKey = subject;
    } else {
        if (subject.startsWith('law_') || subject.startsWith('sudou') || subject === 'court_system' || subject === 'civil_law') {
            labelsKey = 'law_civil';
        } else if (subject.startsWith('python_')) {
            labelsKey = 'python_pro';
        } else if (subject.startsWith('chinese_')) {
            labelsKey = 'chinese_hsk3';
        }
    }
    
    const labels = FSRS_LABELS[labelsKey] || FSRS_LABELS['default'];
    const container = document.getElementById('action-buttons');
    if (!container) return;
    
    const colorStyles = {
        1: 'text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/40 border-rose-200/60 dark:border-rose-900/40',
        2: 'text-amber-600 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-950/40 border-amber-200/60 dark:border-amber-900/40',
        3: 'text-emerald-600 dark:text-emerald-400 hover:bg-emerald-50 dark:hover:bg-emerald-950/40 border-emerald-200/60 dark:border-emerald-900/40',
        4: 'text-sky-600 dark:text-sky-400 hover:bg-sky-50 dark:hover:bg-sky-950/40 border-sky-200/60 dark:border-sky-900/40'
    };
    
    container.innerHTML = [1, 2, 3, 4].map(rating => {
        const config = labels[rating];
        const style = colorStyles[rating];
        return `
            <button data-rating="${rating}" class="flex-1 min-w-0 h-full flex flex-col items-center justify-center rounded-xl bg-surface-container-lowest ${style} active:scale-95 transition-all duration-100 border">
                <span class="font-bold text-xs uppercase tracking-wider font-mono">${escapeHTML(config.label)}</span>
                <span class="text-[9px] text-neutral-400 dark:text-neutral-500 uppercase tracking-tight mt-0.5 truncate max-w-full px-1">${escapeHTML(config.hint)}</span>
            </button>
        `;
    }).join('');
}

const COGNITIVE_ASCII_ARTS = [
    `    (  )
     )
  .----------.
  |  COFFEE  |#
  |  RECOVERY|#
  \`----------'
   \`--------'`,
    `     ______   ______
    /      \\ /      \\
   |  LEARN |  DATA  |
   |  MORE  |  DEEPER|
   |_______/ \\_______|`,
    `   |\\=======/|
   | \\     / |
   |  \\   /  |
   |   \\ /   |
   |    X    |
   |   / \\   |
   |  /   \\  |
   | /     \\ |
   |/=======\\|`,
    `      +---------+
     /         /|
    /         / |
   +---------+  +
   |         | /
   |  FOCUS  |/
   +---------+`,
    `  _________________
 [ >_  GRINDING... ]
  |               |
  |  LOG: RUNNING |
  |  SYS: OK      |
  |_______________|`
];

let flashcard, actionButtons, focusToggle, subjectSelector, body, cardText, cardSecondaryText, cardMainText, cardMnemonic, cardMnemonicContainer, cardCounter, progressFill;

/**
 * Динамическая адаптивная типографика для карточек (Zero Overflow Architecture).
 * Классифицирует объем текста на корзины ('short', 'medium', 'long', 'vignette'),
 * Обеспечивает стабильный, комфортный для глаз размер шрифта (17px) без скачков
 * и сброс позиции скролла для длинных ситуационных вопросов.
 */
function applyDynamicCardTypography(element, text) {
    if (!element) return;
    element.removeAttribute('data-text-length');
    element.style.fontSize = '';
    element.style.lineHeight = '';
    
    // Сброс прокрутки наверх при показе новой карточки
    const scrollContainer = element.closest('.card-scroll-clean') || element.closest('.overflow-y-auto') || element.parentElement;
    if (scrollContainer) {
        scrollContainer.scrollTop = 0;
    }
}

const STAGING_CARD_TEMPLATE = `
    <!-- Значки-индикаторы свайпа (штампы APPROVED / DISCARDED) -->
    <div id="staging-badge-accept" class="absolute top-4 right-4 border-2 border-emerald-600 text-emerald-700 dark:text-emerald-400 px-3 py-1 font-bold text-xs uppercase tracking-wider rotate-12 opacity-0 pointer-events-none transition-opacity rounded-xl bg-surface-container-lowest/95 shadow-md flex items-center gap-1 z-30">
        <span class="material-symbols-outlined text-sm">check_circle</span>
        <span>ОДОБРЕНО</span>
    </div>
    <div id="staging-badge-reject" class="absolute top-4 left-4 border-2 border-secondary text-secondary px-3 py-1 font-bold text-xs uppercase tracking-wider -rotate-12 opacity-0 pointer-events-none transition-opacity rounded-xl bg-surface-container-lowest/95 shadow-md flex items-center gap-1 z-30">
        <span class="material-symbols-outlined text-sm">cancel</span>
        <span>ОТКЛОНЕНО</span>
    </div>

    <!-- Шапка карточки -->
    <div class="w-full shrink-0 flex justify-between items-center text-[10px] text-outline border-b border-outline-variant/30 pb-2 mb-2 select-none">
        <div class="flex items-center gap-1.5 min-w-0">
            <span id="staging-card-number" class="font-mono font-medium shrink-0">Карточка 1 из 1</span>
            <span id="staging-card-chapter-badge" class="hidden inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-neutral-100 dark:bg-neutral-800 text-neutral-600 dark:text-neutral-300 font-mono text-[9px] truncate max-w-[180px]">
                <span class="material-symbols-outlined text-[11px]">menu_book</span>
                <span id="staging-card-chapter-text" class="truncate"></span>
            </span>
        </div>
        <span id="staging-card-tier" class="uppercase font-bold text-primary font-mono px-2 py-0.5 rounded-md bg-surface-container shrink-0">medium</span>
    </div>

    <!-- Скроллируемое тело карточки: структурированные блоки с защитой от обрезки -->
    <div class="flex-1 min-h-0 flex flex-col items-stretch gap-2.5 card-scroll-clean overflow-y-auto py-1 px-1 select-text" style="touch-action: pan-y; -webkit-overflow-scrolling: touch;">
        <!-- Блок Вопроса / Кейса (чистый, крупный, без подсказок) -->
        <div class="flex flex-col gap-1 text-left select-text">
            <span class="text-[9px] font-mono font-bold uppercase tracking-wider text-outline flex items-center gap-1">
                <span class="material-symbols-outlined text-[12px]">help</span>
                <span>ВОПРОС</span>
            </span>
            <div id="staging-card-text" class="dynamic-card-text font-bold text-neutral-900 dark:text-neutral-100 leading-snug break-words">...</div>
        </div>

        <!-- Разделитель -->
        <div class="w-full h-px bg-outline-variant/30 my-0.5 shrink-0"></div>

        <!-- Блок Ответа / Дефиниции с бейджем источника нормы -->
        <div class="bg-surface-container/40 dark:bg-neutral-900/50 p-3 rounded-xl border border-outline-variant/30 text-left select-text">
            <div class="flex items-center justify-between mb-1.5 flex-wrap gap-1">
                <span class="text-[9px] font-mono font-bold uppercase tracking-wider text-outline flex items-center gap-1">
                    <span class="material-symbols-outlined text-[12px]">task_alt</span>
                    <span>ОТВЕТ</span>
                </span>
                <span id="staging-card-secondary" class="text-[9px] text-primary font-mono font-bold bg-primary/10 px-2 py-0.5 rounded-md border border-primary/20 empty:hidden"></span>
            </div>
            <div id="staging-card-translation" class="text-xs sm:text-sm text-on-surface leading-relaxed break-words font-medium">...</div>
        </div>

        <!-- Пример (если есть) -->
        <div id="staging-card-example-box" class="border-l-2 border-primary/40 pl-2.5 py-0.5 text-left empty:hidden">
            <div id="staging-card-example" class="text-[11px] text-outline italic break-words">...</div>
        </div>
        
        <!-- Мнемоника / Ассоциация (если есть) -->
        <div id="staging-card-mnemonic-box" class="w-full bg-amber-500/5 dark:bg-amber-500/10 p-2.5 border border-amber-500/20 rounded-xl text-[10px] text-left hidden shadow-xs">
            <span class="text-amber-700 dark:text-amber-400 font-bold uppercase flex items-center gap-1 mb-0.5 font-mono">
                <span class="material-symbols-outlined text-[12px]">psychology</span>
                <span>АССОЦИАЦИЯ:</span>
            </span>
            <span id="staging-card-mnemonic" class="text-neutral-800 dark:text-neutral-200"></span>
        </div>
    </div>

    <!-- Кнопки управления карточкой внутри песочницы -->
    <div class="w-full grid grid-cols-2 gap-2 pt-2 border-t border-outline-variant/30 shrink-0">
        <button onclick="openStagingEditor()" class="border border-outline-variant hover:border-primary text-primary text-[10px] py-2 uppercase font-bold rounded-xl hover:bg-surface-container transition-all flex items-center justify-center gap-1">
            <span class="material-symbols-outlined text-[14px]">edit</span>
            <span>ПРАВИТЬ</span>
        </button>
        <button onclick="regenerateStagingMnemonic()" class="border border-outline-variant hover:border-primary text-primary text-[10px] py-2 uppercase font-bold rounded-xl hover:bg-surface-container transition-all flex items-center justify-center gap-1">
            <span class="material-symbols-outlined text-[14px]">psychology</span>
            <span>МНЕМОНИКА</span>
        </button>
    </div>
`;

let trainDrag = { isDragging: false, hasMoved: false, startX: 0, startY: 0, currentX: 0, currentY: 0 };

function initTrainGestures() {
    const card = document.getElementById('flashcard');
    if (!card || card._train_gestures_bound) return;
    card._train_gestures_bound = true;

    const onStart = (clientX, clientY) => {
        const currentCard = cardsQueue[currentIndex];
        if (!currentCard) return;
        if (cardsQueue.length === 0 || currentIndex >= cardsQueue.length) return;

        trainDrag.isDragging = true;
        trainDrag.hasMoved = false;
        trainDrag.startX = clientX;
        trainDrag.startY = clientY;
        trainDrag.currentX = clientX;
        trainDrag.currentY = clientY;
        card.style.transition = 'none';
    };

    const onMove = (clientX, clientY) => {
        if (!trainDrag.isDragging) return;
        trainDrag.currentX = clientX;
        trainDrag.currentY = clientY;
        const deltaX = clientX - trainDrag.startX;
        const deltaY = clientY - trainDrag.startY;

        if (Math.abs(deltaX) > 8 || Math.abs(deltaY) > 8) {
            trainDrag.hasMoved = true;
        }

        const rotate = deltaX * 0.05;
        const baseFlip = isFlipped ? 'rotateY(180deg) ' : '';
        card.style.transform = `translate(${deltaX}px, ${deltaY * 0.2}px) ${baseFlip}rotate(${rotate}deg)`;

        const badgeGood = document.getElementById('train-badge-good');
        const badgeAgain = document.getElementById('train-badge-again');

        if (deltaX > 20) {
            if (badgeGood) badgeGood.style.opacity = Math.min(1, (deltaX - 20) / 60).toString();
            if (badgeAgain) badgeAgain.style.opacity = '0';
        } else if (deltaX < -20) {
            if (badgeAgain) badgeAgain.style.opacity = Math.min(1, (-deltaX - 20) / 60).toString();
            if (badgeGood) badgeGood.style.opacity = '0';
        } else {
            if (badgeGood) badgeGood.style.opacity = '0';
            if (badgeAgain) badgeAgain.style.opacity = '0';
        }
    };

    const onEnd = () => {
        if (!trainDrag.isDragging) return;
        trainDrag.isDragging = false;
        const deltaX = trainDrag.currentX - trainDrag.startX;
        const badgeGood = document.getElementById('train-badge-good');
        const badgeAgain = document.getElementById('train-badge-again');
        const currentCard = cardsQueue[currentIndex];

        // Обработка жестов для карт в режиме знакомства
        if (currentCard && currentCard.state === 0 && !currentCard.has_seen_intro) {
            if (deltaX > 75) {
                // Свайп вправо: Знаю наизусть
                card.style.transition = 'transform 0.25s ease, opacity 0.25s ease';
                card.style.transform = 'translate(120%, 20px) rotate(15deg)';
                card.style.opacity = '0';
                if (badgeGood) badgeGood.style.opacity = '1';
                setTimeout(() => {
                    card.style.transition = 'none';
                    card.style.transform = '';
                    card.style.opacity = '1';
                    if (badgeGood) badgeGood.style.opacity = '0';
                    if (currentCard.intro_phase === 1 && currentCard._recall_checked) completeIntroduction();
                    else window.fastTrackIntroduction();
                }, 200);
            } else if (deltaX < -75) {
                // Свайп влево: Переход к следующему шагу знакомства
                card.style.transition = 'transform 0.25s ease, opacity 0.25s ease';
                card.style.transform = 'translate(-120%, 20px) rotate(-15deg)';
                card.style.opacity = '0';
                if (badgeAgain) badgeAgain.style.opacity = '1';
                setTimeout(() => {
                    card.style.transition = 'none';
                    card.style.transform = '';
                    card.style.opacity = '1';
                    if (badgeAgain) badgeAgain.style.opacity = '0';
                    const phase = currentCard.intro_phase || 0;
                    if (phase === 0) {
                        advanceIntroduction();
                    } else if (!currentCard._recall_checked) {
                        window.toggleIntroRecall();
                    } else {
                        window.failIntroduction();
                    }
                }, 200);
            } else {
                card.style.transition = 'transform 0.2s ease';
                card.style.transform = '';
                if (badgeGood) badgeGood.style.opacity = '0';
                if (badgeAgain) badgeAgain.style.opacity = '0';
            }
            return;
        }

        // Обычный режим повторения
        if (deltaX > 75) {
            card.style.transition = 'transform 0.25s ease, opacity 0.25s ease';
            const baseFlip = isFlipped ? 'rotateY(180deg) ' : '';
            card.style.transform = `translate(120%, 20px) ${baseFlip}rotate(15deg)`;
            card.style.opacity = '0';
            if (badgeGood) badgeGood.style.opacity = '1';

            setTimeout(() => {
                card.style.transition = 'none';
                card.style.transform = '';
                card.style.opacity = '1';
                if (badgeGood) badgeGood.style.opacity = '0';
                submitCardRating(3);
            }, 200);
        } else if (deltaX < -75) {
            card.style.transition = 'transform 0.25s ease, opacity 0.25s ease';
            const baseFlip = isFlipped ? 'rotateY(180deg) ' : '';
            card.style.transform = `translate(-120%, 20px) ${baseFlip}rotate(-15deg)`;
            card.style.opacity = '0';
            if (badgeAgain) badgeAgain.style.opacity = '1';

            setTimeout(() => {
                card.style.transition = 'none';
                card.style.transform = '';
                card.style.opacity = '1';
                if (badgeAgain) badgeAgain.style.opacity = '0';
                submitCardRating(1);
            }, 200);
        } else {
            card.style.transition = 'transform 0.2s ease';
            card.style.transform = '';
            if (badgeGood) badgeGood.style.opacity = '0';
            if (badgeAgain) badgeAgain.style.opacity = '0';
        }
    };

    card.addEventListener('touchstart', (e) => {
        if (e.target.closest('button, select, input, textarea, a')) return;
        const touch = e.touches[0];
        onStart(touch.clientX, touch.clientY);
    }, { passive: true });

    card.addEventListener('touchmove', (e) => {
        if (!trainDrag.isDragging) return;
        const touch = e.touches[0];
        const dx = Math.abs(touch.clientX - trainDrag.startX);
        const dy = Math.abs(touch.clientY - trainDrag.startY);
        if (dx > dy && dx > 8 && e.cancelable) {
            e.preventDefault();
        }
        onMove(touch.clientX, touch.clientY);
    }, { passive: false });

    card.addEventListener('touchend', () => onEnd());
    card.addEventListener('touchcancel', () => onEnd());

    card.addEventListener('mousedown', (e) => {
        if (e.target.closest('button, select, input, textarea, a')) return;
        onStart(e.clientX, e.clientY);
    });

    window.addEventListener('mousemove', (e) => onMove(e.clientX, e.clientY));
    window.addEventListener('mouseup', () => onEnd());
}

function bindDOMPointers() {
    flashcard = document.getElementById('flashcard');
    actionButtons = document.getElementById('action-buttons');
    focusToggle = document.getElementById('focus-toggle');
    subjectSelector = document.getElementById('subject-selector');
    cardText = document.getElementById('card-text');
    cardSecondaryText = document.getElementById('card-secondary-text');
    cardMainText = document.getElementById('card-main-text');
    cardMnemonic = document.getElementById('card-mnemonic');
    cardMnemonicContainer = document.getElementById('card-mnemonic-container');
    cardCounter = document.getElementById('card-counter');
    progressFill = document.getElementById('progress-fill');
    body = document.body;

    if (subjectSelector) {
        subjectSelector.onchange = (e) => {
            currentSubject = e.target.value; localStorage.setItem('selected_subject', currentSubject);
            cardsQueue = []; currentIndex = 0; fetchActiveSession(); updateGlobalBadges();
            renderFSRSButtons(currentSubject);
            if (typeof checkTodayPracticeStats === 'function') checkTodayPracticeStats(currentSubject);
            if (currentTab === 'data') loadDataTab();
            if (currentTab === 'stats') loadStatsTab();
            if (currentTab === 'config') loadConfigTab(); 
        };
    }
    
    // Переключение класса фокуса на body. Скрытие элементов контролирует main.css
    if (focusToggle) {
        focusToggle.onclick = () => {
            body.classList.toggle('focus-active');
        };
    }

    const cardFront = document.getElementById('card-front');
    const cardBack = document.getElementById('card-back');

    if (cardFront) {
        cardFront.onclick = (e) => {
            if (e && e.target && e.target.closest('button, select, input, textarea, a')) return;
            if (trainDrag && trainDrag.hasMoved) return;
            const card = cardsQueue[currentIndex];
            if (!card) return;
            
            // В режиме знакомства клик по телу карточки плавно продвигает этап обучения
            window.flipCard();
        };
    }

    if (cardBack) {
        cardBack.onclick = (e) => {
            if (e.target.closest('button, select, input, textarea, a')) return; 
            if (trainDrag && trainDrag.hasMoved) return;
            if (cardsQueue.length === 0 || !isFlipped) return;
            window.flipCard();
        };
    }

    initTrainGestures();
}

let isAppLifecycleInitialized = false;

async function syncActiveAppState() {
    try {
        await loadDynamicSubjects(); 
        if (subjectSelector) subjectSelector.value = currentSubject;
        updateGlobalBadges();
        if (typeof updateTariffBanner === 'function') { updateTariffBanner(); }
        if (typeof checkNightQueueStatus === 'function') { checkNightQueueStatus(); }
        if (typeof window.syncTimerWithServer === 'function') { await window.syncTimerWithServer(); }
        try {
            const cfgRes = await apiFetch(`/api/config?subject=${currentSubject}`);
            if (cfgRes.ok) {
                const cfgData = await cfgRes.json();
                window.isExperimentPhase1 = Boolean(cfgData && cfgData.is_experiment_locked);
            }
        } catch (_) {}
    } catch (err) {
        console.warn("[App State Sync Error]", err);
    }
}

async function initApplicationLifecycle() {
    if (isAppLifecycleInitialized) {
        return syncActiveAppState();
    }
    isAppLifecycleInitialized = true;

    // 1. Немедленная синхронная привязка интерфейса и навигации (не ждёт сеть!)
    try {
        bindDOMPointers();
        initNavigation();
        showSessionStarter();
        initPomodoroEngine();
        initArchiveFilters();
    } catch (uiSyncErr) {
        console.error("[UI Sync Init Warning]", uiSyncErr);
    }

    // 2. Инициализация обработчиков кнопок
    try {
        const btnNew = document.getElementById('btn-session-new');
        if (btnNew) {
            btnNew.onclick = (e) => { 
                if (e) { e.preventDefault(); e.stopPropagation(); } 
                startSession('new'); 
            };
        }
        const btnRev = document.getElementById('btn-session-review');
        if (btnRev) {
            btnRev.onclick = (e) => { 
                if (e) { e.preventDefault(); e.stopPropagation(); } 
                startSession('review'); 
            };
        }
        const btnCram = document.getElementById('btn-session-cram');
        if (btnCram) {
            btnCram.onclick = (e) => { 
                if (e) { e.preventDefault(); e.stopPropagation(); } 
                startSession('cram'); 
            };
        }

        const toggleBtn = document.getElementById('bulk-select-toggle');
        if (toggleBtn) {
            toggleBtn.onclick = () => {
                if (isSelectionMode) {
                    deactivateSelectionMode();
                } else {
                    activateSelectionMode();
                }
            };
        }
        
        const surveySubmitBtn = document.getElementById('survey-submit-btn');
        if (surveySubmitBtn) {
            surveySubmitBtn.onclick = (e) => {
                e.stopPropagation();
                submitDailySessionSurvey();
            };
        }
        
        if (currentTab !== 'train' && focusToggle) {
            focusToggle.classList.add('hidden');
        }
    } catch (btnErr) {
        console.warn("[Button Init Warning]", btnErr);
    }

    // 3. Фоновая асинхронная загрузка данных (не блокирует переключение вкладок!)
    try {
        await syncActiveAppState();
        
        if (typeof updateImportExplanation === 'function') { updateImportExplanation(); }
        if (typeof updateTariffBanner === 'function') { setInterval(updateTariffBanner, 60000); }
        if (typeof checkNightQueueStatus === 'function') { setInterval(checkNightQueueStatus, 30000); }
        if (typeof checkDeepLinkOrHash === 'function') { await checkDeepLinkOrHash(); }
        window.addEventListener('hashchange', () => {
            if (typeof checkDeepLinkOrHash === 'function') checkDeepLinkOrHash();
        });
        if (typeof updateAssocPreferenceUI === 'function') {
            updateAssocPreferenceUI(localStorage.getItem('assoc_preference') || 'acoustic');
        }
    } catch (lifecycleErr) {
        console.error("Ошибка при фоновой загрузке данных:", lifecycleErr);
    }
}

window.flipCard = function() {
    const starter = document.getElementById('session-starter');
    if (starter && !starter.classList.contains('hidden')) return;
    if (cardsQueue.length === 0 || currentIndex >= cardsQueue.length) return;
    const card = cardsQueue[currentIndex];
    if (!card) return;

    if (card.state === 0 && !card.has_seen_intro) {
        const phase = card.intro_phase || 0;
        if (phase === 0) {
            advanceIntroduction();
        } else if (!card._recall_checked) {
            window.toggleIntroRecall();
        } else {
            return; // ответ открыт — оценку ставят кнопками «Вспомнил» / «Не вспомнил»
        }
        triggerHaptic('light');
        return;
    }

    triggerHaptic('light');
    if (!isFlipped) {
        isFlipped = true;
        window.isAnswerRevealed = true;
        if (flashcard) flashcard.classList.add('rotate-y-180');
        if (actionButtons) { actionButtons.classList.remove('hidden'); actionButtons.classList.add('flex'); }
        const trainAns = document.getElementById('train-answer');
        if (trainAns) trainAns.classList.remove('hidden');
        executeVoiceSynthesis(card.text);
        if (typeof window.startGlobalPomodoro === 'function') { window.startGlobalPomodoro(); }
    } else {
        isFlipped = false;
        window.isAnswerRevealed = false;
        if (flashcard) flashcard.classList.remove('rotate-y-180');
        if (actionButtons) { actionButtons.classList.add('hidden'); actionButtons.classList.remove('flex'); }
        const trainAns = document.getElementById('train-answer');
        if (trainAns) trainAns.classList.add('hidden');
    }
};
window.showAnswer = window.flipCard;

window.rateCard = function(rating) {
    if (typeof window.submitCardRating === 'function') {
        return window.submitCardRating(rating);
    } else if (typeof submitCardRating === 'function') {
        return submitCardRating(rating);
    }
};

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initApplicationLifecycle);
} else {
    initApplicationLifecycle();
}

// Восстановление из BFCache при повторном запуске через другую кнопку в Telegram
window.addEventListener('pageshow', (event) => {
    if (event.persisted) {
        if (window.Telegram && window.Telegram.WebApp && typeof window.Telegram.WebApp.expand === 'function') {
            try { window.Telegram.WebApp.expand(); } catch (_) {}
        }
        initApplicationLifecycle();
    }
});

// Автоматическое расширение окна при возврате на экран
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        if (window.Telegram && window.Telegram.WebApp && typeof window.Telegram.WebApp.expand === 'function') {
            try { window.Telegram.WebApp.expand(); } catch (_) {}
        }
    }
});

function isLanguageCard(card) {
    if (!card) return false;
    const sub = ((card.subject || currentSubject || '') + '').toLowerCase();
    // Исключаем право, программирование, геометрию, общие предметы
    if (sub.startsWith('law_') || sub.startsWith('python_') || sub.startsWith('code_') || sub === 'geometry' || sub === 'generic') {
        return false;
    }
    const textToSpeak = card.text || '';
    const containsChinese = /[\u4e00-\u9fa5]/.test(textToSpeak);
    if (containsChinese) return true;

    const isLangSubject = sub.includes('chinese') || sub.includes('hsk') || 
                          sub.includes('english') || sub.includes('eng_') || 
                          sub.includes('german') || sub.includes('de_') ||
                          sub.includes('spanish') || sub.includes('es_') ||
                          sub.includes('french') || sub.includes('fr_') ||
                          sub.includes('vocab') || sub.includes('lang');
    return isLangSubject;
}

function isSpeakable(text) {
    if (!text || text === '---') return false;
    const sub = (currentSubject || '').toLowerCase();
    if (sub.startsWith('law_') || sub.startsWith('python_') || sub.startsWith('code_') || sub === 'geometry' || sub === 'generic') {
        return false;
    }
    const containsChinese = /[\u4e00-\u9fa5]/.test(text);
    if (containsChinese) return true;

    const isLangSubject = sub.includes('chinese') || sub.includes('hsk') || 
                          sub.includes('english') || sub.includes('eng_') || 
                          sub.includes('german') || sub.includes('de_') ||
                          sub.includes('spanish') || sub.includes('es_') ||
                          sub.includes('french') || sub.includes('fr_') ||
                          sub.includes('vocab') || sub.includes('lang');
    return isLangSubject;
}

function executeVoiceSynthesis(textToSpeak) {
    if (!window.speechSynthesis || !textToSpeak) return;
    if (!isSpeakable(textToSpeak)) return;
    
    let lang = null;
    const containsChinese = /[\u4e00-\u9fa5]/.test(textToSpeak);
    const sub = (currentSubject || '').toLowerCase();
    
    if (containsChinese || sub.includes('chinese') || sub === 'chinese_hsk3') {
        lang = 'zh-CN';
    } else if (sub.includes('english') || sub.includes('eng_')) {
        lang = 'en-US';
    } else if (sub.includes('german') || sub.includes('de_')) {
        lang = 'de-DE';
    } else if (sub.includes('spanish') || sub.includes('es_')) {
        lang = 'es-ES';
    } else if (sub.includes('french') || sub.includes('fr_')) {
        lang = 'fr-FR';
    }
    
    if (!lang) return;
    
    try {
        window.speechSynthesis.cancel(); 
        const utterance = new SpeechSynthesisUtterance(textToSpeak);
        utterance.rate = 0.85; 
        utterance.lang = lang;
        window.currentUtterance = utterance;
        window.speechSynthesis.speak(utterance);
    } catch (e) { console.error("Сбой аудио-канала:", e); }
}

window.replayAudioForText = function(text) {
    if (!window.speechSynthesis || !text) return;
    if (!isSpeakable(text)) return;
    
    let lang = null;
    const containsChinese = /[\u4e00-\u9fa5]/.test(text);
    const sub = (currentSubject || '').toLowerCase();
    
    if (containsChinese || sub.includes('chinese')) {
        lang = 'zh-CN';
    } else if (sub.includes('english') || sub.includes('eng_')) {
        lang = 'en-US';
    } else if (sub.includes('german') || sub.includes('de_')) {
        lang = 'de-DE';
    } else if (sub.includes('spanish') || sub.includes('es_')) {
        lang = 'es-ES';
    } else if (sub.includes('french') || sub.includes('fr_')) {
        lang = 'fr-FR';
    }
    
    if (!lang) return;
    
    try {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 0.85;
        utterance.lang = lang;
        window.speechSynthesis.speak(utterance);
    } catch (e) {
        console.error("Сбой аудио-канала:", e);
    }
};

window.replayAudio = function() {
    if (cardsQueue.length === 0 || currentIndex >= cardsQueue.length) return;
    const currentCard = cardsQueue[currentIndex];
    if (currentCard && isLanguageCard(currentCard)) {
        executeVoiceSynthesis(currentCard.text);
    }
};

function renderTopCounters() {
    const dueCount = cardsQueue.length - currentIndex;
    document.querySelectorAll('.data-cnt-queue').forEach(el => el.innerText = Math.max(0, dueCount));
    document.querySelectorAll('.data-cnt-evening').forEach(el => el.innerText = window.eveningDueCount || 0);
}

function shuffleArray(array) {
    for (let i = array.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [array[i], array[j]] = [array[j], array[i]];
    }
}


function setAssocPreference(pref) {
    localStorage.setItem('assoc_preference', pref);
    updateAssocPreferenceUI(pref);
}
window.setAssocPreference = setAssocPreference;

function updateAssocPreferenceUI(pref) {
    const btnVis = document.getElementById('btn-assoc-visual');
    const btnAc = document.getElementById('btn-assoc-acoustic');
    if (btnVis && btnAc) {
        btnVis.classList.remove('bg-primary', 'text-on-primary', 'border-primary');
        btnAc.classList.remove('bg-primary', 'text-on-primary', 'border-primary');
        btnVis.classList.add('text-primary', 'border-outline-variant');
        btnAc.classList.add('text-primary', 'border-outline-variant');

        if (pref === 'visual') {
            btnVis.classList.add('bg-primary', 'text-on-primary', 'border-primary');
            btnVis.classList.remove('text-primary', 'border-outline-variant');
        } else {
            btnAc.classList.add('bg-primary', 'text-on-primary', 'border-primary');
            btnAc.classList.remove('text-primary', 'border-outline-variant');
        }
    }
}
window.updateAssocPreferenceUI = updateAssocPreferenceUI;

window.showSurveyDirectly = function() {
    resetCardDOM();
    if (window.surveyCompletedToday) {
        const surveyContainer = document.getElementById('survey-container');
        if (surveyContainer) surveyContainer.classList.add('hidden');
        if (typeof window.showSessionDebrief === 'function') {
            window.showSessionDebrief();
        } else {
            if (cardText) {
                cardText.classList.remove('hidden');
                cardText.textContent = "Очередь пуста";
            }
            if (cardCounter) cardCounter.textContent = "";
            if (progressFill) progressFill.style.width = "100%";
            if (actionButtons) {
                actionButtons.classList.add('hidden');
                actionButtons.classList.remove('flex');
            }
            if (cardSecondaryText) cardSecondaryText.textContent = "";
            if (cardMainText) cardMainText.textContent = "Все задачи решены. Опрос завершен.";
        }
        return;
    }
    const surveyContainer = document.getElementById('survey-container');
    const cardTextEl = document.getElementById('card-text');
    const cardCounterEl = document.getElementById('card-counter');
    if (surveyContainer) {
        surveyContainer.classList.remove('hidden');
        if (cardTextEl) cardTextEl.classList.add('hidden');
        if (cardCounterEl) cardCounterEl.classList.add('hidden');
    }
    if (actionButtons) {
        actionButtons.classList.add('hidden');
        actionButtons.classList.remove('flex');
    }
    if (cardSecondaryText) cardSecondaryText.textContent = "";
    if (cardMainText) cardMainText.textContent = "Сессия завершена. Оцените параметры сессии:";
};

let currentSessionStats = {
    totalAnswered: 0,
    correctCount: 0,
    lapsedCount: 0,
    newCount: 0,
    startTime: null,
    reviewedCards: []
};

function resetCardDOM() {
    isFlipped = false;
    const flashcardEl = document.getElementById('flashcard');
    if (flashcardEl) {
        flashcardEl.style.transform = '';
        flashcardEl.classList.remove('rotate-y-180');
    }
    const debriefContainer = document.getElementById('session-debrief-container');
    const surveyContainer = document.getElementById('survey-container');
    if (debriefContainer) debriefContainer.classList.add('hidden');
    if (surveyContainer) surveyContainer.classList.add('hidden');
    const normalFront = document.getElementById('card-front-normal');
    const introFront = document.getElementById('card-front-intro');
    const front = document.getElementById('card-front');
    const actionBtns = document.getElementById('action-buttons');
    if (normalFront) normalFront.classList.remove('hidden');
    if (introFront) {
        introFront.classList.add('hidden');
        introFront.classList.remove('flex');
        const bodyEl = document.getElementById('card-intro-body');
        if (bodyEl) bodyEl.innerHTML = '';
        const footerEl = document.getElementById('card-intro-footer');
        if (footerEl) footerEl.innerHTML = '';
    }
    if (front) front.classList.remove('introduction-mode');
    if (actionBtns) {
        actionBtns.classList.add('hidden');
        actionBtns.classList.remove('flex');
    }
    const hintEl = document.getElementById('card-front-hint');
    if (hintEl) {
        hintEl.classList.add('hidden');
        hintEl.innerHTML = '';
        hintEl.className = 'text-xs font-semibold font-mono text-primary bg-primary/10 mt-2 px-3 py-1 rounded-full border border-primary/20 hidden';
    }
}

window.showSessionDebrief = async function() {
    // «Продолжить путь»: вместо итогов сессии — следующий шаг занятия
    if (window.pathRun && window.pathRun.active) {
        triggerHaptic('success');
        resetCardDOM();
        window.pathRun.trainFinished(currentSessionStats);
        return;
    }
    triggerHaptic('success');
    resetCardDOM();
    
    const debriefContainer = document.getElementById('session-debrief-container');
    const normalFront = document.getElementById('card-front-normal');
    const introFront = document.getElementById('card-front-intro');
    const actionBtns = document.getElementById('action-buttons');
    const cardCounterEl = document.getElementById('card-counter');
    
    if (normalFront) normalFront.classList.add('hidden');
    if (introFront) {
        introFront.classList.add('hidden');
        introFront.classList.remove('flex');
    }
    if (actionBtns) {
        actionBtns.classList.add('hidden');
        actionBtns.classList.remove('flex');
    }
    if (cardCounterEl) cardCounterEl.textContent = 'Готово';

    if (debriefContainer) {
        debriefContainer.classList.remove('hidden');
    }

    const reviewed = currentSessionStats.totalAnswered || cardsQueue.length || 0;
    const correct = currentSessionStats.correctCount || Math.max(0, reviewed - (currentSessionStats.lapsedCount || 0));
    const accuracy = reviewed > 0 ? Math.max(0, Math.min(100, Math.round((correct / reviewed) * 100))) : 100;
    const evening = window.eveningDueCount || 0;

    const elReviewed = document.getElementById('debrief-stat-reviewed');
    const elAcc = document.getElementById('debrief-stat-accuracy');
    const elEvening = document.getElementById('debrief-stat-evening');
    const elSub = document.getElementById('debrief-subject-text');
    const elBtnNextText = document.getElementById('debrief-btn-next-text');

    if (elReviewed) elReviewed.textContent = reviewed;
    if (elAcc) elAcc.textContent = `${accuracy}%`;
    if (elEvening) elEvening.textContent = evening;
    
    let subDisplay = (currentSubject && currentSubject !== 'all') ? currentSubject.toUpperCase() : 'ВСЕ ПРЕДМЕТЫ';
    if (cardsQueue.length > 0 && cardsQueue[0].subject_title) {
        subDisplay = cardsQueue[0].subject_title.toUpperCase();
    }
    if (elSub) elSub.textContent = subDisplay;

    if (elBtnNextText) {
        if (currentSessionMode === 'new') {
            elBtnNextText.textContent = 'СЛЕДУЮЩИЙ БЛОК (+10)';
        } else if (currentSessionMode === 'cram') {
            elBtnNextText.textContent = 'ЕЩЕ РАУНД ШТУРМА';
        } else {
            elBtnNextText.textContent = 'ПРОДОЛЖИТЬ ПОВТОРЕНИЕ';
        }
    }

    try {
        const res = await apiFetch(`/api/stats/dashboard?subject=${currentSubject}`);
        if (res.ok) {
            const data = await res.json();
            const total = (data.cards_new || 0) + (data.cards_learning || 0) + (data.cards_review || 0);
            const mastered = data.cards_review || 0;
            const progress = total > 0 ? Math.round((mastered / total) * 100) : 0;
            
            const elProgPercent = document.getElementById('debrief-progress-percent');
            const elProgBar = document.getElementById('debrief-progress-bar');
            if (elProgPercent) elProgPercent.textContent = `${progress}%`;
            if (elProgBar) elProgBar.style.width = `${progress}%`;
        }
    } catch (e) {
        console.error("Ошибка обновления прогресса в debrief:", e);
    }
};

window.continueWithNextChunk = function() {
    triggerHaptic('medium');
    startSession(currentSessionMode || 'new');
};

window.openPracticeFromDebrief = function() {
    triggerHaptic('medium');
    const sub = (currentSubject && currentSubject !== 'all') ? currentSubject : getActiveDeckSubject();
    openPracticeModal(sub);
};

window.openGraphFromDebrief = function() {
    triggerHaptic('medium');
    const sub = (currentSubject && currentSubject !== 'all') ? currentSubject : getActiveDeckSubject();
    openKnowledgeGraphModal(sub);
    switchKgView('graph');

    const reviewed = (currentSessionStats.reviewedCards && currentSessionStats.reviewedCards.length > 0)
        ? currentSessionStats.reviewedCards
        : cardsQueue.slice(0, currentIndex || 10);

    const tryHighlight = () => {
        if (!currentKgGraphData || !currentKgGraphData.nodes || currentKgGraphData.nodes.length === 0) return false;
        if (typeof window.highlightSessionInGraph === 'function') {
            window.highlightSessionInGraph(reviewed);
            return true;
        }
        return false;
    };

    if (!tryHighlight()) {
        let attempts = 0;
        const iv = setInterval(() => {
            attempts++;
            if (tryHighlight() || attempts > 15) {
                clearInterval(iv);
            }
        }, 200);
    }
};

window.teleportCurrentCardToGraph = function() {
    if (cardsQueue.length === 0 || currentIndex >= cardsQueue.length) return;
    const card = cardsQueue[currentIndex];
    if (!card) return;

    triggerHaptic('medium');
    const sub = card.subject || (currentSubject !== 'all' ? currentSubject : getActiveDeckSubject());

    openKnowledgeGraphModal(sub);
    switchKgView('graph');

    const tryFocus = () => {
        if (!currentKgGraphData || !currentKgGraphData.nodes || currentKgGraphData.nodes.length === 0) return false;
        const nodes = currentKgGraphData.nodes;
        
        let target = null;
        if (card.id) {
            target = nodes.find(n => n.card_id === card.id || (n.card_ids && n.card_ids.includes(card.id)));
        }
        if (!target && card.organ_slug) {
            target = nodes.find(n => String(n.id) === String(card.organ_slug));
        }
        if (!target && card.translation) {
            const tLower = card.translation.toLowerCase().trim();
            target = nodes.find(n => n.name && n.name.toLowerCase().trim() === tLower);
            if (!target) {
                target = nodes.find(n => n.name && (tLower.includes(n.name.toLowerCase()) || n.name.toLowerCase().includes(tLower)));
            }
        }
        if (!target && card.text) {
            const qLower = card.text.toLowerCase().trim();
            target = nodes.find(n => n.name && qLower.includes(n.name.toLowerCase().trim()));
            if (!target) {
                const words = qLower.match(/[a-zа-яё0-9]{4,}/g) || [];
                for (const w of words) {
                    const stem = w.slice(0, 5);
                    target = nodes.find(n => n.name && n.name.toLowerCase().includes(stem));
                    if (target) break;
                }
            }
        }

        if (target) {
            focusNodeInGraph(target.id);
            return true;
        }
        return false;
    };

    if (!tryFocus()) {
        let attempts = 0;
        const iv = setInterval(() => {
            attempts++;
            if (tryFocus() || attempts > 15) {
                clearInterval(iv);
            }
        }, 200);
    }
};

function showSessionStarter() {
    resetCardDOM();
    const starter = document.getElementById('session-starter');
    const flashcard = document.getElementById('flashcard');
    const progressBar = document.getElementById('progress-bar');
    const sessionCounters = document.getElementById('train-session-counters');
    if (starter) starter.classList.remove('hidden');
    if (flashcard) flashcard.classList.add('hidden');
    if (progressBar) progressBar.classList.add('hidden');
    if (sessionCounters) sessionCounters.classList.add('hidden');
    updateGlobalBadges();
    if (typeof checkTodayPracticeStats === 'function') checkTodayPracticeStats(currentSubject);
}
window.showSessionStarter = showSessionStarter;

window.exitToSessionMenu = function() {
    if (window.pathRun && window.pathRun.active) window.pathRun.trainInterrupted();
    cardsQueue = [];
    currentIndex = 0;
    isFlipped = false;
    if (flashcard) flashcard.classList.remove('rotate-y-180');
    const surveyContainer = document.getElementById('survey-container');
    if (surveyContainer) surveyContainer.classList.add('hidden');
    showSessionStarter();
    renderTopCounters();
    updateGlobalBadges();
};

window.startSession = startSession;
window.fetchActiveSession = fetchActiveSession;

async function startSession(mode) {
    try {
        currentSessionMode = mode;
        currentSessionStats = {
            totalAnswered: 0,
            correctCount: 0,
            lapsedCount: 0,
            newCount: 0,
            startTime: Date.now(),
            reviewedCards: []
        };
        resetCardDOM();
        const starter = document.getElementById('session-starter');
        const flashcard = document.getElementById('flashcard');
        const progressBar = document.getElementById('progress-bar');
        const sessionCounters = document.getElementById('train-session-counters');
        
        if (starter) starter.classList.add('hidden');
        if (flashcard) flashcard.classList.remove('hidden');
        if (progressBar) progressBar.classList.remove('hidden');
        if (sessionCounters) sessionCounters.classList.remove('hidden');
        
        await fetchActiveSession(mode);
    } catch (e) {
        console.error("[startSession Error]", e);
    }
}

// Ошибка загрузки очереди: понятный текст и кнопки повтора (status 0 — сеть/таймаут)
function renderSessionError(mode, status) {
    cardsQueue = [];
    resetCardDOM();
    if (cardText) {
        cardText.classList.remove('hidden');
        cardText.textContent = status === 401 ? "Требуется авторизация" : "Ошибка сессии";
    }
    const frontHint = document.getElementById('card-front-hint');
    if (frontHint) {
        frontHint.classList.remove('hidden');
        frontHint.className = "mt-4 flex flex-col items-center gap-2";
        frontHint.innerHTML = `
            <p class="text-xs text-neutral-500 dark:text-neutral-400 font-sans max-w-[280px] leading-relaxed text-center mb-1">
                ${status === 401 ? "Пожалуйста, откройте приложение через Telegram-бота для доступа к учебному процессу." : (status === 0 ? "Нет связи с сервером. Проверьте интернет и повторите." : "Не удалось загрузить карточки. Проверьте подключение к серверу.")}
            </p>
            <div class="flex gap-2">
                <button type="button" onclick="event.stopPropagation(); window.fetchActiveSession('${mode}');" class="px-4 py-2 border border-primary text-primary font-bold font-mono text-xs uppercase rounded-xl hover:bg-primary/10 transition-colors">
                    [ ПОВТОРИТЬ ПОПЫТКУ ]
                </button>
                <button type="button" onclick="event.stopPropagation(); window.exitToSessionMenu();" class="px-4 py-2 bg-primary text-on-primary font-bold font-mono text-xs uppercase rounded-xl hover:opacity-90 transition-opacity">
                    [ В МЕНЮ СЕССИЙ ]
                </button>
            </div>
        `;
    }
    currentSessionCounters = { new: 0, learning: 0, review: 0 };
    renderTopCounters();
    updateGlobalBadges();
}

async function fetchActiveSession(mode = 'mixed') {
    try {
        currentSessionMode = mode;
        const run = (window.pathRun && window.pathRun.active) ? window.pathRun.sessionQuery() : null;
        const targetSub = run ? run.subject : ((mode === 'cram') ? 'all' : currentSubject);
        const runParams = run ? run.params : '';
        const response = await apiFetch(`/api/session?subject=${encodeURIComponent(targetSub)}&mode=${mode}${runParams}`);
        if (!response.ok) {
            console.error("[Data Grinder] Сбой ответа сессии:", response.status);
            renderSessionError(mode, response.status);
            return;
        }
        const data = await response.json();
        cardsQueue = Array.isArray(data) ? data : [];
        
        // ВНИМАНИЕ: Для 'new' и 'review' не перемешиваем!
        // Сохраняется дидактический порядок: слой (layer) -> topological_rank -> phrase_id
        if (mode === 'cram') {
            shuffleArray(cardsQueue);
        }
        const surveyContainer = document.getElementById('survey-container');
        if (cardsQueue.length === 0 && run) {
            // Шаг пути оказался пустым (всё уже сделано) — сразу к следующему
            resetCardDOM();
            window.pathRun.trainFinished(null);
            return;
        }
        if (cardsQueue.length === 0) {
            resetCardDOM();
            if (surveyContainer) surveyContainer.classList.add('hidden');
            if (cardText) {
                cardText.classList.remove('hidden');
                cardText.textContent = mode === 'review' ? "Все повторено" : (mode === 'new' ? "Все новые изучены" : (mode === 'cram' ? "Штурм недоступен" : "Очередь пуста"));
            }
            const frontHint = document.getElementById('card-front-hint');
            if (frontHint) {
                frontHint.classList.remove('hidden');
                frontHint.className = "mt-4 flex flex-col items-center gap-2";
                const emptyMsg = mode === 'review' 
                    ? "На данный момент нет карточек, требующих повторения." 
                    : (mode === 'new' 
                        ? "Все новые карточки в текущей колоде уже находятся в процессе изучения." 
                        : (mode === 'cram'
                            ? "В режиме штурма повторяются только пройденные карточки. Сначала изучите новые карточки в режиме «Учить новое»."
                            : "Все задачи решены."));
                frontHint.innerHTML = `
                    <p class="text-xs text-neutral-500 dark:text-neutral-400 font-sans max-w-[280px] leading-relaxed text-center mb-1">
                        ${emptyMsg}
                    </p>
                    <button type="button" onclick="event.stopPropagation(); window.exitToSessionMenu();" class="px-4 py-2 bg-primary text-on-primary font-bold font-mono text-xs uppercase rounded-xl hover:opacity-90 active:scale-95 transition-all shadow-sm flex items-center gap-1.5 cursor-pointer">
                        <span class="material-symbols-outlined text-sm">arrow_back</span>
                        <span>В меню сессий</span>
                    </button>
                `;
            }
            if (cardCounter) cardCounter.textContent = "";
            if (progressFill) progressFill.style.width = "100%"; 
            currentSessionCounters = { new: 0, learning: 0, review: 0 };
            renderTopCounters(); 
            updateGlobalBadges(); 
            return;
        }
        
        if (surveyContainer) {
            surveyContainer.classList.add('hidden');
            if (cardText) cardText.classList.remove('hidden');
            if (cardCounter) cardCounter.classList.remove('hidden');
        }
        
        currentIndex = 0; recalculateQueueCounters(); renderCurrentCard(); updateGlobalBadges();
    } catch (error) {
        console.error("[Data Grinder] Ошибка загрузки сессии:", error);
        renderSessionError(mode, 0);
    }
}

function recalculateQueueCounters() {
    const remainingCards = cardsQueue.slice(currentIndex);
    currentSessionCounters.new = remainingCards.filter(c => c.state === 0).length;
    currentSessionCounters.learning = remainingCards.filter(c => c.state === 1 || c.state === 3).length;
    currentSessionCounters.review = remainingCards.filter(c => c.state === 2).length;
    renderTopCounters();
}

function renderCurrentCard() {
    if (currentIndex >= cardsQueue.length) {
        if (cardsQueue.length > 0) {
            if (!window.surveyCompletedToday) {
                window.showSurveyDirectly();
            } else {
                showSessionDebrief();
            }
        } else {
            resetCardDOM();
            showSessionStarter();
        }
        return;
    }
    
    const card = cardsQueue[currentIndex];
    const isNewCard = (card.state === 0) && !card.has_seen_intro;
    
    if (isNewCard) {
        renderIntroductionCard(card);
    } else {
        renderReviewCard(card);
    }
}

// Кот на карточке знакомства: реплика по фазе + реакция на прошлый ответ
const TRAIN_CAT_LINES = {
    preview: 'Новое понятие. Прочитай ответ и пример, потом проверим себя.',
    askPath: 'Помнишь из урока? Сначала вспомни ответ сам, потом открой.',
    ask: 'Попробуй вспомнить ответ, потом открой.',
    retry: 'Второй заход. Теперь получится!',
    revealed: 'Честно: вспомнил или нет? Ошибка тоже помогает запомнить.'
};
let trainCatPending = null;
const catWidgetTimers = {};

// Маленький кот с репликой (карточки, практика): эмоция, моргание, реакция на ответ
function setCatWidget(catId, sayId, emotion, text, reaction) {
    const el = document.getElementById(catId);
    const say = document.getElementById(sayId);
    if (say) say.textContent = text;
    if (!el || typeof LESSON_CAT === 'undefined') return;
    const cat = LESSON_CAT[emotion] || LESSON_CAT.idle;
    clearInterval(catWidgetTimers[catId]);
    el.textContent = cat.frames[0].join('\n');
    el.className = `lesson-cat intro-cat font-mono text-primary cat-emo-${emotion}`;
    if (prefersReducedMotion()) return;
    if (reaction) {
        void el.offsetWidth;
        el.classList.add(`cat-react-${reaction}`);
    }
    if (cat.frames.length > 1) {
        let i = 0;
        catWidgetTimers[catId] = setInterval(() => { i = (i + 1) % cat.frames.length; el.textContent = cat.frames[i].join('\n'); }, 600);
    } else {
        catWidgetTimers[catId] = setInterval(() => {
            el.textContent = LESSON_CAT.idle.blink.join('\n');
            setTimeout(() => { el.textContent = cat.frames[0].join('\n'); }, 160);
        }, 3400);
    }
}
window.setCatWidget = setCatWidget;

function setTrainCat(emotion, text, reaction) {
    setCatWidget('train-cat', 'train-cat-say', emotion, text, reaction);
}

function introAnswerHTML(card, mnemonicFormatted) {
    const example = card.example && card.example !== '---' ? card.example : '';
    return `
        <div class="intro-answer animate-fade-in">
            <span class="intro-answer-label">Ответ</span>
            <div class="intro-answer-text">${escapeHTML(card.translation)}</div>
            ${example ? `<div class="intro-answer-example">«${escapeHTML(example)}»</div>` : ''}
        </div>
        ${mnemonicFormatted ? `
            <div class="intro-mnemonic animate-fade-in">
                <span class="intro-answer-label"><span class="material-symbols-outlined">psychology</span> Ассоциация</span>
                <div class="text-xs sm:text-sm leading-snug break-words">${mnemonicFormatted}</div>
            </div>
        ` : ''}
    `;
}

function renderIntroductionCard(card) {
    isFlipped = false;
    if (flashcard) {
        flashcard.style.transform = '';
        flashcard.classList.remove('rotate-y-180');
    }
    if (actionButtons) {
        actionButtons.classList.add('hidden');
        actionButtons.classList.remove('flex');
    }

    const normalFront = document.getElementById('card-front-normal');
    const introFront = document.getElementById('card-front-intro');
    const front = document.getElementById('card-front');
    if (normalFront) normalFront.classList.add('hidden');
    if (introFront) {
        introFront.classList.remove('hidden');
        introFront.classList.add('flex', 'flex-col', 'justify-between');
    }
    if (front) front.classList.add('introduction-mode');

    // Путь знаний: урок уже был — начинаем сразу с вспоминания (retrieval first)
    if (!card._intro_init) {
        card._intro_init = true;
        card.intro_phase = card.node_id ? 1 : (card.intro_phase || 0);
    }
    const phase = card.intro_phase;
    const revealed = phase === 1 && card._recall_checked;

    const setChip = (badgeId, textId, value) => {
        const badge = document.getElementById(badgeId);
        const text = document.getElementById(textId);
        if (!badge || !text) return;
        text.textContent = value || '';
        badge.classList.toggle('hidden', !value);
        badge.classList.toggle('inline-flex', !!value);
    };
    setChip('card-intro-chapter-badge', 'card-intro-chapter-text', (card.chapter || card.phrase_text || '').trim());
    setChip('card-intro-subject-badge', 'card-intro-subject-text', card.subject_title || '');

    const isIntroCloze = card.content_type === 'cloze' || /\{\{c\d+::/.test(card.text);
    const termEl = document.getElementById('card-intro-term');
    const secEl = document.getElementById('card-intro-secondary');
    if (termEl) {
        if (isIntroCloze) termEl.innerHTML = formatClozeHTML(card.text, !(phase === 1 && !revealed));
        else termEl.textContent = card.text;
        applyDynamicCardTypography(termEl, card.text);
    }
    if (secEl) {
        // Контекст (источник | раздел) без подсказки ответа; в старых колодах он мог спойлерить — там только в обзоре
        const showSec = card.secondary_text && card.secondary_text !== '---' && (phase === 0 || card.node_id);
        secEl.textContent = showSec ? card.secondary_text : '';
        secEl.classList.toggle('hidden', !showSec);
    }

    let mnemonicFormatted = '';
    if (card.mnemonic) {
        const m = card.mnemonic;
        mnemonicFormatted = typeof m === 'object' ? `<strong class="font-bold text-amber-700 dark:text-amber-300">${escapeHTML(m.keyword)}</strong>: ${escapeHTML(m.verbal_cue)}` : escapeHTML(m);
    }

    const bodyEl = document.getElementById('card-intro-body');
    const footerEl = document.getElementById('card-intro-footer');
    const knowLink = `
        <button onclick="event.stopPropagation(); window.fastTrackIntroduction()" class="intro-link">
            <span class="material-symbols-outlined">verified</span><span>Знаю наизусть</span>
        </button>`;

    let catEmo = 'idle', catText = '';
    if (phase === 0) {
        if (bodyEl) bodyEl.innerHTML = introAnswerHTML(card, mnemonicFormatted);
        if (footerEl) footerEl.innerHTML = `
            <button onclick="event.stopPropagation(); advanceIntroduction()" class="intro-btn intro-btn-primary">
                <span class="material-symbols-outlined">quiz</span><span>Проверить себя</span>
            </button>${knowLink}`;
        catEmo = 'talk'; catText = TRAIN_CAT_LINES.preview;
    } else if (!revealed) {
        if (bodyEl) bodyEl.innerHTML = `
            <button onclick="event.stopPropagation(); window.toggleIntroRecall()" class="intro-reveal animate-fade-in">
                <span class="material-symbols-outlined">visibility</span>
                <span class="intro-reveal-title">Открыть ответ</span>
                <span class="intro-reveal-hint">сначала произнеси его про себя</span>
            </button>`;
        if (footerEl) footerEl.innerHTML = `
            <button onclick="event.stopPropagation(); window.toggleIntroRecall()" class="intro-btn intro-btn-primary">
                <span class="material-symbols-outlined">visibility</span><span>Показать ответ</span>
            </button>${card._retry ? '' : knowLink}`;
        catEmo = 'think';
        catText = card._retry ? TRAIN_CAT_LINES.retry : (card.node_id ? TRAIN_CAT_LINES.askPath : TRAIN_CAT_LINES.ask);
    } else {
        if (bodyEl) bodyEl.innerHTML = introAnswerHTML(card, mnemonicFormatted);
        if (footerEl) footerEl.innerHTML = `
            <div class="intro-grade">
                <button onclick="event.stopPropagation(); window.failIntroduction()" class="intro-btn intro-btn-again">
                    <span class="material-symbols-outlined">close</span><span>Не вспомнил</span>
                </button>
                <button onclick="event.stopPropagation(); completeIntroduction()" class="intro-btn intro-btn-good">
                    <span class="material-symbols-outlined">check</span><span>Вспомнил</span>
                </button>
            </div>`;
        catEmo = 'surprised'; catText = TRAIN_CAT_LINES.revealed;
    }

    // Реакция на ответ по предыдущей карточке
    if (trainCatPending) {
        const p = trainCatPending;
        trainCatPending = null;
        setTrainCat(p.emo, `${p.text} ${catText}`, p.reaction);
    } else {
        setTrainCat(catEmo, catText);
    }

    if (cardCounter) cardCounter.textContent = `${currentIndex + 1} / ${cardsQueue.length}`;
    if (progressFill) progressFill.style.width = `${(currentIndex / cardsQueue.length) * 100}%`;
    cardShowTimestamp = Date.now();
}

window.stepBackIntroduction = function() {
    const card = cardsQueue[currentIndex];
    if (card) {
        card.intro_phase = 0;
        card._recall_checked = false;
        renderIntroductionCard(card);
    }
};

window.toggleIntroRecall = function() {
    const card = cardsQueue[currentIndex];
    if (card) {
        card._recall_checked = true;
        renderIntroductionCard(card);
    }
};

function advanceIntroduction() {
    const card = cardsQueue[currentIndex];
    if (card) {
        triggerHaptic('light');
        card.intro_phase = 1;
        card._recall_checked = false;
        renderIntroductionCard(card);
    }
}
window.advanceIntroduction = advanceIntroduction;

function completeIntroduction() {
    const card = cardsQueue[currentIndex];
    if (!card) return;
    triggerHaptic('success');
    trainCatPending = { emo: 'happy', text: card._retry ? 'Вот, получилось!' : 'Есть!', reaction: 'bounce' };
    card.has_seen_intro = true;
    card.state = 1; // Learning
    
    currentSessionStats.totalAnswered = (currentSessionStats.totalAnswered || 0) + 1;
    currentSessionStats.correctCount = (currentSessionStats.correctCount || 0) + 1;
    currentSessionStats.newCount = (currentSessionStats.newCount || 0) + 1;
    if (!currentSessionStats.reviewedCards) currentSessionStats.reviewedCards = [];
    currentSessionStats.reviewedCards.push(card);

    const responseTimeMs = cardShowTimestamp ? (Date.now() - cardShowTimestamp) : 0;
    window.queueAnswer({ card_id: card.id, rating: 3 /* Good */, response_time: responseTimeMs, is_introduction: true });
    
    currentIndex++;
    recalculateQueueCounters();
    renderCurrentCard();
}
window.completeIntroduction = completeIntroduction;

// «Не вспомнил»: честная ошибка (FSRS «Снова»), карточка вернётся в конце подхода (до двух раз)
window.failIntroduction = function() {
    const card = cardsQueue[currentIndex];
    if (!card) return;
    triggerHaptic('warning');
    currentSessionStats.totalAnswered = (currentSessionStats.totalAnswered || 0) + 1;
    currentSessionStats.lapsedCount = (currentSessionStats.lapsedCount || 0) + 1;
    if (!card._retry) currentSessionStats.newCount = (currentSessionStats.newCount || 0) + 1;

    const responseTimeMs = cardShowTimestamp ? (Date.now() - cardShowTimestamp) : 0;
    window.queueAnswer({ card_id: card.id, rating: 1, response_time: responseTimeMs, is_introduction: true });

    const retry = (card._retry || 0) + 1;
    if (retry <= 2) {
        cardsQueue.push({ ...card, intro_phase: 1, _recall_checked: false, _retry: retry });
        trainCatPending = { emo: 'confused', text: 'Ничего, покажу её ещё раз в конце.', reaction: 'shake' };
    } else {
        trainCatPending = { emo: 'confused', text: 'Эту вернём в повторениях.', reaction: 'shake' };
    }
    currentIndex++;
    recalculateQueueCounters();
    renderCurrentCard();
};

window.fastTrackIntroduction = function() {
    const card = cardsQueue[currentIndex];
    if (!card) return;
    triggerHaptic('success');
    trainCatPending = { emo: 'happy', text: 'Лёгкая!', reaction: 'bounce' };
    card.has_seen_intro = true;
    card.state = 2; // Сразу в Review
    
    currentSessionStats.totalAnswered = (currentSessionStats.totalAnswered || 0) + 1;
    currentSessionStats.correctCount = (currentSessionStats.correctCount || 0) + 1;
    currentSessionStats.newCount = (currentSessionStats.newCount || 0) + 1;
    if (!currentSessionStats.reviewedCards) currentSessionStats.reviewedCards = [];
    currentSessionStats.reviewedCards.push(card);

    const responseTimeMs = cardShowTimestamp ? (Date.now() - cardShowTimestamp) : 0;
    window.queueAnswer({ card_id: card.id, rating: 4 /* Easy */, response_time: responseTimeMs, is_introduction: true, is_fast_track: true });
    
    currentIndex++;
    recalculateQueueCounters();
    renderCurrentCard();
};

function renderReviewCard(card) {
    isFlipped = false;
    if (flashcard) {
        flashcard.style.transform = '';
        flashcard.classList.remove('rotate-y-180');
    }
    if (actionButtons) { actionButtons.classList.add('hidden'); actionButtons.classList.remove('flex'); }
    
    // Показываем/скрываем нужные контейнеры на лицевой стороне
    const normalFront = document.getElementById('card-front-normal');
    const introFront = document.getElementById('card-front-intro');
    const front = document.getElementById('card-front');
    
    if (normalFront) normalFront.classList.remove('hidden');
    if (introFront) {
        introFront.classList.add('hidden');
        introFront.classList.remove('flex');
    }
    if (front) front.classList.remove('introduction-mode');
    
    const isCloze = card.content_type === 'cloze' || /\{\{c\d+::/.test(card.text);

    // Хлебные крошки: предмет карточки на лицевой стороне показываем только в общем режиме ("Все предметы")
    const subTitle = card.subject_title || (card.subject ? card.subject.toUpperCase() : '');
    const frontSubBadge = document.getElementById('card-front-subject-badge');
    const frontSubText = document.getElementById('card-front-subject-text');
    if (frontSubBadge && frontSubText) {
        if (subTitle && currentSubject === 'all') {
            frontSubText.textContent = subTitle;
            frontSubBadge.classList.remove('hidden');
            frontSubBadge.classList.add('inline-flex');
        } else {
            frontSubBadge.classList.add('hidden');
            frontSubBadge.classList.remove('inline-flex');
        }
    }

    // Динамический бейдж режима и причины выдачи на лицевой стороне
    const modeBadge = document.getElementById('card-front-mode-badge');
    const modeText = document.getElementById('card-front-mode-text');
    const modeIcon = document.getElementById('card-front-mode-icon');
    if (modeIcon) {
        modeIcon.textContent = card.reason_icon || (isCloze ? 'edit_note' : (currentSessionMode === 'cram' ? 'local_fire_department' : (currentSessionMode === 'new' ? 'school' : 'history')));
    }
    if (modeText) {
        if (card.reason_label) {
            modeText.textContent = card.reason_label;
            if (modeBadge) {
                if (card.reason_type === 'new') {
                    modeBadge.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-primary/10 border border-primary/30 text-primary';
                } else if (card.reason_type === 'intra_relearn') {
                    modeBadge.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-400';
                } else if (card.reason_type === 'intra_learn') {
                    modeBadge.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/30 text-amber-600 dark:text-amber-400';
                } else if (card.reason_type === 'cram') {
                    modeBadge.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-amber-500/10 border border-amber-500/30 text-amber-600 dark:text-amber-400';
                } else {
                    modeBadge.className = 'inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-neutral-100 dark:bg-neutral-800/80 border border-neutral-200/60 dark:border-neutral-700/50 text-neutral-600 dark:text-neutral-300';
                }
            }
        } else if (isCloze) {
            modeText.textContent = 'ПРОПУСК (CLOZE)';
            if (modeBadge) {
                modeBadge.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-violet-500/10 border border-violet-500/30 text-violet-600 dark:text-violet-400';
            }
        } else if (currentSessionMode === 'cram') {
            modeText.textContent = 'РЕЖИМ ШТУРМА';
            if (modeBadge) {
                modeBadge.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-500/10 border border-amber-500/30 text-amber-600 dark:text-amber-400';
            }
        } else if (currentSessionMode === 'new') {
            modeText.textContent = 'ИЗУЧЕНИЕ НОВОГО';
            if (modeBadge) {
                modeBadge.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-primary/10 border border-primary/30 text-primary';
            }
        } else {
            modeText.textContent = 'ПОВТОРЕНИЕ FSRS';
            if (modeBadge) {
                modeBadge.className = 'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-neutral-100 dark:bg-neutral-800/80 border border-neutral-200/60 dark:border-neutral-700/50 text-neutral-600 dark:text-neutral-300';
            }
        }
    }

    const frontChapterBadge = document.getElementById('card-front-chapter-badge');
    const frontChapterText = document.getElementById('card-front-chapter-text');
    let chapterName = (card.chapter || card.phrase_text || '').trim();

    const qLower = (card.text || '').toLowerCase();
    const ansLower = (card.translation || '').toLowerCase();
    const isDefQuery = /\b(какое понятие|какой термин|назовите понятие|назовите термин|что обозначает|what concept|what term|which term)\b/i.test(qLower);

    // Проверка названия главы на спойлер ответа на лицевой стороне
    if (chapterName && ansLower) {
        const chWords = chapterName.toLowerCase().match(/[a-zа-яё0-9]{4,}/g) || [];
        const ansWords = ansLower.match(/[a-zа-яё0-9]{4,}/g) || [];
        const stopWords = new Set(["суда", "суду", "суде", "дело", "дела", "орган", "закон", "право", "кодекс", "понятие", "термин", "case", "term", "rule"]);
        for (const w of chWords) {
            if (stopWords.has(w)) continue;
            const stem = w.slice(0, 5);
            if (ansWords.some(aw => aw.startsWith(stem) || stem.startsWith(aw.slice(0, 5)))) {
                chapterName = '';
                break;
            }
        }
    }
    if (isDefQuery) {
        chapterName = '';
    }

    if (frontChapterBadge && frontChapterText) {
        if (chapterName) {
            frontChapterText.textContent = chapterName;
            frontChapterBadge.classList.remove('hidden');
            frontChapterBadge.classList.add('inline-flex');
        } else {
            frontChapterBadge.classList.add('hidden');
            frontChapterBadge.classList.remove('inline-flex');
        }
    }
    
    if (cardText) {
        if (isCloze) {
            cardText.innerHTML = formatClozeHTML(card.text, false);
        } else {
            cardText.textContent = card.text;
        }
        applyDynamicCardTypography(cardText, card.text);
    }
    
    const hintEl = document.getElementById('card-front-hint');
    if (hintEl) {
        const isLang = isLanguageCard(card);
        let secText = (card.secondary_text || '').trim();
        if (secText === '---') secText = '';
        
        if (secText) {
            // Защита от спойлеров: на лицевой стороне оставляем только нейтральный нормативный контекст (до '|')
            if (secText.includes('|')) {
                const parts = secText.split('|').map(p => p.trim()).filter(Boolean);
                const subLower = (card.subject_title || subTitle || card.subject || '').toLowerCase();
                if (parts[0] && parts[0].toLowerCase() === subLower && parts.length > 1) {
                    secText = parts[1];
                } else {
                    secText = parts[0] || '';
                }
            }

            // Убираем дублирование названия колоды или главы на лицевой стороне карточки
            const secLower = secText.toLowerCase();
            const subTitleLower = (subTitle || '').toLowerCase();
            const cardSubLower = (card.subject || '').toLowerCase();
            const cardSubTitleLower = (card.subject_title || '').toLowerCase();
            const chLower = (card.chapter || '').toLowerCase();

            if (secLower === subTitleLower || secLower === cardSubLower || secLower === cardSubTitleLower || (chLower && secLower === chLower)) {
                secText = '';
            } else if (subTitleLower && secLower.length > 4 && subTitleLower.includes(secLower)) {
                secText = '';
            } else if (cardSubTitleLower && secLower.length > 4 && cardSubTitleLower.includes(secLower)) {
                secText = '';
            }

            if (secText && ansLower) {
                const secWords = secText.toLowerCase().match(/[a-zа-яё0-9]{4,}/g) || [];
                const ansWords = ansLower.match(/[a-zа-яё0-9]{4,}/g) || [];
                const stopWords = new Set(["суда", "суду", "суде", "дело", "дела", "орган", "закон", "право", "кодекс", "понятие", "термин"]);
                for (const w of secWords) {
                    if (stopWords.has(w)) continue;
                    const stem = w.slice(0, 5);
                    if (ansWords.some(aw => aw.startsWith(stem) || stem.startsWith(aw.slice(0, 5)))) {
                        secText = '';
                        break;
                    }
                }
            }
        }

        if (secText && (isLang || !isDefQuery)) {
            hintEl.textContent = secText;
            hintEl.classList.remove('hidden');
        } else {
            hintEl.classList.add('hidden');
        }
    }
    
    // Показываем/скрываем кнопки повторного озвучивания только для языковых карточек
    const showVoice = isLanguageCard(card);
    const btnFront = document.getElementById('voice-btn-front');
    const btnBack = document.getElementById('voice-btn-back');
    if (btnFront) {
        if (showVoice) btnFront.classList.remove('hidden');
        else btnFront.classList.add('hidden');
    }
    if (btnBack) {
        if (showVoice) btnBack.classList.remove('hidden');
        else btnBack.classList.add('hidden');
    }

    // Обновляем подсказки FSRS под текущий предмет
    renderFSRSButtons(card.subject);

    setTimeout(() => {
        // Заполняем оборотную сторону
        const backSubBadge = document.getElementById('card-back-subject-badge');
        const backSubText = document.getElementById('card-back-subject-text');
        if (backSubBadge && backSubText) {
            if (subTitle) {
                backSubText.textContent = subTitle;
                backSubBadge.classList.remove('hidden');
                backSubBadge.classList.add('inline-flex');
            } else {
                backSubBadge.classList.add('hidden');
                backSubBadge.classList.remove('inline-flex');
            }
        }

        const rawChapter = (card.chapter || card.phrase_text || '').trim();
        const backChapterBadge = document.getElementById('card-back-chapter-badge');
        const backChapterText = document.getElementById('card-back-chapter-text');
        if (backChapterBadge && backChapterText) {
            if (rawChapter) {
                backChapterText.textContent = rawChapter;
                backChapterBadge.classList.remove('hidden');
                backChapterBadge.classList.add('inline-flex');
            } else {
                backChapterBadge.classList.add('hidden');
                backChapterBadge.classList.remove('inline-flex');
            }
        }

        const backTerm = document.getElementById('card-back-term-text');
        if (backTerm) {
            if (isCloze) {
                backTerm.innerHTML = formatClozeHTML(card.text, true);
                backTerm.classList.remove('text-2xl', 'text-3xl');
                backTerm.classList.add('text-base', 'sm:text-lg', 'text-left', 'leading-relaxed');
            } else {
                backTerm.textContent = card.text;
                backTerm.classList.remove('text-base', 'sm:text-lg', 'text-left', 'leading-relaxed');
            }
        }

        const secContainer = document.getElementById('card-secondary-container');
        const hasSec = Boolean(card.secondary_text && card.secondary_text !== '---' && card.secondary_text.trim());
        if (secContainer && cardSecondaryText) {
            if (hasSec) {
                cardSecondaryText.textContent = card.secondary_text;
                secContainer.classList.remove('hidden');
            } else {
                secContainer.classList.add('hidden');
            }
        }
        
        if (cardMainText) cardMainText.textContent = card.translation; 
        
        const exampleContainer = document.getElementById('card-example-container');
        const exampleText = document.getElementById('card-example-text');
        const hasEx = Boolean(card.example && card.example.trim() && card.example !== '---');
        if (exampleContainer && exampleText) {
            if (hasEx) {
                exampleText.textContent = `«${card.example.trim()}»`;
                exampleContainer.classList.remove('hidden');
            } else {
                exampleContainer.classList.add('hidden');
            }
        }

        // Управление аккордеоном деталей (Progressive Disclosure)
        const detailsAccordion = document.getElementById('card-details-accordion');
        const detailsBody = document.getElementById('card-details-body');
        const detailsIcon = document.getElementById('card-details-icon');
        if (detailsAccordion) {
            if (hasSec || hasEx) {
                detailsAccordion.classList.remove('hidden');
                // При каждой новой карте оставляем свернутым
                if (detailsBody) detailsBody.classList.add('hidden');
                if (detailsIcon) detailsIcon.textContent = 'expand_more';
            } else {
                detailsAccordion.classList.add('hidden');
            }
        }

        if (cardMnemonicContainer && cardMnemonic) {
            if (card.mnemonic) {
                let m = card.mnemonic;
                if (typeof m === 'object') {
                    cardMnemonic.innerHTML = `<strong class="font-bold text-amber-700 dark:text-amber-300">${escapeHTML(m.keyword)}</strong>: ${escapeHTML(m.verbal_cue)}`;
                } else {
                    cardMnemonic.textContent = m;
                }
                cardMnemonicContainer.classList.remove('hidden');
            } else { 
                cardMnemonicContainer.classList.add('hidden'); 
            }
        }

        // Показываем/скрываем бейдж проблемной карты (Leech)
        const leechBadge = document.getElementById('card-leech-badge');
        const leechText = document.getElementById('card-leech-text');
        const isLeech = card.is_leech || (card.lapses && card.lapses >= 4);
        if (leechBadge) {
            if (isLeech) {
                if (leechText) leechText.innerHTML = `<span class="material-symbols-outlined text-[13px] mr-1">warning</span>СЛОЖНАЯ КАРТА (СБОЕВ: ${card.lapses || 4})`;
                leechBadge.classList.remove('hidden');
                leechBadge.classList.add('flex');
            } else {
                leechBadge.classList.add('hidden');
                leechBadge.classList.remove('flex');
            }
        }
    }, 150);

    if (cardCounter) cardCounter.textContent = `${currentIndex + 1} / ${cardsQueue.length}`;
    if (progressFill) progressFill.style.width = `${(currentIndex / cardsQueue.length) * 100}%`;
    
    cardShowTimestamp = Date.now();
}

window.regenerateMnemonic = async function(e) {
    if (e) e.stopPropagation();
    const currentCard = cardsQueue[currentIndex];
    if (!currentCard) return;
    
    const pref = localStorage.getItem('assoc_preference') || 'acoustic';
    const leechBtn = e ? e.target : null;
    const oldBtnText = leechBtn ? leechBtn.textContent : '';
    if (leechBtn) leechBtn.textContent = '...';
    
    try {
        const res = await apiFetch(`/api/management/cards/${currentCard.id}/regenerate_mnemonic`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ preference: pref })
        });
        if (res.ok) {
            const data = await res.json();
            if (data.mnemonic) {
                currentCard.mnemonic = data.mnemonic;
                const m = data.mnemonic;
                if (cardMnemonicContainer && cardMnemonic) {
                    cardMnemonic.textContent = typeof m === 'object' ? `${m.keyword}: ${m.verbal_cue}` : m;
                    cardMnemonicContainer.classList.remove('hidden');
                }
            }
        } else {
            const err = await res.json().catch(() => ({}));
            alert(err.detail || "Ошибка перегенерации мнемоники");
        }
    } catch (err) {
        console.error("Ошибка мнемоники:", err);
        alert("Ошибка сети при генерации мнемоники");
    } finally {
        if (leechBtn) leechBtn.textContent = oldBtnText;
    }
};

window.toggleCardDetailsAccordion = function() {
    const body = document.getElementById('card-details-body');
    const icon = document.getElementById('card-details-icon');
    if (!body) return;
    const isHidden = body.classList.contains('hidden');
    if (isHidden) {
        body.classList.remove('hidden');
        if (icon) icon.textContent = 'expand_less';
        triggerHaptic('light');
    } else {
        body.classList.add('hidden');
        if (icon) icon.textContent = 'expand_more';
        triggerHaptic('light');
    }
};

window.submitCardRating = function(rating) {
    if (cardsQueue.length === 0 || currentIndex >= cardsQueue.length) return;
    const currentCard = cardsQueue[currentIndex];
    if (!currentCard) return;

    if (rating === 1) {
        triggerHaptic('error');
    } else if (rating === 2) {
        triggerHaptic('medium');
    } else if (rating === 3) {
        triggerHaptic('light');
    } else if (rating === 4) {
        triggerHaptic('success');
    }

    const payloadCardId = currentCard.id;
    const responseTimeMs = cardShowTimestamp ? (Date.now() - cardShowTimestamp) : 0;
    const hasAssoc = currentCard.mnemonic ? true : false;

    currentSessionStats.totalAnswered = (currentSessionStats.totalAnswered || 0) + 1;
    if (rating >= 3) {
        currentSessionStats.correctCount = (currentSessionStats.correctCount || 0) + 1;
    } else if (rating === 1) {
        currentSessionStats.lapsedCount = (currentSessionStats.lapsedCount || 0) + 1;
    }
    if (!currentSessionStats.reviewedCards) currentSessionStats.reviewedCards = [];
    currentSessionStats.reviewedCards.push(currentCard);

    if (rating === 1) { 
        if (currentCard.state === 2) {
            currentCard.state = 3; 
            currentCard.is_anchored = true;
        }
        currentCard.lapses = (currentCard.lapses || 0) + 1;
        if (currentCard.lapses >= 4) {
            currentCard.is_leech = true;
        }
        const copy = { ...currentCard, _intra_relearn: true };
        const remainingCount = cardsQueue.length - (currentIndex + 1);
        if (remainingCount >= 4) {
            // Вставляем через 3-4 карточки внутри сессии
            const offset = 3 + Math.floor(Math.random() * 2);
            const insertIndex = currentIndex + 1 + offset;
            cardsQueue.splice(insertIndex, 0, copy);
        } else {
            cardsQueue.push(copy);
        }
    }

    currentIndex++;
    recalculateQueueCounters(); 
    renderCurrentCard();

    window.queueAnswer({
        card_id: payloadCardId,
        rating: rating,
        response_time: responseTimeMs,
        has_association: hasAssoc,
        is_cram: currentSessionMode === 'cram'
    });
};

function submitCardRating(rating) {
    return window.submitCardRating(rating);
}

if (document.getElementById('action-buttons')) {
    document.getElementById('action-buttons').addEventListener('click', (e) => {
        const targetButton = e.target.closest('button'); 
        if (!targetButton) return;
        e.stopPropagation(); 
        const rating = parseInt(targetButton.getAttribute('data-rating'));
        if (rating && typeof window.submitCardRating === 'function') {
            window.submitCardRating(rating);
        }
    });
}

async function updateGlobalBadges() {
    try {
        const res = await apiFetch(`/api/stats/dashboard?subject=${currentSubject}`);
        if (!res.ok) return;
        const data = await res.json();
        if (!data || typeof data !== 'object') return;
        window.surveyCompletedToday = data.survey_completed || false;
        window.eveningDueCount = data.due_evening || 0;
        
        const totalCards = (data.cards_new || 0) + (data.cards_learning || 0) + (data.cards_review || 0);
        const dueCount = (Array.isArray(cardsQueue) ? cardsQueue.length : 0) - currentIndex;
        
        // Если опрос сегодня пройден, и очредь пуста/завершена, скрываем его
        if (window.surveyCompletedToday && (cardsQueue.length === 0 || currentIndex >= cardsQueue.length)) {
            const surveyContainer = document.getElementById('survey-container');
            if (surveyContainer) surveyContainer.classList.add('hidden');
            if (cardText) {
                cardText.classList.remove('hidden');
                cardText.textContent = "Очередь пуста";
            }
            if (cardSecondaryText) cardSecondaryText.textContent = "";
            if (cardMainText) cardMainText.textContent = "Все задачи решены. Опрос завершен.";
            if (cardCounter) cardCounter.textContent = "";
            if (progressFill) progressFill.style.width = "100%";
        }
        
        renderTopCounters();
        renderSessionStarterButtons(data);
        if (typeof refreshPathRunButton === 'function') refreshPathRunButton();
        
        const dataTab = document.getElementById('tab-text-data');
        const learnTab = document.getElementById('tab-text-train');
        if (dataTab) dataTab.innerText = totalCards > 0 ? `DATA [${totalCards}]` : 'DATA';
        if (learnTab) {
            if (dueCount > 0) {
                learnTab.innerText = `LEARN [${dueCount}]`;
                learnTab.className = "text-[10px] font-bold tracking-wider font-mono text-secondary";
            } else {
                learnTab.innerText = 'LEARN';
                learnTab.className = "text-[10px] font-bold tracking-wider font-mono text-primary";
            }
        }
    } catch (e) { console.error("Ошибка расчета бэйджей:", e); }
}

function renderSessionStarterButtons(data) {
    if (!data) return;
    const btnNewText = document.getElementById('btn-session-new-text');
    const btnNewBadge = document.getElementById('btn-session-new-badge');
    const btnNew = document.getElementById('btn-session-new');
    
    const btnReviewText = document.getElementById('btn-session-review-text');
    const btnReviewBadge = document.getElementById('btn-session-review-badge');
    const btnReview = document.getElementById('btn-session-review');
    
    const btnCramText = document.getElementById('btn-session-cram-text');
    const btnCramBadge = document.getElementById('btn-session-cram-badge');

    // 1. Повторение (due_reviews_now: REV просроченные + LRN)
    const dueCount = data.due_reviews_now !== undefined ? data.due_reviews_now : (data.cards_learning + data.cards_review);
    if (btnReview && btnReviewBadge && btnReviewText) {
        if (dueCount > 0) {
            btnReviewText.textContent = "[ ПОВТОРЕНИЕ ]";
            btnReviewBadge.textContent = `${dueCount} КАРТ`;
            btnReviewBadge.className = "px-2 py-0.5 rounded-md text-[10px] font-bold bg-secondary text-white shadow-xs animate-pulse";
            btnReview.className = "w-full flex items-center justify-between px-4 border-2 border-secondary text-secondary py-2.5 font-bold tracking-wide hover:bg-secondary hover:text-white transition-all text-xs font-mono uppercase rounded-xl shadow-md cursor-pointer";
        } else {
            btnReviewText.textContent = "[ ПОВТОРЕНИЕ ]";
            btnReviewBadge.textContent = "0 (ВСЕ ПОВТОРЕНО)";
            btnReviewBadge.className = "px-2 py-0.5 rounded-md text-[10px] font-bold bg-neutral-100 dark:bg-neutral-800 text-neutral-400";
            btnReview.className = "w-full flex items-center justify-between px-4 border border-neutral-200 dark:border-neutral-800 text-neutral-400 py-2.5 font-bold tracking-wide transition-all text-xs font-mono uppercase rounded-xl opacity-75 cursor-pointer";
        }
    }

    // 2. Учить новое (new_remaining_today с учетом дневного лимита)
    const newRemaining = data.new_remaining_today !== undefined ? data.new_remaining_today : (data.cards_new || 0);
    const dailyLimit = data.daily_new_limit || 20;
    const totalNew = (data.cards_new !== undefined) ? data.cards_new : (data.unlearned_in_deck || 0);
    if (btnNew && btnNewBadge && btnNewText) {
        if (newRemaining > 0) {
            btnNewText.textContent = "[ УЧИТЬ НОВОЕ ]";
            btnNewBadge.textContent = `${newRemaining} ИЗ ${dailyLimit}`;
            btnNewBadge.className = "px-2 py-0.5 rounded-md text-[10px] font-bold bg-primary/10 text-primary border border-primary/20";
            btnNew.className = "w-full flex items-center justify-between px-4 border border-primary text-primary py-2.5 font-bold tracking-wide hover:bg-primary hover:text-on-primary transition-all text-xs font-mono uppercase rounded-xl shadow-xs cursor-pointer";
        } else if (totalNew > 0) {
            // Дневной лимит исчерпан, но новые карточки в колоде ЕСТЬ — разрешаем учить дальше (over-limit study)
            btnNewText.textContent = "[ УЧИТЬ ЕЩЕ ]";
            btnNewBadge.textContent = `+${totalNew} В КОЛОДЕ`;
            btnNewBadge.className = "px-2 py-0.5 rounded-md text-[10px] font-bold bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20";
            btnNew.className = "w-full flex items-center justify-between px-4 border border-amber-500/60 text-amber-600 dark:text-amber-400 py-2.5 font-bold tracking-wide hover:bg-amber-500 hover:text-white transition-all text-xs font-mono uppercase rounded-xl shadow-xs cursor-pointer";
        } else {
            // Карточек со state == 0 в базе действительно 0
            btnNewText.textContent = "[ УЧИТЬ НОВОЕ ]";
            btnNewBadge.textContent = "0 НОВЫХ";
            btnNewBadge.className = "px-2 py-0.5 rounded-md text-[10px] font-bold bg-neutral-100 dark:bg-neutral-800 text-neutral-400";
            btnNew.className = "w-full flex items-center justify-between px-4 border border-neutral-200 dark:border-neutral-800 text-neutral-400 py-2.5 font-bold tracking-wide transition-all text-xs font-mono uppercase rounded-xl opacity-75 cursor-pointer";
        }
    }

    // 3. Штурм (сложные карточки со всех предметов без влияния на fsrs)
    const cramAvailable = data.cards_cram_available !== undefined 
        ? data.cards_cram_available 
        : (data.cards_learning + data.cards_review);
    const btnCram = document.getElementById('btn-session-cram');
    if (btnCramText && btnCramBadge) {
        btnCramText.textContent = "[ ШТУРМ ]";
        if (cramAvailable > 0) {
            btnCramBadge.textContent = `${cramAvailable} КАРТ (СЛОЖНЫЕ)`;
            btnCramBadge.className = "px-2 py-0.5 rounded-md text-[10px] font-bold bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20";
            if (btnCram) {
                btnCram.className = "w-full flex items-center justify-between px-4 border border-amber-500/40 hover:border-amber-500 text-amber-700 dark:text-amber-300 py-2.5 font-bold tracking-wide transition-all text-xs font-mono uppercase rounded-xl border-dashed hover:bg-amber-500/5 cursor-pointer shadow-xs";
            }
        } else {
            btnCramBadge.textContent = "0 (НЕТ ИЗУЧЕННЫХ)";
            btnCramBadge.className = "px-2 py-0.5 rounded-md text-[10px] font-bold bg-neutral-100 dark:bg-neutral-800 text-neutral-400";
            if (btnCram) {
                btnCram.className = "w-full flex items-center justify-between px-4 border border-neutral-200 dark:border-neutral-800 text-neutral-400 py-2.5 font-bold tracking-wide transition-all text-xs font-mono uppercase rounded-xl opacity-75 border-dashed cursor-pointer";
            }
        }
    }
}

window.switchTab = function(targetTab) {
    if (!targetTab) return;
    currentTab = targetTab;
    
    if (typeof body !== 'undefined' && body) {
        if (targetTab !== 'train') {
            body.classList.remove('focus-active');
        }
    }

    const focusToggleBtn = document.getElementById('focus-toggle');
    if (focusToggleBtn) {
        if (targetTab === 'train') {
            focusToggleBtn.classList.remove('hidden');
        } else {
            focusToggleBtn.classList.add('hidden');
        }
    }
    
    const navButtons = document.querySelectorAll('.nav-link');
    navButtons.forEach(b => { 
        if (b.getAttribute('data-tab') === targetTab) {
            b.classList.remove('text-outline'); 
            b.classList.add('text-primary'); 
        } else {
            b.classList.remove('text-primary'); 
            b.classList.add('text-outline'); 
        }
    });
    
    document.querySelectorAll('.app-screen').forEach(screen => { 
        screen.classList.add('hidden'); 
        screen.classList.remove('flex-1', 'flex', 'flex-col'); 
    });
    
    const targetScreen = document.getElementById(`screen-${targetTab}`);
    if (targetScreen) {
        targetScreen.classList.remove('hidden');
        targetScreen.classList.add('flex-1', 'flex', 'flex-col'); 
    }
    
    try {
        if (targetTab === 'data' && typeof loadDataTab === 'function') loadDataTab(); 
        if (targetTab === 'stats' && typeof loadStatsTab === 'function') loadStatsTab(); 
        if (targetTab === 'config' && typeof loadConfigTab === 'function') loadConfigTab();
    } catch (tabSwitchErr) {
        console.error("[Tab Content Load Error]", tabSwitchErr);
    }
};

function initNavigation() {
    const navButtons = document.querySelectorAll('.nav-link');
    navButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault(); 
            const targetTab = btn.getAttribute('data-tab'); 
            if (targetTab && typeof window.switchTab === 'function') {
                window.switchTab(targetTab);
            }
        });
    });
}

async function loadDataTab() {
    const container = document.getElementById('data-container'); 
    if (container) container.innerHTML = '<div class="text-sm font-mono text-outline py-md text-center">Загрузка архива...</div>';
    try {
        const res = await apiFetch(`/api/data/cards?subject=${currentSubject}`); 
        if (!res.ok) {
            const errData = await res.json().catch(() => ({}));
            throw new Error(errData.detail || `HTTP ${res.status}`);
        }
        const data = await res.json();
        localCardsArchive = Array.isArray(data.cards) ? data.cards : []; 
        renderFilteredArchiveDOM();
    } catch (e) { 
        console.error("[Archive Load Error]", e);
        if (container) {
            container.innerHTML = `
                <div class="flex flex-col items-center justify-center py-lg text-center gap-2 font-mono">
                    <div class="text-xs text-error font-bold">${escapeHTML(e.message || 'Ошибка загрузки архива')}</div>
                    <button onclick="loadDataTab()" class="mt-2 px-3 py-1.5 border border-primary text-primary text-xs rounded-xl hover:bg-primary/10 transition-colors uppercase font-bold">
                        Повторить попытку
                    </button>
                </div>
            `;
        }
    }
}

function initArchiveFilters() {
    const filterButtons = document.querySelectorAll('#archive-filter-bar button');
    filterButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            filterButtons.forEach(b => { b.className = "px-xs py-0.5 text-outline hover:text-primary border border-transparent"; });
            btn.className = "px-xs py-0.5 bg-primary text-on-primary border border-primary";
            currentDataFilter = btn.getAttribute('data-filter'); 
            renderFilteredArchiveDOM();
        });
    });
}

// РЕНДЕРИНГ СТРОКИ АРХИВА С ДОБАВЛЕНИЕМ КНОПКИ МИГРАЦИИ, РЕДАКТИРОВАНИЯ И ЧЕКБОКСОВ
function renderFilteredArchiveDOM() {
    const container = document.getElementById('data-container');
    if (!container) return;
    const cards = Array.isArray(localCardsArchive) ? localCardsArchive : [];
    const filtered = cards.filter(c => {
        if (!c) return false;
        if (currentDataFilter === 'all') return true; 
        if (currentDataFilter === 'new') return c.state === 0; 
        if (currentDataFilter === 'review') return c.state > 0; 
        return true;
    });
    
    const totalCount = cards.length;
    const filteredCount = filtered.length;
    const countBadge = document.getElementById('archive-count');
    if (countBadge) {
        if (filteredCount === totalCount) {
            countBadge.innerText = `(${totalCount})`;
        } else {
            countBadge.innerText = `(${filteredCount}/${totalCount})`;
        }
    }
    
    if (filtered.length === 0) { 
        container.innerHTML = '<div class="text-sm font-mono text-outline py-md text-center">Категория пуста</div>'; 
        return; 
    }
    
    container.innerHTML = filtered.map(c => {
        const labels = ['NEW', 'LRN', 'REV', 'REL'];
        return `
            <div class="flex justify-between items-center py-2.5 px-2 font-mono text-sm gap-sm border-b border-outline-variant/30 archive-row cursor-pointer rounded-xl hover:bg-neutral-100/70 dark:hover:bg-neutral-800/50 transition-colors" 
                 id="archive-row-${c.id}" 
                 data-card-id="${c.id}"
                 onmousedown="startPress(event, ${c.id})"
                 onmouseup="cancelPress()"
                 onmouseleave="cancelPress()"
                 ontouchstart="startPress(event, ${c.id})"
                 ontouchend="cancelPress()"
                 ontouchmove="cancelPress()"
                 onclick="onRowClick(event, ${c.id})">
                <div class="flex items-center gap-xs w-full min-w-0">
                    <input type="checkbox" class="card-checkbox hidden rounded-md border-neutral-300 dark:border-neutral-700 text-primary focus:ring-0 mr-xs" data-card-id="${c.id}" onchange="onCardCheckboxChange(event)">
                    <div class="flex justify-between items-center w-full min-w-0">
                        <span class="font-bold text-base text-primary w-1/5 truncate select-none" title="${escapeHTML(formatClozePlain(c.text))}">${escapeHTML(formatClozePlain(c.text))}</span>
                        <span class="text-outline w-1/4 truncate text-xs select-none">${escapeHTML(c.secondary_text) || '---'}</span>
                        <span class="text-on-surface-variant w-1/3 truncate text-xs select-none">${escapeHTML(c.translation)}</span>
                        <span class="text-[10px] text-outline opacity-60 w-12 text-right font-bold select-none">${labels[c.state] || 'NEW'}</span>
                    </div>
                </div>
                <div class="flex items-center gap-xs shrink-0 archive-row-actions">
                    <button onclick="event.stopPropagation(); requestEditCard(${c.id})" class="text-outline hover:text-primary p-1 font-bold flex items-center justify-center" title="Редактировать"><span class="material-symbols-outlined text-[16px]">edit</span></button>
                    <button onclick="event.stopPropagation(); requestMoveCard(${c.id})" class="text-outline hover:text-primary p-1 font-bold flex items-center justify-center" title="Перенести предмет"><span class="material-symbols-outlined text-[16px]">drive_file_move</span></button>
                    <button onclick="event.stopPropagation(); requestDeleteCard(${c.id})" class="text-outline hover:text-secondary p-1 transition-colors active:scale-95 duration-75 flex items-center justify-center" title="Удалить"><span class="material-symbols-outlined text-[16px]">delete</span></button>
                </div>
            </div>
        `;
    }).join('');
    
    const cbStyle = isSelectionMode ? 'block' : 'none';
    document.querySelectorAll('.card-checkbox').forEach(cb => cb.style.display = cbStyle);
}

// АСИНХРОННЫЙ ПЕРЕНОС КАРТОЧКИ МЕЖДУ ПРЕДМЕТАМИ
async function requestMoveCard(cardId) {
    if (window.isExperimentPhase1) {
        alert("Перенос карточек заблокирован на время Фазы 1 эксперимента.");
        return;
    }
    const selector = document.getElementById('subject-selector');
    if (!selector) return;

    const options = Array.from(selector.options)
        .map(opt => opt.value)
        .filter(val => val !== 'all' && val !== currentSubject);

    if (options.length === 0) {
        alert("Нет других доступных предметов для переноса карточки.");
        return;
    }

    const promptMessage = `Введите код предмета для переноса карточки.\nДоступные дисциплины:\n${options.map(o => `- ${o}`).join('\n')}`;
    const targetSubject = prompt(promptMessage);
    
    if (!targetSubject) return; 
    const cleanTarget = targetSubject.trim().toLowerCase();

    if (!options.includes(cleanTarget)) {
        alert("Указан неверный или несуществующий код предмета.");
        return;
    }

    try {
        const response = await apiFetch(`/api/management/cards/${cardId}/move`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ target_subject: cleanTarget })
        });

        if (response.ok) {
            localCardsArchive = localCardsArchive.filter(c => c.id !== cardId);
            const row = document.getElementById(`archive-row-${cardId}`);
            if (row) row.remove();
            updateGlobalBadges();
        } else {
            alert("Ошибка сервера при переносе карточки.");
        }
    } catch (e) {
        console.error("Критический сбой переноса:", e);
        alert("Сбой сети при переносе карточки.");
    }
}

async function requestDeleteCard(cardId) {
    if (window.isExperimentPhase1) {
        alert("Удаление карточек заблокировано на время Фазы 1 эксперимента.");
        return;
    }
    if (!confirm("Удалить эту карточку навсегда?")) return;
    try {
        const response = await apiFetch(`/api/management/cards/${cardId}`, { method: 'DELETE' });
        if (response.ok) {
            localCardsArchive = localCardsArchive.filter(c => c.id !== cardId);
            const row = document.getElementById(`archive-row-${cardId}`); if (row) row.remove();
            updateGlobalBadges();
        }
    } catch (e) { console.error("Сбой удаления карточки:", e); }
}

function renderMaturity(matData) {
    if (!matData) return;
    const f = matData.fragile || 0;
    const d = matData.developing || 0;
    const m = matData.mature || 0;
    const mast = matData.mastered || 0;
    const total = f + d + m + mast;
    
    const setSeg = (id, count) => {
        const el = document.getElementById(id);
        if (el) {
            const pct = total > 0 ? (count / total) * 100 : 0;
            el.style.width = `${pct}%`;
        }
    };
    setSeg('mat-seg-fragile', f);
    setSeg('mat-seg-dev', d);
    setSeg('mat-seg-mature', m);
    setSeg('mat-seg-mastered', mast);
    
    const setCnt = (id, count) => {
        const el = document.getElementById(id);
        if (el) {
            const pct = total > 0 ? Math.round((count / total) * 100) : 0;
            el.textContent = `${count} (${pct}%)`;
        }
    };
    setCnt('mat-cnt-fragile', f);
    setCnt('mat-cnt-dev', d);
    setCnt('mat-cnt-mature', m);
    setCnt('mat-cnt-mastered', mast);
}

function renderHeatmap(heatmapData) {
    const grid = document.getElementById('heatmap-grid');
    const totalLabel = document.getElementById('heatmap-total-label');
    if (!grid) return;
    
    grid.innerHTML = '';
    const hData = heatmapData || {};
    const now = new Date();
    let totalReviews60d = 0;
    
    // Генерируем даты за последние 60 дней в хронологическом порядке
    const days = [];
    for (let i = 59; i >= 0; i--) {
        const d = new Date(now);
        d.setDate(d.getDate() - i);
        const yyyy = d.getFullYear();
        const mm = String(d.getMonth() + 1).padStart(2, '0');
        const dd = String(d.getDate()).padStart(2, '0');
        const dateKey = `${yyyy}-${mm}-${dd}`;
        const count = hData[dateKey] || 0;
        totalReviews60d += count;
        days.push({ dateKey, count });
    }
    
    if (totalLabel) {
        totalLabel.textContent = `${totalReviews60d} повторений`;
    }
    
    days.forEach(day => {
        const cell = document.createElement('div');
        let bgClass = 'bg-neutral-200 dark:bg-neutral-800 border border-neutral-300 dark:border-neutral-700';
        if (day.count > 0 && day.count <= 4) {
            bgClass = 'bg-emerald-300 dark:bg-emerald-900/80';
        } else if (day.count > 4 && day.count <= 14) {
            bgClass = 'bg-emerald-500 dark:bg-emerald-600';
        } else if (day.count > 14) {
            bgClass = 'bg-emerald-700 dark:bg-emerald-400';
        }
        cell.className = `w-3 h-3 rounded-xs transition-transform hover:scale-125 cursor-pointer ${bgClass}`;
        cell.title = `${day.dateKey}: ${day.count} повторений`;
        grid.appendChild(cell);
    });
}

async function loadStatsTab() {
    try {
        const res = await apiFetch(`/api/stats/dashboard?subject=${currentSubject}`); 
        const data = await res.json();
        const elNew = document.getElementById('stat-new');
        const elLrn = document.getElementById('stat-learning');
        const elRev = document.getElementById('stat-review');
        const elPrg = document.getElementById('stat-progress');
        const elRet = document.getElementById('stat-retention');
        const elStr = document.getElementById('stat-streak');

        if (elNew) elNew.innerText = data.cards_new;
        if (elLrn) elLrn.innerText = data.cards_learning;
        if (elRev) elRev.innerText = data.cards_review;
        if (elPrg) elPrg.innerText = `${data.progress_percent}%`;
        if (elRet) elRet.innerText = `${data.retention_rate_30d}%`;
        if (elStr) elStr.innerText = `${data.streak_days} дней`;
        
        if (data.maturity) renderMaturity(data.maturity);
        if (data.heatmap) renderHeatmap(data.heatmap);

        const titleContainer = document.getElementById('breakdown-title'); 
        const listContainer = document.getElementById('breakdown-list');
        if (titleContainer) {
            titleContainer.innerText = currentSubject === 'all' ? "--- ПРОГРЕСС ПО ПРЕДМЕТАМ ---" : "--- РАЗДЕЛЫ ПРЕДМЕТА ---";
        }
        if (!data.breakdown || data.breakdown.length === 0) { 
            if (listContainer) listContainer.innerHTML = '<div class="text-xs text-outline py-2 text-center">Нет данных по разделам</div>'; 
            return; 
        }
        
        if (listContainer) {
            listContainer.innerHTML = data.breakdown.map(item => {
                const pct = Math.min(100, Math.max(0, item.progress || 0));
                return `
                    <div class="flex flex-col p-3 mb-2 font-mono text-xs gap-2 bg-neutral-50 dark:bg-neutral-900/40 rounded-xl border border-neutral-200/60 dark:border-neutral-800/60">
                        <div class="flex justify-between items-center w-full">
                            <span class="text-on-surface font-bold uppercase truncate max-w-[75%]">${escapeHTML(item.label)}</span>
                            <span class="font-bold text-primary font-mono text-[11px]">${pct}%</span>
                        </div>
                        <div class="w-full h-1.5 bg-neutral-200 dark:bg-neutral-800 rounded-full overflow-hidden">
                            <div class="bg-primary h-full rounded-full transition-all duration-300" style="width: ${pct}%"></div>
                        </div>
                    </div>
                `;
            }).join('');
        }
    } catch (e) { console.error("Ошибка дашборда статистики:", e); }
}

async function loadConfigTab() {
    try {
        const subjLabel = document.getElementById('config-subject-label');
        if (subjLabel) subjLabel.innerText = currentSubject.toUpperCase();

        const res = await apiFetch(`/api/config?subject=${currentSubject}`);
        const data = await res.json();

        window.isExperimentPhase1 = Boolean(data && data.is_experiment_locked);

        // Индикатор Фазы 1 эксперимента в заголовке
        const phaseBadge = document.getElementById('config-phase-badge');
        if (phaseBadge) {
            if (window.isExperimentPhase1) phaseBadge.classList.remove('hidden');
            else phaseBadge.classList.add('hidden');
        }

        // Блокировка нарезки материалов
        const lockBanner = document.getElementById('experiment-phase1-lock-banner');
        const slicingControls = document.getElementById('slicing-controls-container');
        if (window.isExperimentPhase1) {
            if (lockBanner) lockBanner.classList.remove('hidden');
            if (slicingControls) slicingControls.classList.add('hidden');
        } else {
            if (lockBanner) lockBanner.classList.add('hidden');
            if (slicingControls) slicingControls.classList.remove('hidden');
        }

        const shareSubBtn = document.getElementById('btn-share-subject');
        if (shareSubBtn) {
            if (currentSubject === 'all') shareSubBtn.classList.add('hidden');
            else shareSubBtn.classList.remove('hidden');
        }
        const deleteSubBtn = document.getElementById('btn-delete-subject');
        if (deleteSubBtn) {
            if (currentSubject === 'all' || window.isExperimentPhase1) deleteSubBtn.classList.add('hidden');
            else deleteSubBtn.classList.remove('hidden');
        }
        const renameSubBtn = document.getElementById('btn-rename-subject');
        if (renameSubBtn) {
            if (currentSubject === 'all' || window.isExperimentPhase1) renameSubBtn.classList.add('hidden');
            else renameSubBtn.classList.remove('hidden');
        }
        const presetContainer = document.getElementById('config-presets-container');
        const presetNotice = document.getElementById('config-presets-notice');
        if (currentSubject === 'all' && !window.isExperimentPhase1) {
            if (presetContainer) presetContainer.classList.add('hidden');
            if (presetNotice) presetNotice.classList.remove('hidden');
        } else {
            if (presetContainer) presetContainer.classList.remove('hidden');
            if (presetNotice) presetNotice.classList.add('hidden');
            renderPresetButtonsDOM(data.daily_limit);
        }
    } catch (e) { console.error("Ошибка загрузки конфига:", e); }
}

function renderPresetButtonsDOM(activeLimit) {
    [10, 20, 30, 10000].forEach(val => {
        const btn = document.getElementById(`btn-preset-${val}`);
        if (btn) {
            if (window.isExperimentPhase1) {
                if (val === 10) {
                    btn.className = "w-full text-left border p-md transition-all duration-75 flex justify-between items-center bg-primary text-on-primary border-primary font-mono text-xs font-bold uppercase rounded-xl shadow-xs cursor-default";
                    btn.title = "Фиксированный лимит эксперимента (Фаза 1: 10 карт в день)";
                } else {
                    btn.className = "w-full text-left border p-md transition-all duration-75 flex justify-between items-center bg-surface-container-lowest text-neutral-400 dark:text-neutral-600 border-neutral-200 dark:border-neutral-800 font-mono text-xs uppercase rounded-xl shadow-xs opacity-40 cursor-not-allowed pointer-events-none";
                    btn.title = "Зафиксировано на время Фазы 1 эксперимента (10 карт в день)";
                }
            } else {
                if (val === activeLimit) {
                    btn.className = "w-full text-left border p-md transition-all duration-75 flex justify-between items-center bg-primary text-on-primary border-primary font-mono text-xs font-bold uppercase rounded-xl shadow-xs";
                    btn.title = "";
                } else {
                    btn.className = "w-full text-left border p-md transition-all duration-75 flex justify-between items-center bg-surface-container-lowest text-primary border-neutral-300 dark:border-neutral-700 font-mono text-xs uppercase rounded-xl shadow-xs hover:border-primary";
                    btn.title = "";
                }
            }
        }
    });
}

async function setIntensityPreset(limit) {
    if (window.isExperimentPhase1) {
        alert("В Фазе 1 эксперимента дневной лимит зафиксирован на 10 карточек.");
        return;
    }
    renderPresetButtonsDOM(limit);
    try {
        await apiFetch(`/api/config?subject=${currentSubject}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ daily_limit: limit })
        });
        await fetchActiveSession();
    } catch (e) { console.error("Ошибка записи лимита:", e); }
}

window.toggleConfigHelp = function() {
    const content = document.getElementById('config-help-content');
    const icon = document.getElementById('config-help-icon');
    if (content && icon) {
        if (content.classList.contains('hidden')) {
            content.classList.remove('hidden'); icon.textContent = 'expand_less';
        } else {
            content.classList.add('hidden'); icon.textContent = 'expand_more';
        }
    }
}

window.onImportSubjectChange = function(event) {
    const val = event.target.value;
    const newSubInput = document.getElementById('import-new-subject-input');
    const tipEl = document.getElementById('subject-status-tip');
    if (val === '__new__') {
        if (newSubInput) {
            newSubInput.classList.remove('hidden');
            newSubInput.focus();
        }
        if (tipEl) tipEl.textContent = '[НОВЫЙ ПРЕДМЕТ]';
    } else {
        if (newSubInput) {
            newSubInput.classList.add('hidden');
            newSubInput.value = '';
        }
        if (tipEl) tipEl.textContent = val ? `[ВЫБРАН: ${val.toUpperCase()}]` : '[ВЫБЕРИТЕ ПРЕДМЕТ]';
    }
};

window.onStagingSubjectChange = function(event) {
    stagingSubject = event.target.value.trim().toLowerCase();
    const titleEl = document.getElementById('staging-topic-title');
    if (titleEl) titleEl.textContent = `[${stagingSubject.toUpperCase()}] ${stagingTheme}`;
};

function getSelectedImportSubject() {
    const subSel = document.getElementById('import-target-subject');
    if (!subSel) return '';
    let targetSubject = subSel.value;
    if (targetSubject === '__new__') {
        const newSubInp = document.getElementById('import-new-subject-input');
        targetSubject = newSubInp ? newSubInp.value.trim() : '';
    }
    return (targetSubject || '').trim().toLowerCase();
}

async function loadDynamicSubjects() {
    try {
        let subjects = [];
        try {
            const res = await apiFetch('/api/subjects');
            if (res.ok) {
                const serverSubs = await res.json();
                if (Array.isArray(serverSubs)) {
                    subjects = serverSubs;
                    localStorage.setItem('grinder_cached_subjects', JSON.stringify(subjects));
                }
            }
        } catch (netErr) {
            console.warn("Сбой сети при запросе предметов, пробуем локальный кэш:", netErr);
            const cached = localStorage.getItem('grinder_cached_subjects');
            if (cached) {
                try { subjects = JSON.parse(cached); } catch (e) {}
            }
        }

        if (!subjects || !Array.isArray(subjects)) {
            subjects = [];
        }
        const subjectNames = { 
            'chinese_hsk3': 'КИТАЙСКИЙ HSK3', 
            'law_civil': 'ГРАЖДАНСКОЕ ПРАВО', 
            'civil_law': 'ГРАЖДАНСКОЕ ПРАВО',
            'python_pro': 'PYTHON ADVANCED', 
            'geometry': 'ГЕОМЕТРИЯ (ФОРМУЛЫ)', 
            'law_civil_rb': 'ГРАЖДАНСКОЕ ПРАВО РБ' 
        };

        // 1. Селекторы фильтрации карточек/тренировок
        const getSubSlug = (sub) => typeof sub === 'string' ? sub : (sub?.slug || sub?.name || String(sub || ''));
        const selectors = document.querySelectorAll('#subject-selector');
        selectors.forEach(sel => {
            sel.innerHTML = '<option value="all">[ВСЕ ПРЕДМЕТЫ]</option>';
            subjects.forEach(sub => {
                const subStr = getSubSlug(sub);
                const option = document.createElement('option'); 
                option.value = subStr;
                option.textContent = `[${subjectNames[subStr] || subStr.toUpperCase()}]`; 
                sel.appendChild(option);
            });
        });

        // 2. Селектор целевого предмета в панели импорта
        const importSel = document.getElementById('import-target-subject');
        if (importSel) {
            const currentVal = importSel.value;
            importSel.innerHTML = '<option value="" disabled selected>-- ВЫБЕРИТЕ ПРЕДМЕТ --</option>';
            subjects.forEach(sub => {
                const subStr = getSubSlug(sub);
                const opt = document.createElement('option');
                opt.value = subStr;
                opt.textContent = `[${subjectNames[subStr] || subStr.toUpperCase()}]`;
                importSel.appendChild(opt);
            });
            const newOpt = document.createElement('option');
            newOpt.value = '__new__';
            newOpt.textContent = '[+ СОЗДАТЬ НОВЫЙ ПРЕДМЕТ...]';
            importSel.appendChild(newOpt);

            const tipEl = document.getElementById('subject-status-tip');
            const inputNew = document.getElementById('import-new-subject-input');
            const subSlugs = subjects.map(getSubSlug);
            if (currentVal && (subSlugs.includes(currentVal) || currentVal === '__new__')) {
                importSel.value = currentVal;
                if (tipEl) tipEl.textContent = currentVal === '__new__' ? '[НОВЫЙ ПРЕДМЕТ]' : `[ВЫБРАН: ${currentVal.toUpperCase()}]`;
            } else if (currentSubject && currentSubject !== 'all' && subSlugs.includes(currentSubject)) {
                importSel.value = currentSubject;
                if (tipEl) tipEl.textContent = `[ВЫБРАН: ${currentSubject.toUpperCase()}]`;
                if (inputNew) inputNew.classList.add('hidden');
            } else if (subSlugs.length > 0) {
                importSel.value = subSlugs[0];
                if (tipEl) tipEl.textContent = `[ВЫБРАН: ${subSlugs[0].toUpperCase()}]`;
                if (inputNew) inputNew.classList.add('hidden');
            } else {
                importSel.value = '__new__';
                if (inputNew) inputNew.classList.remove('hidden');
                if (tipEl) tipEl.textContent = '[СОЗДАЙТЕ ПРЕДМЕТ]';
            }
        }

        // 3. Селектор предмета в шапке песочницы (Staging Sandbox)
        const stagingSel = document.getElementById('staging-subject-select');
        if (stagingSel) {
            stagingSel.innerHTML = '';
            subjects.forEach(sub => {
                const subStr = getSubSlug(sub);
                const opt = document.createElement('option');
                opt.value = subStr;
                opt.textContent = `[${subjectNames[subStr] || subStr.toUpperCase()}]`;
                stagingSel.appendChild(opt);
            });
            if (typeof stagingSubject !== 'undefined' && stagingSubject) {
                if (!subjects.includes(stagingSubject)) {
                    const opt = document.createElement('option');
                    opt.value = stagingSubject;
                    opt.textContent = `[${stagingSubject.toUpperCase()}]`;
                    stagingSel.appendChild(opt);
                }
                stagingSel.value = stagingSubject;
            }
        }

        bindDOMPointers();
    } catch (e) { 
        console.error("Ошибка загрузки предметов:", e); 
    }
}

function initPomodoroEngine() {
    const sessionTimer = document.getElementById('session-timer'); const restOverlay = document.getElementById('rest-overlay');
    const restTimerDisplay = document.getElementById('rest-timer-display'); const skipRest = document.getElementById('skip-rest');
    const asciiContainer = document.getElementById('rest-ascii-art');
    function formatTime(seconds) { const mins = Math.floor(seconds / 60); const secs = seconds % 60; return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`; }
    function runTimerLoop() {
        if (pomodoroInterval) clearInterval(pomodoroInterval);
        pomodoroInterval = setInterval(async () => {
            if (timeRemaining > 0) {
                timeRemaining--; 
                if (!isRestPhase) { if (sessionTimer) sessionTimer.textContent = formatTime(timeRemaining); } 
                else { if (restTimerDisplay) restTimerDisplay.textContent = formatTime(timeRemaining); }
            } else {
                if (!isRestPhase) {
                    isRestPhase = true; timeRemaining = 17 * 60; if (restTimerDisplay) restTimerDisplay.textContent = formatTime(timeRemaining);
                    const randomIdx = Math.floor(Math.random() * COGNITIVE_ASCII_ARTS.length);
                    if (asciiContainer) asciiContainer.textContent = COGNITIVE_ASCII_ARTS[randomIdx];
                    if (restOverlay) { restOverlay.classList.remove('hidden'); restOverlay.classList.add('flex'); }
                    try { await apiFetch(`/api/timer/rest?tg_id=${tgId}`, { method: 'POST' }); } catch (e) { console.error("Ошибка перерыва:", e); }
                } else {
                    isRestPhase = false; timeRemaining = 52 * 60; if (sessionTimer) sessionTimer.textContent = formatTime(timeRemaining);
                    if (restOverlay) { restOverlay.classList.add('hidden'); restOverlay.classList.remove('flex'); }
                }
            }
        }, 1000);
    }
    window.startGlobalPomodoro = function() { if (isTimerRunning) return; isTimerRunning = true; runTimerLoop(); };
    window.syncTimerWithServer = async function() {
        try {
            const res = await apiFetch(`/api/timer/status?tg_id=${tgId}`); const data = await res.json();
            if (data.is_resting) {
                isRestPhase = true; timeRemaining = data.seconds_left; isTimerRunning = true;
                const randomIdx = Math.floor(Math.random() * COGNITIVE_ASCII_ARTS.length);
                if (asciiContainer) asciiContainer.textContent = COGNITIVE_ASCII_ARTS[randomIdx];
                if (restTimerDisplay) restTimerDisplay.textContent = formatTime(timeRemaining); 
                if (restOverlay) { restOverlay.classList.remove('hidden'); restOverlay.classList.add('flex'); }
                runTimerLoop();
            }
        } catch (e) { console.error("Ошибка синхронизации времени:", e); }
    };
    if (skipRest) {
        skipRest.addEventListener('click', () => {
            isRestPhase = false; timeRemaining = 52 * 60; if (sessionTimer) sessionTimer.textContent = formatTime(timeRemaining);
            if (restOverlay) { restOverlay.classList.add('hidden'); restOverlay.classList.remove('flex'); }
        });
    }
}

let currentGranularityMode = 'atomic';
let currentVolumeLimit = 'auto';
let currentDetailDensity = 'medium';

window.setGranularityMode = function(mode) {
    currentGranularityMode = mode;
    ['atomic', 'detailed', 'blitz'].forEach(m => {
        const el = document.getElementById(`gran-${m}`);
        if (el) {
            if (m === mode) {
                el.className = 'border border-primary bg-primary text-on-primary py-2 px-1 text-[10px] font-bold transition-all flex flex-col items-center justify-center rounded-xl shadow-xs cursor-pointer';
            } else {
                el.className = 'border border-neutral-300 dark:border-neutral-700 text-neutral-600 dark:text-neutral-300 hover:border-primary hover:text-primary py-2 px-1 text-[10px] font-bold transition-all flex flex-col items-center justify-center rounded-xl shadow-xs cursor-pointer';
            }
        }
    });

    if (mode === 'detailed') {
        currentDetailDensity = 'high';
        currentVolumeLimit = 'max';
    } else if (mode === 'blitz' || mode === 'cheatsheet') {
        currentDetailDensity = 'low';
        currentVolumeLimit = 'low_5';
    } else {
        currentDetailDensity = 'medium';
        currentVolumeLimit = 'auto';
    }

    updateImportExplanation();
};

window.toggleFocusChip = function(btn, chipText) {
    const input = document.getElementById('import-custom-instruction');
    if (!input) return;
    
    const isActive = btn.classList.contains('active-chip');
    if (isActive) {
        btn.classList.remove('active-chip', 'bg-primary/15', 'border-primary', 'text-primary', 'font-bold');
        btn.classList.add('border-neutral-200', 'dark:border-neutral-700', 'text-neutral-600', 'dark:text-neutral-300');
        let current = input.value;
        current = current.replace(chipText, '').replace(/;\s*;/g, ';').replace(/^;\s*|;\s*$/g, '').trim();
        input.value = current;
    } else {
        btn.classList.add('active-chip', 'bg-primary/15', 'border-primary', 'text-primary', 'font-bold');
        btn.classList.remove('border-neutral-200', 'dark:border-neutral-700', 'text-neutral-600', 'dark:text-neutral-300');
        let current = input.value.trim();
        if (current) {
            input.value = current + '; ' + chipText;
        } else {
            input.value = chipText;
        }
    }
};

function updateImportExplanation() {
    const explEl = document.getElementById('import-mode-explanation');
    if (!explEl) return;

    if (currentGranularityMode === 'detailed') {
        explEl.textContent = 'Полный разбор: максимальная глубина без сокращений. Извлечение 100% ветвей схем, условий, статей, формул и исключений.';
    } else if (currentGranularityMode === 'blitz' || currentGranularityMode === 'cheatsheet') {
        explEl.textContent = 'Экспресс-блиц: только фундаментальный понятийный каркас верхнего уровня для быстрого входа в тему.';
    } else {
        explEl.textContent = 'High-Yield FSRS (Парето): умная авто-адаптация к формату источника (слайды, конспект, учебник). 1 карточка = 1 ключевой факт (отклик 1.5–3.5 сек).';
    }
}

function isOffPeakWindow() {
    // Пик DeepSeek: пн–пт 01:00–04:00 и 06:00–10:00 UTC, всё остальное время — скидка 50%
    const now = new Date();
    const day = now.getUTCDay();
    if (day === 0 || day === 6) return true;
    const h = now.getUTCHours();
    return !((h >= 1 && h < 4) || (h >= 6 && h < 10));
}

let cachedAiProviderInfo = null;
let lastAiProviderFetchTime = 0;

window.updateTariffBanner = async function() {
    const bannerTitle = document.getElementById('tariff-status-title');
    const bannerBadge = document.getElementById('tariff-status-badge');
    const btnDeferred = document.getElementById('btn-import-deferred');
    if (!bannerBadge) return;

    // Запрашиваем актуального ИИ-провайдера и модель с сервера (с кэшем 30 сек)
    const nowTs = Date.now();
    if (!cachedAiProviderInfo || (nowTs - lastAiProviderFetchTime > 30000)) {
        try {
            const res = await apiFetch('/api/config/ai-provider');
            if (res.ok) {
                cachedAiProviderInfo = await res.json();
                lastAiProviderFetchTime = nowTs;
            }
        } catch (e) {
            // Игнорируем сетевые сбои фонового опроса
        }
    }

    const prov = (cachedAiProviderInfo?.provider || 'deepseek').toUpperCase();
    const model = (cachedAiProviderInfo?.model || 'deepseek-flash').toUpperCase();
    const isOffPeak = isOffPeakWindow();

    const titleText = `ИИ: <span class="text-primary font-bold">${prov} (${model})</span>`;

    if (isOffPeak) {
        if (bannerTitle) bannerTitle.innerHTML = titleText;
        bannerBadge.className = "text-emerald-600 dark:text-emerald-400 font-bold font-mono animate-pulse";
        bannerBadge.textContent = "[СКИДКА -50% АКТИВНА]";
        if (btnDeferred) {
            btnDeferred.innerHTML = `<span class="material-symbols-outlined text-[15px]">dark_mode</span><span>СКИДКА -50% (СЕЙЧАС)</span>`;
        }
    } else {
        // Пик заканчивается в 04:00 или 10:00 UTC
        const now = new Date();
        const utcMinutes = now.getUTCHours() * 60 + now.getUTCMinutes();
        const peakEnd = utcMinutes < 240 ? 240 : 600;
        const diffMinutes = Math.max(0, peakEnd - utcMinutes);
        const h = Math.floor(diffMinutes / 60);
        const m = diffMinutes % 60;

        if (bannerTitle) bannerTitle.innerHTML = titleText;
        bannerBadge.className = "text-secondary font-bold font-mono";
        bannerBadge.textContent = `[СКИДКА 50% ЧЕРЕЗ ${h}ч ${m}м]`;
        if (btnDeferred) {
            btnDeferred.innerHTML = `<span class="material-symbols-outlined text-[15px]">dark_mode</span><span>СО СКИДКОЙ (-50%)</span>`;
        }
    }
};

window.currentStagingJobId = null;

window.discardStagingDeck = async function(targetJobId) {
    const idToDelete = targetJobId || window.currentStagingJobId;
    if (!confirm("Удалить эту колоду карточек? Это действие необратимо.")) {
        return;
    }

    try {
        if (idToDelete) {
            const res = await apiFetch(`/api/config/import/staging/job/${idToDelete}`, {
                method: 'DELETE'
            });
            const data = await res.json();
            if (!res.ok) {
                alert(data.detail || data.message || "Не удалось удалить колоду.");
                return;
            }
        }

        // Если удалялась текущая открытая колода или колода из песочницы
        if (!targetJobId || String(targetJobId) === String(window.currentStagingJobId)) {
            window.currentStagingJobId = null;
            stagingCards = [];
            approvedStagingCards = [];
            rejectedStagingCards = [];
            stagingHistory = [];
            currentStagingIndex = 0;
            if (window.location.hash && window.location.hash.includes('staging_job')) {
                try {
                    history.replaceState(null, '', window.location.pathname + window.location.search);
                } catch (e) {}
            }
            closeStagingOverlay();
        }

        if (typeof checkNightQueueStatus === 'function') {
            checkNightQueueStatus();
        }
    } catch (e) {
        console.error("Ошибка при удалении колоды из песочницы:", e);
        alert("Сбой сети при удалении колоды.");
    }
};

window.openStagingJob = async function(jobId) {
    try {
        const res = await apiFetch(`/api/config/import/staging/job/${jobId}`);
        const data = await res.json();
        if (res.ok && data.status === 'staging') {
            window.currentStagingJobId = jobId;
            startStagingSession(data);
        } else {
            alert(data.detail || data.message || "Не удалось загрузить карточки задачи.");
        }
    } catch (e) {
        console.error("Ошибка открытия песочницы для задачи:", e);
        alert("Сбой сети при загрузке карточек.");
    }
};

window.checkDeepLinkOrHash = async function() {
    let jobId = null;
    const hash = window.location.hash || '';

    // Ссылка из push «Путь знаний готов»: #path_<предмет> → сразу открываем граф предмета
    const pathMatch = hash.match(/^#path_(.+)$/i);
    if (pathMatch && window.openKnowledgeGraphModal) {
        window.openKnowledgeGraphModal(decodeURIComponent(pathMatch[1]));
        return;
    }

    const matchHash = hash.match(/#staging_job_?(\d+)/i);
    if (matchHash) {
        jobId = matchHash[1];
    } else if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initDataUnsafe) {
        const startParam = window.Telegram.WebApp.initDataUnsafe.start_param || '';
        const matchParam = startParam.match(/staging_job_?(\d+)/i);
        if (matchParam) {
            jobId = matchParam[1];
        }
    }

    if (jobId) {
        await window.openStagingJob(jobId);
    }
};

window.checkNightQueueStatus = async function() {
    try {
        const res = await apiFetch('/api/config/import/queue');
        if (!res.ok) return;
        const data = await res.json();
        const indicator = document.getElementById('night-queue-indicator');
        const countSpan = document.getElementById('night-queue-count');
        const listEl = document.getElementById('night-queue-list');
        if (!indicator || !countSpan) return;

        const jobs = data.jobs || [];
        const activeJobs = jobs.filter(j => ['pending', 'processing', 'ready_for_review'].includes(j.status));
        if (activeJobs.length > 0) {
            countSpan.textContent = activeJobs.length;
            indicator.classList.remove('hidden');
            if (listEl) {
                listEl.innerHTML = activeJobs.map(j => {
                    if (j.status === 'ready_for_review') {
                        return `
                            <div class="flex items-center justify-between py-1.5 px-2 bg-emerald-50 dark:bg-emerald-950/30 border border-emerald-300 dark:border-emerald-800 rounded-lg text-[10px] font-mono my-1">
                                <div class="truncate max-w-[55%]">
                                    <span class="font-bold text-emerald-700 dark:text-emerald-300 inline-flex items-center gap-1"><span class="material-symbols-outlined text-[12px]">check_circle</span>${escapeHTML(j.theme || 'Материал')}</span>
                                    <span class="text-[9px] text-neutral-600 dark:text-neutral-400 block font-sans">${j.cards_count} карт. готовы к разбору</span>
                                </div>
                                <div class="flex items-center gap-1">
                                    <button onclick="discardStagingDeck(${j.id})" class="px-2 py-1 border border-secondary text-secondary hover:bg-secondary hover:text-on-secondary rounded text-[9px] font-bold uppercase transition-all flex items-center gap-1 font-mono" title="Удалить колоду">
                                        <span class="material-symbols-outlined text-[12px]">delete</span>
                                        <span>[УДАЛИТЬ]</span>
                                    </button>
                                    <button onclick="openStagingJob(${j.id})" class="px-2.5 py-1 bg-emerald-600 hover:bg-emerald-700 text-white rounded text-[9px] font-bold uppercase transition-all shadow-sm flex items-center gap-1 font-mono">
                                        <span class="material-symbols-outlined text-[13px]">style</span>
                                        <span>[РАЗОБРАТЬ]</span>
                                    </button>
                                </div>
                            </div>
                        `;
                    }
                    return `
                        <div class="flex items-center justify-between py-1 text-[10px] font-mono">
                            <div class="truncate max-w-[65%]">
                                <span class="font-bold text-on-surface">${escapeHTML(j.theme || 'Материал')}</span>
                                <span class="text-[9px] text-secondary">(${j.status === 'processing' ? 'обрабатывается...' : 'в очереди'})</span>
                            </div>
                            <button onclick="cancelQueuedJob(${j.id})" class="px-2 py-0.5 border border-secondary text-secondary hover:bg-secondary hover:text-on-secondary rounded-md text-[9px] font-bold uppercase transition-all flex items-center gap-1">
                                <span class="material-symbols-outlined text-[12px]">close</span>
                                <span>Отменить</span>
                            </button>
                        </div>
                    `;
                }).join('');
            }
        } else {
            indicator.classList.add('hidden');
            if (listEl) listEl.innerHTML = '';
        }
    } catch (e) {}
};


window.cancelQueuedJob = async function(jobId) {
    if (!confirm("Отменить эту задачу создания карточек?")) return;
    try {
        const res = await apiFetch(`/api/config/import/queue/${jobId}`, { method: 'DELETE' });
        const data = await res.json();
        if (res.ok) {
            alert(data.message || "Задача успешно отменена.");
            checkNightQueueStatus();
        } else {
            alert(data.detail || "Не удалось отменить задачу.");
        }
    } catch (e) {
        console.error("Ошибка отмены задачи из очереди:", e);
        alert("Сбой сети при отмене задачи.");
    }
};

let activeImportAbortController = null;

window.cancelActiveGeneration = function() {
    if (activeImportAbortController) {
        activeImportAbortController.abort();
        activeImportAbortController = null;
    }
    const activeBar = document.getElementById('generation-active-bar');
    if (activeBar) activeBar.classList.add('hidden');
    const btnInstant = document.getElementById('btn-import-instant');
    const btnDeferred = document.getElementById('btn-import-deferred');
    if (btnInstant) {
        btnInstant.disabled = false;
        btnInstant.innerHTML = `<span class="material-symbols-outlined text-[15px]">bolt</span><span>СЕЙЧАС</span>`;
    }
    if (btnDeferred) {
        btnDeferred.disabled = false;
        btnDeferred.innerHTML = `<span class="material-symbols-outlined text-[15px]">dark_mode</span><span>${isOffPeakWindow() ? 'СКИДКА -50% (СЕЙЧАС)' : 'НОЧЬЮ (-50%)'}</span>`;
    }
    const statusEl = document.getElementById('file-import-status');
    if (statusEl) statusEl.classList.add('hidden');
    console.log("[Data Grinder] Активная генерация отменена пользователем.");
};

// Повторная нарезка заменяет изучаемый предмет: сервер отвечает 409 replace_confirm, спрашиваем согласие и повторяем
async function fetchWithReplaceConfirm(send) {
    let response = await send(false);
    if (response.status === 409) {
        let data = null;
        try { data = await response.clone().json(); } catch (_) {}
        const d = data && data.detail;
        if (d && d.code === 'replace_confirm') {
            if (!confirm(d.message)) return null;
            response = await send(true);
        }
    }
    return response;
}

// Понятный текст ошибки из ответа сервера (detail может быть строкой, объектом или списком)
function apiErrorText(data, fallback) {
    let t = data && (data.detail || data.message);
    if (!t) return fallback || 'Неизвестная ошибка';
    if (Array.isArray(t)) return t.map(e => (typeof e === 'object' ? (e.msg || JSON.stringify(e)) : e)).join(', ');
    if (typeof t === 'object') return t.message || JSON.stringify(t);
    return t;
}

async function importTextKnowledge(isDeferred = false) {
    if (window.isExperimentPhase1) {
        alert("Нарезка материалов заблокирована на период Фазы 1 эксперимента. Доступно только тестирование готовых карточек (10 шт/день).");
        return;
    }
    const textarea = document.getElementById('import-text'); 
    const btnInstant = document.getElementById('btn-import-instant'); 
    const btnDeferred = document.getElementById('btn-import-deferred'); 
    const text = textarea ? textarea.value.trim() : "";
    if (!text) { alert("Поле ввода пусто. Вставьте текст лекции или конспекта!"); return; }
    
    const targetSubject = getSelectedImportSubject();
    if (!targetSubject) {
        alert("Выберите предмет из списка или укажите новый перед созданием карточек!");
        return;
    }

    const pref = localStorage.getItem('assoc_preference') || 'acoustic';
    const customInstruction = document.getElementById('import-custom-instruction')?.value.trim() || '';

    if (btnInstant) btnInstant.disabled = true;
    if (btnDeferred) btnDeferred.disabled = true;

    if (isDeferred) {
        if (btnDeferred) btnDeferred.innerText = "[ОЧЕРЕДЬ...]";
    } else {
        if (btnInstant) btnInstant.innerText = "[ДЕПСИК...]";
        activeImportAbortController = new AbortController();
        const activeBar = document.getElementById('generation-active-bar');
        const activeStatus = document.getElementById('generation-active-status');
        if (activeBar) activeBar.classList.remove('hidden');
        if (activeStatus) activeStatus.textContent = "ИИ создает карточки...";
    }

    let keepActiveBar = false;
    try {
        const response = await fetchWithReplaceConfirm((confirmReplace) => apiFetch('/api/config/import', {
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' }, 
            signal: activeImportAbortController ? activeImportAbortController.signal : undefined,
            body: JSON.stringify({ 
                text: text, 
                subject: targetSubject,
                density: currentDetailDensity, 
                volume: currentVolumeLimit, 
                priority: 'balanced',
                assoc_preference: pref,
                granularity_mode: currentGranularityMode,
                custom_instruction: customInstruction,
                commit_now: false, // Направляем на проверку
                is_deferred: isDeferred,
                confirm_replace: confirmReplace
            })
        }));
        if (!response) return; // пользователь отказался заменять предмет
        const data = await response.json();
        if (response.ok && data.status === 'queued') {
            if (textarea) textarea.value = "";
            keepActiveBar = window.handleQueuedJob ? window.handleQueuedJob(data, null) : false;
        } else if (response.ok && data.status === 'staging') {
            startStagingSession(data);
        } else if (response.ok && data.status === 'success') {
            alert(`Создано карточек: ${data.cards_count}`);
            if (textarea) textarea.value = ""; 
            await loadDynamicSubjects(); 
            updateGlobalBadges();
        } else { 
            alert("Ошибка создания карточек: " + apiErrorText(data, "Неизвестный сбой.")); 
        }
    } catch (e) { 
        if (e.name === 'AbortError' || e.message === 'The user aborted a request.') {
            console.log("[Data Grinder] Запрос создания карточек отменен пользователем.");
            return;
        }
        console.error("Сбой сети при создании карточек:", e); 
        alert("Сбой сети при обращении к серверу."); 
    } finally { 
        activeImportAbortController = null;
        if (!keepActiveBar) {
            const activeBar = document.getElementById('generation-active-bar');
            if (activeBar) activeBar.classList.add('hidden');
        }
        if (btnInstant) { 
            btnInstant.disabled = false; 
            btnInstant.innerHTML = `<span class="material-symbols-outlined text-[15px]">bolt</span><span>СЕЙЧАС</span>`;
        }
        if (btnDeferred) { 
            btnDeferred.disabled = false; 
            btnDeferred.innerHTML = `<span class="material-symbols-outlined text-[15px]">dark_mode</span><span>${isOffPeakWindow() ? 'СКИДКА -50% (СЕЙЧАС)' : 'НОЧЬЮ (-50%)'}</span>`;
        }
    }
}

window.importPreset = async function(presetName) {
    const btn = document.getElementById('btn-import');
    if (btn) { btn.disabled = true; btn.innerText = "[ЗАГРУЗКА БИБЛИОТЕКИ...]"; }
    try {
        const response = await apiFetch('/api/config/import/preset', {
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' }, 
            body: JSON.stringify({ preset_name: presetName, commit_now: false })
        });
        const data = await response.json();
        if (response.ok && data.status === 'staging') {
            startStagingSession(data);
        } else if (response.ok && data.status === 'success') {
            alert(`Библиотека успешно импортирована!\n\nСоздан топик: ${data.theme}\nИмпортировано карт: ${data.cards_count}\nДобавлено в предмет: [${data.subject.toUpperCase()}]`);
            await loadDynamicSubjects(); 
            updateGlobalBadges();
        } else { 
            alert("Ошибка импорта: " + (data.message || "Неизвестный сбой.")); 
        }
    } catch (e) { 
        console.error("Сбой сети при импорте готовой колоды:", e); 
        alert("Критический сбой сети при импорте."); 
    } finally { 
        if (btn) { btn.disabled = false; btn.innerText = "[ЗАПУСТИТЬ ПАРСЕР ЗНАНИЙ]"; } 
    }
};

window.handleQueuedJob = function(data, statusEl) {
    if (statusEl) statusEl.classList.add('hidden');
    checkNightQueueStatus();

    const activeBar = document.getElementById('generation-active-bar');
    const activeStatus = document.getElementById('generation-active-status');

    if (!data.is_immediate) {
        // Задача отложена до ближайшего окна скидки DeepSeek
        alert(`[ОЧЕРЕДЬ СКИДОК 50%]\n\n${data.message}`);
        if (activeBar) activeBar.classList.add('hidden');
        return false;
    }

    // Немедленная фоновая обработка (скидка 50% активна прямо сейчас или дневной экспресс)
    if (activeBar) activeBar.classList.remove('hidden');
    if (activeStatus) {
        activeStatus.textContent = data.is_offpeak 
            ? `ИИ нарезает карточки со скидкой 50%...` 
            : `ИИ нарезает карточки в фоновом режиме...`;
    }

    const jobId = data.job_id;
    if (!jobId) return true;

    let pollAttempts = 0;
    const maxPollAttempts = 300; // до 15 минут для объемных книг
    const pollInterval = setInterval(async () => {
        pollAttempts++;
        if (pollAttempts > maxPollAttempts) {
            clearInterval(pollInterval);
            if (activeBar) activeBar.classList.add('hidden');
            return;
        }

        try {
            const res = await apiFetch('/api/config/import/queue');
            if (!res.ok) return;
            const qData = await res.json();
            const jobs = qData.jobs || [];
            const thisJob = jobs.find(j => j.id === jobId);

            if (!thisJob) {
                clearInterval(pollInterval);
                if (activeBar) activeBar.classList.add('hidden');
                return;
            }

            if (thisJob.status === 'processing') {
                if (activeStatus) {
                    activeStatus.textContent = data.is_offpeak 
                        ? `ИИ нарезает карточки (-50%): обработка блоков...`
                        : `ИИ нарезает карточки: обработка блоков...`;
                }
            } else if (thisJob.status === 'ready_for_review') {
                clearInterval(pollInterval);
                if (activeBar) activeBar.classList.add('hidden');
                checkNightQueueStatus();
                // Автоматически открываем Песочницу с готовыми карточками!
                await window.openStagingJob(jobId);
            } else if (thisJob.status === 'failed') {
                clearInterval(pollInterval);
                if (activeBar) activeBar.classList.add('hidden');
                checkNightQueueStatus();
                alert(`Ошибка при нарезке материала: ${thisJob.error_message || 'Неизвестная ошибка'}`);
            } else if (thisJob.status === 'cancelled') {
                clearInterval(pollInterval);
                if (activeBar) activeBar.classList.add('hidden');
                checkNightQueueStatus();
            }
        } catch (e) {
            console.warn("[Job Polling Error]", e);
        }
    }, 3000);

    return true;
};

window.handleFileUpload = async function(event) {
    if (window.isExperimentPhase1) {
        alert("Нарезка материалов заблокирована на период Фазы 1 эксперимента.");
        event.target.value = '';
        return;
    }
    const files = event.target.files;
    if (!files || files.length === 0) return;

    const targetSubject = getSelectedImportSubject();
    if (!targetSubject) {
        alert("Выберите целевой предмет из списка или укажите новый перед загрузкой файла!");
        event.target.value = '';
        return;
    }

    const statusEl = document.getElementById('file-import-status');
    if (statusEl) {
        const fileNames = Array.from(files).map(f => f.name).join(', ');
        statusEl.textContent = files.length === 1 
            ? `[ИЗВЛЕЧЕНИЕ: ${files[0].name.toUpperCase()}...]` 
            : `[ОБРАБОТКА ПАЧКИ: ${files.length} ФАЙЛОВ (${fileNames.substring(0, 30)}...)...]`;
        statusEl.classList.remove('hidden');
    }

    const pref = localStorage.getItem('assoc_preference') || 'acoustic';
    const fileSizeMb = (files[0].size / (1024 * 1024)).toFixed(1);
    const fileName = files.length === 1 ? files[0].name : `${files.length} файлов`;

    const formData = new FormData();
    for (let i = 0; i < files.length; i++) {
        formData.append('files', files[i]);
    }
    formData.append('subject', targetSubject);
    formData.append('density', currentDetailDensity);
    formData.append('volume', currentVolumeLimit);
    formData.append('priority', 'balanced');
    formData.append('assoc_preference', pref);
    formData.append('granularity_mode', currentGranularityMode);
    formData.append('custom_instruction', document.getElementById('import-custom-instruction')?.value.trim() || '');
    formData.append('commit_now', 'false');

    // Если сейчас УЖЕ действует скидка DeepSeek, сразу генерируем со скидкой 50%
    // Предлагаем отложить в очередь ТОЛЬКО в дневные часы, чтобы пользователь мог сэкономить 50%.
    const isOffPeak = isOffPeakWindow();
    let isDeferred = false;
    if (!isOffPeak && files.length > 0) {
        isDeferred = confirm(
            `Документ: ${fileName} (${fileSizeMb} МБ)\n\n` +
            `Сейчас действует пиковый тариф DeepSeek.\n` +
            `Поставить в очередь со скидкой 50% (обработка начнётся после окончания пика)?\n\n` +
            `[OK] — В очередь со скидкой 50%\n` +
            `[Отмена] — Создать карточки прямо сейчас`
        );
    }
    formData.append('is_deferred', isDeferred ? 'true' : 'false');

    if (!isDeferred) {
        activeImportAbortController = new AbortController();
        const activeBar = document.getElementById('generation-active-bar');
        const activeStatus = document.getElementById('generation-active-status');
        if (activeBar) activeBar.classList.remove('hidden');
        if (activeStatus) activeStatus.textContent = "ИИ обрабатывает файлы...";
    }

    let keepActiveBar = false;
    try {
        const response = await fetchWithReplaceConfirm((confirmReplace) => {
            formData.set('confirm_replace', confirmReplace ? 'true' : 'false');
            return apiFetch('/api/config/import/file', {
                method: 'POST',
                signal: activeImportAbortController ? activeImportAbortController.signal : undefined,
                body: formData
            });
        });
        if (!response) { if (statusEl) statusEl.classList.add('hidden'); return; }

        if (response.status === 413) {
            let d413 = null;
            try { d413 = await response.clone().json(); } catch (_) {}
            alert(d413 && d413.detail ? apiErrorText(d413) : `Файл слишком большой для веб-сервера (${fileSizeMb} МБ). Рекомендуем разбить документ по главам.`);
            if (statusEl) statusEl.classList.add('hidden');
            return;
        }

        const data = await response.json();
        if (response.ok && data.status === 'queued') {
            keepActiveBar = window.handleQueuedJob ? window.handleQueuedJob(data, statusEl) : false;
        } else if (response.ok && data.status === 'staging') {
            if (statusEl) statusEl.classList.add('hidden');
            startStagingSession(data);
        } else {
            let errorText = data.detail || data.message || "Неизвестная ошибка";
            if (typeof errorText === 'object') {
                if (Array.isArray(errorText)) {
                    errorText = errorText.map(e => (typeof e === 'object' ? (e.msg || JSON.stringify(e)) : e)).join(', ');
                } else {
                    errorText = JSON.stringify(errorText);
                }
            }
            alert("Ошибка обработки файла: " + errorText);
            if (statusEl) statusEl.classList.add('hidden');
        }
    } catch (err) {
        if (err.name === 'AbortError' || err.message === 'The user aborted a request.') {
            console.log("[Data Grinder] Загрузка и обработка файлов отменена пользователем.");
            return;
        }
        console.error("Сбой загрузки файла:", err);
        alert(`Ошибка сети при отправке файла (${fileSizeMb} МБ). Если размер превышает 1 МБ, сервер Nginx может блокировать запрос лимитом client_max_body_size.`);
        if (statusEl) statusEl.classList.add('hidden');
    } finally {
        activeImportAbortController = null;
        if (!keepActiveBar) {
            const activeBar = document.getElementById('generation-active-bar');
            if (activeBar) activeBar.classList.add('hidden');
        }
        event.target.value = '';
    }
};

// Предварительная оптимизация изображения перед передачей в OCR (масштабирование без OOM в мобильном браузере)
async function preprocessImageForOcr(file) {
    return new Promise((resolve) => {
        try {
            const img = new Image();
            const url = URL.createObjectURL(file);
            img.onload = () => {
                URL.revokeObjectURL(url);
                const maxDim = 2000; // Оптимальное разрешение для четкого OCR текста без утечек памяти
                let width = img.width;
                let height = img.height;
                if (width > maxDim || height > maxDim) {
                    if (width > height) {
                        height = Math.round((height * maxDim) / width);
                        width = maxDim;
                    } else {
                        width = Math.round((width * maxDim) / height);
                        height = maxDim;
                    }
                }
                const canvas = document.createElement('canvas');
                canvas.width = width;
                canvas.height = height;
                const ctx = canvas.getContext('2d');
                ctx.imageSmoothingEnabled = true;
                ctx.imageSmoothingQuality = 'high';
                ctx.drawImage(img, 0, 0, width, height);

                // Экспортируем в чистый PNG без потерь качества и без сжатия JPEG
                // Встроенный в Tesseract движок Leptonica сам выполнит идеальную адаптивную бинаризацию (Otsu)
                canvas.toBlob((blob) => {
                    resolve(blob || file);
                }, 'image/png');
            };
            img.onerror = () => {
                URL.revokeObjectURL(url);
                resolve(file);
            };
            img.src = url;
        } catch (err) {
            resolve(file);
        }
    });
}

window.handleImageOcr = async function(event) {
    if (window.isExperimentPhase1) {
        alert("Нарезка материалов заблокирована на период Фазы 1 эксперимента.");
        event.target.value = '';
        return;
    }
    const files = event.target.files ? Array.from(event.target.files) : [];
    if (files.length === 0) return;

    const targetSubject = getSelectedImportSubject();
    if (!targetSubject) {
        alert("Выберите целевой предмет из списка или укажите новый перед распознаванием фото!");
        event.target.value = '';
        return;
    }

    const statusEl = document.getElementById('file-import-status');
    if (statusEl) {
        statusEl.textContent = files.length === 1 
            ? "[OCR: ОПТИМИЗАЦИЯ ФОТО...]" 
            : `[OCR: ОПТИМИЗАЦИЯ ПАЧКИ ИЗ ${files.length} ФОТО...]`;
        statusEl.className = "text-[10px] font-mono text-center text-primary font-bold py-1.5 bg-neutral-100 dark:bg-neutral-800 rounded-xl px-2 border border-neutral-300 dark:border-neutral-700 block";
        statusEl.classList.remove('hidden');
    }

    if (typeof Tesseract === 'undefined') {
        if (statusEl) {
            statusEl.textContent = "[ЗАГРУЗКА OCR-ДВИЖКА TESSERACT...]";
            statusEl.classList.remove('hidden');
        }
        try {
            await new Promise((resolve, reject) => {
                const s = document.createElement('script');
                s.src = "https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js";
                s.onload = resolve;
                s.onerror = () => reject(new Error("Не удалось загрузить библиотеку Tesseract OCR"));
                document.head.appendChild(s);
            });
        } catch (loadErr) {
            alert("Ошибка загрузки движка OCR: " + loadErr.message);
            if (statusEl) statusEl.classList.add('hidden');
            event.target.value = '';
            return;
        }
    }

    const recognizedPages = [];
    const totalFiles = files.length;
    let failedCount = 0;

    try {
        for (let idx = 0; idx < totalFiles; idx++) {
            const rawFile = files[idx];
            const pageNum = idx + 1;
            
            if (statusEl) {
                statusEl.textContent = `[OCR ${pageNum}/${totalFiles}: ПОДГОТОВКА ФОТО (${rawFile.name})...]`;
            }

            // Предварительное масштабирование фото на canvas (избегаем OOM крашей в мобильном браузере)
            const processedBlob = await preprocessImageForOcr(rawFile);

            if (statusEl) {
                statusEl.textContent = `[OCR ${pageNum}/${totalFiles}: РАСПОЗНАВАНИЕ ТЕКСТА...]`;
            }

            try {
                const result = await Tesseract.recognize(
                    processedBlob,
                    'rus+eng',
                    {
                        logger: m => {
                            if (m.status === 'recognizing text' && statusEl) {
                                const pct = Math.round((m.progress || 0) * 100);
                                statusEl.textContent = `[OCR ${pageNum}/${totalFiles}: ${pct}%]`;
                            }
                        }
                    }
                );

                let pageText = (result && result.data && result.data.text) ? result.data.text : "";
                // Санитизация OCR-текста
                pageText = pageText
                    .replace(/\r\n/g, '\n')
                    .replace(/[ \t]+/g, ' ')
                    .replace(/\n\s*\n\s*\n+/g, '\n\n')
                    .trim();

                // Проверяем, что в тексте действительно есть осмысленные буквы/цифры, а не только шум палочек и тире
                const meaningfulChars = pageText.replace(/[^a-zA-Zа-яА-Я0-9ёЁ]/g, '');

                if (meaningfulChars.length >= 15) {
                    const header = `=== МАТЕРИАЛ: ФОТО ${pageNum} (${rawFile.name}) ===\n`;
                    recognizedPages.push(header + pageText);
                } else {
                    console.warn(`[OCR WARN] Фото ${pageNum} (${rawFile.name}) не содержит разборчивого текста (${meaningfulChars.length} знаков).`);
                    failedCount++;
                }
            } catch (singleErr) {
                console.error(`[OCR ERROR] Сбой при распознавании фото ${pageNum}:`, singleErr);
                failedCount++;
            }
        }

        const combinedText = recognizedPages.join('\n\n');

        if (!combinedText.trim()) {
            alert("Не удалось распознать читаемый текст на выбранных фото. Убедитесь, что конспект в фокусе, сфотографирован прямо и при хорошем свете.");
            if (statusEl) statusEl.classList.add('hidden');
            return;
        }

        // Помещаем распознанный текст в текстовое поле импорта (добавляем или перезаписываем)
        const textarea = document.getElementById('import-text');
        if (textarea) {
            if (textarea.value.trim()) {
                textarea.value = textarea.value.trim() + '\n\n' + combinedText;
            } else {
                textarea.value = combinedText;
            }
            // Плавная прокрутка к полю с текстом
            textarea.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }

        if (statusEl) {
            statusEl.textContent = `[РАСПОЗНАНО ${recognizedPages.length} ИЗ ${totalFiles} ФОТО (${combinedText.length} ЗНАКОВ)]`;
            statusEl.className = "text-[10px] font-mono text-center text-primary font-bold py-1.5 bg-neutral-100 dark:bg-neutral-800 rounded-xl px-2 border border-neutral-300 dark:border-neutral-700 block flex items-center justify-center gap-1";
        }

        let alertMsg = `Успешно распознано: ${recognizedPages.length} из ${totalFiles} фото (${combinedText.length} символов).`;
        if (failedCount > 0) {
            alertMsg += `\n(На ${failedCount} фото текст был слишком нечетким и пропущен).`;
        }
        alertMsg += `\n\nТекст помещен в поле ввода.\nПроверьте его и нажмите «[СЕЙЧАС]» или «[НОЧЬЮ (-50%)]» для создания карточек.`;
        alert(alertMsg);
    } catch (ocrErr) {
        console.error("Ошибка пакетного OCR:", ocrErr);
        alert("Ошибка распознавания фото: " + ocrErr.message);
        if (statusEl) statusEl.classList.add('hidden');
    } finally {
        event.target.value = '';
    }
};

// ============================================================================
// ПЕСОЧНИЦА КАРТОЧЕК (STAGING SANDBOX): СВАЙПЫ, МОДЕРАЦИЯ, ПРЕДПРОСМОТР
// ============================================================================
let stagingCards = [];
let currentStagingIndex = 0;
let approvedStagingCards = [];
let rejectedStagingCards = [];
let stagingHistory = []; // Стек действий для бесконечного Undo
let stagingSubject = 'generic';
let stagingTheme = 'Новый блок знаний';

function startStagingSession(data) {
    window.currentStagingJobId = data.job_id || null;
    stagingCards = (data.cards || []).map((c, idx) => {
        const text = c.text || c.front || c.question || '';
        const secondary = c.secondary_text || c.secondary || c.hint || '';
        const translation = c.translation || c.back || c.answer || c.definition || '';
        return {
            ...c,
            text: text,
            front: text,
            question: text,
            secondary_text: secondary,
            secondary: secondary,
            hint: secondary,
            translation: translation,
            back: translation,
            answer: translation,
            definition: translation,
            _orig_idx: idx
        };
    });
    currentStagingIndex = 0;
    approvedStagingCards = [];
    rejectedStagingCards = [];
    stagingHistory = [];
    stagingSubject = (data.subject || 'generic').toLowerCase();
    stagingTheme = data.theme || 'Новый блок знаний';

    if (stagingCards.length === 0) {
        alert("Не найдено карточек для отображения в песочнице.");
        return;
    }

    const overlay = document.getElementById('staging-overlay');
    const titleEl = document.getElementById('staging-topic-title');
    if (titleEl) titleEl.textContent = `[${stagingSubject.toUpperCase()}] ${stagingTheme}`;

    // Синхронизация выпадающего списка предметов в шапке песочницы
    const stagingSel = document.getElementById('staging-subject-select');
    if (stagingSel) {
        let hasOpt = false;
        for (let i = 0; i < stagingSel.options.length; i++) {
            if (stagingSel.options[i].value === stagingSubject) {
                hasOpt = true;
                break;
            }
        }
        if (!hasOpt && stagingSubject) {
            const opt = document.createElement('option');
            opt.value = stagingSubject;
            opt.textContent = `[${stagingSubject.toUpperCase()}]`;
            stagingSel.appendChild(opt);
        }
        stagingSel.value = stagingSubject;
    }

    if (overlay) {
        overlay.classList.remove('hidden');
        overlay.classList.add('flex');
        document.getElementById('bottom-nav')?.classList.add('hidden');
        document.body.classList.add('overflow-hidden');
    }

    renderCurrentStagingCard();
    initStagingGestures();
}

function renderCurrentStagingCard() {
    const cardEl = document.getElementById('staging-card');
    const commitBtn = document.getElementById('staging-commit-btn');
    const approvedCnt = document.getElementById('staging-approved-count');
    const rejectedCnt = document.getElementById('staging-rejected-count');
    const rejectedSubCnt = document.getElementById('staging-rejected-sub-cnt');
    const remainingCnt = document.getElementById('staging-remaining-count');
    const undoBtn = document.getElementById('staging-undo-btn');

    const total = stagingCards.length;
    const remaining = Math.max(0, total - currentStagingIndex);

    if (approvedCnt) approvedCnt.textContent = approvedStagingCards.length;
    if (rejectedCnt) rejectedCnt.textContent = rejectedStagingCards.length;
    if (rejectedSubCnt) rejectedSubCnt.textContent = rejectedStagingCards.length;
    if (remainingCnt) remainingCnt.textContent = remaining;
    if (commitBtn) commitBtn.textContent = `СОХРАНИТЬ (${approvedStagingCards.length})`;

    if (undoBtn) {
        if (stagingHistory.length > 0 && currentStagingIndex > 0) {
            undoBtn.classList.remove('opacity-40', 'cursor-not-allowed');
            undoBtn.classList.add('opacity-100', 'cursor-pointer');
            undoBtn.title = `Отменить последнее действие (${stagingHistory.length}) [Ctrl+Z / Backspace]`;
        } else {
            undoBtn.classList.add('opacity-40', 'cursor-not-allowed');
            undoBtn.classList.remove('opacity-100', 'cursor-pointer');
            undoBtn.title = "Нет действий для отмены";
        }
    }

    if (currentStagingIndex >= total) {
        if (cardEl) {
            cardEl.innerHTML = `
                <div class="flex-1 flex flex-col items-center justify-center text-center p-md gap-md">
                    <span class="material-symbols-outlined text-4xl text-primary">task_alt</span>
                    <h3 class="text-base font-bold uppercase text-primary">Песочница завершена!</h3>
                    <p class="text-xs text-outline leading-relaxed">
                        Одобрено карточек: <strong class="text-primary">${approvedStagingCards.length}</strong><br>
                        Отклонено: <strong class="text-secondary">${rejectedStagingCards.length}</strong>
                    </p>
                    <button onclick="commitApprovedStagingCards()" class="w-full border border-primary bg-primary text-on-primary py-sm font-bold uppercase text-xs hover:bg-transparent hover:text-primary transition-all mt-sm rounded-xl shadow-xs flex items-center justify-center gap-1.5">
                        <span class="material-symbols-outlined text-[15px]">save</span>
                        <span>СОХРАНИТЬ В БАЗУ ДАННЫХ (${approvedStagingCards.length})</span>
                    </button>
                    <div class="flex gap-2 w-full mt-2">
                        <button onclick="stagingUndo()" class="flex-1 border border-outline-variant text-outline hover:text-primary py-2 font-bold uppercase text-[10px] rounded-xl transition-all flex items-center justify-center gap-1">
                            <span class="material-symbols-outlined text-[13px]">undo</span>
                            <span>ВЕРНУТЬ КАРТУ</span>
                        </button>
                        <button onclick="stagingResetSession()" class="flex-1 border border-outline-variant text-outline hover:text-secondary py-2 font-bold uppercase text-[10px] rounded-xl transition-all flex items-center justify-center gap-1">
                            <span class="material-symbols-outlined text-[13px]">restart_alt</span>
                            <span>СБРОСИТЬ</span>
                        </button>
                        <button onclick="discardStagingDeck()" class="flex-1 border border-secondary text-secondary hover:bg-error-container/20 py-2 font-bold uppercase text-[10px] rounded-xl transition-all flex items-center justify-center gap-1" title="Удалить эту колоду">
                            <span class="material-symbols-outlined text-[13px]">delete</span>
                            <span>УДАЛИТЬ</span>
                        </button>
                    </div>
                </div>
            `;
        }
        return;
    }

    if (!document.getElementById('staging-card-text')) {
        if (cardEl) {
            cardEl.innerHTML = STAGING_CARD_TEMPLATE;
            initStagingGestures();
        }
    }

    const card = stagingCards[currentStagingIndex];
    if (!card || !cardEl) return;

    cardEl.style.transform = 'translate(0px, 0px) rotate(0deg)';
    cardEl.style.opacity = '1';
    
    const badgeAccept = document.getElementById('staging-badge-accept');
    const badgeReject = document.getElementById('staging-badge-reject');
    if (badgeAccept) badgeAccept.style.opacity = '0';
    if (badgeReject) badgeReject.style.opacity = '0';

    const numEl = document.getElementById('staging-card-number');
    const tierEl = document.getElementById('staging-card-tier');
    const textEl = document.getElementById('staging-card-text');
    const secEl = document.getElementById('staging-card-secondary');
    const transEl = document.getElementById('staging-card-translation');
    const exEl = document.getElementById('staging-card-example');
    const exBox = document.getElementById('staging-card-example-box');
    const mnemBox = document.getElementById('staging-card-mnemonic-box');
    const mnemEl = document.getElementById('staging-card-mnemonic');

    if (numEl) numEl.textContent = `Карточка ${currentStagingIndex + 1} из ${total}`;
    const themeBadge = document.getElementById('staging-card-theme-badge');
    if (themeBadge) {
        const cTheme = (card.theme || '').trim();
        if (cTheme && cTheme !== stagingTheme) {
            themeBadge.textContent = cTheme.toUpperCase();
            themeBadge.title = `Тематический кластер: ${cTheme}`;
            themeBadge.classList.remove('hidden');
        } else {
            themeBadge.classList.add('hidden');
        }
    }
    if (tierEl) tierEl.textContent = card.initial_difficulty_tier || 'medium';
    const stagingChBadge = document.getElementById('staging-card-chapter-badge');
    const stagingChText = document.getElementById('staging-card-chapter-text');
    const stagingChName = (card.chapter || card.phrase_text || card.theme || '').trim();
    if (stagingChBadge && stagingChText) {
        if (stagingChName) {
            stagingChText.textContent = stagingChName;
            stagingChBadge.classList.remove('hidden');
            stagingChBadge.classList.add('inline-flex');
        } else {
            stagingChBadge.classList.add('hidden');
            stagingChBadge.classList.remove('inline-flex');
        }
    }
    const cardText = card.text || card.front || card.question || '---';
    if (textEl) {
        if (card.content_type === 'cloze' || /\{\{c\d+::/.test(cardText)) {
            textEl.innerHTML = formatClozeHTML(cardText, false);
        } else {
            textEl.textContent = cardText;
        }
        applyDynamicCardTypography(textEl, cardText);
    }
    const secText = (card.secondary_text || card.secondary || card.hint || '').trim();
    if (secEl) {
        secEl.textContent = secText;
        if (secText) {
            secEl.classList.remove('hidden');
        } else {
            secEl.classList.add('hidden');
        }
    }
    const answerText = (card.translation || card.back || card.answer || card.definition || '').trim();
    if (transEl) {
        transEl.textContent = answerText || '---';
    }
    if (exEl) {
        if (card.example && card.example.trim() && card.example !== '---') {
            exEl.textContent = `«${card.example.trim()}»`;
            if (exBox) exBox.classList.remove('hidden');
        } else {
            exEl.textContent = '';
            if (exBox) exBox.classList.add('hidden');
        }
    }

    if (mnemBox && mnemEl) {
        let mText = '';
        if (card.mnemonic) {
            if (typeof card.mnemonic === 'object' && card.mnemonic.keyword) {
                mText = `${card.mnemonic.keyword}: ${card.mnemonic.verbal_cue || ''}`;
            } else if (typeof card.mnemonic === 'string') {
                mText = card.mnemonic;
            }
        }
        if (mText) {
            mnemEl.textContent = mText;
            mnemBox.classList.remove('hidden');
        } else {
            mnemBox.classList.add('hidden');
        }
    }
}

window.stagingSwipeRight = function() {
    if (currentStagingIndex >= stagingCards.length) return;
    const currentCard = stagingCards[currentStagingIndex];
    const cardEl = document.getElementById('staging-card');
    const badgeAccept = document.getElementById('staging-badge-accept');
    if (badgeAccept) badgeAccept.style.opacity = '1';

    if (cardEl) {
        cardEl.style.transition = 'transform 0.3s ease, opacity 0.3s ease';
        cardEl.style.transform = 'translate(120%, 20px) rotate(20deg)';
        cardEl.style.opacity = '0';
    }

    approvedStagingCards.push(currentCard);
    stagingHistory.push({ action: 'approve', card: currentCard, index: currentStagingIndex });
    setTimeout(() => {
        currentStagingIndex++;
        if (cardEl) cardEl.style.transition = 'none';
        renderCurrentStagingCard();
    }, 250);
};

window.stagingSwipeLeft = function() {
    if (currentStagingIndex >= stagingCards.length) return;
    const currentCard = stagingCards[currentStagingIndex];
    const cardEl = document.getElementById('staging-card');
    const badgeReject = document.getElementById('staging-badge-reject');
    if (badgeReject) badgeReject.style.opacity = '1';

    if (cardEl) {
        cardEl.style.transition = 'transform 0.3s ease, opacity 0.3s ease';
        cardEl.style.transform = 'translate(-120%, 20px) rotate(-20deg)';
        cardEl.style.opacity = '0';
    }

    rejectedStagingCards.push(currentCard);
    stagingHistory.push({ action: 'reject', card: currentCard, index: currentStagingIndex });
    setTimeout(() => {
        currentStagingIndex++;
        if (cardEl) cardEl.style.transition = 'none';
        renderCurrentStagingCard();
    }, 250);
};

window.stagingUndo = function() {
    if (stagingHistory.length === 0 || currentStagingIndex <= 0) return;
    const lastAction = stagingHistory.pop();
    if (!lastAction) return;

    if (lastAction.action === 'approve') {
        const idx = approvedStagingCards.lastIndexOf(lastAction.card);
        if (idx !== -1) approvedStagingCards.splice(idx, 1);
    } else if (lastAction.action === 'reject') {
        const idx = rejectedStagingCards.lastIndexOf(lastAction.card);
        if (idx !== -1) rejectedStagingCards.splice(idx, 1);
    }

    currentStagingIndex = Math.max(0, currentStagingIndex - 1);
    const cardEl = document.getElementById('staging-card');
    if (cardEl) {
        const startTranslate = lastAction.action === 'approve' ? '120%' : '-120%';
        const startRotate = lastAction.action === 'approve' ? '20deg' : '-20deg';
        cardEl.style.transition = 'none';
        cardEl.style.transform = `translate(${startTranslate}, 20px) rotate(${startRotate})`;
        cardEl.style.opacity = '0';
        
        renderCurrentStagingCard();
        
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                cardEl.style.transition = 'transform 0.28s cubic-bezier(0.175, 0.885, 0.32, 1.275), opacity 0.25s ease';
                cardEl.style.transform = 'translate(0px, 0px) rotate(0deg)';
                cardEl.style.opacity = '1';
            });
        });
    } else {
        renderCurrentStagingCard();
    }
};

window.stagingResetSession = function() {
    if (currentStagingIndex === 0 && approvedStagingCards.length === 0 && rejectedStagingCards.length === 0) {
        alert("Разбор еще не начат.");
        return;
    }
    if (!confirm("Сбросить текущий разбор и начать сначала? Все решения (одобренные и отклоненные) будут сброшены.")) {
        return;
    }
    currentStagingIndex = 0;
    approvedStagingCards = [];
    rejectedStagingCards = [];
    stagingHistory = [];
    renderCurrentStagingCard();
};

window.openRejectedDrawer = function() {
    const modal = document.getElementById('staging-rejected-modal');
    const listEl = document.getElementById('staging-rejected-list');
    if (!modal || !listEl) return;

    if (rejectedStagingCards.length === 0) {
        listEl.innerHTML = `
            <div class="py-8 text-center text-outline flex flex-col items-center gap-2">
                <span class="material-symbols-outlined text-2xl opacity-40">delete_sweep</span>
                <span>Список отклоненных карточек пуст</span>
            </div>
        `;
    } else {
        listEl.innerHTML = rejectedStagingCards.map((card, i) => {
            const rawTitle = card.text || '---';
            const displayTitle = rawTitle.length > 80 ? rawTitle.substring(0, 80) + '...' : rawTitle;
            const rawSub = card.translation || card.secondary_text || '';
            const displaySub = rawSub.length > 60 ? rawSub.substring(0, 60) + '...' : rawSub;
            const safeTitle = displayTitle.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
            const safeSub = displaySub.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
            return `
                <div class="py-2.5 px-1 flex items-center justify-between gap-2 hover:bg-surface-container-low transition-colors rounded-lg">
                    <div class="flex-1 min-w-0 pr-2">
                        <div class="text-[11px] font-bold text-on-surface truncate">${safeTitle}</div>
                        <div class="text-[10px] text-outline truncate">${safeSub}</div>
                    </div>
                    <button onclick="restoreRejectedCard(${i})" class="shrink-0 border border-primary text-primary hover:bg-primary hover:text-on-primary py-1 px-2.5 rounded-lg text-[10px] font-bold uppercase transition-all shadow-xs" title="Восстановить и одобрить эту карточку">
                        + ВЕРНУТЬ
                    </button>
                </div>
            `;
        }).join('');
    }

    modal.classList.remove('hidden');
    modal.classList.add('flex');
};

window.closeRejectedDrawer = function() {
    const modal = document.getElementById('staging-rejected-modal');
    if (modal) {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
    }
};

window.restoreRejectedCard = function(rejectedIdx) {
    if (rejectedIdx < 0 || rejectedIdx >= rejectedStagingCards.length) return;
    const [restored] = rejectedStagingCards.splice(rejectedIdx, 1);
    if (restored) {
        approvedStagingCards.push(restored);
        openRejectedDrawer();
        renderCurrentStagingCard();
    }
};

window.stagingAcceptAll = function() {
    while (currentStagingIndex < stagingCards.length) {
        const card = stagingCards[currentStagingIndex];
        approvedStagingCards.push(card);
        stagingHistory.push({ action: 'approve', card: card, index: currentStagingIndex });
        currentStagingIndex++;
    }
    renderCurrentStagingCard();
};

window.closeStagingOverlay = function() {
    const overlay = document.getElementById('staging-overlay');
    if (overlay) {
        overlay.classList.add('hidden');
        overlay.classList.remove('flex');
    }
    document.getElementById('bottom-nav')?.classList.remove('hidden');
    document.body.classList.remove('overflow-hidden');
    window.currentStagingJobId = null;
    if (window.location.hash && window.location.hash.includes('staging_job')) {
        try {
            history.replaceState(null, '', window.location.pathname + window.location.search);
        } catch (e) {}
    }
};

window.commitApprovedStagingCards = async function() {
    if (approvedStagingCards.length === 0) {
        alert("Нет одобренных карточек для сохранения.");
        return;
    }

    const btn = document.getElementById('staging-commit-btn');
    if (btn) {
        btn.disabled = true;
        btn.innerText = "[СОХРАНЕНИЕ В БД...]";
    }

    try {
        const payload = {
            subject: stagingSubject,
            theme: stagingTheme,
            cards: approvedStagingCards.map(c => ({
                text: c.text || c.front || c.question || '',
                secondary_text: c.secondary_text || c.secondary || c.hint || '',
                translation: c.translation || c.back || c.answer || c.definition || '',
                example: c.example || '',
                initial_difficulty_tier: c.initial_difficulty_tier || 'medium',
                mnemonic: c.mnemonic || null,
                theme: c.theme || ''
            }))
        };
        if (window.currentStagingJobId) {
            payload.job_id = window.currentStagingJobId;
        }

        const response = await apiFetch('/api/config/import/commit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        const data = await response.json();
        if (response.ok && data.status === 'success') {
            alert(`Успешно сохранено ${data.cards_count} карточек в предмет [${data.subject.toUpperCase()}]!`);
            window.currentStagingJobId = null;
            if (window.location.hash.includes('staging_job')) {
                try { history.replaceState(null, '', window.location.pathname + window.location.search); } catch (e) {}
            }
            closeStagingOverlay();
            const textarea = document.getElementById('import-text');
            if (textarea) textarea.value = '';
            await loadDynamicSubjects();
            updateGlobalBadges();
            if (typeof checkNightQueueStatus === 'function') checkNightQueueStatus();
            if (currentTab === 'data') loadDataTab();
            if (typeof loadKnowledgeGraph === 'function') {
                loadKnowledgeGraph(data.subject || stagingSubject);
            }
        } else {
            let errMsg = data.detail || data.message || "Неизвестная ошибка";
            if (typeof errMsg === 'object') {
                try {
                    errMsg = Array.isArray(errMsg) 
                        ? errMsg.map(e => `${e.loc ? e.loc.join('.') : ''}: ${e.msg}`).join('\n')
                        : JSON.stringify(errMsg);
                } catch (_) {}
            }
            alert("Ошибка сохранения: " + errMsg);
        }
    } catch (e) {
        console.error("Сбой фиксации песочницы:", e);
        alert("Сбой сети при сохранении карточек.");
    } finally {
        if (btn) btn.disabled = false;
    }
};

let stagingDrag = { 
    isDragging: false, 
    startX: 0, 
    startY: 0, 
    currentX: 0, 
    currentY: 0,
    isScrolling: false,
    isSwiping: false,
    gestureDetermined: false
};

function initStagingGestures() {
    const card = document.getElementById('staging-card');
    if (!card) return;

    const resetDrag = () => {
        stagingDrag.isDragging = false;
        stagingDrag.isScrolling = false;
        stagingDrag.isSwiping = false;
        stagingDrag.gestureDetermined = false;
    };

    const onStart = (clientX, clientY) => {
        stagingDrag.isDragging = true;
        stagingDrag.startX = clientX;
        stagingDrag.startY = clientY;
        stagingDrag.currentX = clientX;
        stagingDrag.currentY = clientY;
        stagingDrag.isScrolling = false;
        stagingDrag.isSwiping = false;
        stagingDrag.gestureDetermined = false;
    };

    const onMove = (clientX, clientY, e) => {
        if (!stagingDrag.isDragging || stagingDrag.isScrolling) return;
        stagingDrag.currentX = clientX;
        stagingDrag.currentY = clientY;
        const deltaX = clientX - stagingDrag.startX;
        const deltaY = clientY - stagingDrag.startY;
        const absX = Math.abs(deltaX);
        const absY = Math.abs(deltaY);

        if (!stagingDrag.gestureDetermined) {
            if (absX < 8 && absY < 8) return; // порог чувствительности
            if (absY >= absX) {
                // Преимущественно вертикальный жест: отдаем нативный скролл контейнеру .card-scroll-clean
                stagingDrag.isScrolling = true;
                stagingDrag.gestureDetermined = true;
                return;
            } else {
                // Горизонтальный свайп карточки
                stagingDrag.isSwiping = true;
                stagingDrag.gestureDetermined = true;
                card.style.transition = 'none';
            }
        }

        if (stagingDrag.isSwiping) {
            if (e && e.cancelable) e.preventDefault();
            const rotate = deltaX * 0.07;
            card.style.transform = `translate(${deltaX}px, ${deltaY * 0.3}px) rotate(${rotate}deg)`;

            const badgeAccept = document.getElementById('staging-badge-accept');
            const badgeReject = document.getElementById('staging-badge-reject');

            if (deltaX > 25) {
                if (badgeAccept) badgeAccept.style.opacity = Math.min(1, (deltaX - 25) / 80).toString();
                if (badgeReject) badgeReject.style.opacity = '0';
            } else if (deltaX < -25) {
                if (badgeReject) badgeReject.style.opacity = Math.min(1, (-deltaX - 25) / 80).toString();
                if (badgeAccept) badgeAccept.style.opacity = '0';
            } else {
                if (badgeAccept) badgeAccept.style.opacity = '0';
                if (badgeReject) badgeReject.style.opacity = '0';
            }
        }
    };

    const onEnd = () => {
        if (!stagingDrag.isDragging) return;
        const wasSwiping = stagingDrag.isSwiping;
        const deltaX = stagingDrag.currentX - stagingDrag.startX;
        resetDrag();

        if (wasSwiping) {
            if (deltaX > 80) {
                stagingSwipeRight();
            } else if (deltaX < -80) {
                stagingSwipeLeft();
            } else {
                card.style.transition = 'transform 0.2s ease';
                card.style.transform = 'translate(0px, 0px) rotate(0deg)';
                const badgeAccept = document.getElementById('staging-badge-accept');
                const badgeReject = document.getElementById('staging-badge-reject');
                if (badgeAccept) badgeAccept.style.opacity = '0';
                if (badgeReject) badgeReject.style.opacity = '0';
            }
        }
    };

    if (!card._gestures_bound) {
        card._gestures_bound = true;

        // На тач-экранах: не блокируем интерактивные элементы, разделяем скролл/свайп
        card.addEventListener('touchstart', (e) => {
            if (e.target.closest('button, a, select, input, textarea')) return;
            const t = e.touches[0];
            onStart(t.clientX, t.clientY);
        }, { passive: true });

        // На мыши: не блокируем скролл и выделение текста внутри карточки
        card.addEventListener('mousedown', (e) => {
            if (e.button !== 0) return;
            if (e.target.closest('button, a, select, input, textarea, .card-scroll-clean')) return;
            onStart(e.clientX, e.clientY);
        });
    }

    if (!window._staging_window_gestures_bound) {
        window._staging_window_gestures_bound = true;

        window.addEventListener('touchmove', (e) => {
            if (!stagingDrag.isDragging) return;
            const t = e.touches[0];
            onMove(t.clientX, t.clientY, e);
        }, { passive: false });

        window.addEventListener('touchend', () => {
            if (stagingDrag.isDragging) onEnd();
        });

        window.addEventListener('touchcancel', () => {
            if (stagingDrag.isDragging) onEnd();
        });

        window.addEventListener('mousemove', (e) => {
            if (!stagingDrag.isDragging) return;
            onMove(e.clientX, e.clientY, e);
        });

        window.addEventListener('mouseup', () => {
            if (stagingDrag.isDragging) onEnd();
        });
    }
}

// Горячие клавиши для Песочницы (Tinder-like Sandbox)
window.addEventListener('keydown', (e) => {
    const overlay = document.getElementById('staging-overlay');
    if (!overlay || overlay.classList.contains('hidden')) return;

    // Не перехватываем, если пользователь вводит текст в input/textarea
    const activeTag = document.activeElement ? document.activeElement.tagName.toLowerCase() : '';
    if (activeTag === 'input' || activeTag === 'textarea' || document.activeElement.isContentEditable) return;

    // Закрытие модального окна отклоненных по Escape
    if (e.key === 'Escape') {
        const rejectedModal = document.getElementById('staging-rejected-modal');
        if (rejectedModal && !rejectedModal.classList.contains('hidden')) {
            closeRejectedDrawer();
            e.preventDefault();
            return;
        }
    }

    // Ctrl+Z или Cmd+Z или Backspace -> Отмена (Undo)
    if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z' || e.key === 'я' || e.key === 'Я')) {
        e.preventDefault();
        stagingUndo();
        return;
    }
    if (e.key === 'Backspace') {
        e.preventDefault();
        stagingUndo();
        return;
    }

    // Стрелка вправо -> Принять карточку
    if (e.key === 'ArrowRight') {
        e.preventDefault();
        stagingSwipeRight();
        return;
    }

    // Стрелка влево -> Отклонить карточку
    if (e.key === 'ArrowLeft') {
        e.preventDefault();
        stagingSwipeLeft();
        return;
    }
});

// ============================================================================
// МОДАЛЬНОЕ ОКНО РЕДАКТИРОВАНИЯ И РУЧНОГО СОЗДАНИЯ КАРТОЧЕК
// ============================================================================
window.openManualCardModal = function() {
    if (window.isExperimentPhase1) {
        alert("Создание карточек вручную заблокировано на период Фазы 1 эксперимента.");
        return;
    }
    const modal = document.getElementById('card-editor-modal');
    const title = document.getElementById('card-editor-title');
    if (title) title.textContent = "СОЗДАНИЕ НОВОЙ КАРТОЧКИ";

    document.getElementById('edit-card-id').value = "";
    document.getElementById('edit-is-staging').value = "false";
    document.getElementById('edit-card-subject').value = currentSubject === 'all' ? 'generic' : currentSubject;
    document.getElementById('edit-card-text').value = "";
    document.getElementById('edit-card-secondary').value = "";
    document.getElementById('edit-card-translation').value = "";
    document.getElementById('edit-card-example').value = "";
    document.getElementById('edit-card-mnem-keyword').value = "";
    document.getElementById('edit-card-mnem-cue').value = "";

    if (modal) modal.classList.remove('hidden');
};

window.requestEditCard = function(cardId) {
    if (window.isExperimentPhase1) {
        alert("Редактирование карточек заблокировано на период Фазы 1 эксперимента (режим тестирования).");
        return;
    }
    const card = localCardsArchive.find(c => c.id === cardId);
    if (!card) return;

    const modal = document.getElementById('card-editor-modal');
    const title = document.getElementById('card-editor-title');
    if (title) title.textContent = `РЕДАКТИРОВАНИЕ КАРТОЧКИ #${cardId}`;

    document.getElementById('edit-card-id').value = cardId;
    document.getElementById('edit-is-staging').value = "false";
    document.getElementById('edit-card-subject').value = card.subject || currentSubject;
    document.getElementById('edit-card-text').value = card.text || "";
    document.getElementById('edit-card-secondary').value = card.secondary_text || "";
    document.getElementById('edit-card-translation').value = card.translation || "";
    document.getElementById('edit-card-example').value = card.example || "";

    let kw = "", cue = "";
    if (card.mnemonic && typeof card.mnemonic === 'object') {
        kw = card.mnemonic.keyword || "";
        cue = card.mnemonic.verbal_cue || "";
    }
    document.getElementById('edit-card-mnem-keyword').value = kw;
    document.getElementById('edit-card-mnem-cue').value = cue;

    if (modal) modal.classList.remove('hidden');
};

window.openStagingEditor = function() {
    if (currentStagingIndex >= stagingCards.length) return;
    const card = stagingCards[currentStagingIndex];
    const modal = document.getElementById('card-editor-modal');
    const title = document.getElementById('card-editor-title');
    if (title) title.textContent = "РЕДАКТИРОВАНИЕ КАРТОЧКИ В ПЕСОЧНИЦЕ";

    document.getElementById('edit-card-id').value = "";
    document.getElementById('edit-is-staging').value = "true";
    document.getElementById('edit-card-subject').value = stagingSubject;
    document.getElementById('edit-card-text').value = card.text || card.front || card.question || "";
    document.getElementById('edit-card-secondary').value = card.secondary_text || card.secondary || card.hint || "";
    document.getElementById('edit-card-translation').value = card.translation || card.back || card.answer || card.definition || "";
    document.getElementById('edit-card-example').value = card.example || "";

    let kw = "", cue = "";
    if (card.mnemonic && typeof card.mnemonic === 'object') {
        kw = card.mnemonic.keyword || "";
        cue = card.mnemonic.verbal_cue || "";
    }
    document.getElementById('edit-card-mnem-keyword').value = kw;
    document.getElementById('edit-card-mnem-cue').value = cue;

    if (modal) modal.classList.remove('hidden');
};

window.closeCardEditorModal = function() {
    const modal = document.getElementById('card-editor-modal');
    if (modal) modal.classList.add('hidden');
};

window.saveCardEditorData = async function() {
    const cardId = document.getElementById('edit-card-id')?.value;
    const isStaging = document.getElementById('edit-is-staging')?.value === "true";
    const subject = document.getElementById('edit-card-subject')?.value.trim() || 'generic';
    const text = document.getElementById('edit-card-text')?.value.trim();
    const secondary = document.getElementById('edit-card-secondary')?.value.trim();
    const translation = document.getElementById('edit-card-translation')?.value.trim();
    const example = document.getElementById('edit-card-example')?.value.trim();
    const mnemKw = document.getElementById('edit-card-mnem-keyword')?.value.trim();
    const mnemCue = document.getElementById('edit-card-mnem-cue')?.value.trim();

    if (!text || !translation) {
        alert("Лицевая сторона и перевод обязательны к заполнению!");
        return;
    }

    if (isStaging) {
        stagingCards[currentStagingIndex].text = text;
        stagingCards[currentStagingIndex].front = text;
        stagingCards[currentStagingIndex].secondary_text = secondary;
        stagingCards[currentStagingIndex].secondary = secondary;
        stagingCards[currentStagingIndex].hint = secondary;
        stagingCards[currentStagingIndex].translation = translation;
        stagingCards[currentStagingIndex].back = translation;
        stagingCards[currentStagingIndex].answer = translation;
        stagingCards[currentStagingIndex].example = example;
        stagingCards[currentStagingIndex].mnemonic = (mnemKw || mnemCue) ? { keyword: mnemKw, verbal_cue: mnemCue } : null;
        stagingSubject = subject.toLowerCase();

        const stagingSel = document.getElementById('staging-subject-select');
        if (stagingSel) {
            let hasOpt = false;
            for (let i = 0; i < stagingSel.options.length; i++) {
                if (stagingSel.options[i].value === stagingSubject) {
                    hasOpt = true;
                    break;
                }
            }
            if (!hasOpt && stagingSubject) {
                const opt = document.createElement('option');
                opt.value = stagingSubject;
                opt.textContent = `[${stagingSubject.toUpperCase()}]`;
                stagingSel.appendChild(opt);
            }
            stagingSel.value = stagingSubject;
        }
        const titleEl = document.getElementById('staging-topic-title');
        if (titleEl) titleEl.textContent = `[${stagingSubject.toUpperCase()}] ${stagingTheme}`;

        renderCurrentStagingCard();
        closeCardEditorModal();
        return;
    }

    if (cardId) {
        try {
            const res = await apiFetch(`/api/management/cards/${cardId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    text: text,
                    secondary_text: secondary,
                    translation: translation,
                    example: example,
                    mnemonic_keyword: mnemKw,
                    mnemonic_cue: mnemCue
                })
            });
            if (res.ok) {
                const cleanSub = subject.toLowerCase();
                const origCard = (typeof localCardsArchive !== 'undefined' && localCardsArchive) ? localCardsArchive.find(c => c.id == cardId) : null;
                if (origCard && origCard.subject !== cleanSub && cleanSub) {
                    try {
                        await apiFetch(`/api/management/cards/${cardId}/move`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ target_subject: cleanSub })
                        });
                    } catch (mErr) {
                        console.error("Ошибка перемещения карточки:", mErr);
                    }
                }
                closeCardEditorModal();
                loadDataTab();
                await loadDynamicSubjects();
                updateGlobalBadges();
            } else {
                alert("Ошибка сохранения изменений карточки.");
            }
        } catch (e) {
            console.error("Сбой сохранения:", e);
            alert("Ошибка сети при сохранении карточки.");
        }
    } else {
        try {
            const res = await apiFetch('/api/management/cards', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    subject: subject,
                    phrase_title: "Пользовательские карточки",
                    text: text,
                    secondary_text: secondary,
                    translation: translation,
                    example: example,
                    mnemonic_keyword: mnemKw,
                    mnemonic_cue: mnemCue
                })
            });
            if (res.ok) {
                closeCardEditorModal();
                alert("Карточка успешно создана!");
                await loadDynamicSubjects();
                loadDataTab();
                updateGlobalBadges();
            } else {
                alert("Ошибка создания карточки.");
            }
        } catch (e) {
            console.error("Сбой создания карточки:", e);
            alert("Ошибка сети при создании карточки.");
        }
    }
};

window.regenerateStagingMnemonic = async function() {
    if (currentStagingIndex >= stagingCards.length) return;
    const card = stagingCards[currentStagingIndex];
    try {
        const pref = localStorage.getItem('assoc_preference') || 'acoustic';
        const res = await apiFetch('/api/management/cards/0/regenerate_mnemonic', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ preference: pref })
        });
    } catch (e) {}
};

// --- ДОПОЛНИТЕЛЬНЫЙ ФУНКЦИОНАЛ: МАССОВЫЕ ДЕЙСТВИЯ И КОГНИТИВНЫЙ ОПРОС ---

window.startPress = function(e, cardId) {
    if (isSelectionMode) return;
    if (e.target.closest('button') || e.target.closest('input')) return;
    pressTimer = setTimeout(() => {
        activateSelectionMode(cardId);
    }, 700);
};

window.cancelPress = function() {
    clearTimeout(pressTimer);
};

window.onRowClick = function(e, cardId) {
    if (!isSelectionMode) return;
    if (e.target.closest('button') || e.target.closest('input')) return;
    const cb = document.querySelector(`.card-checkbox[data-card-id="${cardId}"]`);
    if (cb) {
        cb.checked = !cb.checked;
        updateBulkActionBar();
    }
};

window.onCardCheckboxChange = function(e) {
    updateBulkActionBar();
};

function activateSelectionMode(initialCardId = null) {
    isSelectionMode = true;
    const container = document.getElementById('data-container');
    if (container) container.classList.add('selection-mode-active');
    
    const toggleBtn = document.getElementById('bulk-select-toggle');
    if (toggleBtn) {
        toggleBtn.textContent = 'Отмена';
        toggleBtn.className = "text-[10px] font-mono font-bold px-1.5 py-0.5 border border-secondary text-secondary hover:bg-error-container/10 transition-colors uppercase";
    }
    
    populateBulkSubjects();
    
    // Показываем чекбоксы
    document.querySelectorAll('.card-checkbox').forEach(cb => cb.style.display = 'block');
    
    if (initialCardId) {
        const cb = document.querySelector(`.card-checkbox[data-card-id="${initialCardId}"]`);
        if (cb) cb.checked = true;
    }
    updateBulkActionBar();
}

function deactivateSelectionMode() {
    isSelectionMode = false;
    const container = document.getElementById('data-container');
    if (container) container.classList.remove('selection-mode-active');
    
    const toggleBtn = document.getElementById('bulk-select-toggle');
    if (toggleBtn) {
        toggleBtn.textContent = 'Выбрать';
        toggleBtn.className = "text-[10px] font-mono font-bold px-1.5 py-0.5 border border-outline text-outline hover:text-primary hover:border-primary transition-colors uppercase";
    }
    
    // Скрываем чекбоксы и сбрасываем состояние
    document.querySelectorAll('.card-checkbox').forEach(cb => {
        cb.checked = false;
        cb.style.display = 'none';
    });
    updateBulkActionBar();
}
window.exitBulkMode = deactivateSelectionMode;

function updateBulkActionBar() {
    const checkedBoxes = document.querySelectorAll('.card-checkbox:checked');
    const count = checkedBoxes.length;
    const actionBar = document.getElementById('bulk-action-bar');
    const countSpan = document.getElementById('bulk-selected-count');
    
    if (count > 0) {
        if (countSpan) countSpan.textContent = `Выбрано карточек: ${count}`;
        if (actionBar) {
            actionBar.classList.remove('hidden');
            setTimeout(() => {
                actionBar.classList.remove('translate-y-full');
            }, 10);
        }
    } else {
        if (actionBar) {
            actionBar.classList.add('translate-y-full');
            setTimeout(() => {
                if (document.querySelectorAll('.card-checkbox:checked').length === 0) {
                    actionBar.classList.add('hidden');
                }
            }, 300);
        }
    }
}

function populateBulkSubjects() {
    const bulkSelector = document.getElementById('bulk-target-subject');
    const mainSelector = document.getElementById('subject-selector');
    if (!bulkSelector || !mainSelector) return;
    
    bulkSelector.innerHTML = '';
    Array.from(mainSelector.options).forEach(opt => {
        if (opt.value !== 'all' && opt.value !== currentSubject) {
            const newOpt = document.createElement('option');
            newOpt.value = opt.value;
            newOpt.textContent = opt.textContent;
            bulkSelector.appendChild(newOpt);
        }
    });
}

window.executeBulkMove = async function() {
    if (window.isExperimentPhase1) {
        alert("Перенос карточек заблокирован на время Фазы 1 эксперимента.");
        return;
    }
    const bulkSelector = document.getElementById('bulk-target-subject');
    if (!bulkSelector) return;
    const targetSubject = bulkSelector.value;
    if (!targetSubject) {
        alert("Выберите целевой предмет для переноса.");
        return;
    }
    
    const checkedBoxes = document.querySelectorAll('.card-checkbox:checked');
    const cardIds = Array.from(checkedBoxes).map(cb => parseInt(cb.getAttribute('data-card-id')));
    
    if (cardIds.length === 0) {
        alert("Не выбрано ни одной карточки.");
        return;
    }
    
    try {
        const response = await apiFetch('/api/data/cards/move', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                card_ids: cardIds,
                target_subject: targetSubject
            })
        });
        
        if (response.ok) {
            cardIds.forEach(id => {
                const row = document.getElementById(`archive-row-${id}`);
                if (row) {
                    row.style.transition = 'all 0.3s ease';
                    row.style.opacity = '0';
                    row.style.transform = 'translateX(-20px)';
                    setTimeout(() => {
                        row.remove();
                    }, 300);
                }
                localCardsArchive = localCardsArchive.filter(c => c.id !== id);
            });
            
            deactivateSelectionMode();
            updateGlobalBadges();
        } else {
            const errData = await response.json();
            alert("Ошибка при массовом переносе: " + (errData.detail || "Неизвестная ошибка"));
        }
    } catch (e) {
        console.error("Сбой массового переноса:", e);
        alert("Ошибка сети при массовом переносе.");
    }
};

window.executeBulkDelete = async function() {
    if (window.isExperimentPhase1) {
        alert("Удаление карточек заблокировано на время Фазы 1 эксперимента.");
        return;
    }
    const checkedBoxes = document.querySelectorAll('.card-checkbox:checked');
    const cardIds = Array.from(checkedBoxes).map(cb => parseInt(cb.getAttribute('data-card-id')));
    
    if (cardIds.length === 0) {
        alert("Не выбрано ни одной карточки.");
        return;
    }
    
    if (!confirm(`Вы действительно хотите безвозвратно удалить ${cardIds.length} выбранных карточек?`)) {
        return;
    }
    
    try {
        const response = await apiFetch('/api/data/cards/delete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ card_ids: cardIds })
        });
        
        if (response.ok) {
            cardIds.forEach(id => {
                const row = document.getElementById(`archive-row-${id}`);
                if (row) {
                    row.style.transition = 'all 0.3s ease';
                    row.style.opacity = '0';
                    row.style.transform = 'translateX(-20px)';
                    setTimeout(() => {
                        row.remove();
                    }, 300);
                }
                localCardsArchive = localCardsArchive.filter(c => c.id !== id);
            });
            
            deactivateSelectionMode();
            updateGlobalBadges();
            await loadDynamicSubjects();
        } else {
            const errData = await response.json();
            alert("Ошибка при массовом удалении: " + (errData.detail || "Неизвестная ошибка"));
        }
    } catch (e) {
        console.error("Сбой массового удаления:", e);
        alert("Ошибка сети при массовом удалении.");
    }
};

// ============================================================================
// УПРАВЛЕНИЕ ПРЕДМЕТАМИ (СПИСОК, ПЕРЕИМЕНОВАНИЕ, УДАЛЕНИЕ)
// ============================================================================

window.openSubjectsManagerModal = async function() {
    const modal = document.getElementById('subjects-manager-modal');
    if (modal) modal.classList.remove('hidden');
    await loadSubjectsManagerList();
};

window.closeSubjectsManagerModal = function() {
    const modal = document.getElementById('subjects-manager-modal');
    if (modal) modal.classList.add('hidden');
};

window.loadSubjectsManagerList = async function() {
    const container = document.getElementById('subjects-manager-list');
    if (!container) return;
    
    container.innerHTML = '<div class="text-center py-6 text-neutral-400 text-xs font-mono">Загрузка предметов...</div>';
    
    try {
        const res = await apiFetch('/api/data/subjects/details');
        if (!res.ok) {
            container.innerHTML = '<div class="text-center py-6 text-secondary text-xs font-mono">Не удалось загрузить предметы</div>';
            return;
        }
        const data = await res.json();
        const subjects = data.subjects || [];
        
        if (subjects.length === 0) {
            container.innerHTML = '<div class="text-center py-6 text-neutral-400 text-xs font-mono">Нет созданных предметов</div>';
            return;
        }
        
        container.innerHTML = '';
        subjects.forEach(sub => {
            const row = document.createElement('div');
            row.className = "flex items-center justify-between py-2.5 px-2 hover:bg-neutral-50 dark:hover:bg-neutral-800/40 rounded-xl transition-colors";
            
            const isCurrent = sub.slug === currentSubject;
            const badgeHtml = isCurrent ? '<span class="text-[9px] px-1.5 py-0.5 bg-primary text-on-primary font-bold rounded">ТЕКУЩИЙ</span>' : '';
            
            row.innerHTML = `
                <div class="flex flex-col min-w-0 pr-2">
                    <div class="font-bold text-xs text-neutral-800 dark:text-neutral-200 font-mono truncate uppercase flex items-center gap-1.5">
                        <span class="truncate">${escapeHTML(sub.name || sub.slug.toUpperCase())}</span>
                        ${badgeHtml}
                    </div>
                    <div class="text-[10px] text-neutral-400 font-mono mt-0.5">
                        Карточек: <span class="font-bold text-neutral-600 dark:text-neutral-300">${sub.cards_count}</span>
                    </div>
                </div>
                <div class="flex items-center gap-1.5 shrink-0">
                    <button onclick="shareSubjectDeck('${escapeHTML(sub.slug)}')" class="px-2 py-1 text-[10px] font-mono font-bold uppercase border border-primary text-primary hover:bg-primary hover:text-on-primary rounded-lg transition-all flex items-center gap-1" title="Поделиться колодой в Telegram">
                        <span class="material-symbols-outlined text-[12px]">share</span>
                        <span>Поделиться</span>
                    </button>
                    <button onclick="openSubjectRenameModal('${escapeHTML(sub.slug)}')" class="px-2 py-1 text-[10px] font-mono font-bold uppercase border border-neutral-300 dark:border-neutral-700 text-neutral-700 dark:text-neutral-300 hover:bg-neutral-200 dark:hover:bg-neutral-700 rounded-lg transition-all" title="Переименовать предмет">
                        Имя
                    </button>
                    <button onclick="deleteSubjectByName('${escapeHTML(sub.slug)}')" class="px-2 py-1 text-[10px] font-mono font-bold uppercase border border-secondary text-secondary hover:bg-secondary hover:text-on-secondary rounded-lg transition-all" title="Удалить предмет">
                        Удалить
                    </button>
                </div>
            `;
            container.appendChild(row);
        });
    } catch (e) {
        console.error("Сбой загрузки списка предметов:", e);
        container.innerHTML = '<div class="text-center py-6 text-secondary text-xs font-mono">Ошибка загрузки списка предметов</div>';
    }
};

window.shareSubjectDeck = async function(subjectSlug) {
    if (!subjectSlug || subjectSlug === 'all') {
        alert("Выберите конкретный предмет для шеринга.");
        return;
    }
    const btn = event?.target?.closest('button');
    const oldHtml = btn ? btn.innerHTML : '';
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="material-symbols-outlined text-[12px] animate-spin">sync</span> Ждем...';
    }
    try {
        const res = await apiFetch('/api/data/cards/share', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ subject: subjectSlug })
        });
        if (!res.ok) {
            const err = await res.json().catch(() => ({}));
            alert("Ошибка создания ссылки для шеринга: " + (err.detail || "Неизвестная ошибка"));
            return;
        }
        const data = await res.json();
        const shareUrl = data.share_url;
        const deckTitle = data.title || subjectSlug.toUpperCase();
        const totalCards = data.total_cards || 0;
        const shareMsg = `Колода Data Grinder: «${deckTitle}» (${totalCards} карточек FSRS). Открой ссылку для добавления в бота:`;
        const tgShareUrl = `https://t.me/share/url?url=${encodeURIComponent(shareUrl)}&text=${encodeURIComponent(shareMsg)}`;
        
        if (window.Telegram?.WebApp?.openTelegramLink) {
            window.Telegram.WebApp.openTelegramLink(tgShareUrl);
        } else if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(shareUrl);
            alert(`Ссылка на колоду скопирована в буфер обмена!\n\n${shareUrl}`);
        } else {
            prompt("Скопируйте ссылку на колоду:", shareUrl);
        }
    } catch (e) {
        console.error("Сбой шеринга колоды:", e);
        alert("Ошибка сети при подготовке ссылки для шеринга.");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = oldHtml;
        }
    }
};

window.shareCurrentSubject = function() {
    shareSubjectDeck(currentSubject);
};

window.openSubjectRenameModal = function(subjectSlug) {
    if (!subjectSlug || subjectSlug === 'all') {
        alert("Нельзя переименовать агрегированный вид [ВСЕ ПРЕДМЕТЫ]. Выберите конкретный предмет.");
        return;
    }
    const oldInput = document.getElementById('rename-old-subject');
    const oldLabel = document.getElementById('rename-old-label');
    const newInput = document.getElementById('rename-new-input');
    const modal = document.getElementById('subject-rename-modal');
    
    if (oldInput) oldInput.value = subjectSlug;
    if (oldLabel) oldLabel.textContent = subjectSlug.toUpperCase();
    if (newInput) {
        newInput.value = subjectSlug;
    }
    if (modal) {
        modal.classList.remove('hidden');
        if (newInput) {
            setTimeout(() => {
                newInput.focus();
                newInput.select();
            }, 50);
        }
    }
};

window.closeSubjectRenameModal = function() {
    const modal = document.getElementById('subject-rename-modal');
    if (modal) modal.classList.add('hidden');
    const newInput = document.getElementById('rename-new-input');
    if (newInput) newInput.value = '';
};

window.submitSubjectRename = async function() {
    const oldSub = (document.getElementById('rename-old-subject')?.value || '').trim().toLowerCase();
    const newSub = (document.getElementById('rename-new-input')?.value || '').trim().toLowerCase();
    
    if (!oldSub || !newSub) {
        alert("Название предмета не может быть пустым.");
        return;
    }
    if (newSub === 'all') {
        alert("Нельзя использовать имя 'all' (зарезервировано).");
        return;
    }
    try {
        const response = await apiFetch('/api/data/subjects/rename', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ old_subject: oldSub, new_subject: newSub })
        });
        
        if (response.ok) {
            const resData = await response.json();
            closeSubjectRenameModal();
            
            currentSubject = newSub;
            localStorage.setItem('selected_subject', currentSubject);
            localStorage.removeItem('grinder_cached_subjects');
            
            await loadDynamicSubjects();
            const mainSel = document.getElementById('subject-selector');
            if (mainSel && currentSubject) mainSel.value = currentSubject;
            
            const managerModal = document.getElementById('subjects-manager-modal');
            if (managerModal && !managerModal.classList.contains('hidden')) {
                await loadSubjectsManagerList();
            }
            
            if (currentTab === 'config') await loadConfigTab();
            if (currentTab === 'data') await loadArchiveData();
            if (currentTab === 'stats') await loadStatsTab();
            await fetchActiveSession();
            
            alert(`Предмет успешно переименован в [${newSub.toUpperCase()}]. Обновлено карточек: ${resData.cards_updated || 0}.`);
        } else {
            const errData = await response.json();
            alert("Ошибка при переименовании: " + (errData.detail || "Неизвестная ошибка"));
        }
    } catch (e) {
        console.error("Сбой переименования предмета:", e);
        alert("Ошибка сети при переименовании предмета.");
    }
};

window.renameCurrentSubject = function() {
    if (!currentSubject || currentSubject === 'all') {
        alert("Нельзя переименовать агрегированный вид [ВСЕ ПРЕДМЕТЫ]. Выберите конкретный предмет.");
        return;
    }
    openSubjectRenameModal(currentSubject);
};

window.deleteSubjectByName = async function(subjectSlug) {
    if (!subjectSlug || subjectSlug === 'all') {
        alert("Нельзя удалить агрегированный вид [ВСЕ ПРЕДМЕТЫ].");
        return;
    }
    
    const subName = subjectSlug.toUpperCase();
    if (!confirm(`ВНИМАНИЕ! Удалить предмет [${subName}] и ВСЕ связанные с ним карточки? Это действие необратимо!`)) {
        return;
    }
    
    try {
        const response = await apiFetch(`/api/data/subjects/${encodeURIComponent(subjectSlug)}`, {
            method: 'DELETE'
        });
        
        if (response.ok) {
            if (currentSubject === subjectSlug) {
                currentSubject = 'all';
                localStorage.setItem('selected_subject', 'all');
            }
            
            await loadDynamicSubjects();
            const mainSel = document.getElementById('subject-selector');
            if (mainSel) mainSel.value = currentSubject;
            
            const managerModal = document.getElementById('subjects-manager-modal');
            if (managerModal && !managerModal.classList.contains('hidden')) {
                await loadSubjectsManagerList();
            }
            
            if (currentTab === 'config') await loadConfigTab();
            if (currentTab === 'data') await loadArchiveData();
            if (currentTab === 'stats') await loadStatsTab();
            await fetchActiveSession();
            
            alert(`Предмет [${subName}] успешно удален.`);
        } else {
            const errData = await response.json();
            alert("Ошибка при удалении предмета: " + (errData.detail || "Неизвестная ошибка"));
        }
    } catch (e) {
        console.error("Сбой при удалении предмета:", e);
        alert("Ошибка сети при удалении предмета.");
    }
};

window.deleteCurrentSubject = async function() {
    if (!currentSubject || currentSubject === 'all') {
        alert("Нельзя удалить агрегированный вид [ВСЕ ПРЕДМЕТЫ]. Выберите конкретный предмет.");
        return;
    }
    await deleteSubjectByName(currentSubject);
};

window.submitDailySessionSurvey = async function() {
    const btn = document.getElementById('survey-submit-btn');
    if (btn) {
        btn.disabled = true;
        btn.innerText = "[ОТПРАВКА...]";
    }
    
    const mental = parseInt(document.getElementById('survey-mental')?.value || '3');
    const assoc = parseInt(document.getElementById('survey-assoc')?.value || '3');
    const retention = parseInt(document.getElementById('survey-retention')?.value || '3');
    
    const durationSeconds = Math.max(10, (52 * 60) - timeRemaining);
    
    try {
        const response = await apiFetch(`/api/stats/daily_session?tg_id=${tgId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                mental_effort: mental,
                association_utility: assoc,
                perceived_retention: retention,
                session_duration: durationSeconds
            })
        });
        
        if (response.ok) {
            triggerHaptic('success');
            if (window.showNotification) {
                window.showNotification("Отчет сессии сохранен для аналитики FSRS!", "success");
            } else {
                alert("Отчет сессии успешно сохранен для аналитики FSRS!");
            }
            window.surveyCompletedToday = true;
            
            const surveyContainer = document.getElementById('survey-container');
            if (surveyContainer) surveyContainer.classList.add('hidden');
            if (btn) {
                btn.disabled = false;
                btn.innerText = "[ОТПРАВИТЬ ОТЧЕТ СЕССИИ]";
            }
            
            if (typeof window.showSessionDebrief === 'function') {
                window.showSessionDebrief();
            } else {
                window.exitToSessionMenu();
            }
        } else {
            alert("Ошибка при отправке отчета сессии.");
            if (btn) {
                btn.disabled = false;
                btn.innerText = "[ОТПРАВИТЬ ОТЧЕТ СЕССИИ]";
            }
        }
    } catch (e) {
        console.error("Сбой отправки опроса:", e);
        alert("Сбой сети при отправке отчета.");
        if (btn) {
            btn.disabled = false;
            btn.innerText = "[ОТПРАВИТЬ ОТЧЕТ СЕССИИ]";
        }
    }
};

window.exportCardsJSON = async function() {
    try {
        const sub = (typeof currentSubject !== 'undefined' && currentSubject) ? currentSubject : 'all';
        const res = await apiFetch(`/api/data/cards/export?subject=${sub}`);
        if (!res.ok) {
            alert("Ошибка выгрузки карточек с сервера");
            return;
        }
        const blob = await res.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `grinder_deck_${sub}.json`;
        document.body.appendChild(a);
        a.click();
        window.URL.revokeObjectURL(url);
        a.remove();
    } catch (err) {
        console.error("Ошибка экспорта карточек:", err);
        alert("Не удалось скачать карточки: " + err.message);
    }
};

/* ==========================================================================
   MILESTONE 4: KNOWLEDGE GRAPH & TREE MINDMAP CONTROLLER
   ========================================================================== */

function getActiveDeckSubject() {
    if (currentSubject && currentSubject !== 'all') return currentSubject;
    const sel = document.getElementById('subject-selector');
    if (sel && sel.options) {
        for (let i = 0; i < sel.options.length; i++) {
            const val = sel.options[i].value;
            if (val && val !== 'all') return val;
        }
    }
    return currentKgSubject || '';
}

let currentKgSubject = '';
let currentKgGraphData = null;
let currentKgView = 'tree'; // 'tree' | 'graph'
let currentKgLayout = 'radial'; // 'radial' | 'tree' (сверху вниз) | 'horizontal' (слева направо)
let currentForceGraphInstance = null;

// Динамический асинхронный загрузчик библиотек физики графа (D3 & ForceGraph)
let graphVendorLoadingPromise = null;
function ensureGraphVendorLoaded() {
    if (window.ForceGraph && window.d3) {
        return Promise.resolve();
    }
    if (graphVendorLoadingPromise) {
        return graphVendorLoadingPromise;
    }
    graphVendorLoadingPromise = new Promise((resolve, reject) => {
        const loadScript = (src) => new Promise((res, rej) => {
            if (document.querySelector(`script[src="${src}"]`)) {
                res();
                return;
            }
            const s = document.createElement('script');
            s.src = src;
            s.async = false;
            s.onload = () => res();
            s.onerror = (e) => rej(new Error(`Failed to load ${src}`));
            document.head.appendChild(s);
        });

        loadScript('/vendor/d3.min.js')
            .then(() => loadScript('/vendor/force-graph.min.js'))
            .then(() => {
                console.log('[Graph] Библиотеки D3 и ForceGraph успешно загружены в фоновом режиме');
                resolve();
            })
            .catch(err => {
                console.error('[Graph Vendor Load Error]', err);
                graphVendorLoadingPromise = null;
                reject(err);
            });
    });
    return graphVendorLoadingPromise;
}

// Цвета категорий узлов (Neon Dark Cyber theme)
const KG_CATEGORY_COLORS = {
    'authority': '#f59e0b',     // Amber / Gold
    'instance': '#06b6d4',      // Cyan
    'condition': '#10b981',     // Emerald
    'exception': '#f43f5e',     // Rose
    'legal_status': '#a855f7',  // Violet / Purple
    // Статусы узлов «Пути знаний»
    'locked': '#94a3b8',        // Серый силуэт
    'open': '#3b82f6',          // Синий — можно начать урок
    'lesson_done': '#f59e0b',   // Янтарный — урок пройден, идут карточки
    'mastered': '#10b981',      // Изумрудный — освоено
    'default': '#3b82f6'        // Blue
};

const KG_CATEGORY_NAMES = {
    'authority': 'Орган / Компетенция',
    'instance': 'Инстанция / Звено',
    'condition': 'Условие / Основание',
    'exception': 'Исключение / Изъятие',
    'legal_status': 'Правовой статус',
    'locked': 'Закрыто',
    'open': 'Открыто',
    'lesson_done': 'Урок пройден',
    'mastered': 'Освоено'
};

const PATH_TIER_NAMES = ['Основы', 'Тема', 'Подтема', 'Кейс'];
let currentPathState = null;

// Узлы «Пути знаний» → формат графа: id = ключ узла, цвет = статус, размер = ярус.
// Линии дерева (родитель → ребёнок) строятся по тому же структурному дереву, что и раскладка
// (07a_path_layout.js), и добавляются первыми; смысловые связи ИИ — только между ещё не связанными узлами.
function pathStateToGraphData(state) {
    const nodes = (state.nodes || []).map(n => ({
        id: n.key,
        db_id: n.id,
        name: n.name,
        category: n.status,
        status: n.status,
        summary: n.summary || '',
        level: Math.min(n.tier, 2),
        tier: n.tier,
        parent_id: n.parent_key || undefined,
        prereq_keys: n.prereq_keys || [],
        lesson_status: n.lesson_status,
        cards_total: n.cards_total || 0,
        cards_answered: n.cards_answered || 0,
        is_learned: n.status === 'mastered',
        card_state: n.status === 'mastered' ? 2 : (n.status === 'lesson_done' ? 1 : 0)
    }));
    const tree = buildPathTree(nodes);
    const edges = [];
    const linked = new Set();
    const pairKey = (a, b) => (a < b ? `${a}|${b}` : `${b}|${a}`);
    nodes.forEach(n => {
        const p = tree.parent.get(n.id);
        if (!p || p === PATH_COURSE_ID) return;
        n.path_parent = p;
        linked.add(pairKey(p, n.id));
        const isMapParent = n.parent_id === p;
        edges.push({
            source: p,
            target: n.id,
            relation: isMapParent ? 'part_of' : 'prereq',
            label: isMapParent ? 'включает' : 'открывает путь к',
            __structural: true
        });
    });
    (state.edges || []).forEach(e => {
        if (e.from === e.to || linked.has(pairKey(e.from, e.to))) return;
        linked.add(pairKey(e.from, e.to));
        edges.push({ source: e.from, target: e.to, relation: e.relation, label: e.label });
    });
    return { nodes, edges };
}

const KG_RELATION_STYLES = {
    'subject_to_jurisdiction': {
        color: '#60a5fa',        // Яркий синий (структура/институт)
        lightColor: '#2563eb',
        label: 'входит в структуру',
        icon: 'schema',
        borderClass: 'border-blue-500/30 hover:border-blue-500',
        textClass: 'text-blue-600 dark:text-blue-400',
        bgClass: 'bg-blue-50/70 dark:bg-blue-950/40'
    },
    'demarcated_from': {
        color: '#fbbf24',        // Яркий янтарный (разграничение компетенции)
        lightColor: '#d97706',
        label: 'разграничивается с',
        icon: 'compare_arrows',
        borderClass: 'border-amber-500/30 hover:border-amber-500',
        textClass: 'text-amber-600 dark:text-amber-400',
        bgClass: 'bg-amber-50/70 dark:bg-amber-950/40'
    },
    'appealed_to': {
        color: '#22d3ee',        // Яркий циан (обжалование / инстанция)
        lightColor: '#0891b2',
        label: 'обжалуется в',
        icon: 'upgrade',
        borderClass: 'border-cyan-500/30 hover:border-cyan-500',
        textClass: 'text-cyan-600 dark:text-cyan-400',
        bgClass: 'bg-cyan-50/70 dark:bg-cyan-950/40'
    },
    'excludes_application': {
        color: '#fb7185',        // Яркий кораллово-розовый (исключение нормы)
        lightColor: '#e11d48',
        label: 'исключает применение',
        icon: 'block',
        borderClass: 'border-rose-500/30 hover:border-rose-500',
        textClass: 'text-rose-600 dark:text-rose-400',
        bgClass: 'bg-rose-50/70 dark:bg-rose-950/40'
    },
    // «Путь знаний»: пререквизит — тема открывается после освоения этого узла
    'prereq': {
        color: '#a78bfa',
        lightColor: '#7c3aed',
        label: 'открывает путь к',
        icon: 'lock_open',
        borderClass: 'border-violet-300 dark:border-violet-700 hover:border-primary',
        textClass: 'text-violet-700 dark:text-violet-300',
        bgClass: 'bg-violet-50 dark:bg-violet-950/40'
    },
    'default': {
        color: '#94a3b8',        // Светло-серебристый сланец
        lightColor: '#64748b',
        label: 'связь',
        icon: 'arrow_forward',
        borderClass: 'border-neutral-300 dark:border-neutral-700 hover:border-primary',
        textClass: 'text-neutral-600 dark:text-neutral-400',
        bgClass: 'bg-neutral-50 dark:bg-neutral-800/60'
    }
};

function getKgRelationStyle(rel) {
    const key = (rel || '').toLowerCase().trim().replace(/[\s-]+/g, '_');
    return KG_RELATION_STYLES[key] || KG_RELATION_STYLES['default'];
}

function getKgRelationChipClass(rel) {
    const key = (rel || '').toLowerCase().trim().replace(/[\s-]+/g, '_');
    if (key === 'subject_to_jurisdiction') return 'chip-relation chip-relation-jurisdiction';
    if (key === 'demarcated_from') return 'chip-relation chip-relation-demarcated';
    if (key === 'appealed_to') return 'chip-relation chip-relation-appealed';
    if (key === 'excludes_application') return 'chip-relation chip-relation-excluded';
    return 'chip-relation chip-relation-default';
}

function getKgNodeColor(category) {
    return KG_CATEGORY_COLORS[category] || KG_CATEGORY_COLORS['default'];
}

function getKgRelationLinkColor(relation, isLit, isDark, softMultiplier = 1.0) {
    const key = (relation || '').toLowerCase().trim().replace(/[\s-]+/g, '_');
    const style = getKgRelationStyle(key);

    if (isLit) {
        // Подсвеченное / активное состояние: сочные чистые цвета как в основном приложении
        return isDark ? (style.color || '#38bdf8') : (style.lightColor || style.color || '#0284c7');
    }

    // Спокойное состояние: яркие, насыщенные цвета приложения без блеклости
    if (key === 'subject_to_jurisdiction') {
        return isDark
            ? `rgba(96, 165, 250, ${Math.min(1.0, 0.52 * softMultiplier)})`
            : `rgba(37, 99, 235, ${Math.min(1.0, 0.45 * softMultiplier)})`;
    }
    if (key === 'demarcated_from') {
        return isDark
            ? `rgba(251, 191, 36, ${Math.min(1.0, 0.65 * softMultiplier)})`
            : `rgba(217, 119, 6, ${Math.min(1.0, 0.55 * softMultiplier)})`;
    }
    if (key === 'appealed_to') {
        return isDark
            ? `rgba(34, 211, 238, ${Math.min(1.0, 0.65 * softMultiplier)})`
            : `rgba(8, 145, 178, ${Math.min(1.0, 0.55 * softMultiplier)})`;
    }
    if (key === 'excludes_application') {
        return isDark
            ? `rgba(251, 113, 133, ${Math.min(1.0, 0.65 * softMultiplier)})`
            : `rgba(225, 29, 72, ${Math.min(1.0, 0.55 * softMultiplier)})`;
    }
    return isDark
        ? `rgba(148, 163, 184, ${Math.min(1.0, 0.42 * softMultiplier)})`
        : `rgba(100, 116, 139, ${Math.min(1.0, 0.38 * softMultiplier)})`;
}

function getCleanGraphData() {
    if (!currentKgGraphData || !currentKgGraphData.nodes || currentKgGraphData.nodes.length === 0) {
        return null;
    }

    // 1. Clean nodes: preserve parent_id, hierarchy levels, and real-time FSRS learning states
    const seenNodeIds = new Set();
    const nodes = [];
    for (const n of currentKgGraphData.nodes) {
        if (!n || !n.id) continue;
        const nId = String(n.id);
        if (seenNodeIds.has(nId)) continue;
        seenNodeIds.add(nId);
        const lvl = n.level !== undefined ? Number(n.level) : (n.parent_id ? 2 : 1);
        const cardState = n.card_state !== undefined ? n.card_state : (n.is_learned ? 2 : 0);
        nodes.push({
            id: nId,
            name: n.name || n.id,
            category: n.category || 'authority',
            summary: n.summary || '',
            level: lvl,
            parent_id: n.parent_id ? String(n.parent_id) : undefined,
            val: lvl === 0 ? 14 : (lvl === 1 ? 8 : 4.5),
            card_id: n.card_id,
            card_state: cardState,
            is_learned: Boolean(n.is_learned || cardState > 0),
            reps: n.reps || 0,
            learned_count: n.learned_count || 0,
            total_leaves: n.total_leaves || 0,
            mastered_count: n.mastered_count || 0,
            // Поля «Пути знаний»
            db_id: n.db_id,
            status: n.status,
            tier: n.tier,
            prereq_keys: n.prereq_keys,
            lesson_status: n.lesson_status,
            cards_total: n.cards_total,
            cards_answered: n.cards_answered,
            path_parent: n.path_parent
        });
    }

    const nodeMap = new Map(nodes.map(n => [n.id, n]));
    const nodeIds = seenNodeIds;

    // 2. Clean links: D3 converts source/target to node object references.
    // We always extract the underlying ID string to prevent object-in-Set lookup failures.
    const rawEdges = (currentKgGraphData.edges || currentKgGraphData.links || []);
    const links = rawEdges
        .map(e => {
            const s = (typeof e.source === 'object' && e.source !== null) ? String(e.source.id) : String(e.source);
            const t = (typeof e.target === 'object' && e.target !== null) ? String(e.target.id) : String(e.target);
            return {
                source: s,
                target: t,
                relation: e.relation || '',
                label: e.label || '',
                __structural: Boolean(e.__structural),
                __key: getGraphLinkKey(s, t)
            };
        })
        .filter(e => nodeIds.has(e.source) && nodeIds.has(e.target) && e.source !== e.target);

    // Compute __curvature for reciprocal or parallel links
    const pairMap = new Map();
    links.forEach(l => {
        const pairKey = l.source < l.target ? `${l.source}~${l.target}` : `${l.target}~${l.source}`;
        if (!pairMap.has(pairKey)) {
            pairMap.set(pairKey, []);
        }
        pairMap.get(pairKey).push(l);
    });

    pairMap.forEach(group => {
        if (group.length === 1) {
            group[0].__curvature = 0;
        } else if (group.length === 2) {
            const [l1, l2] = group;
            if (l1.source === l2.target && l1.target === l2.source) {
                // Reciprocal edges A->B and B->A bow away from each other
                l1.__curvature = 0.2;
                l2.__curvature = 0.2;
            } else {
                // Parallel edges in the same direction A->B and A->B
                l1.__curvature = 0.2;
                l2.__curvature = -0.2;
            }
        } else {
            group.forEach((l, idx) => {
                const sign = (idx % 2 === 0) ? 1 : -1;
                const factor = Math.ceil((idx + 1) / 2);
                l.__curvature = sign * factor * 0.18;
            });
        }
    });

    // 3. Fallback: If any level 2 concept lacks parent_id, infer it from connecting edge to a level 1 institute
    links.forEach(l => {
        const sNode = nodeMap.get(l.source);
        const tNode = nodeMap.get(l.target);
        if (sNode && tNode) {
            if (sNode.level === 1 && tNode.level === 2 && !tNode.parent_id) {
                tNode.parent_id = sNode.id;
            } else if (tNode.level === 1 && sNode.level === 2 && !sNode.parent_id) {
                sNode.parent_id = tNode.id;
            }
        }
    });

    return { nodes, links };
}

window.openKnowledgeGraphModal = function(targetSubject) {
    const sub = targetSubject || getActiveDeckSubject();
    currentKgSubject = sub;

    // Фоновая предварительная загрузка библиотек физики графа
    if (!window.ForceGraph || !window.d3) {
        ensureGraphVendorLoaded().catch(() => {});
    }

    const modal = document.getElementById('knowledge-graph-modal');
    if (!modal) return;
    
    modal.classList.remove('hidden');

    const badge = document.getElementById('kg-subject-badge');
    if (badge) badge.textContent = sub.toUpperCase();

    // Preserve selected view or default to tree view initially
    switchKgView('graph');
    loadKnowledgeGraph(sub);
};

window.closeKnowledgeGraphModal = function() {
    const modal = document.getElementById('knowledge-graph-modal');
    if (modal) modal.classList.add('hidden');
    closeKgNodeDrawer();
    if (document.fullscreenElement && document.exitFullscreen) {
        document.exitFullscreen().catch(() => {});
    }
    const icon = document.getElementById('kg-fullscreen-icon');
    if (icon) icon.textContent = 'fullscreen';
    if (currentForceGraphInstance && currentForceGraphInstance.pauseAnimation) {
        currentForceGraphInstance.pauseAnimation();
    }
};

window.toggleKgFullscreen = function() {
    const modal = document.getElementById('knowledge-graph-modal');
    const icon = document.getElementById('kg-fullscreen-icon');
    if (!modal) return;

    if (!document.fullscreenElement) {
        if (modal.requestFullscreen) {
            modal.requestFullscreen().catch(() => {});
        }
        if (icon) icon.textContent = 'fullscreen_exit';
    } else {
        if (document.exitFullscreen) {
            document.exitFullscreen().catch(() => {});
        }
        if (icon) icon.textContent = 'fullscreen';
    }

    setTimeout(() => {
        if (currentForceGraphInstance && currentKgView === 'graph') {
            const wrapper = document.getElementById('kg-graph-canvas-wrapper');
            if (wrapper) {
                const w = wrapper.clientWidth || window.innerWidth;
                const h = wrapper.clientHeight || (window.innerHeight - 120);
                currentForceGraphInstance.width(w).height(h);
                currentForceGraphInstance.zoomToFit(400, 30);
            }
        }
    }, 250);
};

document.addEventListener('fullscreenchange', () => {
    const icon = document.getElementById('kg-fullscreen-icon');
    if (icon) {
        icon.textContent = document.fullscreenElement ? 'fullscreen_exit' : 'fullscreen';
    }
    if (currentForceGraphInstance && currentKgView === 'graph') {
        setTimeout(() => {
            const wrapper = document.getElementById('kg-graph-canvas-wrapper');
            if (wrapper) {
                const w = wrapper.clientWidth || window.innerWidth;
                const h = wrapper.clientHeight || (window.innerHeight - 120);
                currentForceGraphInstance.width(w).height(h);
                currentForceGraphInstance.zoomToFit(400, 30);
            }
        }, 200);
    }
});

// Граф — единственный вид «Пути знаний» (вид «Дерево» удалён). Функцию вызывают и из тренировки.
window.switchKgView = function() {
    currentKgView = 'graph';
    const graphView = document.getElementById('kg-graph-view');
    if (graphView) graphView.classList.remove('hidden');
    if (currentKgGraphData) {
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                initForceGraph(currentKgGraphData);
            });
        });
    }
};

window.loadKnowledgeGraph = async function(subject) {
    let sub = subject || currentKgSubject || getActiveDeckSubject();
    if (!sub || sub === 'all') {
        const sel = document.getElementById('subject-selector');
        if (sel && sel.options) {
            for (let i = 0; i < sel.options.length; i++) {
                if (sel.options[i].value && sel.options[i].value !== 'all') {
                    sub = sel.options[i].value;
                    break;
                }
            }
        }
    }

    const badge = document.getElementById('kg-subject-badge');
    const loading = document.getElementById('kg-loading');
    const emptyState = document.getElementById('kg-empty-state');
    const countBadge = document.getElementById('kg-node-count-badge');

    if (!sub || sub === 'all') {
        if (loading) loading.classList.add('hidden');
        if (emptyState) {
            emptyState.classList.remove('hidden');
            const h4 = emptyState.querySelector('h4');
            if (h4) h4.textContent = 'Колоды пока отсутствуют';
            const p = emptyState.querySelector('p');
            if (p) {
                p.textContent = 'У вас пока нет колод для построения графа знаний. Загрузите или создайте учебный материал.';
            }
            const actions = emptyState.querySelector('#kg-empty-actions') || emptyState.querySelector('.flex.flex-col, .flex.gap-2') || emptyState.querySelector('div:last-child');
            if (actions) {
                actions.innerHTML = '';
            }
        }
        if (countBadge) countBadge.textContent = '0 узлов';
        if (badge) badge.textContent = '—';
        currentKgGraphData = null;
        return;
    }

    currentKgSubject = sub;
    if (badge) badge.textContent = sub.toUpperCase();

    if (loading) loading.classList.remove('hidden');
    if (emptyState) emptyState.classList.add('hidden');
    closeKgNodeDrawer();

    try {
        const res = await apiFetch(`/api/path/${encodeURIComponent(sub)}`);
        if (!res.ok) {
            // Check if 404
            if (emptyState) emptyState.classList.remove('hidden');
            if (countBadge) countBadge.textContent = '0 узлов';
            currentKgGraphData = null;
            return;
        }

        const data = await res.json();
        currentPathState = data;
        currentKgSubject = data.subject || sub;
        if (badge) badge.textContent = currentKgSubject.toUpperCase();
        currentKgGraphData = pathStateToGraphData(data);

        const nodesCount = (currentKgGraphData && currentKgGraphData.nodes) ? currentKgGraphData.nodes.length : 0;
        if (countBadge) countBadge.textContent = `${nodesCount} узлов`;

        if (nodesCount === 0) {
            if (emptyState) emptyState.classList.remove('hidden');
            return;
        }

        setTimeout(() => {
            initForceGraph(currentKgGraphData);
        }, 50);

    } catch (e) {
        console.error("Сбой загрузки каркаса знаний:", e);
        if (emptyState) emptyState.classList.remove('hidden');
    } finally {
        if (loading) loading.classList.add('hidden');
    }
};

function wrapNodeText(text, maxChars = 16) {
    text = String(text || '');
    if (!text || text.length <= maxChars) return [text || ''];
    const words = text.split(' ');
    const lines = [];
    let cur = '';
    for (const w of words) {
        if ((cur ? (cur + ' ' + w) : w).length <= maxChars) {
            cur = cur ? (cur + ' ' + w) : w;
        } else {
            if (cur) lines.push(cur);
            cur = w;
        }
    }
    if (cur) lines.push(cur);
    if (lines.length > 2) {
        return [lines[0], lines.slice(1).join(' ').substring(0, maxChars - 2) + '..'];
    }
    return lines;
}

let isInitialLayoutFit = true;
let isInitializingGraph = false;
let kgViewportBounds = { minX: -1e6, maxX: 1e6, minY: -1e6, maxY: 1e6 };
let searchHighlightNodes = new Set();
let searchHighlightLinkKeys = new Set();
let searchBackboneNodes = new Set();
let searchBackboneLinkKeys = new Set();
let activeSearchTargetId = null;
let activeSelectedLink = null;
let nodeClickStep = 0; // 0 = idle, 1 = node focused (drawer open), 2 = spotlight backbone & relations
let isDrawerCollapsed = false;
let lastPointerDownPos = null;
let currentLinksFilter = 'all';

let hoveredNode = null;
let hoveredLink = null;
let hoveredLinkKeys = new Set();
let hoveredNeighborNodeIds = new Set();
let kgResizeHandler = null;

function getGraphLinkKey(source, target) {
    const s = String((typeof source === 'object' && source !== null) ? source.id : source);
    const t = String((typeof target === 'object' && target !== null) ? target.id : target);
    return `${s}->${t}`;
}

function getUndirectedLinkKey(source, target) {
    const s = String((typeof source === 'object' && source !== null) ? source.id : source);
    const t = String((typeof target === 'object' && target !== null) ? target.id : target);
    return s < t ? `${s}--${t}` : `${t}--${s}`;
}

// Результат последней раскладки: круг/узел предмета и изгибы линий (рисуются в onRenderFramePre и linkCanvasObject)
let kgPathLayoutMeta = null;

// Связи между основами и темами: по умолчанию фоном (видно дерево пути), по кнопке «Связи» — все ярко.
// Связи выбранного или наведённого узла подсвечиваются всегда.
let kgShowCrossLinks = false;

window.toggleKgCrossLinks = function() {
    kgShowCrossLinks = !kgShowCrossLinks;
    const btn = document.getElementById('kg-cross-links-toggle');
    if (btn) {
        btn.setAttribute('aria-pressed', String(kgShowCrossLinks));
        btn.classList.toggle('bg-primary', kgShowCrossLinks);
        btn.classList.toggle('text-on-primary', kgShowCrossLinks);
        btn.classList.toggle('text-neutral-500', !kgShowCrossLinks);
    }
    if (currentForceGraphInstance) {
        // Движок перерисовывает кадр только при изменениях: незаметный сдвиг масштаба, физику не трогаем
        const z = currentForceGraphInstance.zoom();
        currentForceGraphInstance.zoom(z * 1.0001, 0);
    }
};

// Раскладка «Пути знаний»: координаты считает 07a_path_layout.js, здесь узлы фиксируются,
// линиям дерева передаются изгибы, связям между ветками — дуги (в круговой) или скрытие (в слоях).
function applyPathLayout(graphData, layoutType) {
    const meta = computePathLayout(graphData.nodes, graphData.links, layoutType);
    kgPathLayoutMeta = { layout: layoutType, ...meta };
    const nodeById = new Map(graphData.nodes.map(n => [String(n.id), n]));
    let ringLane = 0;
    graphData.nodes.forEach(n => {
        const p = meta.positions[String(n.id)];
        if (!p) return;
        n.x = n.fx = p.x;
        n.y = n.fy = p.y;
    });
    graphData.links.forEach(l => {
        const sId = String(typeof l.source === 'object' ? l.source.id : l.source);
        const tId = String(typeof l.target === 'object' ? l.target.id : l.target);
        l.__bends = null;
        l.__ring = null;
        if (l.__structural) {
            l.__curvature = 0;
            if (layoutType !== 'radial' && meta.parent[tId] === sId) l.__bends = meta.bends[tId] || null;
            return;
        }
        const a = nodeById.get(sId);
        const b = nodeById.get(tId);
        if (!a || !b) return;
        if (layoutType === 'radial') {
            // Связь идёт по «кольцевой дороге» между кругом основ и кольцом тем: не режет центр
            l.__curvature = 0;
            l.__ring = (meta.routeRadius || 0) + (ringLane++ % 7 - 3) * 6;
        } else {
            l.__curvature = 0.25;
        }
    });
}

// Предмет не является узлом графа (по нему нельзя тапнуть): рисуем его на подложке.
// Круговая — круг с названием, основы на его ободе. Слои — плашка-заголовок и линии к основам.
function drawPathCourse(ctx, globalScale) {
    const meta = kgPathLayoutMeta;
    if (!meta || !meta.course) return;
    const dark = document.documentElement.classList.contains('dark');
    const title = (currentPathState && currentPathState.title) || '';
    ctx.save();
    if (meta.layout === 'radial') {
        if (meta.hubRadius > 0) {
            ctx.beginPath();
            ctx.arc(0, 0, meta.hubRadius, 0, 2 * Math.PI, false);
            ctx.fillStyle = dark ? 'rgba(59, 130, 246, 0.06)' : 'rgba(59, 130, 246, 0.05)';
            ctx.fill();
            ctx.lineWidth = 2 / Math.max(globalScale, 0.2);
            ctx.strokeStyle = dark ? 'rgba(96, 165, 250, 0.45)' : 'rgba(37, 99, 235, 0.35)';
            ctx.stroke();
        }
        if (title) {
            const fontSize = Math.max(12, (meta.hubRadius || 120) * 0.14);
            ctx.font = `700 ${fontSize}px "Plus Jakarta Sans", -apple-system, sans-serif`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillStyle = dark ? 'rgba(226, 232, 240, 0.85)' : 'rgba(15, 23, 42, 0.75)';
            wrapNodeText(title, 18).slice(0, 3).forEach((line, idx, arr) => {
                ctx.fillText(line, 0, (idx - (arr.length - 1) / 2) * fontSize * 1.2);
            });
        }
        ctx.restore();
        return;
    }
    // Линии от предмета к основам (с изгибами той же «шины»)
    ctx.strokeStyle = dark ? 'rgba(148, 163, 184, 0.7)' : 'rgba(71, 85, 105, 0.6)';
    ctx.lineWidth = 1.4 / globalScale;
    Object.entries(meta.parent).forEach(([id, parentId]) => {
        if (parentId !== PATH_COURSE_ID) return;
        const target = meta.positions[id];
        if (!target) return;
        ctx.beginPath();
        ctx.moveTo(meta.course.x, meta.course.y);
        (meta.bends[id] || []).forEach(pt => ctx.lineTo(pt.x, pt.y));
        ctx.lineTo(target.x, target.y);
        ctx.stroke();
    });
    // Плашка предмета
    const fontSize = 13;
    ctx.font = `700 ${fontSize}px "Plus Jakarta Sans", -apple-system, sans-serif`;
    const lines = wrapNodeText(title || 'Предмет', 22).slice(0, 2);
    const w = Math.max(...lines.map(l => ctx.measureText(l).width)) + 28;
    const h = lines.length * fontSize * 1.25 + 16;
    const x = meta.course.x - w / 2;
    const y = meta.course.y - h / 2;
    ctx.fillStyle = dark ? '#1e293b' : '#eff6ff';
    ctx.strokeStyle = dark ? 'rgba(96, 165, 250, 0.6)' : 'rgba(37, 99, 235, 0.5)';
    ctx.lineWidth = 1.5 / globalScale;
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(x, y, w, h, 10); else ctx.rect(x, y, w, h);
    ctx.fill();
    ctx.stroke();
    ctx.fillStyle = dark ? '#e2e8f0' : '#0f172a';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    lines.forEach((line, idx) => ctx.fillText(line, meta.course.x, meta.course.y + (idx - (lines.length - 1) / 2) * fontSize * 1.25));
    ctx.restore();
}

// Связь между ветками в круговой раскладке: радиально к кольцевой дороге, по ней короткой дугой, обратно к узлу
function drawRingRoadLink(link, ctx, globalScale) {
    const s = link.source;
    const t = link.target;
    if (!s || !t || !Number.isFinite(s.x) || !Number.isFinite(t.x)) return;
    const colorFn = currentForceGraphInstance.linkColor();
    const color = typeof colorFn === 'function' ? colorFn(link) : colorFn;
    if (!color || color === 'rgba(0, 0, 0, 0)') return;
    const widthFn = currentForceGraphInstance.linkWidth();
    const r = link.__ring;
    const a1 = Math.atan2(s.y, s.x);
    const a2 = Math.atan2(t.y, t.x);
    let delta = a2 - a1;
    while (delta > Math.PI) delta -= 2 * Math.PI;
    while (delta < -Math.PI) delta += 2 * Math.PI;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(s.x, s.y);
    ctx.lineTo(Math.cos(a1) * r, Math.sin(a1) * r);
    ctx.arc(0, 0, r, a1, a1 + delta, delta < 0);
    ctx.lineTo(t.x, t.y);
    ctx.strokeStyle = color;
    ctx.lineWidth = (typeof widthFn === 'function' ? widthFn(link) : widthFn) / globalScale;
    ctx.stroke();
    ctx.restore();
}

function kgStructuralLinkBends(link) {
    return link && link.__bends && link.__bends.length ? link.__bends : null;
}

window.setGraphLayout = function(layoutType) {
    const allowed = ['radial', 'tree', 'horizontal'];
    currentKgLayout = allowed.includes(layoutType) ? layoutType : 'radial';
    isInitialLayoutFit = true;

    const buttons = { radial: 'kg-layout-radial', tree: 'kg-layout-tree', horizontal: 'kg-layout-lr' };
    const inactiveClass = "p-1.5 sm:px-2.5 sm:py-1 rounded-lg font-bold uppercase transition-all text-neutral-500 hover:text-primary hover:bg-neutral-100 dark:hover:bg-neutral-800 flex items-center gap-1 cursor-pointer";
    const activeClass = "p-1.5 sm:px-2.5 sm:py-1 rounded-lg font-bold uppercase transition-all bg-primary text-on-primary shadow-xs flex items-center gap-1 cursor-pointer";
    Object.entries(buttons).forEach(([layout, id]) => {
        const btn = document.getElementById(id);
        if (btn) btn.className = currentKgLayout === layout ? activeClass : inactiveClass;
    });

    if (!currentForceGraphInstance) return;

    // Сбрасываем зависшие сенсорные выделения связей и узлов при смене раскладки
    hoveredNode = null;
    hoveredLink = null;
    hoveredLinkKeys.clear();
    hoveredNeighborNodeIds.clear();

    const cleanData = getCleanGraphData();
    if (!cleanData) return;
    applyPathLayout(cleanData, currentKgLayout);

    // Все узлы стоят на вычисленных местах: физика не нужна, остаётся только отрисовка
    currentForceGraphInstance.d3Force('charge', null);
    currentForceGraphInstance.d3Force('center', null);
    currentForceGraphInstance.d3Force('collide', null);
    currentForceGraphInstance.d3Force('x', null);
    currentForceGraphInstance.d3Force('y', null);
    currentForceGraphInstance.d3Force('radial', null);
    if (currentForceGraphInstance.d3Force('link')) {
        currentForceGraphInstance.d3Force('link').strength(0);
    }
    currentForceGraphInstance
        .dagMode(null)
        .onDagError(() => false)
        .graphData(cleanData);

    setTimeout(() => {
        if (currentForceGraphInstance && isInitialLayoutFit) {
            isInitialLayoutFit = false;
            currentForceGraphInstance.zoomToFit(500, 60);
        }
    }, 300);
};

/* ==========================================================================
   ПОИСК ПО ГРАФУ И ПОДСВЕТКА ЛИНИИ СВЯЗЕЙ (SPOTLIGHT SUBGRAPH)
   ========================================================================== */

window.onKgSearchKeyDown = function(e) {
    if (e.key === 'Enter') {
        e.preventDefault();
        window.submitKgSearch();
    }
};

window.submitKgSearch = function() {
    const input = document.getElementById('kg-search-input');
    if (!input) return;
    const q = (input.value || '').trim().toLowerCase();
    input.blur(); // Скрываем виртуальную клавиатуру на мобильных телефонах

    const resBox = document.getElementById('kg-search-results');
    if (resBox) resBox.classList.add('hidden');

    const cleanData = getCleanGraphData();
    if (!cleanData || !cleanData.nodes || cleanData.nodes.length === 0) return;

    if (!q) {
        clearKgSearch();
        return;
    }

    // Ищем лучшее совпадение: точное имя, затем префикс, затем вхождение в имя, затем вхождение в описание
    let match = cleanData.nodes.find(n => (n.name || '').toLowerCase() === q);
    if (!match) {
        match = cleanData.nodes.find(n => (n.name || '').toLowerCase().startsWith(q));
    }
    if (!match) {
        match = cleanData.nodes.find(n => (n.name || '').toLowerCase().includes(q));
    }
    if (!match) {
        match = cleanData.nodes.find(n => (n.summary || '').toLowerCase().includes(q));
    }

    if (match) {
        selectSearchResult(match, true);
    } else {
        if (typeof window.showNotification === 'function') {
            window.showNotification("Понятие не найдено в графе", "info");
        }
    }
};

window.onKgSearchInput = function(query) {
    highlightConceptSearch(query);
};

window.clearKgSearch = function(preserveCamera = false) {
    const input = document.getElementById('kg-search-input');
    if (input) input.value = '';
    const clearBtn = document.getElementById('kg-search-clear');
    if (clearBtn) clearBtn.classList.add('hidden');
    const resBox = document.getElementById('kg-search-results');
    if (resBox) resBox.classList.add('hidden');

    const pill = document.getElementById('kg-selection-pill');
    if (pill) pill.classList.add('hidden');

    searchHighlightNodes.clear();
    searchHighlightLinkKeys.clear();
    searchBackboneNodes.clear();
    searchBackboneLinkKeys.clear();
    activeSearchTargetId = null;
    activeSelectedLink = null;
    nodeClickStep = 0;
    isDrawerCollapsed = false;

    hoveredNode = null;
    hoveredLink = null;
    hoveredLinkKeys.clear();
    hoveredNeighborNodeIds.clear();

    closeKgNodeDrawer();

    if (currentForceGraphInstance) {
        currentForceGraphInstance.refresh();
        if (!preserveCamera) {
            currentForceGraphInstance.zoomToFit(400, 40);
        }
    }
    triggerHaptic('light');
};

window.highlightConceptSearch = function(searchTerm) {
    const clearBtn = document.getElementById('kg-search-clear');
    const resBox = document.getElementById('kg-search-results');

    if (!searchTerm || !searchTerm.trim()) {
        window.clearKgSearch();
        return;
    }

    if (clearBtn) clearBtn.classList.remove('hidden');

    const cleanData = getCleanGraphData();
    if (!cleanData || !cleanData.nodes) return;

    const q = searchTerm.toLowerCase().trim();

    // Поиск по названию узла, описанию и смысловым совпадениям
    const matches = cleanData.nodes.filter(n => {
        const name = (n.name || '').toLowerCase();
        const summary = (n.summary || '').toLowerCase();
        return name.includes(q) || summary.includes(q);
    });

    if (matches.length === 0) {
        if (resBox) {
            resBox.innerHTML = '<div class="p-2 text-neutral-400 text-center">Ничего не найдено</div>';
            resBox.classList.remove('hidden');
        }
        return;
    }

    // Рендер выпадающего списка быстрых совпадений
    if (resBox) {
        resBox.innerHTML = '';
        matches.slice(0, 8).forEach(m => {
            const item = document.createElement('div');
            item.className = 'px-2.5 py-1.5 hover:bg-neutral-100 dark:hover:bg-neutral-800 rounded-lg cursor-pointer flex items-center justify-between gap-2 text-neutral-800 dark:text-neutral-200 transition-colors';
            item.innerHTML = `
                <div class="flex items-center gap-1.5 min-w-0">
                    <span class="w-2 h-2 rounded-full shrink-0 bg-neutral-400"></span>
                    <span class="truncate font-medium">${escapeHTML(m.name || m.id)}</span>
                </div>
                <span class="text-[9px] text-neutral-400 uppercase shrink-0 font-mono">${m.level === 0 ? 'КУРС' : (m.level === 1 ? 'ИНСТИТУТ' : 'ПОНЯТИЕ')}</span>
            `;
            item.onclick = () => {
                selectSearchResult(m, true);
            };
            resBox.appendChild(item);
        });
        resBox.classList.remove('hidden');
    }

    // Фокусируемся на первом лучшем совпадении
    selectSearchResult(matches[0], false);
};

function centerCameraOnNode(targetNode) {
    if (!currentForceGraphInstance) return;
    const cleanData = getCleanGraphData();
    const gNodes = (currentForceGraphInstance.graphData && currentForceGraphInstance.graphData().nodes) || (cleanData ? cleanData.nodes : []) || [];
    const liveTarget = gNodes.find(n => String(n.id) === String(targetNode.id)) || targetNode;
    if (typeof liveTarget.x === 'number' && !isNaN(liveTarget.x) && (liveTarget.x !== 0 || liveTarget.y !== 0)) {
        const isMobile = window.innerWidth <= 768;
        const curZoom = currentForceGraphInstance.zoom ? currentForceGraphInstance.zoom() : 1.0;
        const targetZoom = Math.max(curZoom, isMobile ? 1.30 : 1.50);
        const wrapper = document.getElementById('kg-graph-canvas-wrapper');
        const h = wrapper ? wrapper.clientHeight : (window.innerHeight - 150);
        const yOffset = isMobile ? (h * 0.16 / targetZoom) : 0;
        currentForceGraphInstance.centerAt(liveTarget.x, liveTarget.y + yOffset, 500);
        currentForceGraphInstance.zoom(targetZoom, 500);
    }
}

function focusNodeStep1(targetNode) {
    activeSearchTargetId = String(targetNode.id);
    activeSelectedLink = null;
    nodeClickStep = 1;

    searchBackboneNodes.clear();
    searchBackboneLinkKeys.clear();
    searchHighlightNodes.clear();
    searchHighlightNodes.add(activeSearchTargetId);
    searchHighlightLinkKeys.clear();

    // Шаг 1: Фокус исключительно на выбранном узле (шторка развернута, связи еще не подсвечиваются)

    centerCameraOnNode(targetNode);

    const pill = document.getElementById('kg-selection-pill');
    const pillName = document.getElementById('kg-selection-pill-name');
    if (pill && pillName) {
        pillName.textContent = targetNode.name || targetNode.id;
        pill.classList.remove('hidden');
    }

    // Открываем развернутую шторку (полный вид с описанием)
    showKgNodeDrawer(targetNode, false);
    triggerHaptic('light');

    if (currentForceGraphInstance) {
        currentForceGraphInstance.refresh();
    }
}

function focusNodeStep2(targetNode) {
    activeSearchTargetId = String(targetNode.id);
    activeSelectedLink = null;
    nodeClickStep = 2;

    searchBackboneNodes.clear();
    searchBackboneLinkKeys.clear();
    searchHighlightNodes.clear();
    searchHighlightNodes.add(activeSearchTargetId);
    searchHighlightLinkKeys.clear();

    const cleanData = getCleanGraphData();
    const allLinks = (cleanData.links && cleanData.links.length > 0)
        ? cleanData.links
        : ((currentKgGraphData && currentKgGraphData.edges) ? currentKgGraphData.edges : []);

    const nodeMap = new Map(cleanData.nodes.map(n => [String(n.id), n]));
    const rootNode = cleanData.nodes.find(n => n.level === 0);
    const rootId = rootNode ? String(rootNode.id) : null;

    // 1. Магистраль от ядра к институту и целевому узлу
    let currNode = targetNode;
    while (currNode && currNode.parent_id) {
        const pId = String(currNode.parent_id);
        const parentNode = nodeMap.get(pId);
        if (!parentNode) break;

        searchHighlightNodes.add(pId);
        searchBackboneNodes.add(pId);

        const cId = String(currNode.id);
        const pEdge = allLinks.find(e => {
            const s = String((typeof e.source === 'object' && e.source !== null) ? e.source.id : e.source);
            const t = String((typeof e.target === 'object' && e.target !== null) ? e.target.id : e.target);
            return (s === pId && t === cId) || (s === cId && t === pId);
        });

        if (pEdge) {
            const lKey = pEdge.__key || getGraphLinkKey(pEdge.source, pEdge.target);
            searchBackboneLinkKeys.add(lKey);
            searchHighlightLinkKeys.add(lKey);
        }

        currNode = parentNode;
    }

    if (rootId && !searchHighlightNodes.has(rootId)) {
        const topNodeId = currNode ? String(currNode.id) : null;
        if (topNodeId && topNodeId !== rootId) {
            const rootEdge = allLinks.find(e => {
                const s = String((typeof e.source === 'object' && e.source !== null) ? e.source.id : e.source);
                const t = String((typeof e.target === 'object' && e.target !== null) ? e.target.id : e.target);
                return (s === rootId && t === topNodeId) || (s === topNodeId && t === rootId);
            });
            if (rootEdge) {
                searchHighlightNodes.add(rootId);
                searchBackboneNodes.add(rootId);
                const lKey = rootEdge.__key || getGraphLinkKey(rootEdge.source, rootEdge.target);
                searchBackboneLinkKeys.add(lKey);
                searchHighlightLinkKeys.add(lKey);
            }
        }
    }

    // 2. Все связи целевого узла (исходящие и входящие)
    allLinks.forEach(edge => {
        const sId = String((typeof edge.source === 'object' && edge.source !== null) ? edge.source.id : edge.source);
        const tId = String((typeof edge.target === 'object' && edge.target !== null) ? edge.target.id : edge.target);
        if (sId === activeSearchTargetId || tId === activeSearchTargetId) {
            const lKey = edge.__key || getGraphLinkKey(sId, tId);
            searchHighlightLinkKeys.add(lKey);
            searchHighlightNodes.add(sId);
            searchHighlightNodes.add(tId);
        }
    });

    const pill = document.getElementById('kg-selection-pill');
    const pillName = document.getElementById('kg-selection-pill-name');
    if (pill && pillName) {
        pillName.textContent = `${targetNode.name || targetNode.id} • Магистраль и связи (${searchHighlightLinkKeys.size})`;
        pill.classList.remove('hidden');
    }

    // Сворачиваем шторку в компактную полоску (Peek), чтобы весь граф связей был открыт на холсте
    showKgNodeDrawer(targetNode, true);
    triggerHaptic('medium');

    if (currentForceGraphInstance) {
        currentForceGraphInstance.refresh();
    }
}

window.selectSearchResult = function(targetNode, closeDropdown = true) {
    if (!targetNode) return;
    const resBox = document.getElementById('kg-search-results');
    if (closeDropdown && resBox) resBox.classList.add('hidden');
    focusNodeStep1(targetNode);
};

window.focusNodeInGraph = function(nodeId) {
    const cleanData = getCleanGraphData();
    if (!cleanData || !cleanData.nodes) return;
    const target = cleanData.nodes.find(n => String(n.id) === String(nodeId));
    if (!target) return;

    const nId = String(target.id);
    if (activeSearchTargetId !== nId) {
        // Тап по новому узлу -> Шаг 1: Фокус на понятии и развернутая шторка
        focusNodeStep1(target);
    } else if (nodeClickStep === 1) {
        // Повторный тап по тому же узлу -> Шаг 2: Подсветка связей (магистраль от ядра + разграничения)
        focusNodeStep2(target);
    } else {
        // Третий тап по тому же узлу -> Шаг 3: Полный сброс выделения
        window.clearKgSearch(true);
        window.closeKgNodeDrawer();
        triggerHaptic('light');
    }
};

window.highlightSessionInGraph = function(cards) {
    if (!cards || cards.length === 0) return;
    const cleanData = getCleanGraphData();
    if (!cleanData || !cleanData.nodes || cleanData.nodes.length === 0) return;

    searchHighlightNodes.clear();
    searchHighlightLinkKeys.clear();
    searchBackboneNodes.clear();
    searchBackboneLinkKeys.clear();

    const rootNode = cleanData.nodes.find(n => n.level === 0);
    const rootId = rootNode ? String(rootNode.id) : null;
    if (rootId) {
        searchBackboneNodes.add(rootId);
        searchHighlightNodes.add(rootId);
    }

    const matchedNodes = [];
    cards.forEach(card => {
        let node = null;
        if (card.id) {
            node = cleanData.nodes.find(n => n.card_id === card.id || (n.card_ids && n.card_ids.includes(card.id)));
        }
        if (!node && card.organ_slug) {
            node = cleanData.nodes.find(n => String(n.id) === String(card.organ_slug));
        }
        if (!node && card.translation) {
            const tLower = card.translation.toLowerCase().trim();
            node = cleanData.nodes.find(n => n.name && n.name.toLowerCase().trim() === tLower);
            if (!node) {
                node = cleanData.nodes.find(n => n.name && (tLower.includes(n.name.toLowerCase()) || n.name.toLowerCase().includes(tLower)));
            }
        }
        if (!node && card.text) {
            const qLower = card.text.toLowerCase().trim();
            node = cleanData.nodes.find(n => n.name && qLower.includes(n.name.toLowerCase().trim()));
            if (!node) {
                const words = qLower.match(/[a-zа-яё0-9]{4,}/g) || [];
                for (const w of words) {
                    const stem = w.slice(0, 5);
                    node = cleanData.nodes.find(n => n.name && n.name.toLowerCase().includes(stem));
                    if (node) break;
                }
            }
        }
        if (node && !matchedNodes.some(m => String(m.id) === String(node.id))) {
            matchedNodes.push(node);
        }
    });

    matchedNodes.forEach(node => {
        const nId = String(node.id);
        searchHighlightNodes.add(nId);
        searchBackboneNodes.add(nId);
        if (node.parent_id) {
            const pId = String(node.parent_id);
            searchHighlightNodes.add(pId);
            searchBackboneNodes.add(pId);
        }
    });

    const allLinks = (cleanData.links && cleanData.links.length > 0)
        ? cleanData.links
        : ((currentKgGraphData && currentKgGraphData.edges) ? currentKgGraphData.edges : []);

    allLinks.forEach(edge => {
        const sId = String((typeof edge.source === 'object' && edge.source !== null) ? edge.source.id : edge.source);
        const tId = String((typeof edge.target === 'object' && edge.target !== null) ? edge.target.id : edge.target);
        const lKey = edge.__key || getGraphLinkKey(sId, tId);

        if (searchBackboneNodes.has(sId) && searchBackboneNodes.has(tId)) {
            searchBackboneLinkKeys.add(lKey);
            searchHighlightLinkKeys.add(lKey);
        } else if (searchHighlightNodes.has(sId) && searchHighlightNodes.has(tId)) {
            searchHighlightLinkKeys.add(lKey);
        }
    });

    if (matchedNodes.length > 0) {
        activeSearchTargetId = String(matchedNodes[matchedNodes.length - 1].id);
    }

    if (currentForceGraphInstance) {
        currentForceGraphInstance.refresh();
        setTimeout(() => {
            if (currentForceGraphInstance) {
                currentForceGraphInstance.zoomToFit(600, 50);
            }
        }, 250);
    }

    const pill = document.getElementById('kg-selection-pill');
    const pillName = document.getElementById('kg-selection-pill-name');
    if (pill && pillName) {
        pillName.textContent = `Пройдено в блоке: ${matchedNodes.length} понятий`;
        pill.classList.remove('hidden');
    }
};

window.initForceGraph = async function(graphData) {
    const wrapper = document.getElementById('kg-graph-canvas-wrapper');
    if (!wrapper) return;

    const container = wrapper.parentElement || wrapper;
    const width = wrapper.clientWidth || container.clientWidth || window.innerWidth;
    const height = wrapper.clientHeight || container.clientHeight || (window.innerHeight - 150);

    const isDark = document.documentElement.classList.contains('dark');
    const bgColor = isDark ? '#0e0e0e' : '#fbfbfb';

    // 1. Если граф уже инициализирован, только обновляем размеры и возобновляем анимацию
    if (currentForceGraphInstance) {
        currentForceGraphInstance
            .width(width)
            .height(height)
            .backgroundColor(bgColor);
        if (currentForceGraphInstance.resumeAnimation) {
            currentForceGraphInstance.resumeAnimation();
        }
        setGraphLayout(currentKgLayout || 'radial');
        return;
    }

    // 2. Защита от параллельного двойного запуска конструктора
    if (isInitializingGraph) return;
    isInitializingGraph = true;

    try {
        if (!window.ForceGraph || !window.d3) {
            wrapper.innerHTML = `
                <div class="flex flex-col items-center justify-center p-6 text-center h-full min-h-[300px] text-neutral-600 dark:text-neutral-400">
                    <span class="material-symbols-outlined text-4xl text-primary animate-spin mb-2">sync</span>
                    <p class="font-mono text-sm font-bold text-neutral-800 dark:text-neutral-200 mb-1">Загрузка 2D-движка графа...</p>
                    <p class="text-xs text-neutral-500">Инициализация физической модели D3 и холста...</p>
                </div>
            `;
            try {
                await ensureGraphVendorLoaded();
            } catch (err) {
                wrapper.innerHTML = `
                    <div class="flex flex-col items-center justify-center p-6 text-center h-full min-h-[300px] text-neutral-600 dark:text-neutral-400">
                        <span class="material-symbols-outlined text-4xl text-amber-500 mb-2">account_tree</span>
                        <p class="font-mono text-sm font-bold text-neutral-800 dark:text-neutral-200 mb-1">2D-движок графа недоступен</p>
                        <p class="text-xs mb-4 max-w-sm">Скрипт 2D-визуализации не смог загрузиться из-за сетевых ограничений.</p>
                        <button onclick="window.loadKnowledgeGraph()" class="px-4 py-2 bg-primary text-on-primary rounded-xl font-mono text-xs font-bold uppercase transition-all shadow-xs cursor-pointer flex items-center gap-1.5">
                            <span class="material-symbols-outlined text-[16px]">refresh</span>
                            <span>Повторить</span>
                        </button>
                    </div>
                `;
                return;
            }
        }

        if (!window.ForceGraph) return;
        if (currentForceGraphInstance) return;

        const cleanData = getCleanGraphData();
        if (!cleanData || !cleanData.nodes || cleanData.nodes.length === 0) return;

        wrapper.innerHTML = '';
        isInitialLayoutFit = true;
        wrapper.addEventListener('pointerdown', (e) => {
            isInitialLayoutFit = false;
            lastPointerDownPos = { x: e.clientX, y: e.clientY, time: Date.now() };
        }, { passive: true });
        wrapper.addEventListener('touchstart', (e) => {
            isInitialLayoutFit = false;
            if (e.touches && e.touches[0]) {
                lastPointerDownPos = { x: e.touches[0].clientX, y: e.touches[0].clientY, time: Date.now() };
            }
        }, { passive: true });
        wrapper.addEventListener('wheel', () => { isInitialLayoutFit = false; }, { passive: true });

    currentForceGraphInstance = ForceGraph()(wrapper)
        .width(width)
        .height(height)
        .backgroundColor(bgColor)
        .nodeId('id')
        .nodeVal('val')
        .nodeLabel(node => `${node.name} (${KG_CATEGORY_NAMES[node.category] || node.category})`)
        .linkDirectionalArrowLength(link => {
            if (kgStructuralLinkBends(link) || link.__structural || link.__ring) return 0;  // без стрелок: дерево и кольцевые связи
            const lKey = link.__key || getGraphLinkKey(link.source, link.target);
            if (nodeClickStep === 2) {
                if (searchBackboneLinkKeys.has(lKey) || searchHighlightLinkKeys.has(lKey)) return 7.5;
                return 4.0;
            }
            if (nodeClickStep === 1) {
                const sId = (typeof link.source === 'object' && link.source !== null) ? String(link.source.id) : String(link.source);
                const tId = (typeof link.target === 'object' && link.target !== null) ? String(link.target.id) : String(link.target);
                if (sId === activeSearchTargetId || tId === activeSearchTargetId) return 7.0;
                return 5.0;
            }
            return 5.5;
        })
        .linkDirectionalArrowRelPos(0.88)
        .linkCurvature(link => link.__curvature || 0)
        .linkLineDash(link => (link.relation === 'prereq' ? [5, 4] : null))
        .linkColor(link => {
            const lKey = link.__key || getGraphLinkKey(link.source, link.target);
            const isHovered = hoveredLink === link || hoveredLinkKeys.has(lKey);
            if (isHovered) {
                return getKgRelationLinkColor(link.relation, true, isDark);
            }
            if (nodeClickStep === 2) {
                if (searchBackboneLinkKeys.has(lKey)) {
                    // Магистраль от истока к понятию (Sky Blue)
                    return isDark ? '#38bdf8' : '#0284c7';
                }
                if (searchHighlightLinkKeys.has(lKey)) {
                    // Исходящая или входящая связь/разграничение: яркий сочный цвет из приложения!
                    return getKgRelationLinkColor(link.relation, true, isDark);
                }
                return isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.05)';
            }
            if (nodeClickStep === 1) {
                // Шаг 1: Фокус на выбранном понятии. Прямые связи подсвечиваются ярче!
                const sId = (typeof link.source === 'object' && link.source !== null) ? String(link.source.id) : String(link.source);
                const tId = (typeof link.target === 'object' && link.target !== null) ? String(link.target.id) : String(link.target);
                if (sId === activeSearchTargetId || tId === activeSearchTargetId) {
                    return getKgRelationLinkColor(link.relation, true, isDark);
                }
                // Фоновые связи сохраняют читаемый цвет с комфортным смягчением
                return getKgRelationLinkColor(link.relation, false, isDark, 0.45);
            }
            if (searchHighlightNodes.size > 0 || activeSelectedLink) {
                if (searchBackboneLinkKeys.has(lKey)) {
                    return isDark ? '#38bdf8' : '#0284c7';
                }
                if (searchHighlightLinkKeys.has(lKey)) {
                    return getKgRelationLinkColor(link.relation, true, isDark);
                }
                return isDark ? 'rgba(255, 255, 255, 0.05)' : 'rgba(0, 0, 0, 0.05)';
            }
            if (hoveredNode || hoveredLink) {
                return isDark ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.08)';
            }
            // Обычное состояние: дерево пути — ярко; связи между ветками — по кнопке «Связи»
            // (в круговой — едва заметными дугами, в слоях — скрыты, чтобы не перечёркивать слои)
            if (!link.__structural) {
                if (kgShowCrossLinks) return getKgRelationLinkColor(link.relation, false, isDark, 0.9);
                if (currentKgLayout === 'radial') return isDark ? 'rgba(255, 255, 255, 0.06)' : 'rgba(0, 0, 0, 0.05)';
                return 'rgba(0, 0, 0, 0)';
            }
            return getKgRelationLinkColor(link.relation, false, isDark);
        })
        .linkWidth(link => {
            const lKey = link.__key || getGraphLinkKey(link.source, link.target);
            const isHovered = hoveredLink === link || hoveredLinkKeys.has(lKey);
            if (isHovered) return 3.0;
            if (nodeClickStep === 2) {
                if (searchBackboneLinkKeys.has(lKey)) return 3.2; // Магистраль от истока
                if (searchHighlightLinkKeys.has(lKey)) return 2.8; // Связи и разграничения
                return 0.5;
            }
            if (nodeClickStep === 1) {
                const sId = (typeof link.source === 'object' && link.source !== null) ? String(link.source.id) : String(link.source);
                const tId = (typeof link.target === 'object' && link.target !== null) ? String(link.target.id) : String(link.target);
                if (sId === activeSearchTargetId || tId === activeSearchTargetId) {
                    return 2.4; // Прямые связи выбранного понятия выделены заметно
                }
                return 0.8;
            }
            if (searchHighlightNodes.size > 0 || activeSelectedLink) {
                if (searchBackboneLinkKeys.has(lKey)) return 3.0;
                if (searchHighlightLinkKeys.has(lKey)) return 2.8;
                return 0.5;
            }
            // Обычное состояние: 1.2px для структурных связей, 1.6px для разграничений и исключений
            const rel = (link.relation || '').toLowerCase();
            if (rel === 'demarcated_from' || rel === 'excludes_application' || rel === 'appealed_to') {
                return 1.6;
            }
            return 1.2;
        })
        .linkDirectionalParticles(() => 0) // Без вырвиглазных бегущих частиц!
        .onRenderFramePre((ctx, globalScale) => {
            drawPathCourse(ctx, globalScale);
            try {
                if (currentForceGraphInstance && typeof currentForceGraphInstance.screen2GraphCoords === 'function') {
                    const pad = 120;
                    const w = currentForceGraphInstance.width ? currentForceGraphInstance.width() : ctx.canvas.width;
                    const h = currentForceGraphInstance.height ? currentForceGraphInstance.height() : ctx.canvas.height;
                    const tl = currentForceGraphInstance.screen2GraphCoords(-pad, -pad);
                    const br = currentForceGraphInstance.screen2GraphCoords(w + pad, h + pad);
                    if (tl && br && Number.isFinite(tl.x) && Number.isFinite(br.x)) {
                        kgViewportBounds.minX = Math.min(tl.x, br.x);
                        kgViewportBounds.maxX = Math.max(tl.x, br.x);
                        kgViewportBounds.minY = Math.min(tl.y, br.y);
                        kgViewportBounds.maxY = Math.max(tl.y, br.y);
                        return;
                    }
                }
                kgViewportBounds.minX = -1e6;
                kgViewportBounds.maxX = 1e6;
                kgViewportBounds.minY = -1e6;
                kgViewportBounds.maxY = 1e6;
            } catch (_) {
                kgViewportBounds.minX = -1e6;
                kgViewportBounds.maxX = 1e6;
                kgViewportBounds.minY = -1e6;
                kgViewportBounds.maxY = 1e6;
            }
        })
        .linkCanvasObjectMode(link => (kgStructuralLinkBends(link) || link.__ring ? 'replace' : 'after'))
        .linkCanvasObject((link, ctx, globalScale) => {
            if (link.__ring) {
                drawRingRoadLink(link, ctx, globalScale);
                return;
            }
            // Линия дерева в слоистых раскладках — ломаная «оргчарта» через изгибы из раскладки
            const bends = kgStructuralLinkBends(link);
            if (bends) {
                if (!link.source || !link.target || !Number.isFinite(link.source.x) || !Number.isFinite(link.target.x)) return;
                const colorFn = currentForceGraphInstance.linkColor();
                const widthFn = currentForceGraphInstance.linkWidth();
                ctx.save();
                ctx.beginPath();
                ctx.moveTo(link.source.x, link.source.y);
                bends.forEach(pt => ctx.lineTo(pt.x, pt.y));
                ctx.lineTo(link.target.x, link.target.y);
                ctx.strokeStyle = typeof colorFn === 'function' ? colorFn(link) : colorFn;
                ctx.lineWidth = (typeof widthFn === 'function' ? widthFn(link) : widthFn) / globalScale;
                if (link.relation === 'prereq') ctx.setLineDash([5 / globalScale, 4 / globalScale]);
                ctx.stroke();
                ctx.restore();
                return;
            }
            try {
                if (!link.source || !link.target) return;
                const sx = Number.isFinite(link.source.x) ? link.source.x : null;
                const sy = Number.isFinite(link.source.y) ? link.source.y : null;
                const tx = Number.isFinite(link.target.x) ? link.target.x : null;
                const ty = Number.isFinite(link.target.y) ? link.target.y : null;
                if (sx === null || sy === null || tx === null || ty === null) return;

                const lKey = link.__key || getGraphLinkKey(link.source, link.target);
                const isHovered = hoveredLink === link || hoveredLinkKeys.has(lKey);
                const isBackbone = searchBackboneLinkKeys.has(lKey);
                const isHighlightEdge = searchHighlightLinkKeys.has(lKey);

                // Подписи отображаются ТОЛЬКО на шаге 2 (подсветка связей и магистрали) или при наведении на ПК
                const shouldShowEdgeLabel = (nodeClickStep === 2 && (isBackbone || isHighlightEdge)) || isHovered;
                if (!shouldShowEdgeLabel) return;

                const relStyle = getKgRelationStyle(link.relation);
                const label = link.label || relStyle.label;
                if (!label) return;

                let midX = (sx + tx) / 2;
                let midY = (sy + ty) / 2;

                const curvature = link.__curvature || 0;
                if (curvature !== 0) {
                    const dx = tx - sx;
                    const dy = ty - sy;
                    midX += -dy * curvature * 0.5;
                    midY += dx * curvature * 0.5;
                }
                if (!Number.isFinite(midX) || !Number.isFinite(midY)) return;

                // Viewport Culling для подписи связи без вызова getTransform()
                if (midX < kgViewportBounds.minX || midX > kgViewportBounds.maxX ||
                    midY < kgViewportBounds.minY || midY > kgViewportBounds.maxY) {
                    return;
                }

                ctx.save();
                if (!isHovered && globalScale < 1.40) {
                    ctx.globalAlpha = Math.min(1.0, Math.max(0.1, (globalScale - 1.25) / 0.15));
                }
                if (searchHighlightNodes.size > 0 && !searchHighlightLinkKeys.has(lKey)) {
                    ctx.globalAlpha = 0.05;
                } else if ((hoveredNode || hoveredLink) && !isHovered) {
                    ctx.globalAlpha = 0.15;
                }

                const fontSize = 9;
                ctx.font = `500 ${fontSize}px Inter, "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, sans-serif`;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';

                // Кэширование измерений текста на ребре для ликвидации просадок FPS (0 повторных measureText в кадре)
                if (link.__cachedLabel !== label || !link.__pillW) {
                    link.__cachedLabel = label;
                    const textWidth = ctx.measureText(label).width;
                    link.__textWidth = textWidth;
                    link.__pillW = textWidth + 9.0;
                    link.__pillH = fontSize + 5.0;
                }
                const pillW = link.__pillW;
                const pillH = link.__pillH;

                // Background chip
                ctx.beginPath();
                const pillR = 3.5;
                if (ctx.roundRect) {
                    ctx.roundRect(midX - pillW / 2, midY - pillH / 2, pillW, pillH, pillR);
                } else {
                    ctx.rect(midX - pillW / 2, midY - pillH / 2, pillW, pillH);
                }
                ctx.fillStyle = isDark ? 'rgba(15, 23, 42, 0.88)' : 'rgba(255, 255, 255, 0.94)';
                ctx.fill();

                // Chip border
                const isEdgeLit = (nodeClickStep === 2 && (isBackbone || isHighlightEdge)) || isHovered;
                ctx.strokeStyle = isEdgeLit
                    ? (isBackbone ? (isDark ? '#38bdf8' : '#0284c7') : (isDark ? (relStyle.color || '#38bdf8') : (relStyle.lightColor || '#0284c7')))
                    : (isDark ? 'rgba(255, 255, 255, 0.22)' : 'rgba(0, 0, 0, 0.15)');
                ctx.lineWidth = isEdgeLit ? 1.6 : 0.8;
                ctx.stroke();

                // Text
                ctx.fillStyle = isDark ? '#f1f5f9' : '#0f172a';
                ctx.fillText(label, midX, midY);

                ctx.restore();
            } catch (_) {
                // Предотвращаем падение всего цикла анимации при специфичных сбоях холста
            }
        })
        .onNodeHover(node => {
            const isTouch = ('ontouchstart' in window) || (navigator.maxTouchPoints > 0) || (window.Telegram && window.Telegram.WebApp);
            if (isTouch) return; // На смартфонах узлы выбираются по клику (onNodeClick), чтобы жесты зума не вызывали ложных срабатываний
            if ((!node && !hoveredNode) || (node && hoveredNode && node.id === hoveredNode.id)) return;
            hoveredNode = node || null;
            hoveredLinkKeys.clear();
            hoveredNeighborNodeIds.clear();

            if (node) {
                wrapper.style.cursor = 'pointer';
                const nId = String(node.id);
                hoveredNeighborNodeIds.add(nId);
                const gData = currentForceGraphInstance.graphData ? currentForceGraphInstance.graphData() : getCleanGraphData();
                if (gData && gData.links) {
                    gData.links.forEach(l => {
                        const sId = String((typeof l.source === 'object' && l.source !== null) ? l.source.id : l.source);
                        const tId = String((typeof l.target === 'object' && l.target !== null) ? l.target.id : l.target);
                        if (sId === nId || tId === nId) {
                            hoveredLinkKeys.add(l.__key || getGraphLinkKey(sId, tId));
                            hoveredNeighborNodeIds.add(sId);
                            hoveredNeighborNodeIds.add(tId);
                        }
                    });
                }
            } else {
                wrapper.style.cursor = null;
            }
            if (currentForceGraphInstance) {
                currentForceGraphInstance.refresh();
            }
        })
        .warmupTicks(0)
        .cooldownTicks(45)
        .d3VelocityDecay(0.45) // Быстрый и плавный разогрев на мобильных устройствах без блокировки UI-потока
        .onDagError(() => false)
        .onEngineStop(() => {
            if (isInitialLayoutFit && currentForceGraphInstance && currentKgView === 'graph') {
                isInitialLayoutFit = false;
                currentForceGraphInstance.zoomToFit(400, 35);
            }
        })
        .onNodeDrag(() => {
            isInitialLayoutFit = false;
        })
        .onNodeDragEnd(() => {
            isInitialLayoutFit = false;
        })
        .nodeCanvasObject((node, ctx, globalScale) => {
            try {
                if (!node || !Number.isFinite(node.x) || !Number.isFinite(node.y)) return;

                // 0. Viewport Culling: мгновенно отсекаем узлы за пределами видимого экрана (0 DOMMatrix allocations)
                if (node.x < kgViewportBounds.minX || node.x > kgViewportBounds.maxX ||
                    node.y < kgViewportBounds.minY || node.y > kgViewportBounds.maxY) {
                    return;
                }

                const hasActiveSelection = searchHighlightNodes.size > 0;
                const isHighlighted = !hasActiveSelection || searchHighlightNodes.has(String(node.id));
                const isTarget = activeSearchTargetId === String(node.id);
                const isBackboneNode = searchBackboneNodes.has(String(node.id));
                const label = node.name || node.id;
                const baseR = Math.max(3.0, (node.val || 4) * 0.75);
                const radius = baseR * (isTarget ? 1.4 : (isBackboneNode ? 1.15 : 1.0));
                const currentDark = isDark;

                ctx.save();
            const isHoverActive = Boolean(hoveredNode || hoveredLink);
            const isHovered = (hoveredNode && String(hoveredNode.id) === String(node.id));
            const isHoverNeighbor = hoveredNeighborNodeIds.has(String(node.id));

            if (nodeClickStep === 2) {
                if (!isHighlighted) {
                    ctx.globalAlpha = 0.08;
                } else if (isHoverActive && !isHoverNeighbor) {
                    ctx.globalAlpha = 0.25;
                }
            } else if (nodeClickStep === 1) {
                if (!isTarget) {
                    ctx.globalAlpha = 0.65;
                }
            } else if (hasActiveSelection) {
                if (!isHighlighted) {
                    ctx.globalAlpha = 0.08;
                } else if (isHoverActive && !isHoverNeighbor) {
                    ctx.globalAlpha = 0.25;
                }
            } else if (isHoverActive && !isHoverNeighbor) {
                ctx.globalAlpha = 0.20;
            }

            // 1. Цвет узлов в зависимости от роли, фокуса и FSRS-статуса изучения
            let nodeFill;
            let nodeStroke;
            let strokeWidth = 1.0;
            const cardState = node.card_state !== undefined ? node.card_state : (node.is_learned ? 2 : 0);

            if (isTarget) {
                // Целевой узел: чистый высокий контраст с акцентным фокусом
                nodeFill = currentDark ? '#ffffff' : '#0f172a';
                nodeStroke = currentDark ? '#38bdf8' : '#0284c7';
                strokeWidth = 2.4;

                // Акцентный ореол активного фокуса
                ctx.beginPath();
                ctx.arc(node.x, node.y, radius + 4.5, 0, 2 * Math.PI, false);
                ctx.strokeStyle = currentDark ? '#38bdf8' : '#0284c7';
                ctx.lineWidth = 2.0;
                ctx.stroke();

            } else if (isBackboneNode) {
                // Узлы магистрали (Корень и Родительский институт)
                nodeFill = currentDark ? '#e2e8f0' : '#334155';
                nodeStroke = currentDark ? 'rgba(56, 189, 248, 0.6)' : 'rgba(2, 132, 199, 0.6)';
                strokeWidth = 1.6;

            } else if (hasActiveSelection && isHighlighted) {
                // Соседние понятия ветви
                const catColor = getKgNodeColor(node.category);
                if (cardState === 2) {
                    nodeFill = currentDark ? '#059669' : '#10b981';
                    nodeStroke = currentDark ? '#34d399' : '#047857';
                } else if (cardState === 1 || cardState === 3) {
                    nodeFill = currentDark ? '#1d4ed8' : '#3b82f6';
                    nodeStroke = currentDark ? '#60a5fa' : '#1d4ed8';
                } else {
                    nodeFill = currentDark ? '#181e29' : '#f8fafc';
                    nodeStroke = catColor;
                }
                strokeWidth = 1.5;

            } else {
                // ОБЫЧНОЕ СОСТОЯНИЕ
                if (node.level === 0) {
                    nodeFill = currentDark ? '#ffffff' : '#0f172a';
                    nodeStroke = currentDark ? '#38bdf8' : '#0284c7';
                    strokeWidth = 2.4;
                } else if (node.level === 1) {
                    if (cardState === 2) {
                        nodeFill = currentDark ? '#065f46' : '#059669';
                        nodeStroke = '#10b981';
                        strokeWidth = 2.0;
                    } else if (cardState === 1 || cardState === 3) {
                        nodeFill = currentDark ? '#1e3a8a' : '#2563eb';
                        nodeStroke = '#3b82f6';
                        strokeWidth = 2.0;
                    } else {
                        // Неизученный институт: золотисто-янтарный цвет категории authority (как в приложении)
                        nodeFill = currentDark ? '#292524' : '#fef3c7';
                        nodeStroke = currentDark ? '#fbbf24' : '#d97706';
                        strokeWidth = 1.8;
                    }
                } else {
                    const catColor = getKgNodeColor(node.category);
                    if (cardState === 2) {
                        // Изучено (Mastered): выразительный изумрудный цвет
                        nodeFill = currentDark ? '#059669' : '#10b981';
                        nodeStroke = currentDark ? '#34d399' : '#047857';
                        strokeWidth = 1.4;
                    } else if (cardState === 1 || cardState === 3) {
                        // В процессе изучения: выразительный синий цвет
                        nodeFill = currentDark ? '#1d4ed8' : '#3b82f6';
                        nodeStroke = currentDark ? '#60a5fa' : '#1d4ed8';
                        strokeWidth = 1.4;
                    } else {
                        // Новое понятие: темная сердцевина и окантовка цвета категории из приложения!
                        nodeFill = currentDark ? '#181e29' : '#f8fafc';
                        nodeStroke = catColor;
                        strokeWidth = 1.4;
                    }
                }
            }

            // «Путь знаний»: цвет узла определяется его статусом, закрытые узлы — бледные силуэты
            if (node.status && !isTarget) {
                const statusColor = getKgNodeColor(node.status);
                if (node.status === 'locked') {
                    nodeFill = currentDark ? '#1e293b' : '#f1f5f9';
                    nodeStroke = currentDark ? 'rgba(148, 163, 184, 0.45)' : 'rgba(100, 116, 139, 0.45)';
                    strokeWidth = 1.0;
                } else {
                    nodeFill = statusColor;
                    nodeStroke = currentDark ? '#ffffff' : '#0f172a';
                    strokeWidth = node.status === 'open' ? 2.2 : 1.4;
                }
            }

            // 2. Тело узла
            ctx.beginPath();
            ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI, false);
            ctx.fillStyle = nodeFill;
            ctx.fill();

            // 3. Окантовка узла
            ctx.lineWidth = strokeWidth;
            ctx.strokeStyle = nodeStroke;
            ctx.stroke();

            // 3.1. Индикация изученности карточки (Learning Status Ring & Glow)
            if (cardState === 2) {
                // Изучено / Mastered: Изумрудное кольцо статуса
                ctx.beginPath();
                ctx.arc(node.x, node.y, radius + (node.level <= 1 ? 3.2 : 2.2), 0, 2 * Math.PI, false);
                ctx.strokeStyle = '#10b981';
                ctx.lineWidth = node.level <= 1 ? 2.2 : 1.8;
                ctx.stroke();
            } else if (cardState === 1 || cardState === 3) {
                // В процессе изучения: Синее кольцо статуса
                ctx.beginPath();
                ctx.arc(node.x, node.y, radius + (node.level <= 1 ? 3.2 : 2.2), 0, 2 * Math.PI, false);
                ctx.strokeStyle = '#3b82f6';
                ctx.lineWidth = node.level <= 1 ? 2.2 : 1.8;
                ctx.stroke();
            }

            // 3.2. Акцентный ореол наведенного узла
            if (!isTarget && isHovered) {
                ctx.beginPath();
                ctx.arc(node.x, node.y, radius + 3.8, 0, 2 * Math.PI, false);
                ctx.strokeStyle = currentDark ? '#38bdf8' : '#0284c7';
                ctx.lineWidth = 1.8;
                ctx.stroke();
            }

            // 3.3. Акцентный ореол ядра курса (корень графа)
            if (node.level === 0 && !isTarget) {
                ctx.beginPath();
                ctx.arc(node.x, node.y, radius + 4.2, 0, 2 * Math.PI, false);
                ctx.strokeStyle = currentDark ? 'rgba(56, 189, 248, 0.40)' : 'rgba(2, 132, 199, 0.35)';
                ctx.lineWidth = 1.6;
                ctx.stroke();
            }

            // 3.4. Карточки узла — спутники по кругу (без физики и линий); закрашены уже отвеченные
            if (node.cards_total && globalScale >= 0.45) {
                const total = Math.min(node.cards_total, 16);
                const answered = Math.round((node.cards_answered || 0) * total / node.cards_total);
                const orbit = radius + 4.5;
                const dotR = Math.max(0.9, Math.min(1.6, radius * 0.22));
                for (let i = 0; i < total; i++) {
                    const a = (2 * Math.PI * i) / total - Math.PI / 2;
                    ctx.beginPath();
                    ctx.arc(node.x + Math.cos(a) * orbit, node.y + Math.sin(a) * orbit, dotR, 0, 2 * Math.PI, false);
                    ctx.fillStyle = i < answered
                        ? '#10b981'
                        : (node.status === 'locked'
                            ? (currentDark ? 'rgba(148, 163, 184, 0.35)' : 'rgba(100, 116, 139, 0.35)')
                            : (currentDark ? 'rgba(148, 163, 184, 0.8)' : 'rgba(71, 85, 105, 0.75)'));
                    ctx.fill();
                }
            }

            // 4. Текстовая плашка-метка узла (Obsidian-Style Semantic Zoom LOD)
            let shouldShowLabel = false;
            let labelAlpha = 1.0;
            if (isTarget) {
                // Выделенный целевой узел виден всегда
                shouldShowLabel = true;
                labelAlpha = 1.0;
            } else if (nodeClickStep === 2 && (isBackboneNode || (hasActiveSelection && isHighlighted))) {
                // На шаге 2 магистраль и связанные понятия видны всегда
                shouldShowLabel = true;
                labelAlpha = 1.0;
            } else if (isHovered || isHoverNeighbor) {
                shouldShowLabel = true;
                labelAlpha = 1.0;
            } else if (hasActiveSelection && isHighlighted && nodeClickStep !== 1) {
                // Соседние понятия активной ветки показываются при комфортном масштабе
                shouldShowLabel = globalScale >= 0.70;
                labelAlpha = Math.min(1.0, Math.max(0.0, (globalScale - 0.65) / 0.15));
            } else if (node.level === 0) {
                // Заголовок курса (корень) виден всегда
                shouldShowLabel = true;
                labelAlpha = 1.0;
            } else if (node.level === 1) {
                // Темы подписаны всегда: вместе с основами это скелет предмета
                shouldShowLabel = true;
                labelAlpha = 1.0;
            } else {
                // Понятия появляются при глубоком приближении с плавным переходом 1.80 - 2.10
                shouldShowLabel = globalScale >= 1.80;
                labelAlpha = Math.min(1.0, Math.max(0.0, (globalScale - 1.80) / 0.30));
            }

            if (isHighlighted && shouldShowLabel && labelAlpha > 0.02) {
                const prevLabelAlpha = ctx.globalAlpha;
                ctx.globalAlpha = prevLabelAlpha * labelAlpha;

                // Фиксированный размер шрифта в мировых координатах (без раздувания при отдалении!)
                const baseFontSize = isTarget ? 11 : (node.level === 0 ? 12 : (isBackboneNode || node.level === 1 ? 9.5 : 8));
                ctx.font = `${isTarget || isBackboneNode || node.level <= 1 ? '700' : '500'} ${baseFontSize}px "Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, sans-serif`;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';

                // Кэширование разбиения строк и измерений ширины для предотвращения просадки FPS
                if (!node.__lines || node.__lastLabel !== label) {
                    node.__lines = wrapNodeText(label, node.level === 0 ? 22 : 16);
                    node.__lastLabel = label;
                    node.__lineMetrics = null;
                }

                const lineHeight = baseFontSize + 3.0;
                const startY = node.y + radius + 4.5 + (lineHeight / 2);

                if (!node.__lineMetrics || node.__lineMetricsSize !== baseFontSize) {
                    const padX = 4.0;
                    const padY = 2.5;
                    node.__lineMetricsSize = baseFontSize;
                    node.__lineMetrics = node.__lines.map(line => {
                        const textW = ctx.measureText(line).width;
                        return {
                            pillW: textW + (padX * 2),
                            pillH: baseFontSize + (padY * 2)
                        };
                    });
                }

                node.__lines.forEach((line, i) => {
                    const lineY = startY + (i * lineHeight);
                    const metrics = node.__lineMetrics[i] || { pillW: 40, pillH: baseFontSize + 5.0 };
                    const pillW = metrics.pillW;
                    const pillH = metrics.pillH;

                    // Фон плашки
                    if (isTarget) {
                        ctx.fillStyle = currentDark ? '#ffffff' : '#0f172a';
                    } else if (isBackboneNode) {
                        ctx.fillStyle = currentDark ? 'rgba(255, 255, 255, 0.20)' : 'rgba(15, 23, 42, 0.16)';
                    } else {
                        ctx.fillStyle = currentDark ? 'rgba(18, 18, 18, 0.88)' : 'rgba(255, 255, 255, 0.94)';
                    }

                    ctx.beginPath();
                    const pillRadius = 3.0;
                    if (ctx.roundRect) {
                        ctx.roundRect(node.x - pillW / 2, lineY - pillH / 2, pillW, pillH, pillRadius);
                    } else {
                        ctx.rect(node.x - pillW / 2, lineY - pillH / 2, pillW, pillH);
                    }
                    ctx.fill();

                    // Рамка плашки
                    if (isTarget) {
                        ctx.strokeStyle = currentDark ? '#e2e8f0' : '#1e293b';
                        ctx.lineWidth = 1.0;
                    } else if (isBackboneNode) {
                        ctx.strokeStyle = currentDark ? 'rgba(255, 255, 255, 0.35)' : 'rgba(15, 23, 42, 0.25)';
                        ctx.lineWidth = 0.8;
                    } else {
                        ctx.strokeStyle = currentDark ? 'rgba(255, 255, 255, 0.12)' : 'rgba(0, 0, 0, 0.08)';
                        ctx.lineWidth = 0.6;
                    }
                    ctx.stroke();

                    // Текст плашки
                    if (isTarget) {
                        ctx.fillStyle = currentDark ? '#000000' : '#ffffff';
                    } else if (isBackboneNode) {
                        ctx.fillStyle = currentDark ? '#ffffff' : '#0f172a';
                    } else {
                        ctx.fillStyle = currentDark ? '#f8fafc' : '#0f172a';
                    }
                    ctx.fillText(line, node.x, lineY);
                });

                ctx.globalAlpha = prevLabelAlpha;
            }

            ctx.restore();
            } catch (_) {
                // Предотвращаем срыв отрисовки холста
            }
        })
        .nodePointerAreaPaint((node, color, ctx) => {
            try {
                if (!node || !Number.isFinite(node.x) || !Number.isFinite(node.y)) return;
                if (node.x < kgViewportBounds.minX || node.x > kgViewportBounds.maxX ||
                    node.y < kgViewportBounds.minY || node.y > kgViewportBounds.maxY) {
                    return;
                }

                const baseR = Math.max(3.0, (node.val || 4) * 0.75);
                const radius = Math.max(26, baseR + 10);
                ctx.fillStyle = color;
                ctx.beginPath();
                ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI, false);
                ctx.fill();
            } catch (_) {
                // Предотвращаем падение при сбоях сенсорного хит-теста
            }
        })
        .onNodeClick(node => {
            if (!node || !node.id) return;
            const now = Date.now();
            if (now - (window.__lastKgNodeClickTime || 0) < 250) return;
            window.__lastKgNodeClickTime = now;
            focusNodeInGraph(node.id);
        })
        .onBackgroundClick(event => {
            const resBox = document.getElementById('kg-search-results');
            if (resBox) resBox.classList.add('hidden');

            hoveredNode = null;
            hoveredLink = null;
            hoveredLinkKeys.clear();
            hoveredNeighborNodeIds.clear();

            // Защита от случайного сброса при перетаскивании (pan/drag) холста
            if (lastPointerDownPos && event) {
                const dx = Math.abs(event.clientX - lastPointerDownPos.x);
                const dy = Math.abs(event.clientY - lastPointerDownPos.y);
                const dt = Date.now() - (lastPointerDownPos.time || 0);
                if (dx > 7 || dy > 7 || dt > 650) {
                    return; // Пользователь панорамировал или зумил карту, не сбрасываем!
                }
            }

            // Закрываем шторку узла при клике на свободный фон
            const drawer = document.getElementById('kg-node-drawer');
            const isDrawerOpen = drawer && !drawer.classList.contains('hidden');
            if (isDrawerOpen) {
                closeKgNodeDrawer();
            }

            // Сброс выделения с сохранением зума и координат камеры (preserveCamera: true)
            if (activeSearchTargetId || activeSelectedLink || searchHighlightNodes.size > 0 || nodeClickStep > 0) {
                window.clearKgSearch(true);
            } else if (currentForceGraphInstance) {
                currentForceGraphInstance.refresh();
            }
        });

    setGraphLayout(currentKgLayout || 'radial');

    // Resize on window resize (remove previous listener to prevent memory leak and duplicate events)
    if (kgResizeHandler) {
        window.removeEventListener('resize', kgResizeHandler);
    }
    kgResizeHandler = () => {
        if (currentForceGraphInstance && currentKgView === 'graph') {
            const w = wrapper.clientWidth || (wrapper.parentElement ? wrapper.parentElement.clientWidth : 0) || window.innerWidth;
            const h = wrapper.clientHeight || (wrapper.parentElement ? wrapper.parentElement.clientHeight : 0) || (window.innerHeight - 150);
            currentForceGraphInstance.width(w).height(h);
        }
    };
    window.addEventListener('resize', kgResizeHandler);
    } finally {
        isInitializingGraph = false;
    }
};

window.zoomGraph = function(factor) {
    if (!currentForceGraphInstance) return;
    const currentZoom = currentForceGraphInstance.zoom();
    currentForceGraphInstance.zoom(currentZoom * factor, 300);
};

window.resetGraphZoom = function() {
    if (!currentForceGraphInstance) return;
    currentForceGraphInstance.zoomToFit(400, 40);
    triggerHaptic('light');
};

document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const modal = document.getElementById('knowledge-graph-modal');
        if (modal && !modal.classList.contains('hidden') && currentKgView === 'graph') {
            if (activeSearchTargetId || activeSelectedLink || searchHighlightNodes.size > 0) {
                window.clearKgSearch(true);
            }
        }
    }
});

window.collapseKgNodeDrawer = function() {
    isDrawerCollapsed = true;
    const body = document.getElementById('kg-drawer-body');
    const toggleIcon = document.getElementById('kg-drawer-toggle-icon');
    if (body) {
        body.classList.add('hidden');
    }
    if (toggleIcon) {
        toggleIcon.textContent = 'expand_less';
    }
};

window.expandKgNodeDrawer = function() {
    isDrawerCollapsed = false;
    const body = document.getElementById('kg-drawer-body');
    const toggleIcon = document.getElementById('kg-drawer-toggle-icon');
    if (body) {
        body.classList.remove('hidden');
    }
    if (toggleIcon) {
        toggleIcon.textContent = 'expand_more';
    }
};

window.toggleKgNodeDrawerCollapse = function() {
    if (isDrawerCollapsed) {
        window.expandKgNodeDrawer();
    } else {
        window.collapseKgNodeDrawer();
    }
    triggerHaptic('light');
};

let kgDrawerGesturesInitialized = false;

function initKgDrawerGestures() {
    if (kgDrawerGesturesInitialized) return;
    kgDrawerGesturesInitialized = true;

    const drawer = document.getElementById('kg-node-drawer');
    const handle = document.getElementById('kg-drawer-handle');
    const header = document.getElementById('kg-drawer-header');
    if (!drawer) return;

    if (header) {
        header.addEventListener('click', (e) => {
            if (e.target.closest('button')) return;
            window.toggleKgNodeDrawerCollapse();
        });
    }

    let startY = 0;
    let startX = 0;
    let startTime = 0;

    const onTouchStart = (e) => {
        if (!e.touches || e.touches.length === 0) return;
        startY = e.touches[0].clientY;
        startX = e.touches[0].clientX;
        startTime = Date.now();
    };

    const onTouchEnd = (e) => {
        if (!e.changedTouches || e.changedTouches.length === 0) return;
        const endY = e.changedTouches[0].clientY;
        const endX = e.changedTouches[0].clientX;
        const deltaY = endY - startY;
        const deltaX = Math.abs(endX - startX);
        const dt = Date.now() - startTime;

        if (deltaX > Math.abs(deltaY) || dt > 800) return;

        if (deltaY > 35) {
            // Свайп вниз
            if (!isDrawerCollapsed) {
                window.collapseKgNodeDrawer();
                triggerHaptic('light');
            } else {
                window.clearKgSearch(true);
                window.closeKgNodeDrawer();
                triggerHaptic('medium');
            }
        } else if (deltaY < -35) {
            // Свайп вверх
            if (isDrawerCollapsed) {
                window.expandKgNodeDrawer();
                triggerHaptic('light');
            }
        }
    };

    if (handle) {
        handle.addEventListener('touchstart', onTouchStart, { passive: true });
        handle.addEventListener('touchend', onTouchEnd, { passive: true });
    }
    if (header) {
        header.addEventListener('touchstart', onTouchStart, { passive: true });
        header.addEventListener('touchend', onTouchEnd, { passive: true });
    }
}

window.showKgNodeDrawer = function(node, startCollapsed = false) {
    const drawer = document.getElementById('kg-node-drawer');
    const badge = document.getElementById('kg-drawer-badge');
    const statusBadge = document.getElementById('kg-drawer-status-badge');
    const title = document.getElementById('kg-drawer-title');
    const summary = document.getElementById('kg-drawer-summary');
    const linksContainer = document.getElementById('kg-drawer-links');
    const linksSection = document.getElementById('kg-drawer-links-section');
    const linksCountEl = document.getElementById('kg-drawer-links-count');
    const linksFilterEl = document.getElementById('kg-drawer-links-filter');
    const cardBtn = document.getElementById('kg-drawer-btn-card');

    if (!drawer) return;
    currentKgDrawerNode = node;

    const cat = node.category || 'authority';
    if (badge) {
        badge.className = `px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase shrink-0 badge-${cat}`;
        badge.textContent = KG_CATEGORY_NAMES[cat] || cat.toUpperCase();
    }

    if (statusBadge) {
        const cardState = node.card_state !== undefined ? node.card_state : (node.is_learned ? 2 : 0);
        if (cardState === 2) {
            statusBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase shrink-0 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20';
            statusBadge.textContent = 'Изучено';
            statusBadge.classList.remove('hidden');
        } else if (cardState === 1 || cardState === 3) {
            statusBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase shrink-0 bg-blue-500/10 text-blue-600 dark:text-blue-400 border border-blue-500/20';
            statusBadge.textContent = 'В процессе';
            statusBadge.classList.remove('hidden');
        } else if (node.total_leaves) {
            statusBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase shrink-0 bg-sky-500/10 text-sky-600 dark:text-sky-400 border border-sky-500/20';
            statusBadge.textContent = `Прогресс ${node.learned_count || 0}/${node.total_leaves}`;
            statusBadge.classList.remove('hidden');
        } else if (node.level > 0) {
            statusBadge.className = 'px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase shrink-0 bg-neutral-500/10 text-neutral-500 border border-neutral-500/20';
            statusBadge.textContent = 'Новое';
            statusBadge.classList.remove('hidden');
        } else {
            statusBadge.classList.add('hidden');
        }
    }

    renderPathDrawerState(node);

    if (cardBtn) {
        if (node.card_id) {
            cardBtn.classList.remove('hidden');
        } else {
            cardBtn.classList.add('hidden');
        }
    }

    if (title) title.textContent = node.name || node.id;
    if (summary) summary.textContent = node.summary || 'Детальное описание и законодательное основание отсутствуют.';

    // Рендер связей и разграничений
    if (linksContainer && currentKgGraphData) {
        linksContainer.innerHTML = '';
        const allEdges = currentKgGraphData ? (currentKgGraphData.edges || currentKgGraphData.links || []) : [];
        const connectedEdges = allEdges.filter(e => {
            const s = (typeof e.source === 'object' && e.source !== null) ? String(e.source.id) : String(e.source);
            const t = (typeof e.target === 'object' && e.target !== null) ? String(e.target.id) : String(e.target);
            return s === String(node.id) || t === String(node.id);
        });

        if (linksCountEl) linksCountEl.textContent = connectedEdges.length;

        if (connectedEdges.length > 0) {
            if (linksSection) linksSection.classList.remove('hidden');

            // Подсчет связей по группам
            const groups = {
                'all': connectedEdges.length,
                'subject_to_jurisdiction': connectedEdges.filter(e => (e.relation || 'subject_to_jurisdiction') === 'subject_to_jurisdiction').length,
                'demarcated_from': connectedEdges.filter(e => e.relation === 'demarcated_from').length,
                'other': connectedEdges.filter(e => e.relation !== 'subject_to_jurisdiction' && e.relation !== 'demarcated_from').length
            };

            // Если связей больше 8 (как у корня, где их 50+), отображаем табы фильтрации
            if (linksFilterEl) {
                if (connectedEdges.length > 8) {
                    linksFilterEl.innerHTML = '';
                    const tabDefs = [
                        { key: 'all', label: `Все (${groups.all})` },
                        { key: 'subject_to_jurisdiction', label: `Ветви (${groups.subject_to_jurisdiction})` }
                    ];
                    if (groups.demarcated_from > 0) tabDefs.push({ key: 'demarcated_from', label: `Разграничения (${groups.demarcated_from})` });
                    if (groups.other > 0) tabDefs.push({ key: 'other', label: `Иные (${groups.other})` });

                    tabDefs.forEach(td => {
                        const btn = document.createElement('button');
                        const isActive = currentLinksFilter === td.key;
                        btn.className = `px-1.5 py-0.5 rounded text-[9px] font-bold uppercase transition-all cursor-pointer ${
                            isActive ? 'bg-primary text-on-primary' : 'text-neutral-500 hover:text-primary bg-neutral-100 dark:bg-neutral-800'
                        }`;
                        btn.textContent = td.label;
                        btn.onclick = (ev) => {
                            ev.stopPropagation();
                            currentLinksFilter = td.key;
                            showKgNodeDrawer(node, isDrawerCollapsed);
                        };
                        linksFilterEl.appendChild(btn);
                    });
                    linksFilterEl.classList.remove('hidden');
                } else {
                    linksFilterEl.innerHTML = '';
                    linksFilterEl.classList.add('hidden');
                    currentLinksFilter = 'all';
                }
            }

            // Фильтрация связей в соответствии с выбранным табом
            const displayedEdges = connectedEdges.filter(e => {
                if (currentLinksFilter === 'all') return true;
                const rel = e.relation || 'subject_to_jurisdiction';
                if (currentLinksFilter === 'other') return rel !== 'subject_to_jurisdiction' && rel !== 'demarcated_from';
                return rel === currentLinksFilter;
            });

            displayedEdges.forEach(e => {
                const s = (typeof e.source === 'object' && e.source !== null) ? String(e.source.id) : String(e.source);
                const t = (typeof e.target === 'object' && e.target !== null) ? String(e.target.id) : String(e.target);
                const isOut = s === String(node.id);
                const otherId = isOut ? t : s;
                const otherNode = (currentKgGraphData.nodes || []).find(n => String(n.id) === String(otherId));
                const otherName = otherNode ? (otherNode.name || otherNode.id) : otherId;
                const otherCat = otherNode ? (otherNode.category || 'default') : 'default';
                const otherCatColor = getKgNodeColor(otherCat);
                const relationType = e.relation || 'subject_to_jurisdiction';
                const relStyle = getKgRelationStyle(relationType);
                const relationLabel = e.label || relStyle.label;

                const chip = document.createElement('div');
                chip.className = getKgRelationChipClass(relationType);
                chip.innerHTML = `
                    <span class="w-2 h-2 rounded-full shrink-0" style="background-color: ${otherCatColor};" title="${KG_CATEGORY_NAMES[otherCat] || otherCat}"></span>
                    <span class="rel-label font-bold inline-flex items-center gap-0.5">
                        <span class="material-symbols-outlined text-[13px]">${relStyle.icon}</span> ${escapeHTML(relationLabel)}:
                    </span>
                    <span class="font-medium text-neutral-900 dark:text-neutral-100 truncate max-w-[200px]">${escapeHTML(otherName)}</span>
                `;
                chip.onclick = () => {
                    if (otherNode) {
                        focusNodeInGraph(otherNode.id);
                    }
                };
                linksContainer.appendChild(chip);
            });
        } else {
            if (linksSection) linksSection.classList.add('hidden');
        }
    }

    initKgDrawerGestures();

    if (startCollapsed) {
        window.collapseKgNodeDrawer();
    } else {
        window.expandKgNodeDrawer();
    }

    if (drawer && !drawer.__touchIsolated) {
        drawer.__touchIsolated = true;
        drawer.addEventListener('touchstart', e => e.stopPropagation(), { passive: true });
        drawer.addEventListener('touchmove', e => e.stopPropagation(), { passive: true });
        drawer.addEventListener('pointerdown', e => e.stopPropagation(), { passive: true });
    }

    drawer.classList.remove('hidden');
};

let currentKgDrawerNode = null;

function renderPathDrawerState(node) {
    const lessonBtn = document.getElementById('kg-drawer-btn-lesson');
    const lessonLabel = document.getElementById('kg-drawer-btn-lesson-label');
    const hint = document.getElementById('kg-drawer-path-hint');
    const statusBadge = document.getElementById('kg-drawer-status-badge');
    if (!node.status) {
        if (lessonBtn) lessonBtn.classList.add('hidden');
        if (hint) hint.classList.add('hidden');
        return;
    }
    const tierName = PATH_TIER_NAMES[node.tier] || '';
    if (statusBadge) {
        statusBadge.classList.remove('hidden');
        statusBadge.className = `px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase shrink-0 badge-${node.status}`;
        statusBadge.textContent = node.cards_total
            ? `${tierName} · ${node.cards_answered}/${node.cards_total}`
            : tierName;
    }
    if (hint) {
        if (node.status === 'locked') {
            const byKey = new Map((currentKgGraphData.nodes || []).map(n => [n.id, n]));
            const missing = (node.prereq_keys || [])
                .map(k => byKey.get(k))
                .filter(n => n && n.status !== 'mastered')
                .map(n => `«${n.name}»`);
            hint.textContent = missing.length ? `Откроется после: ${missing.join(', ')}` : 'Пока закрыто';
            hint.classList.remove('hidden');
        } else if (node.status === 'lesson_done') {
            hint.textContent = 'Урок пройден. Карточки темы уже в тренировке.';
            hint.classList.remove('hidden');
        } else {
            hint.classList.add('hidden');
        }
    }
    if (lessonBtn) {
        const available = node.status !== 'locked' && node.lesson_status === 'ready';
        lessonBtn.classList.toggle('hidden', !available);
        if (lessonLabel) lessonLabel.textContent = node.status === 'open' ? 'Начать урок' : 'Повторить урок';
    }
}

window.openLessonForKgNode = function() {
    if (!currentKgDrawerNode || !currentKgDrawerNode.db_id) return;
    if (window.openLesson) {
        window.openLesson(currentKgDrawerNode.db_id);
    }
};

window.closeKgNodeDrawer = function() {
    const drawer = document.getElementById('kg-node-drawer');
    if (drawer) drawer.classList.add('hidden');
    isDrawerCollapsed = false;
    currentKgDrawerNode = null;
};

window.openPracticeForKgNode = function() {
    const sub = currentKgSubject || (typeof getActiveDeckSubject === 'function' ? getActiveDeckSubject() : 'all');
    if (window.openPracticeModal) {
        window.openPracticeModal(sub);
    }
};

window.openCardForKgNode = function() {
    if (!currentKgDrawerNode || !currentKgDrawerNode.card_id) return;
    if (window.requestEditCard) {
        window.requestEditCard(currentKgDrawerNode.card_id);
    }
};




// ============================================================================
// РАСКЛАДКА «ПУТИ ЗНАНИЙ»: СТРУКТУРНОЕ ДЕРЕВО → КООРДИНАТЫ
// Круговая (кольца от центра предмета), «сверху вниз» и «слева направо» (слои).
// Чистые функции без DOM: проверяются автотестом через node (tests/js/path_layout_check.js).
//
// Дерево: корень — предмет, его дети — основы; у остальных узлов ровно один структурный
// родитель (родитель из карты или первый пререквизит ярусом выше). Слой = глубина в дереве,
// поэтому подтема, привязанная прямо к основе, стоит под ней и её линия не перескакивает слои.
// Линии дерева не пересекаются по построению: каждое поддерево занимает свою полосу (сектор).
// ============================================================================

const PATH_COURSE_ID = '__course__';

const PATH_LAYOUT_CONFIG = {
    vertical: {
        depthGap: 150,          // расстояние между слоями
        siblingGap: 22,         // зазор между соседними поддеревьями
        columnThreshold: 4,     // больше стольких листьев у узла — складываем их в столбик
        columnMaxRows: 8,
        columnWidth: 130,
        rowGap: 50,
        leafOffset: 55,         // сдвиг листа вправо от вертикальной «шины» столбика
        rowSpacing: 1.1,        // зазор между рядами при переносе (в долях depthGap)
        targetAspect: 1.3       // желаемое отношение ширины к высоте схемы при переносе рядами
    },
    horizontal: {
        depthGap: 240,
        siblingGap: 10,
        nodeBreadth: 46,        // по вертикали узел с подписью занимает ~46px
        rowSpacing: 0.6,        // зазор между колонками при переносе (в долях depthGap)
        targetAspect: 0.8       // поперечная ось здесь вертикальная: колонки выше, чем шире
    },
    radial: {
        firstRingGap: 220,
        ringGaps: [160, 120, 100],
        minRootArc: 170,        // минимальная дуга между основами на ободе
        minArc: 125,            // минимальная дуга между узлами второго кольца (подписи тем не слипаются)
        minLeafArc: 34,
        minCoreWeight: 2.5,     // минимальный «вес» сектора основы/темы
        leafSubRing: 55         // второй «подкольцевой» ряд для плотных вееров листьев
    }
};

function pathLabelWidth(node) {
    const maxChars = node && node.tier === 0 ? 22 : 16;
    const len = Math.min(String((node && node.name) || '').length, maxChars);
    return Math.max(70, len * 6.4 + 16);
}

// Структурный родитель каждого узла: родитель из карты → первый пререквизит ярусом выше → предмет.
// Циклы разрываются (узел уходит к предмету).
function buildPathTree(nodes) {
    const byId = new Map(nodes.map(n => [String(n.id), n]));
    const parent = new Map();
    nodes.forEach(n => {
        const id = String(n.id);
        let p = PATH_COURSE_ID;
        if (n.tier > 0) {
            const mapParent = n.parent_id !== undefined && n.parent_id !== null ? String(n.parent_id) : '';
            if (mapParent && mapParent !== id && byId.has(mapParent)) {
                p = mapParent;
            } else {
                const prereqs = (n.prereq_keys || [])
                    .map(k => byId.get(String(k)))
                    .filter(x => x && String(x.id) !== id && x.tier < n.tier);
                const primary = prereqs.find(x => x.tier === n.tier - 1) || prereqs.sort((a, b) => b.tier - a.tier)[0];
                if (primary) p = String(primary.id);
            }
        }
        parent.set(id, p);
    });
    nodes.forEach(n => {
        const id = String(n.id);
        const seen = new Set([id]);
        let cur = parent.get(id);
        while (cur && cur !== PATH_COURSE_ID) {
            if (seen.has(cur)) {
                parent.set(id, PATH_COURSE_ID);
                break;
            }
            seen.add(cur);
            cur = parent.get(cur);
        }
    });

    const children = new Map([[PATH_COURSE_ID, []]]);
    nodes.forEach(n => {
        const p = parent.get(String(n.id));
        if (!children.has(p)) children.set(p, []);
        children.get(p).push(String(n.id));
    });

    const depth = new Map([[PATH_COURSE_ID, 0]]);
    const queue = [PATH_COURSE_ID];
    while (queue.length) {
        const u = queue.shift();
        (children.get(u) || []).forEach(c => {
            depth.set(c, depth.get(u) + 1);
            queue.push(c);
        });
    }
    return { byId, parent, children, depth };
}

// Порядок детей: связанные между собой ветки (по смысловым связям) ставим рядом —
// межветочные связи становятся короче и реже пересекаются.
function orderPathChildren(tree, edges) {
    const topUnder = (id, parentId) => {
        let cur = id;
        let guard = 0;
        while (cur && tree.parent.get(cur) !== parentId && guard++ < 64) {
            cur = tree.parent.get(cur);
            if (!cur || cur === PATH_COURSE_ID) return null;
        }
        return tree.parent.get(cur) === parentId ? cur : null;
    };
    tree.children.forEach((kids, parentId) => {
        if (kids.length < 3) return;
        const originalIdx = new Map(kids.map((k, i) => [k, i]));
        const weight = new Map();
        const key = (a, b) => (a < b ? `${a}|${b}` : `${b}|${a}`);
        (edges || []).forEach(e => {
            const a = topUnder(String(e.source), parentId);
            const b = topUnder(String(e.target), parentId);
            if (a && b && a !== b) weight.set(key(a, b), (weight.get(key(a, b)) || 0) + 1);
        });
        if (!weight.size) return;
        const ordered = [kids[0]];
        const rest = new Set(kids.slice(1));
        while (rest.size) {
            const last = ordered[ordered.length - 1];
            let best = null;
            let bestW = -1;
            rest.forEach(c => {
                const w = weight.get(key(last, c)) || 0;
                if (w > bestW || (w === bestW && originalIdx.get(c) < originalIdx.get(best))) {
                    best = c;
                    bestW = w;
                }
            });
            ordered.push(best);
            rest.delete(best);
        }
        tree.children.set(parentId, ordered);
    });
}

// Аккуратное слоистое дерево в координатах (breadth, depth): каждое поддерево — своя полоса.
// Связи рисуются «оргчартом»: от родителя вниз к шине, по шине, вниз к ребёнку.
function tidyPathTree(tree, cfg, breadthOf, wrapRoots) {
    const pos = new Map();
    const bends = new Map();
    const span = new Map();
    const isLeaf = id => !(tree.children.get(id) || []).length;
    const useColumns = cfg.columnThreshold !== undefined;

    const items = id => {
        const kids = tree.children.get(id) || [];
        const leaves = kids.filter(isLeaf);
        if (!useColumns || leaves.length <= cfg.columnThreshold) {
            return kids.map(k => ({ type: 'node', id: k }));
        }
        const branches = kids.filter(k => !isLeaf(k)).map(k => ({ type: 'node', id: k }));
        const column = { type: 'column', leaves, cols: Math.ceil(leaves.length / cfg.columnMaxRows) };
        branches.splice(Math.floor(branches.length / 2), 0, column);
        return branches;
    };
    const itemSpan = it => (it.type === 'column' ? it.cols * cfg.columnWidth : span.get(it.id));

    const measure = id => {
        const its = items(id);
        its.forEach(it => { if (it.type === 'node') measure(it.id); });
        const own = breadthOf(id) + cfg.siblingGap;
        const kidsSpan = its.reduce((sum, it) => sum + itemSpan(it), 0);
        span.set(id, Math.max(own, kidsSpan));
    };

    const place = (id, left, d) => {
        const total = span.get(id);
        const b = left + total / 2;
        pos.set(id, { b, d });
        const its = items(id);
        if (!its.length) return;
        const kidsSpan = its.reduce((sum, it) => sum + itemSpan(it), 0);
        let cur = left + (total - kidsSpan) / 2;
        const busD = d + cfg.depthGap * 0.45;
        its.forEach(it => {
            if (it.type === 'node') {
                place(it.id, cur, d + cfg.depthGap);
                bends.set(it.id, [{ b, d: busD }, { b: pos.get(it.id).b, d: busD }]);
            } else {
                it.leaves.forEach((leafId, idx) => {
                    const col = Math.floor(idx / cfg.columnMaxRows);
                    const row = idx % cfg.columnMaxRows;
                    const spineB = cur + col * cfg.columnWidth + 12;
                    const leafD = d + cfg.depthGap + row * cfg.rowGap;
                    pos.set(leafId, { b: spineB + cfg.leafOffset, d: leafD });
                    bends.set(leafId, [{ b, d: busD }, { b: spineB, d: busD }, { b: spineB, d: leafD }]);
                });
            }
            cur += itemSpan(it);
        });
    };

    measure(PATH_COURSE_ID);
    const roots = tree.children.get(PATH_COURSE_ID) || [];
    const totalSpan = roots.reduce((sum, r) => sum + span.get(r), 0);

    // Высота поддерева основы (по оси слоёв), включая столбики листьев
    const subtreeDepth = rootId => {
        let maxD = 0;
        const walk = id => {
            maxD = Math.max(maxD, pos.get(id).d);
            (tree.children.get(id) || []).forEach(walk);
        };
        walk(rootId);
        return maxD;
    };

    // Ряды: сколько основ с их деревьями в одном ряду, чтобы схема была близка к квадрату
    let rows = [roots];
    if (wrapRoots && roots.length > 1) {
        roots.forEach(r => place(r, 0, 0));
        const height = Math.max(...roots.map(subtreeDepth)) + cfg.depthGap * cfg.rowSpacing;
        const k = Math.max(1, Math.round(Math.sqrt(totalSpan / (cfg.targetAspect * height))));
        if (k > 1) {
            const maxRow = Math.max(...roots.map(r => span.get(r)), totalSpan / k);
            rows = [[]];
            let rowSpan = 0;
            roots.forEach(r => {
                if (rows[rows.length - 1].length && rowSpan + span.get(r) > maxRow * 1.05) {
                    rows.push([]);
                    rowSpan = 0;
                }
                rows[rows.length - 1].push(r);
                rowSpan += span.get(r);
            });
        }
    }

    if (rows.length === 1) {
        place(PATH_COURSE_ID, -span.get(PATH_COURSE_ID) / 2, 0);
        return { pos, bends };
    }

    // Перенос рядами: предмет в левом верхнем углу, от него вниз «ствол» левее всех деревьев,
    // от ствола к каждому ряду — своя шина между рядами. Линии дерева по-прежнему не пересекаются.
    const trunkB = -cfg.depthGap * 0.35;
    pos.set(PATH_COURSE_ID, { b: trunkB, d: 0 });
    let rowTop = cfg.depthGap;
    rows.forEach(row => {
        let left = 0;
        let rowBottom = rowTop;
        const busD = rowTop - cfg.depthGap * 0.45;
        row.forEach(r => {
            place(r, left, rowTop);
            bends.set(r, [{ b: trunkB, d: busD }, { b: pos.get(r).b, d: busD }]);
            rowBottom = Math.max(rowBottom, subtreeDepth(r));
            left += span.get(r);
        });
        rowTop = rowBottom + cfg.depthGap * cfg.rowSpacing;
    });
    return { pos, bends };
}

function layoutPathVertical(tree, horizontal) {
    const cfg = horizontal
        ? Object.assign({}, PATH_LAYOUT_CONFIG.horizontal)
        : Object.assign({}, PATH_LAYOUT_CONFIG.vertical);
    const breadthOf = id => {
        if (id === PATH_COURSE_ID) return horizontal ? 60 : 180;
        return horizontal ? cfg.nodeBreadth : pathLabelWidth(tree.byId.get(id));
    };
    const { pos, bends } = tidyPathTree(tree, cfg, breadthOf, true);
    const toXY = p => (horizontal ? { x: p.d, y: p.b } : { x: p.b, y: p.d });
    const positions = {};
    pos.forEach((p, id) => { positions[id] = toXY(p); });
    const outBends = {};
    bends.forEach((pts, id) => { outBends[id] = pts.map(toXY); });
    return { positions, bends: outBends, course: positions[PATH_COURSE_ID], hubRadius: 0 };
}

// Круговая: то же дерево в полярных координатах. Угол — полоса поддерева, радиус — слой.
function layoutPathRadial(tree) {
    const cfg = PATH_LAYOUT_CONFIG.radial;
    const leafWeight = new Map();
    const weightOf = id => {
        if (leafWeight.has(id)) return leafWeight.get(id);
        const kids = tree.children.get(id) || [];
        const node = tree.byId.get(id);
        // Основа или тема без подтем всё равно получает заметный сектор — иначе соседние темы слипаются
        const minWeight = node && node.tier <= 1 ? cfg.minCoreWeight : 1;
        const w = Math.max(minWeight, kids.length ? kids.reduce((s, k) => s + weightOf(k), 0) : 1);
        leafWeight.set(id, w);
        return w;
    };
    weightOf(PATH_COURSE_ID);

    const angle = new Map();
    const roots = tree.children.get(PATH_COURSE_ID) || [];
    const totalW = roots.reduce((s, r) => s + weightOf(r), 0) || 1;
    const equal = 1 / Math.max(1, roots.length);
    const assign = (id, start, end) => {
        angle.set(id, (start + end) / 2);
        const kids = tree.children.get(id) || [];
        if (!kids.length) return;
        const w = kids.reduce((s, k) => s + weightOf(k), 0);
        let cur = start;
        kids.forEach(k => {
            const share = (end - start) * weightOf(k) / w;
            assign(k, cur, cur + share);
            cur += share;
        });
    };
    // Сектор основы: половина поровну (основы не слипаются), половина — по размеру её ветвей
    let cur = -Math.PI / 2;
    roots.forEach(r => {
        const share = 2 * Math.PI * (0.5 * equal + 0.5 * weightOf(r) / totalW);
        assign(r, cur, cur + share);
        cur += share;
    });

    // Радиусы колец: не меньше, чем нужно, чтобы соседние подписи на кольце не слипались
    const byDepth = new Map();
    tree.depth.forEach((d, id) => {
        if (id === PATH_COURSE_ID) return;
        if (!byDepth.has(d)) byDepth.set(d, []);
        byDepth.get(d).push(id);
    });
    const minGapAngle = ids => {
        const a = ids.map(id => angle.get(id)).sort((x, y) => x - y);
        if (a.length < 2) return 2 * Math.PI;
        let g = 2 * Math.PI - (a[a.length - 1] - a[0]);
        for (let i = 1; i < a.length; i++) g = Math.min(g, a[i] - a[i - 1]);
        return Math.max(g, 1e-3);
    };
    const radius = new Map();
    const maxDepth = Math.max(0, ...byDepth.keys());
    const hubRadius = roots.length > 1 ? Math.max(130, cfg.minRootArc / minGapAngle(roots)) : 0;
    radius.set(1, hubRadius);
    for (let d = 2; d <= maxDepth; d++) {
        const ids = byDepth.get(d) || [];
        const gap = d === 2 ? cfg.firstRingGap : cfg.ringGaps[Math.min(d - 3, cfg.ringGaps.length - 1)];
        // Подписи тем (ярус ≤ 1) видны всегда — им нужна дуга minArc; мелким узлам хватает minLeafArc
        const core = ids.filter(id => (tree.byId.get(id) || {}).tier <= 1);
        const need = Math.max(
            core.length > 1 ? cfg.minArc / minGapAngle(core) : 0,
            cfg.minLeafArc / minGapAngle(ids)
        );
        radius.set(d, Math.min(2600, Math.max(radius.get(d - 1) + gap, need)));
    }

    const positions = { [PATH_COURSE_ID]: { x: 0, y: 0 } };
    tree.depth.forEach((d, id) => {
        if (id === PATH_COURSE_ID) return;
        let r = radius.get(d) || 0;
        // Плотный веер листьев одного родителя — через один на второе «подкольцо»
        const siblings = tree.children.get(tree.parent.get(id)) || [];
        const isLeaf = !(tree.children.get(id) || []).length;
        if (d > 2 && isLeaf && siblings.length > 5 && siblings.indexOf(id) % 2 === 1) r += cfg.leafSubRing;
        const a = angle.get(id);
        positions[id] = { x: Math.cos(a) * r, y: Math.sin(a) * r };
    });
    const ring2 = radius.get(2) || hubRadius + cfg.firstRingGap;
    const routeRadius = hubRadius + (ring2 - hubRadius) * 0.5;
    return { positions, bends: {}, course: { x: 0, y: 0 }, hubRadius, routeRadius };
}

// Главная точка входа: nodes [{id, tier, name, parent_id, prereq_keys}], edges [{source, target}]
function computePathLayout(nodes, edges, layoutType) {
    const tree = buildPathTree(nodes);
    orderPathChildren(tree, edges);
    const result = layoutType === 'radial'
        ? layoutPathRadial(tree)
        : layoutPathVertical(tree, layoutType === 'horizontal');
    result.parent = {};
    tree.parent.forEach((p, id) => { result.parent[id] = p; });
    result.depth = {};
    tree.depth.forEach((d, id) => { result.depth[id] = d; });
    return result;
}

if (typeof module !== 'undefined' && module.exports) {
    module.exports = { PATH_COURSE_ID, PATH_LAYOUT_CONFIG, buildPathTree, computePathLayout, pathLabelWidth };
}

/* ==========================================================================
   MILESTONE 4: INTERACTIVE PRACTICE MODULE CONTROLLER (R5)
   ========================================================================== */

let practiceItems = [];
let practiceFailedItems = [];
let practiceCurrentIndex = 0;
let practiceScore = 0;
let practiceAnswerSubmitted = false;
let isRetrySession = false;
let currentPracticeSubject = '';
let practiceStreak = 0;

const PRACTICE_CAT_INTRO = {
    recall: 'Варианты похожи — выбирай внимательно.',
    situational: 'Разберём ситуацию: какое правило здесь работает?',
    relation: 'Как связаны эти темы?'
};

function practiceCat(emotion, text, reaction) {
    if (typeof setCatWidget === 'function') setCatWidget('practice-cat', 'practice-cat-say', emotion, text, reaction);
}

function normalizeAnswer(str) {
    if (!str) return '';
    return str
        .replace(/[\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]/g, ' ')
        .trim()
        .replace(/[.!?,;:]+$/g, '')
        .trim()
        .toLowerCase();
}

window.openPracticeModal = function(targetSubject) {
    const modal = document.getElementById('practice-modal');
    if (!modal) return;

    currentPracticeSubject = targetSubject || (typeof getActiveDeckSubject === 'function' ? getActiveDeckSubject() : '');
    window.currentPracticeSubject = currentPracticeSubject;
    const sub = currentPracticeSubject;
    const badge = document.getElementById('practice-subject-badge');
    if (badge) badge.textContent = sub.toUpperCase();

    modal.classList.remove('hidden');
    startPracticeSession(sub);
};

window.closePracticeModal = function() {
    const modal = document.getElementById('practice-modal');
    if (modal) modal.classList.add('hidden');
};

window.startPracticeSession = async function(customSub) {
    isRetrySession = false;
    currentPracticeSubject = customSub || currentPracticeSubject || (typeof getActiveDeckSubject === 'function' ? getActiveDeckSubject() : '');
    window.currentPracticeSubject = currentPracticeSubject;
    const sub = currentPracticeSubject;
    
    const loading = document.getElementById('practice-loading');
    const cardContainer = document.getElementById('practice-card-container');
    const finishScreen = document.getElementById('practice-finish-screen');

    if (loading) loading.classList.remove('hidden');
    if (cardContainer) cardContainer.classList.remove('hidden');
    if (finishScreen) finishScreen.classList.add('hidden');

    practiceItems = [];
    practiceFailedItems = [];
    practiceCurrentIndex = 0;
    practiceScore = 0;
    practiceStreak = 0;
    practiceAnswerSubmitted = false;

    try {
        const count = (window.pathRun && window.pathRun.active && window.pathRun.practiceCount) || 10;
        const res = await apiFetch(`/api/practice/session?subject=${encodeURIComponent(sub)}&count=${count}`);
        if (res.ok) {
            practiceItems = await res.json();
        }

        if (!practiceItems || practiceItems.length === 0) {
            alert("Для этого предмета еще нет карточек практики. Загрузите конспект или учебник для автоматической нарезки практических кейсов.");
            closePracticeModal();
            return;
        }

        renderPracticeQuestion();

    } catch (e) {
        console.error("Сбой запуска практики:", e);
        alert("Ошибка сети при подготовке практических заданий.");
        closePracticeModal();
    } finally {
        if (loading) loading.classList.add('hidden');
    }
};

function renderPracticeQuestion() {
    if (practiceCurrentIndex >= practiceItems.length) {
        showPracticeFinish();
        return;
    }

    const item = practiceItems[practiceCurrentIndex];
    practiceAnswerSubmitted = false;

    // Counters
    const curStep = document.getElementById('practice-current-step');
    const totStep = document.getElementById('practice-total-step');
    if (curStep) curStep.textContent = practiceCurrentIndex + 1;
    if (totStep) totStep.textContent = practiceItems.length;

    // Type badge
    const typeBadge = document.getElementById('practice-item-type-badge');
    if (typeBadge) {
        const typeConfigs = {
            'situational': { icon: 'psychology', label: 'СИТУАЦИОННЫЙ КЕЙС' },
            'recall': { icon: 'quiz', label: 'ВСПОМНИ ОТВЕТ' },
            'relation': { icon: 'hub', label: 'СВЯЗЬ ТЕМ' },
            'contrast_pair': { icon: 'compare_arrows', label: 'КОНТРАСТНАЯ ПАРА' },
            'slot_filling': { icon: 'edit_note', label: 'ЗАПОЛНЕНИЕ ПРОПУСКА' },
            'conceptual': { icon: 'quiz', label: 'ТЕСТОВЫЙ ВОПРОС' },
            'taxonomy': { icon: 'account_tree', label: 'КЛАССИФИКАЦИЯ' }
        };
        const cfg = typeConfigs[item.type] || { icon: 'quiz', label: 'ПРАКТИЧЕСКИЙ ТЕСТ' };
        typeBadge.innerHTML = `<span class="material-symbols-outlined text-[13px]">${cfg.icon}</span><span>${cfg.label}</span>`;
    }

    // Prompt
    const promptEl = document.getElementById('practice-prompt');
    if (promptEl) promptEl.textContent = item.prompt;

    // Кот объявляет задание; на первом — напоминает, что ошибаться здесь нормально
    const catLine = PRACTICE_CAT_INTRO[item.type] || 'Выбери верный вариант.';
    practiceCat('think', practiceCurrentIndex === 0 && !isRetrySession
        ? `Практика вперемешку: учимся отличать похожее. Ошибаться здесь нормально. ${catLine}`
        : (isRetrySession ? `Работа над ошибками. ${catLine}` : catLine));

    // Hide feedback container
    const feedbackBox = document.getElementById('practice-feedback-container');
    if (feedbackBox) feedbackBox.classList.add('hidden');

    // Render options
    const optionsContainer = document.getElementById('practice-options-list');
    if (!optionsContainer) return;
    optionsContainer.innerHTML = '';

    const letters = ['A', 'B', 'C', 'D', 'E'];
    item.options.forEach((optText, idx) => {
        const btn = document.createElement('button');
        btn.className = "practice-option-btn w-full p-3 rounded-xl bg-surface-container-lowest border border-neutral-200 dark:border-neutral-800 hover:border-neutral-400 transition-all text-left flex items-start gap-3 cursor-pointer select-none text-xs sm:text-sm text-neutral-800 dark:text-neutral-200 shadow-xs";
        btn.innerHTML = `
            <span class="w-6 h-6 rounded-lg bg-neutral-100 dark:bg-neutral-800 border border-neutral-200 dark:border-neutral-700 text-neutral-700 dark:text-neutral-300 font-mono font-bold text-xs flex items-center justify-center shrink-0">
                ${letters[idx] || (idx + 1)}
            </span>
            <span class="leading-snug pt-0.5">${escapeHTML(optText)}</span>
        `;

        btn.onclick = () => {
            selectPracticeOption(item.id, optText, btn);
        };

        optionsContainer.appendChild(btn);
    });
}

async function selectPracticeOption(itemId, selectedText, clickedBtn) {
    if (practiceAnswerSubmitted) return;
    practiceAnswerSubmitted = true;
    const currentItem = practiceItems[practiceCurrentIndex];

    // Disable all option buttons
    const allButtons = document.querySelectorAll('#practice-options-list button');
    allButtons.forEach(b => {
        b.disabled = true;
        b.classList.remove('hover:border-neutral-400', 'cursor-pointer');
    });

    clickedBtn.innerHTML += ` <span class="material-symbols-outlined text-sm animate-spin ml-auto">sync</span>`;

    const handleVerifyFailure = () => {
        const spinner = clickedBtn.querySelector('.animate-spin');
        if (spinner) spinner.remove();
        allButtons.forEach(b => {
            b.disabled = false;
            b.classList.add('hover:border-neutral-400', 'cursor-pointer');
        });
        practiceAnswerSubmitted = false;
        if (typeof window.showNotification === 'function') {
            window.showNotification("Ошибка проверки ответа. Попробуйте еще раз.", "error");
        } else {
            alert("Ошибка проверки ответа. Попробуйте еще раз.");
        }
    };

    try {
        const res = await apiFetch('/api/practice/verify', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                item_id: itemId,
                selected_answer: selectedText
            })
        });

        if (!res.ok) {
            handleVerifyFailure();
            return;
        }

        const data = await res.json();
        
        // Remove spinner
        const spinner = clickedBtn.querySelector('.animate-spin');
        if (spinner) spinner.remove();

        const isCorrect = data.correct;
        const why = (data.explanation && data.explanation !== `Правильный ответ: ${data.correct_answer}`) ? ` ${data.explanation}` : '';
        if (isCorrect) {
            practiceStreak++;
            const praise = practiceStreak >= 3 ? `${practiceStreak} подряд!` : 'Верно!';
            practiceCat('happy', `${praise}${why}`, 'bounce');
        } else {
            practiceStreak = 0;
            practiceCat('confused', `Не совсем. Правильно: «${String(data.correct_answer || '').replace(/[.!]+$/, '')}».${why}`, 'shake');
        }
        if (isCorrect) {
            practiceScore++;
            clickedBtn.classList.add('practice-option-correct');
            if (window.Telegram?.WebApp?.HapticFeedback) {
                window.Telegram.WebApp.HapticFeedback.notificationOccurred('success');
            }
        } else {
            if (currentItem) {
                practiceFailedItems.push(currentItem);
            }
            clickedBtn.classList.add('practice-option-wrong');
            if (window.Telegram?.WebApp?.HapticFeedback) {
                window.Telegram.WebApp.HapticFeedback.notificationOccurred('error');
            }

            // Highlight authoritative correct answer button
            allButtons.forEach(b => {
                const textSpan = b.querySelectorAll('span')[1];
                if (textSpan && normalizeAnswer(textSpan.textContent) === normalizeAnswer(data.correct_answer)) {
                    b.classList.add('practice-option-correct');
                }
            });
        }

        // Show feedback container
        const feedbackContainer = document.getElementById('practice-feedback-container');
        const statusEl = document.getElementById('practice-feedback-status');
        const goldBox = document.getElementById('practice-gold-standard-box');
        const goldText = document.getElementById('practice-gold-standard-text');
        const explText = document.getElementById('practice-explanation-text');

        if (statusEl) {
            if (isCorrect) {
                statusEl.className = "flex items-center gap-2 font-mono font-bold text-xs uppercase text-emerald-700 dark:text-emerald-400";
                statusEl.innerHTML = `<span class="material-symbols-outlined text-base">check_circle</span> <span>ВЕРНО! ТОЧНЫЙ ВЫБОР</span>`;
            } else {
                statusEl.className = "flex items-center gap-2 font-mono font-bold text-xs uppercase text-rose-700 dark:text-rose-400";
                statusEl.innerHTML = `<span class="material-symbols-outlined text-base">cancel</span> <span>НЕВЕРНО. ПРАВИЛЬНЫЙ ОТВЕТ: ${escapeHTML(data.correct_answer)}</span>`;
            }
        }

        if (goldBox && goldText) {
            if (data.gold_standard && normalizeAnswer(data.gold_standard) !== normalizeAnswer(data.correct_answer)) {
                goldBox.classList.remove('hidden');
                goldText.textContent = data.gold_standard;
            } else {
                goldBox.classList.add('hidden');
            }
        }

        if (explText) {
            explText.textContent = data.explanation || 'Обоснование отсутствует.';
        }
        // Разъяснение уже сказал кот — не дублируем его ниже
        const explBox = document.getElementById('practice-explanation-box');
        if (explBox) explBox.classList.toggle('hidden', !!why);

        if (feedbackContainer) {
            feedbackContainer.classList.remove('hidden');
            feedbackContainer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

    } catch (e) {
        console.error("Сбой проверки ответа:", e);
        handleVerifyFailure();
    }
}

window.nextPracticeQuestion = function() {
    practiceCurrentIndex++;
    renderPracticeQuestion();
};

function showPracticeFinish() {
    const cardContainer = document.getElementById('practice-card-container');
    const finishScreen = document.getElementById('practice-finish-screen');
    const scoreEl = document.getElementById('practice-finish-score');
    const msgEl = document.getElementById('practice-finish-message');
    const savedBadge = document.getElementById('practice-saved-badge');

    if (cardContainer) cardContainer.classList.add('hidden');
    if (finishScreen) finishScreen.classList.remove('hidden');

    const total = practiceItems.length;
    const percent = total > 0 ? Math.round((practiceScore / total) * 100) : 0;

    if (scoreEl) {
        scoreEl.textContent = `${practiceScore} / ${total} (${percent}%)`;
    }

    let finishEmo = 'happy';
    if (msgEl) {
        if (isRetrySession) {
            msgEl.textContent = "Ошибки разобраны — теперь эти различия твои.";
        } else if (percent >= 80) {
            msgEl.textContent = "Отлично различаешь похожее. Мур!";
        } else if (percent >= 50) {
            msgEl.textContent = "Неплохо! Ошибки — это как раз то, что стоит разобрать ещё раз.";
            finishEmo = 'think';
        } else {
            msgEl.textContent = "Пока путается — это нормально. Разбери ошибки, и в следующий раз будет легче.";
            finishEmo = 'confused';
        }
    }
    const finishCat = document.getElementById('practice-finish-cat');
    if (finishCat && typeof LESSON_CAT !== 'undefined') {
        finishCat.textContent = (LESSON_CAT[finishEmo] || LESSON_CAT.happy).frames[0].join('\n');
        if (!prefersReducedMotion() && finishEmo === 'happy') {
            finishCat.classList.remove('cat-react-bounce');
            void finishCat.offsetWidth;
            finishCat.classList.add('cat-react-bounce');
        }
    }

    // Сохраняем результат в базу данных и обновляем бейдж на стартовом экране (только для основных сессий)
    const curSub = currentPracticeSubject || (typeof getActiveDeckSubject === 'function' ? getActiveDeckSubject() : '');
    if (isRetrySession) {
        if (savedBadge) {
            savedBadge.innerHTML = `<span class="material-symbols-outlined text-sm">task_alt</span><span>Ошибки успешно проработаны!</span>`;
        }
    } else {
        if (savedBadge) {
            savedBadge.innerHTML = `<span class="material-symbols-outlined text-sm">check_circle</span><span>Результат практики зафиксирован в профиле</span>`;
        }
        apiFetch('/api/practice/complete', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                subject: curSub,
                score: practiceScore,
                total: total
            })
        }).then(() => {
            if (typeof checkTodayPracticeStats === 'function') checkTodayPracticeStats(curSub);
        }).catch(err => {
            console.warn("Сбой фиксации результатов практики:", err);
        });
    }

    // Настройка кнопки повторения ошибок
    const retryBtn = document.getElementById('practice-retry-errors-btn');
    const errorsCountEl = document.getElementById('practice-errors-count');
    if (retryBtn && errorsCountEl) {
        if (practiceFailedItems.length > 0) {
            errorsCountEl.textContent = practiceFailedItems.length;
            retryBtn.classList.remove('hidden');
        } else {
            retryBtn.classList.add('hidden');
        }
    }

    // Настройка кнопки прохождения новой сессии; в «Продолжить путь» она ведёт к следующему шагу
    const newSessionBtn = document.getElementById('practice-new-session-btn');
    if (newSessionBtn) {
        const inRun = window.pathRun && window.pathRun.active;
        newSessionBtn.innerHTML = inRun
            ? '<span class="material-symbols-outlined text-sm">arrow_forward</span><span>Дальше по пути</span>'
            : '<span class="material-symbols-outlined text-sm">refresh</span><span>Пройти новую сессию</span>';
        newSessionBtn.onclick = inRun
            ? () => { closePracticeModal(); window.pathRun.stepFinished(); }
            : () => startPracticeSession(currentPracticeSubject);
    }
}

window.retryPracticeErrors = function() {
    if (!practiceFailedItems || practiceFailedItems.length === 0) return;

    isRetrySession = true;

    const cardContainer = document.getElementById('practice-card-container');
    const finishScreen = document.getElementById('practice-finish-screen');
    if (cardContainer) cardContainer.classList.remove('hidden');
    if (finishScreen) finishScreen.classList.add('hidden');

    practiceItems = [...practiceFailedItems];
    practiceFailedItems = [];
    practiceCurrentIndex = 0;
    practiceScore = 0;
    practiceAnswerSubmitted = false;

    renderPracticeQuestion();
};

window.checkTodayPracticeStats = async function(sub) {
    const targetSub = sub || getActiveDeckSubject();
    const badge = document.getElementById('starter-practice-badge');
    if (!badge) return;

    try {
        const res = await apiFetch(`/api/practice/stats?subject=${encodeURIComponent(targetSub)}`);
        if (res.ok) {
            const data = await res.json();
            if (data && data.today_completed) {
                badge.className = "px-2 py-0.5 rounded-md text-[10px] font-mono font-bold bg-emerald-100 dark:bg-emerald-950 text-emerald-700 dark:text-emerald-400 border border-emerald-300 dark:border-emerald-800 flex items-center gap-1";
                badge.innerHTML = `<span class="material-symbols-outlined text-[12px]">check_circle</span><span>СЕГОДНЯ ${data.last_score}/${data.last_total}</span>`;
                return;
            }
        }
        badge.className = "px-2 py-0.5 rounded-md text-[10px] font-bold bg-neutral-100 dark:bg-neutral-800 text-neutral-500 border border-neutral-200 dark:border-neutral-700";
        badge.textContent = "ТРЕНАЖЕР";
    } catch (e) {
        console.warn("Сбой проверки статистики практики:", e);
    }
};
// ============================================================================
// УРОК УЗЛА «ПУТИ ЗНАНИЙ»: КОТ-РАССКАЗЧИК, ВОПРОСЫ НА ПОНИМАНИЕ, ЗАВЕРШЕНИЕ
// Озвучки нет: кот «говорит» только текстом (TTS работает лишь в тренировке языков).
// ============================================================================

// Кадры кота: 3 строки по 9 символов. Эмоции приходят из урока (поле emo).
const LESSON_CAT = {
    idle:      { frames: [[' /\\_/\\   ', '( o.o )  ', ' > ^ <   ']], blink: [' /\\_/\\   ', '( -.- )  ', ' > ^ <   '] },
    talk:      { frames: [[' /\\_/\\   ', '( o.o )  ', ' > o <   '], [' /\\_/\\   ', '( o.o )  ', ' > - <   ']] },
    happy:     { frames: [[' /\\_/\\   ', '( ^.^ )  ', ' > w <   ']] },
    think:     { frames: [[' /\\_/\\  ?', '( -.- )  ', ' > ~ <   '], [' /\\_/\\ ? ', '( -.- )  ', ' > ~ <   ']] },
    surprised: { frames: [[' /\\_/\\  !', '( O.O )  ', ' > o <   ']] },
    confused:  { frames: [[' /\\_/\\  ?', '( o.O )  ', ' > ~ <   ']] }
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
    ['lesson-focus', 'lesson-options'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.innerHTML = '';
    });
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
    // В режиме «Продолжить путь» карточки темы идут сразу: вспоминание сразу после урока
    if (window.pathRun && window.pathRun.active) {
        sayLesson(`Урок пройден!${scoreLine} Теперь закрепим на карточках, пока свежо.`, 'happy', 'bounce');
        setLessonButtons('Дальше', true, null);
        lessonState.onNext = () => {
            closeLesson();
            window.pathRun.stepFinished();
        };
        return;
    }
    sayLesson(`Урок пройден!${scoreLine} Карточки этой темы уже ждут тебя в тренировке.`, 'happy', 'bounce');
    setLessonButtons('К графу', true, null);
    lessonState.onNext = () => {
        closeLesson();
        if (window.loadKnowledgeGraph) window.loadKnowledgeGraph(typeof currentKgSubject !== 'undefined' ? currentKgSubject : undefined);
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
        screens: data.lesson.screens,
        checks: data.lesson.check || [],
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
    if (tierEl) tierEl.textContent = LESSON_TIER_NAMES[data.node.tier] || '';

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
