
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
    practiceAnswerSubmitted = false;

    try {
        const res = await apiFetch(`/api/practice/session?subject=${encodeURIComponent(sub)}&count=10`);
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

    if (msgEl) {
        if (isRetrySession) {
            msgEl.textContent = "Ошибки успешно проработаны! Спорные узлы и критерии закреплены в памяти.";
        } else if (percent >= 80) {
            msgEl.textContent = "Превосходно! Вы безошибочно различаете правовые режимы, звенья инстанций и водоразделы.";
        } else if (percent >= 50) {
            msgEl.textContent = "Хороший результат. Рекомендуем повторить спорные узлы через Каркас знаний или колоду FSRS.";
        } else {
            msgEl.textContent = "Материал требует закрепления. Изучите структуру понятий в Каркасе знаний перед следующей практикой.";
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

    // Настройка кнопки прохождения новой сессии
    const newSessionBtn = document.getElementById('practice-new-session-btn');
    if (newSessionBtn) {
        newSessionBtn.onclick = () => startPracticeSession(currentPracticeSubject);
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