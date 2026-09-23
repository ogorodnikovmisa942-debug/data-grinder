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
            'civil_law': 'ГРАЖДАНСКОЕ ПРАВО',
            'sudoust': 'СУДОУСТРОЙСТВО РФ',
            'sudoustr': 'СУДОУСТРОЙСТВО РФ',
            'sudoustroystvo': 'СУДОУСТРОЙСТВО РФ',
            'court_system': 'СУДОУСТРОЙСТВО РФ',
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
    const now = new Date();
    const utcMinutes = now.getUTCHours() * 60 + now.getUTCMinutes();
    return utcMinutes >= 990 || utcMinutes < 30; // 16:30 - 00:30 UTC / 19:30 - 03:30 MSK
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

        if (bannerTitle) bannerTitle.innerHTML = titleText;
        bannerBadge.className = "text-secondary font-bold font-mono";
        bannerBadge.textContent = `[СКИДКА 50% ЧЕРЕЗ ${h}ч ${m}м]`;
        if (btnDeferred) {
            btnDeferred.innerHTML = `<span class="material-symbols-outlined text-[15px]">dark_mode</span><span>НОЧЬЮ (-50%)</span>`;
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

