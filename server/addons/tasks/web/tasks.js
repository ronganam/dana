/* Tasks — the tasks addon's page (Dana.panel, static/dana.js): reminders in three panes (the group /
 * trigger filters, the list with its sort modes, the detail pane), the new-reminder modal and the group
 * manager. Above them the core Today strip (the morning briefing and the weekly-review banner), hosted
 * with api.piece("today", el).
 *
 *   GET  /api/tasks · POST /api/tasks · PUT|DELETE /api/tasks/{id}
 *   GET  /api/task-groups · POST /api/task-groups · DELETE /api/task-groups/{id}
 *   GET  /api/zones (the user's zones for location triggers: Settings → Zones)
 *
 * The pure part is tasks-core.js (DanaTasksCore; tests/test_web_tasks.mjs).
 */
(() => {
  "use strict";
  let A = null;           // the page's api
  let C = null;           // DanaTasksCore
  let root = null;        // the page's <section>
  const S = {
    tasks: [], groups: [], zones: [],
    sort: "group",        // "group" | "priority" | "due"
    group: null,          // a group id, or null: all
    trigger: null,        // a trigger kind, or null
    selected: null,       // the task in the detail pane
    showDone: false,
    collapsed: { completed: true },
    priority: "medium",   // the new-reminder modal's choice
  };
  const $ = (id) => root.querySelector(`#${id}`);
  const esc = (s) => A.esc(s);

  const TEMPLATE = `
    <header class="top-bar">
      <span class="top-bar-title">Reminders</span>
      <div class="top-bar-actions">
        <button class="btn-primary" id="btn-add-task-top"><i data-lucide="plus" style="width:16px;height:16px;"></i><span>New Reminder</span></button>
      </div>
    </header>
    <div class="hidden" id="tasks-today"></div>
    <div class="three-pane-container" id="tasks-three-pane">
      <div class="pane-filters" id="tasks-pane-filters">
        <div class="filter-sidebar-section">
          <span class="filter-sidebar-title">Sort By</span>
          <div class="filter-nav-item active" data-sort="group"><span>📁 Group</span></div>
          <div class="filter-nav-item" data-sort="priority"><span>⚡ Priority</span></div>
          <div class="filter-nav-item" data-sort="due"><span>📅 Due Date</span></div>
        </div>
        <div class="filter-sidebar-section">
          <div style="display:flex;align-items:center;justify-content:space-between;">
            <span class="filter-sidebar-title">Groups</span>
            <button class="icon-btn" id="btn-manage-groups" title="Manage Groups" style="width:24px;height:24px;"><i data-lucide="settings" style="width:13px;height:13px;"></i></button>
          </div>
          <div id="tasks-group-filter-list"></div>
        </div>
        <div class="filter-sidebar-section">
          <span class="filter-sidebar-title">Triggers</span>
          <div class="filter-nav-item" data-trigger="time"><span>⏰ Time</span><span class="count-badge" data-count="time">0</span></div>
          <div class="filter-nav-item" data-trigger="location"><span>📍 Location</span><span class="count-badge" data-count="location">0</span></div>
          <div class="filter-nav-item" data-trigger="car"><span>🚗 Car</span><span class="count-badge" data-count="car">0</span></div>
          <div class="filter-nav-item" data-trigger="wake"><span>☀️ Wake Up</span><span class="count-badge" data-count="wake">0</span></div>
        </div>
        <div class="filter-sidebar-section" style="margin-top:auto;border-top:1px solid var(--hairline);padding-top:12px;">
          <label style="display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--body);cursor:pointer;padding:4px;">
            <input type="checkbox" id="task-toggle-completed" style="accent-color:var(--accent);"><span>Show completed</span>
          </label>
        </div>
      </div>
      <div class="pane-list" id="tasks-pane-list">
        <div class="pane-master-header"><input type="text" class="form-control" id="tasks-search-input" placeholder="Search reminders..."></div>
        <div class="pane-master-list" id="tasks-scroll-list"></div>
      </div>
      <div class="pane-content" id="task-detail-pane"></div>
    </div>
    <button class="fab-btn" id="btn-add-task-fab" title="New Reminder"><i data-lucide="plus"></i></button>

    <div class="modal-overlay" id="task-modal">
      <div class="modal-sheet">
        <div class="modal-header">
          <h3 class="modal-title">New Reminder</h3>
          <button class="icon-btn" data-close="task-modal"><i data-lucide="x"></i></button>
        </div>
        <form id="task-form" class="modal-body">
          <div class="form-group">
            <label class="form-label" for="task-title">Title</label>
            <input type="text" class="form-control" id="task-title" required placeholder="What do you need to do?">
          </div>
          <div class="form-group">
            <label class="form-label" for="task-desc">Description (Optional)</label>
            <textarea class="form-control" id="task-desc" placeholder="Extra notes or instructions..."></textarea>
          </div>
          <div class="form-group">
            <label class="form-label">Priority</label>
            <div class="segmented-control" id="task-prio-control">
              <div class="segmented-btn" data-prio="low">Low</div>
              <div class="segmented-btn active" data-prio="medium">Medium</div>
              <div class="segmented-btn" data-prio="high">High</div>
            </div>
          </div>
          <div class="form-group">
            <label class="form-label" for="task-group-select">Group</label>
            <select class="form-control" id="task-group-select"></select>
          </div>
          <div class="form-group">
            <label class="form-label">Triggers (Optional)</label>
            <div style="display:flex;flex-direction:column;gap:8px;">
              <label class="trigger-label"><input type="checkbox" id="task-trig-time-enable"> Time Reminder</label>
              <input type="datetime-local" class="form-control hidden" id="task-trig-time-val">
              <label class="trigger-label"><input type="checkbox" id="task-trig-loc-enable"> Location Trigger</label>
              <div class="hidden" id="task-trig-loc-group" style="display:flex;gap:6px;">
                <select class="form-control" id="task-trig-loc-zone" style="flex:2;"></select>
                <select class="form-control" id="task-trig-loc-event" style="flex:1;"><option value="arrive">Arrive</option><option value="leave">Leave</option></select>
              </div>
              <label class="trigger-label"><input type="checkbox" id="task-trig-car-enable"> Car Status Trigger</label>
              <select class="form-control hidden" id="task-trig-car-val"><option value="enter">Enter Car</option><option value="leave">Leave Car</option></select>
              <label class="trigger-label"><input type="checkbox" id="task-trig-wake-enable"> Wake Up Trigger</label>
            </div>
          </div>
        </form>
        <div class="modal-footer">
          <button class="btn-secondary" data-close="task-modal">Cancel</button>
          <button class="btn-primary" id="btn-task-save">Save Reminder</button>
        </div>
      </div>
    </div>

    <div class="modal-overlay" id="group-modal">
      <div class="modal-sheet">
        <div class="modal-header">
          <h3 class="modal-title">Manage Task Groups</h3>
          <button class="icon-btn" data-close="group-modal"><i data-lucide="x"></i></button>
        </div>
        <div class="modal-body">
          <span class="form-label">Existing Groups</span>
          <div class="pane-master-list" id="group-manager-list" style="max-height:220px;gap:6px;"></div>
          <div style="border-top:1px solid var(--hairline);margin:8px 0;padding-top:12px;">
            <span class="form-label" style="margin-bottom:8px;display:block;">Add New Group</span>
            <form id="group-form" style="display:flex;flex-direction:column;gap:10px;">
              <div style="display:flex;gap:8px;">
                <input type="text" class="form-control" id="new-group-emoji" value="📁" style="width:50px;text-align:center;">
                <input type="text" class="form-control" id="new-group-name" placeholder="Group Name (e.g. Travel)" required style="flex:1;">
                <input type="color" class="form-control" id="new-group-color" value="#6366f1" style="width:44px;padding:2px;cursor:pointer;">
              </div>
              <input type="text" class="form-control" id="new-group-desc" placeholder="Description (Optional)">
              <button type="submit" class="btn-primary" style="align-self:flex-end;">+ Add Group</button>
            </form>
          </div>
        </div>
        <div class="modal-footer"><button class="btn-secondary" data-close="group-modal">Close</button></div>
      </div>
    </div>`;

  // ---------- data ----------
  async function load() {
    const [t, g, z] = await Promise.all([A.json("GET", "/api/tasks"), A.json("GET", "/api/task-groups"), A.json("GET", "/api/zones")]);
    if (t.status === 200 && Array.isArray(t.body)) S.tasks = t.body;
    if (g.status === 200 && Array.isArray(g.body)) S.groups = g.body;
    if (z.status === 200 && Array.isArray(z.body)) S.zones = z.body;
    renderFilters();
    renderList();
  }

  const save = (id, body) => A.json(id ? "PUT" : "POST", id ? `/api/tasks/${encodeURIComponent(id)}` : "/api/tasks", body);

  // ---------- filters ----------
  function renderFilters() {
    const counts = C.triggerCounts(S.tasks);
    const perGroup = C.groupCounts(S.tasks);
    const item = (id, emoji, name, n) => `
      <div class="filter-nav-item ${S.group === id ? "active" : ""}" data-group="${esc(id === null ? "all" : id)}">
        <span style="display:flex;align-items:center;gap:6px;">${emoji ? `<span>${esc(emoji)}</span>` : ""}<span>${esc(name)}</span></span>
        <span class="count-badge">${n}</span>
      </div>`;
    $("tasks-group-filter-list").innerHTML = item(null, "", "All Reminders", counts.all) + item("general", "📁", "General", perGroup.general || 0)
      + S.groups.filter((g) => g.id !== "general").map((g) => item(g.id, g.emoji || "📁", g.name, perGroup[g.id] || 0)).join("");
    root.querySelectorAll("[data-count]").forEach((el) => { el.textContent = counts[el.dataset.count]; });
    root.querySelectorAll("#tasks-pane-filters [data-trigger]").forEach((el) => el.classList.toggle("active", S.trigger === el.dataset.trigger));
    root.querySelectorAll("#tasks-pane-filters [data-sort]").forEach((el) => el.classList.toggle("active", S.sort === el.dataset.sort));
  }

  // ---------- the list ----------
  function cardHtml(t) {
    const done = t.status === "completed";
    const p = (t.priority || "medium").toLowerCase();
    const trig = C.triggersOf(t);
    const group = S.groups.find((g) => g.id === (t.group_id || t.groupId));
    // One muted line: the group (a dot in its colour) and the triggers, in words.
    const meta = [
      group ? `<span class="task-meta-group"><i class="task-meta-dot" aria-hidden="true"></i>${esc(group.name)}</span>` : "",
      trig.time ? esc(String(trig.time).replace("T", " ")) : "",
      trig.location && typeof trig.location === "object" ? esc(`${trig.location.event === "leave" ? "Leaving" : "Arriving at"} ${trig.location.location || ""}`.trim()) : "",
      trig.car ? esc(`Car (${trig.car})`) : "",
      trig.wake ? "When I wake" : "",
    ].filter(Boolean);
    return `
      <div class="task-card dn-row ${t.id === S.selected ? "active" : ""}" data-id="${esc(t.id)}"${group && group.color ? ` style="--hue:${esc(group.color)}"` : ""}>
        <div class="task-checkbox prio-${esc(p)} ${done ? "completed" : ""}" data-check="${esc(t.id)}">
          ${done ? `<i data-lucide="check" style="width:14px;height:14px;stroke-width:3;color:var(--on-primary);"></i>` : ""}
        </div>
        <div class="task-content">
          <span class="task-title ${done ? "completed" : ""}" dir="auto">${esc(t.title)}</span>
          ${t.description ? `<span class="task-desc" dir="auto">${esc(t.description)}</span>` : ""}
          ${meta.length ? `<span class="task-meta">${meta.join('<span aria-hidden="true"> · </span>')}</span>` : ""}
        </div>
      </div>`;
  }

  function sectionHtml(key, title, tasks, extra = "") {
    const collapsed = Boolean(S.collapsed[key]);
    return `
      <div class="task-group-section" data-section="${esc(key)}"${extra}>
        <div class="task-group-header ${collapsed ? "collapsed" : ""}" data-toggle-sec="${esc(key)}">
          <span>${esc(title)} (${tasks.length})</span>
          <i data-lucide="chevron-down" class="chevron" style="width:16px;height:16px;"></i>
        </div>
        <div class="task-group-tasks ${collapsed ? "hidden" : ""}">${tasks.map(cardHtml).join("")}</div>
      </div>`;
  }

  function renderList() {
    const list = $("tasks-scroll-list");
    const { open, done } = C.filterTasks(S.tasks, { group: S.group, trigger: S.trigger, q: $("tasks-search-input").value });
    if (!open.length && (!S.showDone || !done.length)) {
      list.innerHTML = `<div class="pane-empty-state"><i data-lucide="check-square" style="width:40px;height:40px;opacity:0.4;"></i><span>No reminders match your filters.</span></div>`;
    } else {
      if (!S.selected && open.length && window.innerWidth >= 768) S.selected = open[0].id;
      list.innerHTML = C.sections(open, S.sort, S.groups).map((s) => sectionHtml(s.key, s.title, s.tasks)).join("")
        + (S.showDone && done.length ? sectionHtml("completed", "Completed", done, ` style="margin-top:16px;"`) : "");
    }
    renderDetail();
    A.icons();
  }

  // ---------- the detail pane ----------
  function renderDetail() {
    const pane = $("task-detail-pane");
    const task = S.tasks.find((t) => t.id === S.selected);
    if (!task) {
      pane.innerHTML = `<div class="pane-empty-state"><i data-lucide="check-square" style="width:40px;height:40px;opacity:0.4;"></i><span>Select a reminder to view its details</span></div>`;
      A.icons();
      return;
    }
    const done = task.status === "completed";
    const p = (task.priority || "medium").toLowerCase();
    const trig = C.triggersOf(task);
    const loc = trig.location && typeof trig.location === "object" ? trig.location : null;
    const seg = (attr, value, label, on) => `<div class="segmented-btn ${on ? "active" : ""}" data-${attr}="${value}">${label}</div>`;
    const option = (value, label, on) => `<option value="${esc(value)}"${on ? " selected" : ""}>${esc(label)}</option>`;
    pane.innerHTML = `
      <div class="task-detail-inline">
        <div class="task-detail-head"><div class="task-detail-head-row">
          <div class="segmented-control task-status-seg" id="td-status">${seg("status", "needs_action", "Active", !done)}${seg("status", "completed", "Done", done)}</div>
          <div class="task-detail-actions">
            <button class="icon-btn" id="td-reset" title="Discard changes"><i data-lucide="rotate-ccw"></i></button>
            <button class="btn-destructive-outline" id="td-delete"><i data-lucide="trash-2" style="width:14px;height:14px;"></i> Delete</button>
            <button class="btn-primary" id="td-save"><i data-lucide="check" style="width:14px;height:14px;"></i> Save</button>
          </div>
        </div></div>
        <div class="task-detail-form">
          <div class="form-group"><label class="form-label" for="td-title">Title</label><input type="text" class="form-control" id="td-title" value="${esc(task.title)}"></div>
          <div class="task-detail-grid">
            <div class="form-group"><label class="form-label">Priority</label>
              <div class="segmented-control" id="td-priority">${seg("prio", "low", "Low", p === "low")}${seg("prio", "medium", "Medium", p === "medium")}${seg("prio", "high", "High", p === "high")}</div></div>
            <div class="form-group"><label class="form-label" for="td-group">Group</label>
              <select class="form-control" id="td-group">${option("", "Auto-detect Group", false)}${S.groups.map((g) => option(g.id, `${g.emoji || "📁"} ${g.name}`, g.id === (task.group_id || task.groupId))).join("")}</select></div>
          </div>
          <div class="task-detail-grid">
            <div class="form-group"><label class="form-label" for="td-due">Due Date</label><input type="datetime-local" class="form-control" id="td-due" value="${esc(C.localDatetime(task.due_date || task.dueDate))}"></div>
            <div class="form-group"><label class="form-label" for="td-status-note">Status</label><input type="text" class="form-control" id="td-status-note" value="${done ? "Completed" : "Needs Action"}" disabled style="opacity:0.6;"></div>
          </div>
          <div class="form-group"><label class="form-label" for="td-desc">Description</label><textarea class="form-control" id="td-desc" rows="3" placeholder="Extra notes or instructions...">${esc(task.description || "")}</textarea></div>
        </div>
        <div class="task-detail-triggers">
          <span class="filter-sidebar-title">Triggers</span>
          <div class="trigger-option">
            <label class="trigger-label"><input type="checkbox" id="td-trig-time" data-reveal="td-trig-time-val" ${trig.time ? "checked" : ""}> ⏰ Time Reminder</label>
            <input type="datetime-local" class="form-control trigger-val ${trig.time ? "" : "hidden"}" id="td-trig-time-val" value="${esc(C.localDatetime(trig.time))}">
          </div>
          <div class="trigger-option">
            <label class="trigger-label"><input type="checkbox" id="td-trig-loc" data-reveal="td-trig-loc-fields" ${loc ? "checked" : ""}> 📍 Location Trigger</label>
            <div class="trigger-val trigger-fields ${loc ? "" : "hidden"}" id="td-trig-loc-fields" style="display:flex;gap:6px;">
              <select class="form-control" id="td-trig-loc-zone" style="flex:2;">${C.zoneOptions(S.zones, loc && loc.location, esc)}</select>
              <select class="form-control" id="td-trig-loc-event" style="flex:1;">${option("arrive", "Arrive", !loc || loc.event !== "leave")}${option("leave", "Leave", loc && loc.event === "leave")}</select>
            </div>
          </div>
          <div class="trigger-option">
            <label class="trigger-label"><input type="checkbox" id="td-trig-car" data-reveal="td-trig-car-val" ${trig.car ? "checked" : ""}> 🚗 Car Trigger</label>
            <select class="form-control trigger-val ${trig.car ? "" : "hidden"}" id="td-trig-car-val">${option("enter", "Enter Car", trig.car !== "leave")}${option("leave", "Leave Car", trig.car === "leave")}</select>
          </div>
          <div class="trigger-option"><label class="trigger-label"><input type="checkbox" id="td-trig-wake" ${trig.wake ? "checked" : ""}> ☀️ Wake Up Trigger</label></div>
        </div>
      </div>`;
    A.icons();
  }

  async function saveDetail() {
    const task = S.tasks.find((t) => t.id === S.selected);
    const title = $("td-title").value.trim();
    if (!task || !title) return;
    const active = (sel) => root.querySelector(`${sel} .segmented-btn.active`);
    const r = await save(task.id, {
      title,
      description: $("td-desc").value.trim() || null,
      priority: active("#td-priority")?.dataset.prio || task.priority || "medium",
      group_id: $("td-group").value || null,
      due_date: $("td-due").value || null,
      status: active("#td-status")?.dataset.status || task.status,
      triggers: C.triggersFrom({
        time: $("td-trig-time").checked ? $("td-trig-time-val").value : "",
        zone: $("td-trig-loc").checked ? $("td-trig-loc-zone").value : null,
        event: $("td-trig-loc-event").value,
        car: $("td-trig-car").checked ? $("td-trig-car-val").value : null,
        wake: $("td-trig-wake").checked,
      }),
    });
    if (r.status >= 200 && r.status < 300) await load(); else A.toast("Could not save the reminder");
  }

  const cardOf = (id) => $("tasks-scroll-list").querySelector(`.task-card[data-id="${CSS.escape(id)}"]`);

  /** Done: the check fills and draws, the title is struck through, the row fades and collapses (~400 ms,
   *  Dana.motion.complete) while the change is saved. Back to active: the row returns with a highlight. */
  async function setDone(id, done) {
    const card = cardOf(id);
    const leaving = done && card ? A.motion.complete(card) : null;
    await save(id, { status: done ? "completed" : "needs_action" });
    const t = S.tasks.find((x) => x.id === id);
    if (t) t.status = done ? "completed" : "needs_action";
    if (leaving) await leaving;
    renderFilters();
    renderList();
    if (!done) A.motion.flash(cardOf(id));
  }

  // ---------- the new-reminder modal ----------
  function openEditor() {
    $("task-form").reset();
    ["task-trig-time-val", "task-trig-loc-group", "task-trig-car-val"].forEach((id) => $(id).classList.add("hidden"));
    $("task-group-select").innerHTML = `<option value="">Auto-detect Group</option>`
      + S.groups.map((g) => `<option value="${esc(g.id)}">${esc(`${g.emoji || "📁"} ${g.name}`)}</option>`).join("");
    $("task-trig-loc-zone").innerHTML = C.zoneOptions(S.zones, "", esc);
    S.priority = "medium";
    root.querySelectorAll("#task-prio-control .segmented-btn").forEach((b) => b.classList.toggle("active", b.dataset.prio === S.priority));
    $("task-modal").classList.add("open");
  }

  async function saveEditor() {
    const title = $("task-title").value.trim();
    if (!title) return;
    const r = await save(null, {
      title,
      description: $("task-desc").value.trim() || null,
      priority: S.priority,
      group_id: $("task-group-select").value || null,
      triggers: C.triggersFrom({
        time: $("task-trig-time-enable").checked ? $("task-trig-time-val").value : "",
        zone: $("task-trig-loc-enable").checked ? $("task-trig-loc-zone").value : null,
        event: $("task-trig-loc-event").value,
        car: $("task-trig-car-enable").checked ? $("task-trig-car-val").value : null,
        wake: $("task-trig-wake-enable").checked,
      }),
    });
    if (r.status >= 200 && r.status < 300) { $("task-modal").classList.remove("open"); load(); }
    else A.toast("Could not add the reminder");
  }

  // ---------- the group manager ----------
  function renderGroups() {
    $("group-manager-list").innerHTML = S.groups.map((g) => `
      <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 10px;background:rgb(var(--ink-rgb) / .03);border:1px solid var(--hairline);border-radius:var(--radius-md);">
        <div style="display:flex;align-items:center;gap:8px;">
          <span>${esc(g.emoji || "📁")}</span>
          <strong style="font-size:13px;color:var(--ink);">${esc(g.name)}</strong>
          <span style="width:10px;height:10px;border-radius:var(--radius-full);background:${esc(g.color || "#6366f1")};"></span>
        </div>
        ${g.id !== "general" ? `<button class="icon-btn destructive" data-delete-group="${esc(g.id)}" style="width:28px;height:28px;"><i data-lucide="trash-2" style="width:13px;height:13px;"></i></button>` : ""}
      </div>`).join("");
    A.icons();
  }

  async function addGroup(ev) {
    ev.preventDefault();
    const name = $("new-group-name").value.trim();
    if (!name) return;
    await A.json("POST", "/api/task-groups", {
      name, emoji: $("new-group-emoji").value.trim() || "📁", color: $("new-group-color").value || "#6366f1",
      description: $("new-group-desc").value.trim() || "",
    });
    $("group-form").reset();
    $("new-group-emoji").value = "📁";
    $("new-group-color").value = "#6366f1";
    await load();
    renderGroups();
  }

  // ---------- wiring (once, on the first mount) ----------
  function wire() {
    root.addEventListener("click", async (ev) => {
      const t = ev.target;
      const close = t.closest("[data-close]");
      if (close) { $(close.dataset.close).classList.remove("open"); return; }
      const sort = t.closest("#tasks-pane-filters [data-sort]");
      if (sort) { S.sort = sort.dataset.sort; renderFilters(); renderList(); return; }
      const trigger = t.closest("#tasks-pane-filters [data-trigger]");
      if (trigger) { S.trigger = S.trigger === trigger.dataset.trigger ? null : trigger.dataset.trigger; renderFilters(); renderList(); return; }
      const group = t.closest("[data-group]");
      if (group) { S.group = group.dataset.group === "all" ? null : group.dataset.group; renderFilters(); renderList(); return; }
      const check = t.closest("[data-check]");
      if (check) { await setDone(check.dataset.check, !check.classList.contains("completed")); return; }
      const toggle = t.closest("[data-toggle-sec]");
      if (toggle) {
        const key = toggle.dataset.toggleSec;
        S.collapsed[key] = !S.collapsed[key];
        toggle.classList.toggle("collapsed", S.collapsed[key]);
        toggle.nextElementSibling?.classList.toggle("hidden", S.collapsed[key]);
        return;
      }
      const card = t.closest(".task-card");
      if (card) {
        S.selected = card.dataset.id;
        root.querySelectorAll(".task-card").forEach((c) => c.classList.toggle("active", c === card));
        renderDetail();
        return;
      }
      const segBtn = t.closest(".segmented-btn");
      if (segBtn) {
        segBtn.parentElement.querySelectorAll(".segmented-btn").forEach((b) => b.classList.toggle("active", b === segBtn));
        if (segBtn.dataset.prio && segBtn.closest("#task-prio-control")) S.priority = segBtn.dataset.prio;
        if (segBtn.dataset.status) $("td-status-note").value = segBtn.dataset.status === "completed" ? "Completed" : "Needs Action";
        return;
      }
      if (t.closest("#td-save")) { saveDetail(); return; }
      if (t.closest("#td-reset")) { renderDetail(); return; }
      if (t.closest("#td-delete")) {
        if (!confirm("Delete this reminder?")) return;
        await A.json("DELETE", `/api/tasks/${encodeURIComponent(S.selected)}`);
        S.selected = null;
        load();
        return;
      }
      if (t.closest("#btn-add-task-top, #btn-add-task-fab")) { openEditor(); return; }
      if (t.closest("#btn-task-save")) { saveEditor(); return; }
      if (t.closest("#btn-manage-groups")) { renderGroups(); $("group-modal").classList.add("open"); return; }
      const del = t.closest("[data-delete-group]");
      if (del && confirm("Delete this group? Tasks will be moved to General.")) {
        await A.json("DELETE", `/api/task-groups/${encodeURIComponent(del.dataset.deleteGroup)}`);
        await load();
        renderGroups();
      }
    });
    root.addEventListener("change", (ev) => {
      const t = ev.target;
      if (t.id === "task-toggle-completed") { S.showDone = t.checked; renderList(); return; }
      if (t.dataset.reveal) $(t.dataset.reveal).classList.toggle("hidden", !t.checked);
      const modalReveal = { "task-trig-time-enable": "task-trig-time-val", "task-trig-loc-enable": "task-trig-loc-group", "task-trig-car-enable": "task-trig-car-val" }[t.id];
      if (modalReveal) $(modalReveal).classList.toggle("hidden", !t.checked);
    });
    $("tasks-search-input").addEventListener("input", renderList);
    $("group-form").addEventListener("submit", addGroup);
  }

  Dana.panel({
    scripts: ["tasks-core.js"],
    opens: ["task"],
    start(api) { A = api; C = window.DanaTasksCore; },
    mount(el) {
      if (!root) { root = el; el.innerHTML = TEMPLATE; wire(); }
      A.piece("today", $("tasks-today"));
      load();
    },
  });
})();
