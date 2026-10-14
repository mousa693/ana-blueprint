/* ANA — public web app. Vanilla JS, no dependencies, no build step.
 *
 * Security rules for this file:
 *  - Server/user text is NEVER assigned to innerHTML/outerHTML/insertAdjacentHTML.
 *    Every node is built with h()/svg(), which only use createTextNode for
 *    content and setAttribute for attributes; class names derived from data are
 *    whitelisted.
 *  - URLs from the server (safety resources) are only used if http(s).
 *  - No tokens or personal data in localStorage: the session is an HttpOnly
 *    cookie; localStorage only holds UI preferences ("ana.theme").
 *  - CSP-compatible: no inline scripts/styles; dynamic sizes use the CSSOM.
 */
(() => {
  "use strict";

  // ── DOM helpers ─────────────────────────────────────────────
  const $ = (sel, root = document) => root.querySelector(sel);
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
      if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
      else if (k === "value" && "value" in el) el.value = v;
      else el.setAttribute(k, v === true ? "" : String(v));
    }
    return el;
  }
  const h = (tag, attrs, ...kids) => appendKids(setAttrs(document.createElement(tag), attrs), kids);
  const svg = (tag, attrs, ...kids) => appendKids(setAttrs(document.createElementNS(SVG_NS, tag), attrs), kids);
  const clear = (el) => { while (el.firstChild) el.removeChild(el.firstChild); return el; };
  const setWidth = (el, frac) => el.style.setProperty("width", `${Math.round(Math.max(0, Math.min(1, frac)) * 1000) / 10}%`);

  // ── data helpers (tolerant of missing / extra fields) ───────
  const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  const obj = (v) => (isObj(v) ? v : {});
  const arr = (v) => (Array.isArray(v) ? v : []);
  const str = (v) => (v === null || v === undefined ? "" : typeof v === "string" ? v : String(v));
  const num = (v, d = null) => (typeof v === "number" && Number.isFinite(v) ? v : d);
  const cap = (s) => { s = str(s); return s ? s[0].toUpperCase() + s.slice(1) : s; };
  const humanize = (s) => str(s).replace(/_/g, " ");
  const reducedMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const STAGES = [
    { key: "call", short: "Call", label: "The call to adventure" },
    { key: "threshold", short: "Threshold", label: "Crossing the threshold" },
    { key: "mentor", short: "Mentor", label: "Meeting the mentor" },
    { key: "ordeal", short: "Ordeal", label: "The ordeal" },
    { key: "reward", short: "Reward", label: "Seizing the reward" },
    { key: "return", short: "Return", label: "The return" },
  ];
  const STAGE_ALIASES = { "call to adventure": "call", "crossing the threshold": "threshold", "meeting the mentor": "mentor", "the ordeal": "ordeal", "seizing the reward": "reward", "the return": "return" };
  function stageIndex(v) {
    let s = str(v).toLowerCase().replace(/_/g, " ").trim();
    if (!s) return -1;
    if (STAGE_ALIASES[s]) s = STAGE_ALIASES[s];
    let i = STAGES.findIndex((x) => x.key === s);
    if (i < 0) i = STAGES.findIndex((x) => s.includes(x.key));
    return i;
  }

  const ARCHETYPES = {
    self: "the self", shadow: "the shadow", anima: "the anima", animus: "the animus",
    mentor: "the mentor", trickster: "the trickster", hero: "the hero",
    threshold_guardian: "the guardian", herald: "the herald", ally: "the ally",
  };
  const ROLE_TO_ARCH = { guardian: "threshold_guardian", "threshold guardian": "threshold_guardian" };
  const voiceClass = (arch) => (Object.prototype.hasOwnProperty.call(ARCHETYPES, arch) ? `voice-${arch}` : "");

  const safeUrl = (u) => { u = str(u).trim(); return /^https?:\/\//i.test(u) ? u : ""; };

  // ── state ───────────────────────────────────────────────────
  const state = {
    me: null,           // GET /api/me payload, or null when logged out
    onboarding: null,   // GET /api/onboarding payload (cached)
    pendingCache: new Map(), // journey id -> pending scene handed over by POST /api/journeys
    lastSafety: new Map(),   // journey id -> last crisis safety result seen this session
    keyHandler: null,   // per-view keyboard handler
    flash: null,        // message to show after the next navigation
  };
  let navSeq = 0;

  // ── API ─────────────────────────────────────────────────────
  class ApiError extends Error {
    constructor(status, code, message, data) { super(message); this.status = status; this.code = code; this.data = data; }
  }
  class NetError extends Error {}

  const DEFAULT_MESSAGES = {
    400: "Something about that request wasn't right.",
    401: "Please log in to continue.",
    403: "That isn't allowed.",
    404: "We couldn't find that.",
    409: "That can't be done right now.",
    413: "That's a little too long.",
    422: "Please check what you entered.",
    429: "You've reached today's limit. The story will be waiting for you tomorrow.",
    500: "Something went wrong on our side. Please try again in a moment.",
    503: "ANA is resting for a moment. Please try again shortly.",
  };

  /** fetch wrapper: same-origin cookies, JSON body on every non-GET, parsed
   *  `{error:{code,message}}` → ApiError, network failure → NetError, and 401 →
   *  back to login (unless the caller expects 401, e.g. the login form). */
  async function api(path, { method = "GET", body, expect401 = false, raw = false } = {}) {
    const init = { method, credentials: "same-origin", headers: { Accept: raw ? "*/*" : "application/json" }, cache: "no-store" };
    if (method !== "GET" && method !== "HEAD") {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body === undefined ? {} : body);
    }
    let res;
    try {
      res = await fetch(path, init);
    } catch (_) {
      throw new NetError("We couldn't reach ANA. Check your connection and try again.");
    }
    if (res.ok && raw) return res;
    if (res.status === 204) return null;
    let data = null;
    if ((res.headers.get("content-type") || "").includes("json")) {
      try { data = await res.json(); } catch (_) { data = null; }
    }
    if (!res.ok) {
      const e = obj(obj(data).error);
      const err = new ApiError(res.status, str(e.code) || `http_${res.status}`,
        str(e.message) || DEFAULT_MESSAGES[res.status] || DEFAULT_MESSAGES[500], data);
      if (res.status === 401 && !expect401) {
        err.handled = true;
        onUnauthorized();
      }
      throw err;
    }
    return data;
  }

  function onUnauthorized() {
    const wasIn = !!state.me;
    obClear();
    state.me = null;
    updateNav();
    if (wasIn) state.flash = "Your session has ended. Please log in again.";
    if (!/^#\/(login|signup)?$/.test(location.hash)) go("#/login");
  }

  /** Report an error from an action. Network errors get a Retry toast. */
  function report(err, retry) {
    if (err && err.handled) return;
    if (err instanceof NetError) {
      toast(err.message, { action: retry ? { label: "Retry", fn: retry } : null, sticky: !!retry });
    } else {
      toast(err && err.message ? err.message : DEFAULT_MESSAGES[500], { kind: "bad" });
    }
  }

  // ── toasts ──────────────────────────────────────────────────
  function toast(message, { kind = "", action = null, sticky = false, ms = 5200 } = {}) {
    const root = $("#toasts");
    const el = h("div", { class: `toast ${kind}` }, h("span", null, message));
    const close = () => { if (el.parentNode) el.parentNode.removeChild(el); };
    if (action) {
      el.append(h("button", { class: "btn", type: "button", onclick: () => { close(); action.fn(); } }, action.label));
    }
    el.append(h("button", { class: "toast-x", type: "button", "aria-label": "Dismiss", onclick: close }, "×"));
    root.append(el);
    if (!sticky) setTimeout(close, ms);
    return close;
  }

  // ── confirm dialog ──────────────────────────────────────────
  function confirmDialog({ title, body, ok = "Confirm", cancel = "Not yet", danger = false }) {
    const dlg = $("#confirm");
    $("#confirm-title").textContent = title;
    $("#confirm-body").textContent = body;
    const okBtn = $("#confirm-ok");
    okBtn.textContent = ok;
    okBtn.className = `btn ${danger ? "btn-danger" : "btn-primary"}`;
    $("#confirm-cancel").textContent = cancel;
    return new Promise((resolve) => {
      const done = () => { dlg.removeEventListener("close", done); resolve(dlg.returnValue === "ok"); };
      dlg.addEventListener("close", done);
      dlg.returnValue = "cancel";
      if (typeof dlg.showModal === "function") dlg.showModal();
      else resolve(window.confirm(`${title}\n\n${body}`));
      $("#confirm-cancel").focus();
    });
  }

  // ── downloads ───────────────────────────────────────────────
  function download(filename, content, type) {
    const blob = content instanceof Blob ? content : new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = h("a", { href: url, download: filename, class: "sr-only" });
    document.body.append(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(url); a.remove(); }, 1000);
  }
  const slug = (s) => str(s).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "ana-journey";

  // ── safety overlay ──────────────────────────────────────────
  function showSafety(safety, { buttonLabel = "Return home", onClose } = {}) {
    const s = obj(safety);
    const root = $("#overlay-root");
    clear(root);
    const resources = arr(s.resources).filter(isObj);
    const returnTo = document.activeElement;
    // Everything behind the card is inert and hidden from assistive tech while
    // it is open (skip link, header, view, toasts…), so focus can't escape.
    const behind = [...document.body.children].filter((el) => el !== root && el.tagName !== "SCRIPT");
    const prevState = behind.map((el) => [el, el.hasAttribute("inert"), el.getAttribute("aria-hidden")]);
    for (const el of behind) { el.setAttribute("inert", ""); el.setAttribute("aria-hidden", "true"); }
    const prevKeys = state.keyHandler;
    const close = () => {
      document.removeEventListener("keydown", trap, true);
      clear(root);
      for (const [el, wasInert, aria] of prevState) {
        if (!wasInert) el.removeAttribute("inert");
        if (aria === null) el.removeAttribute("aria-hidden"); else el.setAttribute("aria-hidden", aria);
      }
      state.keyHandler = prevKeys;
      if (returnTo && returnTo.isConnected && typeof returnTo.focus === "function") returnTo.focus({ preventScroll: true });
      (onClose || (() => go("#/home")))();
    };
    // Belt and braces for browsers without `inert`: keep Tab inside the card.
    const trap = (e) => {
      if (e.key !== "Tab") return;
      const card = root.firstChild;
      if (!card) return;
      const f = [...card.querySelectorAll("a[href], button:not([disabled]), [tabindex]:not([tabindex='-1'])")];
      if (!f.length) { e.preventDefault(); return; }
      const first = f[0], last = f[f.length - 1];
      const inside = card.contains(document.activeElement);
      if (e.shiftKey && (!inside || document.activeElement === first || document.activeElement === $("#safety-title"))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (!inside || document.activeElement === last)) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", trap, true);
    const card = h("div", { class: "safety", role: "alertdialog", "aria-modal": "true", "aria-labelledby": "safety-title", "aria-describedby": "safety-msg" },
      h("div", { class: "safety-inner" },
        h("div", { class: "breath", "aria-hidden": "true" }),
        h("h1", { id: "safety-title", tabindex: "-1" }, "Let's pause here."),
        h("p", { class: "msg", id: "safety-msg" }, str(s.message) ||
          "It sounds like things may be really hard right now. You deserve support from a real person. Your journey is paused and will be here whenever you want to return."),
        resources.length ? h("ul", { class: "resources", "aria-label": "Support you can reach now" },
          resources.map((r) => {
            const url = safeUrl(r.url);
            return h("li", null,
              h("div", { class: "name" }, url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, str(r.name) || url) : str(r.name)),
              r.note ? h("div", { class: "note" }, str(r.note)) : null);
          })) : null,
        h("div", { class: "btn-row" }, h("button", { class: "btn btn-primary", type: "button", onclick: close }, buttonLabel))));
    root.append(card);
    $("#safety-title").focus();
  }

  // ── chrome ──────────────────────────────────────────────────
  function updateNav(route) {
    const nav = $("#nav");
    nav.hidden = !state.me;
    for (const a of nav.querySelectorAll("a")) {
      if (route && a.dataset.route === route) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    $(".wordmark").setAttribute("href", state.me ? "#/home" : "#/");
  }
  window.addEventListener("scroll", () => {
    $("#topbar").classList.toggle("scrolled", window.scrollY > 4);
  }, { passive: true });

  function applyTheme(pref) {
    const root = document.documentElement;
    if (pref === "light" || pref === "dark") root.setAttribute("data-theme", pref);
    else root.removeAttribute("data-theme");
  }
  function themePref() { try { return localStorage.getItem("ana.theme") || "system"; } catch (_) { return "system"; } }
  function setThemePref(p) { try { localStorage.setItem("ana.theme", p); } catch (_) { /* private mode */ } applyTheme(p); }
  applyTheme(themePref());

  document.addEventListener("keydown", (e) => {
    if (state.keyHandler) state.keyHandler(e);
  });

  // ── router ──────────────────────────────────────────────────
  const go = (hash) => { if (location.hash === hash) route(); else location.hash = hash; };
  const PUBLIC = new Set(["", "login", "signup"]);

  async function route() {
    const seq = ++navSeq;
    state.keyHandler = null;
    const parts = location.hash.replace(/^#\/?/, "").split("/").map(decodeURIComponent);
    const name = parts[0] || "";
    const view = $("#view");
    if (!PUBLIC.has(name) && !state.me) { go("#/login"); return; }
    if (state.me && (name === "" || name === "login" || name === "signup")) {
      go(state.me.has_identity ? "#/home" : "#/onboarding"); return;
    }
    if (state.me && !state.me.has_identity && name !== "onboarding" && name !== "settings") { go("#/onboarding"); return; }
    updateNav(name);
    const ctx = {
      seq, view,
      alive: () => seq === navSeq,
      mount(node, title) {
        if (seq !== navSeq) return false;
        clear(view).append(node);
        document.title = title ? `${title} · ANA` : "ANA";
        window.scrollTo(0, 0);
        // Move focus to the new heading on in-app navigation, but not on the
        // first page load: there the document start is the right place, so the
        // first Tab lands on "Skip to content".
        const focusEl = view.querySelector("h1");
        if (focusEl) {
          focusEl.setAttribute("tabindex", "-1");
          if (state.navigated) focusEl.focus({ preventScroll: true });
        }
        if (node && !node.classList.contains("loading")) state.navigated = true;
        if (state.flash) { toast(state.flash); state.flash = null; }
        return true;
      },
    };
    const views = { "": viewLanding, login: viewLogin, signup: viewSignup, onboarding: viewOnboarding, home: viewHome, play: viewPlay, debrief: viewDebrief, settings: viewSettings };
    const fn = views[name];
    if (!fn) { ctx.mount(errorPage("Nothing here", "That page doesn't exist.", "#/"), "Not found"); return; }
    try {
      await fn(ctx, parts.slice(1));
    } catch (err) {
      if (err && err.handled) return;
      if (!ctx.alive()) return;
      if (err instanceof NetError) {
        ctx.mount(errorPage("We couldn't reach ANA", err.message, null, () => route()), "Offline");
      } else {
        ctx.mount(errorPage("Something went wrong", err && err.message ? err.message : DEFAULT_MESSAGES[500], state.me ? "#/home" : "#/"), "Error");
      }
    }
  }
  window.addEventListener("hashchange", route);

  function loading(text = "One moment…") { return h("div", { class: "loading", role: "status" }, text); }
  function errorPage(title, message, href, retry) {
    return h("div", { class: "error-page col" },
      h("h1", null, title), h("p", null, message),
      h("div", { class: "btn-row" },
        retry ? h("button", { class: "btn btn-primary", type: "button", onclick: retry }, "Try again") : null,
        href ? h("a", { class: "btn", href }, "Go back") : null));
  }

  async function getOnboarding() {
    if (state.onboarding) return state.onboarding;
    state.onboarding = obj(await api("/api/onboarding", { expect401: true }));
    return state.onboarding;
  }
  const SCENE_MOVED = "This scene moved on in another window — here's where you are.";
  // Mirrors ana/safety.py RESOURCES exactly (names, URLs, numbers). Used only
  // when no safety result from the server is at hand (e.g. a paused journey
  // opened in a fresh session).
  const RESOURCES = [
    { name: "Emergency services", url: null, note: "If you or someone else is in immediate danger, call your local emergency number now (for example 911, 112 or 999)." },
    { name: "Find A Helpline", url: "https://findahelpline.com", note: "Free, confidential helplines around the world — search by country and by what you're going through." },
    { name: "988 Suicide & Crisis Lifeline", url: "https://988lifeline.org", note: "United States: call or text 988, any time." },
    { name: "9-8-8 Suicide Crisis Helpline", url: "https://988.ca", note: "Canada: call or text 9-8-8, any time." },
    { name: "Samaritans", url: "https://www.samaritans.org", note: "UK & Ireland: call 116 123, free, any time." },
    { name: "Lifeline", url: "https://www.lifeline.org.au", note: "Australia: call 13 11 14, any time." },
  ];
  const FALLBACK_DISCLAIMER = "ANA is a reflective story, not therapy or medical advice, and it is for adults (18+). If you are in crisis, please contact local emergency services or a crisis line such as those listed at findahelpline.com.";

  // ── landing ─────────────────────────────────────────────────
  async function viewLanding(ctx) {
    const disc = h("p", { class: "disclaimer" }, FALLBACK_DISCLAIMER);
    const node = h("div", { class: "landing fade-in" },
      svg("svg", { class: "sigil", viewBox: "0 0 84 84", "aria-hidden": "true" },
        svg("circle", { class: "ring", cx: 42, cy: 42, r: 38 }),
        svg("circle", { class: "ring-2", cx: 42, cy: 42, r: 28 }),
        svg("circle", { class: "core", cx: 42, cy: 42, r: 6 })),
      h("h1", null, "A story that ", h("em", null, "mirrors"), " you."),
      h("p", { class: "lede" },
        "ANA is a quiet, symbolic world that grows out of your inner life. Each turn it dreams a scene shaped by what you carry; you choose what to do, the world answers, and over a journey you walk the old hero’s road — and come home with a reflection of who you were becoming."),
      h("div", { class: "btn-row" },
        h("a", { class: "btn btn-primary", href: "#/signup" }, "Begin — create an account"),
        h("a", { class: "btn btn-quiet", href: "#/login" }, "I already have one")),
      h("ol", { class: "steps", "aria-label": "How it works" },
        h("li", null, h("span", { class: "n" }, "i."), h("h2", null, "Tell it a little"), h("p", null, "A few gentle questions seed your self: what you carry, what you hope to become, what must never appear.")),
        h("li", null, h("span", { class: "n" }, "ii."), h("h2", null, "Live a journey"), h("p", null, "Scenes unfold beat by beat. Choose, or write your own action. Figures from your inner cast walk with you.")),
        h("li", null, h("span", { class: "n" }, "iii."), h("h2", null, "Come home"), h("p", null, "Every journey ends in a debrief — chapters, turning points, lessons, and a closing image to keep."))),
      disc);
    ctx.mount(node, "");
    try {
      const ob = await getOnboarding();
      if (ctx.alive() && ob.disclaimer) disc.textContent = str(ob.disclaimer);
    } catch (_) { /* static copy stays */ }
  }

  // ── auth ────────────────────────────────────────────────────
  function fieldEl({ id, label, type = "text", autocomplete, hint, attrs = {} }) {
    const hintId = hint ? `${id}-hint` : null;
    return h("label", { class: "field", for: id },
      h("span", { class: "label" }, label),
      h("input", { class: "input", id, name: id, type, autocomplete, "aria-describedby": hintId, ...attrs }),
      hint ? h("span", { class: "hint", id: hintId }, hint) : null);
  }
  function formError(form) {
    let el = form.querySelector(".form-error");
    if (!el) { el = h("div", { class: "form-error", role: "alert", hidden: true }); form.append(el); }
    return {
      show(msg) { el.textContent = msg; el.hidden = false; },
      hide() { el.hidden = true; el.textContent = ""; },
    };
  }
  function busy(btn, on, text) {
    if (on) { btn.dataset.label = btn.textContent; btn.disabled = true; btn.classList.add("busy"); if (text) btn.textContent = text; }
    else { btn.disabled = false; btn.classList.remove("busy"); if (btn.dataset.label) btn.textContent = btn.dataset.label; }
  }

  // Same shape as the server's _EMAIL_RE: 1–64 char local part, a dotted
  // domain, and a top-level part of at least two characters.
  const EMAIL_RE = /^[^@\s\x00-\x1f]{1,64}@[^@\s\x00-\x1f]+\.[^@\s\x00-\x1f]{2,}$/;
  const validEmail = (e) => e.length >= 3 && e.length <= 254 && EMAIL_RE.test(e);

  async function afterAuth() {
    state.me = obj(await api("/api/me"));
    updateNav();
    go(state.me.has_identity ? "#/home" : "#/onboarding");
  }

  async function viewSignup(ctx) {
    const inviteField = fieldEl({ id: "invite", label: "Invite code", autocomplete: "off", hint: "ANA is opening slowly. Your invite is in the email that brought you here." });
    inviteField.hidden = true;
    const submit = h("button", { class: "btn btn-primary", type: "submit" }, "Create account");
    const form = h("form", { novalidate: true },
      fieldEl({ id: "email", label: "Email", type: "email", autocomplete: "email", attrs: { required: true, inputmode: "email", autocapitalize: "off", spellcheck: "false" } }),
      fieldEl({ id: "password", label: "Password", type: "password", autocomplete: "new-password", hint: "At least 10 characters, not counting spaces at either end.", attrs: { required: true, minlength: 10, maxlength: 256 } }),
      inviteField,
      h("label", { class: "check", for: "age" },
        h("input", { type: "checkbox", id: "age", name: "age", required: true }),
        h("span", null, "I’m 18 or older, and I understand ANA is a reflective story — not therapy.")),
      submit);
    const errBox = formError(form);
    const node = h("div", { class: "auth fade-in" },
      h("h1", null, "Create your account"),
      h("p", { class: "sub" }, "Your inner material stays yours: private, exportable, deletable."),
      form,
      h("p", { class: "alt" }, "Already have an account? ", h("a", { href: "#/login" }, "Log in")));
    if (!ctx.mount(node, "Sign up")) return;
    $("#email").focus();
    getOnboarding().then((ob) => { if (ob.invite_required === true) inviteField.hidden = false; }).catch(() => {});

    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      errBox.hide();
      const email = $("#email").value.trim();
      const password = $("#password").value;
      const age = $("#age").checked;
      const invite = $("#invite").value.trim();
      for (const id of ["email", "password"]) $(`#${id}`).removeAttribute("aria-invalid");
      // Mirrors the server's rules (ana/app/auth.py) and wording, so the browser
      // and the server never disagree; if the server still rejects, its own
      // message is shown verbatim below.
      if (!validEmail(email)) { $("#email").setAttribute("aria-invalid", "true"); $("#email").focus(); errBox.show("That doesn't look like an email address."); return; }
      if (password.trim().length < 10) { $("#password").setAttribute("aria-invalid", "true"); $("#password").focus(); errBox.show("Your password needs at least 10 characters (not counting spaces at either end)."); return; }
      if (password.length > 256) { $("#password").setAttribute("aria-invalid", "true"); $("#password").focus(); errBox.show("Please use a password of at most 256 characters."); return; }
      if (!age) { $("#age").focus(); errBox.show("ANA is for adults. Please confirm you are 18 or older."); return; }
      const payload = { email, password, age_confirmed: true };
      if (invite) payload.invite_code = invite;
      busy(submit, true, "Creating…");
      const attempt = async () => {
        try {
          await api("/api/auth/signup", { method: "POST", body: payload, expect401: true });
          await afterAuth();
        } catch (err) {
          busy(submit, false);
          if (err instanceof NetError) { report(err, () => { busy(submit, true, "Creating…"); attempt(); }); return; }
          if (/email/i.test(err.code || "")) { $("#email").setAttribute("aria-invalid", "true"); $("#email").focus(); }
          else if (/password/i.test(err.code || "")) { $("#password").setAttribute("aria-invalid", "true"); $("#password").focus(); }
          if (/invite/i.test(err.code || "")) {
            inviteField.hidden = false;
            $("#invite").setAttribute("aria-invalid", "true");
            $("#invite").focus();
          }
          errBox.show(err.message);
        }
      };
      attempt();
    });
  }

  async function viewLogin(ctx) {
    const submit = h("button", { class: "btn btn-primary", type: "submit" }, "Log in");
    const form = h("form", { novalidate: true },
      fieldEl({ id: "email", label: "Email", type: "email", autocomplete: "email", attrs: { required: true, inputmode: "email", autocapitalize: "off", spellcheck: "false" } }),
      fieldEl({ id: "password", label: "Password", type: "password", autocomplete: "current-password", attrs: { required: true } }),
      submit);
    const errBox = formError(form);
    const node = h("div", { class: "auth fade-in" },
      h("h1", null, "Welcome back"),
      h("p", { class: "sub" }, "Your story is where you left it."),
      form,
      h("p", { class: "alt" }, "New here? ", h("a", { href: "#/signup" }, "Create an account")));
    if (!ctx.mount(node, "Log in")) return;
    $("#email").focus();
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      errBox.hide();
      const email = $("#email").value.trim(), password = $("#password").value;
      if (!email || !password) { errBox.show("Please enter your email and password."); return; }
      busy(submit, true, "Logging in…");
      const attempt = async () => {
        try {
          await api("/api/auth/login", { method: "POST", body: { email, password }, expect401: true });
          await afterAuth();
        } catch (err) {
          busy(submit, false);
          if (err instanceof NetError) { report(err, () => { busy(submit, true, "Logging in…"); attempt(); }); return; }
          errBox.show(err.status === 401 ? (err.message || "That email and password don't match.") : err.message);
        }
      };
      attempt();
    });
  }

  // ── onboarding ──────────────────────────────────────────────
  const optValue = (o) => (isObj(o) ? (o.value !== undefined ? o.value : o.label) : o);
  const optLabel = (o) => str(isObj(o) ? (o.label !== undefined ? o.label : o.value) : o);
  const allowsText = (q) => q.allow_other === true || q.allow_text === true || q.free_text === true || q.allow_free_text === true || q.allows_text === true;

  // In-progress onboarding survives a reload: answers + current step live in
  // sessionStorage (this tab only), tied to the account, and are cleared on
  // submit, logout, session end or account deletion. Never holds passwords.
  const OB_KEY = "ana.onboarding";
  const meId = () => str(obj(obj(state.me).user).id);
  function obLoad() {
    try {
      const d = JSON.parse(sessionStorage.getItem(OB_KEY) || "null");
      return isObj(d) && d.v === 1 && d.uid === meId() && isObj(d.answers) ? d : null;
    } catch (_) { return null; }
  }
  function obSave(i, answers) {
    try { sessionStorage.setItem(OB_KEY, JSON.stringify({ v: 1, uid: meId(), i, answers })); } catch (_) { /* storage off */ }
  }
  function obClear() { try { sessionStorage.removeItem(OB_KEY); } catch (_) { /* storage off */ } }

  async function viewOnboarding(ctx) {
    ctx.mount(loading(), "Welcome");
    const ob = await getOnboarding();
    const questions = arr(ob.questions).filter(isObj);
    if (!ctx.alive()) return;
    const saved = obLoad();
    const answers = {};
    const ids = new Set(questions.map((q) => str(q.id)));
    if (saved) for (const [k, v] of Object.entries(saved.answers)) if (ids.has(k)) answers[k] = v;
    let i = saved ? Math.max(0, Math.min(questions.length - 1, num(saved.i, 0))) : 0;
    const root = h("div", { class: "ob" });
    ctx.mount(root, "Welcome");

    const isEmpty = (v) => v === undefined || v === null || (typeof v === "string" && !v.trim()) || (Array.isArray(v) && !v.length);

    function renderQ() {
      const q = questions[i];
      clear(root);
      const total = questions.length;
      const barFill = h("span");
      const qid = `q-${i}`;
      const errEl = h("p", { class: "form-error", role: "alert", id: `${qid}-err`, hidden: true });
      const body = h("div", { class: "ob-body" });
      let read = () => undefined;     // returns the current answer value
      const kind = str(q.kind) || "text";
      const opts = arr(q.options);
      const prev = answers[str(q.id)];

      if (kind === "choice") {
        const name = `${qid}-opt`;
        const wrap = h("div", { class: "options", role: "radiogroup", "aria-labelledby": `${qid}-title` },
          opts.map((o, k) => h("label", { class: "option" },
            h("input", { type: "radio", name, value: String(k), checked: prev !== undefined && String(optValue(o)) === String(prev) }),
            h("span", null, optLabel(o)))));
        body.append(wrap);
        let txt = null;
        if (allowsText(q)) {
          txt = h("input", { class: "input", type: "text", maxlength: 300, "aria-label": "Or in your own words", placeholder: "Or in your own words…" });
          if (prev !== undefined && !opts.some((o) => String(optValue(o)) === String(prev))) txt.value = str(prev);
          body.append(h("div", { class: "field" }, txt));
        }
        read = () => {
          const t = txt && txt.value.trim();
          if (t) return t;
          const c = wrap.querySelector("input:checked");
          return c ? optValue(opts[Number(c.value)]) : undefined;
        };
      } else if (kind === "multi") {
        const prevList = arr(prev).map(str);
        const known = new Set(opts.map((o) => str(optValue(o))));
        const wrap = h("div", { class: "chips", role: "group", "aria-labelledby": `${qid}-title` },
          opts.map((o) => h("label", { class: "chip-toggle" },
            h("input", { type: "checkbox", value: str(optValue(o)), checked: prevList.includes(str(optValue(o))) }),
            h("span", null, cap(optLabel(o))))));
        body.append(wrap);
        let txt = null;
        if (allowsText(q)) {
          txt = h("input", { class: "input", type: "text", maxlength: 300, id: `${qid}-text`, placeholder: "Something else, in your own words…" });
          txt.value = prevList.filter((x) => !known.has(x)).join(", ");
          body.append(h("label", { class: "field", for: `${qid}-text` }, h("span", { class: "label" }, "In your own words"), txt));
        }
        read = () => {
          const out = [...wrap.querySelectorAll("input:checked")].map((x) => x.value);
          const t = txt && txt.value.trim();
          if (t) out.push(t);
          return out;
        };
      } else if (kind === "scale") {
        const name = `${qid}-scale`;
        let points;
        if (opts.length) points = opts.map((o, k) => ({ value: isObj(o) && typeof o.value === "number" ? o.value : k, label: optLabel(o) }));
        else {
          const lo = num(q.min, 1), hi = num(q.max, 5);
          points = [];
          for (let v = lo; v <= hi && points.length < 11; v++) points.push({ value: v, label: String(v) });
        }
        const wrap = h("div", { class: "scale", role: "radiogroup", "aria-labelledby": `${qid}-title` },
          points.map((p) => h("label", null,
            h("input", { type: "radio", name, value: String(p.value), checked: prev !== undefined && Number(prev) === p.value }),
            h("span", null, p.label))));
        wrap.style.setProperty("--n", String(points.length));
        body.append(wrap);
        if (!opts.length && (q.min_label || q.max_label)) {
          body.append(h("div", { class: "ribbon-cap" }, h("span", null, str(q.min_label)), h("span", null, str(q.max_label))));
        }
        read = () => { const c = wrap.querySelector("input:checked"); return c ? Number(c.value) : undefined; };
      } else {
        const short = str(q.id) === "name" || q.short === true;
        const input = short
          ? h("input", { class: "input", type: "text", id: `${qid}-input`, maxlength: num(q.max_length, 80), autocomplete: "off", "aria-labelledby": `${qid}-title`, "aria-describedby": q.help ? `${qid}-help` : null })
          : h("textarea", { class: "textarea", id: `${qid}-input`, rows: 3, maxlength: num(q.max_length, 500), "aria-labelledby": `${qid}-title`, "aria-describedby": q.help ? `${qid}-help` : null });
        if (prev !== undefined) input.value = str(prev);
        body.append(input);
        read = () => { const v = input.value.trim(); return v || undefined; };
      }

      const keep = () => { const v = read(); if (isEmpty(v)) delete answers[str(q.id)]; else answers[str(q.id)] = v; };
      const back = h("button", { class: "btn btn-quiet", type: "button", onclick: () => { keep(); i -= 1; obSave(i, answers); renderQ(); } }, "← Back");
      if (i === 0) back.setAttribute("hidden", "");
      const last = i === total - 1;
      const next = h("button", { class: "btn btn-primary", type: "submit" }, last ? "Begin" : (q.optional && isEmpty(prev) ? "Skip / next" : "Next"));

      const form = h("form", { class: "ob-q", novalidate: true, "aria-describedby": `${qid}-err` },
        h("h1", { id: `${qid}-title` }, str(q.prompt), q.optional ? h("span", { class: "optional" }, "optional") : null),
        q.help ? h("p", { class: "help", id: `${qid}-help` }, str(q.help)) : null,
        body, errEl,
        h("div", { class: "ob-nav" }, i === 0 ? h("span") : back, next));

      // live "Skip" label for optional questions
      const onEdit = () => {
        if (!last) next.textContent = q.optional && isEmpty(read()) ? "Skip / next" : "Next";
        keep(); obSave(i, answers);
      };
      form.addEventListener("input", onEdit);
      form.addEventListener("change", onEdit);
      form.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && e.target.tagName === "TEXTAREA" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); form.requestSubmit(); }
      });
      form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const v = read();
        if (!q.optional && isEmpty(v)) {
          errEl.textContent = kind === "multi" ? "Choose at least one, or add your own words." : kind === "text" ? "A few words is enough — write anything to continue." : "Choose one to continue.";
          errEl.hidden = false;
          return;
        }
        if (isEmpty(v)) delete answers[str(q.id)]; else answers[str(q.id)] = v;
        if (!last) { i += 1; obSave(i, answers); renderQ(); return; }
        busy(next, true, "Seeding your self…");
        const send = async () => {
          try {
            const res = obj(await api("/api/onboarding", { method: "POST", body: { answers } }));
            if (res.safety && obj(res.safety).level === "crisis") {
              busy(next, false);
              obClear();
              showSafety(res.safety, { buttonLabel: "Return", onClose: () => { i = 0; renderQ(); } });
              return;
            }
            obClear();
            state.me = Object.assign({}, state.me, { has_identity: true });
            seeded(obj(res.identity), res.safety, Object.assign({}, answers));
          } catch (err) {
            busy(next, false);
            report(err, err instanceof NetError ? () => { busy(next, true, "Seeding your self…"); send(); } : null);
          }
        };
        send();
      });

      root.append(
        h("div", { class: "ob-progress" },
          h("span", { class: "kicker" }, `${i + 1} of ${total}`),
          h("div", { class: "ob-bar", role: "progressbar", "aria-label": "Onboarding progress", "aria-valuemin": 0, "aria-valuemax": total, "aria-valuenow": i + 1 }, barFill)),
        form);
      requestAnimationFrame(() => setWidth(barFill, (i + 1) / total));
      form.classList.add("fade-in");
      const first = body.querySelector("input:not([type=hidden]), textarea");
      const title = form.querySelector("h1");
      title.setAttribute("tabindex", "-1");
      (kind === "text" && first ? first : title).focus({ preventScroll: true });
    }

    // Labels for what the person actually picked (or wrote) for a question.
    function chosenLabels(qid, value) {
      const q = questions.find((x) => str(x.id) === qid) || {};
      const labels = new Map(arr(q.options).map((o) => [str(optValue(o)), optLabel(o)]));
      return arr(value).map(str).map((v) => v.trim()).filter(Boolean).map((v) => labels.get(v) || v);
    }
    function seededCopy(given) {
      const carried = chosenLabels("carrying", given.carrying);
      const never = chosenLabels("never_show", given.never_show);
      const start = carried.length ? `The world will begin with what you carry — ${carried.join(", ")}` : "The world will begin with what you shared";
      return never.length ? `${start} — and it will keep what you asked it never to show out of every scene.` : `${start}.`;
    }

    function seeded(identity, safety, given) {
      clear(root);
      const name = str(identity.name);
      const start = h("button", { class: "btn btn-primary", type: "button" }, "Begin your first journey");
      start.addEventListener("click", () => beginJourney(start));
      root.append(h("div", { class: "ob-q fade-in" },
        h("p", { class: "kicker" }, "Your self is seeded"),
        h("h1", { tabindex: "-1" }, name && name !== "the wanderer" ? `Thank you, ${name}.` : "Thank you."),
        concernNote(safety, { lead: true }),
        h("p", { class: "help u-text" }, seededCopy(obj(given))),
        h("div", { class: "ob-nav" }, h("a", { class: "btn btn-quiet", href: "#/home" }, "Go to home"), start)));
      root.querySelector("h1").focus();
    }

    if (!questions.length) {
      ctx.mount(errorPage("Onboarding is unavailable", "We couldn't load the questions. Please try again shortly.", null, () => route()), "Welcome");
      return;
    }
    renderQ();
  }

  function concernNote(safety, { lead = false } = {}) {
    const s = obj(safety);
    if (!s.level || s.level === "none") return null;
    const res = arr(s.resources).filter(isObj);
    return h("aside", { class: `concern${lead ? " concern-lead" : ""}`, role: "note", "aria-label": "A gentle note" },
      h("p", null, str(s.message) || "If things feel heavy, support is available."),
      res.length ? h("ul", null, res.map((r) => {
        const url = safeUrl(r.url);
        return h("li", null, url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, str(r.name)) : str(r.name), r.note ? ` — ${str(r.note)}` : "");
      })) : null);
  }

  /** Support lines as cards (paused view). */
  function resourcesList(resources) {
    return h("ul", { class: "resources", "aria-label": "Support you can reach now" },
      resources.map((r) => {
        const url = safeUrl(r.url);
        return h("li", null,
          h("div", { class: "name" }, url ? h("a", { href: url, target: "_blank", rel: "noopener noreferrer" }, str(r.name) || url) : str(r.name)),
          r.note ? h("div", { class: "note" }, str(r.note)) : null);
      }));
  }

  // ── home ────────────────────────────────────────────────────
  async function beginJourney(btn, noticeEl) {
    busy(btn, true, "Dreaming the first scene…");
    try {
      const res = obj(await api("/api/journeys", { method: "POST" }));
      const j = obj(res.journey);
      if (res.pending) state.pendingCache.set(str(j.id), { journey: j, pending: res.pending });
      go(`#/play/${encodeURIComponent(str(j.id))}`);
    } catch (err) {
      busy(btn, false);
      if (err instanceof NetError) { report(err, () => beginJourney(btn, noticeEl)); return; }
      if (err.status === 409 || err.status === 429) {
        const msg = err.status === 409 && !/journey/i.test(err.message)
          ? `${err.message} You can have only a few journeys open at once — finish or end one to begin another.`
          : err.message;
        if (noticeEl) { noticeEl.textContent = msg; noticeEl.hidden = false; }
        else toast(msg);
        return;
      }
      report(err);
    }
  }

  const STATUS_LABEL = { active: "In progress", closed: "Came home", ended: "Ended", paused_safety: "Paused" };
  const fmtDate = (iso) => {
    const d = new Date(str(iso));
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
  };
  function greeting(name) {
    const hr = new Date().getHours();
    const part = hr < 5 ? "Still awake" : hr < 12 ? "Good morning" : hr < 18 ? "Good afternoon" : "Good evening";
    return name && name !== "the wanderer" ? `${part}, ${name}.` : `${part}.`;
  }

  async function viewHome(ctx) {
    ctx.mount(loading(), "Home");
    const [me, idRes, jRes] = await Promise.all([api("/api/me"), api("/api/identity"), api("/api/journeys")]);
    if (!ctx.alive()) return;
    state.me = obj(me);
    const ident = obj(obj(idRes).identity);
    const journeys = arr(obj(jRes).journeys).filter(isObj);
    const usage = obj(state.me.usage);
    const open = journeys.filter((j) => j.status === "active" || j.status === "paused_safety");

    const notice = h("p", { class: "notice", role: "status", hidden: true });
    const beginBtn = h("button", { class: "btn btn-primary", type: "button" }, "Begin a new journey");
    beginBtn.addEventListener("click", () => beginJourney(beginBtn, notice));

    const goals = arr(ident.goals).filter(isObj);
    const themes = arr(ident.active_themes).map(str).filter(Boolean);
    const insights = arr(ident.insights).map(str).filter(Boolean);
    const selfCard = h("section", { class: "card self", "aria-labelledby": "self-h" },
      h("h2", { id: "self-h", class: "section-head" }, "Your self"),
      h("p", { class: "name" }, cap(str(ident.name) || "the wanderer")),
      themes.length ? [h("h3", null, "What you carry"), h("div", { class: "tags" }, themes.map((t) => h("span", { class: "tag" }, humanize(t))))] : null,
      goals.length ? [h("h3", null, "Who you're becoming"),
        h("ul", { class: "goals" }, goals.map((g) => {
          const done = g.realised === true;
          // A realised goal is complete, whatever its last progress number was.
          const p = done ? 1 : Math.max(0, Math.min(1, num(g.progress, 0)));
          const fill = h("span");
          requestAnimationFrame(() => setWidth(fill, p));
          return h("li", { class: `goal${done ? " realised" : ""}` },
            h("div", { class: "desc" }, h("span", { class: "u-text" }, cap(str(g.description))),
              done ? h("span", { class: "badge" }, h("span", { "aria-hidden": "true" }, "✦"), "Realised") : h("span", { class: "pct" }, `${Math.round(p * 100)}%`)),
            h("div", { class: "bar", role: "progressbar", "aria-label": `Progress toward ${str(g.description)}`, "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": Math.round(p * 100), "aria-valuetext": done ? "Realised" : `${Math.round(p * 100)}%` }, fill));
        }))] : null,
      [h("h3", null, "What the journal has noticed"),
        insights.length ? h("ul", { class: "insights" }, insights.slice(0, 5).map((x) => h("li", null, cap(x))))
          : h("p", { class: "muted small" }, "Insights gather as you live a journey or two.")]);

    const list = journeys.length
      ? h("ul", { class: "journeys" }, journeys.map((j) => {
        const id = encodeURIComponent(str(j.id));
        const done = j.status === "closed" || j.status === "ended";
        const cycles = num(j.cycles, 0);
        return h("li", { class: "journey" },
          h("div", null,
            h("h3", null, str(j.title) || "Untitled journey"),
            h("p", { class: "meta" },
              h("span", { class: `status-dot ${STATUS_LABEL[j.status] ? j.status : ""}`, "aria-hidden": "true" }),
              STATUS_LABEL[j.status] || cap(humanize(j.status)),
              j.chapter && j.chapter !== "Prologue" ? ` · ${str(j.chapter)}` : "",
              ` · ${cycles} turn${cycles === 1 ? "" : "s"}`,
              fmtDate(j.updated_at) ? ` · ${fmtDate(j.updated_at)}` : "")),
          done ? h("a", { class: "btn", href: `#/debrief/${id}` }, "Read debrief")
            : h("a", { class: "btn btn-primary", href: `#/play/${id}` }, j.status === "paused_safety" ? "Return" : "Continue"));
      }))
      : h("p", { class: "empty-note" }, "No journeys yet. The first one is waiting.");

    const limit = num(usage.limit), today = num(usage.today, 0);
    const node = h("div", { class: "home fade-in" },
      h("div", { class: "greet" },
        h("h1", null, greeting(str(ident.name))),
        h("p", null, open.length ? "Your story is where you left it." : "Where would you like to begin?")),
      h("section", { class: "card begin", "aria-label": "Start a journey" },
        h("div", null, h("h2", null, open.length ? "Or begin somewhere new" : journeys.length ? "A new road is waiting" : "Your first journey is waiting"),
          h("p", null, "A fresh road from the call to the return. Your self carries over.")),
        beginBtn),
      notice,
      h("div", { class: "home-grid" },
        h("section", { "aria-labelledby": "jr-h" }, h("h2", { id: "jr-h" }, "Your journeys"), list),
        selfCard),
      limit !== null ? h("p", { class: "usage" }, `Turns today: ${today} of ${limit}. They reset at midnight UTC.`) : null);
    ctx.mount(node, "Home");
  }

  // ── play ────────────────────────────────────────────────────
  function trackEl(stage) {
    const idx = stageIndex(stage);
    return h("ol", { class: "track", "aria-label": idx >= 0 ? `Hero's journey — stage ${idx + 1} of 6: ${STAGES[idx].label}` : "Hero's journey" },
      STAGES.map((s, k) => h("li", { class: k < idx ? "past" : k === idx ? "now" : "", "aria-current": k === idx ? "step" : null, title: s.label },
        h("span", { class: "dot", "aria-hidden": "true" }),
        h("span", { class: "lbl" }, s.short))));
  }
  function ribbonWords(c) {
    if (c < 0.15) return "the world keeps its distance";
    if (c < 0.35) return "the world begins to mirror you";
    if (c < 0.6) return "inner and outer draw closer";
    if (c < 0.8) return "the world mirrors you";
    return "inner and outer are one";
  }
  function ribbonEl(conv) {
    const c = Math.max(0, Math.min(1, num(conv, 0)));
    const fill = h("span");
    const el = h("div", { class: "ribbon" },
      h("div", { class: "ribbon-line", role: "meter", "aria-label": "Convergence — how closely the world mirrors you", "aria-valuemin": 0, "aria-valuemax": 100, "aria-valuenow": Math.round(c * 100), "aria-valuetext": `${Math.round(c * 100)}%, ${ribbonWords(c)}` }, fill),
      h("div", { class: "ribbon-cap", "aria-hidden": "true" }, h("span", null, ribbonWords(c)), h("span", null, `${Math.round(c * 100)}%`)));
    el.update = (v) => {
      const x = Math.max(0, Math.min(1, num(v, 0)));
      setWidth(fill, x);
      const line = el.firstChild;
      line.setAttribute("aria-valuenow", String(Math.round(x * 100)));
      line.setAttribute("aria-valuetext", `${Math.round(x * 100)}%, ${ribbonWords(x)}`);
      el.lastChild.firstChild.textContent = ribbonWords(x);
      el.lastChild.lastChild.textContent = `${Math.round(x * 100)}%`;
    };
    requestAnimationFrame(() => requestAnimationFrame(() => setWidth(fill, c)));
    return el;
  }

  /** Typographic quotes/apostrophes for story text (pure text transform). */
  const typo = (t) => str(t)
    .replace(/(^|[\s(\[—–-])"/g, "$1\u201C").replace(/"/g, "\u201D")
    .replace(/(^|[\s(\[—–-])'/g, "$1\u2018").replace(/'/g, "\u2019");
  const escRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

  /** Split engine dialogue like `Corren says: "…"` or `Corren, a figure …,
   *  speaks. "…"` into an optional intro and the spoken line, since the
   *  speaker is already shown as a label. Falls back to the raw text. */
  function splitDialogue(text, speaker) {
    if (!speaker) return { intro: "", line: text };
    const sp = escRe(speaker);
    let m = new RegExp(`^${sp}\\s+(?:says|tells you|murmurs|asks|whispers|calls out|answers|replies|adds|warns)(?: softly| quietly)?(?:,[^:"“]{1,40})?:\\s*(.+)$`, "i").exec(text);
    if (m) return { intro: "", line: m[1] };
    m = new RegExp(`^${sp},\\s*(.+?),?\\s+speaks\\.\\s*(.+)$`, "i").exec(text);
    if (m) return { intro: m[1], line: m[2] };
    return { intro: "", line: text };
  }

  function beatEl(b) {
    b = obj(b);
    const kind = str(b.kind) || "description";
    const text = typo(b.text);
    const speaker = str(b.speaker);
    const arch = str(b.archetype);
    if (kind === "dialogue" && /grounding voice/i.test(speaker)) {
      return h("li", { class: "beat beat-ground" }, h("span", { class: "sr-only" }, "A grounding voice: "), text);
    }
    if (kind === "dialogue") {
      return h("li", { class: `beat beat-dialogue ${voiceClass(arch)}` },
        speaker ? h("span", { class: "speaker" }, speaker, ARCHETYPES[arch] ? h("span", { class: "arch" }, ARCHETYPES[arch]) : null) : null,
        ...(() => { const d = splitDialogue(text, speaker); return [d.intro ? h("span", { class: "intro" }, d.intro) : null, d.line]; })());
    }
    if (kind === "synchronicity") {
      return h("li", { class: "beat beat-synchronicity" }, h("span", { class: "speaker" }, "✦ Synchronicity"), text);
    }
    if (kind === "event") {
      const entropy = str(b.symbol).startsWith("~");
      return h("li", { class: `beat beat-event${entropy ? " entropy" : ""}` }, h("span", { class: "sr-only" }, "Event: "), speaker ? `${speaker}: ` : "", text);
    }
    if (kind === "choice") return h("li", { class: "beat beat-choice" }, text);
    // A drop cap on a one-letter word ("A town…") reads as "Atown": skip it.
    const oneLetter = /^\W*\w\s/.test(String(text));
    return h("li", { class: oneLetter ? "beat beat-description no-dropcap" : "beat beat-description" }, text);
  }

  const actionText = (a) => (isObj(a) ? str(a.raw || a.text) : str(a));

  async function viewPlay(ctx, [jid]) {
    jid = str(jid);
    const enc = encodeURIComponent(jid);
    let journey, pending, records = [];
    const cached = state.pendingCache.get(jid);
    if (cached) {
      state.pendingCache.delete(jid);
      journey = cached.journey; pending = cached.pending;
    } else {
      ctx.mount(loading("Finding your place in the story…"), "Journey");
      const res = obj(await api(`/api/journeys/${enc}`));
      journey = obj(res.journey); records = arr(res.records); pending = res.pending;
      if (!ctx.alive()) return;
      if (journey.status === "closed" || journey.status === "ended") { go(`#/debrief/${enc}`); return; }
      if (!pending) {
        const p = obj(await api(`/api/journeys/${enc}/pending`));
        if (!ctx.alive()) return;
        if (p.closed) { go(`#/debrief/${enc}`); return; }
        pending = p.pending;
      }
    }
    let turn = Math.max(records.length, num(obj(pending).journey_tick, 0)) + 1;
    // Turn numbers follow the scene's journey_tick, so a scene re-rendered
    // after "scene_changed" shows the right turn without counting locally.
    const baseTick = num(obj(pending).journey_tick);
    const baseTurn = turn;
    let sceneTick = baseTick;    // journey_tick of the scene on screen

    const head = h("header", { class: "play-head" });
    const trackSlot = h("div");
    const ribbon = ribbonEl(obj(pending).convergence);
    const stage = h("div", { class: "stage-area" });
    const turnLbl = h("span", null);
    const endBtn = h("button", { class: "btn btn-quiet", type: "button" }, "End journey");
    const root = h("div", { class: "play" }, head, trackSlot, ribbon, stage,
      h("footer", { class: "play-foot" }, turnLbl, endBtn));
    if (!ctx.mount(root, "Journey")) return;

    endBtn.addEventListener("click", async () => {
      const ok = await confirmDialog({
        title: "End this journey here?",
        body: "The story will close where it stands and ANA will write your debrief from what you have lived so far. You can't continue it afterwards.",
        ok: "End and read debrief", cancel: "Keep walking",
      });
      if (!ok) return;
      busy(endBtn, true, "Closing…");
      const send = async () => {
        try {
          await api(`/api/journeys/${enc}/end`, { method: "POST" });
          go(`#/debrief/${enc}`);
        } catch (err) { busy(endBtn, false); report(err, err instanceof NetError ? send : null); }
      };
      send();
    });

    function setHead(p) {
      clear(head).append(
        h("p", { class: "kicker" }, str(p.chapter) || "The journey"),
        p.location ? h("p", { class: "loc" }, `at ${str(p.location)}`) : null);
      clear(trackSlot).append(trackEl(p.stage || obj(p.scene).stage));
      ribbon.update(p.convergence);
      turnLbl.textContent = `Turn ${turn}`;
    }

    async function resume(btn) {
      busy(btn, true, "One moment…");
      try {
        const r = obj(await api(`/api/journeys/${enc}/resume`, { method: "POST", body: { acknowledged: true } }));
        if (!r.pending) { go(`#/debrief/${enc}`); return; }
        showScene(obj(r.pending));
      } catch (err) { busy(btn, false); report(err, err instanceof NetError ? () => resume(btn) : null); }
    }
    function showPaused(safety) {
      state.keyHandler = null;
      clear(head); clear(trackSlot); ribbon.hidden = true; turnLbl.textContent = "";
      const s = obj(safety || state.lastSafety.get(jid) || journey.safety);
      const res = arr(s.resources).filter(isObj);
      const disc = h("p", { class: "disclaimer" }, FALLBACK_DISCLAIMER);
      const title = h("h1", { class: "paused-title", tabindex: "-1" }, "Your journey is paused");
      clear(stage).append(h("section", { class: "paused fade-in", "aria-labelledby": "paused-title" },
        title,
        h("p", { class: "msg" }, "This journey was paused so you could take care of yourself. There's no hurry — it will be here whenever you're ready."),
        h("h2", { class: "kicker" }, "Support you can reach now"),
        resourcesList(res.length ? res : RESOURCES),
        disc,
        h("div", { class: "btn-row db-actions" },
          h("button", { class: "btn btn-primary", type: "button", onclick: (e) => resume(e.currentTarget) }, "I'm ready to continue"),
          h("a", { class: "btn btn-quiet", href: "#/home" }, "Return home"))));
      title.id = "paused-title";
      document.title = "Paused · ANA";
      title.focus({ preventScroll: true });
      getOnboarding().then((ob) => { if (ob.disclaimer && disc.isConnected) disc.textContent = str(ob.disclaimer); }).catch(() => {});
    }
    if (journey.status === "paused_safety") { showPaused(); return; }
    showScene(obj(pending));

    /** Render a scene. `notice` is a calm line shown above it (e.g. the scene
     *  moved on in another window); `safety` a concern-level result whose
     *  gentle note leads the scene rather than trailing the last turn. */
    function showScene(p, { notice = "", safety = null } = {}) {
      const tick = num(p.journey_tick);
      sceneTick = tick;
      if (tick !== null && baseTick !== null) turn = baseTurn + (tick - baseTick);
      ribbon.hidden = false;
      setHead(p);
      const scene = obj(p.scene);
      const beats = arr(scene.beats);
      const list = h("ol", { class: "beats", "aria-live": "polite", "aria-label": "The scene" });
      const title = h("h1", { class: "scene-title" }, str(scene.title) || "A scene");
      const card = h("article", { class: `card scene${scene.is_vision ? " vision" : ""}`, "aria-labelledby": "scene-title" },
        scene.is_vision ? h("p", { class: "vision-mark" }, "◌ A vision — not literal") : null,
        title, list);
      title.id = "scene-title";
      const controls = h("div", { class: "reveal-row" });
      const after = h("div");
      const noticeEl = notice ? h("p", { class: "notice scene-moved", role: "status" }, notice) : null;
      appendKids(clear(stage), [noticeEl, concernNote(safety, { lead: true }), card, controls, after]);
      card.classList.add("fade-in");
      window.scrollTo({ top: 0, behavior: reducedMotion() ? "auto" : "smooth" });
      title.setAttribute("tabindex", "-1");
      title.focus({ preventScroll: true });

      let shown = 0;
      const revealNext = () => {
        if (shown >= beats.length) return;
        const el = beatEl(beats[shown]);
        if (!reducedMotion()) el.classList.add("enter");
        list.append(el);
        shown += 1;
        if (shown >= beats.length) finishReveal();
        else if (!reducedMotion()) {
          const r = el.getBoundingClientRect();
          if (r.bottom > window.innerHeight - 120) window.scrollBy({ top: r.bottom - window.innerHeight + 160, behavior: "smooth" });
        }
      };
      const revealAll = () => { while (shown < beats.length) revealNext(); };
      const nextBtn = h("button", { class: "btn btn-primary", type: "button", onclick: revealNext }, "Continue");
      const allBtn = h("button", { class: "btn btn-quiet", type: "button", onclick: revealAll }, "Show all");
      const hint = h("span", { class: "reveal-hint", "aria-hidden": "true" }, "or press ", h("span", { class: "kbd" }, "Space"));

      function finishReveal() {
        clear(controls);
        renderChoices(scene, after);
      }

      // Tap the scene to reveal the next beat (not when selecting text).
      card.addEventListener("click", (e) => {
        if (shown < beats.length && !(window.getSelection && String(window.getSelection()).length) && !e.target.closest("a,button")) revealNext();
      });
      state.keyHandler = (e) => {
        const t = e.target;
        if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.isContentEditable)) return;
        if ($("#confirm").open || $("#overlay-root").firstChild) return;
        if (shown < beats.length && (e.key === " " || e.key === "ArrowRight" || (e.key === "Enter" && (!t || t.tagName !== "BUTTON" && t.tagName !== "A")))) {
          e.preventDefault(); revealNext(); return;
        }
        if (shown >= beats.length && /^[1-9]$/.test(e.key) && !e.metaKey && !e.ctrlKey && !e.altKey) {
          const btns = after.querySelectorAll(".choice:not([disabled])");
          const b = btns[Number(e.key) - 1];
          if (b) { e.preventDefault(); b.click(); }
        }
      };

      if (!beats.length || reducedMotion()) { revealAll(); return; }
      revealNext();
      if (shown < beats.length) controls.append(nextBtn, hint, allBtn);
    }

    async function reloadScene(notice) {
      const p = obj(await api(`/api/journeys/${enc}/pending`));
      if (p.closed) { go(`#/debrief/${enc}`); return; }
      showScene(obj(p.pending), { notice });
    }

    function renderChoices(scene, slot) {
      const expectedTick = sceneTick;   // the scene this person is answering
      const choices = arr(scene.choices).map(str).filter(Boolean);
      const errEl = h("p", { class: "notice", role: "alert", hidden: true });
      const head2 = h("h2", { class: "kicker", tabindex: "-1" }, "What do you do?");
      const counter = h("p", { class: "counter", "aria-live": "polite" }, "0 / 400");
      const ta = h("textarea", { class: "textarea", id: "own", maxlength: 400, rows: 3, placeholder: "I…", "aria-describedby": "own-help" });
      const doBtn = h("button", { class: "btn btn-primary", type: "submit" }, "Do this");
      const writeForm = h("form", { hidden: true, novalidate: true },
        h("label", { class: "sr-only", for: "own" }, "Write what you do"),
        ta,
        h("div", { class: "btn-row" }, h("span", { class: "reveal-hint", id: "own-help" }, "Up to 400 characters · ", h("span", { class: "kbd" }, "Ctrl"), " + ", h("span", { class: "kbd" }, "Enter"), " to send"), doBtn),
        counter);
      const toggle = h("button", { class: "link-btn write-toggle", type: "button", "aria-expanded": "false", "aria-controls": "own-form" }, choices.length ? "or write your own…" : "Write what you do…");
      writeForm.id = "own-form";
      toggle.addEventListener("click", () => {
        const open = writeForm.hidden;
        writeForm.hidden = !open;
        toggle.setAttribute("aria-expanded", String(open));
        if (open) ta.focus();
      });
      ta.addEventListener("input", () => {
        counter.textContent = `${ta.value.length} / 400`;
        counter.classList.toggle("over", ta.value.length >= 400);
      });
      ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); writeForm.requestSubmit(); } });

      const withTick = (body) => (expectedTick !== null ? Object.assign(body, { expected_tick: expectedTick }) : body);
      const buttons = choices.map((c, k) => h("button", { class: "choice", type: "button", onclick: () => act(withTick({ choice_index: k }), buttons[k]) },
        h("span", { class: "num", "aria-hidden": "true" }, String(k + 1)), h("span", null, c)));
      writeForm.addEventListener("submit", (e) => {
        e.preventDefault();
        const text = ta.value.trim();
        if (!text) { ta.focus(); return; }
        act(withTick({ text }), null);
      });

      const lockAll = (on) => {
        for (const b of buttons) b.disabled = on;
        ta.disabled = on; doBtn.disabled = on; toggle.disabled = on;
      };

      clear(slot).append(h("section", { class: "choices-wrap fade-in", "aria-label": "Your choices" },
        head2,
        choices.length ? h("ul", { class: "choices" }, buttons.map((b) => h("li", null, b))) : null,
        h("div", { class: "write" }, toggle, writeForm),
        errEl));
      if (!choices.length) toggle.click();
      head2.focus({ preventScroll: true });

      async function act(payload, chosenBtn) {
        errEl.hidden = true;
        lockAll(true);
        if (chosenBtn) chosenBtn.classList.add("chosen");
        const thinking = h("div", { class: "thinking", role: "status" }, "The world is answering", h("span", { class: "dots", "aria-hidden": "true" }, h("span"), h("span"), h("span")));
        slot.append(thinking);
        state.keyHandler = null;
        try {
          // `payload` is sent byte-for-byte the same on Retry, including
          // expected_tick, so the server can recognise an already-applied turn
          // (200 with `replayed: true`) or a scene that moved on (409).
          const res = obj(await api(`/api/journeys/${enc}/act`, { method: "POST", body: payload }));
          thinking.remove();
          if (res.safety && obj(res.safety).level === "crisis") {
            state.lastSafety.set(jid, res.safety);
            showSafety(res.safety, { onClose: () => go("#/home") });
            return;
          }
          resolveTurn(res, slot);   // a replay (res.replayed) renders exactly like a fresh turn
        } catch (err) {
          thinking.remove();
          lockAll(false);
          if (chosenBtn) chosenBtn.classList.remove("chosen");
          if (err.handled) return;
          if (err instanceof NetError) {
            // Only offer to resend when the act carries expected_tick — without
            // it a resend could apply the turn twice. Otherwise, re-read where
            // the story stands (a safe GET) instead.
            if (payload.expected_tick !== undefined) report(err, () => act(payload, chosenBtn));
            else toast(err.message, { sticky: true, action: { label: "Check where I am", fn: () => reloadScene("").catch((e) => report(e)) } });
            return;
          }
          if (err.status === 409 && err.code === "scene_changed") {
            const d = obj(err.data);
            const p = d.pending || obj(d.error).pending;
            if (d.closed === true) { go(`#/debrief/${enc}`); return; }
            if (isObj(p)) showScene(p, { notice: SCENE_MOVED });
            else { try { await reloadScene(SCENE_MOVED); } catch (e2) { report(e2); } }
            return;
          }
          if (err.status === 429) {
            errEl.textContent = err.message || DEFAULT_MESSAGES[429];
            errEl.hidden = false;
            lockAll(true);
            slot.append(h("div", { class: "btn-row db-actions" }, h("a", { class: "btn", href: "#/home" }, "Return home")));
            return;
          }
          if (err.status === 409 && /closed/i.test(err.code)) { go(`#/debrief/${enc}`); return; }
          if (err.status === 409 && /paused/i.test(err.code)) { showPaused(obj(err.data).safety); return; }
          errEl.textContent = err.message;
          errEl.hidden = false;
        }
      }
    }

    function resolveTurn(res, slot) {
      const rec = obj(res.record);
      const reactions = arr(rec.npc_events).map(str).filter(Boolean);
      const lesson = str(rec.lesson);
      const concern = obj(res.safety).level === "concern" ? res.safety : null;
      if (num(rec.convergence) !== null) ribbon.update(rec.convergence);
      const contBtn = h("button", { class: "btn btn-primary", type: "button" });
      const box = h("section", { class: "resolve fade-in", "aria-label": "What happened" },
        concernNote(res.safety, { lead: true }),
        actionText(rec.action) ? h("p", { class: "you" }, actionText(rec.action)) : null,
        reactions.length ? h("ul", { class: "reactions" }, reactions.map((r) => h("li", null, cap(r)))) : null,
        lesson ? h("p", { class: "epigraph" }, typo(lesson)) : null,
        h("div", { class: "reveal-row" }, contBtn));
      clear(slot).append(box);
      turn += 1;
      if (res.closed || !res.pending) {
        contBtn.textContent = res.closed ? "The journey has come home — read your debrief" : "Continue";
        contBtn.addEventListener("click", async () => {
          if (res.closed) { go(`#/debrief/${enc}`); return; }
          busy(contBtn, true, "One moment…");
          try {
            const p = obj(await api(`/api/journeys/${enc}/pending`));
            if (p.closed) go(`#/debrief/${enc}`); else showScene(obj(p.pending), { safety: concern });
          } catch (err) { busy(contBtn, false); report(err); }
        });
      } else {
        contBtn.textContent = "Continue the journey";
        contBtn.addEventListener("click", () => showScene(obj(res.pending), { safety: concern }));
      }
      state.keyHandler = (e) => {
        const t = e.target;
        if (t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT")) return;
        if (e.key === " " || e.key === "ArrowRight") { e.preventDefault(); contBtn.click(); }
      };
      const r = box.getBoundingClientRect();
      if (r.top > window.innerHeight * 0.6) box.scrollIntoView({ block: "start", behavior: reducedMotion() ? "auto" : "smooth" });
      contBtn.focus({ preventScroll: true });
    }
  }

  // ── debrief ─────────────────────────────────────────────────
  function chapterItem(line) {
    // Current format: "Chapter One, The Call — An unmarked threshold. …"
    const n = /^Chapter\s+(\w+),\s*(.+?)\s+—\s+(.*)$/.exec(line);
    if (n) return h("li", null, h("strong", null, n[2]), h("br"), cap(n[3]));
    const m = /^Chapter\s+\d+,\s*(.+?)\s*\((cycles?\s[^)]*)\)\s*(.*)$/i.exec(line);
    if (!m) return h("li", null, line);
    const rest = m[3].replace(/^at\s+/i, "");
    return h("li", null, h("strong", null, m[1]), h("span", { class: "muted" }, ` · ${m[2].replace("-", "–")}`), h("br"),
      rest ? cap(rest) : "");
  }
  function companionItem(line) {
    const m = /^(.+?),\s*(the\s+[a-z ]+?)\s+[—-]\s+(.*)$/i.exec(line);
    if (!m) return h("li", null, line);
    const role = m[2].replace(/^the\s+/i, "").toLowerCase().trim();
    const arch = ROLE_TO_ARCH[role] || role.replace(/\s+/g, "_");
    return h("li", { class: voiceClass(arch) }, h("span", { class: "who" }, m[1]), h("span", null, `${cap(m[2])} — ${m[3]}`));
  }

  async function viewDebrief(ctx, [jid]) {
    jid = str(jid);
    const enc = encodeURIComponent(jid);
    ctx.mount(loading("Writing your debrief…"), "Debrief");
    let res;
    try {
      res = obj(await api(`/api/journeys/${enc}/debrief`));
    } catch (err) {
      if (err.status === 409) {
        ctx.mount(h("div", { class: "error-page col" }, h("h1", null, "The journey isn't over yet"),
          h("p", null, err.message),
          h("div", { class: "btn-row db-foot" }, h("a", { class: "btn btn-primary", href: `#/play/${enc}` }, "Return to it"))), "Debrief");
        return;
      }
      throw err;
    }
    if (!ctx.alive()) return;
    const d = obj(res.debrief);
    const md = str(res.markdown);
    const title = str(d.title) || "Your journey";
    const items = (xs) => arr(xs).map(typo).filter(Boolean);
    const sec = (head, content) => content ? h("section", { class: "db-sec" }, h("h2", null, head), content) : null;
    const listOr = (xs, cls, mapper) => { xs = items(xs); return xs.length ? h(cls === "ol" ? "ol" : "ul", { class: mapper.cls }, xs.map(mapper.fn)) : null; };

    const dlBtn = h("button", { class: "btn", type: "button", onclick: () => download(`${slug(title)}.md`, md || `# ${title}\n`, "text/markdown;charset=utf-8") },
      svg("svg", { viewBox: "0 0 20 20", "aria-hidden": "true" }, svg("path", { d: "M10 3v10m0 0-4-4m4 4 4-4M3 13v3a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-3" })),
      "Download (.md)");
    const symbols = items(d.recurring_symbols);
    const node = h("article", { class: "debrief fade-in" },
      h("header", { class: "db-head" },
        h("p", { class: "kicker" }, "Debrief"),
        h("h1", { class: "db-title" }, title),
        h("div", { class: "btn-row db-actions" }, dlBtn, h("a", { class: "btn btn-quiet", href: "#/home" }, "Home"))),
      d.arc_summary ? h("p", { class: "db-summary" }, typo(d.arc_summary)) : null,
      sec("The chapters", listOr(d.chapters, "ol", { cls: "db-chapters", fn: chapterItem })),
      sec("Turning points", listOr(d.turning_points, "ul", { cls: "db-timeline", fn: (x) => h("li", null, x) })),
      sec("What you learned", listOr(d.lessons, "ul", { cls: "db-lessons", fn: (x) => h("li", null, x) })),
      sec("How you changed", listOr(d.how_you_changed, "ul", { cls: "db-list", fn: (x) => h("li", null, x) })),
      sec("Who walked with you", listOr(d.companions, "ul", { cls: "db-companions", fn: companionItem })),
      symbols.length ? sec("Symbols that kept returning", h("div", { class: "tags" }, symbols.map((s) => h("span", { class: "tag" }, s)))) : null,
      sec("Questions to carry home", listOr(d.open_questions, "ul", { cls: "db-questions", fn: (x) => h("li", null, x) })),
      d.closing_image ? h("blockquote", { class: "pull-quote" }, h("p", null, typo(d.closing_image))) : null,
      h("div", { class: "btn-row db-foot" },
        h("a", { class: "btn btn-primary", href: "#/home" }, "Return home"),
        h("button", { class: "btn btn-quiet", type: "button", onclick: () => window.print() }, "Print")));
    ctx.mount(node, title);
  }

  // ── settings ────────────────────────────────────────────────
  async function viewSettings(ctx) {
    ctx.mount(loading(), "Settings");
    let ident = {};
    if (state.me && state.me.has_identity) {
      try { ident = obj(obj(await api("/api/identity")).identity); } catch (err) { if (err.status !== 404) throw err; }
    }
    if (!ctx.alive()) return;
    let forbidden = arr(ident.forbidden_themes).map(str).filter(Boolean);
    let dirty = false;

    const chips = h("div", { class: "tags", "aria-live": "polite" });
    const saveBtn = h("button", { class: "btn btn-primary", type: "button", disabled: true }, "Save");
    const savedLbl = h("span", { class: "saved", role: "status" });
    const addInput = h("input", { class: "input", id: "never-add", type: "text", maxlength: 60, placeholder: "e.g. drowning", autocomplete: "off" });
    const renderChips = () => {
      clear(chips);
      if (!forbidden.length) chips.append(h("p", { class: "muted small" }, "Nothing on the list."));
      forbidden.forEach((t, k) => chips.append(h("span", { class: "removable" }, h("span", { class: "u-text" }, t),
        h("button", { type: "button", "aria-label": `Remove ${t}`, onclick: () => { forbidden.splice(k, 1); markDirty(); renderChips(); addInput.focus(); } }, "×"))));
    };
    const markDirty = () => { dirty = true; saveBtn.disabled = false; savedLbl.textContent = ""; };
    const add = () => {
      const parts = addInput.value.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean);
      let added = false;
      for (const p of parts) if (!forbidden.includes(p) && forbidden.length < 50) { forbidden.push(p.slice(0, 60)); added = true; }
      addInput.value = "";
      if (added) { markDirty(); renderChips(); }
    };
    addInput.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } });
    const save = async () => {
      busy(saveBtn, true, "Saving…");
      try {
        const r = obj(await api("/api/identity", { method: "PATCH", body: { forbidden_themes: forbidden } }));
        forbidden = arr(obj(r.identity).forbidden_themes).map(str).filter(Boolean);
        renderChips();
        dirty = false;
        busy(saveBtn, false);
        saveBtn.disabled = true;
        savedLbl.textContent = "Saved. Future scenes will keep these out.";
      } catch (err) { busy(saveBtn, false); report(err, err instanceof NetError ? save : null); }
    };
    saveBtn.addEventListener("click", save);

    const exportBtn = h("button", { class: "btn", type: "button" }, "Download my data (.json)");
    exportBtn.addEventListener("click", async function doExport() {
      busy(exportBtn, true, "Preparing…");
      try {
        const res = await api("/api/me/export", { raw: true });
        const blob = await res.blob();
        const cd = res.headers.get("content-disposition") || "";
        const m = /filename="?([^";]+)"?/i.exec(cd);
        const name = m && /^[\w.\-]+$/.test(m[1]) ? m[1] : `ana-export-${new Date().toISOString().slice(0, 10)}.json`;
        download(name, blob);
        toast("Your data is downloading.");
      } catch (err) { report(err, err instanceof NetError ? doExport : null); }
      busy(exportBtn, false);
    });

    const theme = themePref();
    const themeSeg = h("div", { class: "seg", role: "radiogroup", "aria-label": "Appearance" },
      [["system", "System"], ["light", "Light"], ["dark", "Dark"]].map(([v, l]) => h("label", null,
        h("input", { type: "radio", name: "theme", value: v, checked: theme === v, onchange: () => setThemePref(v) }),
        h("span", null, l))));

    const pw = h("input", { class: "input", id: "del-pw", type: "password", autocomplete: "current-password" });
    const delErr = h("p", { class: "form-error", role: "alert", hidden: true });
    const delBtn = h("button", { class: "btn btn-danger-quiet", type: "submit" }, "Delete my account");
    const delForm = h("form", { novalidate: true },
      h("label", { class: "field", for: "del-pw" }, h("span", { class: "label" }, "Confirm with your password"), pw),
      delErr,
      h("div", { class: "btn-row" }, delBtn));
    delForm.style.setProperty("margin-top", "4px");
    delForm.addEventListener("submit", async (e) => {
      e.preventDefault();
      delErr.hidden = true;
      if (!pw.value) { delErr.textContent = "Please enter your password to confirm."; delErr.hidden = false; pw.focus(); return; }
      const ok = await confirmDialog({
        title: "Delete everything?",
        body: "Your account, your self, every journey, debrief and memory will be permanently erased. This cannot be undone.",
        ok: "Delete forever", cancel: "Keep my account", danger: true,
      });
      if (!ok) return;
      busy(delBtn, true, "Deleting…");
      try {
        await api("/api/me", { method: "DELETE", body: { password: pw.value }, expect401: true });
        obClear();
        state.me = null;
        state.flash = "Your account and everything in it has been deleted.";
        updateNav();
        go("#/");
      } catch (err) {
        busy(delBtn, false);
        if (err instanceof NetError) { report(err); return; }
        delErr.textContent = err.status === 401 || err.status === 403 ? (err.message || "That password isn't right.") : err.message;
        delErr.hidden = false;
      }
    });

    const logoutBtn = h("button", { class: "btn", type: "button" }, "Log out");
    logoutBtn.addEventListener("click", async () => {
      busy(logoutBtn, true, "Logging out…");
      try { await api("/api/auth/logout", { method: "POST", expect401: true }); } catch (err) { if (err instanceof NetError) { busy(logoutBtn, false); report(err); return; } }
      obClear();
      state.me = null;
      updateNav();
      go("#/");
    });

    const node = h("div", { class: "settings fade-in" },
      h("h1", null, "Settings"),
      state.me && state.me.user ? h("p", { class: "muted" }, `Signed in as ${str(obj(state.me.user).email)}`) : null,
      state.me && state.me.has_identity ? h("section", { class: "card set-sec", "aria-labelledby": "never-h" },
        h("h2", { id: "never-h" }, "Never show me"),
        h("p", { class: "desc" }, "Themes, images or words ANA must keep out of every scene, dialogue and debrief."),
        h("div", { class: "field" }, chips),
        h("div", { class: "add-row" },
          h("label", { class: "sr-only", for: "never-add" }, "Add something ANA must never show"),
          addInput, h("button", { class: "btn", type: "button", onclick: add }, "Add")),
        h("div", { class: "btn-row" }, saveBtn, savedLbl)) : null,
      h("section", { class: "card set-sec", "aria-labelledby": "look-h" },
        h("h2", { id: "look-h" }, "Appearance"),
        h("p", { class: "desc" }, "Stored only in this browser."),
        themeSeg),
      h("section", { class: "card set-sec", "aria-labelledby": "data-h" },
        h("h2", { id: "data-h" }, "Your data"),
        h("p", { class: "desc" }, "Everything ANA holds about you — your self, journeys, debriefs and memories — as one JSON file."),
        h("div", { class: "btn-row" }, exportBtn),
        h("hr", { class: "rule" }),
        h("h2", null, "Session"),
        h("p", { class: "desc" }, "Log out of ANA on this device."),
        h("div", { class: "btn-row" }, logoutBtn)),
      h("section", { class: "card set-sec danger", "aria-labelledby": "del-h" },
        h("h2", { id: "del-h" }, "Delete account"),
        h("p", { class: "desc" }, "Permanently erase your account and everything in it, including the memories your journeys left behind."),
        delForm));
    if (!ctx.mount(node, "Settings")) return;
    renderChips();
    window.addEventListener("beforeunload", (e) => { if (dirty && location.hash === "#/settings") { e.preventDefault(); } }, { once: true });
  }

  // ── boot ────────────────────────────────────────────────────
  async function boot() {
    try {
      state.me = obj(await api("/api/me", { expect401: true }));
    } catch (err) {
      state.me = null;
      if (err instanceof NetError) {
        updateNav();
        clear($("#view")).append(errorPage("We couldn't reach ANA", err.message, null, () => boot()));
        return;
      }
    }
    updateNav();
    route();
  }
  boot();
})();
