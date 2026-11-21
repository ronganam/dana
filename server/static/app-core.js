/* The web app — shared state and helpers (static/app.js says how the app's scripts fit together).
 *
 * The state the screens share (the chat session id, chat and voice state) and the helpers every screen
 * uses: refreshIcons, isRtlText, formatTime, escapeHtml.
 */

// ══════════════════════════════════════════════════════════════════════
// 1. GLOBAL STATE & UTILITIES
// ══════════════════════════════════════════════════════════════════════

// Saved preferences were once kept under "ganam_*" keys: each moves to its "dana_*" name once, on the
// first load of this version (the old key is deleted; a value already under the new name wins).
(function moveOldStorageKeys() {
    try {
        const old = [];
        for (let i = 0; i < localStorage.length; i++) {
            const key = localStorage.key(i);
            if (key && key.startsWith("ganam_")) old.push(key);
        }
        for (const key of old) {
            const renamed = "dana_" + key.slice("ganam_".length);
            if (localStorage.getItem(renamed) === null) localStorage.setItem(renamed, localStorage.getItem(key));
            localStorage.removeItem(key);
        }
    } catch (e) { /* storage unavailable: nothing to move */ }
})();

let currentSessionId = localStorage.getItem("dana_session_id");
if (!currentSessionId) {
    currentSessionId = "web_" + Math.random().toString(36).substring(2, 10);
    localStorage.setItem("dana_session_id", currentSessionId);
}

let activeTab = "chat";

// Chat & Voice state
let chatMessages = [];
let isRecording = false;
let mediaRecorder = null;
let recordedAudioChunks = null;
let pendingImageFile = null;
let isCallActive = false;
let callStatus = "idle"; // "idle" | "connecting" | "listening" | "speaking" | "tool_running" | "error"
let realtimeWs = null;
let selectedRealtimeModel = "gpt-realtime";
let isCallMuted = false;
let isSpeakerOn = true;
// In-flight assistant stream. Aborting only detaches this client — the
// server keeps running the task and persists the result.
let assistantStreamAbort = null;


// ── Helper: Safe Lucide icon renderer ──
function refreshIcons() {
    if (window.lucide && typeof window.lucide.createIcons === "function") {
        window.lucide.createIcons();
    }
}

// ── Helper: RTL Hebrew/Arabic detection ──
function isRtlText(text) {
    if (!text) return false;
    return /[\u0590-\u05FF\u0600-\u06FF\uFB1D-\uFDFF\uFE70-\uFEFF]/.test(text);
}

// ── Helper: Format timestamps ──
function formatTime(isoString) {
    if (!isoString) return "";
    try {
        const date = new Date(isoString);
        if (isNaN(date.getTime())) return isoString;
        return date.toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    } catch (e) {
        return isoString;
    }
}

// ── Helper: Escape HTML ──
function escapeHtml(str) {
    if (!str) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
