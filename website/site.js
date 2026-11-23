// Dana website: the showreel (a day in scenes that advance on their own), the live diagram of what Dana takes in
// and what comes out, the section nav with a progress line, scroll reveals, the code tabs.
(function () {
  "use strict";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- header: border once scrolled, the current section in the nav, the progress line
  const top = document.querySelector(".top");
  const progress = top.querySelector(".top-progress i");
  const here = top.querySelector(".top-here");
  const links = [...top.querySelectorAll(".top-nav a")];
  const targets = links.map((a) => document.querySelector(a.getAttribute("href")));
  let scrollQueued = false;
  function onScroll() {
    scrollQueued = false;
    top.classList.toggle("scrolled", scrollY > 8);
    const max = document.documentElement.scrollHeight - innerHeight;
    progress.style.transform = `scaleX(${max > 0 ? Math.min(1, scrollY / max) : 0})`;
    // the current section: the last one whose top passed 40% of the screen (all of those beside it too)
    const line = innerHeight * 0.4;
    const tops = targets.map((t) => t.getBoundingClientRect().top);
    let cur = -1;
    tops.forEach((y, i) => { if (y <= line) cur = i; });
    if (scrollY >= max - 2) cur = targets.length - 1;
    const on = tops.map((y, i) => cur >= 0 && Math.abs(y - tops[cur]) < 12);
    links.forEach((a, i) => { if (on[i]) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current"); });
    here.textContent = cur >= 0 ? links[on.indexOf(true)].textContent : "";
  }
  const queueScroll = () => { if (!scrollQueued) { scrollQueued = true; requestAnimationFrame(onScroll); } };
  addEventListener("scroll", queueScroll, { passive: true });
  addEventListener("resize", queueScroll);
  onScroll();

  // ---- reveal on scroll
  const reveals = document.querySelectorAll(".reveal");
  if ("IntersectionObserver" in window && !reduced) {
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); }
    }, { rootMargin: "0px 0px -8% 0px" });
    reveals.forEach((el, i) => { el.style.transitionDelay = (i % 4) * 60 + "ms"; io.observe(el); });
  } else {
    reveals.forEach((el) => el.classList.add("in"));
  }

  // ---- showreel
  const reel = document.querySelector(".reel");
  const scenes = [...reel.querySelectorAll(".scene")];
  const chapters = reel.querySelector(".chapters");
  const timeEl = reel.querySelector(".reel-time");
  const toggle = reel.querySelector(".reel-toggle");
  const DURATION = 7000;
  let index = 0, started = 0, elapsed = 0, raf = 0, paused = false, hovering = false, visible = false;

  const buttons = scenes.map((scene, i) => {
    const li = document.createElement("li");
    const b = document.createElement("button");
    b.type = "button";
    const label = scene.querySelector(".scene-kicker").textContent.split("·")[1].trim();
    b.innerHTML = `<span class="bar"><i></i></span>${scene.dataset.time}<span class="ch-label">${label}</span>`;
    b.setAttribute("aria-label", `${scene.dataset.time}, ${label}`);
    b.addEventListener("click", () => { show(i); elapsed = 0; started = performance.now(); });
    li.appendChild(b);
    chapters.appendChild(li);
    return b;
  });

  function countUp(scene) {
    scene.querySelectorAll(".count").forEach((el) => {
      const to = +el.dataset.to;
      if (reduced) { el.textContent = to.toLocaleString("en-US"); return; }
      const t0 = performance.now() + 500, dur = 1100;
      const step = (t) => {
        const p = Math.min(1, Math.max(0, (t - t0) / dur));
        el.textContent = Math.round(to * (1 - Math.pow(1 - p, 3))).toLocaleString("en-US");
        if (p < 1) requestAnimationFrame(step);
      };
      el.textContent = "0";
      requestAnimationFrame(step);
    });
  }

  function animateClock(from, to) {
    const m = (s) => { const [h, mi] = s.split(":").map(Number); return h * 60 + mi; };
    const a = m(from), b = m(to);
    if (reduced || a === b) { timeEl.textContent = to; return; }
    const t0 = performance.now(), dur = 700;
    const fmt = (v) => `${String(Math.floor(v / 60)).padStart(2, "0")}:${String(Math.floor(v % 60)).padStart(2, "0")}`;
    const step = (t) => {
      const p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3);
      timeEl.textContent = fmt(a + (b - a) * e);
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  function show(i) {
    const prev = scenes[index];
    index = (i + scenes.length) % scenes.length;
    const scene = scenes[index];
    scenes.forEach((s, k) => {
      const on = k === index;
      s.classList.toggle("is-active", on);
      s.setAttribute("aria-hidden", on ? "false" : "true");
    });
    buttons.forEach((b, k) => {
      b.parentElement.classList.toggle("done", k < index);
      if (k === index) b.setAttribute("aria-current", "step"); else b.removeAttribute("aria-current");
      b.querySelector(".bar i").style.transform = k < index ? "scaleX(1)" : "scaleX(0)";
    });
    reel.dataset.sky = scene.dataset.sky;
    animateClock(prev.dataset.time, scene.dataset.time);
    countUp(scene);
  }

  const running = () => !paused && !hovering && visible && !reduced;

  function tick(now) {
    if (running()) {
      elapsed += now - started;
      const bar = buttons[index].querySelector(".bar i");
      bar.style.transform = `scaleX(${Math.min(1, elapsed / DURATION)})`;
      if (elapsed >= DURATION) { elapsed = 0; show(index + 1); }
    }
    started = now;
    raf = requestAnimationFrame(tick);
  }

  toggle.addEventListener("click", () => {
    paused = !paused;
    toggle.setAttribute("aria-pressed", String(paused));
    toggle.setAttribute("aria-label", paused ? "Play the showreel" : "Pause the showreel");
  });
  reel.addEventListener("pointerenter", (e) => { if (e.pointerType === "mouse") hovering = true; });
  reel.addEventListener("pointerleave", () => { hovering = false; });
  reel.addEventListener("focusin", (e) => { if (e.target.matches(":focus-visible")) hovering = true; });
  reel.addEventListener("focusout", () => { hovering = false; });
  reel.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") { show(index + 1); elapsed = 0; }
    if (e.key === "ArrowLeft") { show(index - 1); elapsed = 0; }
  });

  if ("IntersectionObserver" in window) {
    new IntersectionObserver(([e]) => { visible = e.isIntersecting; }, { threshold: 0.35 }).observe(reel);
  } else visible = true;
  document.addEventListener("visibilitychange", () => { if (document.hidden) visible = false; });

  if (reduced) { paused = true; toggle.setAttribute("aria-pressed", "true"); toggle.setAttribute("aria-label", "Play the showreel"); }
  show(0);
  started = performance.now();
  raf = requestAnimationFrame(tick);

  // ---- code tabs
  const tabs = [...document.querySelectorAll(".code-tabs [role=tab]")];
  const select = (t) => tabs.forEach((x) => {
    const on = x === t;
    x.setAttribute("aria-selected", String(on));
    x.tabIndex = on ? 0 : -1;
    document.getElementById(x.getAttribute("aria-controls")).hidden = !on;
  });
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => select(t));
    t.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        const n = tabs[(i + (e.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length];
        select(n); n.focus();
      }
    });
  });

  // ---- the live diagram: made-up events enter at their input, light the steps they go through on the server,
  // and reach an output or are kept quiet. Pauses off screen and in a hidden tab; static under reduced motion.
  const dia = document.querySelector(".dia");
  if (dia) diagram(dia);

  function diagram(dia) {
    const NS = "http://www.w3.org/2000/svg";
    const svg = dia.querySelector(".dia-wires");
    const server = dia.querySelector(".dia-server");
    const bus = dia.querySelector(".dia-bus");
    const tray = dia.querySelector(".dia-tray");
    const trayChips = tray.querySelector(".tray-chips");
    const model = dia.querySelector(".dia-model");
    const layer = dia.querySelector(".dia-pills");
    const inList = dia.querySelector(".dia-in");
    const outList = dia.querySelector(".dia-out");
    const readEl = dia.querySelector("[data-read]"), toldEl = dia.querySelector("[data-told]"), foldEl = dia.querySelector("[data-folded]");
    const node = (k) => dia.querySelector(`.node[data-node="${k}"]`);
    const stepEl = (k) => dia.querySelector(`.dia-step[data-step="${k}"]`);
    const stacked = matchMedia("(max-width: 900px)");
    const HUE = { notif: "var(--teal)", call: "var(--sage)", signal: "var(--amber)", account: "var(--sky)", bank: "var(--rose)", you: "var(--plum)" };
    const MODEL_STEPS = { sort: 1, understand: 1, remember: 1 }; // the steps that ask a model; Decide is rules and triggers
    const STEPS = ["sort", "understand", "remember", "decide"];

    // Every event and outcome is something Dana does (website/README.md lists where each lives in the code).
    // stops: [step, outcome, needs-you?]; out: the output it reaches, said: what it shows there; quiet: the tray chip.
    const E = {
      promo:   { in: "notif", text: "Shop app · 30% off everything", stops: [["sort", "noise · folded"]], quiet: "Promo" },
      pickup:  { in: "notif", text: "WhatsApp · School parents: pickup moved to 13:00", stops: [["sort", "needs you", 1], ["decide", "urgent · alert until seen", 1]], out: "push", said: "Pickup moved to 13:00" },
      news:    { in: "notif", text: "News · 12 new stories", stops: [["sort", "noise · folded"]], quiet: "News" },
      sleep:   { in: "signal", text: "Sleep ended 06:51", stops: [["decide", "wake → briefing"]], out: "watch", said: "Good morning: standup 09:30" },
      parcel:  { in: "notif", text: "Delivery · Out for delivery", stops: [["sort", "can wait · inbox"]], quiet: "Delivery" },
      letter:  { in: "notif", text: "Gmail · The weekly newsletter", stops: [["sort", "noise · folded"]], quiet: "Newsletter" },
      dor:     { in: "call", text: "Call · Dor, 4 min", stops: [["understand", "event: Thu 09:00"]], out: "now", said: "Add “Dor comes” Thu 09:00?" },
      group:   { in: "notif", text: "Building group · 14 new messages", stops: [["sort", "noise · folded"]], quiet: "Group chat" },
      grocer:  { in: "bank", text: "Card · ₪23 at the supermarket", stops: [["understand", "everyday · as usual"]], quiet: "Everyday spend" },
      car:     { in: "signal", text: "Car connected", stops: [["decide", "reminder fires"]], out: "push", said: "Dry cleaning, on your way home" },
      game:    { in: "notif", text: "Game · Your lives are full", stops: [["sort", "noise · folded"]], quiet: "Game" },
      fact:    { in: "you", text: "“I'm vegetarian, by the way”", stops: [["remember", "a fact about you"]] },
      maya:    { in: "account", text: "Calendar · Maya in 10 min", stops: [["remember", "brief: what's open with her"]], out: "push", said: "Maya: what you last talked about" },
      cart:    { in: "notif", text: "Shop app · Your cart misses you", stops: [["sort", "noise · folded"]], quiet: "Promo" },
      work:    { in: "signal", text: "Arrived · Work", stops: [["decide", "nothing due here"]], quiet: "Arrived" },
      plan:    { in: "bank", text: "Phone plan · ₪38 more than last month", stops: [["understand", "price went up"]], out: "now", said: "Phone plan up ₪38 · compare?" },
      gal:     { in: "call", text: "Call · Gal, 2 min", stops: [["understand", "nothing to do"]], quiet: "Call" },
      milk:    { in: "you", text: "“We're out of milk and eggs”", stops: [["understand", "Shopping +2, shared with Gal"]], out: "wall", said: "Milk, eggs · Dairy" },
    };
    // the first round shows each kind once; after that, mostly noise, the way a real day is
    const FIRST = ["promo", "pickup", "news", "sleep", "parcel", "letter", "dor", "group", "grocer", "car", "game", "fact", "maya", "cart", "work", "plan", "gal", "milk"];
    const WEIGHTS = Object.entries(E).map(([k, e]) => [k, e.out ? 0.55 : e.stops[0][0] === "sort" ? 3 : 1]);
    const WSUM = WEIGHTS.reduce((a, [, w]) => a + w, 0);
    let order = 0, lastKey = "";
    function nextEvent() {
      if (order < FIRST.length) return (lastKey = FIRST[order++]);
      let k;
      do { let x = Math.random() * WSUM; k = WEIGHTS.find(([, w]) => (x -= w) < 0)[0]; } while (k === lastKey);
      return (lastKey = k);
    }

    // ---- geometry: where every port is, from the laid-out boxes
    let G = null;
    function measure() {
      const o = dia.getBoundingClientRect();
      const r = (el) => {
        const b = el.getBoundingClientRect();
        const l = b.left - o.left, t = b.top - o.top;
        return { l, t, r: l + b.width, b: t + b.height, cx: l + b.width / 2, cy: t + b.height / 2 };
      };
      const S = r(server), B = r(bus), T = r(tray), M = r(model), I = r(inList), O = r(outList);
      const g = { stack: stacked.matches, width: o.width, S, busY: B.cy, trayY: T.cy, M, nodes: {}, steps: {} };
      dia.querySelectorAll(".node").forEach((n) => { g.nodes[n.dataset.node] = r(n); });
      STEPS.forEach((k) => { g.steps[k] = r(stepEl(k)); });
      if (g.stack) {
        g.railL = S.l + 11; g.railR = S.r - 11;
        g.inY = I.b + 26; g.outY = O.t - 26;
        g.entry = [g.railL, g.busY]; g.exit = [g.railR, g.busY];
      } else {
        g.entry = [S.l, g.busY]; g.exit = [S.r, g.busY];
      }
      return g;
    }
    const f = (v) => Math.round(v * 10) / 10;
    const pt = (p) => `${f(p[0])},${f(p[1])}`;
    // a polyline with rounded corners
    function poly(pts, rad = 12) {
      let d = `M${pt(pts[0])}`;
      for (let i = 1; i < pts.length - 1; i++) {
        const [x0, y0] = pts[i - 1], [x1, y1] = pts[i], [x2, y2] = pts[i + 1];
        const d1 = Math.hypot(x1 - x0, y1 - y0) || 1, d2 = Math.hypot(x2 - x1, y2 - y1) || 1, k = Math.min(rad, d1 / 2, d2 / 2);
        d += ` L${pt([x1 + (x0 - x1) / d1 * k, y1 + (y0 - y1) / d1 * k])} Q${pt([x1, y1])} ${pt([x1 + (x2 - x1) / d2 * k, y1 + (y2 - y1) / d2 * k])}`;
      }
      return d + ` L${pt(pts[pts.length - 1])}`;
    }
    const curve = (a, b) => { const dx = (b[0] - a[0]) * 0.55; return `M${pt(a)} C${pt([a[0] + dx, a[1]])} ${pt([b[0] - dx, b[1]])} ${pt(b)}`; };
    const port = (k) => [G.steps[k].cx, G.busY];
    const inStart = (k) => { const n = G.nodes[k]; return G.stack ? [n.cx, n.cy] : [n.r, n.cy]; };
    const outEnd = (k) => { const n = G.nodes[k]; return G.stack ? [n.cx, n.cy] : [n.l, n.cy]; };
    function toServer(k, to) {
      const s = inStart(k);
      return G.stack ? poly([s, [s[0], G.inY], [G.railL, G.inY], G.entry, to]) : `${curve(s, G.entry)} L${pt(to)}`;
    }
    function toOutput(from, k) {
      const e = outEnd(k);
      return G.stack ? poly([from, G.exit, [G.railR, G.outY], [e[0], G.outY], e]) : `M${pt(from)} L${pt(G.exit)} ${curve(G.exit, e).replace(/^M[^C]+/, "")}`;
    }
    const modelWire = () => {
      const x = G.stack ? G.M.cx : G.S.cx;
      return `M${pt([x, G.S.b])} L${pt([x, G.M.t])}`;
    };

    function path(d, cls, hue) {
      const p = document.createElementNS(NS, "path");
      p.setAttribute("d", d);
      p.setAttribute("class", cls);
      if (hue) p.style.setProperty("--h", hue);
      svg.appendChild(p);
      return p;
    }

    function drawWires() {
      svg.textContent = "";
      const ins = ["notif", "call", "signal", "account", "bank", "you"], outs = ["now", "watch", "wall", "push"];
      if (G.stack) {
        ins.forEach((k) => { const n = G.nodes[k]; path(`M${pt([n.cx, n.cy])} L${pt([n.cx, G.inY])}`, "wire"); });
        const maxIn = Math.max(...ins.map((k) => G.nodes[k].cx));
        path(poly([[maxIn, G.inY], [G.railL, G.inY], G.entry]), "wire");
        const minOut = Math.min(...outs.map((k) => G.nodes[k].cx));
        path(poly([G.exit, [G.railR, G.outY], [minOut, G.outY]]), "wire");
        outs.forEach((k) => { const n = G.nodes[k]; path(`M${pt([n.cx, G.outY])} L${pt([n.cx, n.cy])}`, "wire"); });
      } else {
        ins.forEach((k) => path(curve(inStart(k), G.entry), "wire"));
        outs.forEach((k) => path(curve(G.exit, outEnd(k)), "wire"));
      }
      path(`M${pt(G.entry)} L${pt(G.exit)}`, "wire bus");
      STEPS.forEach((k) => { const s = G.steps[k]; path(`M${pt([s.cx, s.b])} L${pt([s.cx, G.busY])}`, "wire"); });
      path(modelWire(), "wire dash");
    }

    // ---- the parts that light up
    const timers = new Map();
    function later(key, ms, fn) { clearTimeout(timers.get(key)); timers.set(key, setTimeout(fn, ms)); }
    function light(k, text, needs, hue) {
      const el = stepEl(k), o = el.querySelector(".dia-step-o");
      el.style.setProperty("--h", needs ? "var(--ember)" : hue);
      el.classList.add("lit");
      o.textContent = text;
      o.classList.toggle("needs", !!needs);
      later(k, 1700, () => { el.classList.remove("lit"); o.textContent = o.dataset.idle; o.classList.remove("needs"); });
    }
    function ping(k, ms = 900) {
      const n = node(k);
      n.classList.add("ping");
      later("p" + k, ms, () => n.classList.remove("ping"));
    }
    function deliver(k, said) {
      const n = node(k), sm = n.querySelector("small");
      n.classList.add("got", "ping");
      sm.textContent = said;
      later("o" + k, 2800, () => { n.classList.remove("got", "ping"); sm.textContent = sm.dataset.idle; });
    }
    const counts = { read: 0, told: 0, quiet: 0 }, quietBy = new Map();
    function count(which) { counts[which]++; ({ read: readEl, told: toldEl, quiet: foldEl })[which].textContent = counts[which]; }
    function keepQuiet(label) {
      count("quiet");
      const n = (quietBy.get(label) || 0) + 1;
      quietBy.set(label, n);
      let chip = [...trayChips.children].find((c) => c.dataset.k === label);
      if (!chip) { chip = document.createElement("i"); chip.dataset.k = label; }
      chip.textContent = n > 1 ? `${label} ×${n}` : label;
      trayChips.prepend(chip);
      while (trayChips.children.length > 6) trayChips.lastChild.remove();
    }

    // ---- reduced motion: the diagram with four events drawn in place
    if (reduced) {
      const draw = () => {
        G = measure();
        drawWires();
        const shown = [["promo"], ["dor"], ["maya"], ["sleep"]];
        for (const [k] of shown) {
          const e = E[k], hue = HUE[e.in], [sk, text, needs] = e.stops[0];
          light(sk, text, needs, hue);
          clearTimeout(timers.get(sk));
          const end = e.out ? toOutput(port(sk), e.out) : `M${pt(port(sk))} L${pt([port(sk)[0], G.trayY])}`;
          path(toServer(e.in, port(sk)), "trail", hue);
          path(end, "trail", hue);
          const sm = node(e.in).querySelector("small");
          sm.textContent = e.text; node(e.in).classList.add("got");
          if (e.out) { const o = node(e.out).querySelector("small"); o.textContent = e.said; node(e.out).classList.add("got"); }
        }
        if (!trayChips.children.length) { ["Promo", "News", "Group chat", "Delivery"].forEach((l, i) => { for (let j = 0; j < [14, 12, 6, 4][i]; j++) keepQuiet(l); }); }
        readEl.textContent = 41; toldEl.textContent = 3;
      };
      dia.querySelectorAll("small[data-idle], .dia-step-o").forEach((s) => { s.dataset.idle = s.dataset.idle || s.textContent; });
      const redraw = () => requestAnimationFrame(draw);
      (document.fonts ? document.fonts.ready : Promise.resolve()).then(redraw);
      new ResizeObserver(redraw).observe(dia);
      return;
    }

    // ---- live: a clock that only runs while the diagram is on screen and the tab is visible
    let visible = false, clock = 0, last = 0, nextSpawn = 600, frame = 0;
    const live = [];
    const SPEED = () => (G.stack ? 260 : 340); // px per second
    const SPAWN_EVERY = 1500;

    function spawn() {
      const key = nextEvent(), e = E[key], hue = HUE[e.in];
      const el = document.createElement("div");
      el.className = "ev";
      el.style.setProperty("--h", hue);
      el.innerHTML = "<span></span>";
      el.firstChild.textContent = e.text;
      el.style.opacity = "0";
      layer.appendChild(el);
      const w = el.offsetWidth, h = el.offsetHeight;
      const segs = [];
      const move = (d, extra = {}) => {
        const p = path(d, "trail", hue), len = p.getTotalLength();
        p.style.strokeDasharray = `${len} ${len}`;
        p.style.strokeDashoffset = String(len);
        segs.push({ kind: "move", p, len, dur: Math.max(380, (len / SPEED()) * 1000), ...extra });
      };
      const wait = (dur, extra = {}) => segs.push({ kind: "wait", dur, ...extra });
      const first = e.stops[0][0];
      move(toServer(e.in, port(first)), { start: () => ping(e.in), end: () => count("read") });
      e.stops.forEach(([sk, text, needs], i) => {
        if (i > 0) move(`M${pt(port(e.stops[i - 1][0]))} L${pt(port(sk))}`);
        wait(i === e.stops.length - 1 && !e.out && !e.quiet ? 1300 : 850, {
          start: () => { light(sk, text, needs, hue); if (MODEL_STEPS[sk]) modelCall(); },
        });
      });
      const lastPort = port(e.stops[e.stops.length - 1][0]);
      if (e.out) move(toOutput(lastPort, e.out), { end: () => { deliver(e.out, e.said); count("told"); } });
      else if (e.quiet) move(`M${pt(lastPort)} L${pt([lastPort[0], G.trayY])}`, { shrink: true, end: () => keepQuiet(e.quiet) });
      live.push({ el, w, h, segs, i: 0, t: 0, trails: segs.filter((s) => s.p).map((s) => s.p), started: false });
    }

    function modelCall() {
      const p = path(modelWire(), "wire"), len = p.getTotalLength();
      p.style.opacity = "0";
      const dot = document.createElementNS(NS, "circle");
      dot.setAttribute("r", "3.5");
      dot.setAttribute("class", "pulse");
      svg.appendChild(dot);
      model.classList.add("ping");
      later("model", 900, () => model.classList.remove("ping"));
      live.push({ pulse: true, p, dot, len, t: 0, dur: 800 });
    }

    function place(m, x, y, scale, op) {
      x = Math.max(m.w / 2, Math.min(G.width - m.w / 2, x)); // a pill never leaves the diagram sideways
      m.el.style.transform = `translate(${f(x - m.w / 2)}px, ${f(y - m.h / 2)}px) scale(${scale})`;
      m.el.style.opacity = String(op);
    }

    function step(dt) {
      for (let j = live.length - 1; j >= 0; j--) {
        const m = live[j];
        if (m.pulse) {
          m.t += dt;
          const u = Math.min(1, m.t / m.dur), there = u < 0.5 ? u * 2 : 2 - u * 2;
          const q = m.p.getPointAtLength(there * m.len);
          m.dot.setAttribute("cx", q.x); m.dot.setAttribute("cy", q.y);
          if (u >= 1) { m.p.remove(); m.dot.remove(); live.splice(j, 1); }
          continue;
        }
        let s = m.segs[m.i];
        if (!m.started) { m.started = true; s.start && s.start(); }
        m.t += dt;
        while (s && m.t >= s.dur) {
          m.t -= s.dur;
          if (s.p) { s.p.style.strokeDashoffset = "0"; }
          s.end && s.end();
          m.i++;
          s = m.segs[m.i];
          if (s && s.start) s.start();
        }
        if (!s) {
          live.splice(j, 1);
          m.el.style.transition = "opacity .35s, transform .35s";
          m.el.style.opacity = "0";
          setTimeout(() => m.el.remove(), 400);
          m.trails.forEach((p) => { p.classList.add("gone"); setTimeout(() => p.remove(), 700); });
          continue;
        }
        const u = Math.min(1, m.t / s.dur);
        if (s.kind === "move") {
          const e = u < 0.5 ? 2 * u * u : 1 - Math.pow(-2 * u + 2, 2) / 2;
          const q = s.p.getPointAtLength(e * s.len);
          s.p.style.strokeDashoffset = String(s.len * (1 - e));
          const fadeIn = m.i === 0 ? Math.min(1, m.t / 200) : 1;
          place(m, q.x, q.y, s.shrink ? 1 - 0.4 * e : 1, s.shrink ? fadeIn * (1 - e * 0.85) : fadeIn);
          s.pos = [q.x, q.y];
        } else {
          const prev = m.segs[m.i - 1];
          if (prev && prev.pos) place(m, prev.pos[0], prev.pos[1], 1, 1);
        }
        // a trail fades once its pill has moved on
        m.segs.slice(0, m.i).forEach((x) => { if (x.p && !x.p.classList.contains("gone")) x.p.classList.add("gone"); });
      }
    }

    function tick(now) {
      const dt = Math.min(64, now - last);
      last = now;
      if (visible && G) {
        clock += dt;
        if (clock >= nextSpawn) { nextSpawn = clock + SPAWN_EVERY; spawn(); }
        step(dt);
      }
      frame = requestAnimationFrame(tick);
    }

    function relayout() {
      // the boxes moved: drop what is in flight (its paths are stale) and draw the wires again
      live.splice(0).forEach((m) => { if (m.pulse) { m.p.remove(); m.dot.remove(); } else { m.el.remove(); } });
      G = measure();
      drawWires();
    }
    dia.querySelectorAll("small[data-idle], .dia-step-o").forEach((s) => { s.dataset.idle = s.dataset.idle || s.textContent; });
    let lastSize = "";
    new ResizeObserver(() => {
      const b = dia.getBoundingClientRect(), size = `${Math.round(b.width)}x${Math.round(b.height)}`;
      if (size !== lastSize) { lastSize = size; relayout(); }
    }).observe(dia);
    if (document.fonts) document.fonts.ready.then(relayout);
    if ("IntersectionObserver" in window) {
      new IntersectionObserver(([e]) => { visible = e.isIntersecting && !document.hidden; }, { threshold: 0.2 }).observe(dia);
    } else visible = true;
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) visible = false;
      else { const b = dia.getBoundingClientRect(); visible = b.bottom > 0 && b.top < innerHeight; }
    });
    last = performance.now();
    frame = requestAnimationFrame(tick);
  }
})();
