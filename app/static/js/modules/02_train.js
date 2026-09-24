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
                    if (typeof window.fastTrackIntroduction === 'function') {
                        window.fastTrackIntroduction();
                    }
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
                        if (typeof window.toggleIntroRecall === 'function') {
                            window.toggleIntroRecall();
                        }
                    } else {
                        advanceIntroduction();
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
            if (typeof window.toggleIntroRecall === 'function') {
                window.toggleIntroRecall();
            }
        } else {
            advanceIntroduction();
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

async function fetchActiveSession(mode = 'mixed') {
    try {
        currentSessionMode = mode;
        const targetSub = (mode === 'cram') ? 'all' : currentSubject;
        const response = await apiFetch(`/api/session?subject=${targetSub}&mode=${mode}`);
        if (!response.ok) {
            console.error("[Data Grinder] Сбой ответа сессии:", response.status);
            cardsQueue = [];
            resetCardDOM();
            if (cardText) {
                cardText.classList.remove('hidden');
                cardText.textContent = response.status === 401 ? "Требуется авторизация" : "Ошибка сессии";
            }
            const frontHint = document.getElementById('card-front-hint');
            if (frontHint) {
                frontHint.classList.remove('hidden');
                frontHint.className = "mt-4 flex flex-col items-center gap-2";
                frontHint.innerHTML = `
                    <p class="text-xs text-neutral-500 dark:text-neutral-400 font-sans max-w-[280px] leading-relaxed text-center mb-1">
                        ${response.status === 401 ? "Пожалуйста, откройте приложение через Telegram-бота для доступа к учебному процессу." : "Не удалось загрузить карточки. Проверьте подключение к серверу."}
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

function renderIntroductionCard(card) {
    isFlipped = false;
    if (flashcard) {
        flashcard.style.transform = '';
        flashcard.classList.remove('rotate-y-180');
    }
    
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
    const subTitle = card.subject_title || (card.subject ? card.subject.toUpperCase() : '');
    const introSubBadge = document.getElementById('card-intro-subject-badge');
    const introSubText = document.getElementById('card-intro-subject-text');
    if (introSubBadge && introSubText) {
        if (subTitle) {
            introSubText.textContent = subTitle;
            introSubBadge.classList.remove('hidden');
            introSubBadge.classList.add('inline-flex');
        } else {
            introSubBadge.classList.add('hidden');
            introSubBadge.classList.remove('inline-flex');
        }
    }

    const introModeText = document.getElementById('card-intro-mode-text');
    if (introModeText) {
        introModeText.textContent = card.reason_label || 'РЕЖИМ ЗНАКОМСТВА';
    }

    const introModeIcon = document.getElementById('card-intro-mode-icon');
    if (introModeIcon) {
        introModeIcon.textContent = card.reason_icon || 'school';
    }

    const introChapterBadge = document.getElementById('card-intro-chapter-badge');
    const introChapterText = document.getElementById('card-intro-chapter-text');
    const introChapterName = (card.chapter || card.phrase_text || '').trim();
    if (introChapterBadge && introChapterText) {
        if (introChapterName) {
            introChapterText.textContent = introChapterName;
            introChapterBadge.classList.remove('hidden');
            introChapterBadge.classList.add('inline-flex');
        } else {
            introChapterBadge.classList.add('hidden');
            introChapterBadge.classList.remove('inline-flex');
        }
    }

    const isIntroCloze = card.content_type === 'cloze' || /\{\{c\d+::/.test(card.text);
    const termEl = document.getElementById('card-intro-term');
    const secEl = document.getElementById('card-intro-secondary');
    if (termEl) {
        if (isIntroCloze) {
            termEl.innerHTML = formatClozeHTML(card.text, (card.intro_phase || 0) === 1 ? false : true);
        } else {
            termEl.textContent = card.text;
        }
        applyDynamicCardTypography(termEl, card.text);
    }
    if (secEl) {
        // Защита от спойлеров: в фазе 1 (Recall / самопроверка) secondary_text строго скрыт!
        // Показывается только в фазе 0 (Preview) при первом ознакомлении с ответом
        const currentIntroPhase = card.intro_phase || 0;
        if (currentIntroPhase === 0 && card.secondary_text && card.secondary_text !== '---') {
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
                        class="w-full bg-primary text-on-primary py-2.5 rounded-xl font-bold tracking-wide hover:opacity-90 transition-all text-xs font-mono uppercase shadow-xs flex items-center justify-center gap-1.5">
                    <span class="material-symbols-outlined text-[15px]">quiz</span>
                    <span>ПРОВЕРИТЬ СЕБЯ В ПАМЯТИ</span>
                </button>
                <button onclick="event.stopPropagation(); window.fastTrackIntroduction()" 
                        class="w-full border border-neutral-200 dark:border-neutral-700 text-neutral-500 dark:text-neutral-400 hover:text-primary hover:border-primary py-2 rounded-xl font-bold tracking-wide transition-all text-[11px] font-mono uppercase flex items-center justify-center gap-1.5">
                    <span class="material-symbols-outlined text-[15px]">verified</span>
                    <span>ЗНАЮ НАИЗУСТЬ</span>
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
                        <div class="text-xs font-mono font-bold text-primary uppercase">ПОКАЗАТЬ ОТВЕТ И АССОЦИАЦИЮ</div>
                        <div class="text-[11px] text-neutral-500 dark:text-neutral-400 mt-1.5 font-sans">Попробуйте воспроизвести значение по памяти</div>
                    </div>
                `;
            }
            if (footerEl) {
                footerEl.innerHTML = `
                    <button onclick="event.stopPropagation(); window.toggleIntroRecall()" 
                            class="w-full border border-primary text-primary py-2.5 rounded-xl font-bold tracking-wide hover:bg-primary/10 transition-all text-xs font-mono uppercase flex items-center justify-center gap-1.5">
                        <span class="material-symbols-outlined text-[15px]">visibility</span>
                        <span>ПОКАЗАТЬ ОТВЕТ</span>
                    </button>
                    <button onclick="event.stopPropagation(); window.fastTrackIntroduction()" 
                            class="w-full border border-neutral-200 dark:border-neutral-700 text-neutral-500 dark:text-neutral-400 hover:text-primary hover:border-primary py-2 rounded-xl font-bold tracking-wide transition-all text-[11px] font-mono uppercase flex items-center justify-center gap-1.5">
                        <span class="material-symbols-outlined text-[15px]">verified</span>
                        <span>ЗНАЮ НАИЗУСТЬ</span>
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
                            class="w-full bg-primary text-on-primary py-2.5 rounded-xl font-bold tracking-wide hover:opacity-90 transition-all text-xs font-mono uppercase shadow-xs flex items-center justify-center gap-1.5">
                        <span class="material-symbols-outlined text-[15px]">check_circle</span>
                        <span>ВСПОМНИЛ И ЗАКРЕПИЛ</span>
                    </button>
                    <button onclick="event.stopPropagation(); stepBackIntroduction()" 
                            class="w-full border border-neutral-200 dark:border-neutral-700 text-neutral-500 dark:text-neutral-400 hover:text-primary hover:border-primary py-2 rounded-xl font-bold tracking-wide transition-all text-[11px] font-mono uppercase flex items-center justify-center gap-1.5">
                        <span class="material-symbols-outlined text-[15px]">arrow_back</span>
                        <span>ВЕРНУТЬСЯ К ОБЗОРУ</span>
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
window.advanceIntroduction = advanceIntroduction;

function completeIntroduction() {
    const card = cardsQueue[currentIndex];
    triggerHaptic('success');
    card.has_seen_intro = true;
    card.state = 1; // Learning
    
    currentSessionStats.totalAnswered = (currentSessionStats.totalAnswered || 0) + 1;
    currentSessionStats.correctCount = (currentSessionStats.correctCount || 0) + 1;
    currentSessionStats.newCount = (currentSessionStats.newCount || 0) + 1;
    if (!currentSessionStats.reviewedCards) currentSessionStats.reviewedCards = [];
    currentSessionStats.reviewedCards.push(card);

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
window.completeIntroduction = completeIntroduction;

window.fastTrackIntroduction = function() {
    const card = cardsQueue[currentIndex];
    if (!card) return;
    triggerHaptic('success');
    card.has_seen_intro = true;
    card.state = 2; // Сразу в Review
    
    currentSessionStats.totalAnswered = (currentSessionStats.totalAnswered || 0) + 1;
    currentSessionStats.correctCount = (currentSessionStats.correctCount || 0) + 1;
    currentSessionStats.newCount = (currentSessionStats.newCount || 0) + 1;
    if (!currentSessionStats.reviewedCards) currentSessionStats.reviewedCards = [];
    currentSessionStats.reviewedCards.push(card);

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

