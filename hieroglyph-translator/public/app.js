(() => {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const MAX_SIDE = 1600; // px; keeps uploads small and model costs low
  const HISTORY_KEY = 'hieroglyph-history-v1';
  const HISTORY_MAX = 20;

  // Build DOM from model output with textContent only — never innerHTML.
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') node.className = v;
      else if (k === 'style') node.style.cssText = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  // ---------- Tabs ----------
  const tabs = document.querySelectorAll('.tab');
  function showTab(name) {
    tabs.forEach((t) => {
      const on = t.dataset.tab === name;
      t.classList.toggle('active', on);
      t.setAttribute('aria-selected', on);
    });
    document.querySelectorAll('.panel').forEach((p) => (p.hidden = p.id !== `tab-${name}`));
    if (name !== 'scan') stopCamera();
    if (name === 'history') renderHistory();
  }
  tabs.forEach((t) => t.addEventListener('click', () => showTab(t.dataset.tab)));

  // ---------- Config badge ----------
  fetch('api/config')
    .then((r) => r.json())
    .then(({ provider, model }) => {
      const badge = $('#model-badge');
      badge.textContent = provider === 'demo' ? 'Demo mode' : model;
      badge.title = provider === 'demo' ? 'No API key configured — results are a sample' : `Provider: ${provider}`;
      badge.classList.toggle('demo', provider === 'demo');
      badge.hidden = false;
    })
    .catch(() => {});

  // ---------- Image capture ----------
  const video = $('#video');
  const preview = $('#preview');
  const empty = $('#viewer-empty');
  const btnCamera = $('#btn-camera');
  const btnSnap = $('#btn-snap');
  const btnTranslate = $('#btn-translate');
  let stream = null;
  let imageDataUrl = null;

  function setImage(dataUrl) {
    imageDataUrl = dataUrl;
    preview.src = dataUrl;
    preview.hidden = false;
    empty.hidden = true;
    btnTranslate.disabled = false;
  }

  // Downscale to MAX_SIDE and re-encode as JPEG.
  function drawToDataUrl(source, w, h) {
    const scale = Math.min(1, MAX_SIDE / Math.max(w, h));
    const canvas = $('#canvas');
    canvas.width = Math.round(w * scale);
    canvas.height = Math.round(h * scale);
    canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.88);
  }

  $('#file').addEventListener('change', async (e) => {
    const file = e.target.files?.[0];
    e.target.value = '';
    if (!file) return;
    stopCamera();
    try {
      const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
      setImage(drawToDataUrl(bitmap, bitmap.width, bitmap.height));
      bitmap.close?.();
    } catch {
      showError('Could not read that image. Try a JPEG or PNG.');
    }
  });

  async function startCamera() {
    if (!navigator.mediaDevices?.getUserMedia) {
      showError('Live camera is not available in this browser — use "Upload / take photo" instead.');
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1440 } },
        audio: false,
      });
      video.srcObject = stream;
      await video.play();
      video.hidden = false;
      preview.hidden = true;
      empty.hidden = true;
      btnSnap.hidden = false;
      btnCamera.textContent = 'Stop camera';
    } catch {
      showError('Camera permission was denied or no camera was found. You can still upload a photo.');
    }
  }

  function stopCamera() {
    stream?.getTracks().forEach((t) => t.stop());
    stream = null;
    video.hidden = true;
    btnSnap.hidden = true;
    btnCamera.textContent = 'Live camera';
    if (!imageDataUrl) empty.hidden = false;
    else preview.hidden = false;
  }

  btnCamera.addEventListener('click', () => (stream ? stopCamera() : startCamera()));
  btnSnap.addEventListener('click', () => {
    if (!video.videoWidth) return;
    setImage(drawToDataUrl(video, video.videoWidth, video.videoHeight));
    stopCamera();
  });

  btnTranslate.addEventListener('click', () => {
    if (imageDataUrl) translate({ image: imageDataUrl, hint: $('#hint').value });
  });

  // ---------- Text input + sign keyboard ----------
  const textInput = $('#text-input');
  const keyboard = $('#keyboard');
  for (const s of window.SIGNS) {
    keyboard.append(
      el('button', {
        class: 'key',
        type: 'button',
        title: `${s.gardiner} · ${s.description} · ${s.translit}`,
        'aria-label': `${s.description}, ${s.gardiner}`,
        onclick: () => insertAtCursor(textInput, s.glyph),
      }, s.glyph),
    );
  }
  function insertAtCursor(input, str) {
    const { selectionStart: a, selectionEnd: b, value } = input;
    input.value = value.slice(0, a) + str + value.slice(b);
    input.selectionStart = input.selectionEnd = a + str.length;
    input.focus({ preventScroll: true });
  }
  $('#btn-clear').addEventListener('click', () => (textInput.value = ''));
  $('#btn-translate-text').addEventListener('click', () => {
    const text = textInput.value.trim();
    if (!text) return showError('Type or tap some hieroglyphs first.');
    translate({ text });
  });

  // ---------- Sign list ----------
  let signCat = 'all';
  function renderSigns() {
    const q = $('#sign-search').value.trim().toLowerCase();
    const list = $('#sign-list');
    list.replaceChildren(
      ...window.SIGNS.filter((s) => signCat === 'all' || s.category === signCat)
        .filter((s) => !q || [s.gardiner, s.description, s.translit, s.meaning].some((f) => f.toLowerCase().includes(q)))
        .map((s) =>
          el('div', { class: 'sign' },
            el('div', { class: 'sign-glyph' }, s.glyph),
            el('div', {},
              el('div', { class: 'sign-title' }, s.description, el('code', {}, s.gardiner)),
              el('div', { class: 'sign-meta' }, el('i', {}, s.translit), ' — ', s.meaning),
            ),
          ),
        ),
    );
    if (!list.children.length) list.append(el('p', { class: 'muted' }, 'No signs match.'));
  }
  $('#sign-search').addEventListener('input', renderSigns);
  $('#sign-filters').addEventListener('click', (e) => {
    const chip = e.target.closest('.chip');
    if (!chip) return;
    signCat = chip.dataset.cat;
    document.querySelectorAll('#sign-filters .chip').forEach((c) => c.classList.toggle('active', c === chip));
    renderSigns();
  });
  renderSigns();

  // ---------- Translate ----------
  const resultBox = $('#result');
  let busy = false;

  async function translate(payload) {
    if (busy) return;
    busy = true;
    setBusy(true);
    resultBox.hidden = false;
    resultBox.replaceChildren(
      el('div', { class: 'loading' }, el('span', { class: 'spinner' }, '𓂀'), 'Reading the signs…'),
    );
    resultBox.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    try {
      const res = await fetch('api/translate', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
      renderResult(data.result);
      saveHistory(payload, data.result);
    } catch (err) {
      showError(err.message || 'Something went wrong.');
    } finally {
      busy = false;
      setBusy(false);
    }
  }

  function setBusy(on) {
    btnTranslate.disabled = on || !imageDataUrl;
    $('#btn-translate-text').disabled = on;
  }

  function showError(msg) {
    resultBox.hidden = false;
    resultBox.replaceChildren(el('p', { class: 'error' }, msg));
  }

  function confidenceColor(c) {
    return c >= 0.7 ? 'var(--good)' : c >= 0.4 ? 'var(--warn)' : 'var(--bad)';
  }

  function renderResult(r) {
    if (!r.detected) {
      resultBox.replaceChildren(
        el('h2', {}, 'No Egyptian text found'),
        el('p', {}, r.notes || 'Try a closer, sharper photo of the inscription.'),
      );
      return;
    }
    const pct = Math.round(r.confidence * 100);
    const plainText = [r.translation, r.transliteration && `(${r.transliteration})`].filter(Boolean).join(' ');

    // Sections are optional, so children mix nodes, arrays and falsy values; flatten them first.
    const parts = [
      el('h2', {}, 'Translation'),
      el('p', { class: 'translation' }, r.translation || '—'),
      el('div', { class: 'conf' },
        el('span', {}, 'Confidence'),
        el('div', { class: 'conf-bar' },
          el('div', { class: 'conf-fill', style: `width:${pct}%;background:${confidenceColor(r.confidence)}` }),
        ),
        el('span', {}, `${pct}%`),
      ),
      r.transliteration && [
        el('h2', {}, 'Transliteration'),
        el('p', { class: 'translit' }, r.transliteration),
        r.literal_gloss && el('p', { class: 'gloss' }, r.literal_gloss),
      ],
      r.glyphs.length > 0 && [
        el('h2', {}, `Signs (${r.glyphs.length})`),
        el('div', { class: 'glyph-row' },
          r.glyphs.map((g) =>
            el('div', { class: `glyph-card ${g.role}`, title: g.role },
              el('div', { class: 'glyph' }, g.sign || '?'),
              el('div', { class: 'ph' }, g.phonetic || '—'),
              g.gardiner && el('div', { class: 'gd' }, g.gardiner),
              g.description && el('div', { class: 'ds' }, g.description),
            ),
          ),
        ),
      ],
      r.context && [el('h2', {}, 'What this is'), el('p', {}, r.context)],
      r.notes && [el('h2', {}, 'Notes'), el('p', { class: 'notes' }, r.notes)],
      el('div', { class: 'meta-row' },
        el('span', { class: 'pill' }, r.script),
        el('span', { class: 'pill' }, `reads ${r.reading_direction}`),
      ),
      el('div', { class: 'result-actions' },
        el('button', { class: 'btn', type: 'button', onclick: (e) => copy(plainText, e.currentTarget) }, 'Copy'),
        navigator.share &&
          el('button', {
            class: 'btn', type: 'button',
            onclick: () => navigator.share({ title: 'Hieroglyph translation', text: plainText }).catch(() => {}),
          }, 'Share'),
      ),
    ];
    resultBox.replaceChildren(...parts.flat().filter(Boolean));
  }

  function copy(text, btn) {
    navigator.clipboard?.writeText(text).then(() => {
      btn.textContent = 'Copied';
      setTimeout(() => (btn.textContent = 'Copy'), 1500);
    });
  }

  // ---------- History (this device only) ----------
  function loadHistory() {
    try {
      return JSON.parse(localStorage.getItem(HISTORY_KEY)) || [];
    } catch {
      return [];
    }
  }

  // Store a tiny thumbnail rather than the full photo so localStorage doesn't fill up.
  function thumbnail(dataUrl) {
    return new Promise((resolve) => {
      const img = new Image();
      img.onload = () => {
        const c = document.createElement('canvas');
        const s = 128 / Math.max(img.width, img.height);
        c.width = Math.round(img.width * s);
        c.height = Math.round(img.height * s);
        c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
        resolve(c.toDataURL('image/jpeg', 0.7));
      };
      img.onerror = () => resolve(null);
      img.src = dataUrl;
    });
  }

  async function saveHistory(payload, result) {
    if (!result.detected) return;
    const entry = {
      at: Date.now(),
      thumb: payload.image ? await thumbnail(payload.image) : null,
      text: payload.text || null,
      result,
    };
    const items = [entry, ...loadHistory()].slice(0, HISTORY_MAX);
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(items));
    } catch {
      /* storage full or blocked — history is a convenience, skip it */
    }
  }

  function renderHistory() {
    const items = loadHistory();
    $('#history-empty').hidden = items.length > 0;
    $('#history-list').replaceChildren(
      ...items.map((h) =>
        el('button', {
          class: 'history-item', type: 'button',
          onclick: () => {
            resultBox.hidden = false;
            renderResult(h.result);
            resultBox.scrollIntoView({ behavior: 'smooth' });
          },
        },
          h.thumb
            ? el('img', { src: h.thumb, alt: '' })
            : el('div', { class: 'thumb-glyph' }, Array.from(h.text || '𓂀')[0]),
          el('div', {},
            el('div', { class: 't' }, h.result.translation || h.result.transliteration || 'Untitled'),
            el('div', { class: 'd' }, new Date(h.at).toLocaleString()),
          ),
        ),
      ),
    );
  }
})();
