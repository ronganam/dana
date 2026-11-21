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

function renderSiriSphere() {
    if (!sphereCtx || !sphereCanvas) return;
    const w = sphereCanvas.width;
    const h = sphereCanvas.height;
    const cx = w / 2;
    const cy = h / 2;
    sphereCtx.clearRect(0, 0, w, h);

    const listening = isRecording || isCallActive;

    if (!listening) {
        // Idle warm breathing orb (monochrome warm tint)
        spherePhase += 0.025;
        const breathe = 1 + 0.08 * Math.sin(spherePhase);
        const radius = (w * 0.35) * breathe;

        const grad = sphereCtx.createRadialGradient(cx, cy, 0, cx, cy, radius);
        grad.addColorStop(0, "rgba(247, 245, 240, 0.95)");
        grad.addColorStop(0.5, "rgba(218, 210, 193, 0.6)");
        grad.addColorStop(1, "rgba(43, 38, 34, 0)");

        sphereCtx.fillStyle = grad;
        sphereCtx.beginPath();
        sphereCtx.arc(cx, cy, radius, 0, Math.PI * 2);
        sphereCtx.fill();
    } else {
        // Actively listening / calling: multi-color rotating cyan/magenta gradient with ripples
        spherePhase += 0.08;
        const breathe = 1 + 0.15 * Math.sin(spherePhase * 2);
        const baseRadius = (w * 0.32) * breathe;

        // Outer expanding ripple
        const rippleR = (baseRadius * 1.5) + (baseRadius * 0.4 * Math.sin(spherePhase));
        const rippleGrad = sphereCtx.createRadialGradient(cx, cy, baseRadius * 0.5, cx, cy, rippleR);
        rippleGrad.addColorStop(0, "rgba(0, 240, 255, 0.25)");
        rippleGrad.addColorStop(0.6, "rgba(208, 0, 255, 0.15)");
        rippleGrad.addColorStop(1, "rgba(0, 0, 0, 0)");
        sphereCtx.fillStyle = rippleGrad;
        sphereCtx.beginPath();
        sphereCtx.arc(cx, cy, rippleR, 0, Math.PI * 2);
        sphereCtx.fill();

        // Orb 1: Cyan/Blue
        const ox1 = cx + Math.sin(spherePhase) * 4;
        const oy1 = cy + Math.sin(spherePhase + 1) * 3;
        const g1 = sphereCtx.createRadialGradient(ox1, oy1, 0, ox1, oy1, baseRadius);
        g1.addColorStop(0, "rgba(0, 240, 255, 0.9)");
        g1.addColorStop(1, "rgba(0, 240, 255, 0)");
        sphereCtx.fillStyle = g1;
        sphereCtx.beginPath();
        sphereCtx.arc(ox1, oy1, baseRadius, 0, Math.PI * 2);
        sphereCtx.fill();

        // Orb 2: Magenta/Pink
        const ox2 = cx - Math.sin(spherePhase + 2) * 4;
        const oy2 = cy - Math.sin(spherePhase + 3) * 3;
        const g2 = sphereCtx.createRadialGradient(ox2, oy2, 0, ox2, oy2, baseRadius);
        g2.addColorStop(0, "rgba(208, 0, 255, 0.85)");
        g2.addColorStop(1, "rgba(208, 0, 255, 0)");
        sphereCtx.fillStyle = g2;
        sphereCtx.beginPath();
        sphereCtx.arc(ox2, oy2, baseRadius, 0, Math.PI * 2);
        sphereCtx.fill();

        // White center core
        const gCore = sphereCtx.createRadialGradient(cx, cy, 0, cx, cy, baseRadius * 0.5);
        gCore.addColorStop(0, "rgba(247, 245, 240, 0.95)");
        gCore.addColorStop(1, "rgba(247, 245, 240, 0)");
        sphereCtx.fillStyle = gCore;
        sphereCtx.beginPath();
        sphereCtx.arc(cx, cy, baseRadius * 0.5, 0, Math.PI * 2);
        sphereCtx.fill();
    }

    sphereAnimId = requestAnimationFrame(renderSiriSphere);
}
renderSiriSphere();

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
        isRecording = true;
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
        isRecording = false;
        chatInputWrapper?.classList.remove("listening");
        chatTextInput.placeholder = "Ask me anything...";
    }
}

function stopVoiceRecording() {
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
        mediaRecorder.stop();
    }
    isRecording = false;
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
    isCallActive = true;
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
                    if (callStatus === "speaking") callPulseOrb.style.background = "var(--orb-cyan)";
                    else if (callStatus === "tool_running") callPulseOrb.style.background = "var(--orb-magenta)";
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
    isCallActive = false;
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
