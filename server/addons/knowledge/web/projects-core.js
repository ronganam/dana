/* Projects — the pure part of the Projects panel (projects.js is the controller; docs/knowledge.md).
 *
 * DanaProjectsCore: no DOM, no network. Turns the API's payloads (GET /api/projects, GET
 * /api/projects/{id}/items: {project, sections, entries, tasks}) into what the panel shows: values
 * formatted by column type, the best value of each compared column, the order options are shown in, a
 * section's state ("3 options · Chosen: X"), the date countdown, the budget, the fields a value edit
 * sends. Money is shown in the open project's currency (its owner's: `project.currency`, set with
 * setCurrency). Exercised by test_web_projects.mjs.
 */
(() => {
  "use strict";

  const DECISIONS = ["", "shortlist", "chosen", "rejected"];
  const DECISION_LABELS = { "": "Option", shortlist: "Shortlist", chosen: "Chosen", rejected: "Rejected" };
  const DECISION_LABELS_HE = { "": "אפשרות", shortlist: "רשימה קצרה", chosen: "נבחר", rejected: "נפסל" };
  const decisionLabel = (d, lang) => (lang === "he" ? DECISION_LABELS_HE : DECISION_LABELS)[d || ""];
  const DECISION_ORDER = { chosen: 0, shortlist: 1, "": 2, rejected: 3 };
  const TYPES = ["text", "money", "number", "phone", "url", "date", "checkbox", "rating", "select"];
  const TYPE_LABELS = { text: "Text", money: "Price ($)", number: "Number", phone: "Phone", url: "Link", date: "Date",
                        checkbox: "Yes / no", rating: "Rating", select: "Choice" };

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isRtl = (s) => /[֐-׿؀-ۿ]/.test(String(s || ""));
  const plural = (n, one, many) => `${n} ${n === 1 ? one : many || one + "s"}`;
  /** Hebrew count: "אפשרות אחת" / "3 אפשרויות" (one = the word with "אחת/אחד" already). */
  const heCount = (n, one, many) => (n === 1 ? one : `${n} ${many}`);

  /** A number from 4500, "4,500", "₪ 13,400", "13400 ש״ח"; null for a range or text (as the server's as_number). */
  function asNumber(v) {
    if (typeof v === "boolean" || v === null || v === undefined) return null;
    if (typeof v === "number") return Number.isFinite(v) ? v : null;
    const t = String(v).replace(/[\s,₪$€£]|ש["״]?ח|nis|ils/gi, "");
    return /^-?\d+(\.\d+)?$/.test(t) ? Number(t) : null;
  }

  /** "₪" for ILS, "$" for USD, "CHF " for a currency without a narrow symbol; "" for none. */
  function currencySymbol(code) {
    if (!/^[A-Z]{3}$/.test(code || "")) return "";
    try {
      const sym = new Intl.NumberFormat("en-US", { style: "currency", currency: code, currencyDisplay: "narrowSymbol" })
        .formatToParts(0).find((p) => p.type === "currency")?.value || code;
      return /^[A-Za-z]+$/.test(sym) ? `${sym} ` : sym;
    } catch (_) { return `${code} `; }
  }
  let symbol = currencySymbol("USD");
  /** The currency money values are shown in (an ISO 4217 code; the panel sets the open project's). */
  function setCurrency(code) {
    symbol = currencySymbol(code) || symbol;
    TYPE_LABELS.money = `Price (${symbol.trim()})`;
  }
  const money = (n) => `${symbol}${Math.round(n).toLocaleString("en-US")}`;
  const shortMoney = (n) => (Math.abs(n) >= 1000 ? `${symbol}${(Math.round(n / 100) / 10).toLocaleString("en-US")}k` : money(n));

  /** The field of `entry` named `name` (case-insensitive), or null. */
  function field(entry, name) {
    const key = String(name || "").toLowerCase();
    return (entry?.fields || []).find((f) => String(f?.name || "").toLowerCase() === key) || null;
  }
  const valueOf = (entry, col) => field(entry, col.name)?.value ?? null;
  const isEmpty = (v) => v === null || v === undefined || v === "";

  /** Israeli phone → {tel, wa} (wa: digits with 972, for wa.me), null when it holds no number. */
  function phoneLinks(v) {
    const digits = String(v || "").replace(/[^\d+]/g, "");
    if (digits.replace(/\D/g, "").length < 7) return null;
    let intl = digits.replace(/^\+/, "");
    if (intl.startsWith("0")) intl = "972" + intl.slice(1);
    return { tel: digits.startsWith("+") ? digits : String(v).replace(/[^\d+]/g, ""), wa: intl.replace(/\D/g, "") };
  }

  function safeUrl(v) {
    const s = String(v || "").trim();
    if (!s) return "";
    const u = /^[a-z][a-z0-9+.-]*:/i.test(s) ? s : `https://${s}`;
    return /^https?:\/\//i.test(u) ? u : "";
  }
  const host = (u) => { try { return new URL(u).host.replace(/^www\./, ""); } catch (_) { return u; } };

  /** A value as HTML for its column type (compact: for tables and cards). */
  function valueHtml(col, v, lang) {
    if (isEmpty(v)) return `<span class="prj-none">—</span>`;
    const type = col?.type || "text";
    if (type === "money" || type === "number") {
      const n = asNumber(v);
      if (n === null) return `<span dir="auto">${esc(v)}</span>`;
      return `<span class="prj-num">${type === "money" ? money(n) : n.toLocaleString("en-US")}</span>`;
    }
    if (type === "checkbox") return v === true ? `<span class="prj-yes">✓ ${lang === "he" ? "כן" : "Yes"}</span>` : `<span class="prj-no">✕ ${lang === "he" ? "לא" : "No"}</span>`;
    if (type === "rating") {
      const n = Math.max(0, Math.min(5, Math.round(asNumber(v) ?? 0)));
      return n ? `<span class="prj-stars" aria-label="${n} of 5">${"★".repeat(n)}<span class="prj-star-off">${"★".repeat(5 - n)}</span></span>` : `<span dir="auto">${esc(v)}</span>`;
    }
    if (type === "phone") {
      const p = phoneLinks(v);
      return p ? `<a class="prj-link" href="tel:${esc(p.tel)}" dir="ltr">${esc(v)}</a>` : `<span dir="auto">${esc(v)}</span>`;
    }
    if (type === "url") {
      const u = safeUrl(v);
      return u ? `<a class="prj-link" href="${esc(u)}" target="_blank" rel="noopener noreferrer" dir="ltr">${esc(host(u))}</a>` : esc(v);
    }
    if (type === "date") return `<span dir="ltr">${esc(formatDate(v))}</span>`;
    return `<span dir="auto">${esc(v)}</span>`;
  }

  function formatDate(v) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(v || ""));
    if (!m) return String(v || "");
    return `${Number(m[3])}.${Number(m[2])}.${m[1]}`;
  }

  /** Which way a column's best value lies: "low" / "high" / "" (money: low, rating: high, unless set). */
  function bestWay(col) {
    if (col?.best) return col.best;
    if (col?.type === "money") return "low";
    if (col?.type === "rating") return "high";
    return "";
  }

  /** colKey → Set of entry ids holding the best value of that column among `entries` (rejected left out;
   *  only when at least two options have a number and they are not all equal). */
  function bestIds(section, entries) {
    const out = {};
    const live = (entries || []).filter((e) => e.decision !== "rejected");
    for (const col of section?.columns || []) {
      const way = bestWay(col);
      if (!way) continue;
      const nums = live.map((e) => [e.id, asNumber(valueOf(e, col))]).filter(([, n]) => n !== null);
      if (nums.length < 2) continue;
      const pick = way === "low" ? Math.min(...nums.map(([, n]) => n)) : Math.max(...nums.map(([, n]) => n));
      if (nums.every(([, n]) => n === pick)) continue;
      out[col.key] = new Set(nums.filter(([, n]) => n === pick).map(([id]) => id));
    }
    return out;
  }

  /** Options in the order shown: chosen, shortlisted, the rest, rejected; then oldest first. */
  function orderOptions(entries) {
    return [...(entries || [])].sort((a, b) =>
      (DECISION_ORDER[a.decision || ""] ?? 2) - (DECISION_ORDER[b.decision || ""] ?? 2)
      || String(a.created_at || "").localeCompare(String(b.created_at || "")));
  }

  const priceColumn = (section) => (section?.columns || []).find((c) => c.role === "price")
    || (section?.columns || []).find((c) => c.type === "money") || null;

  /** What a section card says: {count, chosen: [entries], shortlisted, rejected, line, price: {min, max} | null}. */
  function sectionState(section, entries, lang) {
    const mine = (entries || []).filter((e) => e.section_id === section.id);
    const chosen = mine.filter((e) => e.decision === "chosen");
    const shortlisted = mine.filter((e) => e.decision === "shortlist").length;
    const rejected = mine.filter((e) => e.decision === "rejected").length;
    const col = priceColumn(section);
    const prices = col ? mine.filter((e) => e.decision !== "rejected").map((e) => asNumber(valueOf(e, col))).filter((n) => n !== null) : [];
    const price = prices.length ? { min: Math.min(...prices), max: Math.max(...prices) } : null;
    const he = lang === "he";
    const open = mine.length - rejected;
    let line;
    if (section.kind !== "options") line = mine.length ? (he ? heCount(mine.length, "פריט אחד", "פריטים") : plural(mine.length, "item")) : (he ? "עדיין ריק" : "Nothing yet");
    else if (!mine.length) line = he ? "אין עדיין אפשרויות" : "No options yet";
    else if (chosen.length) line = `${he ? "נבחר" : "Chosen"}: ${chosen.map((e) => e.title).join(", ")}`;
    else if (he) line = [heCount(open, "אפשרות אחת", "אפשרויות"), shortlisted ? `${shortlisted} ברשימה הקצרה` : "", "טרם הוחלט"].filter(Boolean).join(" · ");
    else line = [plural(open, "option"), shortlisted ? `${shortlisted} shortlisted` : "", "not decided"].filter(Boolean).join(" · ");
    return { count: mine.length, chosen, shortlisted, rejected, line, price, decided: chosen.length > 0 };
  }

  /** Days from `today` (a Date, local) to an ISO date: {days, label} or null. */
  function countdown(iso, today, lang) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ""));
    if (!m) return null;
    const now = today || new Date();
    const a = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
    const b = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    const days = Math.round((b - a) / 86400000);
    const label = lang === "he"
      ? (days === 0 ? "היום" : days === 1 ? "מחר" : days > 0 ? `בעוד ${days} ימים` : `לפני ${-days} ימים`)
      : (days === 0 ? "Today" : days === 1 ? "Tomorrow" : days > 0 ? `in ${plural(days, "day")}` : `${plural(-days, "day")} ago`);
    return { days, label, date: formatDate(iso) };
  }

  /** {budget, committed, left, pct} for a project with a budget, or null. `committed` from the list
   *  payload, or computed from the items payload when given. */
  function budgetInfo(project, sections, entries) {
    const budget = asNumber(project?.budget);
    let committed = asNumber(project?.committed) || 0;
    if (sections && entries) {
      committed = 0;
      for (const s of sections) {
        const col = (s.columns || []).find((c) => c.role === "price");
        if (!col) continue;
        for (const e of entries) if (e.section_id === s.id && e.decision === "chosen") committed += asNumber(valueOf(e, col)) || 0;
      }
    }
    if (!budget && !committed) return null;
    return { budget, committed, left: budget ? budget - committed : null, pct: budget ? Math.min(100, Math.round((committed / budget) * 100)) : null };
  }

  /** `fields` with the value of column `col` set to `value` (typed as the column; "" removes nothing,
   *  it stores an empty value). Other fields are kept in order. */
  function setField(fields, col, value) {
    const out = (fields || []).map((f) => ({ ...f }));
    let v = value;
    if (col.type === "money" || col.type === "number" || col.type === "rating") {
      const n = asNumber(value);
      v = n === null ? String(value ?? "").trim() || null : n;
    } else if (col.type === "checkbox") v = value === true || value === "true";
    else v = String(value ?? "").trim();
    const i = out.findIndex((f) => String(f.name || "").toLowerCase() === col.name.toLowerCase());
    if (i >= 0) out[i] = { name: out[i].name, type: col.type, value: v };
    else out.push({ name: col.name, type: col.type, value: v });
    return out;
  }

  /** Fields of an entry that are not columns of its section (shown as "More details"). */
  function extraFields(entry, section) {
    const names = new Set((section?.columns || []).map((c) => c.name.toLowerCase()));
    return (entry?.fields || []).filter((f) => f && f.name && !names.has(String(f.name).toLowerCase()));
  }

  /** The first phone among an entry's columns / fields → {value, tel, wa}. */
  function firstPhone(entry, section) {
    const cols = (section?.columns || []).filter((c) => c.type === "phone");
    for (const c of cols) { const v = valueOf(entry, c); const p = phoneLinks(v); if (p) return { value: v, ...p }; }
    for (const f of entry?.fields || []) {
      if (f.type === "phone" || /phone|טלפון|נייד/i.test(f.name || "")) { const p = phoneLinks(f.value); if (p) return { value: f.value, ...p }; }
    }
    return null;
  }

  const fileKind = (mime) => {
    const m = String(mime || "");
    if (m.startsWith("image/")) return "image";
    if (m.startsWith("audio/")) return "audio";
    if (m.startsWith("video/")) return "video";
    if (m === "application/pdf") return "pdf";
    return "file";
  };
  const FILE_ICONS = { image: "image", audio: "audio-lines", video: "film", pdf: "file-text", file: "paperclip" };
  function fileSize(n) {
    const b = Number(n) || 0;
    if (b >= 1024 * 1024) return `${(b / (1024 * 1024)).toFixed(1)} MB`;
    if (b >= 1024) return `${Math.round(b / 1024)} KB`;
    return `${b} B`;
  }

  /** A section's columns for the editor → what PUT sends (names trimmed, empties dropped, keys kept). */
  function columnsPayload(rows) {
    const seen = new Set();
    return (rows || []).map((r) => ({ ...r, name: String(r.name || "").trim() }))
      .filter((r) => r.name && !seen.has(r.name.toLowerCase()) && seen.add(r.name.toLowerCase()))
      .map((r) => {
        const out = { name: r.name, type: TYPES.includes(r.type) ? r.type : "text" };
        if (r.key) out.key = r.key;
        if (r.role === "price") out.role = "price";
        if (r.best) out.best = r.best;
        if (r.options) out.options = r.options;
        return out;
      });
  }

  /** Projects in the list's order: active first (newest first as the server sends), archived last. */
  const orderProjects = (list) => [...(list || [])].sort((a, b) => (a.status === "archived") - (b.status === "archived"));

  /** The text a markdown body renders from; the plain first line for previews. */
  function preview(body, n = 140) {
    const t = String(body || "").replace(/[#*_>`|-]+/g, " ").replace(/\s+/g, " ").trim();
    return t.length > n ? t.slice(0, n - 1) + "…" : t;
  }

  window.DanaProjectsCore = {
    DECISIONS, DECISION_LABELS, DECISION_LABELS_HE, decisionLabel, heCount, TYPES, TYPE_LABELS, FILE_ICONS,
    esc, isRtl, plural, asNumber, currencySymbol, setCurrency, money, shortMoney, field, valueOf, isEmpty, phoneLinks, safeUrl, host, valueHtml,
    formatDate, bestWay, bestIds, orderOptions, priceColumn, sectionState, countdown, budgetInfo, setField,
    extraFields, firstPhone, fileKind, fileSize, columnsPayload, orderProjects, preview,
  };
})();
