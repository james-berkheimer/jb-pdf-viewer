// Reader: page layout, navigation, sidebar panels, progress.
import { $, $$, el, api, clamp, debounce } from './util.js';
import { loadWords, paint, termsFrom } from './textlayer.js';
import { Magnifier } from './magnifier.js';
import { Keymap, pretty as prettyKey } from './keymap.js';
import { ShortcutsSheet } from './shortcuts.js';

const GAP = 2;            // px between the two halves of a spread
const PAD = 18;           // .pages padding, kept in sync with reader.css
const ZOOM_STEPS = [0.5, 0.65, 0.8, 1, 1.25, 1.5, 2, 2.5, 3];
const PREFS = 'pdfv.prefs';

const docId = location.pathname.split('/').pop();

const S = {
  doc: null,
  page: 1,
  mode: 'spread',        // single | spread | scroll
  zoom: { kind: 'fit', value: 1 },
  coverAlone: true,      // page 1 sits by itself, as on a real magazine
  surface: 'dark',
  side: true,
  tab: 'toc',
  terms: [],
  leaves: new Map(),     // page number -> leaf element (scroll mode)
  magnifier: null,
  keymap: null,
  shortcuts: null,
};

// ---------------------------------------------------------------- prefs

/** Phone-width: a two-page spread is unreadable and the sidebar covers the page. */
const isNarrow = () => window.matchMedia('(max-width: 860px)').matches;

function loadPrefs() {
  try {
    const p = JSON.parse(localStorage.getItem(PREFS) || '{}');
    if (p.mode) S.mode = p.mode;
    if (p.zoom) S.zoom = p.zoom;
    if (typeof p.coverAlone === 'boolean') S.coverAlone = p.coverAlone;
    if (p.surface) S.surface = p.surface;
    if (typeof p.side === 'boolean') S.side = p.side;
  } catch { /* first run */ }

  if (isNarrow()) {
    // Override for this session only; the desktop preference stays on disk.
    S.mode = S.mode === 'scroll' ? 'scroll' : 'single';
    S.side = false;
  }
}

const savePrefs = debounce(() => {
  let stored = {};
  try { stored = JSON.parse(localStorage.getItem(PREFS) || '{}'); } catch { /* ignore */ }
  const layout = isNarrow()
    ? { mode: stored.mode, side: stored.side }   // keep whatever the desktop chose
    : { mode: S.mode, side: S.side };
  localStorage.setItem(PREFS, JSON.stringify({
    ...stored, ...layout, zoom: S.zoom,
    coverAlone: S.coverAlone, surface: S.surface,
  }));
}, 300);

const saveProgress = debounce(() => {
  if (!S.doc) return;
  fetch(`/api/progress/${docId}`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ page: S.page }),
  }).catch(() => { /* reading must not break if the write fails */ });
}, 900);

// ---------------------------------------------------------------- geometry

/** Which pages sit side by side when `page` is showing. */
function spreadFor(page) {
  if (S.mode !== 'spread') return [page];
  if (S.coverAlone) {
    if (page <= 1) return [1];
    const start = page % 2 === 0 ? page : page - 1;
    return [start, start + 1].filter((p) => p <= S.doc.pages);
  }
  const start = page % 2 === 1 ? page : page - 1;
  return [start, start + 1].filter((p) => p <= S.doc.pages);
}

function aspect() {
  const { page_w: w, page_h: h } = S.doc;
  return w && h ? w / h : 0.773;
}

/** CSS-pixel width for a single page at the current zoom. */
function leafWidth(count) {
  const stage = $('#stage');
  const availW = stage.clientWidth - PAD * 2;
  const availH = stage.clientHeight - PAD * 2;
  const perPageW = (availW - GAP * (count - 1)) / count;

  if (S.zoom.kind === 'width') return Math.max(120, perPageW);
  const fitH = availH * aspect();
  const fit = Math.max(120, Math.min(perPageW, fitH));
  if (S.zoom.kind === 'fit') return fit;
  return Math.max(120, perPageW * S.zoom.value);
}

/** Snap to a width the server caches, allowing for retina displays. */
function requestWidth(cssWidth) {
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const want = cssWidth * dpr;
  const widths = S.doc.widths;
  return widths.find((w) => w >= want) ?? widths[widths.length - 1];
}

// ---------------------------------------------------------------- leaves

function makeLeaf(page, cssWidth) {
  const leaf = el('div', {
    class: 'leaf',
    dataset: { page },
    style: `width:${Math.round(cssWidth)}px;aspect-ratio:${aspect()}`,
  },
    el('div', { class: 'placeholder' }, el('div', { class: 'spin' })),
    el('div', { class: 'textlayer' }),
    el('span', { class: 'pgno', text: `p. ${page}` }),
  );
  return leaf;
}

function fillLeaf(leaf, page, cssWidth) {
  const w = requestWidth(cssWidth);
  if (leaf.dataset.loadedWidth === String(w) && leaf.querySelector('img')) {
    repaintText(leaf, page, cssWidth);
    return;
  }
  leaf.dataset.loadedWidth = String(w);

  const img = el('img', {
    src: `/api/doc/${docId}/page/${page}?w=${w}`,
    alt: `Page ${page}`,
    decoding: 'async',
  });
  img.addEventListener('load', () => {
    leaf.classList.add('ready');
    // Aspect from the real image beats the whole-document guess.
    leaf.style.aspectRatio = `${img.naturalWidth} / ${img.naturalHeight}`;
    repaintText(leaf, page, cssWidth);
    syncPanning();
  }, { once: true });
  img.addEventListener('error', () => {
    leaf.classList.add('ready');
    leaf.querySelector('.placeholder')?.replaceChildren(
      el('span', { style: 'color:var(--fg-3);font-size:12px', text: `Page ${page} failed` }));
  }, { once: true });

  const old = leaf.querySelector('img');
  if (old) old.replaceWith(img); else leaf.prepend(img);
}

async function repaintText(leaf, page, cssWidth) {
  const host = leaf.querySelector('.textlayer');
  if (!host) return;
  const data = await loadWords(docId, page);
  if (!data) return;
  const w = leaf.querySelector('img')?.clientWidth || cssWidth;
  paint(host, data, w, S.terms);
}

// ---------------------------------------------------------------- render

function render() {
  const body = document.body;
  body.dataset.mode = S.mode;
  body.dataset.side = S.side ? 'open' : 'closed';
  body.dataset.surface = S.surface;
  body.dataset.find = S.terms.length ? 'on' : 'off';

  if (S.mode === 'scroll') renderScroll();
  else renderPaged();

  syncChrome();
  savePrefs();
  if (S.magnifier?.armed) S.magnifier.preload(visiblePages());
}

function renderPaged() {
  const host = $('#pages');
  const pages = spreadFor(S.page);
  const w = leafWidth(pages.length);

  // Reuse leaves when the same pages are already on screen so a resize does
  // not flash the whole spread away.
  const existing = new Map(
    $$('.leaf', host).map((n) => [Number(n.dataset.page), n]));
  const next = pages.map((p) => existing.get(p) ?? makeLeaf(p, w));
  host.replaceChildren(...next);

  next.forEach((leaf, i) => {
    leaf.style.width = `${Math.round(w)}px`;
    fillLeaf(leaf, pages[i], w);
  });
  S.leaves = new Map(pages.map((p, i) => [p, next[i]]));

  $('#stage').scrollTop = 0;
}

let scrollObserver = null;

function renderScroll() {
  const host = $('#pages');
  const w = leafWidth(1);

  if (host.dataset.built !== `${S.doc.pages}` ) {
    host.replaceChildren();
    S.leaves.clear();
    for (let p = 1; p <= S.doc.pages; p++) {
      const leaf = makeLeaf(p, w);
      host.append(leaf);
      S.leaves.set(p, leaf);
    }
    host.dataset.built = String(S.doc.pages);
    observeScroll();
  }
  for (const [, leaf] of S.leaves) leaf.style.width = `${Math.round(w)}px`;
  loadVisible(w);
}

function observeScroll() {
  scrollObserver?.disconnect();
  const stage = $('#stage');

  // Load pages a screen ahead of the viewport.
  const loader = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const page = Number(e.target.dataset.page);
      fillLeaf(e.target, page, e.target.clientWidth);
    }
  }, { root: stage, rootMargin: '150% 0px' });

  // Track which page is centred, for the scrubber and progress.
  const tracker = new IntersectionObserver((entries) => {
    let best = null;
    for (const e of entries) {
      if (e.isIntersecting && (!best || e.intersectionRatio > best.intersectionRatio)) {
        best = e;
      }
    }
    if (!best) return;
    const page = Number(best.target.dataset.page);
    if (page !== S.page) {
      S.page = page;
      syncChrome();
      saveProgress();
    }
  }, { root: stage, threshold: [0.25, 0.5, 0.75], rootMargin: '-35% 0px -35% 0px' });

  for (const [, leaf] of S.leaves) { loader.observe(leaf); tracker.observe(leaf); }
  scrollObserver = { disconnect: () => { loader.disconnect(); tracker.disconnect(); } };
}

function loadVisible(w) {
  const leaf = S.leaves.get(S.page);
  if (leaf) fillLeaf(leaf, S.page, w);
}

// ---------------------------------------------------------------- chrome

/**
 * Mark the body while the stage is pannable. The click-to-turn zones are
 * absolutely positioned inside the scroller, so they have to be hidden once
 * the page is larger than the viewport or they drift across the content.
 */
function syncPanning() {
  const stage = $('#stage');
  const pannable = stage.scrollWidth > stage.clientWidth + 1
                || stage.scrollHeight > stage.clientHeight + 1;
  const next = pannable ? 'on' : 'off';
  if (document.body.dataset.panning !== next) document.body.dataset.panning = next;
  return pannable;
}

const ICON_X = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
  + 'stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>';

/** A visible way out of the magnifier, which is otherwise a hidden mode. */
function renderChips() {
  const host = $('#chips');
  const chips = [];

  if (S.magnifier?.armed) {
    chips.push(el('div', { class: 'chip accent' },
      el('strong', { text: 'Magnifier' }),
      el('span', { class: 'hint', text: 'click a spot to zoom · wheel to size' }),
      el('button', {
        class: 'x', title: 'Turn off the magnifier (m or Esc)',
        'aria-label': 'Turn off the magnifier', html: ICON_X,
        onclick: () => S.magnifier.disarm(),
      })));
  }

  host.replaceChildren(...chips);
}

function syncChrome() {
  const total = S.doc.pages;
  $('#pg-input').value = String(S.page);
  $('#pg-total').textContent = `/ ${total}`;
  const scrub = $('#scrub');
  scrub.max = String(total);
  if (document.activeElement !== scrub) scrub.value = String(S.page);

  const pages = spreadFor(S.page);
  const first = pages[0];
  const last = pages[pages.length - 1];
  $('#p-prev').disabled = first <= 1;
  $('#p-next').disabled = last >= total;

  for (const b of $$('.seg [data-mode]')) {
    b.setAttribute('aria-pressed', String(b.dataset.mode === S.mode));
    b.classList.toggle('active', b.dataset.mode === S.mode);
  }
  $('#z-label').textContent =
    S.zoom.kind === 'fit' ? 'Fit' :
    S.zoom.kind === 'width' ? 'Width' : `${Math.round(S.zoom.value * 100)}%`;
  $('#t-side').setAttribute('aria-pressed', String(S.side));

  const sub = [];
  if (S.doc.group_name) sub.push(S.doc.group_name);
  sub.push(`${total} pages`);
  if (S.doc.toc_count) sub.push(`${S.doc.toc_count} articles`);
  if (!S.doc.has_text) sub.push('no text layer');
  $('#doc-sub').textContent = sub.join(' · ');

  markActiveToc();
  markActiveThumb();
  renderChips();
  requestAnimationFrame(syncPanning);
}

// ---------------------------------------------------------------- navigation

function goTo(page, { smooth = true, fromSidebar = false } = {}) {
  const target = clamp(Math.round(page) || 1, 1, S.doc.pages);
  if (fromSidebar && isNarrow() && S.side) { S.side = false; render(); }
  if (S.mode === 'scroll') {
    S.page = target;
    const leaf = S.leaves.get(target);
    fillLeaf(leaf, target, leaf?.clientWidth || leafWidth(1));
    leaf?.scrollIntoView({ block: 'start', behavior: smooth ? 'smooth' : 'auto' });
    syncChrome();
    saveProgress();
    return;
  }
  if (spreadFor(S.page).includes(target) && S.leaves.has(target)) {
    S.page = target;
    syncChrome();
    saveProgress();
    return;
  }
  S.page = target;
  renderPaged();
  syncChrome();
  saveProgress();
}

function step(dir) {
  if (S.mode === 'scroll') {
    $('#stage').scrollBy({ top: $('#stage').clientHeight * 0.9 * dir, behavior: 'smooth' });
    return;
  }
  const pages = spreadFor(S.page);
  const jump = S.mode === 'spread' ? pages.length : 1;
  goTo(dir > 0 ? pages[pages.length - 1] + 1 : pages[0] - jump);
}

function setMode(mode) {
  if (mode === S.mode) return;
  S.mode = mode;
  $('#pages').dataset.built = '';   // force a rebuild when entering scroll
  $('#pages').replaceChildren();
  scrollObserver?.disconnect();
  S.leaves.clear();
  render();
  if (S.mode === 'scroll') goTo(S.page, { smooth: false });
}

function zoomBy(dir) {
  const current = S.zoom.kind === 'scale' ? S.zoom.value : 1;
  const idx = ZOOM_STEPS.findIndex((z) => z >= current - 0.001);
  const next = clamp(idx + dir, 0, ZOOM_STEPS.length - 1);
  S.zoom = { kind: 'scale', value: ZOOM_STEPS[next] };
  render();
  toast(`${Math.round(ZOOM_STEPS[next] * 100)}%`);
}

/**
 * Zoom in on one point of a page and put it under the reader's eye.
 * `rx`/`ry` are 0..1 within the page; `mag` is the loupe's magnification,
 * which is what the reader just decided was readable.
 */
function zoomToPoint(page, rx, ry, mag) {
  const stage = $('#stage');
  S.zoom = { kind: 'scale', value: clamp(mag, 1, ZOOM_STEPS.at(-1)) };

  const settle = () => {
    const leaf = S.leaves.get(page) ?? $(`.leaf[data-page="${page}"]`);
    if (!leaf) return;
    const target = leaf.getBoundingClientRect();
    const view = stage.getBoundingClientRect();
    // Where that point now sits inside the scrollable stage.
    const px = leaf.offsetLeft + rx * target.width;
    const py = leaf.offsetTop + ry * target.height;
    stage.scrollTo({
      left: px - view.width / 2,
      top: py - view.height / 2,
      behavior: 'smooth',
    });
  };

  if (S.mode === 'scroll') {
    renderScroll();
    requestAnimationFrame(settle);
  } else {
    S.page = page;
    render();
    requestAnimationFrame(settle);
  }
  syncChrome();
}

function cycleFit() {
  S.zoom = S.zoom.kind === 'fit' ? { kind: 'width', value: 1 } : { kind: 'fit', value: 1 };
  render();
  toast(S.zoom.kind === 'fit' ? 'Fit page' : 'Fit width');
}

// ---------------------------------------------------------------- sidebar

function renderToc() {
  const host = $('[data-panel="toc"]');
  if (!S.doc.toc.length) {
    host.replaceChildren(el('div', { class: 'side-note' },
      el('p', { text: 'This issue has no embedded contents.' }),
      el('p', { text: 'Use the Pages tab to browse, or Find to search the text.' })));
    return;
  }
  host.replaceChildren(...S.doc.toc.map((t) => el('button', {
    class: 'toc-item',
    dataset: { level: String(Math.min(3, t.level)), page: String(t.page) },
    onclick: () => goTo(t.page, { fromSidebar: true }),
  },
    el('span', { text: t.title }),
    el('span', { class: 'pg', text: String(t.page) }),
  )));
}

function markActiveToc() {
  const visible = new Set(spreadFor(S.page));
  let best = null;
  for (const node of $$('.toc-item')) {
    const p = Number(node.dataset.page);
    node.classList.remove('here');
    if (p <= S.page && (!best || p >= Number(best.dataset.page))) best = node;
    if (visible.has(p)) best = node;
  }
  best?.classList.add('here');
}

let thumbsBuilt = false;
function renderThumbs() {
  if (thumbsBuilt) return;
  thumbsBuilt = true;
  const host = $('#thumbs');
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      io.unobserve(e.target);
      const img = e.target;
      img.src = img.dataset.src;
      img.addEventListener('load', () => img.classList.add('ready'), { once: true });
    }
  }, { root: $('[data-panel="thumbs"]'), rootMargin: '300px 0px' });

  const nodes = [];
  for (let p = 1; p <= S.doc.pages; p++) {
    const img = el('img', { 'data-src': `/api/doc/${docId}/thumb/${p}`, alt: '' });
    nodes.push(el('button', {
      class: 'thumb', dataset: { page: String(p) },
      onclick: () => goTo(p, { fromSidebar: true }),
    }, el('div', { class: 'box' }, img), el('span', { text: String(p) })));
  }
  host.replaceChildren(...nodes);
  for (const img of $$('img[data-src]', host)) io.observe(img);
}

function markActiveThumb() {
  const visible = new Set(spreadFor(S.page));
  for (const t of $$('.thumb')) {
    t.classList.toggle('here', visible.has(Number(t.dataset.page)));
  }
}

const runFind = debounce(async (q) => {
  const host = $('#find-results');
  if (!q.trim()) {
    host.replaceChildren();
    S.terms = [];
    render();
    return;
  }
  host.replaceChildren(el('div', { class: 'side-note' }, el('div', { class: 'spin', style: 'margin:0 auto' })));
  try {
    const res = await api(`/api/search?q=${encodeURIComponent(q)}&doc_id=${docId}&limit=80`);
    S.terms = termsFrom(q);
    if (!res.hits.length) {
      host.replaceChildren(el('div', { class: 'side-note' },
        el('p', { text: S.doc.has_text ? 'No matches in this issue.' : 'This issue has no text layer to search.' })));
      render();
      return;
    }
    host.replaceChildren(...res.hits.map((h) => el('button', {
      class: 'hit', onclick: () => goTo(h.page, { fromSidebar: true }),
    },
      el('span', { class: 'where' },
        el('span', { class: 'kind', text: h.kind === 'article' ? 'Article' : 'Page' }),
        ` · p.${h.page}`),
      el('span', { html: h.excerpt }),
    )));
    render();
  } catch (err) {
    host.replaceChildren(el('div', { class: 'side-note', text: String(err.message) }));
  }
}, 260);

function selectTab(tab) {
  S.tab = tab;
  for (const b of $$('.side-tabs button')) {
    b.setAttribute('aria-selected', String(b.dataset.tab === tab));
  }
  for (const p of $$('.side-panel')) {
    if (p.dataset.panel === tab) p.setAttribute('data-open', ''); else p.removeAttribute('data-open');
  }
  if (tab === 'thumbs') renderThumbs();
  if (tab === 'find') setTimeout(() => $('#find-q').focus(), 60);
}

// ---------------------------------------------------------------- misc ui

let toastTimer;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('on');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('on'), 1300);
}

function cycleSurface() {
  const order = ['dark', 'sepia', 'light'];
  S.surface = order[(order.indexOf(S.surface) + 1) % order.length];
  render();
  toast(`Surface: ${S.surface}`);
}

function setupMagnifier() {
  S.magnifier = new Magnifier({
    docId,
    magnifyWidth: S.doc.widths.at(-1),
    onZoomTo: zoomToPoint,
    onStateChange: (armed) => {
      $('#t-magnify').setAttribute('aria-pressed', String(armed));
      $('#t-magnify').classList.toggle('active', armed);
      if (armed) S.magnifier.preload(visiblePages());
      renderChips();
    },
  });
  $('#t-magnify').onclick = () => S.magnifier.toggle();
}

/** Pages currently drawn, so the magnifier can warm the right renders. */
function visiblePages() {
  if (S.mode !== 'scroll') return spreadFor(S.page);
  const stage = $('#stage');
  const top = stage.scrollTop;
  const bottom = top + stage.clientHeight;
  return [...S.leaves.entries()]
    .filter(([, leaf]) => leaf.offsetTop < bottom && leaf.offsetTop + leaf.offsetHeight > top)
    .map(([page]) => page);
}

function setupScrubber() {
  const scrub = $('#scrub');
  const peek = $('#peek');
  const img = peek.querySelector('img');
  const label = peek.querySelector('span');

  const preview = () => {
    const p = Number(scrub.value);
    label.textContent = `p. ${p}`;
    img.src = `/api/doc/${docId}/thumb/${p}`;
    const rect = scrub.getBoundingClientRect();
    const frac = (p - 1) / Math.max(1, S.doc.pages - 1);
    peek.style.left = `${clamp(frac * rect.width, 56, rect.width - 56)}px`;
    peek.classList.add('on');
  };

  scrub.addEventListener('input', preview);
  scrub.addEventListener('change', () => { peek.classList.remove('on'); goTo(Number(scrub.value)); });
  scrub.addEventListener('pointerdown', preview);
  scrub.addEventListener('pointerup', () => setTimeout(() => peek.classList.remove('on'), 200));
  scrub.addEventListener('mouseleave', () => peek.classList.remove('on'));
}

function setupDragPan() {
  const stage = $('#stage');
  let dragId = null;
  let sx = 0, sy = 0, sl = 0, st = 0;

  const canPan = () => stage.scrollWidth > stage.clientWidth + 1
                    || stage.scrollHeight > stage.clientHeight + 1;

  const stop = () => {
    if (dragId === null) return;
    try { stage.releasePointerCapture(dragId); } catch { /* already gone */ }
    dragId = null;
    stage.classList.remove('dragging');
  };

  stage.addEventListener('pointerdown', (e) => {
    // Left button or middle-drag, and only when there is somewhere to pan to.
    if (e.button !== 0 && e.button !== 1) return;
    if (S.magnifier?.armed) return;
    if (!canPan()) return;

    dragId = e.pointerId;
    sx = e.clientX; sy = e.clientY;
    sl = stage.scrollLeft; st = stage.scrollTop;
    stage.classList.add('dragging');
    // Capture so the gesture survives the pointer leaving the stage - losing
    // it mid-drag was what made the grab cursor feel intermittent.
    try { stage.setPointerCapture(e.pointerId); } catch { /* unsupported */ }
    e.preventDefault();
  });

  stage.addEventListener('pointermove', (e) => {
    if (e.pointerId !== dragId) return;
    stage.scrollLeft = sl - (e.clientX - sx);
    stage.scrollTop = st - (e.clientY - sy);
    e.preventDefault();
  });

  for (const type of ['pointerup', 'pointercancel', 'lostpointercapture']) {
    stage.addEventListener(type, (e) => {
      if (e.pointerId === dragId) stop();
    });
  }
  // Leaving the window entirely never fires pointerup on the stage.
  addEventListener('blur', stop);
}

// Toolbar buttons advertise their shortcut, so the hints have to follow the
// keymap rather than hard-coded letters once bindings can be changed.
const BUTTON_ACTIONS = [
  ['#t-side', 'sidebar', 'Sidebar'],
  ['#t-magnify', 'magnifier', 'Magnifier — hover to inspect, click to zoom in'],
  ['#t-surface', 'surface', 'Reading surface'],
  ['#t-full', 'fullscreen', 'Fullscreen'],
  ['#t-help', 'help', 'Keyboard shortcuts'],
  ['#z-in', 'zoomIn', 'Zoom in'],
  ['#z-out', 'zoomOut', 'Zoom out'],
  ['#z-fit', 'zoomFit', 'Click to fit the page again'],
  ['#p-prev', 'prevPage', 'Previous page'],
  ['#p-next', 'nextPage', 'Next page'],
  ['[data-mode="single"]', 'modeSingle', 'Single page'],
  ['[data-mode="spread"]', 'modeSpread', 'Two-page spread'],
  ['[data-mode="scroll"]', 'modeScroll', 'Continuous scroll'],
];

function refreshKeyHints() {
  if (!S.keymap) return;
  for (const [selector, action, label] of BUTTON_ACTIONS) {
    const node = $(selector);
    if (!node) continue;
    const keys = S.keymap.keysFor(action).slice(0, 2).map(prettyKey);
    node.title = keys.length ? `${label} (${keys.join(' or ')})` : label;
  }
}

/** What each named action does. Keys are resolved through the keymap. */
function actionHandlers() {
  return {
    nextPage:   () => step(1),
    prevPage:   () => step(-1),
    firstPage:  () => goTo(1),
    lastPage:   () => goTo(S.doc.pages),
    gotoPage:   () => { $('#pg-input').focus(); $('#pg-input').select(); },
    prevIssue:  () => { if (S.doc.prev) location.href = `/read/${S.doc.prev.id}`; },
    nextIssue:  () => { if (S.doc.next) location.href = `/read/${S.doc.next.id}`; },

    modeSingle: () => setMode('single'),
    modeSpread: () => setMode('spread'),
    modeScroll: () => setMode('scroll'),
    spreadOffset: () => {
      S.coverAlone = !S.coverAlone;
      render();
      toast(S.coverAlone ? 'Cover on its own' : 'Pages paired from 1');
    },
    surface:    () => cycleSurface(),
    fullscreen: () => toggleFullscreen(),

    magnifier:  () => S.magnifier?.toggle(),
    zoomIn:     () => zoomBy(1),
    zoomOut:    () => zoomBy(-1),
    zoomFit:    () => cycleFit(),

    sidebar:    () => { S.side = !S.side; render(); },
    find:       () => { S.side = true; render(); selectTab('find'); },
    help:       () => S.shortcuts?.toggle(),
  };
}

function setupKeys() {
  const handlers = actionHandlers();

  addEventListener('keydown', (e) => {
    // While the shortcuts sheet is capturing, every key belongs to it.
    if (S.shortcuts?.handleKey(e)) {
      e.preventDefault();
      return;
    }

    const t = e.target;
    const typing = t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement;

    if (e.key === 'Escape') {
      if (S.shortcuts?.open) { S.shortcuts.close(); return; }
      if (S.magnifier?.armed) { S.magnifier.disarm(); return; }
      if (typing) { t.blur(); return; }
      if (S.terms.length) {
        $('#find-q').value = '';
        S.terms = [];
        $('#find-results').replaceChildren();
        render();
      }
      return;
    }
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;

    const action = S.keymap.actionFor(e.key);
    const fn = action && handlers[action];
    if (!fn) return;
    e.preventDefault();
    fn();
  });
}

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen?.().catch(() => {});
}

// ---------------------------------------------------------------- boot

async function main() {
  loadPrefs();
  try {
    S.doc = await api(`/api/doc/${docId}`);
  } catch (err) {
    document.body.replaceChildren(el('div', { class: 'empty' },
      el('h2', { text: 'Could not open that issue' }),
      el('p', { text: String(err.message) }),
      el('p', {}, el('a', { class: 'btn', href: '/', text: 'Back to the library' }))));
    return;
  }

  document.title = `${S.doc.title} · jb-pdf-viewer`;
  $('#doc-title').textContent = S.doc.title;
  $('#dl').href = `/api/doc/${docId}/file`;

  if (!S.doc.pages) {
    // Damaged or empty source file; there is nothing to lay out.
    $('#stage').replaceChildren(el('div', { class: 'empty' },
      el('h2', { text: 'This file has no readable pages' }),
      el('p', { text: 'The PDF is empty or damaged. You can still download the original.' }),
      el('p', {}, el('a', { class: 'btn', href: '/', text: 'Back to the library' }))));
    $('#doc-sub').textContent = 'unreadable';
    return;
  }

  const urlPage = Number(new URLSearchParams(location.search).get('page'));
  S.page = clamp(urlPage || S.doc.progress?.page || 1, 1, S.doc.pages);

  renderToc();
  render();
  goTo(S.page, { smooth: false });

  // chrome wiring
  $('#p-prev').onclick = () => step(-1);
  $('#p-next').onclick = () => step(1);
  $('#z-prev').onclick = () => step(-1);
  $('#z-next').onclick = () => step(1);
  $('#z-in').onclick = () => zoomBy(1);
  $('#z-out').onclick = () => zoomBy(-1);
  $('#z-fit').onclick = () => cycleFit();
  $('#t-side').onclick = () => { S.side = !S.side; render(); };
  $('#t-surface').onclick = () => cycleSurface();
  $('#t-full').onclick = () => toggleFullscreen();
  S.keymap = new Keymap();
  S.shortcuts = new ShortcutsSheet(S.keymap, () => refreshKeyHints());
  $('#help-close').onclick = () => S.shortcuts.close();
  $('#t-help').onclick = () => S.shortcuts.show();

  for (const b of $$('.seg [data-mode]')) b.onclick = () => setMode(b.dataset.mode);
  for (const b of $$('.side-tabs button')) b.onclick = () => selectTab(b.dataset.tab);

  $('#pg-input').addEventListener('change', (e) => goTo(Number(e.target.value)));
  $('#pg-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') e.target.blur(); });
  $('#find-q').addEventListener('input', (e) => runFind(e.target.value));

  const stage = $('#stage');
  stage.addEventListener('scroll', () => syncPanning(), { passive: true });
  new ResizeObserver(() => syncPanning()).observe($('#pages'));

  refreshKeyHints();
  setupMagnifier();
  setupScrubber();
  setupDragPan();
  setupKeys();
  selectTab(S.doc.toc.length ? 'toc' : 'thumbs');

  addEventListener('resize', debounce(() => render(), 140));
  addEventListener('beforeunload', () => saveProgress.flush());
}

main();
