// ============================================================================
// ИНИЦИАЛИЗАЦИЯ TELEGRAM MINI APP SDK И СБОР ТЕЛЕМЕТРИИ
// ============================================================================
let tgId = 'default_user'; 
if (window.Telegram && window.Telegram.WebApp) {
    const tg = window.Telegram.WebApp;
    tg.ready(); 
    tg.expand(); 
    tg.isVerticalSwipesEnabled = false;
    
    if (typeof tg.requestFullscreen === 'function') {
        tg.requestFullscreen();
    }
    
    tg.setHeaderColor('#fbfbfb'); 
    tg.setBackgroundColor('#fbfbfb');
    
    if (tg.initDataUnsafe && tg.initDataUnsafe.user) {
        tgId = tg.initDataUnsafe.user.id.toString();
    }

    const updateSafeArea = () => {
        const safeArea = tg.safeAreaInset || { top: 0, bottom: 0 };
        const contentSafeArea = tg.contentSafeAreaInset || { top: 0, bottom: 0 };
        
        const safeTop = Math.max(safeArea.top, contentSafeArea.top, 0);
        const safeBottom = Math.max(safeArea.bottom, contentSafeArea.bottom, 0);
        
        document.documentElement.style.setProperty('--tg-safe-top', `${safeTop}px`);
        document.documentElement.style.setProperty('--tg-safe-bottom', `${safeBottom}px`);
    };
    
    updateSafeArea();
    
    if (typeof tg.onEvent === 'function') {
        tg.onEvent('safeAreaChanged', updateSafeArea);
        tg.onEvent('contentSafeAreaChanged', updateSafeArea);
    }
}

// Универсальная обертка для HTTP-запросов с передачей авторизации Telegram
async function apiFetch(url, options = {}) {
    const opts = { ...options };
    opts.headers = { ...(opts.headers || {}) };
    
    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initData) {
        opts.headers['Authorization'] = `tma ${window.Telegram.WebApp.initData}`;
        opts.headers['X-Telegram-Init-Data'] = window.Telegram.WebApp.initData;
    }
    opts.headers['X-User-Id'] = tgId;
    
    return fetch(url, opts);
}

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

// Тактильный отклик (Haptic Feedback) для Telegram WebApp SDK
function triggerHaptic(type = 'light') {
    try {
        const haptic = window.Telegram?.WebApp?.HapticFeedback;
        if (!haptic) return;
        if (['light', 'medium', 'heavy', 'rigid', 'soft'].includes(type)) {
            haptic.impactOccurred(type);
        } else if (['error', 'success', 'warning'].includes(type)) {
            haptic.notificationOccurred(type);
        }
    } catch (e) {
        // Игнорируем вне среды Telegram
    }
}

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
        if (subject.startsWith('law_')) {
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

let trainDrag = { isDragging: false, hasMoved: false, startX: 0, startY: 0, currentX: 0, currentY: 0 };

function initTrainGestures() {
    const card = document.getElementById('flashcard');
    if (!card || card._train_gestures_bound) return;
    card._train_gestures_bound = true;

    const onStart = (clientX, clientY) => {
        const currentCard = cardsQueue[currentIndex];
        if (!currentCard || (currentCard.state === 0 && !currentCard.has_seen_intro)) return;
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
        onMove(touch.clientX, touch.clientY);
    }, { passive: true });

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
        cardFront.onclick = () => {
            if (trainDrag && trainDrag.hasMoved) return;
            const card = cardsQueue[currentIndex];
            if (!card) return;
            
            // НЕ переворачиваем, если карточка новая и не прошла знакомство
            if (card.state === 0 && !card.has_seen_intro) {
                return;
            }
            
            if (cardsQueue.length === 0 || isFlipped) return;
            isFlipped = true; 
            triggerHaptic('light');
            if (flashcard) flashcard.classList.add('rotate-y-180');
            if (actionButtons) { actionButtons.classList.remove('hidden'); actionButtons.classList.add('flex'); }
            executeVoiceSynthesis(cardsQueue[currentIndex].text);
            if (typeof window.startGlobalPomodoro === 'function') { window.startGlobalPomodoro(); }
        };
    }

    if (cardBack) {
        cardBack.onclick = (e) => {
            if (e.target.closest('button, select, input, textarea, a')) return; 
            if (trainDrag && trainDrag.hasMoved) return;
            if (cardsQueue.length === 0 || !isFlipped) return;
            isFlipped = false; 
            triggerHaptic('light');
            if (flashcard) flashcard.classList.remove('rotate-y-180');
            if (actionButtons) { actionButtons.classList.add('hidden'); actionButtons.classList.remove('flex'); }
        };
    }

    initTrainGestures();
}

document.addEventListener('DOMContentLoaded', async () => {
    bindDOMPointers();
    await loadDynamicSubjects(); 
    if (subjectSelector) subjectSelector.value = currentSubject;
    showSessionStarter(); initPomodoroEngine(); initNavigation();
    initArchiveFilters(); updateGlobalBadges();
    
    // Инициализация кнопки массового выбора
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
    
    // Привязываем обработчик отправки опроса
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
    if (typeof updateImportExplanation === 'function') { updateImportExplanation(); }
    if (typeof updateTariffBanner === 'function') { updateTariffBanner(); setInterval(updateTariffBanner, 60000); }
    if (typeof checkNightQueueStatus === 'function') { checkNightQueueStatus(); setInterval(checkNightQueueStatus, 30000); }
    if (typeof checkDeepLinkOrHash === 'function') { await checkDeepLinkOrHash(); }
    window.addEventListener('hashchange', () => {
        if (typeof checkDeepLinkOrHash === 'function') checkDeepLinkOrHash();
    });
    if (typeof updateAssocPreferenceUI === 'function') {
        updateAssocPreferenceUI(localStorage.getItem('assoc_preference') || 'acoustic');
    }
    if (typeof window.syncTimerWithServer === 'function') { await window.syncTimerWithServer(); }

    window.addEventListener('keydown', (e) => {
        if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement?.tagName)) return;
        if (currentTab !== 'train') return;
        if (cardsQueue.length === 0 || currentIndex >= cardsQueue.length) return;
        const card = cardsQueue[currentIndex];
        if (!card || (card.state === 0 && !card.has_seen_intro)) return;

        if (e.code === 'Space') {
            e.preventDefault();
            triggerHaptic('light');
            if (!isFlipped) {
                isFlipped = true;
                if (flashcard) flashcard.classList.add('rotate-y-180');
                if (actionButtons) { actionButtons.classList.remove('hidden'); actionButtons.classList.add('flex'); }
                executeVoiceSynthesis(card.text);
                if (typeof window.startGlobalPomodoro === 'function') { window.startGlobalPomodoro(); }
            } else {
                isFlipped = false;
                if (flashcard) flashcard.classList.remove('rotate-y-180');
                if (actionButtons) { actionButtons.classList.add('hidden'); actionButtons.classList.remove('flex'); }
            }
        } else if (['Digit1', 'Digit2', 'Digit3', 'Digit4', 'Numpad1', 'Numpad2', 'Numpad3', 'Numpad4'].includes(e.code)) {
            if (isFlipped) {
                e.preventDefault();
                const ratingMap = {
                    'Digit1': 1, 'Numpad1': 1,
                    'Digit2': 2, 'Numpad2': 2,
                    'Digit3': 3, 'Numpad3': 3,
                    'Digit4': 4, 'Numpad4': 4
                };
                if (typeof window.submitCardRating === 'function') {
                    window.submitCardRating(ratingMap[e.code]);
                }
            }
        }
    });
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

function showSurveyDirectly() {
    resetCardDOM();
    if (window.surveyCompletedToday) {
        const surveyContainer = document.getElementById('survey-container');
        if (surveyContainer) surveyContainer.classList.add('hidden');
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
    if (cardMainText) cardMainText.textContent = "Очередь пуста. Оцените параметры сессии:";
}

function resetCardDOM() {
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
}

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

window.exitToSessionMenu = function() {
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

async function startSession(mode) {
    currentSessionMode = mode;
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
}

async function fetchActiveSession(mode = 'mixed') {
    try {
        currentSessionMode = mode;
        const response = await apiFetch(`/api/session?subject=${currentSubject}&mode=${mode}`);
        cardsQueue = await response.json();
        shuffleArray(cardsQueue);
        const surveyContainer = document.getElementById('survey-container');
        if (cardsQueue.length === 0) {
            resetCardDOM();
            if (surveyContainer && !window.surveyCompletedToday) {
                surveyContainer.classList.remove('hidden');
                if (cardText) cardText.classList.add('hidden');
                if (cardCounter) cardCounter.classList.add('hidden');
            } else {
                if (surveyContainer) surveyContainer.classList.add('hidden');
                if (cardText) {
                    cardText.classList.remove('hidden');
                    cardText.textContent = mode === 'review' ? "Все повторено ✓" : (mode === 'new' ? "Все новые изучены ✓" : "Очередь пуста");
                }
            }
            if (cardSecondaryText) cardSecondaryText.textContent = "";
            if (cardMainText) {
                cardMainText.textContent = mode === 'review' 
                    ? "На данный момент нет карточек, требующих повторения." 
                    : (mode === 'new' 
                        ? "Вы изучили все новые карточки на сегодня или дневной лимит исчерпан." 
                        : "Все задачи решены.");
            }
            if (cardCounter) cardCounter.textContent = "";
            if (progressFill) progressFill.style.width = "100%"; 
            currentSessionCounters = { new: 0, learning: 0, review: 0 };
            renderTopCounters(); updateGlobalBadges(); return;
        }
        
        if (surveyContainer) {
            surveyContainer.classList.add('hidden');
            if (cardText) cardText.classList.remove('hidden');
            if (cardCounter) cardCounter.classList.remove('hidden');
        }
        
        currentIndex = 0; recalculateQueueCounters(); renderCurrentCard(); updateGlobalBadges();
    } catch (error) { console.error("[Data Grinder] Ошибка загрузки сессии:", error); }
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
            showSurveyDirectly();
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

function renderIntroductionCard(card) {
    isFlipped = false;
    if (flashcard) flashcard.classList.remove('rotate-y-180');
    
    // Скрываем кнопки FSRS
    if (actionButtons) {
        actionButtons.classList.add('hidden');
        actionButtons.classList.remove('flex');
    }
    
    // Переключаем контейнеры на лицевой стороне
    const normalFront = document.getElementById('card-front-normal');
    const introFront = document.getElementById('card-front-intro');
    const front = document.getElementById('card-front');
    
    if (normalFront) normalFront.classList.add('hidden');
    if (introFront) {
        introFront.classList.remove('hidden');
        introFront.classList.add('flex', 'flex-col', 'justify-between');
    }
    if (front) front.classList.add('introduction-mode');
    
    // 1. Заполняем намертво закрепленный заголовок термина (НИКОГДА НЕ СКРОЛЛИТСЯ)
    const isIntroCloze = card.content_type === 'cloze' || /\{\{c\d+::/.test(card.text);
    const termEl = document.getElementById('card-intro-term');
    const secEl = document.getElementById('card-intro-secondary');
    if (termEl) {
        if (isIntroCloze) {
            termEl.innerHTML = formatClozeHTML(card.text, (card.intro_phase || 0) === 1 ? false : true);
        } else {
            termEl.textContent = card.text;
        }
    }
    if (secEl) {
        if (card.secondary_text && card.secondary_text !== '---') {
            secEl.textContent = card.secondary_text;
            secEl.classList.remove('hidden');
        } else {
            secEl.classList.add('hidden');
        }
    }
    
    // 2. Форматирование мнемоники
    let mnemonicFormatted = '';
    if (card.mnemonic) {
        const m = card.mnemonic;
        mnemonicFormatted = typeof m === 'object' ? `<strong class="font-bold text-amber-700 dark:text-amber-300">${escapeHTML(m.keyword)}</strong>: ${escapeHTML(m.verbal_cue)}` : escapeHTML(m);
    }
    
    // Фаза: 0 - Обзор (Preview), 1 - Самопроверка (Recall)
    const phase = card.intro_phase || 0;
    const bodyEl = document.getElementById('card-intro-body');
    const footerEl = document.getElementById('card-intro-footer');
    
    if (phase === 0) {
        // ШАГ 1: КОМПАКТНЫЙ ОБЗОР (Вопрос + Определение + Мнемоника на одном экране!)
        if (bodyEl) {
            bodyEl.innerHTML = `
                <div class="w-full flex flex-col gap-2 animate-fade-in text-center">
                    <!-- Определение -->
                    <div class="bg-neutral-50 dark:bg-neutral-900/40 p-2.5 sm:p-3 rounded-xl border border-neutral-200/80 dark:border-neutral-800 text-left">
                        <span class="block text-[10px] text-neutral-400 dark:text-neutral-500 uppercase font-mono tracking-wider mb-1">ОПРЕДЕЛЕНИЕ</span>
                        <div class="text-sm sm:text-base text-neutral-900 dark:text-neutral-100 font-medium leading-relaxed break-words">
                            ${escapeHTML(card.translation)}
                        </div>
                    </div>
                    
                    <!-- Мнемоника / Ассоциация (если есть) -->
                    ${mnemonicFormatted ? `
                        <div class="bg-amber-500/5 dark:bg-amber-500/10 p-2.5 rounded-xl border border-amber-500/20 text-left">
                            <span class="block text-[10px] text-amber-600 dark:text-amber-400 uppercase font-bold tracking-wider mb-1 font-mono flex items-center gap-1">
                                <span class="material-symbols-outlined text-[13px]">psychology</span> АССОЦИАЦИЯ
                            </span>
                            <div class="text-xs sm:text-sm text-neutral-800 dark:text-neutral-200 leading-snug break-words">
                                ${mnemonicFormatted}
                            </div>
                        </div>
                    ` : ''}

                    <!-- Пример (если есть) -->
                    ${card.example && card.example !== '---' ? `
                        <div class="border-l-2 border-primary/50 pl-3 py-0.5 text-left">
                            <div class="text-xs text-neutral-500 dark:text-neutral-400 italic break-words">
                                «${escapeHTML(card.example)}»
                            </div>
                        </div>
                    ` : ''}
                </div>
            `;
        }
        
        if (footerEl) {
            footerEl.innerHTML = `
                <button onclick="event.stopPropagation(); advanceIntroduction()" 
                        class="w-full bg-primary text-on-primary py-2.5 rounded-xl font-bold tracking-wide hover:opacity-90 transition-all text-xs font-mono uppercase shadow-xs">
                    [→ ПРОВЕРИТЬ СЕБЯ В ПАМЯТИ]
                </button>
                <button onclick="event.stopPropagation(); window.fastTrackIntroduction()" 
                        class="w-full border border-neutral-200 dark:border-neutral-700 text-neutral-500 dark:text-neutral-400 hover:text-primary hover:border-primary py-2 rounded-xl font-bold tracking-wide transition-all text-[11px] font-mono uppercase flex items-center justify-center gap-1">
                    <span class="material-symbols-outlined text-[14px]">verified</span>
                    <span>[ЗНАЮ НАИЗУСТЬ]</span>
                </button>
            `;
        }
    } else {
        // ШАГ 2: САМОПРОВЕРКА (Вопрос на виду, проверяем память)
        if (!card._recall_checked) {
            if (bodyEl) {
                bodyEl.innerHTML = `
                    <div onclick="event.stopPropagation(); window.toggleIntroRecall()" 
                         class="w-full h-full flex flex-col items-center justify-center border-2 border-dashed border-primary/40 bg-primary/5 rounded-xl p-5 cursor-pointer hover:bg-primary/10 transition-all text-center animate-fade-in group">
                        <span class="material-symbols-outlined text-primary text-3xl mb-2 group-hover:scale-110 transition-transform">visibility</span>
                        <div class="text-xs font-mono font-bold text-primary uppercase">[ПОКАЗАТЬ ОТВЕТ И АССОЦИАЦИЮ]</div>
                        <div class="text-[11px] text-neutral-500 dark:text-neutral-400 mt-1.5 font-sans">Попробуйте воспроизвести значение по памяти</div>
                    </div>
                `;
            }
            if (footerEl) {
                footerEl.innerHTML = `
                    <button onclick="event.stopPropagation(); window.toggleIntroRecall()" 
                            class="w-full border border-primary text-primary py-2.5 rounded-xl font-bold tracking-wide hover:bg-primary/10 transition-all text-xs font-mono uppercase">
                        [ПОКАЗАТЬ ОТВЕТ]
                    </button>
                    <button onclick="event.stopPropagation(); window.fastTrackIntroduction()" 
                            class="w-full border border-neutral-200 dark:border-neutral-700 text-neutral-500 dark:text-neutral-400 hover:text-primary hover:border-primary py-2 rounded-xl font-bold tracking-wide transition-all text-[11px] font-mono uppercase flex items-center justify-center gap-1">
                        <span class="material-symbols-outlined text-[14px]">verified</span>
                        <span>[ЗНАЮ НАИЗУСТЬ]</span>
                    </button>
                `;
            }
        } else {
            // Ответ раскрыт после самопроверки
            if (bodyEl) {
                bodyEl.innerHTML = `
                    <div class="w-full flex flex-col gap-2 animate-fade-in text-center">
                        <div class="bg-neutral-50 dark:bg-neutral-900/40 p-2.5 sm:p-3 rounded-xl border border-neutral-200/80 dark:border-neutral-800 text-left">
                            <span class="block text-[10px] text-neutral-400 dark:text-neutral-500 uppercase font-mono tracking-wider mb-1">ОПРЕДЕЛЕНИЕ</span>
                            <div class="text-sm sm:text-base text-neutral-900 dark:text-neutral-100 font-medium leading-relaxed break-words">
                                ${escapeHTML(card.translation)}
                            </div>
                        </div>
                        ${mnemonicFormatted ? `
                            <div class="bg-amber-500/5 dark:bg-amber-500/10 p-2.5 rounded-xl border border-amber-500/20 text-left">
                                <span class="block text-[10px] text-amber-600 dark:text-amber-400 uppercase font-bold tracking-wider mb-1 font-mono flex items-center gap-1">
                                    <span class="material-symbols-outlined text-[13px]">psychology</span> АССОЦИАЦИЯ
                                </span>
                                <div class="text-xs sm:text-sm text-neutral-800 dark:text-neutral-200 leading-snug break-words">
                                    ${mnemonicFormatted}
                                </div>
                            </div>
                        ` : ''}
                    </div>
                `;
            }
            if (footerEl) {
                footerEl.innerHTML = `
                    <button onclick="event.stopPropagation(); completeIntroduction()" 
                            class="w-full bg-primary text-on-primary py-2.5 rounded-xl font-bold tracking-wide hover:opacity-90 transition-all text-xs font-mono uppercase shadow-xs">
                        [✓ ВСПОМНИЛ И ЗАКРЕПИЛ, НАЧАТЬ УЧИТЬ]
                    </button>
                    <button onclick="event.stopPropagation(); stepBackIntroduction()" 
                            class="w-full border border-neutral-200 dark:border-neutral-700 text-neutral-500 dark:text-neutral-400 hover:text-primary hover:border-primary py-2 rounded-xl font-bold tracking-wide transition-all text-[11px] font-mono uppercase">
                        [← ВЕРНУТЬСЯ К ОБЗОРУ]
                    </button>
                `;
            }
        }
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

function completeIntroduction() {
    const card = cardsQueue[currentIndex];
    triggerHaptic('success');
    card.has_seen_intro = true;
    card.state = 1; // Learning
    
    const responseTimeMs = cardShowTimestamp ? (Date.now() - cardShowTimestamp) : 0;
    apiFetch('/api/answer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            card_id: card.id,
            rating: 3, // Good
            response_time: responseTimeMs,
            is_introduction: true
        })
    }).then(res => { if (res.ok) updateGlobalBadges(); })
      .catch(err => console.error("Ошибка синхронизации ознакомления:", err));
    
    currentIndex++;
    recalculateQueueCounters();
    renderCurrentCard();
}

window.fastTrackIntroduction = function() {
    const card = cardsQueue[currentIndex];
    if (!card) return;
    card.has_seen_intro = true;
    card.state = 2; // Сразу в Review
    
    const responseTimeMs = cardShowTimestamp ? (Date.now() - cardShowTimestamp) : 0;
    apiFetch('/api/answer', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            card_id: card.id,
            rating: 4, // Easy
            response_time: responseTimeMs,
            is_introduction: true,
            is_fast_track: true
        })
    }).then(res => { if (res.ok) updateGlobalBadges(); })
      .catch(err => console.error("Ошибка fast-track синхронизации:", err));
    
    currentIndex++;
    recalculateQueueCounters();
    renderCurrentCard();
};

function renderReviewCard(card) {
    isFlipped = false;
    if (flashcard) flashcard.classList.remove('rotate-y-180'); 
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

    // Динамический бейдж режима на лицевой стороне
    const modeBadge = document.getElementById('card-front-mode-badge');
    const modeText = document.getElementById('card-front-mode-text');
    if (modeText) {
        if (isCloze) {
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
    
    if (cardText) {
        if (isCloze) {
            cardText.innerHTML = formatClozeHTML(card.text, false);
            cardText.classList.remove('text-2xl', 'text-3xl');
            cardText.classList.add('text-base', 'sm:text-lg', 'text-left', 'leading-relaxed');
        } else {
            cardText.textContent = card.text;
            cardText.classList.remove('text-base', 'sm:text-lg', 'text-left', 'leading-relaxed');
        }
    }
    
    const hintEl = document.getElementById('card-front-hint');
    if (hintEl) {
        if (card.secondary_text && card.secondary_text !== '---') {
            hintEl.textContent = card.secondary_text;
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
        if (secContainer && cardSecondaryText) {
            if (card.secondary_text && card.secondary_text !== '---') {
                cardSecondaryText.textContent = card.secondary_text;
                secContainer.classList.remove('hidden');
            } else {
                secContainer.classList.add('hidden');
            }
        }
        
        if (cardMainText) cardMainText.textContent = card.translation; 
        
        const exampleContainer = document.getElementById('card-example-container');
        const exampleText = document.getElementById('card-example-text');
        if (exampleContainer && exampleText) {
            if (card.example && card.example.trim() && card.example !== '---') {
                exampleText.textContent = `«${card.example.trim()}»`;
                exampleContainer.classList.remove('hidden');
            } else {
                exampleContainer.classList.add('hidden');
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
                if (leechText) leechText.textContent = `[!] СЛОЖНАЯ КАРТА (СБОЕВ: ${card.lapses || 4})`;
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

window.submitCardRating = function(rating) {
    if (cardsQueue.length === 0 || currentIndex >= cardsQueue.length) return;
    const currentCard = cardsQueue[currentIndex];
    if (!currentCard) return;

    if (rating === 1) {
        triggerHaptic('warning');
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

    apiFetch('/api/answer', {
        method: 'POST', 
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
            card_id: payloadCardId, 
            rating: rating,
            response_time: responseTimeMs,
            has_association: hasAssoc,
            is_cram: currentSessionMode === 'cram'
        })
    }).then(res => { if (res.ok) updateGlobalBadges(); })
      .catch(err => console.error("[Data Grinder] Фоновая ошибка синхронизации:", err));
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
        const res = await apiFetch(`/api/stats/dashboard?subject=${currentSubject}`); const data = await res.json();
        window.surveyCompletedToday = data.survey_completed || false;
        window.eveningDueCount = data.due_evening || 0;
        
        const totalCards = data.cards_new + data.cards_learning + data.cards_review;
        const dueCount = cardsQueue.length - currentIndex;
        
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
            btnReview.className = "w-full flex items-center justify-between px-4 border-2 border-secondary text-secondary py-2.5 font-bold tracking-wide hover:bg-secondary hover:text-white transition-all text-xs font-mono uppercase rounded-xl shadow-md";
        } else {
            btnReviewText.textContent = "[ ПОВТОРЕНИЕ ]";
            btnReviewBadge.textContent = "0 (ВСЕ ПОВТОРЕНО ✓)";
            btnReviewBadge.className = "px-2 py-0.5 rounded-md text-[10px] font-bold bg-neutral-100 dark:bg-neutral-800 text-neutral-400";
            btnReview.className = "w-full flex items-center justify-between px-4 border border-neutral-200 dark:border-neutral-800 text-neutral-400 py-2.5 font-bold tracking-wide transition-all text-xs font-mono uppercase rounded-xl opacity-75";
        }
    }

    // 2. Учить новое (new_remaining_today с учетом дневного лимита)
    const newRemaining = data.new_remaining_today !== undefined ? data.new_remaining_today : data.cards_new;
    const dailyLimit = data.daily_new_limit || 20;
    if (btnNew && btnNewBadge && btnNewText) {
        if (newRemaining > 0) {
            btnNewText.textContent = "[ УЧИТЬ НОВОЕ ]";
            btnNewBadge.textContent = `${newRemaining} ИЗ ${dailyLimit}`;
            btnNewBadge.className = "px-2 py-0.5 rounded-md text-[10px] font-bold bg-primary/10 text-primary border border-primary/20";
            btnNew.className = "w-full flex items-center justify-between px-4 border border-primary text-primary py-2.5 font-bold tracking-wide hover:bg-primary hover:text-on-primary transition-all text-xs font-mono uppercase rounded-xl shadow-xs";
        } else {
            btnNewText.textContent = "[ УЧИТЬ НОВОЕ ]";
            btnNewBadge.textContent = "ЛИМИТ ИСЧЕРПАН ✓";
            btnNewBadge.className = "px-2 py-0.5 rounded-md text-[10px] font-bold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20";
            btnNew.className = "w-full flex items-center justify-between px-4 border border-neutral-200 dark:border-neutral-800 text-neutral-400 py-2.5 font-bold tracking-wide transition-all text-xs font-mono uppercase rounded-xl opacity-75";
        }
    }

    // 3. Штурм
    const totalDeck = data.total_cards !== undefined ? data.total_cards : (data.cards_new + data.cards_learning + data.cards_review);
    if (btnCramText && btnCramBadge) {
        btnCramText.textContent = "[ ШТУРМ ]";
        btnCramBadge.textContent = `${totalDeck} КАРТ`;
    }
}

function initNavigation() {
    const navButtons = document.querySelectorAll('.nav-link');
    const focusToggleBtn = document.getElementById('focus-toggle');
    
    navButtons.forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.preventDefault(); 
            const targetTab = btn.getAttribute('data-tab'); 
            currentTab = targetTab;
            
            if (targetTab !== 'train') {
                body.classList.remove('focus-active');
            }

            if (focusToggleBtn) {
                if (targetTab === 'train') {
                    focusToggleBtn.classList.remove('hidden');
                } else {
                    focusToggleBtn.classList.add('hidden');
                }
            }
            
            navButtons.forEach(b => { 
                b.classList.remove('text-primary'); 
                b.classList.add('text-outline'); 
            });
            btn.classList.remove('text-outline');
            btn.classList.add('text-primary');
            
            document.querySelectorAll('.app-screen').forEach(screen => { 
                screen.classList.add('hidden'); 
                screen.classList.remove('flex-1', 'flex', 'flex-col'); 
            });
            
            const targetScreen = document.getElementById(`screen-${targetTab}`);
            if (targetScreen) {
                targetScreen.classList.remove('hidden');
                targetScreen.classList.add('flex-1', 'flex', 'flex-col'); 
            }
            
            if (targetTab === 'data') loadDataTab(); 
            if (targetTab === 'stats') loadStatsTab(); 
            if (targetTab === 'config') loadConfigTab();
        });
    });
}

async function loadDataTab() {
    const container = document.getElementById('data-container'); 
    if (container) container.innerHTML = '<div class="text-sm font-mono text-outline py-md">Загрузка архива...</div>';
    try {
        const res = await apiFetch(`/api/data/cards?subject=${currentSubject}`); const data = await res.json();
        localCardsArchive = data.cards; renderFilteredArchiveDOM();
    } catch (e) { if (container) container.innerHTML = '<div class="text-sm font-mono text-error py-md">Ошибка архива</div>'; }
}

function initArchiveFilters() {
    const filterButtons = document.querySelectorAll('#archive-filter-bar button');
    filterButtons.forEach(btn => {
        btn.addEventListener('click', () => {
            filterButtons.forEach(b => { b.className = "px-xs py-0.5 text-outline hover:text-primary border border-transparent"; });
            btn.className = "px-xs py-0.5 bg-primary text-on-primary border border-primary";
            currentDataFilter = btn.getAttribute('data-filter'); renderFilteredArchiveDOM();
        });
    });
}

// РЕНДЕРИНГ СТРОКИ АРХИВА С ДОБАВЛЕНИЕМ КНОПКИ МИГРАЦИИ, РЕДАКТИРОВАНИЯ И ЧЕКБОКСОВ
function renderFilteredArchiveDOM() {
    const container = document.getElementById('data-container');
    if (!container) return;
    const filtered = localCardsArchive.filter(c => {
        if (currentDataFilter === 'all') return true; if (currentDataFilter === 'new') return c.state === 0; if (currentDataFilter === 'review') return c.state > 0; return true;
    });
    
    const totalCount = localCardsArchive ? localCardsArchive.length : 0;
    const filteredCount = filtered ? filtered.length : 0;
    const countBadge = document.getElementById('archive-count');
    if (countBadge) {
        if (filteredCount === totalCount) {
            countBadge.innerText = `(${totalCount})`;
        } else {
            countBadge.innerText = `(${filteredCount}/${totalCount})`;
        }
    }
    
    if (filtered.length === 0) { container.innerHTML = '<div class="text-sm font-mono text-outline py-md text-center">Категория пуста</div>'; return; }
    
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
        const shareSubBtn = document.getElementById('btn-share-subject');
        if (shareSubBtn) {
            if (currentSubject === 'all') shareSubBtn.classList.add('hidden');
            else shareSubBtn.classList.remove('hidden');
        }
        const deleteSubBtn = document.getElementById('btn-delete-subject');
        if (deleteSubBtn) {
            if (currentSubject === 'all') deleteSubBtn.classList.add('hidden');
            else deleteSubBtn.classList.remove('hidden');
        }
        const renameSubBtn = document.getElementById('btn-rename-subject');
        if (renameSubBtn) {
            if (currentSubject === 'all') renameSubBtn.classList.add('hidden');
            else renameSubBtn.classList.remove('hidden');
        }
        const presetContainer = document.getElementById('config-presets-container');
        const presetNotice = document.getElementById('config-presets-notice');
        if (currentSubject === 'all') {
            if (presetContainer) presetContainer.classList.add('hidden');
            if (presetNotice) presetNotice.classList.remove('hidden');
        } else {
            if (presetContainer) presetContainer.classList.remove('hidden');
            if (presetNotice) presetNotice.classList.add('hidden');
            const res = await apiFetch(`/api/config?subject=${currentSubject}`); const data = await res.json();
            renderPresetButtonsDOM(data.daily_limit);
        }
    } catch (e) { console.error("Ошибка загрузки конфига:", e); }
}

function renderPresetButtonsDOM(activeLimit) {
    [10, 20, 30, 10000].forEach(val => {
        const btn = document.getElementById(`btn-preset-${val}`);
        if (btn) {
            if (val === activeLimit) {
                btn.className = "w-full text-left border p-md transition-all duration-75 flex justify-between items-center bg-primary text-on-primary border-primary font-mono text-xs font-bold uppercase rounded-xl shadow-xs";
            } else {
                btn.className = "w-full text-left border p-md transition-all duration-75 flex justify-between items-center bg-surface-container-lowest text-primary border-neutral-300 dark:border-neutral-700 font-mono text-xs uppercase rounded-xl shadow-xs hover:border-primary";
            }
        }
    });
}

async function setIntensityPreset(limit) {
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
            'python_pro': 'PYTHON ADVANCED', 
            'geometry': 'ГЕОМЕТРИЯ (ФОРМУЛЫ)', 
            'law_civil_rb': 'ГРАЖДАНСКОЕ ПРАВО РБ' 
        };

        // 1. Селекторы фильтрации карточек/тренировок
        const selectors = document.querySelectorAll('#subject-selector');
        selectors.forEach(sel => {
            sel.innerHTML = '<option value="all">[ВСЕ ПРЕДМЕТЫ]</option>';
            subjects.forEach(sub => {
                const option = document.createElement('option'); 
                option.value = sub;
                option.textContent = `[${subjectNames[sub] || sub.toUpperCase()}]`; 
                sel.appendChild(option);
            });
        });

        // 2. Селектор целевого предмета в панели импорта
        const importSel = document.getElementById('import-target-subject');
        if (importSel) {
            const currentVal = importSel.value;
            importSel.innerHTML = '<option value="" disabled selected>-- ВЫБЕРИТЕ ПРЕДМЕТ --</option>';
            subjects.forEach(sub => {
                const opt = document.createElement('option');
                opt.value = sub;
                opt.textContent = `[${subjectNames[sub] || sub.toUpperCase()}]`;
                importSel.appendChild(opt);
            });
            const newOpt = document.createElement('option');
            newOpt.value = '__new__';
            newOpt.textContent = '[+ СОЗДАТЬ НОВЫЙ ПРЕДМЕТ...]';
            importSel.appendChild(newOpt);

            const tipEl = document.getElementById('subject-status-tip');
            const inputNew = document.getElementById('import-new-subject-input');
            if (currentVal && (subjects.includes(currentVal) || currentVal === '__new__')) {
                importSel.value = currentVal;
                if (tipEl) tipEl.textContent = currentVal === '__new__' ? '[НОВЫЙ ПРЕДМЕТ]' : `[ВЫБРАН: ${currentVal.toUpperCase()}]`;
            } else if (currentSubject && currentSubject !== 'all' && subjects.includes(currentSubject)) {
                importSel.value = currentSubject;
                if (tipEl) tipEl.textContent = `[ВЫБРАН: ${currentSubject.toUpperCase()}]`;
                if (inputNew) inputNew.classList.add('hidden');
            } else if (subjects.length > 0) {
                importSel.value = subjects[0];
                if (tipEl) tipEl.textContent = `[ВЫБРАН: ${subjects[0].toUpperCase()}]`;
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
                const opt = document.createElement('option');
                opt.value = sub;
                opt.textContent = `[${subjectNames[sub] || sub.toUpperCase()}]`;
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

const VOLUME_SLIDER_STEPS = [
    { value: 'auto', label: '[АВТО] High-Yield (Парето)', desc: '1–2 золотые карты / стр (защита от перегрузки)' },
    { value: 'low_5', label: '5 карт / блок (минимум)', desc: 'до 5 карт на блок' },
    { value: 'med_10', label: '10 карт / блок (сжато)', desc: 'до 10 карт на блок' },
    { value: 'med_15', label: '15 карт / блок (стандарт)', desc: 'до 15 карт на блок' },
    { value: 'high_20', label: '20 карт / блок (подробно)', desc: 'до 20 карт на блок' },
    { value: 'max', label: 'МАКСИМУМ (все данные)', desc: 'все ключевые термины' }
];

window.setGranularityMode = function(mode) {
    currentGranularityMode = mode;
    ['atomic', 'detailed', 'blitz'].forEach(m => {
        const el = document.getElementById(`gran-${m}`);
        if (el) {
            if (m === mode) {
                el.className = 'border border-primary bg-primary text-on-primary py-2 px-1 text-[10px] font-bold transition-all flex flex-col items-center justify-center rounded-xl shadow-xs';
            } else {
                el.className = 'border border-neutral-300 dark:border-neutral-700 text-neutral-600 dark:text-neutral-300 hover:border-primary hover:text-primary py-2 px-1 text-[10px] font-bold transition-all flex flex-col items-center justify-center rounded-xl shadow-xs';
            }
        }
    });

    if (mode === 'detailed') {
        currentDetailDensity = 'high';
        currentVolumeLimit = 'auto';
    } else if (mode === 'blitz' || mode === 'cheatsheet') {
        currentDetailDensity = 'low';
        currentVolumeLimit = 'med_10';
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

window.onVolumeSliderChange = function(sliderVal) {
    const idx = parseInt(sliderVal, 10);
    const step = VOLUME_SLIDER_STEPS[idx] || VOLUME_SLIDER_STEPS[0];
    currentVolumeLimit = step.value;
    updateImportExplanation();
};

window.setVolumeLimit = function(vol) {
    currentVolumeLimit = vol;
    updateImportExplanation();
};

window.setDetailDensity = function(density) {
    currentDetailDensity = density;
    updateImportExplanation();
};

function updateImportExplanation() {
    const explEl = document.getElementById('import-mode-explanation');
    if (!explEl) return;

    if (currentGranularityMode === 'detailed') {
        explEl.textContent = 'Глубокий разбор: больше точечных микро-карточек по всем нюансам и исключениям (каждое условие — в отдельную карточку).';
    } else if (currentGranularityMode === 'blitz' || currentGranularityMode === 'cheatsheet') {
        explEl.textContent = 'Экспресс-блиц: 5–10 самых фундаментальных основ в предельно сжатых карточках.';
    } else {
        explEl.textContent = 'Баланс High-Yield FSRS: ~1–2 золотые карточки на страницу, защита от перегрузки колоды (отклик 1.5–3.5 сек).';
    }
}

function isOffPeakWindow() {
    const now = new Date();
    const utcMinutes = now.getUTCHours() * 60 + now.getUTCMinutes();
    return utcMinutes >= 990 || utcMinutes < 30; // 16:30 - 00:30 UTC / 19:30 - 03:30 MSK
}

window.updateTariffBanner = function() {
    const bannerTitle = document.getElementById('tariff-status-title');
    const bannerBadge = document.getElementById('tariff-status-badge');
    const btnDeferred = document.getElementById('btn-import-deferred');
    if (!bannerBadge) return;

    const isOffPeak = isOffPeakWindow();
    if (isOffPeak) {
        if (bannerTitle) bannerTitle.innerHTML = `МОДЕЛЬ: <span class="text-primary font-bold">DEEPSEEK V3</span>`;
        bannerBadge.className = "text-emerald-600 dark:text-emerald-400 font-bold font-mono animate-pulse";
        bannerBadge.textContent = "[НОЧНОЙ ТАРИФ -50% АКТИВЕН]";
        if (btnDeferred) {
            btnDeferred.innerHTML = `<span class="material-symbols-outlined text-[15px]">dark_mode</span><span>СКИДКА -50% (СЕЙЧАС)</span>`;
        }
    } else {
        const now = new Date();
        const utcMinutes = now.getUTCHours() * 60 + now.getUTCMinutes();
        let diffMinutes = 990 - utcMinutes;
        if (diffMinutes < 0) diffMinutes += 1440;
        const h = Math.floor(diffMinutes / 60);
        const m = diffMinutes % 60;

        if (bannerTitle) bannerTitle.innerHTML = `МОДЕЛЬ: <span class="text-primary font-bold">DEEPSEEK V3</span>`;
        bannerBadge.className = "text-secondary font-bold font-mono";
        bannerBadge.textContent = `[СКИДКА 50% ЧЕРЕЗ ${h}ч ${m}м]`;
        if (btnDeferred) {
            btnDeferred.innerHTML = `<span class="material-symbols-outlined text-[15px]">dark_mode</span><span>НОЧЬЮ (-50%)</span>`;
        }
    }
};

window.currentStagingJobId = null;

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
                                <div class="truncate max-w-[60%]">
                                    <span class="font-bold text-emerald-700 dark:text-emerald-300">✓ ${escapeHTML(j.theme || 'Материал')}</span>
                                    <span class="text-[9px] text-neutral-600 dark:text-neutral-400 block font-sans">${j.cards_count} карт. готовы к разбору</span>
                                </div>
                                <button onclick="openStagingJob(${j.id})" class="px-2.5 py-1 bg-emerald-600 hover:bg-emerald-700 text-white rounded text-[9px] font-bold uppercase transition-all shadow-sm flex items-center gap-1 font-mono">
                                    <span class="material-symbols-outlined text-[13px]">style</span>
                                    <span>[РАЗОБРАТЬ]</span>
                                </button>
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

async function importTextKnowledge(isDeferred = false) {
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

    try {
        const response = await apiFetch('/api/config/import', {
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
                is_deferred: isDeferred
            })
        });
        let keepActiveBar = false;
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
            alert("Ошибка создания карточек: " + (data.message || data.detail || "Неизвестный сбой.")); 
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
        // Задача отложена на скидочное время (19:30 МСК)
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

    // Если сейчас УЖЕ действует ночная скидка (19:30 - 03:30 МСК), сразу генерируем со скидкой 50%!
    // Предлагаем отложить в очередь ТОЛЬКО в дневные часы, чтобы пользователь мог сэкономить 50%.
    const isOffPeak = isOffPeakWindow();
    let isDeferred = false;
    if (!isOffPeak && files.length > 0) {
        isDeferred = confirm(
            `Документ: ${fileName} (${fileSizeMb} МБ)\n\n` +
            `Сейчас действует стандартный дневной тариф.\n` +
            `Поставить в очередь «Ночной Грайнд» со скидкой 50% (обработка в 19:30 МСК)?\n\n` +
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
        const response = await apiFetch('/api/config/import/file', {
            method: 'POST',
            signal: activeImportAbortController ? activeImportAbortController.signal : undefined,
            body: formData
        });

        if (response.status === 413) {
            alert(`Файл слишком большой для веб-сервера (${fileSizeMb} МБ). Nginx ограничил размер загрузки. Рекомендуем разбить документ по главам.`);
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
        alert("Движок Tesseract OCR ещё загружается. Подождите пару секунд и повторите.");
        if (statusEl) statusEl.classList.add('hidden');
        event.target.value = '';
        return;
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
            statusEl.textContent = `[✓ РАСПОЗНАНО ${recognizedPages.length} ИЗ ${totalFiles} ФОТО (${combinedText.length} ЗНАКОВ)]`;
            statusEl.className = "text-[10px] font-mono text-center text-primary font-bold py-1.5 bg-neutral-100 dark:bg-neutral-800 rounded-xl px-2 border border-neutral-300 dark:border-neutral-700 block";
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

const STAGING_CARD_TEMPLATE = `
    <div id="staging-badge-accept" class="absolute top-4 right-4 border-2 border-primary text-primary px-3 py-1 font-bold text-xs uppercase tracking-widest rotate-12 opacity-0 pointer-events-none transition-opacity rounded-xl bg-primary/10">
        [✓ ОДОБРЕНО]
    </div>
    <div id="staging-badge-reject" class="absolute top-4 left-4 border-2 border-secondary text-secondary px-3 py-1 font-bold text-xs uppercase tracking-widest -rotate-12 opacity-0 pointer-events-none transition-opacity rounded-xl bg-secondary/10">
        [✕ ОТКЛОНЕНО]
    </div>

    <div class="w-full flex justify-between items-center text-[10px] text-outline border-b border-outline-variant/30 pb-xs">
        <span id="staging-card-number">Карточка 1 из 1</span>
        <span id="staging-card-tier" class="uppercase font-bold text-primary">medium</span>
    </div>

    <div class="flex-1 flex flex-col justify-center items-center text-center gap-sm overflow-y-auto my-auto py-sm">
        <div id="staging-card-text" class="text-2xl sm:text-3xl font-bold text-primary break-words">...</div>
        <div id="staging-card-secondary" class="text-xs text-outline font-mono">...</div>
        <div class="w-16 h-px bg-outline-variant/40 my-xs"></div>
        <div id="staging-card-translation" class="text-sm sm:text-base text-on-surface leading-relaxed break-words">...</div>
        <div id="staging-card-example" class="text-[11px] text-outline italic mt-xs max-h-16 overflow-hidden">...</div>
        
        <div id="staging-card-mnemonic-box" class="w-full mt-xs bg-surface p-2.5 border border-outline-variant/30 rounded-xl text-[10px] text-left hidden shadow-xs">
            <span class="text-primary font-bold uppercase block mb-0.5">Ассоциация:</span>
            <span id="staging-card-mnemonic" class="text-on-surface-variant"></span>
        </div>
    </div>

    <div class="w-full grid grid-cols-2 gap-xs pt-xs border-t border-outline-variant/30 shrink-0">
        <button onclick="openStagingEditor()" class="border border-outline-variant hover:border-primary text-primary text-[10px] py-1.5 uppercase font-bold rounded-xl hover:bg-surface-container transition-all">
            [ПРАВИТЬ]
        </button>
        <button onclick="regenerateStagingMnemonic()" class="border border-outline-variant hover:border-primary text-primary text-[10px] py-1.5 uppercase font-bold rounded-xl hover:bg-surface-container transition-all">
            [МНЕМОНИКА]
        </button>
    </div>
`;

function startStagingSession(data) {
    stagingCards = (data.cards || []).map((c, idx) => ({ ...c, _orig_idx: idx }));
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
                    <button onclick="commitApprovedStagingCards()" class="w-full border border-primary bg-primary text-on-primary py-sm font-bold uppercase text-xs hover:bg-transparent hover:text-primary transition-all mt-sm rounded-xl shadow-xs">
                        [СОХРАНИТЬ В БАЗУ ДАННЫХ (${approvedStagingCards.length})]
                    </button>
                    <div class="flex gap-2 w-full mt-2">
                        <button onclick="stagingUndo()" class="flex-1 border border-outline-variant text-outline hover:text-primary py-2 font-bold uppercase text-[10px] rounded-xl transition-all flex items-center justify-center gap-1">
                            <span class="material-symbols-outlined text-[13px]">undo</span>
                            <span>ВЕРНУТЬ КАРТУ</span>
                        </button>
                        <button onclick="stagingResetSession()" class="flex-1 border border-outline-variant text-outline hover:text-secondary py-2 font-bold uppercase text-[10px] rounded-xl transition-all">
                            [СБРОСИТЬ]
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
            cardEl._gestures_bound = false;
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
    if (textEl) {
        if (card.content_type === 'cloze' || /\{\{c\d+::/.test(card.text)) {
            textEl.innerHTML = formatClozeHTML(card.text, false);
        } else {
            textEl.textContent = card.text || '---';
        }
    }
    if (secEl) secEl.textContent = card.secondary_text || '';
    if (transEl) transEl.textContent = card.translation || '---';
    if (exEl) {
        if (card.example) {
            exEl.textContent = `Пример: ${card.example}`;
            exEl.classList.remove('hidden');
        } else {
            exEl.classList.add('hidden');
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
            cards: approvedStagingCards
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

let stagingDrag = { isDragging: false, startX: 0, startY: 0, currentX: 0, currentY: 0 };

function initStagingGestures() {
    const card = document.getElementById('staging-card');
    if (!card || card._gestures_bound) return;
    card._gestures_bound = true;

    const onStart = (clientX, clientY) => {
        stagingDrag.isDragging = true;
        stagingDrag.startX = clientX;
        stagingDrag.startY = clientY;
        stagingDrag.currentX = clientX;
        stagingDrag.currentY = clientY;
        card.style.transition = 'none';
    };

    const onMove = (clientX, clientY) => {
        if (!stagingDrag.isDragging) return;
        stagingDrag.currentX = clientX;
        stagingDrag.currentY = clientY;
        const deltaX = clientX - stagingDrag.startX;
        const deltaY = clientY - stagingDrag.startY;

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
    };

    const onEnd = () => {
        if (!stagingDrag.isDragging) return;
        stagingDrag.isDragging = false;
        const deltaX = stagingDrag.currentX - stagingDrag.startX;

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
    };

    card.addEventListener('touchstart', (e) => {
        if (e.target.closest('button')) return;
        const t = e.touches[0];
        onStart(t.clientX, t.clientY);
    }, { passive: true });

    window.addEventListener('touchmove', (e) => {
        if (!stagingDrag.isDragging) return;
        const t = e.touches[0];
        onMove(t.clientX, t.clientY);
    }, { passive: true });

    window.addEventListener('touchend', () => {
        if (stagingDrag.isDragging) onEnd();
    });

    card.addEventListener('mousedown', (e) => {
        if (e.target.closest('button')) return;
        onStart(e.clientX, e.clientY);
    });

    window.addEventListener('mousemove', (e) => {
        if (!stagingDrag.isDragging) return;
        onMove(e.clientX, e.clientY);
    });

    window.addEventListener('mouseup', () => {
        if (stagingDrag.isDragging) onEnd();
    });
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
        stagingCards[currentStagingIndex].secondary_text = secondary;
        stagingCards[currentStagingIndex].translation = translation;
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
    if (oldSub === newSub) {
        closeSubjectRenameModal();
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
            
            if (currentSubject === oldSub) {
                currentSubject = newSub;
                localStorage.setItem('selected_subject', currentSubject);
            }
            
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
            alert("Отчет сессии успешно сохранен для аналитики FSRS!");
            window.surveyCompletedToday = true;
            
            const surveyContainer = document.getElementById('survey-container');
            const cardTextEl = document.getElementById('card-text');
            const cardCounterEl = document.getElementById('card-counter');
            
            if (surveyContainer) surveyContainer.classList.add('hidden');
            if (cardTextEl) {
                cardTextEl.classList.remove('hidden');
                cardTextEl.textContent = "Очередь пуста";
            }
            if (cardCounterEl) cardCounterEl.classList.remove('hidden');
            
            if (cardSecondaryText) cardSecondaryText.textContent = "";
            if (cardMainText) cardMainText.textContent = "Все задачи решены. Опрос завершен.";
            
            cardsQueue = [];
            currentIndex = 0;
            recalculateQueueCounters();
            updateGlobalBadges();
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

let currentKgSubject = 'sudoustroystvo';
let currentKgGraphData = null;
let currentKgTreeData = null;
let currentKgView = 'tree'; // 'tree' | 'graph'
let currentForceGraphInstance = null;

// Цвета категорий узлов (Neon Dark Cyber theme)
const KG_CATEGORY_COLORS = {
    'authority': '#f59e0b',     // Amber / Gold
    'instance': '#06b6d4',      // Cyan
    'condition': '#10b981',     // Emerald
    'exception': '#f43f5e',     // Rose
    'legal_status': '#a855f7',  // Violet / Purple
    'default': '#3b82f6'        // Blue
};

const KG_CATEGORY_NAMES = {
    'authority': 'Орган / Компетенция',
    'instance': 'Инстанция / Звено',
    'condition': 'Условие / Основание',
    'exception': 'Исключение / Изъятие',
    'legal_status': 'Правовой статус'
};

function getKgNodeColor(category) {
    return KG_CATEGORY_COLORS[category] || KG_CATEGORY_COLORS['default'];
}

window.openKnowledgeGraphModal = function(targetSubject) {
    const sub = targetSubject || (currentSubject && currentSubject !== 'all' ? currentSubject : 'sudoustroystvo');
    currentKgSubject = sub;

    const modal = document.getElementById('knowledge-graph-modal');
    if (!modal) return;
    
    modal.classList.remove('hidden');

    const badge = document.getElementById('kg-subject-badge');
    if (badge) badge.textContent = sub.toUpperCase();

    // Default to tree view initially
    switchKgView('tree');
    loadKnowledgeGraph(sub);
};

window.closeKnowledgeGraphModal = function() {
    const modal = document.getElementById('knowledge-graph-modal');
    if (modal) modal.classList.add('hidden');
    closeKgNodeDrawer();
};

window.switchKgView = function(viewType) {
    currentKgView = viewType;
    const treeView = document.getElementById('kg-tree-view');
    const graphView = document.getElementById('kg-graph-view');
    const tabTree = document.getElementById('kg-tab-tree');
    const tabGraph = document.getElementById('kg-tab-graph');

    if (viewType === 'tree') {
        if (treeView) treeView.classList.remove('hidden');
        if (graphView) graphView.classList.add('hidden');
        if (tabTree) {
            tabTree.className = "px-3 py-1 text-xs font-mono font-bold uppercase rounded-lg transition-all bg-primary text-on-primary shadow-xs flex items-center gap-1";
        }
        if (tabGraph) {
            tabGraph.className = "px-3 py-1 text-xs font-mono font-bold uppercase rounded-lg transition-all text-neutral-500 hover:text-primary flex items-center gap-1";
        }
    } else {
        if (treeView) treeView.classList.add('hidden');
        if (graphView) graphView.classList.remove('hidden');
        if (tabTree) {
            tabTree.className = "px-3 py-1 text-xs font-mono font-bold uppercase rounded-lg transition-all text-neutral-500 hover:text-primary flex items-center gap-1";
        }
        if (tabGraph) {
            tabGraph.className = "px-3 py-1 text-xs font-mono font-bold uppercase rounded-lg transition-all bg-primary text-on-primary shadow-xs flex items-center gap-1";
        }

        // Initialize or resize 2D Canvas Force Graph
        if (currentKgGraphData) {
            setTimeout(() => {
                initForceGraph(currentKgGraphData);
            }, 50);
        }
    }
};

window.loadKnowledgeGraph = async function(subject) {
    const loading = document.getElementById('kg-loading');
    const emptyState = document.getElementById('kg-empty-state');
    const treeView = document.getElementById('kg-tree-view');
    const countBadge = document.getElementById('kg-node-count-badge');

    if (loading) loading.classList.remove('hidden');
    if (emptyState) emptyState.classList.add('hidden');
    closeKgNodeDrawer();

    try {
        const res = await apiFetch(`/api/knowledge-graph?subject=${encodeURIComponent(subject)}`);
        if (!res.ok) {
            // Check if 404
            if (emptyState) emptyState.classList.remove('hidden');
            if (treeView) treeView.innerHTML = '';
            if (countBadge) countBadge.textContent = '0 узлов';
            currentKgGraphData = null;
            currentKgTreeData = null;
            return;
        }

        const data = await res.json();
        currentKgGraphData = data.graph_data;
        currentKgTreeData = data.tree_data;

        const nodesCount = (currentKgGraphData && currentKgGraphData.nodes) ? currentKgGraphData.nodes.length : 0;
        if (countBadge) countBadge.textContent = `${nodesCount} узлов`;

        if (nodesCount === 0) {
            if (emptyState) emptyState.classList.remove('hidden');
            if (treeView) treeView.innerHTML = '';
            return;
        }

        // Render DOM Mindmap Tree
        if (treeView) {
            treeView.innerHTML = '';
            if (currentKgTreeData) {
                renderKnowledgeTreeNode(currentKgTreeData, treeView, 0);
            } else if (currentKgGraphData.nodes.length > 0) {
                // Fallback: render flat cards if tree_data wasn't generated
                currentKgGraphData.nodes.forEach(node => {
                    renderKnowledgeTreeNode(node, treeView, 0);
                });
            }
        }

        // If currently in graph view, render canvas
        if (currentKgView === 'graph') {
            setTimeout(() => {
                initForceGraph(currentKgGraphData);
            }, 50);
        }

    } catch (e) {
        console.error("Сбой загрузки каркаса знаний:", e);
        if (emptyState) emptyState.classList.remove('hidden');
    } finally {
        if (loading) loading.classList.add('hidden');
    }
};

window.loadSeedOrDemoGraph = async function() {
    currentKgSubject = 'sudoustroystvo';
    const badge = document.getElementById('kg-subject-badge');
    if (badge) badge.textContent = 'SUDOUSTROYSTVO';
    await loadKnowledgeGraph('sudoustroystvo');
};

function renderKnowledgeTreeNode(node, container, depth) {
    if (!node) return;

    const nodeWrapper = document.createElement('div');
    nodeWrapper.className = depth === 0 ? "mb-2.5" : "tree-branch-container my-1.5";

    const hasChildren = node.children && node.children.length > 0;
    const cat = node.category || 'authority';
    const badgeClass = `badge-${cat}`;
    const catLabel = KG_CATEGORY_NAMES[cat] || cat.toUpperCase();

    const card = document.createElement('div');
    card.className = "tree-node-card p-3 rounded-xl bg-surface-container-lowest border border-neutral-200 dark:border-neutral-800 hover:border-neutral-400 transition-all flex items-start justify-between gap-2.5 cursor-pointer shadow-xs select-none";
    
    card.innerHTML = `
        <div class="flex items-start gap-2.5 min-w-0">
            ${hasChildren ? `
                <button class="tree-toggle-btn text-neutral-400 hover:text-primary p-0.5 mt-0.5 rounded transition-transform duration-200" title="Свернуть/Развернуть">
                    <span class="material-symbols-outlined text-[16px]">arrow_drop_down</span>
                </button>
            ` : `
                <span class="w-1.5 h-1.5 rounded-full bg-neutral-400 dark:bg-neutral-600 mt-2 ml-1 shrink-0"></span>
            `}
            <div class="flex flex-col min-w-0">
                <div class="flex items-center gap-1.5 flex-wrap">
                    <span class="font-bold text-xs sm:text-sm text-neutral-900 dark:text-neutral-100 font-mono">${escapeHTML(node.name || node.id)}</span>
                    <span class="px-1.5 py-0.2 rounded text-[9px] font-mono font-bold uppercase ${badgeClass}">${escapeHTML(catLabel)}</span>
                </div>
                ${node.summary ? `
                    <p class="text-[11px] text-neutral-600 dark:text-neutral-400 leading-snug mt-1 font-sans line-clamp-2">${escapeHTML(node.summary)}</p>
                ` : ''}
            </div>
        </div>
        <button class="text-neutral-400 hover:text-primary p-1 shrink-0 rounded transition-colors" title="Подробнее">
            <span class="material-symbols-outlined text-[16px]">info</span>
        </button>
    `;

    // Click on node card opens drawer
    card.onclick = (e) => {
        // If clicked on toggle button, handle collapse/expand
        if (e.target.closest('.tree-toggle-btn')) {
            e.stopPropagation();
            const btn = e.target.closest('.tree-toggle-btn');
            const childrenWrapper = nodeWrapper.querySelector('.tree-children-container');
            if (childrenWrapper) {
                const isHidden = childrenWrapper.classList.toggle('hidden');
                btn.style.transform = isHidden ? 'rotate(-90deg)' : 'rotate(0deg)';
            }
            return;
        }
        showKgNodeDrawer(node);
    };

    nodeWrapper.appendChild(card);

    // Recursively render children
    if (hasChildren) {
        const childrenContainer = document.createElement('div');
        childrenContainer.className = "tree-children-container";
        node.children.forEach(child => {
            renderKnowledgeTreeNode(child, childrenContainer, depth + 1);
        });
        nodeWrapper.appendChild(childrenContainer);
    }

    container.appendChild(nodeWrapper);
}

function wrapNodeText(text, maxChars = 16) {
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

window.initForceGraph = function(graphData) {
    const wrapper = document.getElementById('kg-graph-canvas-wrapper');
    if (!wrapper || !window.ForceGraph) return;

    // Check width/height
    const width = wrapper.clientWidth || window.innerWidth;
    const height = wrapper.clientHeight || (window.innerHeight - 120);

    if (!graphData || !graphData.nodes || graphData.nodes.length === 0) return;

    const isDark = document.documentElement.classList.contains('dark');
    const bgColor = isDark ? '#0e0e0e' : '#fbfbfb';

    // Prepare clean data
    const nodes = graphData.nodes.map(n => ({
        id: n.id,
        name: n.name || n.id,
        category: n.category || 'authority',
        summary: n.summary || '',
        val: n.level === 0 ? 12 : (n.level === 1 ? 8 : 5)
    }));

    const nodeIds = new Set(nodes.map(n => n.id));
    const links = (graphData.edges || [])
        .filter(e => nodeIds.has(e.source) && nodeIds.has(e.target))
        .map(e => ({
            source: e.source,
            target: e.target,
            relation: e.relation || '',
            label: e.label || ''
        }));

    // If graph already initialized, reuse and update data
    if (currentForceGraphInstance) {
        currentForceGraphInstance.width(width).height(height);
        currentForceGraphInstance.backgroundColor(bgColor);
        currentForceGraphInstance.graphData({ nodes, links });
        if (currentForceGraphInstance.d3Force('charge')) {
            currentForceGraphInstance.d3Force('charge').strength(-380);
        }
        if (currentForceGraphInstance.d3Force('link')) {
            currentForceGraphInstance.d3Force('link').distance(95);
        }
        currentForceGraphInstance.zoomToFit(400, 40);
        return;
    }

    wrapper.innerHTML = '';

    currentForceGraphInstance = ForceGraph()(wrapper)
        .width(width)
        .height(height)
        .backgroundColor(bgColor)
        .graphData({ nodes, links })
        .nodeId('id')
        .nodeVal('val')
        .nodeLabel(node => `${node.name} (${node.category})`)
        .linkColor(() => isDark ? 'rgba(255, 255, 255, 0.15)' : 'rgba(0, 0, 0, 0.15)')
        .linkWidth(1.5)
        .linkDirectionalParticles(2)
        .linkDirectionalParticleSpeed(0.006)
        .linkDirectionalParticleWidth(2)
        .linkDirectionalParticleColor(() => isDark ? '#ffffff' : '#1a1a1a')
        .cooldownTicks(90)
        .d3VelocityDecay(0.3)
        .nodeCanvasObject((node, ctx, globalScale) => {
            const label = node.name || node.id;
            const radius = Math.max(3.5, (node.val || 5));
            const currentDark = document.documentElement.classList.contains('dark');
            const bgStrokeColor = currentDark ? '#0e0e0e' : '#fbfbfb';
            const textFillColor = currentDark ? '#f1f5f9' : '#111827';

            // Node body: Black in light mode, crisp light-gray in dark mode
            ctx.beginPath();
            ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI, false);
            ctx.fillStyle = currentDark ? '#ededed' : '#1a1a1a';
            ctx.fill();

            // Border
            ctx.lineWidth = 1.5 / Math.max(0.5, globalScale);
            ctx.strokeStyle = currentDark ? '#ffffff' : '#404040';
            ctx.stroke();

            // Text label with line wrapping and contrast halo
            if (globalScale >= 0.35) {
                const fontSize = Math.min(10.5, Math.max(6.5, 9 / Math.pow(globalScale, 0.4)));
                ctx.font = `600 ${fontSize}px Inter, -apple-system, BlinkMacSystemFont, sans-serif`;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'top';

                const lines = wrapNodeText(label, 15);
                const lineHeight = fontSize + 2.5;

                lines.forEach((line, i) => {
                    const lineY = node.y + radius + 4 + (i * lineHeight);

                    // Contrast halo (stroke) behind text to prevent link lines from crossing letters
                    ctx.lineWidth = 3.5;
                    ctx.strokeStyle = bgStrokeColor;
                    ctx.strokeText(line, node.x, lineY);

                    // Crisp foreground text
                    ctx.fillStyle = textFillColor;
                    ctx.fillText(line, node.x, lineY);
                });
            }
        })
        .nodePointerAreaPaint((node, color, ctx) => {
            const radius = Math.max(4, (node.val || 5)) + 4;
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(node.x, node.y, radius, 0, 2 * Math.PI, false);
            ctx.fill();
        })
        .onNodeClick(node => {
            showKgNodeDrawer(node);
        });

    if (currentForceGraphInstance.d3Force('charge')) {
        currentForceGraphInstance.d3Force('charge').strength(-380);
    }
    if (currentForceGraphInstance.d3Force('link')) {
        currentForceGraphInstance.d3Force('link').distance(95);
    }

    // Resize on window resize
    window.addEventListener('resize', () => {
        if (currentForceGraphInstance && currentKgView === 'graph') {
            const w = wrapper.clientWidth || window.innerWidth;
            const h = wrapper.clientHeight || (window.innerHeight - 120);
            currentForceGraphInstance.width(w).height(h);
        }
    });

    setTimeout(() => {
        if (currentForceGraphInstance) {
            currentForceGraphInstance.zoomToFit(400, 40);
        }
    }, 400);
};

window.zoomGraph = function(factor) {
    if (!currentForceGraphInstance) return;
    const currentZoom = currentForceGraphInstance.zoom();
    currentForceGraphInstance.zoom(currentZoom * factor, 300);
};

window.resetGraphZoom = function() {
    if (!currentForceGraphInstance) return;
    currentForceGraphInstance.zoomToFit(400, 30);
};

window.showKgNodeDrawer = function(node) {
    const drawer = document.getElementById('kg-node-drawer');
    const badge = document.getElementById('kg-drawer-badge');
    const title = document.getElementById('kg-drawer-title');
    const summary = document.getElementById('kg-drawer-summary');
    const linksContainer = document.getElementById('kg-drawer-links');
    const linksSection = document.getElementById('kg-drawer-links-section');

    if (!drawer) return;

    const cat = node.category || 'authority';
    if (badge) {
        badge.className = `px-2 py-0.5 rounded text-[10px] font-mono font-bold uppercase shrink-0 badge-${cat}`;
        badge.textContent = KG_CATEGORY_NAMES[cat] || cat.toUpperCase();
    }
    if (title) title.textContent = node.name || node.id;
    if (summary) summary.textContent = node.summary || 'Детальное описание и законодательное основание отсутствуют.';

    // Populate links if graph data available
    if (linksContainer && currentKgGraphData && currentKgGraphData.edges) {
        linksContainer.innerHTML = '';
        const connectedEdges = currentKgGraphData.edges.filter(e => e.source === node.id || e.target === node.id);
        
        if (connectedEdges.length > 0) {
            if (linksSection) linksSection.classList.remove('hidden');
            connectedEdges.forEach(e => {
                const isOut = e.source === node.id;
                const otherId = isOut ? e.target : e.source;
                const otherNode = currentKgGraphData.nodes.find(n => n.id === otherId);
                const otherName = otherNode ? otherNode.name : otherId;
                const arrow = isOut ? '→' : '←';
                const relationLabel = e.label || e.relation || 'связь';

                const chip = document.createElement('div');
                chip.className = "px-2 py-1 bg-neutral-900 border border-neutral-700 rounded-lg text-[11px] font-mono text-neutral-300 flex items-center gap-1 hover:border-cyan-400 cursor-pointer transition-colors";
                chip.innerHTML = `<span class="text-cyan-400 font-bold">${arrow} ${escapeHTML(relationLabel)}:</span> <span>${escapeHTML(otherName)}</span>`;
                chip.onclick = () => {
                    if (otherNode) showKgNodeDrawer(otherNode);
                };
                linksContainer.appendChild(chip);
            });
        } else {
            if (linksSection) linksSection.classList.add('hidden');
        }
    }

    drawer.classList.remove('hidden');
};

window.closeKgNodeDrawer = function() {
    const drawer = document.getElementById('kg-node-drawer');
    if (drawer) drawer.classList.add('hidden');
};


/* ==========================================================================
   MILESTONE 4: INTERACTIVE PRACTICE MODULE CONTROLLER (R5)
   ========================================================================== */

let practiceItems = [];
let practiceFailedItems = [];
let practiceCurrentIndex = 0;
let practiceScore = 0;
let practiceAnswerSubmitted = false;

window.openPracticeModal = function(targetSubject) {
    const modal = document.getElementById('practice-modal');
    if (!modal) return;

    const sub = targetSubject || (currentSubject && currentSubject !== 'all' ? currentSubject : 'sudoustroystvo');
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
    const sub = customSub || (currentSubject && currentSubject !== 'all' ? currentSubject : 'sudoustroystvo');
    
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
    practiceAnswerSubmitted = false;

    try {
        const res = await apiFetch(`/api/practice/session?subject=${encodeURIComponent(sub)}&count=10`);
        if (res.ok) {
            practiceItems = await res.json();
        }

        // Fallback to sudoustroystvo if empty
        if (!practiceItems || practiceItems.length === 0) {
            const fallbackRes = await apiFetch(`/api/practice/session?subject=sudoustroystvo&count=10`);
            if (fallbackRes.ok) {
                practiceItems = await fallbackRes.json();
            }
        }

        if (!practiceItems || practiceItems.length === 0) {
            alert("Не удалось загрузить задания практики для этого предмета.");
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
            'situational': { icon: 'gavel', label: 'СИТУАЦИОННЫЙ КЕЙС' },
            'contrast_pair': { icon: 'compare_arrows', label: 'КОНТРАСТНАЯ ПАРА' },
            'slot_filling': { icon: 'edit_note', label: 'ЗАПОЛНЕНИЕ ПРОПУСКА' }
        };
        const cfg = typeConfigs[item.type] || { icon: 'psychology', label: 'ПРАКТИЧЕСКИЙ КЕЙС' };
        typeBadge.innerHTML = `<span class="material-symbols-outlined text-[13px]">${cfg.icon}</span><span>${cfg.label}</span>`;
    }

    // Prompt
    const promptEl = document.getElementById('practice-prompt');
    if (promptEl) promptEl.textContent = item.prompt;

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
            alert("Ошибка верификации ответа.");
            return;
        }

        const data = await res.json();
        
        // Remove spinner
        const spinner = clickedBtn.querySelector('.animate-spin');
        if (spinner) spinner.remove();

        const isCorrect = data.correct;
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
                if (textSpan && textSpan.textContent.trim() === data.correct_answer.trim()) {
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
            if (data.gold_standard) {
                goldBox.classList.remove('hidden');
                goldText.textContent = data.gold_standard;
            } else {
                goldBox.classList.add('hidden');
            }
        }

        if (explText) {
            explText.textContent = data.explanation || 'Обоснование отсутствует.';
        }

        if (feedbackContainer) {
            feedbackContainer.classList.remove('hidden');
            feedbackContainer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }

    } catch (e) {
        console.error("Сбой проверки ответа:", e);
        alert("Ошибка сети при проверке ответа.");
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

    if (cardContainer) cardContainer.classList.add('hidden');
    if (finishScreen) finishScreen.classList.remove('hidden');

    const total = practiceItems.length;
    const percent = total > 0 ? Math.round((practiceScore / total) * 100) : 0;

    if (scoreEl) {
        scoreEl.textContent = `${practiceScore} / ${total} (${percent}%)`;
    }

    if (msgEl) {
        if (percent >= 80) {
            msgEl.textContent = "Превосходно! Вы безошибочно различаете правовые режимы, звенья инстанций и водоразделы.";
        } else if (percent >= 50) {
            msgEl.textContent = "Хороший результат. Рекомендуем повторить спорные узлы через Каркас знаний или колоду FSRS.";
        } else {
            msgEl.textContent = "Материал требует закрепления. Изучите структуру понятий в Каркасе знаний перед следующей практикой.";
        }
    }

    // Сохраняем результат в базу данных и обновляем бейдж на стартовом экране
    const curSub = (currentSubject && currentSubject !== 'all') ? currentSubject : 'sudoustroystvo';
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
}

window.retryPracticeErrors = function() {
    if (!practiceFailedItems || practiceFailedItems.length === 0) return;

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
    const targetSub = sub || ((currentSubject && currentSubject !== 'all') ? currentSubject : 'sudoustroystvo');
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