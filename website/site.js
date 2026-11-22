// Dana website: the showreel (a day in scenes that advance on their own), scroll reveals, the code tabs.
(function () {
  "use strict";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---- header border once scrolled
  const top = document.querySelector(".top");
  const onScroll = () => top.classList.toggle("scrolled", scrollY > 8);
  addEventListener("scroll", onScroll, { passive: true });
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
})();
