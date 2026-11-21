/* The web app — the Memory screen (static/app.js says how the app's scripts fit together).
 *
 * The memory list, its detail pane and the add / edit modal (/api/memories).
 */

// ══════════════════════════════════════════════════════════════════════
// 6. SCREEN 3: MEMORY CENTER CONTROLLER
// ══════════════════════════════════════════════════════════════════════

const memoryMasterList = document.getElementById("memory-master-list");
const memoryDetailPane = document.getElementById("memory-detail-pane");
const memorySearchInput = document.getElementById("memory-search-input");
const btnAddMemoryTop = document.getElementById("btn-add-memory-top");
const btnAddMemoryFab = document.getElementById("btn-add-memory-fab");
const memoryModal = document.getElementById("memory-modal");
const memoryModalClose = document.getElementById("memory-modal-close");
const memoryForm = document.getElementById("memory-form");
const memoryKeyInput = document.getElementById("memory-key");
const memoryValueInput = document.getElementById("memory-value");
const memoryCatControl = document.getElementById("memory-cat-control");
const btnMemorySave = document.getElementById("btn-memory-save");
const btnMemoryCancel = document.getElementById("btn-memory-cancel");
const btnMemoryDelete = document.getElementById("btn-memory-delete");
let currentSelectedMemoryCat = "general";
let activeMemoryKey = null;

memoryCatControl?.querySelectorAll(".segmented-btn").forEach(btn => {
    btn.addEventListener("click", () => {
        memoryCatControl.querySelectorAll(".segmented-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        currentSelectedMemoryCat = btn.getAttribute("data-cat");
    });
});

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
                <div class="memory-row-item ${isSelected ? "active" : ""}" data-key="${escapeHtml(m.key)}">
                    <div class="memory-cat-bar" style="background:${catColor};"></div>
                    <div style="flex:1;overflow:hidden;display:flex;flex-direction:column;gap:2px;">
                        <strong style="font-size:13.5px;color:var(--ink);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                            ${escapeHtml(m.key.replace(/_/g, " "))}
                        </strong>
                        <span style="font-size:12px;color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                            ${escapeHtml(m.value)}
                        </span>
                    </div>
                    <span class="memory-cat-badge" style="background:${catColor}22;color:${catColor};">${escapeHtml(cat)}</span>
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

memorySearchInput?.addEventListener("input", renderMemoryMasterList);

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
        refreshIcons();
        return;
    }

    const cat = (m.category || "general").toLowerCase();
    const catColor = cat === "personal" ? "var(--cat-personal)" : cat === "work" ? "var(--cat-work)" : "var(--cat-general)";

    memoryDetailPane.innerHTML = `
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px;">
                <span class="memory-cat-badge" style="background:${catColor}22;color:${catColor};font-size:12px;padding:3px 8px;">
                    ${escapeHtml(cat)}
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
                ${escapeHtml(m.key.replace(/_/g, " "))}
            </h2>
            <div style="border-top:1px solid var(--hairline);margin-bottom:16px;"></div>
            <div style="font-size:15px;line-height:1.6;color:var(--body-strong);white-space:pre-wrap;">
                ${escapeHtml(m.value)}
            </div>
            ${m.updated_at ? `
                <span style="font-size:11px;color:var(--mute);margin-top:24px;">Last updated: ${escapeHtml(m.updated_at)}</span>
            ` : ""}
        `;

    document.getElementById("btn-edit-memory-detail")?.addEventListener("click", () => openMemoryModal(m));
    document.getElementById("btn-delete-memory-detail")?.addEventListener("click", async () => {
        if (confirm(`Delete memory "${m.key}"?`)) {
            await deleteMemory(m.key);
        }
    });

    refreshIcons();
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

async function deleteMemory(key) {
    try {
        await fetch(`/api/memories/${encodeURIComponent(key)}`, { method: "DELETE" });
        activeMemoryKey = null;
        loadMemories();
    } catch (e) {
        console.error(e);
    }
}
