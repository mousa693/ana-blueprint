/* ANA journey viewer — vanilla JS, no dependencies, works offline.
 *
 * Safety rule for this file: model/user text is NEVER assigned to innerHTML.
 * Every DOM node is built with h()/svg(), which only ever uses textContent /
 * createTextNode for content and setAttribute for (non-URL) attributes.
 */
(() => {
  "use strict";

  // ── DOM helpers ─────────────────────────────────────────────
  const $ = (sel) => document.querySelector(sel);
  const SVG_NS = "http://www.w3.org/2000/svg";

  function appendKids(el, kids) {
    for (const c of kids.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      el.appendChild(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }
  function setAttrs(el, attrs) {
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") el.setAttribute("class", v);
      else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : String(v));
    }
    return el;
  }
  function h(tag, attrs, ...kids) {
    return appendKids(setAttrs(document.createElement(tag), attrs), kids);
  }
  function svg(tag, attrs, ...kids) {
    return appendKids(setAttrs(document.createElementNS(SVG_NS, tag), attrs), kids);
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

  // ── data helpers (tolerant of missing / extra fields) ──────
  const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  const obj = (v) => (isObj(v) ? v : {});
  const arr = (v) => (Array.isArray(v) ? v : []);
  const str = (v) => (v === null || v === undefined ? "" : typeof v === "string" ? v : String(v));
  const num = (v, d = null) => (typeof v === "number" && Number.isFinite(v) ? v : d);
  const fmt = (v, digits = 2) => { const n = num(v); return n === null ? "—" : n.toFixed(digits); };
  const pct = (v) => { const n = num(v); return n === null ? "—" : `${Math.round(n * 100)}%`; };
  const humanize = (s) => str(s).replace(/_/g, " ");
  const cap = (s) => { s = str(s); return s ? s[0].toUpperCase() + s.slice(1) : s; };

  const STAGES = [
    { key: "call", label: "The call to adventure", match: "call to adventure" },
    { key: "threshold", label: "Crossing the threshold", match: "crossing the threshold" },
    { key: "mentor", label: "Meeting the mentor", match: "meeting the mentor" },
    { key: "ordeal", label: "The ordeal", match: "the ordeal" },
    { key: "reward", label: "Seizing the reward", match: "seizing the reward" },
    { key: "return", label: "The return", match: "the return" },
  ];

  /** Hero's-journey stage: explicit field if a future schema adds one, else the
   *  stage label embedded in the title, else derived from convergence the same
   *  way the generative engine picks it (marked `derived`). */
  function stageOf(r) {
    const explicit = [r.stage, r.journey_stage, obj(r.scene).stage, obj(r.scene).journey_stage]
      .find((v) => typeof v === "string" && v);
    if (explicit) {
      const e = explicit.toLowerCase();
      const i = STAGES.findIndex((s) => s.key === e || e.includes(s.match));
      return { index: i, label: i >= 0 ? STAGES[i].label : cap(explicit), derived: false };
    }
    const title = str(obj(r.scene).title).toLowerCase();
    let i = STAGES.findIndex((s) => title.includes(s.match));
    if (i >= 0) return { index: i, label: STAGES[i].label, derived: false };
    const c = num(obj(r.identity_before).convergence, num(r.convergence, 0));
    i = Math.floor(Math.max(0, Math.min(0.999, c)) * STAGES.length);
    return { index: i, label: STAGES[i].label, derived: true };
  }

  const worldOf = (r) => obj(obj(r.request).world);
  const tickOf = (r, i) => { const t = num(r.tick); return t === null ? i : t; };

  // ── state ───────────────────────────────────────────────────
  const state = {
    runs: [],
    run: null,          // metadata of the selected run
    records: [],
    es: null,           // EventSource for live streams
    selected: null,     // index of the highlighted cycle
    traitOrder: [],     // stable first-seen order => stable colors
  };
  const SERIES = ["--s1", "--s2", "--s3", "--s4", "--s5", "--s6", "--s7", "--s8"];
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  // ── status line ─────────────────────────────────────────────
  function status(msg, kind) {
    const el = clear($("#status"));
    if (msg) el.appendChild(h("span", { class: kind || "" }, msg));
  }

  // ── API ─────────────────────────────────────────────────────
  async function api(path, opts) {
    const res = await fetch(path, opts);
    let body = null;
    try { body = await res.json(); } catch (_) { /* non-JSON */ }
    if (!res.ok) {
      const d = body && body.detail;
      const msg = typeof d === "string" ? d : isObj(d) ? str(d.message) : `HTTP ${res.status}`;
      throw new Error(msg || `HTTP ${res.status}`);
    }
    return body;
  }

  async function refreshRuns(selectId) {
    const data = await api("/api/runs");
    state.runs = arr(data && data.runs);
    const sel = clear($("#run-select"));
    if (!state.runs.length) sel.appendChild(h("option", { value: "" }, "No runs loaded"));
    for (const r of state.runs) {
      const src = r.source === "live" ? (r.status === "running" ? "● live" : "live") : r.source;
      sel.appendChild(h("option", { value: r.id }, `${str(r.name)} — ${num(r.cycles, 0)} cycles · ${src}`));
    }
    const want = selectId || (state.run && state.run.id);
    if (want && state.runs.some((r) => r.id === want)) sel.value = want;
  }

  async function openRun(id) {
    stopStream();
    if (!id) return;
    try { history.replaceState(null, "", `#run=${encodeURIComponent(id)}`); } catch (_) { /* file: */ }
    const meta = state.runs.find((r) => r.id === id);
    if (meta && meta.status === "running") {
      startView(meta, []);
      attachStream(id);
      return;
    }
    status("Loading run…");
    try {
      const data = await api(`/api/runs/${encodeURIComponent(id)}`);
      startView(data, arr(data.records));
      const notes = [];
      if (num(data.skipped, 0)) notes.push(`${data.skipped} unreadable line(s) skipped`);
      if (num(data.partial, 0)) notes.push(`${data.partial} record(s) only partly matched the schema`);
      status(notes.length ? `Loaded ${str(data.name)}: ${notes.join("; ")}.` : "");
    } catch (e) {
      status(`Could not load run: ${e.message}`, "bad");
    }
  }

  function stopStream() {
    if (state.es) { state.es.close(); state.es = null; }
    $("#live-tail").hidden = true;
    $("#live-start").disabled = false;
  }

  function attachStream(id) {
    stopStream();
    const es = new EventSource(`/api/live/${encodeURIComponent(id)}/stream`);
    state.es = es;
    $("#live-tail").hidden = false;
    $("#live-start").disabled = true;
    status("Live journey running…");
    es.addEventListener("cycle", (ev) => {
      let rec;
      try { rec = JSON.parse(ev.data); } catch (_) { return; }
      if (!isObj(rec)) return;
      const idx = Number(ev.lastEventId);
      if (Number.isInteger(idx) && idx < state.records.length) return; // replayed duplicate
      addRecord(rec);
    });
    es.addEventListener("error", (ev) => {
      if (ev.data) {
        let msg = "";
        try { msg = str(JSON.parse(ev.data).error); } catch (_) { /* ignore */ }
        status(`Live run failed: ${msg}`, "bad");
      }
    });
    es.addEventListener("done", () => {
      stopStream();
      status(`Live journey complete — ${state.records.length} cycles.`, "ok");
      $("#live-start").disabled = false;
      refreshRuns(id).catch(() => {});
    });
  }

  // ── view lifecycle ──────────────────────────────────────────
  function startView(meta, records) {
    state.run = meta;
    state.records = [];
    state.selected = null;
    state.traitOrder = [];
    clear($("#timeline"));
    $("#empty").hidden = true;
    $("#run-view").hidden = false;
    for (const r of records) addRecord(r, { quiet: true });
    renderSummary();
    renderCharts();
  }

  let pending = 0;
  function scheduleRender() {
    if (pending) return;
    pending = requestAnimationFrame(() => { pending = 0; renderSummary(); renderCharts(); });
  }

  function addRecord(rec, { quiet = false } = {}) {
    const i = state.records.length;
    state.records.push(rec);
    for (const t of arr(obj(rec.identity_before).traits)) {
      const n = str(obj(t).name);
      if (n && !state.traitOrder.includes(n)) state.traitOrder.push(n);
    }
    const card = renderCard(rec, i);
    if (!quiet) card.classList.add("enter");
    $("#timeline").appendChild(card);
    if (!quiet) {
      scheduleRender();
      if (window.scrollY + window.innerHeight >= document.body.scrollHeight - 400) {
        card.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    }
  }

  function selectCycle(i, scroll = true) {
    state.selected = i;
    renderCharts();
    const card = document.getElementById(`cycle-${i}`);
    if (!card) return;
    if (scroll) card.scrollIntoView({ block: "start", behavior: "smooth" });
    card.classList.add("flash");
    setTimeout(() => card.classList.remove("flash"), 1400);
  }

  // ── summary tiles ───────────────────────────────────────────
  function tile(label, value, note, textual) {
    return h("div", { class: "tile" },
      h("div", { class: "tile-label" }, label),
      h("div", { class: `tile-value${textual ? " text" : ""}` }, value),
      note ? h("div", { class: "tile-note" }, note) : null);
  }

  function renderSummary() {
    const recs = state.records;
    const el = clear($("#tiles"));
    const meta = $("#run-meta");
    clear(meta);
    if (!recs.length) {
      el.appendChild(tile("Cycles", "0", state.run && state.run.status === "running" ? "waiting for the first cycle" : ""));
      return;
    }
    const first = recs[0], last = recs[recs.length - 1];
    const c0 = num(first.convergence, 0), c1 = num(last.convergence, 0);
    const syncs = recs.filter((r) => arr(obj(r.scene).beats).some((b) => obj(b).kind === "synchronicity")).length;
    const visions = recs.filter((r) => obj(r.scene).is_vision).length;
    const flags = recs.filter((r) => obj(r.alignment).blocked || arr(obj(r.harmonizer).continuity_errors).length).length;
    const st = stageOf(last);
    el.append(
      tile("Cycles", String(recs.length), state.run && state.run.status === "running" ? "live · streaming" : str(state.run && state.run.source)),
      tile("Convergence", fmt(c1), `${c1 >= c0 ? "+" : ""}${(c1 - c0).toFixed(2)} since cycle ${tickOf(first, 0)}`),
      tile("Stage", st.label, `${st.derived ? "≈ derived · " : ""}${humanize(worldOf(last).location) || "unknown place"}`, true),
      tile("Synchronicities", String(syncs), `${visions} vision${visions === 1 ? "" : "s"}`),
      tile("Flags", String(flags), flags ? "blocks or continuity errors" : "no blocks or continuity errors"),
    );
    meta.append(`${str(state.run && state.run.name)} · ${recs.length} cycle${recs.length === 1 ? "" : "s"}`);
  }

  // ── charts ──────────────────────────────────────────────────
  const tooltip = $("#tooltip");
  function showTip(x, y, head, rows, hint) {
    clear(tooltip);
    tooltip.append(h("div", { class: "tt-head" }, head));
    for (const [name, color, value] of rows) {
      const sw = h("i");
      sw.style.color = color;
      tooltip.append(h("div", { class: "tt-row" }, h("span", null, sw, name), h("b", null, value)));
    }
    if (hint) tooltip.append(h("div", { class: "tt-hint" }, hint));
    tooltip.hidden = false;
    const r = tooltip.getBoundingClientRect();
    let left = x + 14, top = y + 14;
    if (left + r.width > window.innerWidth - 8) left = x - r.width - 14;
    if (top + r.height > window.innerHeight - 8) top = y - r.height - 14;
    tooltip.style.left = `${Math.max(8, left)}px`;
    tooltip.style.top = `${Math.max(8, top)}px`;
  }
  function hideTip() { tooltip.hidden = true; }

  /**
   * Single-axis line chart. series: [{name, color, values:[number|null], dashed, area}]
   */
  function lineChart(container, opts) {
    clear(container);
    const { series, compact = false, yMin = 0, zero = null, valueFmt = (v) => fmt(v), endLabels = false } = opts;
    const n = state.records.length;
    const W = Math.max(120, container.clientWidth || 300);
    const H = Math.max(50, container.clientHeight || 120);
    const all = series.flatMap((s) => s.values).filter((v) => num(v) !== null);
    const yMax = opts.yMax !== undefined ? Math.max(opts.yMax, ...all) : Math.max(1, ...all);
    const m = compact
      ? { l: 26, r: 6, t: 6, b: 16 }
      : { l: 32, r: endLabels ? 96 : 12, t: 10, b: 22 };
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const x = (i) => m.l + (n <= 1 ? iw / 2 : (i * iw) / (n - 1));
    const y = (v) => m.t + ih - ((v - yMin) / (yMax - yMin || 1)) * ih;

    const root = svg("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img",
      "aria-label": opts.label || series.map((s) => s.name).join(", ") });

    // selected-cycle band
    if (state.selected !== null && state.selected < n) {
      const bw = n <= 1 ? 16 : Math.max(8, iw / (n - 1) * 0.6);
      root.append(svg("rect", { class: "sel", x: x(state.selected) - bw / 2, y: m.t, width: bw, height: ih, rx: 3 }));
    }

    // grid + y labels
    const ticks = compact ? [yMin, yMax] : [yMin, yMin + (yMax - yMin) / 2, yMax];
    const g = svg("g", { class: "grid" });
    const ax = svg("g", { class: "axis" });
    for (const t of ticks) {
      g.append(svg("line", { x1: m.l, x2: W - m.r, y1: y(t), y2: y(t) }));
      ax.append(svg("text", { x: m.l - 6, y: y(t) + 3.5, "text-anchor": "end" }, Number(t.toFixed(2)).toString()));
    }
    if (zero !== null) g.append(svg("line", { class: "zero", x1: m.l, x2: W - m.r, y1: y(zero), y2: y(zero) }));
    // x labels (cycle ticks), thinned
    const maxLabels = Math.max(2, Math.floor(iw / (compact ? 34 : 40)));
    const step = Math.max(1, Math.ceil(n / maxLabels));
    for (let i = 0; i < n; i += step) {
      ax.append(svg("text", { x: x(i), y: H - (compact ? 3 : 6), "text-anchor": "middle" }, String(tickOf(state.records[i], i))));
    }
    root.append(g, ax);

    // series
    for (const s of series) {
      const pts = s.values.map((v, i) => (num(v) === null ? null : [x(i), y(v)]));
      let d = "", started = false;
      for (const p of pts) {
        if (!p) { started = false; continue; }
        d += `${started ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`;
        started = true;
      }
      if (!d) continue;
      if (s.area) {
        const valid = pts.filter(Boolean);
        const ad = `${d}L${valid[valid.length - 1][0].toFixed(1)},${y(Math.max(yMin, zero ?? yMin)).toFixed(1)}L${valid[0][0].toFixed(1)},${y(Math.max(yMin, zero ?? yMin)).toFixed(1)}Z`;
        const area = svg("path", { class: "area", d: ad });
        area.style.fill = s.color;
        root.append(area);
      }
      const path = svg("path", { class: `series${s.dashed ? " dashed" : ""}`, d });
      path.style.stroke = s.color;
      root.append(path);
      const lastP = [...pts].reverse().find(Boolean);
      if (lastP) {
        const dot = svg("circle", { class: "dot", cx: lastP[0], cy: lastP[1], r: compact ? 3 : 3.5 });
        dot.style.fill = s.color;
        root.append(dot);
      }
    }

    // direct end labels (de-collided)
    if (endLabels && n) {
      const labs = series.map((s) => {
        const vals = s.values.map((v) => num(v));
        const lv = [...vals].reverse().find((v) => v !== null);
        return lv === undefined ? null : { s, y: y(lv), v: lv };
      }).filter(Boolean).sort((a, b) => a.y - b.y);
      for (let i = 1; i < labs.length; i++) if (labs[i].y - labs[i - 1].y < 12) labs[i].y = labs[i - 1].y + 12;
      for (const l of labs) {
        root.append(svg("text", { class: "end-lbl", x: W - m.r + 8, y: l.y + 3.5 }, `${l.s.name} ${valueFmt(l.v)}`));
      }
    }

    // hover layer
    const cross = svg("line", { class: "cross", y1: m.t, y2: m.t + ih, visibility: "hidden" });
    const hoverDots = svg("g");
    root.append(cross, hoverDots);
    const hit = svg("rect", { class: "hit", x: m.l - 8, y: 0, width: iw + 16, height: H });
    root.append(hit);
    const idxAt = (evt) => {
      const r = root.getBoundingClientRect();
      const px = evt.clientX - r.left;
      if (n <= 1) return 0;
      return Math.max(0, Math.min(n - 1, Math.round(((px - m.l) / iw) * (n - 1))));
    };
    const onMove = (evt) => {
      if (!n) return;
      const i = idxAt(evt);
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i));
      cross.setAttribute("visibility", "visible");
      clear(hoverDots);
      const rows = [];
      for (const s of series) {
        const v = num(s.values[i]);
        if (v === null) continue;
        const c = svg("circle", { class: "dot", cx: x(i), cy: y(v), r: 4.5 });
        c.style.fill = s.color;
        hoverDots.append(c);
        rows.push([s.name, s.color, valueFmt(v)]);
      }
      const rec = state.records[i];
      showTip(evt.clientX, evt.clientY, `Cycle ${tickOf(rec, i)} · ${str(obj(rec.scene).title) || stageOf(rec).label}`, rows, "click to open this cycle");
    };
    hit.addEventListener("pointermove", onMove);
    hit.addEventListener("pointerdown", onMove);
    hit.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); clear(hoverDots); hideTip(); });
    hit.addEventListener("click", (evt) => { if (n) { hideTip(); selectCycle(idxAt(evt)); } });
    container.append(root);
  }

  function legend(el, series) {
    clear(el);
    for (const s of series) {
      const sw = h("i", { class: s.dashed ? "dashed" : "" });
      sw.style.color = s.color;
      el.append(h("span", null, sw, s.name));
    }
  }

  function smallMultiple(id, values, color, opts = {}) {
    const el = document.getElementById(id);
    lineChart(el, { series: [{ name: opts.name, color, values, area: true }], compact: true, ...opts });
    const cap = el.parentElement.querySelector("figcaption");
    if (cap) {
      const last = [...values].reverse().find((v) => num(v) !== null);
      let b = cap.querySelector("b");
      if (!b) { b = h("b"); cap.append(b); }
      b.textContent = last === undefined ? "—" : (opts.valueFmt || fmt)(last);
    }
  }

  function renderCharts() {
    if ($("#run-view").hidden) return;
    const R = state.records;
    const conv = [
      { name: "convergence", color: css("--s1"), values: R.map((r) => num(r.convergence)) },
      { name: "Ω surprise", color: css("--s2"), values: R.map((r) => num(r.omega_surprise)), dashed: true },
      { name: "α surprise", color: css("--s3"), values: R.map((r) => num(r.alpha_surprise)), dashed: true },
    ];
    lineChart($("#chart-convergence"), { series: conv, yMax: 1, endLabels: true, label: "Convergence and surprise per cycle" });
    legend($("#legend-convergence"), conv);

    const al = (k) => R.map((r) => num(obj(r.alignment)[k]));
    smallMultiple("chart-reflection", al("reflection_pct"), css("--s1"), { name: "reflection", yMax: 1, valueFmt: pct });
    smallMultiple("chart-stress", al("stress"), css("--s2"), { name: "stress", yMax: 1 });
    smallMultiple("chart-intensity", al("recommended_intensity"), css("--s3"), { name: "intensity", yMax: 1 });

    // traits
    const traitSeries = state.traitOrder.map((name, k) => ({
      name: humanize(name),
      key: name,
      color: css(SERIES[k % SERIES.length]),
      dashed: k >= SERIES.length,
      values: R.map((r) => {
        const t = arr(obj(r.identity_before).traits).find((t) => obj(t).name === name);
        return t ? num(t.value) : null;
      }),
    }));
    lineChart($("#chart-traits"), { series: traitSeries, yMax: 1, label: "Trait values per cycle" });
    legend($("#legend-traits"), traitSeries);
    renderTraitTable(traitSeries);

    const em = (k) => R.map((r) => num(obj(obj(r.identity_before).emotion)[k]));
    smallMultiple("chart-valence", em("valence"), css("--s7"), { name: "valence", yMin: -1, yMax: 1, zero: 0 });
    smallMultiple("chart-estress", em("stress"), css("--s2"), { name: "stress", yMax: 1 });
    renderThemeGrid();
  }

  function renderTraitTable(series) {
    const t = clear($("#trait-table"));
    if (!series.length) return;
    t.append(h("thead", null, h("tr", null, h("th", null, "Trait"), h("th", null, "Trend"),
      h("th", { class: "num" }, "Now"), h("th", { class: "num" }, "Δ"))));
    const body = h("tbody");
    for (const s of series) {
      const vals = s.values.filter((v) => v !== null);
      const first = vals[0], last = vals[vals.length - 1];
      const d = vals.length ? last - first : null;
      const W = 90, H = 22;
      let path = "";
      const n = s.values.length;
      s.values.forEach((v, i) => {
        if (v === null) return;
        const px = n <= 1 ? W / 2 : 2 + (i * (W - 4)) / (n - 1);
        const py = H - 2 - Math.max(0, Math.min(1, v)) * (H - 4);
        path += `${path ? "L" : "M"}${px.toFixed(1)},${py.toFixed(1)}`;
      });
      const p = svg("path", { d: path || "M0,0" });
      p.style.stroke = s.color;
      const sw = h("span", { class: "swatch" });
      sw.style.background = s.color;
      const cls = d === null || Math.abs(d) < 0.005 ? "delta-flat" : d > 0 ? "delta-up" : "delta-down";
      body.append(h("tr", null,
        h("td", null, h("span", { class: "tname" }, sw, s.name)),
        h("td", { class: "spark" }, svg("svg", { viewBox: `0 0 ${W} ${H}`, "aria-hidden": "true" }, p)),
        h("td", { class: "num" }, fmt(last)),
        h("td", { class: `num ${cls}` }, d === null ? "—" : `${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(2)}`)));
    }
    t.append(body);
  }

  function renderThemeGrid() {
    const el = clear($("#theme-grid"));
    const R = state.records;
    const themes = [];
    for (const r of R) for (const t of arr(obj(r.identity_before).active_themes)) {
      const s = str(t);
      if (s && !themes.includes(s)) themes.push(s);
    }
    if (!themes.length) { el.append(h("span", { class: "panel-sub" }, "no active themes recorded")); return; }
    el.style.gridTemplateColumns = `minmax(70px, max-content) repeat(${R.length}, minmax(4px, 1fr))`;
    for (const th of themes) {
      el.append(h("span", { class: "tg-name", title: th }, th));
      R.forEach((r, i) => {
        const on = arr(obj(r.identity_before).active_themes).includes(th);
        el.append(h("span", {
          class: `tg-cell${on ? " on" : ""}`,
          title: `cycle ${tickOf(r, i)}: ${th} ${on ? "active" : "inactive"}`,
          onclick: () => selectCycle(i),
        }));
      });
    }
  }

  // ── cycle cards ─────────────────────────────────────────────
  function renderBeat(b) {
    b = obj(b);
    const kind = str(b.kind) || "description";
    const text = str(b.text);
    const speaker = str(b.speaker);
    const sym = b.symbol ? h("span", { class: "symbol" }, `· ${str(b.symbol)}`) : null;
    const arch = b.archetype ? h("span", { class: "arch" }, humanize(b.archetype)) : null;
    if (kind === "dialogue" && /grounding voice/i.test(speaker)) {
      return h("li", { class: "beat beat-ground" }, h("span", { class: "speaker" }, "Grounding voice"), text);
    }
    if (kind === "dialogue") {
      return h("li", { class: "beat beat-dialogue" },
        speaker ? h("span", { class: "speaker" }, speaker, arch) : null, text);
    }
    if (kind === "synchronicity") {
      return h("li", { class: "beat beat-synchronicity" },
        h("span", { class: "speaker" }, "✦ Synchronicity", sym), text);
    }
    if (kind === "event") {
      return h("li", { class: "beat beat-event" }, h("span", { class: "mark", "aria-label": "event" }, "⚡"),
        h("span", null, speaker ? `${speaker}: ` : "", text, sym));
    }
    if (kind === "choice") {
      return h("li", { class: "beat beat-choice" }, "? ", text);
    }
    return h("li", { class: `beat beat-description` }, speaker ? `${speaker}: ` : "", text);
  }

  function list(items, emptyText) {
    const xs = arr(items).map(str).filter(Boolean);
    if (!xs.length) return h("p", { class: "none" }, emptyText);
    return h("ul", null, xs.map((x) => h("li", null, x)));
  }

  function renderCard(r, i) {
    const scene = obj(r.scene), out = obj(r.outcome), al = obj(r.alignment), hv = obj(r.harmonizer);
    const world = worldOf(r);
    const st = stageOf(r);
    const beats = arr(scene.beats);
    const hasSync = beats.some((b) => obj(b).kind === "synchronicity");
    const contErr = arr(hv.continuity_errors);
    const act = obj(out.user_action);
    const actRaw = str(act.raw);

    const top = h("div", { class: "card-top" },
      h("span", { class: "cycle-no" }, `Cycle ${tickOf(r, i)}`),
      h("span", { class: "chip stage", title: st.derived ? "derived from convergence" : "" }, `${st.derived ? "≈ " : ""}${st.label}`),
      scene.is_vision ? h("span", { class: "chip vision" }, "◈ Vision") : null,
      hasSync ? h("span", { class: "chip sync" }, "✦ Synchronicity") : null,
      al.blocked ? h("span", { class: "chip bad" }, "⛔ Blocked") : null,
      contErr.length ? h("span", { class: "chip bad" }, `⚠ ${contErr.length} continuity`) : null,
      h("span", { class: "spacer" }),
      scene.source ? h("span", { class: "chip llm" }, str(scene.source)) : null,
    );

    const arc = h("div", { class: "arc", "aria-hidden": "true" },
      STAGES.map((_, k) => h("span", { class: k < st.index ? "past" : k === st.index ? "now" : "" })));

    const whereParts = [humanize(world.location), str(world.time_of_day), str(world.weather)].filter(Boolean);
    const where = h("p", { class: "where" });
    whereParts.forEach((p, k) => { if (k) where.append(h("span", { class: "sep" }, "·")); where.append(p); });
    if (num(scene.intensity) !== null) {
      where.append(h("span", { class: "sep" }, "·"), `intensity ${fmt(scene.intensity)}`);
    }

    const choices = arr(scene.choices).map(str);
    const taken = num(act.choice_index);
    const choiceList = choices.length ? h("ul", { class: "choices", "aria-label": "Choices offered" },
      choices.map((c, k) => h("li", { class: (taken === k || (taken === null && c === actRaw)) ? "taken" : "" }, c))) : null;

    const traitMoves = Object.entries(obj(out.trait_updates)).filter(([, v]) => num(v) !== null);

    const alignSummary = h("summary", null, "Alignment & harmonizer",
      h("span", { class: al.blocked ? "chip bad" : "chip" }, al.blocked ? "blocked" : (al.in_safe_band === false ? "outside safe band" : "in safe band")),
      hv.approved === false ? h("span", { class: "chip bad" }, "not approved") : null,
      arr(al.nudges).length ? h("span", { class: "chip" }, `${arr(al.nudges).length} nudge${arr(al.nudges).length === 1 ? "" : "s"}`) : null);

    const alignBody = h("div", { class: "align-body" },
      h("div", null, h("p", { class: "label" }, "Self-alignment"),
        h("dl", { class: "kv" },
          h("dt", null, "reflection"), h("dd", null, pct(al.reflection_pct)),
          h("dt", null, "adaptation"), h("dd", null, fmt(al.adaptation_level)),
          h("dt", null, "stress"), h("dd", null, fmt(al.stress)),
          h("dt", null, "next intensity"), h("dd", null, fmt(al.recommended_intensity)),
          h("dt", null, "safe band"), h("dd", { class: al.in_safe_band === false ? "bad-txt" : "ok-txt" }, al.in_safe_band === false ? "✕ outside" : "✓ inside"),
          h("dt", null, "blocked"), h("dd", { class: al.blocked ? "bad-txt" : "ok-txt" }, al.blocked ? `⛔ yes${al.block_reason ? ` — ${str(al.block_reason)}` : ""}` : "✓ no"))),
      h("div", null, h("p", { class: "label" }, "Nudges for next cycle"), list(al.nudges, "none")),
      h("div", null, h("p", { class: "label" }, "Harmonizer"),
        h("dl", { class: "kv" },
          h("dt", null, "approved"), h("dd", { class: hv.approved === false ? "bad-txt" : "ok-txt" }, hv.approved === false ? "✕ no" : "✓ yes"),
          h("dt", null, "vision"), h("dd", null, hv.is_vision ? "yes" : "no"),
          hv.grounding_note ? [h("dt", null, "grounding"), h("dd", null, str(hv.grounding_note))] : null)),
      h("div", null, h("p", { class: "label" }, "Continuity errors"), list(contErr, "none")),
      h("div", null, h("p", { class: "label" }, "Corrections"), list(hv.corrections, "none")),
      arr(obj(r.request).constraints).length ? h("div", null, h("p", { class: "label" }, "Constraints in request"), list(obj(r.request).constraints, "none")) : null,
    );
    const details = h("details", { class: "align" }, alignSummary, alignBody);
    if (al.blocked || contErr.length) details.open = true;

    return h("li", { class: `card${scene.is_vision ? " is-vision" : ""}`, id: `cycle-${i}` },
      top, arc,
      h("h3", null, str(scene.title) || cap(str(scene.theme)) || `Cycle ${tickOf(r, i)}`),
      where,
      scene.summary ? h("p", { class: "summary" }, str(scene.summary)) : null,
      beats.length ? h("ol", { class: "beats" }, beats.map(renderBeat)) : null,
      choiceList,
      actRaw ? h("div", { class: "act" }, h("span", { class: "arrow", "aria-hidden": "true" }, "➤"),
        h("div", null, h("p", { class: "label" }, "Action taken"), h("p", null, actRaw))) : null,
      arr(out.npc_events).length ? [h("p", { class: "label" }, "NPC reactions"),
        h("ul", { class: "reactions" }, arr(out.npc_events).map((e) => h("li", null, str(e))))] : null,
      out.lesson ? h("div", { class: "lesson" }, h("p", { class: "label" }, "Lesson"), str(out.lesson)) : null,
      h("div", { class: "metrics" },
        h("span", null, "convergence", h("b", null, fmt(r.convergence))),
        h("span", null, "Ω", h("b", null, fmt(r.omega_surprise, 3))),
        h("span", null, "α", h("b", null, fmt(r.alpha_surprise, 3))),
        h("span", null, "theme", h("b", null, str(scene.theme) || "—")),
        traitMoves.length ? h("span", { class: "trait-moves" }, "traits",
          traitMoves.map(([k, v]) => h("b", { class: v >= 0 ? "delta-up" : "delta-down" }, `${humanize(k)} ${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(2)}`))) : null),
      details,
    );
  }

  // ── controls ────────────────────────────────────────────────
  $("#run-select").addEventListener("change", (e) => openRun(e.target.value));

  $("#upload-input").addEventListener("change", async (e) => {
    const f = e.target.files && e.target.files[0];
    e.target.value = "";
    if (!f) return;
    if (f.size > 20 * 1024 * 1024) { status("That file is larger than the 20 MB upload limit.", "bad"); return; }
    status(`Uploading ${f.name}…`);
    const fd = new FormData();
    fd.append("file", f, f.name);
    try {
      const meta = await api("/api/runs/upload", { method: "POST", body: fd });
      await refreshRuns(meta.id);
      await openRun(meta.id);
      const skipped = num(meta.skipped, 0);
      status(`Loaded ${num(meta.cycles, 0)} cycles from ${str(meta.name)}${skipped ? ` (${skipped} bad line${skipped === 1 ? "" : "s"} skipped)` : ""}.`, skipped ? "" : "ok");
    } catch (err) {
      status(`Upload failed: ${err.message}`, "bad");
    }
  });

  $("#live-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const cycles = Math.max(1, Math.min(200, parseInt($("#live-cycles").value, 10) || 8));
    const seed = parseInt($("#live-seed").value, 10);
    const btn = $("#live-start");
    btn.disabled = true;
    try {
      const res = await api("/api/live", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cycles, seed: Number.isFinite(seed) ? seed : 42, use_llm: false, driver: $("#live-driver").value }),
      });
      await refreshRuns(res.id);
      try { history.replaceState(null, "", `#run=${encodeURIComponent(res.id)}`); } catch (_) { /* ignore */ }
      startView(res.run, []);
      attachStream(res.id);
    } catch (err) {
      btn.disabled = false;
      status(`Could not start live journey: ${err.message}`, "bad");
    }
  });

  // Re-render charts at the new width (and on theme flips, since colors are read from CSS).
  let rz = 0;
  const rerender = () => { cancelAnimationFrame(rz); rz = requestAnimationFrame(renderCharts); };
  if ("ResizeObserver" in window) new ResizeObserver(rerender).observe($("#main"));
  else window.addEventListener("resize", rerender);
  if (window.matchMedia) {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    if (mq.addEventListener) mq.addEventListener("change", rerender);
  }

  // ── boot ────────────────────────────────────────────────────
  (async () => {
    try {
      await refreshRuns();
      const m = /run=([^&]+)/.exec(location.hash);
      const want = m ? decodeURIComponent(m[1]) : null;
      const pick = (want && state.runs.find((r) => r.id === want)) || state.runs[0];
      if (pick) { $("#run-select").value = pick.id; await openRun(pick.id); }
    } catch (e) {
      status(`Could not reach the viewer API: ${e.message}`, "bad");
    }
  })();
})();
