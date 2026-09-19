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

