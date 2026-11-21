/* The web app — the Tasks screen (static/app.js says how the app's scripts fit together).
 *
 * Reminders in three panes: the group / trigger filters, the list with its sort modes, the detail pane,
 * the task modal (priority, triggers) and the group manager.
 */

// ══════════════════════════════════════════════════════════════════════
// 5. SCREEN 2: TASKS (Reminders) CONTROLLER — 3-Pane Architecture
// ══════════════════════════════════════════════════════════════════════

const tasksScrollList = document.getElementById("tasks-scroll-list");
const tasksSearchInput = document.getElementById("tasks-search-input");
const tasksGroupFilterList = document.getElementById("tasks-group-filter-list");
const taskDetailPane = document.getElementById("task-detail-pane");
const taskToggleCompleted = document.getElementById("task-toggle-completed");
const btnAddTaskTop = document.getElementById("btn-add-task-top");
const btnAddTaskFab = document.getElementById("btn-add-task-fab");
const btnManageGroups = document.getElementById("btn-manage-groups");

// Task Modal elements
const taskModal = document.getElementById("task-modal");
const taskModalClose = document.getElementById("task-modal-close");
const taskModalTitle = document.getElementById("task-modal-title");
const taskForm = document.getElementById("task-form");
const taskIdInput = document.getElementById("task-id");
const taskTitleInput = document.getElementById("task-title");
const taskDescInput = document.getElementById("task-desc");
const taskPrioControl = document.getElementById("task-prio-control");
const taskGroupSelect = document.getElementById("task-group-select");
const taskTrigTimeEnable = document.getElementById("task-trig-time-enable");
const taskTrigTimeVal = document.getElementById("task-trig-time-val");
const taskTrigLocEnable = document.getElementById("task-trig-loc-enable");
const taskTrigLocGroup = document.getElementById("task-trig-loc-group");
const taskTrigLocZone = document.getElementById("task-trig-loc-zone");
const taskTrigLocEvent = document.getElementById("task-trig-loc-event");
const taskTrigCarEnable = document.getElementById("task-trig-car-enable");
const taskTrigCarVal = document.getElementById("task-trig-car-val");
const taskTrigWakeEnable = document.getElementById("task-trig-wake-enable");
const btnTaskCancel = document.getElementById("btn-task-cancel");
const btnTaskSave = document.getElementById("btn-task-save");
const btnTaskDelete = document.getElementById("btn-task-delete");

// Group Modal elements
const groupModal = document.getElementById("group-modal");
const groupModalClose = document.getElementById("group-modal-close");
const btnGroupClose = document.getElementById("btn-group-close");
const groupManagerList = document.getElementById("group-manager-list");
const groupForm = document.getElementById("group-form");
const newGroupEmoji = document.getElementById("new-group-emoji");
const newGroupName = document.getElementById("new-group-name");
const newGroupColor = document.getElementById("new-group-color");
const newGroupDesc = document.getElementById("new-group-desc");

let currentSelectedPriority = "medium";
let selectedTaskId = null;
let showCompletedTasks = false; // Hidden by default as requested!
let collapsedSections = { "completed": true }; // Completed section collapsed by default

// Priority segmented buttons in task modal
taskPrioControl?.querySelectorAll(".segmented-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        taskPrioControl.querySelectorAll(".segmented-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        currentSelectedPriority = btn.getAttribute("data-prio");
    });
});

// Trigger toggle handlers in modal
taskTrigTimeEnable?.addEventListener("change", () => taskTrigTimeVal.classList.toggle("hidden", !taskTrigTimeEnable.checked));
taskTrigLocEnable?.addEventListener("change", () => taskTrigLocGroup.classList.toggle("hidden", !taskTrigLocEnable.checked));
taskTrigCarEnable?.addEventListener("change", () => taskTrigCarVal.classList.toggle("hidden", !taskTrigCarEnable.checked));

// Show/Hide Completed toggle
taskToggleCompleted?.addEventListener("change", () => {
    showCompletedTasks = taskToggleCompleted.checked;
    renderTasks();
});

async function loadTasks() {
    try {
        const [tRes, gRes, zRes] = await Promise.all([
            fetch("/api/tasks"),
            fetch("/api/task-groups"),
            fetch("/api/zones")
        ]);
        if (tRes.ok) tasks = await tRes.json();
        if (gRes.ok) taskGroups = await gRes.json();
        if (zRes.ok) zones = await zRes.json();

        renderTaskFiltersSidebar();
        renderTasks();
        populateTaskModalOptions();
    } catch (e) {
        console.error("Error loading tasks:", e);
    }
}

function renderTaskFiltersSidebar() {
    if (!tasksGroupFilterList) return;
    const activeTasks = tasks.filter(t => t.status !== "completed");
    const total = activeTasks.length;

    // Update counts
    const cntAll = document.getElementById("tasks-count-all");
    if (cntAll) cntAll.textContent = total;

    const countTrigger = (type) => activeTasks.filter(t => {
        try {
            const trig = JSON.parse(t.triggers || "{}");
            if (type === "wake") return !!trig.wake;
            return !!trig[type];
        } catch (e) { return false; }
    }).length;

    const cntTime = document.getElementById("tasks-count-time");
    if (cntTime) cntTime.textContent = countTrigger("time");
    const cntLoc = document.getElementById("tasks-count-location");
    if (cntLoc) cntLoc.textContent = countTrigger("location");
    const cntCar = document.getElementById("tasks-count-car");
    if (cntCar) cntCar.textContent = countTrigger("car");
    const cntWake = document.getElementById("tasks-count-wake");
    if (cntWake) cntWake.textContent = countTrigger("wake");

    // Groups list
    let html = `
            <div class="filter-nav-item ${taskGroupFilter === null ? "active" : ""}" data-group="all">
                <span>All Reminders</span>
                <span class="count-badge">${total}</span>
            </div>
            <div class="filter-nav-item ${taskGroupFilter === "general" ? "active" : ""}" data-group="general">
                <span style="display:flex;align-items:center;gap:6px;">
                    <span>📁</span>
                    <span>General</span>
                </span>
                <span class="count-badge">${activeTasks.filter(t => !t.groupId && !t.group_id || t.groupId === "general" || t.group_id === "general").length}</span>
            </div>
        `;
    taskGroups.forEach(g => {
        if (g.id === "general") return;
        const cnt = activeTasks.filter(t => (t.groupId === g.id || t.group_id === g.id)).length;
        html += `
                <div class="filter-nav-item ${taskGroupFilter === g.id ? "active" : ""}" data-group="${g.id}">
                    <span style="display:flex;align-items:center;gap:6px;">
                        <span>${g.emoji || "📁"}</span>
                        <span>${escapeHtml(g.name)}</span>
                    </span>
                    <span class="count-badge">${cnt}</span>
                </div>
            `;
    });
    tasksGroupFilterList.innerHTML = html;

    tasksGroupFilterList.querySelectorAll(".filter-nav-item").forEach(item => {
        item.addEventListener("click", () => {
            const gid = item.getAttribute("data-group");
            taskGroupFilter = gid === "all" ? null : gid;
            renderTaskFiltersSidebar();
            renderTasks();
        });
    });

    // Trigger filters
    document.querySelectorAll("#tasks-pane-filters [data-trigger]").forEach(item => {
        const trig = item.getAttribute("data-trigger");
        item.classList.toggle("active", taskTriggerFilter === trig);
        item.onclick = () => {
            taskTriggerFilter = (taskTriggerFilter === trig) ? null : trig;
            renderTaskFiltersSidebar();
            renderTasks();
        };
    });

    // Sort mode items
    document.querySelectorAll("#tasks-pane-filters [data-sort]").forEach(item => {
        const sm = item.getAttribute("data-sort");
        item.classList.toggle("active", taskSortMode === sm);
        item.onclick = () => {
            taskSortMode = sm;
            renderTaskFiltersSidebar();
            renderTasks();
        };
    });
}

tasksSearchInput?.addEventListener("input", renderTasks);

function renderTasks() {
    if (!tasksScrollList) return;
    const q = (tasksSearchInput?.value || "").toLowerCase().trim();

    // Filter active tasks
    const filteredActive = tasks.filter(t => {
        if (t.status === "completed") return false;
        if (taskGroupFilter !== null) {
            const tgId = t.groupId || t.group_id || "general";
            if (taskGroupFilter === "general") {
                if (tgId !== "general" && tgId !== "") return false;
            } else {
                if (tgId !== taskGroupFilter) return false;
            }
        }
        if (taskTriggerFilter) {
            try {
                const trig = JSON.parse(t.triggers || "{}");
                if (taskTriggerFilter === "wake" && !trig.wake) return false;
                if (taskTriggerFilter !== "wake" && !trig[taskTriggerFilter]) return false;
            } catch (e) { return false; }
        }
        if (q) {
            const match = (t.title || "").toLowerCase().includes(q) || (t.description || "").toLowerCase().includes(q);
            if (!match) return false;
        }
        return true;
    });

    // Completed tasks
    const completedList = tasks.filter(t => {
        if (t.status !== "completed") return false;
        if (taskGroupFilter && t.groupId !== taskGroupFilter) return false;
        if (q) {
            const match = (t.title || "").toLowerCase().includes(q) || (t.description || "").toLowerCase().includes(q);
            if (!match) return false;
        }
        return true;
    });

    if (filteredActive.length === 0 && (!showCompletedTasks || completedList.length === 0)) {
        tasksScrollList.innerHTML = `
                <div class="pane-empty-state">
                    <i data-lucide="check-square" style="width:40px;height:40px;opacity:0.4;"></i>
                    <span>No reminders match your filters.</span>
                </div>
            `;
        refreshIcons();
        renderTaskDetail();
        return;
    }

    let html = "";
    const grouped = {};
    const sectionTitles = {};

    if (taskSortMode === "priority") {
        const order = ["high", "medium", "low"];
        order.forEach(p => { grouped[p] = []; });
        sectionTitles["high"] = "🔴 High Priority";
        sectionTitles["medium"] = "🟡 Medium Priority";
        sectionTitles["low"] = "🟢 Low Priority";

        filteredActive.forEach(t => {
            const p = (t.priority || "medium").toLowerCase();
            if (!grouped[p]) grouped[p] = [];
            grouped[p].push(t);
        });
    } else if (taskSortMode === "due") {
        // Compute due date categories matching Android
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        const todayMs = today.getTime();
        const tomorrowMs = todayMs + 86400000;
        const weekMs = todayMs + 7 * 86400000;

        const order = ["overdue", "today", "tomorrow", "thisweek", "later", "nodate"];
        order.forEach(k => { grouped[k] = []; });
        sectionTitles["overdue"] = "🔴 Overdue";
        sectionTitles["today"] = "📅 Today";
        sectionTitles["tomorrow"] = "📅 Tomorrow";
        sectionTitles["thisweek"] = "📅 This Week";
        sectionTitles["later"] = "📅 Later";
        sectionTitles["nodate"] = "📋 No Due Date";

        filteredActive.forEach(t => {
            if (!t.dueDate && !t.due_date) {
                grouped["nodate"].push(t);
            } else {
                try {
                    const d = new Date(t.dueDate || t.due_date);
                    d.setHours(0, 0, 0, 0);
                    const tMs = d.getTime();
                    if (isNaN(tMs)) grouped["nodate"].push(t);
                    else if (tMs < todayMs) grouped["overdue"].push(t);
                    else if (tMs < tomorrowMs) grouped["today"].push(t);
                    else if (tMs < tomorrowMs + 86400000) grouped["tomorrow"].push(t);
                    else if (tMs < weekMs) grouped["thisweek"].push(t);
                    else grouped["later"].push(t);
                } catch (e) {
                    grouped["nodate"].push(t);
                }
            }
        });
    } else {
        // Group by task group (check both groupId and group_id)
        grouped["general"] = [];
        sectionTitles["general"] = "📁 General";
        taskGroups.forEach(g => {
            grouped[g.id] = [];
            sectionTitles[g.id] = `${g.emoji || "📁"} ${escapeHtml(g.name)}`;
        });

        filteredActive.forEach(t => {
            const gid = t.groupId || t.group_id || "general";
            if (!grouped[gid]) grouped[gid] = [];
            grouped[gid].push(t);
        });
    }

    // Render each active section
    Object.keys(grouped).forEach(secKey => {
        const secTasks = grouped[secKey];
        if (!secTasks || secTasks.length === 0) return;

        const isCollapsed = !!collapsedSections[secKey];
        const title = sectionTitles[secKey] || secKey;

        html += `
                <div class="task-group-section" data-section="${secKey}">
                    <div class="task-group-header ${isCollapsed ? "collapsed" : ""}" data-toggle-sec="${secKey}">
                        <span>${title} (${secTasks.length})</span>
                        <i data-lucide="chevron-down" class="chevron" style="width:16px;height:16px;"></i>
                    </div>
                    <div class="task-group-tasks ${isCollapsed ? "hidden" : ""}">
                        ${secTasks.map(t => renderTaskCardHtml(t)).join("")}
                    </div>
                </div>
            `;
    });

    // Completed Section (Only if showCompletedTasks is true!)
    if (showCompletedTasks && completedList.length > 0) {
        const isCompletedCollapsed = !!collapsedSections["completed"];
        html += `
                <div class="task-group-section" style="margin-top:16px;" data-section="completed">
                    <div class="task-group-header ${isCompletedCollapsed ? "collapsed" : ""}" data-toggle-sec="completed" style="color:var(--mute);">
                        <span>Completed (${completedList.length})</span>
                        <i data-lucide="chevron-down" class="chevron" style="width:16px;height:16px;"></i>
                    </div>
                    <div class="task-group-tasks ${isCompletedCollapsed ? "hidden" : ""}">
                        ${completedList.map(t => renderTaskCardHtml(t)).join("")}
                    </div>
                </div>
            `;
    }

    tasksScrollList.innerHTML = html;

    // Wire section collapse / expand clicks
    tasksScrollList.querySelectorAll("[data-toggle-sec]").forEach(hdr => {
        hdr.addEventListener("click", () => {
            const sec = hdr.getAttribute("data-toggle-sec");
            collapsedSections[sec] = !collapsedSections[sec];
            const secEl = tasksScrollList.querySelector(`.task-group-section[data-section="${sec}"]`);
            if (secEl) {
                const tasksEl = secEl.querySelector(".task-group-tasks");
                const isNowCollapsed = !!collapsedSections[sec];
                hdr.classList.toggle("collapsed", isNowCollapsed);
                if (tasksEl) tasksEl.classList.toggle("hidden", isNowCollapsed);
            }
        });
    });

    // Wire task card clicks
    tasksScrollList.querySelectorAll(".task-card").forEach(card => {
        const tid = card.getAttribute("data-id");
        card.addEventListener("click", (e) => {
            if (e.target.closest(".task-checkbox")) return;
            selectedTaskId = tid;
            tasksScrollList.querySelectorAll(".task-card").forEach(c => c.classList.remove("active"));
            card.classList.add("active");
            renderTaskDetail();
        });
    });

    // Wire checkbox clicks
    tasksScrollList.querySelectorAll(".task-checkbox").forEach(box => {
        box.addEventListener("click", async (e) => {
            e.stopPropagation();
            const tid = box.getAttribute("data-id");
            const isCompleted = box.classList.contains("completed");
            await toggleTaskStatus(tid, !isCompleted);
        });
    });

    // Auto-select first task if none selected and on desktop
    if (!selectedTaskId && filteredActive.length > 0 && window.innerWidth >= 768) {
        selectedTaskId = filteredActive[0].id;
        const firstCard = tasksScrollList.querySelector(`.task-card[data-id="${selectedTaskId}"]`);
        if (firstCard) firstCard.classList.add("active");
    }
    renderTaskDetail();
    refreshIcons();
}

function renderTaskCardHtml(t) {
    const isCompleted = t.status === "completed";
    const p = (t.priority || "medium").toLowerCase();
    let triggers = {};
    try { triggers = JSON.parse(t.triggers || "{}"); } catch (e) {}

    const group = taskGroups.find(g => g.id === (t.group_id || t.groupId));
    const isSelected = t.id === selectedTaskId;

    return `
            <div class="task-card ${isSelected ? "active" : ""}" data-id="${t.id}">
                <div class="task-checkbox prio-${p} ${isCompleted ? "completed" : ""}" data-id="${t.id}">
                    ${isCompleted ? `<i data-lucide="check" style="width:14px;height:14px;stroke-width:3;color:var(--on-primary);"></i>` : ""}
                </div>
                <div class="task-content">
                    <span class="task-title ${isCompleted ? "completed" : ""}">${escapeHtml(t.title)}</span>
                    ${t.description ? `<span class="task-desc">${escapeHtml(t.description)}</span>` : ""}
                    <div class="task-tags-row">
                        ${group ? `
                            <span class="badge-tag" style="background:${group.color ? group.color + "22" : "rgba(247,245,240,0.1)"};color:${group.color || "var(--ink)"};">
                                ${group.emoji || "📁"} ${escapeHtml(group.name)}
                            </span>
                        ` : ""}
                        ${triggers.time ? `<span class="badge-tag">⏰ ${escapeHtml(triggers.time.replace("T", " "))}</span>` : ""}
                        ${triggers.location && typeof triggers.location === "object"
                        ? `<span class="badge-tag">📍 ${escapeHtml(triggers.location.location || "")} (${escapeHtml(triggers.location.event || "arrive")})</span>` : ""}
                        ${triggers.car ? `<span class="badge-tag">🚗 Car (${escapeHtml(triggers.car)})</span>` : ""}
                        ${triggers.wake ? `<span class="badge-tag">☀️ Wake Up</span>` : ""}
                    </div>
                </div>
            </div>
        `;
}

function toLocalDatetimeValue(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function getZoneOptionsHtml() {
    const zoneList = zones.length ? zones : [{ name: "Home" }, { name: "Work" }];
    return zoneList.map(z => {
        const zname = (z && typeof z === "object" && z.name != null) ? z.name : String(z);
        return `<option value="${escapeHtml(zname)}">${escapeHtml(zname)}</option>`;
    }).join("");
}

function renderTaskDetail() {
    if (!taskDetailPane) return;
    const task = tasks.find(x => x.id === selectedTaskId);
    if (!task) {
        taskDetailPane.innerHTML = `
                <div class="pane-empty-state">
                    <i data-lucide="check-square" style="width:40px;height:40px;opacity:0.4;"></i>
                    <span>Select a reminder to view its details</span>
                </div>
            `;
        refreshIcons();
        return;
    }

    const isCompleted = task.status === "completed";
    const p = (task.priority || "medium").toLowerCase();
    let triggers = {};
    try { triggers = JSON.parse(task.triggers || "{}"); } catch (e) {}

    const groupOptions = taskGroups.map(g =>
        `<option value="${escapeHtml(g.id)}">${g.emoji || "📁"} ${escapeHtml(g.name)}</option>`
    ).join("");

    taskDetailPane.innerHTML = `
            <div class="task-detail-inline">
                <div class="task-detail-head">
                    <div class="task-detail-head-row">
                        <div class="segmented-control task-status-seg" id="td-status">
                            <div class="segmented-btn ${isCompleted ? "" : "active"}" data-status="needs_action">Active</div>
                            <div class="segmented-btn ${isCompleted ? "active" : ""}" data-status="completed">Done</div>
                        </div>
                        <div class="task-detail-actions">
                            <button class="icon-btn" id="td-reset" title="Discard changes"><i data-lucide="rotate-ccw"></i></button>
                            <button class="btn-destructive-outline" id="td-delete"><i data-lucide="trash-2" style="width:14px;height:14px;"></i> Delete</button>
                            <button class="btn-primary" id="td-save"><i data-lucide="check" style="width:14px;height:14px;"></i> Save</button>
                        </div>
                    </div>
                </div>

                <div class="task-detail-form">
                    <div class="form-group">
                        <label class="form-label" for="td-title">Title</label>
                        <input type="text" class="form-control" id="td-title" value="${escapeHtml(task.title)}">
                    </div>
                    <div class="task-detail-grid">
                        <div class="form-group">
                            <label class="form-label">Priority</label>
                            <div class="segmented-control" id="td-priority">
                                <div class="segmented-btn ${p === "low" ? "active" : ""}" data-prio="low">Low</div>
                                <div class="segmented-btn ${p === "medium" ? "active" : ""}" data-prio="medium">Medium</div>
                                <div class="segmented-btn ${p === "high" ? "active" : ""}" data-prio="high">High</div>
                            </div>
                        </div>
                        <div class="form-group">
                            <label class="form-label" for="td-group">Group</label>
                            <select class="form-control" id="td-group">
                                <option value="">Auto-detect Group</option>
                                ${groupOptions}
                            </select>
                        </div>
                    </div>
                    <div class="task-detail-grid">
                        <div class="form-group">
                            <label class="form-label" for="td-due">Due Date</label>
                            <input type="datetime-local" class="form-control" id="td-due" value="${escapeHtml(toLocalDatetimeValue(task.due_date || task.dueDate))}">
                        </div>
                        <div class="form-group">
                            <label class="form-label" for="td-status-note">Status</label>
                            <input type="text" class="form-control" id="td-status-note" value="${isCompleted ? "Completed" : "Needs Action"}" disabled style="opacity:0.6;">
                        </div>
                    </div>
                    <div class="form-group">
                        <label class="form-label" for="td-desc">Description</label>
                        <textarea class="form-control" id="td-desc" rows="3" placeholder="Extra notes or instructions...">${escapeHtml(task.description || "")}</textarea>
                    </div>
                </div>

                <div class="task-detail-triggers">
                    <span class="filter-sidebar-title">Triggers</span>
                    <div class="trigger-option">
                        <label class="trigger-label"><input type="checkbox" id="td-trig-time" ${triggers.time ? "checked" : ""}> ⏰ Time Reminder</label>
                        <input type="datetime-local" class="form-control trigger-val hidden" id="td-trig-time-val" value="${escapeHtml(toLocalDatetimeValue(triggers.time))}">
                    </div>
                    <div class="trigger-option">
                        <label class="trigger-label"><input type="checkbox" id="td-trig-loc" ${triggers.location ? "checked" : ""}> 📍 Location Trigger</label>
                        <div class="trigger-val trigger-fields hidden" id="td-trig-loc-fields" style="display:flex;gap:6px;">
                            <select class="form-control" id="td-trig-loc-zone" style="flex:2;">
                                ${getZoneOptionsHtml()}
                            </select>
                            <select class="form-control" id="td-trig-loc-event" style="flex:1;">
                                <option value="arrive">Arrive</option>
                                <option value="leave">Leave</option>
                            </select>
                        </div>
                    </div>
                    <div class="trigger-option">
                        <label class="trigger-label"><input type="checkbox" id="td-trig-car" ${triggers.car ? "checked" : ""}> 🚗 Car Trigger</label>
                        <select class="form-control trigger-val hidden" id="td-trig-car-val">
                            <option value="enter">Enter Car</option>
                            <option value="leave">Leave Car</option>
                        </select>
                    </div>
                    <div class="trigger-option">
                        <label class="trigger-label"><input type="checkbox" id="td-trig-wake" ${triggers.wake ? "checked" : ""}> ☀️ Wake Up Trigger</label>
                    </div>
                </div>
            </div>
        `;

    // Pre-fill trigger sub-controls
    const statusSegs = document.querySelectorAll("#td-status .segmented-btn");
    const prioSegs = document.querySelectorAll("#td-priority .segmented-btn");
    const trigTimeEnable = document.getElementById("td-trig-time");
    const trigTimeVal = document.getElementById("td-trig-time-val");
    const trigLocEnable = document.getElementById("td-trig-loc");
    const trigLocFields = document.getElementById("td-trig-loc-fields");
    const trigLocZone = document.getElementById("td-trig-loc-zone");
    const trigLocEvent = document.getElementById("td-trig-loc-event");
    const trigCarEnable = document.getElementById("td-trig-car");
    const trigCarVal = document.getElementById("td-trig-car-val");
    const trigWakeEnable = document.getElementById("td-trig-wake");

    if (task.status === "completed") {
        document.getElementById("td-status-note").value = "Completed";
    }

    if (triggers.time) { trigTimeVal.classList.remove("hidden"); }
    if (triggers.location && typeof triggers.location === "object") {
        trigLocFields.classList.remove("hidden");
        if (triggers.location.location) trigLocZone.value = triggers.location.location;
        if (triggers.location.event) trigLocEvent.value = triggers.location.event;
    }
    if (triggers.car) { trigCarVal.classList.remove("hidden"); trigCarVal.value = triggers.car; }

    // Live field reveal toggles
    function bindTriggerToggle(cb, field) {
        cb?.addEventListener("change", () => field.classList.toggle("hidden", !cb.checked));
    }
    bindTriggerToggle(trigTimeEnable, trigTimeVal);
    bindTriggerToggle(trigLocEnable, trigLocFields);
    bindTriggerToggle(trigCarEnable, trigCarVal);

    // Status & priority segmented controls
    statusSegs.forEach(btn => btn.addEventListener("click", () => {
        statusSegs.forEach(b => b.classList.toggle("active", b === btn));
        document.getElementById("td-status-note").value =
            btn.getAttribute("data-status") === "completed" ? "Completed" : "Needs Action";
    }));
    prioSegs.forEach(btn => btn.addEventListener("click", () => {
        prioSegs.forEach(b => b.classList.toggle("active", b === btn));
    }));

    // Save
    document.getElementById("td-save").addEventListener("click", async () => {
        const title = document.getElementById("td-title").value.trim();
        if (!title) return;

        const newStatusSeg = document.querySelector("#td-status .segmented-btn.active");
        const newPrioSeg = document.querySelector("#td-priority .segmented-btn.active");
        const status = newStatusSeg ? newStatusSeg.getAttribute("data-status") : task.status;
        const priority = newPrioSeg ? newPrioSeg.getAttribute("data-prio") : p;
        const dueVal = document.getElementById("td-due").value;

        const triggersPayload = {};
        if (trigTimeEnable.checked && trigTimeVal.value) triggersPayload.time = trigTimeVal.value;
        if (trigLocEnable.checked) {
            triggersPayload.location = {
                location: trigLocZone.value,
                event: trigLocEvent.value || "arrive"
            };
        }
        if (trigCarEnable.checked) triggersPayload.car = trigCarVal.value;
        if (trigWakeEnable.checked) triggersPayload.wake = true;

        try {
            const res = await fetch(`/api/tasks/${encodeURIComponent(task.id)}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    title: title,
                    description: document.getElementById("td-desc").value.trim() || null,
                    priority: priority,
                    group_id: document.getElementById("td-group").value || null,
                    due_date: dueVal || null,
                    status: status,
                    triggers: triggersPayload
                })
            });
            if (res.ok) {
                selectedTaskId = task.id;
                await loadTasks();
            }
        } catch (e) {
            console.error(e);
        }
    });

    // Reset (re-render from server state)
    document.getElementById("td-reset").addEventListener("click", () => renderTaskDetail());

    // Delete
    document.getElementById("td-delete").addEventListener("click", async () => {
        if (confirm("Delete this reminder?")) {
            await fetch(`/api/tasks/${encodeURIComponent(task.id)}`, { method: "DELETE" });
            selectedTaskId = null;
            loadTasks();
        }
    });

    refreshIcons();
}

async function toggleTaskStatus(taskId, completed) {
    try {
        await fetch(`/api/tasks/${encodeURIComponent(taskId)}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                status: completed ? "completed" : "needs_action"
            })
        });
        const t = tasks.find(x => x.id === taskId);
        if (t) t.status = completed ? "completed" : "needs_action";
        renderTasks();
        renderTaskFiltersSidebar();
    } catch (e) {
        console.error(e);
    }
}

// Modal populate and open
function populateTaskModalOptions() {
    if (!taskGroupSelect || !taskTrigLocZone) return;
    taskGroupSelect.innerHTML = `<option value="">Auto-detect Group</option>` +
        taskGroups.map(g => `<option value="${g.id}">${g.emoji || "📁"} ${escapeHtml(g.name)}</option>`).join("");

    const zoneOptions = zones.length ? zones : [{ name: "Home" }, { name: "Work" }];
    taskTrigLocZone.innerHTML = zoneOptions.map(z => {
        const zname = (z && typeof z === "object" && z.name != null) ? z.name : String(z);
        return `<option value="${escapeHtml(zname)}">${escapeHtml(zname)}</option>`;
    }).join("");
}

function openTaskEditor(taskId = null) {
    populateTaskModalOptions();
    taskForm.reset();
    taskTrigTimeVal.classList.add("hidden");
    taskTrigLocGroup.classList.add("hidden");
    taskTrigCarVal.classList.add("hidden");

    if (taskId) {
        const t = tasks.find(x => x.id === taskId);
        if (!t) return;
        taskModalTitle.textContent = "Edit Reminder";
        taskIdInput.value = t.id;
        taskTitleInput.value = t.title || "";
        taskDescInput.value = t.description || "";
        currentSelectedPriority = (t.priority || "medium").toLowerCase();
        taskGroupSelect.value = t.group_id || t.groupId || "";

        try {
            const trig = JSON.parse(t.triggers || "{}");
            if (trig.time) {
                taskTrigTimeEnable.checked = true;
                taskTrigTimeVal.value = trig.time;
                taskTrigTimeVal.classList.remove("hidden");
            }
            if (trig.location) {
                taskTrigLocEnable.checked = true;
                taskTrigLocZone.value = trig.location.location;
                taskTrigLocEvent.value = trig.location.event;
                taskTrigLocGroup.classList.remove("hidden");
            }
            if (trig.car) {
                taskTrigCarEnable.checked = true;
                taskTrigCarVal.value = trig.car;
                taskTrigCarVal.classList.remove("hidden");
            }
            if (trig.wake) {
                taskTrigWakeEnable.checked = true;
            }
        } catch (e) {}

        btnTaskDelete.classList.remove("hidden");
    } else {
        taskModalTitle.textContent = "New Reminder";
        taskIdInput.value = "";
        currentSelectedPriority = "medium";
        btnTaskDelete.classList.add("hidden");
    }

    taskPrioControl.querySelectorAll(".segmented-btn").forEach(b => {
        b.classList.toggle("active", b.getAttribute("data-prio") === currentSelectedPriority);
    });

    taskModal.classList.add("open");
}

btnAddTaskTop?.addEventListener("click", () => openTaskEditor());
btnAddTaskFab?.addEventListener("click", () => openTaskEditor());
taskModalClose?.addEventListener("click", () => taskModal.classList.remove("open"));
btnTaskCancel?.addEventListener("click", () => taskModal.classList.remove("open"));

btnTaskSave?.addEventListener("click", async () => {
    const title = taskTitleInput.value.trim();
    if (!title) return;

    const triggers = {};
    if (taskTrigTimeEnable.checked && taskTrigTimeVal.value) {
        triggers.time = taskTrigTimeVal.value;
    }
    if (taskTrigLocEnable.checked) {
        triggers.location = {
            location: taskTrigLocZone.value,
            event: taskTrigLocEvent.value
        };
    }
    if (taskTrigCarEnable.checked) {
        triggers.car = taskTrigCarVal.value;
    }
    if (taskTrigWakeEnable.checked) {
        triggers.wake = true;
    }

    const payload = {
        title: title,
        description: taskDescInput.value.trim() || null,
        priority: currentSelectedPriority,
        group_id: taskGroupSelect.value || null,
        triggers: triggers
    };

    const tid = taskIdInput.value;
    try {
        if (tid) {
            await fetch(`/api/tasks/${encodeURIComponent(tid)}`, {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
        } else {
            await fetch("/api/tasks", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            });
        }
        taskModal.classList.remove("open");
        loadTasks();
    } catch (e) {
        console.error(e);
    }
});

btnTaskDelete?.addEventListener("click", async () => {
    const tid = taskIdInput.value;
    if (!tid) return;
    if (confirm("Delete this reminder?")) {
        try {
            await fetch(`/api/tasks/${encodeURIComponent(tid)}`, { method: "DELETE" });
            taskModal.classList.remove("open");
            if (selectedTaskId === tid) selectedTaskId = null;
            loadTasks();
        } catch (e) {
            console.error(e);
        }
    }
});

// Group Manager Modal
btnManageGroups?.addEventListener("click", () => {
    renderGroupManagerList();
    groupModal.classList.add("open");
});
groupModalClose?.addEventListener("click", () => groupModal.classList.remove("open"));
btnGroupClose?.addEventListener("click", () => groupModal.classList.remove("open"));

function renderGroupManagerList() {
    if (!groupManagerList) return;
    groupManagerList.innerHTML = taskGroups.map(g => `
            <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 10px;background:rgba(247,245,240,0.03);border:1px solid var(--hairline);border-radius:var(--radius-md);">
                <div style="display:flex;align-items:center;gap:8px;">
                    <span>${g.emoji || "📁"}</span>
                    <strong style="font-size:13px;color:var(--ink);">${escapeHtml(g.name)}</strong>
                    <span style="width:10px;height:10px;border-radius:var(--radius-full);background:${g.color || "#6366f1"};"></span>
                </div>
                ${g.id !== "general" ? `
                    <button class="icon-btn destructive btn-delete-group" data-id="${g.id}" style="width:28px;height:28px;">
                        <i data-lucide="trash-2" style="width:13px;height:13px;"></i>
                    </button>
                ` : ""}
            </div>
        `).join("");

    groupManagerList.querySelectorAll(".btn-delete-group").forEach(btn => {
        btn.addEventListener("click", async () => {
            const gid = btn.getAttribute("data-id");
            if (confirm("Delete this group? Tasks will be moved to General.")) {
                try {
                    await fetch(`/api/task-groups/${encodeURIComponent(gid)}`, { method: "DELETE" });
                    await loadTasks();
                    renderGroupManagerList();
                } catch (e) {
                    console.error(e);
                }
            }
        });
    });
    refreshIcons();
}

groupForm?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = newGroupName.value.trim();
    if (!name) return;

    try {
        await fetch("/api/task-groups", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                name: name,
                emoji: newGroupEmoji.value.trim() || "📁",
                color: newGroupColor.value || "#6366f1",
                description: newGroupDesc.value.trim() || ""
            })
        });
        groupForm.reset();
        newGroupEmoji.value = "📁";
        newGroupColor.value = "#6366f1";
        await loadTasks();
        renderGroupManagerList();
    } catch (err) {
        console.error(err);
    }
});
