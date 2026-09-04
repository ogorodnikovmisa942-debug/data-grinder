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
    
    container.innerHTML = [1, 2, 3, 4].map(rating => {
        const config = labels[rating];
        const colorClass = rating === 1 ? 'text-secondary hover:bg-error-container/20' : 
                          rating === 2 ? 'text-secondary hover:bg-error-container/20' :
                          rating === 3 ? 'text-primary hover:bg-surface-container' :
                                         'text-outline hover:bg-surface-container';
        return `
            <button data-rating="${rating}" class="flex-1 h-full flex flex-col items-center justify-center bg-transparent ${colorClass} transition-all active:scale-95 duration-75 ${rating < 4 ? 'border-r border-outline-variant/30' : ''}">
                <span class="font-mono font-bold text-xs uppercase tracking-wider">${escapeHTML(config.label)}</span>
                <span class="text-[8px] text-outline opacity-70 uppercase tracking-tighter mt-0.5">${escapeHTML(config.hint)}</span>
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
            const card = cardsQueue[currentIndex];
            if (!card) return;
            
            // НЕ переворачиваем, если карточка новая и не прошла знакомство
            if (card.state === 0 && !card.has_seen_intro) {
                return;
            }
            
            if (cardsQueue.length === 0 || isFlipped) return;
            isFlipped = true; 
            if (flashcard) flashcard.classList.add('rotate-y-180');
            if (actionButtons) { actionButtons.classList.remove('hidden'); actionButtons.classList.add('flex'); }
            executeVoiceSynthesis(cardsQueue[currentIndex].text);
            if (typeof window.startGlobalPomodoro === 'function') { window.startGlobalPomodoro(); }
        };
    }

    if (cardBack) {
        cardBack.onclick = (e) => {
            if (e.target.closest('button')) return; 
            if (cardsQueue.length === 0 || !isFlipped) return;
            isFlipped = false; 
            if (flashcard) flashcard.classList.remove('rotate-y-180');
            if (actionButtons) { actionButtons.classList.add('hidden'); actionButtons.classList.remove('flex'); }
        };
    }
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
    if (typeof window.syncTimerWithServer === 'function') { await window.syncTimerWithServer(); }
});

function executeVoiceSynthesis(textToSpeak) {
    if (!window.speechSynthesis) return;
    
    let lang = null;
    const containsChinese = /[\u4e00-\u9fa5]/.test(textToSpeak);
    const containsCyrillic = /[а-яА-ЯёЁ]/.test(textToSpeak);
    const containsLatin = /[a-zA-Z]/.test(textToSpeak);
    
    if (containsCyrillic) {
        lang = 'ru-RU';
    } else if (containsChinese) {
        lang = 'zh-CN';
    } else if (currentSubject.includes('chinese') || currentSubject === 'chinese_hsk3') {
        lang = 'zh-CN';
    } else if (currentSubject.includes('english') || currentSubject.includes('eng_') || (containsLatin && !containsCyrillic)) {
        lang = 'en-US';
    } else if (currentSubject.includes('german') || currentSubject.includes('de_')) {
        lang = 'de-DE';
    } else if (currentSubject.includes('spanish') || currentSubject.includes('es_')) {
        lang = 'es-ES';
    } else if (currentSubject.includes('french') || currentSubject.includes('fr_')) {
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

function isLanguageCard(card) {
    if (!card) return false;
    const textToSpeak = card.text;
    const containsChinese = /[\u4e00-\u9fa5]/.test(textToSpeak);
    const containsCyrillic = /[а-яА-ЯёЁ]/.test(textToSpeak);
    const containsLatin = /[a-zA-Z]/.test(textToSpeak);
    
    return containsChinese || 
           containsCyrillic ||
           currentSubject.includes('chinese') || 
           currentSubject === 'chinese_hsk3' || 
           currentSubject.includes('english') || 
           currentSubject.includes('eng_') || 
           currentSubject.includes('german') ||
           currentSubject.includes('spanish') ||
           currentSubject.includes('french') ||
           (containsLatin && !containsCyrillic);
}

function isSpeakable(text) {
    if (!text || text === '---') return false;
    const containsChinese = /[\u4e00-\u9fa5]/.test(text);
    const containsCyrillic = /[а-яА-ЯёЁ]/.test(text);
    const containsLatin = /[a-zA-Z]/.test(text);
    
    return containsChinese || 
           containsCyrillic ||
           currentSubject.includes('chinese') || 
           currentSubject === 'chinese_hsk3' || 
           currentSubject.includes('english') || 
           currentSubject.includes('eng_') || 
           currentSubject.includes('german') ||
           currentSubject.includes('spanish') ||
           currentSubject.includes('french') ||
           (containsLatin && !containsCyrillic);
}

window.replayAudioForText = function(text) {
    if (!window.speechSynthesis || !text) return;
    
    let lang = null;
    const containsChinese = /[\u4e00-\u9fa5]/.test(text);
    const containsCyrillic = /[а-яА-ЯёЁ]/.test(text);
    const containsLatin = /[a-zA-Z]/.test(text);
    
    if (containsCyrillic) {
        lang = 'ru-RU';
    } else if (containsChinese || currentSubject.includes('chinese')) {
        lang = 'zh-CN';
    } else if (currentSubject.includes('english') || currentSubject.includes('eng_') || (containsLatin && !containsCyrillic)) {
        lang = 'en-US';
    } else if (currentSubject.includes('german') || currentSubject.includes('de_')) {
        lang = 'de-DE';
    } else if (currentSubject.includes('spanish') || currentSubject.includes('es_')) {
        lang = 'es-ES';
    } else if (currentSubject.includes('french') || currentSubject.includes('fr_')) {
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
    executeVoiceSynthesis(currentCard.text);
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

function showSurveyDirectly() {
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

function showSessionStarter() {
    const starter = document.getElementById('session-starter');
    const flashcard = document.getElementById('flashcard');
    const progressBar = document.getElementById('progress-bar');
    if (starter) starter.classList.remove('hidden');
    if (flashcard) flashcard.classList.add('hidden');
    if (progressBar) progressBar.classList.add('hidden');
    if (actionButtons) actionButtons.classList.add('hidden');
}

async function startSession(mode) {
    currentSessionMode = mode;
    const starter = document.getElementById('session-starter');
    const flashcard = document.getElementById('flashcard');
    const progressBar = document.getElementById('progress-bar');
    
    if (starter) starter.classList.add('hidden');
    if (flashcard) flashcard.classList.remove('hidden');
    if (progressBar) progressBar.classList.remove('hidden');
    
    await fetchActiveSession(mode);
}

async function fetchActiveSession(mode = 'mixed') {
    try {
        const response = await apiFetch(`/api/session?subject=${currentSubject}&mode=${mode}`);
        cardsQueue = await response.json();
        shuffleArray(cardsQueue);
        const surveyContainer = document.getElementById('survey-container');
        if (cardsQueue.length === 0) {
            if (surveyContainer && !window.surveyCompletedToday) {
                surveyContainer.classList.remove('hidden');
                if (cardText) cardText.classList.add('hidden');
                if (cardCounter) cardCounter.classList.add('hidden');
            } else {
                if (surveyContainer) surveyContainer.classList.add('hidden');
                if (cardText) {
                    cardText.classList.remove('hidden');
                    cardText.textContent = "Очередь пуста";
                }
            }
            if (cardSecondaryText) cardSecondaryText.textContent = "";
            if (cardMainText) {
                cardMainText.textContent = window.surveyCompletedToday ? "Все задачи решены. Опрос завершен." : "Все задачи решены.";
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
            fetchActiveSession();
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
    
    // Переключаем видимость контейнеров на лицевой стороне
    const normalFront = document.getElementById('card-front-normal');
    const introFront = document.getElementById('card-front-intro');
    const front = document.getElementById('card-front');
    
    if (normalFront) normalFront.classList.add('hidden');
    if (introFront) {
        introFront.classList.remove('hidden');
        introFront.classList.add('flex', 'flex-col', 'items-center', 'justify-between');
    }
    if (front) front.classList.add('introduction-mode');
    
    const phase = card.intro_phase || 0;
    let mnemonicText = '---';
    if (card.mnemonic) {
        const m = card.mnemonic;
        mnemonicText = typeof m === 'object' ? `${m.keyword}: ${m.verbal_cue}` : m;
    }
    
    // Структура блоков для знакомства
    const blocks = [
        { type: 'term', label: 'ТЕРМИН', content: card.text },
        { type: 'context', label: 'КОНТЕКСТ', content: card.secondary_text || '---' },
        { type: 'definition', label: 'ОПРЕДЕЛЕНИЕ', content: card.translation },
        { type: 'example', label: 'ПРИМЕР', content: card.example || '---' },
        { type: 'mnemonic', label: 'АССОЦИАЦИЯ', content: mnemonicText }
    ].filter(b => b.content && b.content !== '---');
    
    // Рендерим блоки до текущей фазы включительно
    let html = `<div class="flex flex-col items-center gap-md w-full my-auto overflow-y-auto pr-1" style="max-height: 80%;">`;
    
    for (let i = 0; i <= phase && i < blocks.length; i++) {
        const block = blocks[i];
        const safeContent = escapeHTML(block.content);
        
        html += `
            <div class="w-full text-center introduction-block animate-fade-in" style="animation-delay: ${i * 0.08}s">
                ${i > 0 ? '<div class="w-full h-px bg-outline-variant/30 my-sm"></div>' : ''}
                <span class="block text-[10px] text-outline uppercase mb-xs tracking-wider font-mono">${escapeHTML(block.label)}</span>
                <div class="text-${block.type === 'term' ? '[24px] sm:text-[28px] font-bold' : 'sm:text-base'} text-primary leading-relaxed break-words px-sm">
                    ${safeContent}
                </div>
                ${isSpeakable(block.content) || block.type === 'term' ? `
                    <button onclick="event.stopPropagation(); window.replayAudioForText('${safeContent.replace(/'/g, "\\'")}');" 
                            class="text-outline hover:text-primary transition-all mt-xs inline-flex items-center gap-xs py-1 px-2 border border-outline-variant/30 rounded-none bg-surface-container-lowest active:scale-95 duration-75">
                        <span class="material-symbols-outlined text-[16px]">volume_up</span>
                        <span class="text-[9px] font-mono uppercase">Прослушать</span>
                    </button>
                ` : ''}
            </div>
        `;
    }
    html += `</div>`;
    
    // Кнопка навигации
    if (phase < blocks.length - 1) {
        html += `
            <button onclick="event.stopPropagation(); advanceIntroduction()" 
                    class="mt-auto w-full border border-primary text-primary py-sm font-bold tracking-wide hover:bg-primary hover:text-on-primary transition-all text-xs font-mono uppercase shrink-0">
                [→ ${blocks[phase + 1].label}]
            </button>
        `;
    } else {
        if (!card._recall_checked) {
            html += `
                <div class="mt-auto w-full flex flex-col gap-xs shrink-0">
                    <div class="text-[10px] text-outline uppercase font-mono text-center mb-0.5">САМОПРОВЕРКА ПАМЯТИ ПЕРЕД ЗАКРЕПЛЕНИЕМ:</div>
                    <button onclick="event.stopPropagation(); window.toggleIntroRecall()" 
                            class="w-full border border-dashed border-primary text-primary py-sm font-bold tracking-wide hover:bg-surface-container transition-all text-xs font-mono uppercase">
                        [ПОКАЗАТЬ ОТВЕТ И ПРОВЕРИТЬ ПАМЯТЬ]
                    </button>
                </div>
            `;
        } else {
            html += `
                <button onclick="event.stopPropagation(); completeIntroduction()" 
                        class="mt-auto w-full border border-primary bg-primary text-on-primary py-sm font-bold tracking-wide hover:bg-transparent hover:text-primary transition-all text-xs font-mono uppercase shrink-0">
                    [✓ Я ВСПОМНИЛ И ЗАКРЕПИЛ, НАЧАТЬ УЧИТЬ]
                </button>
            `;
        }
    }
    
    if (introFront) {
        introFront.innerHTML = html;
        // Scroll to bottom
        setTimeout(() => {
            const scrollableDiv = introFront.querySelector('.overflow-y-auto');
            if (scrollableDiv) scrollableDiv.scrollTop = scrollableDiv.scrollHeight;
        }, 50);
    }
    
    if (cardCounter) cardCounter.textContent = `${currentIndex + 1} / ${cardsQueue.length}`;
    if (progressFill) progressFill.style.width = `${(currentIndex / cardsQueue.length) * 100}%`;
    
    cardShowTimestamp = Date.now();
}

window.toggleIntroRecall = function() {
    const card = cardsQueue[currentIndex];
    if (card) {
        card._recall_checked = true;
        renderIntroductionCard(card);
    }
};

function advanceIntroduction() {
    const card = cardsQueue[currentIndex];
    card.intro_phase = (card.intro_phase || 0) + 1;
    renderIntroductionCard(card);
}

function completeIntroduction() {
    const card = cardsQueue[currentIndex];
    card.has_seen_intro = true;
    card.state = 1; // Learning
    
    // Отправляем на бэкенд
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
        introFront.classList.remove('flex', 'flex-col', 'items-center', 'justify-between');
    }
    if (front) front.classList.remove('introduction-mode');
    
    if (cardText) cardText.textContent = card.text;
    
    // Показываем/скрываем кнопки повторного озвучивания для языковых карточек
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
        if (cardSecondaryText) cardSecondaryText.textContent = card.secondary_text || '---'; 
        if (cardMainText) cardMainText.textContent = card.translation; 
        
        const metaLabel = document.getElementById('card-meta-label');
        if (metaLabel) {
            if (card.is_anchored && card.phrase_text) {
                metaLabel.innerHTML = `[!] КОНТЕКСТНЫЙ ЯКОРЬ: <span class="text-secondary font-bold font-mono">${card.phrase_text}</span>`;
            } else {
                metaLabel.textContent = (currentSubject === 'chinese_hsk3' || card.text.match(/[\u4e00-\u9fa5]/)) ? 'Pinyin' : 'Контекстная подсказка';
            }
        }

        if (cardMnemonicContainer && cardMnemonic) {
            if (card.mnemonic) {
                let m = card.mnemonic;
                cardMnemonic.textContent = typeof m === 'object' ? `${m.keyword}: ${m.verbal_cue}` : m;
                cardMnemonicContainer.classList.remove('hidden');
            } else { cardMnemonicContainer.classList.add('hidden'); }
        }
    }, 200);

    if (cardCounter) cardCounter.textContent = `${currentIndex + 1} / ${cardsQueue.length}`;
    if (progressFill) progressFill.style.width = `${(currentIndex / cardsQueue.length) * 100}%`;
    
    cardShowTimestamp = Date.now();
}

if (document.getElementById('action-buttons')) {
    document.getElementById('action-buttons').addEventListener('click', (e) => {
        const targetButton = e.target.closest('button'); if (!targetButton) return;
        e.stopPropagation(); 
        const rating = parseInt(targetButton.getAttribute('data-rating'));
        const currentCard = cardsQueue[currentIndex]; if (!currentCard) return;

        const payloadCardId = currentCard.id;
        const responseTimeMs = cardShowTimestamp ? (Date.now() - cardShowTimestamp) : 0;
        const hasAssoc = currentCard.mnemonic ? true : false;

        if (rating === 1) { 
            if (currentCard.state === 2) {
                currentCard.state = 3; currentCard.is_anchored = true;
            }
            const copy = {...currentCard};
            const remainingCount = cardsQueue.length - (currentIndex + 1);
            if (remainingCount > 0) {
                const randomOffset = Math.floor(Math.random() * (remainingCount + 1));
                const insertIndex = currentIndex + 1 + randomOffset;
                cardsQueue.splice(insertIndex, 0, copy);
            } else {
                cardsQueue.push(copy);
            }
        }

        currentIndex++;
        recalculateQueueCounters(); renderCurrentCard();

        apiFetch('/api/answer', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                card_id: payloadCardId, 
                rating: rating,
                response_time: responseTimeMs,
                has_association: hasAssoc,
                is_cram: currentSessionMode === 'cram'
            })
        }).then(res => { if (res.ok) updateGlobalBadges(); })
          .catch(err => console.error("[Data Grinder] Фоновая ошибка синхронизации:", err));
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
            <div class="flex justify-between items-center py-2 font-mono text-sm gap-sm border-b border-outline-variant/30 archive-row cursor-pointer" 
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
                    <input type="checkbox" class="card-checkbox hidden rounded-none border-outline text-primary focus:ring-0 mr-xs" data-card-id="${c.id}" onchange="onCardCheckboxChange(event)">
                    <div class="flex justify-between items-center w-full min-w-0">
                        <span class="font-bold text-base text-primary w-1/5 truncate select-none">${escapeHTML(c.text)}</span>
                        <span class="text-outline w-1/4 truncate text-xs select-none">${escapeHTML(c.secondary_text) || '---'}</span>
                        <span class="text-on-surface-variant w-1/3 truncate text-xs select-none">${escapeHTML(c.translation)}</span>
                        <span class="text-[10px] text-outline opacity-60 w-12 text-right font-bold select-none">${labels[c.state] || 'NEW'}</span>
                    </div>
                </div>
                <div class="flex items-center gap-xs shrink-0 archive-row-actions">
                    <button onclick="event.stopPropagation(); requestEditCard(${c.id})" class="text-outline hover:text-primary p-1 font-bold" title="Редактировать">✎</button>
                    <button onclick="event.stopPropagation(); requestMoveCard(${c.id})" class="text-outline hover:text-primary p-1 font-bold" title="Перенести предмет">➔</button>
                    <button onclick="event.stopPropagation(); requestDeleteCard(${c.id})" class="text-outline hover:text-secondary p-1 transition-colors active:scale-95 duration-75 flex items-center justify-center">✕</button>
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
    if (!confirm("Выжечь эту матрицу знаний из базы данных навсегда?")) return;
    try {
        const response = await apiFetch(`/api/management/cards/${cardId}`, { method: 'DELETE' });
        if (response.ok) {
            localCardsArchive = localCardsArchive.filter(c => c.id !== cardId);
            const row = document.getElementById(`archive-row-${cardId}`); if (row) row.remove();
            updateGlobalBadges();
        }
    } catch (e) { console.error("Сбой удаления карточки:", e); }
}

function makeAsciiBar(progressValue) {
    const totalBlocks = 10;
    const filledBlocks = Math.round((progressValue / 100) * totalBlocks);
    const emptyBlocks = totalBlocks - filledBlocks;
    return '[' + '█'.repeat(filledBlocks) + '░'.repeat(emptyBlocks) + ']';
}

async function loadStatsTab() {
    try {
        const res = await apiFetch(`/api/stats/dashboard?subject=${currentSubject}`); const data = await res.json();
        document.getElementById('stat-new').innerText = data.cards_new; document.getElementById('stat-learning').innerText = data.cards_learning;
        document.getElementById('stat-review').innerText = data.cards_review; document.getElementById('stat-progress').innerText = data.progress_percent;
        document.getElementById('stat-retention').innerText = data.retention_rate_30d; document.getElementById('stat-streak').innerText = `${data.streak_days} дней`;
        
        const titleContainer = document.getElementById('breakdown-title'); const listContainer = document.getElementById('breakdown-list');
        if (titleContainer) titleContainer.innerText = currentSubject === 'all' ? "--- ОСВОЕНИЕ ПРЕДМЕТОВ ---" : "--- ТЕМАТИЧЕСКАЯ МАТРИЦА ---";
        if (!data.breakdown || data.breakdown.length === 0) { if (listContainer) listContainer.innerHTML = '<div class="text-xs text-outline py-xs">Нет данных</div>'; return; }
        
        if (listContainer) {
            listContainer.innerHTML = data.breakdown.map(item => {
                const barHtml = makeAsciiBar(item.progress);
                return `
                    <div class="flex flex-col py-2 font-mono text-xs gap-xs border-b border-outline-variant/20">
                        <div class="flex justify-between items-center w-full">
                            <span class="text-on-surface-variant font-bold uppercase truncate max-w-[70%]">${escapeHTML(item.label)}</span>
                            <span class="font-bold text-primary">[${item.progress}%]</span>
                        </div>
                        <div class="text-outline tracking-wider font-bold select-none">${barHtml}</div>
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
                btn.className = "w-full text-left border p-md transition-all duration-75 flex justify-between items-center bg-primary text-on-primary border-primary font-mono text-xs font-bold uppercase";
            } else {
                btn.className = "w-full text-left border p-md transition-all duration-75 flex justify-between items-center bg-surface-container-lowest text-primary border-outline-variant font-mono text-xs uppercase";
            }
        }
    });
}

async function setIntensityPreset(limit) {
    renderPresetButtonsDOM(limit);
    try {
        await apiFetch(`/api/config?subject=${currentSubject}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ daily_limit: limit, focus_mode_default: false })
        });
    } catch (e) { console.error("Фоновый сбой сохранения пресета:", e); }
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
        const res = await apiFetch('/api/subjects'); 
        const subjects = await res.json();
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
            if (currentVal && (subjects.includes(currentVal) || currentVal === '__new__')) {
                importSel.value = currentVal;
                if (tipEl) tipEl.textContent = currentVal === '__new__' ? '[НОВЫЙ ПРЕДМЕТ]' : `[ВЫБРАН: ${currentVal.toUpperCase()}]`;
            } else if (currentSubject && currentSubject !== 'all' && subjects.includes(currentSubject)) {
                importSel.value = currentSubject;
                if (tipEl) tipEl.textContent = `[ВЫБРАН: ${currentSubject.toUpperCase()}]`;
            } else if (subjects.length > 0) {
                importSel.value = subjects[0];
                if (tipEl) tipEl.textContent = `[ВЫБРАН: ${subjects[0].toUpperCase()}]`;
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
let currentVolumeLimit = 'medium';
let currentDetailDensity = 'medium';

window.setGranularityMode = function(mode) {
    currentGranularityMode = mode;
    ['atomic', 'single_deep', 'cheatsheet'].forEach(m => {
        const el = document.getElementById(`gran-${m}`);
        if (el) {
            if (m === mode) {
                el.className = 'border border-primary bg-primary text-on-primary py-1.5 px-1 text-[9px] font-bold uppercase transition-all flex flex-col items-center justify-center';
            } else {
                el.className = 'border border-outline-variant text-outline hover:text-primary py-1.5 px-1 text-[9px] font-bold uppercase transition-all flex flex-col items-center justify-center';
            }
        }
    });

    const volButtons = document.getElementById('import-volume-buttons');
    const volLocked = document.getElementById('import-volume-locked');
    const volLabel = document.getElementById('import-volume-label');

    if (mode === 'single_deep') {
        if (volButtons) volButtons.classList.add('hidden');
        if (volLocked) volLocked.classList.remove('hidden');
        if (volLabel) volLabel.textContent = '1 карта';
    } else {
        if (volButtons) volButtons.classList.remove('hidden');
        if (volLocked) volLocked.classList.add('hidden');
        updateVolumeLabel();
    }
    updateImportExplanation();
};

window.setVolumeLimit = function(vol) {
    currentVolumeLimit = vol;
    ['low', 'medium', 'high', 'max'].forEach(v => {
        const el = document.getElementById(`vol-${v}`);
        if (el) {
            if (v === vol) {
                el.className = 'border border-primary bg-primary text-on-primary py-0.5 text-[9px] font-bold';
            } else {
                el.className = 'border border-outline-variant text-outline hover:text-primary py-0.5 text-[9px] font-bold';
            }
        }
    });
    updateVolumeLabel();
    updateImportExplanation();
};

function updateVolumeLabel() {
    const volLabel = document.getElementById('import-volume-label');
    if (!volLabel) return;
    const map = { 'low': 'до 5 карт', 'medium': 'до 15 карт', 'high': 'до 30 карт', 'max': 'все данные' };
    volLabel.textContent = map[currentVolumeLimit] || currentVolumeLimit;
}

window.setDetailDensity = function(density) {
    currentDetailDensity = density;
    ['low', 'medium', 'high'].forEach(d => {
        const el = document.getElementById(`dense-${d}`);
        if (el) {
            if (d === density) {
                el.className = 'border border-primary bg-primary text-on-primary py-0.5 text-[9px] font-bold';
            } else {
                el.className = 'border border-outline-variant text-outline hover:text-primary py-0.5 text-[9px] font-bold';
            }
        }
    });
    const densityLabel = document.getElementById('import-density-label');
    if (densityLabel) {
        const map = { 'low': 'Кратко', 'medium': 'Баланс', 'high': 'Подробно' };
        densityLabel.textContent = map[currentDetailDensity] || currentDetailDensity;
    }
    updateImportExplanation();
};

function updateImportExplanation() {
    const explEl = document.getElementById('import-mode-explanation');
    if (!explEl) return;

    if (currentGranularityMode === 'single_deep') {
        const dText = currentDetailDensity === 'low' ? 'краткое резюме' : currentDetailDensity === 'high' ? 'исчерпывающий разбор со всеми подпунктами' : 'определение и контекст';
        explEl.textContent = `> РЕЖИМ: 1 Большая карта | Объем: строго 1 карта | Глубина: ${dText} всей темы.`;
    } else if (currentGranularityMode === 'cheatsheet') {
        const vText = currentVolumeLimit === 'low' ? 'до 5 карт' : currentVolumeLimit === 'high' ? 'до 30 карт' : currentVolumeLimit === 'max' ? 'все термины' : 'до 15 карт';
        explEl.textContent = `> РЕЖИМ: Шпоры / Блиц | Объем: ${vText} | Глубина: выжимки по 1–2 предложения.`;
    } else { // atomic
        const vText = currentVolumeLimit === 'low' ? 'до 5 карт' : currentVolumeLimit === 'high' ? 'до 30 карт' : currentVolumeLimit === 'max' ? 'все термины' : 'до 15 карт';
        const dText = currentDetailDensity === 'low' ? 'кратко (1–2 фразы)' : currentDetailDensity === 'high' ? 'подробно со всеми деталями' : 'суть + пример';
        explEl.textContent = `> РЕЖИМ: Обычные карточки по ключевым терминам | Объем: ${vText} | Глубина: ${dText}.`;
    }
}

async function importTextKnowledge() {
    const textarea = document.getElementById('import-text'); 
    const btn = document.getElementById('btn-import'); 
    const text = textarea ? textarea.value.trim() : "";
    if (!text) { alert("Входной буфер пуст. Вставь текст лекции или статьи кодекса!"); return; }
    
    const targetSubject = getSelectedImportSubject();
    if (!targetSubject) {
        alert("Выберите целевой предмет из списка или укажите новый перед запуском парсера!");
        return;
    }

    const pref = localStorage.getItem('assoc_preference') || 'acoustic';
    const customInstruction = document.getElementById('import-custom-instruction')?.value.trim() || '';

    if (btn) { btn.disabled = true; btn.innerText = "[ПАРСИНГ GEMINI 3.7 FLASH...]"; }
    try {
        const response = await apiFetch('/api/config/import', {
            method: 'POST', 
            headers: { 'Content-Type': 'application/json' }, 
            body: JSON.stringify({ 
                text: text, 
                subject: targetSubject,
                density: currentDetailDensity, 
                volume: currentVolumeLimit, 
                priority: 'balanced',
                assoc_preference: pref,
                granularity_mode: currentGranularityMode,
                custom_instruction: customInstruction,
                commit_now: false // Направляем в Песочницу!
            })
        });
        const data = await response.json();
        if (response.ok && data.status === 'staging') {
            startStagingSession(data);
        } else if (response.ok && data.status === 'success') {
            alert(`Импортировано карт: ${data.cards_count}`);
            if (textarea) textarea.value = ""; await loadDynamicSubjects(); updateGlobalBadges();
        } else { 
            alert("Ошибка ИИ-конвейера: " + (data.message || data.detail || "Неизвестный сбой.")); 
        }
    } catch (e) { 
        console.error("Сбой сети при импорте знаний:", e); 
        alert("Критический сбой сети."); 
    } finally { 
        if (btn) { btn.disabled = false; btn.innerText = "[ЗАПУСТИТЬ ПАРСЕР ЗНАНИЙ]"; } 
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

window.handleFileUpload = async function(event) {
    const file = event.target.files && event.target.files[0];
    if (!file) return;

    const targetSubject = getSelectedImportSubject();
    if (!targetSubject) {
        alert("Выберите целевой предмет из списка или укажите новый перед загрузкой файла!");
        event.target.value = '';
        return;
    }

    const statusEl = document.getElementById('file-import-status');
    if (statusEl) {
        statusEl.textContent = `[ИЗВЛЕЧЕНИЕ ТЕКСТА ИЗ ${file.name.toUpperCase()}...]`;
        statusEl.classList.remove('hidden');
    }

    const pref = localStorage.getItem('assoc_preference') || 'acoustic';

    const formData = new FormData();
    formData.append('file', file);
    formData.append('subject', targetSubject);
    formData.append('density', currentDetailDensity);
    formData.append('volume', currentVolumeLimit);
    formData.append('priority', 'balanced');
    formData.append('assoc_preference', pref);
    formData.append('granularity_mode', currentGranularityMode);
    formData.append('custom_instruction', document.getElementById('import-custom-instruction')?.value.trim() || '');
    formData.append('commit_now', 'false');

    try {
        const response = await apiFetch('/api/config/import/file', {
            method: 'POST',
            body: formData
        });
        const data = await response.json();
        if (response.ok && data.status === 'staging') {
            if (statusEl) statusEl.classList.add('hidden');
            startStagingSession(data);
        } else {
            alert("Ошибка обработки файла: " + (data.detail || data.message || "Сбой"));
            if (statusEl) statusEl.classList.add('hidden');
        }
    } catch (err) {
        console.error("Сбой загрузки файла:", err);
        alert("Ошибка сети при отправке файла.");
        if (statusEl) statusEl.classList.add('hidden');
    } finally {
        event.target.value = '';
    }
};

window.handleImageOcr = async function(event) {
    const file = event.target.files && event.target.files[0];
    if (!file) return;

    const statusEl = document.getElementById('file-import-status');
    if (statusEl) {
        statusEl.textContent = "[TESSERACT OCR: ИНИЦИАЛИЗАЦИЯ ДВИЖКА...]";
        statusEl.classList.remove('hidden');
    }

    if (typeof Tesseract === 'undefined') {
        alert("Движок Tesseract OCR ещё загружается. Подождите пару секунд и повторите.");
        if (statusEl) statusEl.classList.add('hidden');
        return;
    }

    try {
        const result = await Tesseract.recognize(
            file,
            'rus+eng',
            {
                logger: m => {
                    if (m.status === 'recognizing text' && statusEl) {
                        const pct = Math.round((m.progress || 0) * 100);
                        statusEl.textContent = `[TESSERACT OCR: РАСПОЗНАВАНИЕ ${pct}%]`;
                    }
                }
            }
        );

        const recognizedText = (result && result.data && result.data.text) ? result.data.text.trim() : "";
        if (!recognizedText) {
            alert("Не удалось распознать текст на фото. Попробуйте более четкий снимок.");
            if (statusEl) statusEl.classList.add('hidden');
            return;
        }

        if (statusEl) {
            statusEl.textContent = `[OCR УСПЕШНО! ИЗВЛЕЧЕНО ${recognizedText.length} СИМВОЛОВ, ЗАПУСК ИИ...]`;
        }

        const textarea = document.getElementById('import-text');
        if (textarea) textarea.value = recognizedText;

        await importTextKnowledge();
        if (statusEl) statusEl.classList.add('hidden');
    } catch (ocrErr) {
        console.error("Ошибка OCR:", ocrErr);
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
let stagingSubject = 'generic';
let stagingTheme = 'Новый блок знаний';

function startStagingSession(data) {
    stagingCards = (data.cards || []).map((c, idx) => ({ ...c, _orig_idx: idx }));
    currentStagingIndex = 0;
    approvedStagingCards = [];
    rejectedStagingCards = [];
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
    }

    renderCurrentStagingCard();
    initStagingGestures();
}

function renderCurrentStagingCard() {
    const cardEl = document.getElementById('staging-card');
    const commitBtn = document.getElementById('staging-commit-btn');
    const approvedCnt = document.getElementById('staging-approved-count');
    const rejectedCnt = document.getElementById('staging-rejected-count');
    const remainingCnt = document.getElementById('staging-remaining-count');

    const total = stagingCards.length;
    const remaining = Math.max(0, total - currentStagingIndex);

    if (approvedCnt) approvedCnt.textContent = approvedStagingCards.length;
    if (rejectedCnt) rejectedCnt.textContent = rejectedStagingCards.length;
    if (remainingCnt) remainingCnt.textContent = remaining;
    if (commitBtn) commitBtn.textContent = `СОХРАНИТЬ (${approvedStagingCards.length})`;

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
                    <button onclick="commitApprovedStagingCards()" class="w-full border border-primary bg-primary text-on-primary py-sm font-bold uppercase text-xs hover:bg-transparent hover:text-primary transition-all mt-sm">
                        [СОХРАНИТЬ В БАЗУ ДАННЫХ]
                    </button>
                </div>
            `;
        }
        return;
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
    if (tierEl) tierEl.textContent = card.initial_difficulty_tier || 'medium';
    if (textEl) textEl.textContent = card.text || '---';
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
    const cardEl = document.getElementById('staging-card');
    const badgeAccept = document.getElementById('staging-badge-accept');
    if (badgeAccept) badgeAccept.style.opacity = '1';

    if (cardEl) {
        cardEl.style.transition = 'transform 0.3s ease, opacity 0.3s ease';
        cardEl.style.transform = 'translate(120%, 20px) rotate(20deg)';
        cardEl.style.opacity = '0';
    }

    approvedStagingCards.push(stagingCards[currentStagingIndex]);
    setTimeout(() => {
        currentStagingIndex++;
        if (cardEl) cardEl.style.transition = 'none';
        renderCurrentStagingCard();
    }, 250);
};

window.stagingSwipeLeft = function() {
    if (currentStagingIndex >= stagingCards.length) return;
    const cardEl = document.getElementById('staging-card');
    const badgeReject = document.getElementById('staging-badge-reject');
    if (badgeReject) badgeReject.style.opacity = '1';

    if (cardEl) {
        cardEl.style.transition = 'transform 0.3s ease, opacity 0.3s ease';
        cardEl.style.transform = 'translate(-120%, 20px) rotate(-20deg)';
        cardEl.style.opacity = '0';
    }

    rejectedStagingCards.push(stagingCards[currentStagingIndex]);
    setTimeout(() => {
        currentStagingIndex++;
        if (cardEl) cardEl.style.transition = 'none';
        renderCurrentStagingCard();
    }, 250);
};

window.stagingAcceptAll = function() {
    while (currentStagingIndex < stagingCards.length) {
        approvedStagingCards.push(stagingCards[currentStagingIndex]);
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
        const response = await apiFetch('/api/config/import/commit', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                subject: stagingSubject,
                theme: stagingTheme,
                cards: approvedStagingCards
            })
        });

        const data = await response.json();
        if (response.ok && data.status === 'success') {
            alert(`Успешно сохранено ${data.cards_count} карточек в предмет [${data.subject.toUpperCase()}]!`);
            closeStagingOverlay();
            const textarea = document.getElementById('import-text');
            if (textarea) textarea.value = '';
            await loadDynamicSubjects();
            updateGlobalBadges();
            if (currentTab === 'data') loadDataTab();
        } else {
            alert("Ошибка сохранения: " + (data.detail || data.message || "Неизвестная ошибка"));
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