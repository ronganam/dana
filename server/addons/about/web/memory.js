/* Memory — the facts Dana keeps, listed, searched and edited (/api/memories), the about addon's page
 * (Dana.panel, static/dana.js): the list, its detail pane and the add / edit modal.
 */
(() => {
  "use strict";

  const TEMPLATE = `
    <header class="top-bar">
        <span class="top-bar-title">Memory Center</span>
        <div class="top-bar-actions">
            <button class="btn-primary" id="btn-add-memory-top">
                <i data-lucide="plus" style="width:16px;height:16px;"></i>
                <span>Add Memory</span>
            </button>
        </div>
    </header>

    <div class="two-pane-container" id="memory-two-pane">
        <!-- Left: Master List -->
        <div class="pane-master">
            <div class="pane-master-header">
                <input type="text" class="form-control" id="memory-search-input" placeholder="Search memories...">
            </div>
            <div class="pane-master-list" id="memory-master-list">
                <!-- Memory compact rows inserted here -->
            </div>
        </div>
        <!-- Right: Detail / Inline Editor Pane -->
        <div class="pane-detail" id="memory-detail-pane">
            <div class="pane-empty-state">
                <i data-lucide="star" style="width:40px;height:40px;opacity:0.4;"></i>
                <span>Select a memory to view or edit</span>
            </div>
        </div>
    </div>

    <button class="fab-btn" id="btn-add-memory-fab" title="Add Memory">
        <i data-lucide="plus"></i>
    </button>


    <div class="modal-overlay" id="memory-modal">
        <div class="modal-sheet">
            <div class="modal-header">
                <h3 class="modal-title" id="memory-modal-title">New Memory</h3>
                <button class="icon-btn" id="memory-modal-close"><i data-lucide="x"></i></button>
            </div>
            <form id="memory-form" class="modal-body">
                <input type="hidden" id="memory-original-key">
                <div class="form-group">
                    <label class="form-label" for="memory-key">Topic / Key</label>
                    <input type="text" class="form-control" id="memory-key" required placeholder="e.g. WiFi password">
                </div>
                <div class="form-group">
                    <label class="form-label" for="memory-value">Content</label>
                    <textarea class="form-control" id="memory-value" required rows="3" placeholder="What should the assistant remember?"></textarea>
                </div>
                <div class="form-group">
                    <label class="form-label">Category</label>
                    <div class="segmented-control" id="memory-cat-control">
                        <div class="segmented-btn active" data-cat="general">General</div>
                        <div class="segmented-btn" data-cat="personal">Personal</div>
                        <div class="segmented-btn" data-cat="work">Work</div>
                    </div>
                </div>
            </form>
            <div class="modal-footer">
                <button class="btn-destructive-outline hidden" id="btn-memory-delete">Delete</button>
                <button class="btn-secondary" id="btn-memory-cancel">Cancel</button>
                <button class="btn-primary" id="btn-memory-save">Save Memory</button>
            </div>
        </div>
    </div>
  `;

  let A = null;          // the page's Dana api (static/dana.js)
  const esc = (s) => A.esc(s);
  let memories = [];
  let memoryMasterList = null;
  let memoryDetailPane = null;
  let memorySearchInput = null;
  let btnAddMemoryTop = null;
  let btnAddMemoryFab = null;
  let memoryModal = null;
  let memoryModalClose = null;
  let memoryForm = null;
  let memoryKeyInput = null;
  let memoryValueInput = null;
  let memoryCatControl = null;
  let btnMemorySave = null;
  let btnMemoryCancel = null;
  let btnMemoryDelete = null;
  let currentSelectedMemoryCat = "general";
  let activeMemoryKey = null;

  async function loadMemories() {
    try {
      const res = await fetch("/api/memories");
      if (res.ok) {
        memories = await res.json();
        renderMemoryMasterList();
      }
    } catch (e) {
      console.error(e);
    }
  }

  function renderMemoryMasterList() {
    if (!memoryMasterList) return;
    const q = (memorySearchInput?.value || "").toLowerCase().trim();
    const filtered = memories.filter(m => {
      if (!q) return true;
      return (m.key || "").toLowerCase().includes(q) || (m.value || "").toLowerCase().includes(q);
    });

    if (filtered.length === 0) {
      memoryMasterList.innerHTML = `<span style="font-size:12px;color:var(--mute);padding:12px;">No memories found.</span>`;
      return;
    }

    memoryMasterList.innerHTML = filtered.map(m => {
      const cat = (m.category || "general").toLowerCase();
      const catColor = cat === "personal" ? "var(--cat-personal)" : cat === "work" ? "var(--cat-work)" : "var(--cat-general)";
      const isSelected = m.key === activeMemoryKey;

      return `
          <div class="memory-row-item dn-row ${isSelected ? "active" : ""}" data-key="${esc(m.key)}">
            <div class="memory-cat-bar" style="background:${catColor};"></div>
            <div style="flex:1;overflow:hidden;display:flex;flex-direction:column;gap:2px;">
              <strong style="font-size:13.5px;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                ${esc(m.key.replace(/_/g, " "))}
              </strong>
              <span style="font-size:12px;color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                ${esc(m.value)}
              </span>
            </div>
            <span class="memory-cat-badge" style="background:${catColor}22;color:${catColor};">${esc(cat)}</span>
          </div>
        `;
    }).join("");

    memoryMasterList.querySelectorAll(".memory-row-item").forEach(item => {
      item.addEventListener("click", () => {
        activeMemoryKey = item.getAttribute("data-key");
        renderMemoryMasterList();
        renderMemoryDetail();
      });
    });

    // Auto select first on desktop if none selected
    if (!activeMemoryKey && filtered.length > 0 && window.innerWidth >= 768) {
      activeMemoryKey = filtered[0].key;
      renderMemoryMasterList();
      renderMemoryDetail();
    }
  }

  function renderMemoryDetail() {
    if (!memoryDetailPane) return;
    const m = memories.find(x => x.key === activeMemoryKey);
    if (!m) {
      memoryDetailPane.innerHTML = `
          <div class="pane-empty-state">
            <i data-lucide="star" style="width:40px;height:40px;opacity:0.4;"></i>
            <span>Select a memory to view details</span>
          </div>
        `;
      A.icons();
      return;
    }

    const cat = (m.category || "general").toLowerCase();
    const catColor = cat === "personal" ? "var(--cat-personal)" : cat === "work" ? "var(--cat-work)" : "var(--cat-general)";

    memoryDetailPane.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
          <span class="memory-cat-badge" style="background:${catColor}22;color:${catColor};font-size:12px;padding:3px 8px;">
            ${esc(cat)}
          </span>
          <div style="display:flex;gap:8px;">
            <button class="btn-secondary" id="btn-edit-memory-detail">
              <i data-lucide="edit" style="width:14px;height:14px;"></i> Edit
            </button>
            <button class="btn-destructive-outline" id="btn-delete-memory-detail">
              <i data-lucide="trash-2" style="width:14px;height:14px;"></i> Delete
            </button>
          </div>
        </div>
        <h2 style="font-size:22px;font-weight:600;color:var(--ink);margin-bottom:12px;">
          ${esc(m.key.replace(/_/g, " "))}
        </h2>
        <div style="border-top:1px solid var(--hairline);margin-bottom:16px;"></div>
        <div style="font-size:15px;line-height:1.6;color:var(--body-strong);white-space:pre-wrap;">
          ${esc(m.value)}
        </div>
        ${m.updated_at ? `
          <span style="font-size:11px;color:var(--mute);margin-top:24px;">Last updated: ${esc(m.updated_at)}</span>
        ` : ""}
      `;

    document.getElementById("btn-edit-memory-detail")?.addEventListener("click", () => openMemoryModal(m));
    document.getElementById("btn-delete-memory-detail")?.addEventListener("click", async () => {
      if (confirm(`Delete memory "${m.key}"?`)) {
        await deleteMemory(m.key);
      }
    });

    A.icons();
  }

  function openMemoryModal(mem = null) {
    memoryForm.reset();
    if (mem) {
      document.getElementById("memory-modal-title").textContent = "Edit Memory";
      document.getElementById("memory-original-key").value = mem.key;
      memoryKeyInput.value = mem.key;
      memoryValueInput.value = mem.value;
      currentSelectedMemoryCat = (mem.category || "general").toLowerCase();
      btnMemoryDelete.classList.remove("hidden");
    } else {
      document.getElementById("memory-modal-title").textContent = "New Memory";
      document.getElementById("memory-original-key").value = "";
      currentSelectedMemoryCat = "general";
      btnMemoryDelete.classList.add("hidden");
    }

    memoryCatControl.querySelectorAll(".segmented-btn").forEach(b => {
      b.classList.toggle("active", b.getAttribute("data-cat") === currentSelectedMemoryCat);
    });

    memoryModal.classList.add("open");
  }

  async function deleteMemory(key) {
    try {
      await fetch(`/api/memories/${encodeURIComponent(key)}`, { method: "DELETE" });
      activeMemoryKey = null;
      loadMemories();
    } catch (e) {
      console.error(e);
    }
  }

  function wire() {
    memoryMasterList = document.getElementById("memory-master-list");
    memoryDetailPane = document.getElementById("memory-detail-pane");
    memorySearchInput = document.getElementById("memory-search-input");
    btnAddMemoryTop = document.getElementById("btn-add-memory-top");
    btnAddMemoryFab = document.getElementById("btn-add-memory-fab");
    memoryModal = document.getElementById("memory-modal");
    memoryModalClose = document.getElementById("memory-modal-close");
    memoryForm = document.getElementById("memory-form");
    memoryKeyInput = document.getElementById("memory-key");
    memoryValueInput = document.getElementById("memory-value");
    memoryCatControl = document.getElementById("memory-cat-control");
    btnMemorySave = document.getElementById("btn-memory-save");
    btnMemoryCancel = document.getElementById("btn-memory-cancel");
    btnMemoryDelete = document.getElementById("btn-memory-delete");

    memoryCatControl?.querySelectorAll(".segmented-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        memoryCatControl.querySelectorAll(".segmented-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        currentSelectedMemoryCat = btn.getAttribute("data-cat");
      });
    });

    memorySearchInput?.addEventListener("input", renderMemoryMasterList);

    btnAddMemoryTop?.addEventListener("click", () => openMemoryModal());

    btnAddMemoryFab?.addEventListener("click", () => openMemoryModal());

    memoryModalClose?.addEventListener("click", () => memoryModal.classList.remove("open"));

    btnMemoryCancel?.addEventListener("click", () => memoryModal.classList.remove("open"));

    btnMemorySave?.addEventListener("click", async () => {
      const key = memoryKeyInput.value.trim().toLowerCase().replace(/\s+/g, "_");
      const val = memoryValueInput.value.trim();
      if (!key || !val) return;

      try {
        await fetch("/api/memories", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            key: key,
            value: val,
            category: currentSelectedMemoryCat
          })
        });
        memoryModal.classList.remove("open");
        activeMemoryKey = key;
        loadMemories();
      } catch (e) {
        console.error(e);
      }
    });

    btnMemoryDelete?.addEventListener("click", async () => {
      const key = document.getElementById("memory-original-key").value;
      if (key && confirm("Delete memory?")) {
        await deleteMemory(key);
        memoryModal.classList.remove("open");
      }
    });
  }

  Dana.panel({
    mount(el, api) {
      A = api;
      if (!el.firstChild) { el.innerHTML = TEMPLATE; wire(); }
      loadMemories();
    },
  });
})();
