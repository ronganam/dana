/* The web app — shared state and helpers (static/app.js says how the app's scripts fit together).
 *
 * The state the screens share (the chat session id, the loaded lists, chat and voice state, task filters,
 * the calls audio player) and the helpers every screen uses: refreshIcons, isRtlText,
 * formatTime, formatDuration, escapeHtml.
 */

// ══════════════════════════════════════════════════════════════════════
// 1. GLOBAL STATE & UTILITIES
// ══════════════════════════════════════════════════════════════════════

let currentSessionId = localStorage.getItem("ganam_session_id");
if (!currentSessionId) {
    currentSessionId = "web_" + Math.random().toString(36).substring(2, 10);
    localStorage.setItem("ganam_session_id", currentSessionId);
}

let activeTab = "chat";
let tasks = [];
let taskGroups = [];
let zones = [];
let memories = [];
let calls = [];
let selectedCallId = null;
let reanalyzeTargetCall = null;

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


// Filter & Sort state
let taskSortMode = "group"; // "group" | "priority" | "due"
let taskGroupFilter = null;
let taskTriggerFilter = null;

// Global Audio Player for Calls
const callAudio = new Audio();
let isCallAudioPlaying = false;
let currentPlayingCallId = null;

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

function formatDuration(sec) {
    if (!sec || isNaN(sec)) return "0:00";
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s < 10 ? "0" : ""}${s}`;
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
