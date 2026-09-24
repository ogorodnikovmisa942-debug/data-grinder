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

