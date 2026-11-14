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
// УВЕДОМЛЕНИЯ: неблокирующие тосты вместо системных alert() (в Telegram WebView они выглядят как сбой)
// confirm() остаётся системным: там нужен ответ пользователя.
// ============================================================================
window.nativeAlert = window.alert.bind(window);
window.showToast = function(message, kind = 'info') {
    try {
        const text = String(message == null ? '' : message);
        if (!text.trim() || !document.body) return;
        let host = document.getElementById('toast-host');
        if (!host) {
            host = document.createElement('div');
            host.id = 'toast-host';
            host.setAttribute('role', 'status');
            host.setAttribute('aria-live', 'polite');
            host.style.cssText = 'position:fixed;left:0;right:0;top:calc(var(--tg-safe-top,0px) + 12px);z-index:10000;' +
                'display:flex;flex-direction:column;align-items:center;gap:8px;pointer-events:none;padding:0 16px;';
            document.body.appendChild(host);
        }
        const colors = { info: '#e5e7eb', error: '#fecaca', success: '#bbf7d0' };
        const borders = { info: '#525252', error: '#ef4444', success: '#22c55e' };
        const el = document.createElement('button');
        el.type = 'button';
        el.textContent = text;
        el.style.cssText = 'pointer-events:auto;max-width:520px;text-align:left;padding:10px 14px;border-radius:14px;cursor:pointer;' +
            `background:#171717;color:${colors[kind] || colors.info};border:1px solid ${borders[kind] || borders.info};` +
            'font:500 13px/1.4 system-ui,sans-serif;box-shadow:0 8px 24px rgba(0,0,0,.35);white-space:pre-line;';
        const close = () => { try { el.remove(); } catch (_) {} };
        el.onclick = close;
        host.appendChild(el);
        setTimeout(close, Math.min(12000, 3500 + text.length * 45));
    } catch (_) {}
};
// Все существующие alert(...) в приложении становятся тостами; ошибки подсвечиваются по тексту
window.alert = function(message) {
    const text = String(message == null ? '' : message);
    window.showToast(text, /ошибк|не удалось|сбой|нет связи|запрещ|слишком/i.test(text) ? 'error' : 'info');
};

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
            'width:max-content;max-width:calc(100vw - 32px);text-align:center;padding:8px 14px;border-radius:16px;border:1px solid #f59e0b;background:#1c1917;color:#fbbf24;font:600 12px/1.3 monospace;cursor:pointer;';
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
        const dayOverlay = document.getElementById('day-celebration');
        if (dayOverlay && !dayOverlay.classList.contains('hidden')) {
            if (typeof window.closeDayCelebration === 'function') window.closeDayCelebration();
            return;
        }
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
// ASCII-ЛОАДЕР: маленький кот над полосой прогресса вместо крутящейся иконки.
// Один общий таймер на все [data-ascii-spinner]; скрытые лоадеры кадры не отнимают.
// ============================================================================
const ASCII_SPINNER_BAR_WIDTH = 11;
let asciiSpinnerTimer = null;

function asciiSpinnerText(step) {
    const cat = (typeof LESSON_CAT !== 'undefined' && LESSON_CAT.idle) ? LESSON_CAT.idle : null;
    const phase = step % 20;
    const catLines = cat ? ((phase === 5 || phase === 16) ? cat.blink : cat.frames[0]) : null;
    const pos = step % ASCII_SPINNER_BAR_WIDTH;
    const bar = `[${'='.repeat(pos)}>${' '.repeat(ASCII_SPINNER_BAR_WIDTH - pos)}]`;
    return catLines ? `${catLines.join('\n')}\n${bar}` : bar;
}

function asciiSpinnerTick() {
    const reduced = typeof prefersReducedMotion === 'function' && prefersReducedMotion();
    let visible = 0;
    document.querySelectorAll('[data-ascii-spinner]').forEach(el => {
        if (!el.getClientRects().length) return; // скрыт — не тратим кадры
        visible += 1;
        const step = reduced ? 0 : (el._dgAsciiStep || 0);
        el.textContent = asciiSpinnerText(step);
        if (!reduced) el._dgAsciiStep = (step + 1) % 40;
    });
    asciiSpinnerTimer = setTimeout(asciiSpinnerTick, visible ? 150 : 500);
}

function startAsciiSpinners() {
    if (asciiSpinnerTimer) return;
    asciiSpinnerTick();
}
window.startAsciiSpinners = startAsciiSpinners;

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', startAsciiSpinners);
} else {
    setTimeout(startAsciiSpinners, 0);
}

// ============================================================================
// АДАПТИВНАЯ ОЦЕНКА ДОМЕНА КАРТОЧКИ, PARTIAL BLUR И УНИВЕРСАЛЬНЫЙ TTS
// ============================================================================
window.detectCardDomain = function(card) {
    const c = card || {};
    const subRaw = String(c.subject || c.subject_title || (typeof currentSubject !== 'undefined' ? currentSubject : '') || '').trim();
    const sub = subRaw.toLowerCase();
    const q = String(c.text || '');
    const trans = String(c.translation || '');
    const sec = String(c.secondary_text || '');
    const ex = String(c.example || '');
    const joinedLang = q + ' ' + ex;

    const hasHanzi = /[\u4e00-\u9fa5]/.test(joinedLang);
    const hasKana = /[\u3040-\u30ff]/.test(joinedLang);
    const hasHangul = /[\uac00-\ud7af]/.test(joinedLang);
    const langSubMatch = /(chinese|hsk|mandarin|pinyin|english|eng_|german|de_|spanish|es_|french|fr_|italian|it_|japanese|jp_|korean|ko_|vocab|lang|язык|китай|англ|немец|испан|франц|япон|корей|иероглиф)/i.test(sub);
    const hasPinyinCue = /(пиньин|pinyin|тон|tone|hsk)/i.test(sec);
    const isLanguage = hasHanzi || hasKana || hasHangul || langSubMatch || hasPinyinCue;

    const isLaw = !isLanguage && (
        /(law|право|гк\b|ук\b|апк\b|гпк\b|упк\b|коап|конст|суд|юрис|арбитраж|кодекс)/i.test(sub)
        || /(?:^|[\s(])(ст\.|ч\.\s*\d+|п\.\s*\d+|гк\s*рф|ук\s*рф|апк\s*рф|гпк\s*рф|фз\s*№|конституц|правовая\s+норма|законодатель)/i.test(q + ' ' + sec + ' ' + trans)
    );
    const isStem = !isLanguage && !isLaw && (
        /(phys|math|chem|bio|info|code|alg|calc|geom|mech|физ|хим|мат|инф|програм|алг|геом|механ)/i.test(sub)
        || /[=≈≤≥∑∫÷±→↔]/.test(sec)
        || /(формула|единица|СИ:|Дж|Вт|кг\/м|м\/с|моль|Ом)/i.test(sec + ' ' + trans)
    );

    const key = isLanguage ? 'language' : (isLaw ? 'law' : (isStem ? 'stem' : 'general'));
    const langCode = window.detectSpeechLang(q || ex, c);
    const isChinese = key === 'language' && (langCode === 'zh-CN' || hasHanzi || /chinese|hsk|mandarin|китай/.test(sub));

    const labels = key === 'language'
        ? { main: 'ПЕРЕВОД И ЗНАЧЕНИЕ', sec: isChinese ? 'ЧТЕНИЕ (ПИНЬИНЬ)' : 'ЧТЕНИЕ / ФОРМА', ex: 'ПРИМЕР В КОНТЕКСТЕ', frontAction: 'Озвучить' }
        : key === 'law'
            ? { main: 'ПРАВОВОЕ ПРАВИЛО', sec: 'НОРМА И УСЛОВИЕ', ex: 'ПРИМЕР ИЗ ПРАКТИКИ', frontAction: null }
            : key === 'stem'
                ? { main: 'СУТЬ И ЗАКОН', sec: 'ФОРМУЛА / СИ', ex: 'ПРИМЕР ПРИМЕНЕНИЯ', frontAction: null }
                : { main: 'ОТВЕТ / СУТЬ', sec: 'КОНТЕКСТ И ДЕТАЛИ', ex: 'ПРИМЕР', frontAction: null };

    return { key, isLanguage, isChinese, langCode, labels };
};

window.detectSpeechLang = function(text, cardOrSub) {
    const t = String(text || '');
    if (/[\u4e00-\u9fa5]/.test(t)) return 'zh-CN';
    if (/[\u3040-\u30ff]/.test(t)) return 'ja-JP';
    if (/[\uac00-\ud7af]/.test(t)) return 'ko-KR';

    let sub = '';
    if (typeof cardOrSub === 'string') sub = cardOrSub;
    else if (cardOrSub && typeof cardOrSub === 'object') sub = cardOrSub.subject || cardOrSub.subject_title || '';
    if (!sub && typeof currentSubject !== 'undefined' && currentSubject !== 'all') sub = currentSubject;
    const s = String(sub || '').toLowerCase();

    if (/chinese|hsk|mandarin|китай/.test(s)) return 'zh-CN';
    if (/japanese|jp_|япон/.test(s)) return 'ja-JP';
    if (/korean|ko_|корей/.test(s)) return 'ko-KR';
    if (/english|eng_|англ/.test(s)) return 'en-US';
    if (/german|de_|немец/.test(s)) return 'de-DE';
    if (/spanish|es_|испан/.test(s)) return 'es-ES';
    if (/french|fr_|франц/.test(s)) return 'fr-FR';
    if (/italian|it_|итал/.test(s)) return 'it-IT';
    if (/vocab|lang|язык/.test(s) && /[A-Za-zÀ-ÿ]{2,}/.test(t)) return 'en-US';
    return null;
};

function _extractSpeakableSegment(text, lang) {
    if (!text || text === '---' || !lang) return '';
    let clean = typeof formatClozePlain === 'function' ? formatClozePlain(String(text)) : String(text);
    clean = clean.replace(/\s*\([А-Яа-яЁё][^)]*\)\s*/g, ' ').replace(/\s+/g, ' ').trim();
    if (!clean) return '';

    if (lang === 'zh-CN' || lang === 'ja-JP' || lang === 'ko-KR') {
        const scriptRe = lang === 'zh-CN' ? /[\u4e00-\u9fa5]/ : (lang === 'ja-JP' ? /[\u3040-\u30ff\u4e00-\u9fa5]/ : /[\uac00-\ud7af]/);
        if (!scriptRe.test(clean)) return '';
        const parts = clean.split(/\s*(?:—|–|\|)\s*|\s+\/\s+/);
        let target = parts.find(p => scriptRe.test(p) && !/[А-Яа-яЁё]{3,}/.test(p));
        if (!target) {
            const lead = clean.replace(/[\s—–|/:-]*[А-Яа-яЁё].*$/, '').trim();
            if (scriptRe.test(lead)) target = lead;
        }
        return (target && scriptRe.test(target)) ? target.replace(/[\s—–|/:]+$/, '').trim() : '';
    }

    if (/[A-Za-zÀ-ÿ]/.test(clean)) {
        const parts = clean.split(/\s*(?:—|–|\|)\s*|\s+\/\s+/);
        let target = parts.find(p => /[A-Za-zÀ-ÿ]/.test(p) && !/[А-Яа-яЁё]{3,}/.test(p));
        if (!target) {
            const lead = clean.replace(/[\s—–|/:-]*[А-Яа-яЁё].*$/, '').trim();
            if (/[A-Za-zÀ-ÿ]{2,}/.test(lead)) target = lead;
        }
        return (target && /[A-Za-zÀ-ÿ]{2,}/.test(target)) ? target.replace(/[\s—–|/:]+$/, '').trim() : '';
    }
    return '';
}

window.isSpeakable = function(text, cardOrSub) {
    if (!text || text === '---') return false;
    const lang = window.detectSpeechLang(text, cardOrSub);
    return Boolean(lang && _extractSpeakableSegment(text, lang));
};

window.isLanguageCard = function(card) {
    if (!card) return false;
    const d = window.detectCardDomain(card);
    if (!d.isLanguage) return false;
    return window.isSpeakable(card.text, card) || window.isSpeakable(card.example, card);
};

window.executeVoiceSynthesis = function(textToSpeak, cardOrSub) {
    if (!window.speechSynthesis || !textToSpeak) return false;
    const lang = window.detectSpeechLang(textToSpeak, cardOrSub);
    const speakable = _extractSpeakableSegment(textToSpeak, lang);
    if (!lang || !speakable) return false;

    try {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(speakable);
        utterance.rate = 0.85;
        utterance.lang = lang;
        window.currentUtterance = utterance;
        window.speechSynthesis.speak(utterance);
        return true;
    } catch (e) {
        console.error("Сбой аудио-канала:", e);
        return false;
    }
};

window.replayAudioForText = function(text, cardOrSub) {
    return window.executeVoiceSynthesis(text, cardOrSub);
};

window.makeScaffoldHint = function(val, domainKey) {
    const raw = String(val || '').trim();
    if (!raw) return '';
    if (raw.includes('=') && domainKey === 'stem') {
        const idx = raw.indexOf('=');
        const lhs = raw.slice(0, idx).trim();
        const rhs = raw.slice(idx + 1).trim();
        const maskedRhs = rhs.replace(/[A-Za-zА-Яа-яЁё0-9]+/g, m => '_'.repeat(Math.min(2, m.length)));
        return `${lhs} = ${maskedRhs}`;
    }
    const legalKeepRe = /^(п\.|ч\.|ст\.|гл\.|разд\.|ГК|УК|АПК|ГПК|УПК|РФ|НК|КоАП|ФЗ|и|в|на|от|по)$/i;
    return raw.split(/\s+/).map(tok => {
        if (domainKey === 'law' && legalKeepRe.test(tok)) return tok;
        if (/^\d+[а-яa-z0-9.,-]*$/i.test(tok)) return '_'.repeat(Math.min(3, tok.replace(/[.,]/g, '').length || 1));
        if (tok.length <= 1) return tok;
        const first = tok[0];
        const tailLen = tok.replace(/[.,;:!?()\[\]]+$/, '').length - 1;
        if (tailLen <= 0) return tok;
        return first + '_'.repeat(Math.min(2, tailLen));
    }).join(' ');
};

window.splitSecondaryParts = function(secondary, domainInfo) {
    const raw = String(secondary || '').trim();
    if (!raw || raw === '---') return [];
    const d = domainInfo || { key: 'general', isChinese: false };
    const chunks = raw.split(/\s*(?:\||;|\n)\s*/).map(s => s.trim()).filter(Boolean).slice(0, 4);
    return chunks.map((chunk, idx) => {
        const colonIdx = chunk.indexOf(':');
        let label = '';
        let value = chunk;
        if (colonIdx > 0 && colonIdx <= 22) {
            label = chunk.slice(0, colonIdx).trim();
            value = chunk.slice(colonIdx + 1).trim() || chunk;
        } else if (idx === 0) {
            label = d.key === 'language'
                ? (d.isChinese ? 'Пиньинь' : 'Чтение')
                : (d.key === 'law' ? 'Норма' : (d.key === 'stem' ? 'Формула' : ''));
        }
        return {
            label,
            value,
            scaffold: window.makeScaffoldHint(value, d.key)
        };
    });
};

window.renderPartialBlurChipsHTML = function(secondary, domainInfo, opts = {}) {
    const parts = window.splitSecondaryParts(secondary, domainInfo);
    if (!parts.length) return '';
    const masked = opts.masked !== false;
    const searchTerms = Array.isArray(opts.searchTerms) && opts.searchTerms.length > 0 ? opts.searchTerms : null;
    return parts.map(p => {
        const valHtml = (searchTerms && typeof highlightMatches === 'function')
            ? highlightMatches(p.value, searchTerms)
            : escapeHTML(p.value);
        const cls = masked ? 'blur-chip blur-chip-masked' : 'blur-chip blur-chip-open';
        return `
            <button type="button" data-no-swipe class="${cls}" onclick="event.stopPropagation(); window.toggleBlurChip(this);" aria-label="Показать или скрыть подсказку">
                ${p.label ? `<span class="blur-chip-key">${escapeHTML(p.label)}</span>` : ''}
                <span class="blur-chip-scaffold" aria-hidden="true">${escapeHTML(p.scaffold)}</span>
                <span class="blur-chip-val">${valHtml}</span>
            </button>`;
    }).join('');
};

window.renderExampleTokensHTML = function(example, card, opts = {}) {
    const rawEx = String(example || '').trim();
    if (!rawEx || rawEx === '---') return '';
    const wrapped = /^[«"'].*[»"']$/.test(rawEx) ? rawEx : `«${rawEx}»`;
    const searchTerms = Array.isArray(opts.searchTerms) && opts.searchTerms.length > 0 ? opts.searchTerms : null;
    if (searchTerms && typeof highlightMatches === 'function') {
        return highlightMatches(wrapped, searchTerms);
    }

    const rawTerm = (typeof formatClozePlain === 'function' ? formatClozePlain(String(card?.text || '')) : String(card?.text || '')).trim();
    const masked = opts.masked !== false;
    if (!rawTerm || rawTerm.includes('?')) return escapeHTML(wrapped);

    const hasCjk = /[\u4e00-\u9fa5\u3040-\u30ff\uac00-\ud7af]/.test(rawTerm);
    const wordCount = rawTerm.split(/\s+/).filter(Boolean).length;
    const eligible = (hasCjk && rawTerm.length >= 1 && rawTerm.length <= 12)
        || (!hasCjk && rawTerm.length >= 3 && rawTerm.length <= 28 && wordCount <= 3);
    if (!eligible) return escapeHTML(wrapped);

    const tokenCls = `blur-token ${masked ? 'is-masked' : 'is-revealed'}`;
    const makeBtn = (matched) => `<button type="button" data-no-swipe class="${tokenCls}" onclick="event.stopPropagation(); window.toggleBlurToken(this);" aria-label="Показать слово в примере"><span class="blur-token-txt">${escapeHTML(matched)}</span></button>`;

    if (hasCjk) {
        const idx = wrapped.indexOf(rawTerm);
        if (idx === -1) return escapeHTML(wrapped);
        return escapeHTML(wrapped.slice(0, idx)) + makeBtn(wrapped.slice(idx, idx + rawTerm.length)) + escapeHTML(wrapped.slice(idx + rawTerm.length));
    }

    const letterRe = /[A-Za-zА-Яа-яЁё0-9]/;
    const lowerWrapped = wrapped.toLowerCase();
    const lowerTerm = rawTerm.toLowerCase();
    let searchPos = 0;
    while (searchPos < lowerWrapped.length) {
        const idx = lowerWrapped.indexOf(lowerTerm, searchPos);
        if (idx === -1) break;
        const prevCh = idx > 0 ? wrapped[idx - 1] : '';
        const nextCh = idx + rawTerm.length < wrapped.length ? wrapped[idx + rawTerm.length] : '';
        if (!letterRe.test(prevCh) && !letterRe.test(nextCh)) {
            return escapeHTML(wrapped.slice(0, idx))
                + makeBtn(wrapped.slice(idx, idx + rawTerm.length))
                + escapeHTML(wrapped.slice(idx + rawTerm.length));
        }
        searchPos = idx + lowerTerm.length;
    }
    return escapeHTML(wrapped);
};

window.toggleBlurChip = function(btn) {
    if (!btn) return;
    const nowMasked = btn.classList.contains('blur-chip-masked');
    btn.classList.toggle('blur-chip-masked', !nowMasked);
    btn.classList.toggle('blur-chip-open', nowMasked);
    if (typeof triggerHaptic === 'function') triggerHaptic('light');
};

window.toggleBlurToken = function(btn) {
    if (!btn) return;
    const nowMasked = btn.classList.contains('is-masked');
    btn.classList.toggle('is-masked', !nowMasked);
    btn.classList.toggle('is-revealed', nowMasked);
    if (typeof triggerHaptic === 'function') triggerHaptic('light');
};

window.toggleCardScaffold = function(containerOrId, triggerBtn) {
    const container = typeof containerOrId === 'string'
        ? document.getElementById(containerOrId)
        : (triggerBtn ? triggerBtn.closest('[data-blur-sec]') : null);
    if (!container) return;
    const chips = container.querySelectorAll('.blur-chip');
    const anyMasked = Array.from(chips).some(c => c.classList.contains('blur-chip-masked'));
    if (!anyMasked) {
        chips.forEach(c => {
            c.classList.add('blur-chip-masked');
            c.classList.remove('blur-chip-open');
        });
        container.classList.add('blur-container-scaffold');
    } else {
        container.classList.toggle('blur-container-scaffold');
    }
    if (triggerBtn) {
        triggerBtn.classList.toggle('is-active', container.classList.contains('blur-container-scaffold'));
    }
    if (typeof triggerHaptic === 'function') triggerHaptic('light');
};

window.toggleCardBlurScope = function(scopeOrId, triggerBtn) {
    const scope = typeof scopeOrId === 'string'
        ? document.getElementById(scopeOrId)
        : scopeOrId;
    if (!scope) return;
    const chips = scope.querySelectorAll('.blur-chip');
    const tokens = scope.querySelectorAll('.blur-token');
    const hasMasked = Array.from(chips).some(c => c.classList.contains('blur-chip-masked'))
        || Array.from(tokens).some(t => t.classList.contains('is-masked'));

    chips.forEach(c => {
        c.classList.toggle('blur-chip-masked', !hasMasked);
        c.classList.toggle('blur-chip-open', hasMasked);
    });
    tokens.forEach(t => {
        t.classList.toggle('is-masked', !hasMasked);
        t.classList.toggle('is-revealed', hasMasked);
    });
    if (triggerBtn) {
        triggerBtn.textContent = hasMasked ? 'Скрыть' : 'Открыть';
        triggerBtn.classList.toggle('is-active', hasMasked);
    }
    if (typeof triggerHaptic === 'function') triggerHaptic('light');
};


