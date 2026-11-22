/* The cameras addon's dashboard widget (server/docs/dashboard.md): one camera of the dashboard owner's Frigate,
 * live over WebRTC (go2rtc signaling through POST /api/frigate/webrtc/{camera}), with its latest events beside it
 * at the large size (tap one: its clip full-screen). Data from camera_widget (addons/cameras/hooks.py).
 * Registered with Dana.widget (static/dana.js).
 *
 * Reliability (a wall tablet runs it day and night, often with the screen off):
 *  - liveness is frames decoding (requestVideoFrameCallback, else video.currentTime moving); the watchdog skips
 *    while the page is hidden and gives a grace period on waking;
 *  - a stalled stream shows the frozen banner and reconnects with backoff (3 s → 45 s); a "disconnected" ICE
 *    state gets a grace period to heal; the peer is renewed every 4 hours;
 *  - WebRTC over TCP (go2rtc's passive TCP candidate) unless ?camtcp=0: a tablet's Wi-Fi that loses packets
 *    never decodes a whole keyframe over UDP (go2rtc does not resend), TCP retransmits.
 * Zoom: one finger drags, two pinch around their midpoint, the wheel zooms at the cursor; the pan is clamped
 * so the picture always covers the frame, and pointer deltas are divided by the page zoom.
 */
(() => {
  "use strict";
  const STALL_MS = 10000, WATCHDOG_MS = 5000, DISCONNECT_GRACE_MS = 8000, CONNECT_TIMEOUT_MS = 25000;
  const REFRESH_MS = 4 * 60 * 60 * 1000, BACKOFF_MIN_MS = 3000, BACKOFF_MAX_MS = 45000, MAX_SCALE = 5;
  const CLIP_AFTER_END_MS = 15000, CLIP_MAX_MS = 3 * 60 * 1000;
  const TCP_ONLY = new URLSearchParams(window.location.search).get("camtcp") !== "0";

  /** Keep only the answer's TCP ICE candidates (when it has any). */
  function preferTcp(sdp) {
    const lines = sdp.split(/\r?\n/);
    const cand = (l) => l.startsWith("a=candidate:");
    const tcp = (l) => / tcp /i.test(l);
    if (!lines.some((l) => cand(l) && tcp(l))) return sdp;
    return lines.filter((l) => !cand(l) || tcp(l)).join("\r\n");
  }

  function fmtAgo(sec) {
    if (sec < 15) return "just now";
    if (sec < 60) return Math.floor(sec) + "s ago";
    const m = Math.floor(sec / 60);
    if (m < 60) return m + "m ago";
    const h = Math.floor(m / 60);
    if (h < 24) return h + "h " + (m % 60) + "m ago";
    return Math.floor(h / 24) + "d ago";
  }
  function evLabel(e) {
    if (e.type === "motion") return "Motion";
    const name = e.sub_label ? String(e.sub_label) : String(e.label || "event");
    const zone = e.zones && e.zones[0] ? " · " + String(e.zones[0]).replace(/_/g, " ") : "";
    return name.charAt(0).toUpperCase() + name.slice(1) + zone;
  }

  /** The live picture of `camera` in `viewport`; returns {stop()}. */
  function livePicture(viewport, camera, webrtcPath) {
    const video = viewport.querySelector("video");
    const loading = viewport.querySelector(".cw-loading");
    const badge = viewport.querySelector(".cw-live");
    const badgeText = viewport.querySelector(".cw-live-text");
    const frozen = viewport.querySelector(".cw-frozen");
    let pc = null, gen = 0, reconnectTimer = null, backoff = BACKOFF_MIN_MS, lastVideoTime = -1;
    let connectingSince = 0, startedAt = 0, lastFrameAt = Date.now(), state = "connecting", stopped = false;

    function setState(s) {
      state = s;
      badge.className = "cw-live " + (s === "live" ? "live" : s === "connecting" ? "connecting" : "frozen");
      badgeText.textContent = s === "live" ? "LIVE" : s === "connecting" ? "CONNECTING" : s === "frozen" ? "FROZEN" : "OFFLINE";
      frozen.classList.toggle("visible", s === "frozen" || s === "offline");
    }
    function alive() {
      lastFrameAt = Date.now();
      if (state !== "live") {
        loading.style.display = "none";
        video.style.display = "block";
        setState("live");
      }
      backoff = BACKOFF_MIN_MS;
    }
    if (typeof video.requestVideoFrameCallback === "function") {
      const loop = () => {
        if (stopped) return;
        if (video.readyState >= 2) alive();
        video.requestVideoFrameCallback(loop);
      };
      video.requestVideoFrameCallback(loop);
    }
    video.addEventListener("playing", () => { lastVideoTime = video.currentTime; alive(); });

    function scheduleReconnect(delay) {
      if (reconnectTimer || stopped) return;
      const wait = typeof delay === "number" ? delay : backoff;
      backoff = Math.min(BACKOFF_MAX_MS, backoff * 2);
      reconnectTimer = setTimeout(() => { reconnectTimer = null; connect(); }, wait);
    }

    function connect() {
      const mine = ++gen;
      if (pc) { try { pc.close(); } catch (_) { /* closed */ } pc = null; }
      if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
      video.pause();
      video.srcObject = null;   // the old stream's currentTime must not fake liveness
      video.style.display = "none";
      loading.style.display = "flex";
      loading.querySelector("span").textContent = "Connecting to camera…";
      connectingSince = startedAt = lastFrameAt = Date.now();
      lastVideoTime = -1;
      setState("connecting");
      const conn = new RTCPeerConnection({ iceServers: [{ urls: "stun:stun.l.google.com:19302" }] });
      pc = conn;
      conn.addTransceiver("video", { direction: "recvonly" });
      conn.ontrack = (event) => {
        if (mine !== gen) return;
        video.srcObject = event.streams[0];
        video.style.display = "block";
        loading.style.display = "none";
        video.play().catch(() => {});
      };
      conn.oniceconnectionstatechange = () => {
        if (mine !== gen) return;
        const s = conn.iceConnectionState;
        if (s === "failed" || s === "closed") { setState("offline"); scheduleReconnect(500); }
        else if (s === "disconnected") {
          setTimeout(() => {
            if (mine !== gen || !pc) return;
            const now = pc.iceConnectionState;
            if (now === "disconnected" || now === "failed" || now === "closed") { setState("offline"); scheduleReconnect(500); }
          }, DISCONNECT_GRACE_MS);
        }
      };
      conn.createOffer()
        .then((offer) => (mine !== gen ? null : conn.setLocalDescription(offer).then(() => conn.localDescription.sdp)))
        .then((sdp) => (sdp ? fetch(webrtcPath, { method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/sdp" }, body: sdp }) : null))
        .then((r) => {
          if (!r) return null;
          if (!r.ok) throw new Error(`WebRTC signaling failed: ${r.status}`);
          return r.text();
        })
        .then((answer) => {
          if (!answer || mine !== gen) return null;
          return conn.setRemoteDescription(new RTCSessionDescription({ type: "answer", sdp: TCP_ONLY ? preferTcp(answer) : answer }));
        })
        .catch((err) => {
          if (mine !== gen) return;
          console.error(`[camera ${camera}]`, err);
          setState("offline");
          loading.querySelector("span").textContent = "Camera unavailable — retrying…";
          scheduleReconnect();
        });
    }

    function watchdog() {
      const now = Date.now();
      if (video.srcObject && video.paused) video.play().catch(() => {});
      if (video.readyState >= 2 && !video.paused && video.currentTime !== lastVideoTime) {
        lastVideoTime = video.currentTime;
        alive();
      }
      if (document.hidden) return;
      if (startedAt && now - startedAt > REFRESH_MS && state === "live") return connect();
      if (state === "connecting") {
        if (connectingSince && now - connectingSince > CONNECT_TIMEOUT_MS) { setState("offline"); scheduleReconnect(); }
        return;
      }
      if (state !== "live") return;
      if (now - lastFrameAt > STALL_MS || video.error || video.readyState < 2) { setState("frozen"); scheduleReconnect(); }
    }
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      lastFrameAt = Date.now();
      lastVideoTime = video.currentTime;
      if (state === "live") video.play().catch(() => {});
      else if (state !== "connecting") { backoff = BACKOFF_MIN_MS; connect(); }
    };
    document.addEventListener("visibilitychange", onVisible);
    viewport.querySelector(".cw-retry").addEventListener("click", (e) => {
      e.stopPropagation();
      backoff = BACKOFF_MIN_MS;
      connect();
    });
    const timer = setInterval(watchdog, WATCHDOG_MS);

    // Zoom and pan: scale s and translation (tx, ty) in the video's own CSS px, around its centre.
    let scale = 1, tx = 0, ty = 0, gesture = null;
    const pointers = new Map();
    const frame = () => {
      const r = viewport.getBoundingClientRect();
      return { ratio: (r.width / viewport.offsetWidth) || 1, cx: r.left + r.width / 2, cy: r.top + r.height / 2,
        w: viewport.offsetWidth, h: viewport.offsetHeight };
    };
    function apply(animate) {
      scale = Math.min(MAX_SCALE, Math.max(1, scale));
      const f = frame();
      const mx = (scale - 1) * f.w / 2, my = (scale - 1) * f.h / 2;
      tx = Math.min(mx, Math.max(-mx, tx));
      ty = Math.min(my, Math.max(-my, ty));
      video.style.transition = animate ? "transform 0.15s ease-out" : "none";
      video.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
    }
    function zoomAt(next, sx, sy) {
      const f = frame();
      const mx = (sx - f.cx) / f.ratio, my = (sy - f.cy) / f.ratio;
      const px = (mx - tx) / scale, py = (my - ty) / scale;
      scale = Math.min(MAX_SCALE, Math.max(1, next));
      tx = mx - scale * px;
      ty = my - scale * py;
      apply(false);
    }
    function startGesture() {
      const pts = [...pointers.values()];
      const f = frame();
      if (pts.length >= 2) {
        const [a, b] = pts;
        const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
        gesture = { kind: "pinch", s0: scale, d0: Math.hypot(a.x - b.x, a.y - b.y) || 1,
          px: ((mx - f.cx) / f.ratio - tx) / scale, py: ((my - f.cy) / f.ratio - ty) / scale };
      } else if (pts.length === 1) {
        gesture = { kind: "drag", x0: pts[0].x, y0: pts[0].y, tx0: tx, ty0: ty };
      } else gesture = null;
    }
    viewport.addEventListener("pointerdown", (e) => {
      if ((e.pointerType === "mouse" && e.button !== 0) || e.target.closest("button")) return;
      viewport.setPointerCapture(e.pointerId);
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      startGesture();
    });
    viewport.addEventListener("pointermove", (e) => {
      if (!pointers.has(e.pointerId) || !gesture) return;
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const f = frame();
      if (gesture.kind === "pinch" && pointers.size >= 2) {
        const [a, b] = [...pointers.values()];
        const mx = ((a.x + b.x) / 2 - f.cx) / f.ratio, my = ((a.y + b.y) / 2 - f.cy) / f.ratio;
        scale = Math.min(MAX_SCALE, Math.max(1, gesture.s0 * Math.hypot(a.x - b.x, a.y - b.y) / gesture.d0));
        tx = mx - scale * gesture.px;
        ty = my - scale * gesture.py;
        apply(false);
      } else if (gesture.kind === "drag" && scale > 1) {
        tx = gesture.tx0 + (e.clientX - gesture.x0) / f.ratio;
        ty = gesture.ty0 + (e.clientY - gesture.y0) / f.ratio;
        apply(false);
      }
    });
    const endPointer = (e) => { if (pointers.delete(e.pointerId)) startGesture(); };
    viewport.addEventListener("pointerup", endPointer);
    viewport.addEventListener("pointercancel", endPointer);
    viewport.addEventListener("dblclick", () => { scale = 1; tx = 0; ty = 0; apply(true); });
    viewport.addEventListener("wheel", (e) => {
      e.preventDefault();
      zoomAt(scale * (e.deltaY > 0 ? 1 / 1.15 : 1.15), e.clientX, e.clientY);
    }, { passive: false });
    const resize = window.ResizeObserver ? new ResizeObserver(() => apply(false)) : null;
    if (resize) resize.observe(viewport);

    connect();
    return {
      stop() {
        stopped = true;
        gen++;
        clearInterval(timer);
        if (reconnectTimer) clearTimeout(reconnectTimer);
        if (pc) { try { pc.close(); } catch (_) { /* closed */ } }
        if (resize) resize.disconnect();
        document.removeEventListener("visibilitychange", onVisible);
        video.srcObject = null;
      },
    };
  }

  /** The events rail: cards kept by id so a GIF is fetched again only when its event changed. */
  function eventsRail(rail, api) {
    const list = rail.querySelector(".cw-events-list");
    const cards = new Map();
    let serverTime = 0, fetchedAt = 0;
    function ages() {
      if (!serverTime) return;
      const now = serverTime + (performance.now() - fetchedAt) / 1000;
      for (const c of cards.values()) {
        const live = !c.event.end_time;
        const age = Math.max(0, now - (live ? c.event.start_time : c.event.end_time));
        c.ago.textContent = live ? "● now" : fmtAgo(age);
        c.el.classList.toggle("ev-live", live);
        c.el.classList.toggle("ev-new", live || age < 90);
      }
    }
    const ageTimer = setInterval(ages, 10000);
    function card(e) {
      const el = document.createElement("div");
      el.className = "ev-card";
      el.innerHTML = `<div class="ev-media"><img decoding="async" alt=""></div><div class="ev-meta"><span class="ev-label"></span><span class="ev-ago"></span></div>`;
      const img = el.querySelector("img");
      img.addEventListener("error", () => el.classList.add("ev-err"));
      img.addEventListener("load", () => el.classList.remove("ev-err"));
      const c = { el, img, label: el.querySelector(".ev-label"), ago: el.querySelector(".ev-ago"), event: e, v: null };
      el.addEventListener("click", () => openClip(c));
      return c;
    }
    function show(data) {
      const events = (data && data.events) || [];
      if (data && data.server_time) { serverTime = data.server_time; fetchedAt = performance.now(); }
      rail.toggleAttribute("data-stale", Boolean(data && data.error));
      if (data && data.error) return;
      const seen = new Set();
      events.forEach((e, i) => {
        seen.add(e.id);
        let c = cards.get(e.id);
        if (!c) { c = card(e); cards.set(e.id, c); }
        c.event = e;
        c.label.textContent = evLabel(e);
        c.el.classList.toggle("ev-motion", e.type === "motion");
        const src = `${e.preview_url}?v=${encodeURIComponent(e.end_time ? String(e.end_time) : "live")}`;
        if (c.v !== src) { c.v = src; c.img.src = src; }
        if (list.children[i] !== c.el) list.insertBefore(c.el, list.children[i] || null);
      });
      for (const [id, c] of cards) if (!seen.has(id)) { c.el.remove(); cards.delete(id); }
      const empty = list.querySelector(".cw-events-empty");
      if (!events.length && !empty) list.insertAdjacentHTML("beforeend", `<div class="cw-events-empty">No recent events</div>`);
      else if (events.length && empty) empty.remove();
      ages();
    }
    // The clip of a tapped card, full-screen; it closes on a tap outside / ✕, a while after it ended, or after
    // CLIP_MAX_MS (a kiosk must come back on its own).
    let overlay = null, clipTimer = null;
    function closeClip() {
      clearTimeout(clipTimer);
      if (!overlay) return;
      const v = overlay.querySelector("video");
      v.pause();
      v.removeAttribute("src");
      v.load();   // release the decoder
      overlay.remove();
      overlay = null;
    }
    function openClip(c) {
      closeClip();
      overlay = document.createElement("div");
      overlay.className = "cw-clip";
      overlay.innerHTML = `<div class="cw-clip-box"><div class="cw-clip-head"><span></span><button title="Close">&times;</button></div>`
        + `<video controls playsinline muted preload="auto"></video></div>`;
      overlay.querySelector("span").textContent = `${c.label.textContent} · ${c.ago.textContent}`;
      const v = overlay.querySelector("video");
      v.src = c.event.clip_url;
      v.addEventListener("ended", () => { clearTimeout(clipTimer); clipTimer = setTimeout(closeClip, CLIP_AFTER_END_MS); });
      v.addEventListener("error", () => { overlay.querySelector("span").textContent = "Clip unavailable"; });
      overlay.querySelector("button").addEventListener("click", closeClip);
      overlay.addEventListener("click", (ev) => { if (ev.target === overlay) closeClip(); });
      document.body.appendChild(overlay);
      v.play().catch(() => {});
      clipTimer = setTimeout(closeClip, CLIP_MAX_MS);
    }
    return { show, stop() { clearInterval(ageTimer); closeClip(); } };
  }

  Dana.widget("cameras", {
    style: "widget.css",
    bare: true,
    mount(el, w, api) {
      const d = w.data || {};
      el.classList.add("cw-body");
      if (!d.camera) {
        el.innerHTML = `<div class="dw-note">${d.error === "bad_camera" ? "That camera name cannot be used."
          : "Choose a camera for this widget in the dashboard editor (Devices)."}</div>`;
        el._camera = null;
        return;
      }
      el._camera = d.camera;
      const rail = w.size === "large";
      el.innerHTML = `<div class="cw-viewport"><div class="cw-loading"><div class="spinner-sm"></div><span></span></div>`
        + `<video autoplay muted playsinline></video>`
        + `<div class="cw-overlay"><span class="cw-label"><i data-lucide="video"></i> ${api.esc(d.camera)}</span>`
        + `<span class="cw-live connecting"><span class="live-dot"></span><span class="cw-live-text">CONNECTING</span></span></div>`
        + `<div class="cw-frozen"><i data-lucide="triangle-alert"></i><span>Stream frozen — reconnecting…</span>`
        + `<button class="cw-retry">Retry now</button></div></div>`
        + (rail ? `<aside class="cw-events" aria-label="Recent camera events"><div class="cw-events-list"></div></aside>` : "");
      el._live = livePicture(el.querySelector(".cw-viewport"), d.camera, d.webrtc);
      el._rail = rail ? eventsRail(el.querySelector(".cw-events"), api) : null;
      if (el._rail) el._rail.show(d);
    },
    update(el, w, api) {
      const d = w.data || {};
      if ((d.camera || null) !== el._camera) {   // another camera: start over
        this.unmount(el);
        el.innerHTML = "";
        return this.mount(el, w, api);
      }
      if (el._rail) el._rail.show(d);
    },
    unmount(el) {
      if (el._live) el._live.stop();
      if (el._rail) el._rail.stop();
      el._live = el._rail = null;
    },
  });
})();
