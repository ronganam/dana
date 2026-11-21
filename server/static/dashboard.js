(function() {
    const FRIGATE_CAMERA = 'ptz';
    const CLIMATE_ENTITY = 'climate.bedroom_ac';
    // From /api/client-config (the user's Home Assistant battery sensor setting).
    let PHONE_ENTITY = 'sensor.phone_battery_level';
    const WATCH_ENTITY = 'sensor.oneplus_watch_3_43mm_000d_battery_level';

    fetch('/api/client-config').then(r => r.ok ? r.json() : null).then(cfg => {
        if (cfg && cfg.battery_sensor) PHONE_ENTITY = cfg.battery_sensor;
    }).catch(() => {});

    const STATE_MS = 30000;
    const CALENDAR_MS = 300000;
    const TASKS_MS = 60000;

    const HVAC_MODES = ['off', 'cool', 'heat', 'fan_only'];
    const FAN_MODES = ['auto', 'low', 'mid', 'high'];
    const HVAC_ICONS = { off: 'power', cool: 'snowflake', heat: 'flame', fan_only: 'fan' };
    const FAN_ICONS = { auto: 'gauge', low: 'chevrons-down', mid: 'minus', high: 'chevrons-up' };

    let climateState = { hvac_mode: 'off', fan_mode: 'auto', temperature: 21, current_temperature: '--', humidity: '--' };
    let relayoutCam = null;   // assigned once the camera fitter is defined below
    let camScale = 1;

    // ── Page Zoom (from ?zoom= param) ──
    // When the zoom is pinned through the URL the zoom buttons are pointless:
    // the controls column (page zoom + camera zoom) gets hidden and the clock
    // is enlarged instead — see the html[data-url-zoom] rules in dashboard.css.
    let currentZoom = 100;
    const URL_ZOOM = (() => {
        const raw = new URLSearchParams(window.location.search).get('zoom');
        if (raw === null) return null;
        const z = parseInt(raw, 10);
        return (!isNaN(z) && z >= 20 && z <= 300) ? z : null;
    })();
    function getZoomFromUrl() {
        return URL_ZOOM !== null ? URL_ZOOM : 100;
    }
    function computeLayoutAttr() {
        const z = currentZoom / 100;
        const w = window.innerWidth / z;
        const h = window.innerHeight / z;
        if (w < 760 || h < 480) return 'tiny';
        if (w < 1150 || h < 660) return 'compact';
        return 'normal';
    }
    function applyZoom(value) {
        currentZoom = Math.min(300, Math.max(20, value));
        const z = currentZoom / 100;
        document.body.style.zoom = z;
        document.body.style.setProperty('--zoom-scale', String(z));
        document.documentElement.dataset.zoom = String(currentZoom);
        document.documentElement.dataset.layout = computeLayoutAttr();
        document.getElementById('zoom-label').textContent = currentZoom + '%';
        if (relayoutCam) requestAnimationFrame(relayoutCam);
    }
    document.getElementById('zoom-out').addEventListener('click', () => applyZoom(currentZoom - 10));
    document.getElementById('zoom-in').addEventListener('click', () => applyZoom(currentZoom + 10));
    applyZoom(getZoomFromUrl());
    if (URL_ZOOM !== null) document.documentElement.dataset.urlZoom = '1';
    window.addEventListener('resize', () => {
        document.documentElement.dataset.layout = computeLayoutAttr();
        if (relayoutCam) relayoutCam();
    });

    // ── Clock ──
    const DAY_NAMES = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];

    function updateClock() {
        const now = new Date();
        const dayName = DAY_NAMES[now.getDay()];
        const d = String(now.getDate()).padStart(2,'0');
        const m = String(now.getMonth()+1).padStart(2,'0');
        document.getElementById('clock-date').textContent = `${dayName}, ${d}/${m}`;
        const h = String(now.getHours()).padStart(2,'0');
        const min = String(now.getMinutes()).padStart(2,'0');
        const s = String(now.getSeconds()).padStart(2,'0');
        document.getElementById('clock-time').textContent = nightActive ? `${h}:${min}` : `${h}:${min}:${s}`;
    }

    // ── Night mode config (see "Night mode" in dashboard.css) ──
    // Inside the Dana Kiosk app (window.DanaKiosk) the tablet's own light sensor decides,
    // with hysteresis, and night dims the REAL backlight. In a normal browser (or with no
    // sensor reading) the 22-7 schedule decides and night dims with a black overlay.
    // ?night=23-6 forces a fixed schedule (device clock) | ?night=off | ?nightdim=0.55 (overlay opacity)
    // | ?nightlux=5 (dark below this, bright again at 3x this) | ?backlight=6 (kiosk night backlight, 0-255)
    const KIOSK = window.DanaKiosk && typeof window.DanaKiosk.lux === 'function'
        && typeof window.DanaKiosk.setBacklight === 'function' ? window.DanaKiosk : null;
    const NIGHT_WAKE_MS = 2 * 60 * 1000;
    const NIGHT_PARAM = new URLSearchParams(window.location.search).get('night') || '';
    const NIGHT_OFF = NIGHT_PARAM === 'off';
    const NIGHT_FIXED_HOURS = /^\d{1,2}-\d{1,2}$/.test(NIGHT_PARAM);
    const USE_LIGHT_SENSOR = !!KIOSK && !NIGHT_OFF && !NIGHT_FIXED_HOURS;
    const NIGHT_HOURS = (() => {
        const m = /^(\d{1,2})-(\d{1,2})$/.exec(NIGHT_FIXED_HOURS ? NIGHT_PARAM : '22-7');
        return { start: +m[1] % 24, end: +m[2] % 24 };
    })();
    const NIGHT_LUX = (() => {
        const v = parseFloat(new URLSearchParams(window.location.search).get('nightlux'));
        return isNaN(v) || v <= 0 ? 5 : v;
    })();
    let luxDark = null;        // null = no usable sensor reading -> schedule
    let luxPending = 0, luxFails = 0;
    const NIGHT_DIM = (() => {
        const v = parseFloat(new URLSearchParams(window.location.search).get('nightdim'));
        return isNaN(v) ? 0.55 : Math.min(0.95, Math.max(0, v));
    })();
    // Kiosk night backlight level (0-255); by day the system brightness applies.
    const NIGHT_BACKLIGHT = (() => {
        const v = parseInt(new URLSearchParams(window.location.search).get('backlight'), 10);
        return isNaN(v) ? 6 : Math.min(255, Math.max(0, v));
    })();
    let nightWakeUntil = 0;
    function computeNight() {
        if (NIGHT_OFF || Date.now() < nightWakeUntil) return false;
        if (USE_LIGHT_SENSOR && luxDark !== null) return luxDark;
        const h = new Date().getHours(), { start, end } = NIGHT_HOURS;
        return start === end ? false : (start > end ? (h >= start || h < end) : (h >= start && h < end));
    }
    let nightActive = computeNight();
    document.documentElement.toggleAttribute('data-night', nightActive);
    document.documentElement.style.setProperty('--night-dim', KIOSK ? 0 : NIGHT_DIM);

    // ── Camera — WebRTC via go2rtc signaling proxy ──
    // Reliability strategy (kiosk runs 24/7, often with screen off):
    //  * Liveness = video.currentTime advancing (frames actually decoding).
    //    requestVideoFrameCallback is only a bonus signal — it stops firing
    //    when rendering is suspended (background tab / screen off), which
    //    previously caused false "stalled" detections on stable networks.
    //  * Watchdog is skipped while the tab is hidden; on wake it grants a
    //    grace period and reconnects only if truly dead.
    //  * Stalled stream → frozen banner + reconnect with exponential backoff
    //    (3s → 45s). "disconnected" ICE gets a grace period to self-heal.
    //  * Forced refresh every CAM_REFRESH_MS to avoid stale long-lived peers.
    const camVideo = document.getElementById('cam-video');
    const camLoading = document.getElementById('cam-loading');
    const camViewport = document.getElementById('cam-viewport');
    const camLiveBadge = document.getElementById('cam-live-badge');
    const camBadgeText = document.getElementById('cam-badge-text');
    const camFrozenOverlay = document.getElementById('cam-frozen-overlay');

    const CAM_STALL_MS = 10000;       // no media progress for this long = frozen
    const CAM_WATCHDOG_MS = 5000;
    const CAM_DISCONNECT_GRACE_MS = 8000;
    const CAM_CONNECT_TIMEOUT_MS = 25000;
    const CAM_REFRESH_MS = 4 * 60 * 60 * 1000; // proactive restart
    const CAM_BACKOFF_MIN_MS = 3000;
    const CAM_BACKOFF_MAX_MS = 45000;
    // WebRTC over TCP (go2rtc's passive TCP candidate on :8555) unless ?camtcp=0.
    // The tablet's Wi-Fi loses ~10% of packets and go2rtc doesn't resend lost
    // ones; a 1440p keyframe is ~420 packets, so over UDP one practically never
    // arrived whole, nothing decoded, and the watchdog reconnected forever.
    // TCP retransmits; at ~3 Mbps the link has plenty of headroom for that.
    const CAM_TCP_ONLY = new URLSearchParams(window.location.search).get('camtcp') !== '0';

    /** Keep only the answer's TCP ICE candidates (when it has any). */
    function preferTcpCandidates(sdp) {
        const lines = sdp.split(/\r?\n/);
        const isCand = l => l.startsWith('a=candidate:');
        const isTcp = l => / tcp /i.test(l);
        if (!lines.some(l => isCand(l) && isTcp(l))) return sdp;
        return lines.filter(l => !isCand(l) || isTcp(l)).join('\r\n');
    }

    let pc = null;
    let camGen = 0;                   // generation counter — invalidates stale async callbacks
    let camReconnectTimer = null;
    let camBackoffMs = CAM_BACKOFF_MIN_MS;
    let camLastAliveAt = Date.now();
    let lastVideoTime = -1;
    let camConnectingSince = 0;
    let camState = 'connecting';      // connecting | live | frozen | offline
    let camStartedAt = 0;

    function setCamState(state) {
        camState = state;
        if (state === 'live') {
            camLiveBadge.className = 'cam-live live';
            camBadgeText.textContent = 'LIVE';
            camFrozenOverlay.classList.remove('visible');
        } else if (state === 'connecting') {
            camLiveBadge.className = 'cam-live connecting';
            camBadgeText.textContent = 'CONNECTING';
            camFrozenOverlay.classList.remove('visible');
        } else if (state === 'frozen') {
            camLiveBadge.className = 'cam-live frozen';
            camBadgeText.textContent = 'FROZEN';
            camFrozenOverlay.classList.add('visible');
        } else { // offline
            camLiveBadge.className = 'cam-live frozen';
            camBadgeText.textContent = 'OFFLINE';
            camFrozenOverlay.classList.add('visible');
        }
    }

    let camLastRvfcAt = Date.now();

    function noteAlive() {
        camLastAliveAt = Date.now();
        camLastRvfcAt = Date.now();
        if (camState !== 'live') {
            camLoading.style.display = 'none';
            camVideo.style.display = 'block';
            setCamState('live');
        }
        camBackoffMs = CAM_BACKOFF_MIN_MS;
    }

    // rVFC: precise when rendering is active (authoritative when visible).
    if (typeof camVideo.requestVideoFrameCallback === 'function') {
        const frameLoop = () => {
            if (camVideo.readyState >= 2) {
                camLastRvfcAt = Date.now();
                noteAlive();
            }
            camVideo.requestVideoFrameCallback(frameLoop);
        };
        camVideo.requestVideoFrameCallback(frameLoop);
    }
    camVideo.addEventListener('playing', () => { lastVideoTime = camVideo.currentTime; noteAlive(); });

    // Size the camera card to the real stream aspect ratio so the feed fills it
    // edge to edge (no letterbox bars). Falls back to 16:9 in CSS.
    // JS computes an explicit fitted pixel box: CSS aspect-ratio cannot honour
    // both max-width and max-height without distorting the ratio.
    let camAspect = 16 / 9;
    // Wide layout: the top row is exactly as tall as the camera at its column
    // width (no empty bands above/below it), capped so the calendar keeps at
    // least CAL_MIN_SHARE of the height; the calendar takes the rest.
    const CAL_MIN_SHARE = 0.25;
    // The motion strip under the camera (.motion-strip height + .cam-slot gap).
    const STRIP_SPACE = 12 + 4;
    function fitMainRow(slot) {
        const root = document.documentElement.style;
        if (window.matchMedia('(max-width: 1000px)').matches) { root.removeProperty('--main-h'); return; }
        const main = document.querySelector('.dash-main');
        const cs = getComputedStyle(main);
        const padY = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom);
        const bodyH = document.body.clientHeight;
        const want = slot.clientWidth / camAspect + padY + STRIP_SPACE;
        root.setProperty('--main-h', Math.floor(Math.min(want, bodyH * (1 - CAL_MIN_SHARE))) + 'px');
    }
    function fitCamPanel() {
        const slot = document.querySelector('.cam-slot');
        if (!slot) return;
        fitMainRow(slot);
        const availW = slot.clientWidth;
        const availH = slot.clientHeight - STRIP_SPACE;
        if (!availW || !availH) return;
        // Largest ratio-correct box that fits the slot.
        let w = availW;
        let h = w / camAspect;
        if (h > availH) { h = availH; w = h * camAspect; }
        document.documentElement.style.setProperty('--cam-w', Math.floor(w) + 'px');
        document.documentElement.style.setProperty('--cam-h', Math.floor(h) + 'px');
    }

    function syncCamAspect() {
        const w = camVideo.videoWidth, h = camVideo.videoHeight;
        if (w && h) {
            camAspect = w / h;
            document.documentElement.style.setProperty('--cam-ratio', `${w} / ${h}`);
        }
        fitCamPanel();
    }
    // Exposed for the zoom/resize handlers defined earlier in the file.
    relayoutCam = fitCamPanel;
    requestAnimationFrame(fitCamPanel);   // size the rows before the stream's metadata arrives
    camVideo.addEventListener('loadedmetadata', syncCamAspect);
    camVideo.addEventListener('resize', syncCamAspect);
    window.addEventListener('resize', fitCamPanel);
    if (window.ResizeObserver) {
        new ResizeObserver(fitCamPanel).observe(document.querySelector('.cam-slot'));
    }

    function watchdogCheck() {
        const now = Date.now();

        // Unpause a paused video (some WebViews pause muted autoplay on quirks).
        if (camVideo.srcObject && camVideo.paused) camVideo.play().catch(() => {});

        // Bootstrap: currentTime progress while connecting
        if (camVideo.readyState >= 2 && !camVideo.paused && camVideo.currentTime !== lastVideoTime) {
            lastVideoTime = camVideo.currentTime;
            if (camState === 'connecting') noteAlive();
        }

        // Skip stall detection while hidden (compositor may suspend rVFC)
        if (document.hidden) return;

        // Proactive refresh on very long uptimes
        if (camStartedAt && (now - camStartedAt) > CAM_REFRESH_MS && camState === 'live') {
            console.warn('Camera: scheduled refresh');
            connectCamera();
            return;
        }

        if (camState === 'connecting') {
            if (camConnectingSince && (now - camConnectingSince) > CAM_CONNECT_TIMEOUT_MS) {
                console.warn('Camera: connect timeout, retrying');
                setCamState('offline');
                scheduleReconnect();
            }
            return;
        }

        if (camState !== 'live') return;

        // Stall if no presented frames (rVFC) in CAM_STALL_MS while visible
        const noFrames = (now - camLastRvfcAt) > CAM_STALL_MS;
        if (noFrames || camVideo.error || camVideo.readyState < 2) {
            console.warn('Camera: stream stalled (readyState=' + camVideo.readyState + '), reconnecting');
            setCamState('frozen');
            scheduleReconnect();
        }
    }

    function connectCamera() {
        const gen = ++camGen;
        if (pc) { try { pc.close(); } catch(e) {} pc = null; }
        if (camReconnectTimer) { clearTimeout(camReconnectTimer); camReconnectTimer = null; }

        camVideo.pause();
        camVideo.srcObject = null;   // detach old stream so its currentTime can't fake liveness
        camVideo.style.display = 'none';
        camLoading.style.display = 'flex';
        camLoading.querySelector('span').textContent = 'Connecting to camera…';
        camConnectingSince = Date.now();
        camStartedAt = Date.now();
        camLastAliveAt = Date.now();
        camLastRvfcAt = Date.now();
        lastVideoTime = -1;
        setCamState('connecting');

        const conn = new RTCPeerConnection({
            iceServers: [{ urls: 'stun:stun.l.google.com:19302' }]
        });
        pc = conn;
        conn.addTransceiver('video', { direction: 'recvonly' });

        conn.ontrack = (event) => {
            if (gen !== camGen) return;
            camVideo.srcObject = event.streams[0];
            camVideo.style.display = 'block';
            camLoading.style.display = 'none';
            camVideo.play().catch(() => {});
            // Wait for actual decoded frames before declaring LIVE (watchdog does it).
        };

        conn.oniceconnectionstatechange = () => {
            if (gen !== camGen) return;
            const state = conn.iceConnectionState;
            if (state === 'connected' || state === 'completed') {
                // LIVE is set by noteAlive() once media actually flows.
            } else if (state === 'failed' || state === 'closed') {
                setCamState('offline');
                scheduleReconnect(500);
            } else if (state === 'disconnected') {
                // Give ICE a chance to self-heal before tearing it down.
                setTimeout(() => {
                    if (gen !== camGen || !pc) return;
                    const s = pc.iceConnectionState;
                    if (s === 'disconnected' || s === 'failed' || s === 'closed') {
                        setCamState('offline');
                        scheduleReconnect(500);
                    }
                }, CAM_DISCONNECT_GRACE_MS);
            }
        };

        conn.createOffer()
            .then(offer => {
                if (gen !== camGen) return null;
                return conn.setLocalDescription(offer).then(() => conn.localDescription.sdp);
            })
            .then(sdp => {
                if (!sdp) return null;
                return fetch(`/api/frigate/webrtc/${FRIGATE_CAMERA}`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/sdp' },
                    body: sdp
                });
            })
            .then(r => {
                if (!r) return null;
                if (!r.ok) throw new Error(`WebRTC signaling failed: ${r.status}`);
                return r.text();
            })
            .then(answer => {
                if (!answer || gen !== camGen) return;
                const sdp = CAM_TCP_ONLY ? preferTcpCandidates(answer) : answer;
                return conn.setRemoteDescription(new RTCSessionDescription({ type: 'answer', sdp }));
            })
            .catch(err => {
                if (gen !== camGen) return;
                console.error('WebRTC connection error:', err);
                setCamState('offline');
                camLoading.querySelector('span').textContent = 'Camera unavailable — retrying…';
                scheduleReconnect();
            });
    }

    function scheduleReconnect(delayMs) {
        if (camReconnectTimer) return;
        const delay = typeof delayMs === 'number' ? delayMs : camBackoffMs;
        camBackoffMs = Math.min(CAM_BACKOFF_MAX_MS, camBackoffMs * 2);
        camReconnectTimer = setTimeout(() => {
            camReconnectTimer = null;
            connectCamera();
        }, delay);
    }

    document.getElementById('cam-retry-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        if (camReconnectTimer) { clearTimeout(camReconnectTimer); camReconnectTimer = null; }
        camBackoffMs = CAM_BACKOFF_MIN_MS;
        connectCamera();
    });

    // Recover promptly when the kiosk tab becomes visible again after sleep.
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') return;
        // Grace period: the media pipeline may need a beat to resume after wake.
        camLastAliveAt = Date.now();
        camLastRvfcAt = Date.now();
        lastVideoTime = camVideo.currentTime;
        if (camState === 'live') {
            // Watchdog re-checks frames shortly; nudge playback now.
            camVideo.play().catch(() => {});
        } else if (camState !== 'connecting') {
            camBackoffMs = CAM_BACKOFF_MIN_MS;
            connectCamera();
        }
    });

    setInterval(watchdogCheck, CAM_WATCHDOG_MS);
    connectCamera();

    // ── Camera zoom & pan ──
    // State: scale s and translation (tx, ty) in the video element's own CSS px,
    // applied as translate(tx, ty) scale(s) around the centre. Pans are clamped
    // so the picture always covers the frame (no dragging past its edges). One
    // finger drags 1:1, two fingers pinch around their midpoint (which also
    // pans), the wheel zooms around the cursor, and the buttons around the
    // centre. Pointer deltas are converted to element px, so page zoom
    // (?zoom=) doesn't change the speed. No CSS transition while a finger is
    // down, so the picture tracks it exactly.
    const CAM_MAX_SCALE = 5;
    let camTx = 0, camTy = 0;
    const camPointers = new Map();     // pointerId -> {x, y}
    let camGesture = null;

    // Screen px per element px (page zoom), and the element's centre on screen.
    function camFrame() {
        const r = camViewport.getBoundingClientRect();
        const ratio = (r.width / camViewport.offsetWidth) || 1;
        return { ratio, cx: r.left + r.width / 2, cy: r.top + r.height / 2, w: camViewport.offsetWidth, h: camViewport.offsetHeight };
    }
    function clampCam() {
        camScale = Math.min(CAM_MAX_SCALE, Math.max(1, camScale));
        const f = camFrame();
        const maxX = (camScale - 1) * f.w / 2, maxY = (camScale - 1) * f.h / 2;
        camTx = Math.min(maxX, Math.max(-maxX, camTx));
        camTy = Math.min(maxY, Math.max(-maxY, camTy));
    }
    function applyCamZoom(animate) {
        clampCam();
        camVideo.style.transition = animate ? 'transform 0.15s ease-out' : 'none';
        camVideo.style.transform = `translate(${camTx}px, ${camTy}px) scale(${camScale})`;
        document.getElementById('cam-zoom-label').textContent = Math.round(camScale * 100) + '%';
    }
    // Zoom to `scale`, keeping the picture point under screen point (sx, sy) in place.
    function zoomCamAt(scale, sx, sy, animate) {
        const f = camFrame();
        const mx = (sx - f.cx) / f.ratio, my = (sy - f.cy) / f.ratio;   // relative to centre, element px
        const px = (mx - camTx) / camScale, py = (my - camTy) / camScale;
        camScale = Math.min(CAM_MAX_SCALE, Math.max(1, scale));
        camTx = mx - camScale * px;
        camTy = my - camScale * py;
        applyCamZoom(animate);
    }
    function zoomCamCentre(scale) { const f = camFrame(); zoomCamAt(scale, f.cx, f.cy, true); }
    document.getElementById('cam-zoom-in').addEventListener('click', () => zoomCamCentre(camScale + 0.5));
    document.getElementById('cam-zoom-out').addEventListener('click', () => zoomCamCentre(camScale - 0.5));
    document.getElementById('cam-zoom-reset').addEventListener('click', () => { camScale = 1; camTx = 0; camTy = 0; applyCamZoom(true); });

    function startCamGesture() {
        const pts = [...camPointers.values()];
        const f = camFrame();
        if (pts.length >= 2) {
            const [a, b] = pts;
            const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
            camGesture = {
                kind: 'pinch', s0: camScale,
                d0: Math.hypot(a.x - b.x, a.y - b.y) || 1,
                // Picture point under the fingers' midpoint, in unscaled element px.
                px: ((mx - f.cx) / f.ratio - camTx) / camScale,
                py: ((my - f.cy) / f.ratio - camTy) / camScale,
            };
        } else if (pts.length === 1) {
            camGesture = { kind: 'drag', x0: pts[0].x, y0: pts[0].y, tx0: camTx, ty0: camTy };
        } else {
            camGesture = null;
        }
    }
    camViewport.addEventListener('pointerdown', (e) => {
        if (e.pointerType === 'mouse' && e.button !== 0) return;
        if (e.target.closest('button')) return;   // e.g. "Retry now" keeps working
        camViewport.setPointerCapture(e.pointerId);
        camPointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        startCamGesture();
    });
    camViewport.addEventListener('pointermove', (e) => {
        if (!camPointers.has(e.pointerId) || !camGesture) return;
        camPointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        const f = camFrame();
        if (camGesture.kind === 'pinch' && camPointers.size >= 2) {
            const [a, b] = [...camPointers.values()];
            const mx = ((a.x + b.x) / 2 - f.cx) / f.ratio, my = ((a.y + b.y) / 2 - f.cy) / f.ratio;
            camScale = Math.min(CAM_MAX_SCALE, Math.max(1, camGesture.s0 * Math.hypot(a.x - b.x, a.y - b.y) / camGesture.d0));
            camTx = mx - camScale * camGesture.px;
            camTy = my - camScale * camGesture.py;
            applyCamZoom(false);
        } else if (camGesture.kind === 'drag' && camScale > 1) {
            camTx = camGesture.tx0 + (e.clientX - camGesture.x0) / f.ratio;
            camTy = camGesture.ty0 + (e.clientY - camGesture.y0) / f.ratio;
            applyCamZoom(false);
        }
    });
    function endCamPointer(e) {
        if (!camPointers.delete(e.pointerId)) return;
        startCamGesture();   // pinch → drag with the remaining finger, without a jump
    }
    camViewport.addEventListener('pointerup', endCamPointer);
    camViewport.addEventListener('pointercancel', endCamPointer);

    camViewport.addEventListener('wheel', (e) => {
        e.preventDefault();
        zoomCamAt(camScale * (e.deltaY > 0 ? 1 / 1.15 : 1.15), e.clientX, e.clientY, false);
    }, { passive: false });
    // The frame changes size with the layout; keep the pan inside it.
    if (window.ResizeObserver) new ResizeObserver(() => applyCamZoom(false)).observe(camViewport);

    // ── Climate ──
    async function refreshClimate() {
        try {
            const r = await fetch(`/api/ha/state/${CLIMATE_ENTITY}`);
            if (!r.ok) return;
            const data = await r.json();
            const a = data.attributes || {};
            climateState.hvac_mode = (data.state === 'off') ? 'off' : (a.last_on_operation || data.state);
            climateState.fan_mode = a.fan_mode || 'auto';
            climateState.temperature = a.temperature || 21;
            climateState.current_temperature = a.current_temperature ?? '--';
            climateState.humidity = a.current_humidity ?? '--';

            document.getElementById('cur-temp').textContent = climateState.current_temperature + '°';
            document.getElementById('target-temp').textContent = climateState.temperature + '°';
            document.getElementById('climate-humidity').querySelector('span').textContent = climateState.humidity + '%';

            renderHvacModes();
            renderFanModes();
        } catch(e) { console.error('Climate refresh error:', e); }
    }

    function renderHvacModes() {
        const container = document.getElementById('hvac-modes');
        container.innerHTML = '';
        HVAC_MODES.forEach(mode => {
            const btn = document.createElement('button');
            btn.className = 'mode-pill' + (climateState.hvac_mode === mode ? ' active' : '');
            btn.title = mode.replace('_', ' ');
            btn.innerHTML = `<i data-lucide="${HVAC_ICONS[mode]}"></i>`;
            btn.addEventListener('click', () => setHvacMode(mode));
            container.appendChild(btn);
        });
        lucide.createIcons();
    }

    function renderFanModes() {
        const container = document.getElementById('fan-modes');
        container.innerHTML = '';
        if (climateState.hvac_mode === 'off') {
            container.innerHTML = '<span class="fan-off">AC is off</span>';
            return;
        }
        FAN_MODES.forEach(mode => {
            const btn = document.createElement('button');
            btn.className = 'mode-pill' + (climateState.fan_mode === mode ? ' active' : '');
            btn.title = 'Fan: ' + mode;
            btn.innerHTML = `<i data-lucide="${FAN_ICONS[mode]}"></i>`;
            btn.addEventListener('click', () => setFanMode(mode));
            container.appendChild(btn);
        });
        lucide.createIcons();
    }

    async function setHvacMode(mode) {
        const service = mode === 'off' ? 'turn_off' : 'set_hvac_mode';
        const service_data = mode === 'off' ? {} : { hvac_mode: mode };
        try {
            await fetch('/api/ha/service', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ domain: 'climate', service, entity_id: CLIMATE_ENTITY, service_data })
            });
            setTimeout(refreshClimate, 800);
        } catch(e) { console.error('setHvacMode error:', e); }
    }

    async function setFanMode(mode) {
        try {
            await fetch('/api/ha/service', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ domain: 'climate', service: 'set_fan_mode', entity_id: CLIMATE_ENTITY, service_data: { fan_mode: mode } })
            });
            setTimeout(refreshClimate, 800);
        } catch(e) { console.error('setFanMode error:', e); }
    }

    async function setTemperature(temp) {
        temp = Math.min(32, Math.max(16, temp));
        try {
            await fetch('/api/ha/service', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ domain: 'climate', service: 'set_temperature', entity_id: CLIMATE_ENTITY, service_data: { temperature: temp } })
            });
            climateState.temperature = temp;
            document.getElementById('target-temp').textContent = temp + '°';
        } catch(e) { console.error('setTemperature error:', e); }
    }

    document.getElementById('temp-up').addEventListener('click', () => setTemperature(climateState.temperature + 1));
    document.getElementById('temp-down').addEventListener('click', () => setTemperature(climateState.temperature - 1));

    let tempInterval = null;
    function startTempHold(dir) {
        tempInterval = setInterval(() => setTemperature(climateState.temperature + dir), 200);
    }
    function stopTempHold() { clearInterval(tempInterval); tempInterval = null; }
    ['temp-up', 'temp-down'].forEach(id => {
        const el = document.getElementById(id);
        const dir = id === 'temp-up' ? 1 : -1;
        el.addEventListener('mousedown', () => startTempHold(dir));
        el.addEventListener('mouseup', stopTempHold);
        el.addEventListener('mouseleave', stopTempHold);
        el.addEventListener('touchstart', (e) => { e.preventDefault(); startTempHold(dir); }, { passive: false });
        el.addEventListener('touchend', stopTempHold);
    });

    // ── Batteries ──
    function battColor(pct) {
        if (pct > 50) return 'var(--listening-green)';
        if (pct > 20) return 'var(--connecting-amber)';
        return 'var(--destructive)';
    }

    async function refreshBatteries() {
        try {
            const [phoneR, watchR] = await Promise.all([
                fetch(`/api/ha/state/${PHONE_ENTITY}`),
                fetch(`/api/ha/state/${WATCH_ENTITY}`)
            ]);
            if (phoneR.ok) {
                const d = await phoneR.json();
                const pct = parseInt(d.state) || 0;
                document.getElementById('phone-pct').textContent = pct + '%';
                document.getElementById('phone-pct').style.color = battColor(pct);
                document.getElementById('phone-bar').style.width = pct + '%';
                document.getElementById('phone-bar').style.background = battColor(pct);
            }
            if (watchR.ok) {
                const d = await watchR.json();
                const pct = parseInt(d.state) || 0;
                document.getElementById('watch-pct').textContent = pct + '%';
                document.getElementById('watch-pct').style.color = battColor(pct);
                document.getElementById('watch-bar').style.width = pct + '%';
                document.getElementById('watch-bar').style.background = battColor(pct);
            }
        } catch(e) { console.error('Battery refresh error:', e); }
    }

    // ── Tasks ──
    async function refreshTasks() {
        try {
            const [r, br] = await Promise.all([
                fetch('/api/tasks'),
                new Date().getHours() < 12 ? fetch('/api/briefing/today').catch(() => null) : null,
            ]);
            if (!r.ok) return;
            const tasks = await r.json();
            const high = tasks.filter(t => t.status === 'needs_action' && (t.priority === 'urgent' || t.priority === 'high'));
            const container = document.getElementById('tasks-list');
            // Mornings: today's wake briefing (core/briefing.py) on top of the card.
            let brief = null;
            try { brief = br && br.ok ? (await br.json()).briefing : null; } catch (_) { brief = null; }
            container.innerHTML = '';
            if (brief) {
                const el = document.createElement('div');
                const rtl = /[\u0590-\u05FF]/.test(brief.headline + brief.points.join(''));
                el.className = `brief-item ${rtl ? 'rtl' : 'ltr'}`;
                el.innerHTML = `<div class="brief-head">☀️ ${escapeHtml(brief.headline)}</div>`
                    + brief.points.slice(0, 3).map(p => `<div class="brief-pt">${escapeHtml(p)}</div>`).join('');
                container.appendChild(el);
            }
            if (high.length === 0) {
                if (!brief) container.innerHTML = '<div class="tasks-empty">No high priority tasks</div>';
                return;
            }
            high.slice(0, 4).forEach(t => {
                const el = document.createElement('div');
                // Hebrew titles are right-aligned (and the dot flips to the
                // right), English stays left-aligned.
                const rtl = /[\u0590-\u05FF]/.test(t.title || '');
                el.className = `task-item ${t.priority || ''} ${rtl ? 'rtl' : 'ltr'}`;
                el.innerHTML = `
                    <span class="task-dot ${t.priority}"></span>
                    <span class="task-title">${escapeHtml(t.title)}</span>
                `;
                container.appendChild(el);
            });
        } catch(e) { console.error('Tasks refresh error:', e); }
    }

    function escapeHtml(t) {
        if (!t) return '';
        return t.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    }

    // ── Calendar Strip ──
    function getSunday(d) {
        const r = new Date(d);
        r.setDate(r.getDate() - r.getDay());
        r.setHours(0,0,0,0);
        return r;
    }
    function fmtDateKey(d) {
        return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
    }
    function isToday(d) {
        const now = new Date();
        return d.getFullYear()===now.getFullYear() && d.getMonth()===now.getMonth() && d.getDate()===now.getDate();
    }

    const CAL_COLORS = [
        'rgba(255,45,85,0.28)', 'rgba(0,122,255,0.28)', 'rgba(52,199,89,0.28)',
        'rgba(175,82,222,0.28)', 'rgba(255,149,0,0.28)', 'rgba(90,200,250,0.28)',
        'rgba(255,59,48,0.28)', 'rgba(255,204,0,0.28)'
    ];
    function calColor(calId) {
        let h = 0;
        for (let i = 0; i < (calId||'').length; i++) { h = ((h<<5)-h) + calId.charCodeAt(i); h |= 0; }
        return CAL_COLORS[Math.abs(h) % CAL_COLORS.length];
    }

    const CAL_DAY_MAX = 12;   // events rendered per day; fitCalStrip shrinks/hides what doesn't fit
    // Two weeks are fetched every time: this week feeds the strip, both feed the
    // full-screen view, which then opens instantly from this cache.
    let calEvents = null, calFetchedAt = 0, calRefreshing = null;
    function refreshCalendar() {
        if (calRefreshing) return calRefreshing;
        calRefreshing = (async () => {
            try {
                const sunday = getSunday(new Date());
                const lastDay = new Date(sunday);
                lastDay.setDate(lastDay.getDate() + 13);
                const r = await fetch(`/api/calendar/events?start_date=${fmtDateKey(sunday)}&end_date=${fmtDateKey(lastDay)}`);
                if (!r.ok) return;
                calEvents = await r.json();
                calFetchedAt = Date.now();
                renderCalendarViews();
            } catch(e) { console.error('Calendar refresh error:', e); }
            finally { calRefreshing = null; }
        })();
        return calRefreshing;
    }
    function renderCalendarViews() {
        if (!calEvents) return;
        const sunday = getSunday(new Date());
        const nextSunday = new Date(sunday);
        nextSunday.setDate(nextSunday.getDate() + 7);
        renderWeekRow(document.getElementById('cal-row'), calEvents, sunday);
        if (calOverlayOpen) {
            renderWeekRow(document.getElementById('cal-ov-this'), calEvents, sunday);
            renderWeekRow(document.getElementById('cal-ov-next'), calEvents, nextSunday);
            const t = new Date(calFetchedAt);
            document.getElementById('cal-ov-updated').textContent =
                `Updated ${String(t.getHours()).padStart(2,'0')}:${String(t.getMinutes()).padStart(2,'0')}`;
        }
        fitCalStrip();
    }

    // Compact 24h start time for the strip: 15:00 → "15", 16:30 → "16:3",
    // 10:35 → "10:35". All-day / unparsable events get no time.
    function fmtEvTime(ev) {
        if (!ev || ev.all_day || !ev.start) return '';
        const d = new Date(ev.start);
        if (isNaN(d.getTime())) return '';
        const h = String(d.getHours()).padStart(2, '0');
        const m = d.getMinutes();
        return m ? `${h}:${String(m).padStart(2, '0')}` : h;
    }

    // Local day key + real instant for an event. Google can return mixed
    // timezone offsets per calendar ("...Z" vs "...+03:00"), so comparing raw
    // ISO strings both misplaces events on the day grid and orders personal
    // events after work events even when they start sooner.
    function evDayKey(ev) {
        if (!ev || !ev.start) return '';
        if (ev.all_day) return ev.start.substring(0, 10);
        const d = new Date(ev.start);
        return isNaN(d.getTime()) ? ev.start.substring(0, 10) : fmtDateKey(d);
    }
    function evSortTs(ev) {
        if (ev.all_day) return Number.MIN_SAFE_INTEGER;   // all-day rows come first
        const d = new Date(ev.start);
        return isNaN(d.getTime()) ? Number.MAX_SAFE_INTEGER : d.getTime();
    }

    // Has the event already finished? All-day events count as done once their
    // (exclusive) end date is today or earlier; timed events once end < now.
    function evIsPast(ev, todayKey) {
        if (!ev) return false;
        if (ev.all_day) {
            const endKey = (ev.end || '').substring(0, 10);
            return endKey ? endKey <= todayKey : false;
        }
        const d = new Date(ev.end || ev.start);
        return isNaN(d.getTime()) ? false : d.getTime() < Date.now();
    }

    function renderWeekRow(container, events, sunday) {
        container.innerHTML = '';
        const DAY_SHORT = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];
        const todayKey = fmtDateKey(new Date());
        const tomorrow = new Date();
        tomorrow.setDate(tomorrow.getDate() + 1);
        const tomorrowKey = fmtDateKey(tomorrow);
        const cols = [];
        for (let i = 0; i < 7; i++) {
            const d = new Date(sunday);
            d.setDate(d.getDate() + i);
            const key = fmtDateKey(d);
            const dayPast = key < todayKey;
            const dayEvents = events.filter(ev => evDayKey(ev) === key);
            dayEvents.sort((a, b) => evSortTs(a) - evSortTs(b));
            const el = document.createElement('div');
            el.className = 'cal-day' + (isToday(d) ? ' today' : '') + (dayPast ? ' past' : '');
            // Today is the widest column, tomorrow slightly wider than the rest.
            cols.push(key === todayKey ? '1.5fr' : key === tomorrowKey ? '1.18fr' : '1fr');
            let eventsHtml = '';
            if (dayEvents.length > 0) {
                dayEvents.slice(0, CAL_DAY_MAX).forEach(ev => {
                    const c = calColor(ev.calendar_id);
                    const t = fmtEvTime(ev);
                    // A past day is dimmed as a whole, so only flag ended events
                    // on days that are still live.
                    const evPast = !dayPast && evIsPast(ev, todayKey);
                    eventsHtml += `<div class="cd-ev${evPast ? ' past' : ''}" style="background:${c};"><span class="cd-ev-name">${escapeHtml(ev.summary)}</span><span class="cd-ev-time">${t}</span></div>`;
                });
                const extra = Math.max(0, dayEvents.length - CAL_DAY_MAX);
                eventsHtml += `<div class="cd-more" data-extra="${extra}"${extra ? '' : ' hidden'}>+${extra} more</div>`;
            } else {
                eventsHtml = '<div class="cd-empty">--</div>';
            }
            el.innerHTML = `
                <div class="cd-head">
                    <span class="cd-name">${DAY_SHORT[d.getDay()]}</span>
                    <span class="cd-num">${d.getDate()}</span>
                </div>
                <div class="cd-events">${eventsHtml}</div>
            `;
            container.appendChild(el);
        }
        container.style.gridTemplateColumns = cols.join(' ');
    }

    // Busy days: shrink the last events to one compact line, latest first,
    // until the day fits; if even all-compact overflows, hide from the end
    // behind "+N more". Re-run whenever the strip changes size.
    function fitCalDay(list) {
        const cards = [...list.querySelectorAll('.cd-ev')];
        const more = list.querySelector('.cd-more');
        if (!more) return;
        const baseExtra = Number(more.dataset.extra) || 0;
        cards.forEach(c => { c.classList.remove('compact'); c.hidden = false; });
        const setMore = hiddenCount => {
            const n = baseExtra + hiddenCount;
            more.hidden = n === 0;
            more.textContent = `+${n} more`;
        };
        setMore(0);
        const overflows = () => list.scrollHeight > list.clientHeight + 1;
        for (let i = cards.length - 1; i >= 0 && overflows(); i--) cards[i].classList.add('compact');
        let hidden = 0;
        for (let i = cards.length - 1; i > 0 && overflows(); i--) {
            cards[i].hidden = true;
            setMore(++hidden);
        }
    }
    function fitCalStrip() {
        document.querySelectorAll('#cal-row .cd-events').forEach(fitCalDay);
        if (calOverlayOpen) document.querySelectorAll('#cal-overlay .cd-events').forEach(fitCalDay);
    }
    if (window.ResizeObserver) {
        const ro = new ResizeObserver(() => fitCalStrip());
        ro.observe(document.getElementById('cal-row'));
        ro.observe(document.getElementById('cal-overlay'));
    } else window.addEventListener('resize', fitCalStrip);

    // ── Full-screen calendar overlay ──
    // Tapping the strip opens this week + next week, drawn with the strip's own
    // day cards (same type and sizes), straight from the cached events; stale
    // data (> 1 min) is refreshed in the background and re-rendered in place.
    // Tapping anywhere closes it.
    const calOverlay = document.getElementById('cal-overlay');
    const CAL_OVERLAY_FRESH_MS = 60000;
    let calOverlayOpen = false;

    function openCalOverlay() {
        if (calOverlayOpen) return;
        calOverlayOpen = true;
        calOverlay.hidden = false;
        renderCalendarViews();
        if (!calEvents || Date.now() - calFetchedAt > CAL_OVERLAY_FRESH_MS) refreshCalendar();
    }
    function closeCalOverlay() {
        if (!calOverlayOpen) return;
        calOverlayOpen = false;
        calOverlay.hidden = true;
    }
    document.querySelector('.cal-strip').addEventListener('click', openCalOverlay);
    // Capture runs before the strip's own listener, so the opening click cannot
    // immediately close the overlay that it is about to open.
    document.addEventListener('click', () => { if (calOverlayOpen) closeCalOverlay(); }, true);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeCalOverlay(); });

    // ── Frigate events rail ──
    // Latest detections and plain motion beside the live feed: GIF preview + relative age.
    // Ages are computed from the server's clock (server_time) so a drifting
    // tablet clock cannot make them wrong. Cards are keyed by event id so the
    // GIFs are not reloaded (or restarted) on every refresh.
    const EVENTS_MS = 15000;
    const EVENTS_LIMIT = 3;   // matches the 3 rows of .events-list
    const eventsRail = document.getElementById('events-rail');
    const eventsList = document.getElementById('events-list');
    const evCards = new Map();            // id -> { el, img, label, ago, event, v }
    let evServerTime = 0, evFetchedAt = 0;

    function fmtAgo(sec) {
        if (sec < 15) return 'just now';
        if (sec < 60) return Math.floor(sec) + 's ago';
        const m = Math.floor(sec / 60);
        if (m < 60) return m + 'm ago';
        const h = Math.floor(m / 60);
        if (h < 24) return h + 'h ' + (m % 60) + 'm ago';
        return Math.floor(h / 24) + 'd ago';
    }
    function evLabelText(e) {
        if (e.type === 'motion') return 'Motion';
        const name = e.sub_label ? String(e.sub_label) : String(e.label || 'event');
        const zone = e.zones && e.zones[0] ? ' \u00b7 ' + String(e.zones[0]).replace(/_/g, ' ') : '';
        return name.charAt(0).toUpperCase() + name.slice(1) + zone;
    }
    function updateEventAges() {
        if (!evServerTime) return;
        const nowSrv = evServerTime + (performance.now() - evFetchedAt) / 1000;
        for (const c of evCards.values()) {
            const e = c.event;
            const live = !e.end_time;
            const age = Math.max(0, nowSrv - (live ? e.start_time : e.end_time));
            c.ago.textContent = live ? '\u25cf now' : fmtAgo(age);
            c.el.classList.toggle('ev-live', live);
            c.el.classList.toggle('ev-new', live || age < 90);
        }
    }
    function makeEventCard(e) {
        const el = document.createElement('div'); el.className = 'ev-card'; el.dataset.id = e.id;
        const media = document.createElement('div'); media.className = 'ev-media';
        const img = document.createElement('img');
        img.decoding = 'async'; img.alt = '';
        img.addEventListener('error', () => el.classList.add('ev-err'));
        img.addEventListener('load', () => el.classList.remove('ev-err'));
        media.appendChild(img);
        const meta = document.createElement('div'); meta.className = 'ev-meta';
        const label = document.createElement('span'); label.className = 'ev-label';
        const ago = document.createElement('span'); ago.className = 'ev-ago';
        meta.append(label, ago); el.append(media, meta);
        return { el, img, label, ago, event: e, v: null };
    }
    function renderEvents(events) {
        const seen = new Set();
        events.forEach((e, i) => {
            seen.add(e.id);
            let c = evCards.get(e.id);
            if (!c) { c = makeEventCard(e); evCards.set(e.id, c); }
            c.event = e;
            c.label.textContent = evLabelText(e);
            c.el.classList.toggle('ev-motion', e.type === 'motion');
            const v = e.end_time ? String(e.end_time) : 'live';
            const src = `${e.preview_url}?v=${encodeURIComponent(v)}`;
            if (c.v !== src) {   // new card, the event just ended or the motion window grew -> fetch the final GIF
                c.v = src;
                c.img.src = src;
            }
            if (eventsList.children[i] !== c.el) eventsList.insertBefore(c.el, eventsList.children[i] || null);
        });
        for (const [id, c] of evCards) if (!seen.has(id)) { c.el.remove(); evCards.delete(id); }
        const empty = eventsList.querySelector('.events-empty');
        if (!events.length && !empty) {
            const d = document.createElement('div'); d.className = 'events-empty'; d.textContent = 'No recent events';
            eventsList.appendChild(d);
        } else if (events.length && empty) empty.remove();
        updateEventAges();
    }
    // Tap a card -> play the event's clip full-screen. Closes on tap outside / ✕,
    // when the clip has ended for a while, or after CLIP_MAX_MS (kiosk must return on its own).
    const CLIP_AFTER_END_MS = 15000;
    const CLIP_MAX_MS = 3 * 60 * 1000;
    const clipOverlay = document.getElementById('clip-overlay');
    const clipVideo = document.getElementById('clip-video');
    const clipTitle = document.getElementById('clip-title');
    let clipTimer = null;
    function closeClip() {
        clearTimeout(clipTimer); clipTimer = null;
        clipOverlay.hidden = true;
        clipVideo.pause();
        clipVideo.removeAttribute('src');
        clipVideo.load();                       // release the decoder + buffers
    }
    function openClip(id) {
        const c = evCards.get(id);
        if (!c) return;
        clipTitle.textContent = `${c.label.textContent} \u00b7 ${c.ago.textContent}`;
        clipVideo.muted = true;                 // bedroom: sound is opt-in via the controls
        clipVideo.src = c.event.clip_url;
        clipOverlay.hidden = false;
        clipVideo.play().catch(() => {});
        clearTimeout(clipTimer); clipTimer = setTimeout(closeClip, CLIP_MAX_MS);
    }
    eventsList.addEventListener('click', (ev) => {
        const card = ev.target.closest('.ev-card');
        if (card && card.dataset.id) openClip(card.dataset.id);
    });
    clipVideo.addEventListener('ended', () => { clearTimeout(clipTimer); clipTimer = setTimeout(closeClip, CLIP_AFTER_END_MS); });
    clipVideo.addEventListener('error', () => { clipTitle.textContent = 'Clip unavailable'; });
    document.getElementById('clip-close').addEventListener('click', closeClip);
    clipOverlay.addEventListener('click', (ev) => { if (ev.target === clipOverlay) closeClip(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !clipOverlay.hidden) closeClip(); });

    async function refreshEvents() {
        if (document.hidden) return;
        try {
            const r = await fetch(`/api/frigate/events/${FRIGATE_CAMERA}?limit=${EVENTS_LIMIT}`);
            if (!r.ok) throw new Error(r.status);
            const data = await r.json();
            evServerTime = data.server_time; evFetchedAt = performance.now();
            renderEvents(data.events || []);
            eventsRail.removeAttribute('data-stale');
        } catch (err) {
            console.warn('Events refresh failed:', err);
            eventsRail.setAttribute('data-stale', '');   // keep showing the last known list, greyed
        }
    }

    // ── Activity strip (last 6 h under the camera) ──
    // A slim heat map built from Frigate's dog detections (+ barks), not raw
    // motion: one cell per 2 min, dark when the dog was still, amber brighter
    // the longer it moved in that slot. People are a thin blue line along the
    // bottom (so "the dog got up because I did" is visible). Thin dark tags
    // mark the hours.
    const MOTION_MS = 30000;
    const ACT_FULL_SEC = 45;   // this much dog movement in a cell = full colour
    const ACT_HOURS = (() => {   // ?acthours=6 (1-24)
        const v = parseInt(new URLSearchParams(window.location.search).get('acthours'), 10);
        return v >= 1 && v <= 24 ? v : 6;
    })();
    const motionCanvas = document.getElementById('motion-canvas');
    let motionData = null;
    function drawMotionStrip() {
        if (!motionCanvas) return;
        const w = motionCanvas.clientWidth, h = motionCanvas.clientHeight;
        if (!w || !h) return;
        const dpr = window.devicePixelRatio || 1;
        const pw = Math.round(w * dpr), ph = Math.round(h * dpr);
        if (motionCanvas.width !== pw || motionCanvas.height !== ph) { motionCanvas.width = pw; motionCanvas.height = ph; }
        const ctx = motionCanvas.getContext('2d');
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, w, h);
        if (!motionData) return;
        const { start, end } = motionData;
        const dog = motionData.series.dog || [], person = motionData.series.person || [];
        const n = Math.max(dog.length, person.length, 1);
        const span = end - start, bw = w / n;
        dog.forEach((sec, i) => {
            if (!sec) return;
            const a = 0.3 + 0.7 * Math.min(1, sec / ACT_FULL_SEC);
            ctx.fillStyle = `rgba(255, 179, 0, ${a.toFixed(3)})`;
            ctx.fillRect(Math.floor(i * bw), 0, Math.ceil(bw), h);
        });
        ctx.fillStyle = 'rgba(90, 169, 255, 0.95)';
        person.forEach((sec, i) => { if (sec) ctx.fillRect(Math.floor(i * bw), h - 2, Math.ceil(bw), 2); });
        ctx.font = '600 8px ' + getComputedStyle(document.body).fontFamily;
        ctx.textBaseline = 'middle';
        for (let t = Math.ceil(start / 3600) * 3600; t < end; t += 3600) {
            const x = Math.round((t - start) / span * w);
            const label = String(new Date(t * 1000).getHours());
            ctx.fillStyle = 'rgba(43, 38, 34, 0.92)';          // --canvas: hour mark + label tag
            ctx.fillRect(x, 0, Math.ceil(ctx.measureText(label).width) + 5, h);
            ctx.fillStyle = 'rgba(247, 245, 240, 0.7)';
            ctx.fillText(label, x + 2.5, h / 2 + 0.5);
        }
    }
    if (window.ResizeObserver && motionCanvas) new ResizeObserver(drawMotionStrip).observe(motionCanvas);
    else window.addEventListener('resize', drawMotionStrip);
    // "Dog moved 12m ago" on the video, from the same payload (server clock).
    const lastMoveEl = document.getElementById('cam-lastmove');
    let motionFetchedAt = 0;
    function updateLastMove() {
        if (!lastMoveEl || !motionData) return;
        const last = motionData.last || {};
        let text, recent = false;
        if (!('dog' in last)) {
            text = `Dog still \u00b7 ${ACT_HOURS}h+`;
        } else if (last.dog == null) {
            text = '\u25cf Dog moving now'; recent = true;
        } else {
            const nowSrv = motionData.server_time + (performance.now() - motionFetchedAt) / 1000;
            const age = Math.max(0, nowSrv - last.dog);
            text = age < 15 ? 'Dog moved just now' : `Dog moved ${fmtAgo(age)}`;
            recent = age < 120;
        }
        lastMoveEl.textContent = text;
        lastMoveEl.classList.toggle('recent', recent);
        lastMoveEl.hidden = false;
    }
    async function refreshMotion() {
        if (document.hidden || !motionCanvas) return;
        try {
            const r = await fetch(`/api/frigate/activity/${FRIGATE_CAMERA}?hours=${ACT_HOURS}`);
            if (!r.ok) throw new Error(r.status);
            motionData = await r.json();
            motionFetchedAt = performance.now();
            drawMotionStrip();
            updateLastMove();
        } catch (err) {
            console.warn('Activity strip refresh failed:', err);
        }
    }

    // ── Ambient light -> night mode (kiosk app's light sensor) ──
    function refreshLight() {
        if (!USE_LIGHT_SENSOR) return;
        try {
            const lux = KIOSK.lux();            // < 0: no sensor / no reading yet
            if (!(lux >= 0)) throw new Error('no reading');
            luxFails = 0;
            const want = lux < NIGHT_LUX ? true : (lux >= NIGHT_LUX * 3 ? false : null); // null = in the hysteresis band
            if (want === null) luxPending = 0;
            else if (luxDark === null) luxDark = want;
            else if (want !== luxDark) { if (++luxPending >= 2) { luxDark = want; luxPending = 0; } }
            else luxPending = 0;
        } catch (err) {
            if (++luxFails >= 5) luxDark = null;   // no reading for ~50 s -> back to the schedule
        }
        applyNight();
    }

    // ── Real backlight (kiosk app): dim at night, system brightness by day ──
    function syncBacklight() {
        if (KIOSK) KIOSK.setBacklight(nightActive ? NIGHT_BACKLIGHT : -1);
    }

    // ── Night mode controller ──
    function applyNight() {
        const night = computeNight();
        if (night === nightActive) return;
        nightActive = night;
        syncBacklight();
        document.documentElement.toggleAttribute('data-night', night);
        updateClock();
    }
    setInterval(applyNight, 5000);
    syncBacklight();
    // A tap during the night restores full brightness for NIGHT_WAKE_MS.
    document.addEventListener('pointerdown', () => {
        if (!nightActive) return;
        nightWakeUntil = Date.now() + NIGHT_WAKE_MS;
        applyNight();
    }, true);
    // Burn-in protection: nudge the layout a few px every 3 minutes.
    function shiftPixels() {
        const r = () => Math.round((Math.random() * 2 - 1) * 4);
        document.documentElement.style.setProperty('--shift-x', r() + 'px');
        document.documentElement.style.setProperty('--shift-y', r() + 'px');
    }
    setInterval(shiftPixels, 3 * 60 * 1000);

    // ── Init & Intervals ──
    updateClock();
    setInterval(updateClock, 1000);

    refreshClimate();
    setInterval(refreshClimate, STATE_MS);

    refreshBatteries();
    setInterval(refreshBatteries, STATE_MS);

    refreshTasks();
    setInterval(refreshTasks, TASKS_MS);

    refreshCalendar();
    setInterval(refreshCalendar, CALENDAR_MS);

    if (USE_LIGHT_SENSOR) { refreshLight(); setInterval(refreshLight, 10000); }

    refreshEvents();
    setInterval(refreshEvents, EVENTS_MS);
    setInterval(updateEventAges, 10000);
    refreshMotion();
    setInterval(refreshMotion, MOTION_MS);
    setInterval(updateLastMove, 10000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) { refreshEvents(); refreshMotion(); } });

    // ── Ask Dana: the Dana Kiosk Android app exposes window.DanaKiosk ──
    if (window.DanaKiosk && typeof window.DanaKiosk.openAssistant === 'function') {
        const askBtn = document.getElementById('ask-dana');
        askBtn.hidden = false;
        askBtn.addEventListener('click', () => window.DanaKiosk.openAssistant());
    }

    lucide.createIcons();
})();
