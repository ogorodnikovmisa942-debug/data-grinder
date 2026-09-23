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


