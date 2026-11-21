/* The web app — voice (static/app.js says how the app's scripts fit together).
 *
 * The orb (the canvas sphere on the Chat screen), voice messages recorded from the microphone and sent
 * as a run, and the realtime call mode over a WebSocket.
 */

// ══════════════════════════════════════════════════════════════════════
// 2. SIRI GLOW SPHERE VISUALIZER (Canvas animation)
// ══════════════════════════════════════════════════════════════════════

const sphereCanvas = document.getElementById("siri-sphere-canvas");
const sphereBtn = document.getElementById("btn-chat-voice");
let sphereCtx = sphereCanvas ? sphereCanvas.getContext("2d") : null;
let sphereAnimId = null;
let spherePhase = 0;

// The orb's colours come from the theme's tokens (styles.css: --ember-rgb, --amber-rgb, --ink-rgb).
function sphereHue(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(`--${name}-rgb`).trim() || "255 106 61";
}

// Idle: one still frame. Listening (a voice message or a call): it breathes until listening stops; nothing
// animates otherwise (life-spec: no infinite animation outside an active state).
function renderSiriSphere() {
    sphereAnimId = null;
    if (!sphereCtx || !sphereCanvas) return;
    const w = sphereCanvas.width;
    const h = sphereCanvas.height;
    const cx = w / 2;
    const cy = h / 2;
    sphereCtx.clearRect(0, 0, w, h);

    const listening = isRecording || isCallActive;
    const ember = sphereHue("ember");

    if (!listening) {
        const radius = w * 0.34;
        const grad = sphereCtx.createRadialGradient(cx, cy - radius * 0.25, 0, cx, cy, radius);
        grad.addColorStop(0, `rgb(${ember} / 0.95)`);
        grad.addColorStop(0.65, `rgb(${ember} / 0.55)`);
        grad.addColorStop(1, `rgb(${ember} / 0)`);
        sphereCtx.fillStyle = grad;
        sphereCtx.beginPath();
        sphereCtx.arc(cx, cy, radius, 0, Math.PI * 2);
        sphereCtx.fill();
        return;
    }

    spherePhase += 0.08;
    const amber = sphereHue("amber");
    const ink = sphereHue("ink");
    const breathe = 1 + 0.12 * Math.sin(spherePhase * 2);
    const baseRadius = (w * 0.32) * breathe;

    const ox = cx + Math.sin(spherePhase) * 3;
    const oy = cy + Math.sin(spherePhase + 1) * 2;
    const g1 = sphereCtx.createRadialGradient(ox, oy, 0, ox, oy, baseRadius * 1.25);
    g1.addColorStop(0, `rgb(${amber} / 0.85)`);
    g1.addColorStop(1, `rgb(${amber} / 0)`);
    sphereCtx.fillStyle = g1;
    sphereCtx.beginPath();
    sphereCtx.arc(ox, oy, baseRadius * 1.25, 0, Math.PI * 2);
    sphereCtx.fill();

    const g2 = sphereCtx.createRadialGradient(cx, cy, 0, cx, cy, baseRadius);
    g2.addColorStop(0, `rgb(${ember} / 0.95)`);
    g2.addColorStop(1, `rgb(${ember} / 0)`);
    sphereCtx.fillStyle = g2;
    sphereCtx.beginPath();
    sphereCtx.arc(cx, cy, baseRadius, 0, Math.PI * 2);
    sphereCtx.fill();

    const gCore = sphereCtx.createRadialGradient(cx, cy, 0, cx, cy, baseRadius * 0.45);
    gCore.addColorStop(0, `rgb(${ink} / 0.9)`);
    gCore.addColorStop(1, `rgb(${ink} / 0)`);
    sphereCtx.fillStyle = gCore;
    sphereCtx.beginPath();
    sphereCtx.arc(cx, cy, baseRadius * 0.45, 0, Math.PI * 2);
    sphereCtx.fill();

    sphereAnimId = requestAnimationFrame(renderSiriSphere);
}

/** Redraws the orb for the current state: starts the breathing when listening begins, and the still
 *  frame after it ends (also after a theme change). */
function wakeSphere() {
    const listening = isRecording || isCallActive;
    sphereBtn?.classList.toggle("listening", listening);
    if (!sphereAnimId) renderSiriSphere();
}
renderSiriSphere();
window.matchMedia?.("(prefers-color-scheme: light)").addEventListener?.("change", wakeSphere);

// Voice Recording via Microphone (Siri Orb click)
sphereBtn?.addEventListener("click", () => {
    if (isCallActive) {
        endRealtimeCall();
        return;
    }
    if (isRecording) {
        stopVoiceRecording();
    } else {
        startVoiceRecording();
    }
});

async function startVoiceRecording() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        isRecording = true; wakeSphere();
        chatInputWrapper?.classList.add("listening");
        chatTextInput.placeholder = "Listening...";
        recordedAudioChunks = [];

        mediaRecorder = new MediaRecorder(stream);
        mediaRecorder.ondataavailable = (e) => {
            if (e.data.size > 0) recordedAudioChunks.push(e.data);
        };
        mediaRecorder.onstop = async () => {
            const audioBlob = new Blob(recordedAudioChunks, { type: "audio/webm" });
            stream.getTracks().forEach(t => t.stop());
            await sendVoiceBlob(audioBlob);
        };
        mediaRecorder.start();
    } catch (e) {
        console.error("Mic access error:", e);
        alert("Could not access microphone: " + e.message);
        isRecording = false; wakeSphere();
        chatInputWrapper?.classList.remove("listening");
        chatTextInput.placeholder = "Ask me anything...";
    }
}

function stopVoiceRecording() {
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
        mediaRecorder.stop();
    }
    isRecording = false; wakeSphere();
    chatInputWrapper?.classList.remove("listening");
    chatTextInput.placeholder = "Ask me anything...";
}

async function sendVoiceBlob(blob) {
    const ctx = newRunContext();
    startThinking(ctx, "Transcribing…");
    flushRender();

    const fd = new FormData();
    fd.append("audio", blob, "voice.webm");
    await runAssistantStream(
        `/api/assistant/voice?${new URLSearchParams(Object.assign({ session_id: currentSessionId, device: "web" }, selectedLlm ? { model: selectedLlm } : {}))}`,
        { method: "POST", body: fd },
        ctx
    );
}

// Realtime Phone Call Mode
btnChatCall?.addEventListener("click", () => {
    if (isCallActive) {
        endRealtimeCall();
    } else {
        startRealtimeCall();
    }
});
btnCallHangup?.addEventListener("click", endRealtimeCall);

function startRealtimeCall() {
    isCallActive = true; wakeSphere();
    callStatus = "connecting";
    chatCallBanner.classList.remove("hidden");
    callBannerStatus.textContent = "Connecting to Realtime server...";
    callPulseOrb.style.background = "var(--connecting-amber)";
    btnChatCall.classList.add("destructive");
    btnChatCall.classList.remove("primary");
    chatCallIcon.setAttribute("data-lucide", "phone-off");
    refreshIcons();

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${proto}//${window.location.host}/api/assistant/realtime?session_id=${encodeURIComponent(currentSessionId)}&model=${encodeURIComponent(selectedRealtimeModel)}`;

    try {
        realtimeWs = new WebSocket(wsUrl);
        realtimeWs.onopen = () => {
            callStatus = "listening";
            callBannerStatus.textContent = "Connected — Assistant is listening";
            callPulseOrb.style.background = "var(--listening-green)";
        };
        realtimeWs.onmessage = (e) => {
            try {
                const data = JSON.parse(e.data);
                if (data.type === "transcript") {
                    const transcriptEl = document.getElementById("call-live-transcript");
                    const textEl = document.getElementById("call-live-text");
                    if (transcriptEl && textEl) {
                        transcriptEl.classList.remove("hidden");
                        textEl.textContent = data.text;
                    }
                } else if (data.type === "status") {
                    callStatus = data.status || "listening";
                    callBannerStatus.textContent = data.message || callStatus;
                    if (callStatus === "speaking") callPulseOrb.style.background = "var(--accent)";
                    else if (callStatus === "tool_running") callPulseOrb.style.background = "var(--amber)";
                    else callPulseOrb.style.background = "var(--listening-green)";
                }
            } catch (err) {
                console.log("Realtime message:", e.data);
            }
        };
        realtimeWs.onerror = () => {
            callBannerStatus.textContent = "Realtime connection error";
            callPulseOrb.style.background = "var(--destructive)";
        };
        realtimeWs.onclose = () => {
            if (isCallActive) endRealtimeCall();
        };
    } catch (e) {
        console.error("WS connection error:", e);
        endRealtimeCall();
    }
}

function endRealtimeCall() {
    isCallActive = false; wakeSphere();
    callStatus = "idle";
    if (realtimeWs) {
        try { realtimeWs.close(); } catch (e) {}
        realtimeWs = null;
    }
    chatCallBanner.classList.add("hidden");
    btnChatCall.classList.remove("destructive");
    btnChatCall.classList.add("primary");
    chatCallIcon.setAttribute("data-lucide", "phone");
    refreshIcons();
}
