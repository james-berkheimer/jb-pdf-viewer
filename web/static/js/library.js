// Library page: library switcher, group shelves, continue-reading, filter.
import { $, el, api, lazyImages, fmtBytes, fmtWhen, debounce, slashToFocus }
  from './util.js';
import { LibraryManager } from './libadmin.js';

const TILE_KEY = 'pdfv.tileSize';
const LIB_KEY = 'pdfv.library';

// Above this many groups the shelf buttons stop being useful and a picker
// plus progressive loading takes over.
const MANY_GROUPS = 14;
const SHELF_CHUNK = 8;

const state = {
  libs: [],
  lib: null,         // active library id, null = all
  groups: [],
  filter: '',
  active: null,      // active group slug, null = all
  shown: 0,          // how many shelves are rendered so far
  io: null,
};

// ------------------------------------------------------------------ tiles

function coverTile(issue) {
  const pct = issue.progress_page && issue.pages
    ? Math.min(100, Math.round((issue.progress_page / issue.pages) * 100))
    : 0;
  const badge = issue.finished
    ? el('span', { class: 'badge done', text: '✓' })
    : pct > 0 ? el('span', { class: 'badge', text: `${pct}%` }) : null;

  return el('a', {
    class: 'tile',
    href: `/read/${issue.id}`,
    title: `${issue.title} — ${issue.pages} pages`,
  },
    el('div', { class: 'frame' },
      el('img', { 'data-src': `/api/doc/${issue.id}/cover`, alt: '',
                  loading: 'lazy', decoding: 'async' }),
      badge,
      pct > 0 ? el('div', { class: 'prog' }, el('i', { style: `width:${pct}%` })) : null,
    ),
    el('div', { class: 'cap' },
      el('div', { class: 'n', text: issue.label, title: issue.title }),
      el('div', { class: 'd' },
        el('span', { text: `${issue.pages} pp` }),
        issue.toc_count > 0 ? el('span', { text: `· ${issue.toc_count} articles` }) : null,
        !issue.has_text
          ? el('span', { class: 'no-ocr', title: 'No text layer — not searchable',
                         text: '· no OCR' })
          : null,
      ),
    ),
  );
}

function matches(issue) {
  if (!state.filter) return true;
  const f = state.filter.toLowerCase();
  return issue.title.toLowerCase().includes(f)
      || issue.label.toLowerCase().includes(f);
}

function shelf(group) {
  const issues = group.issues.filter(matches);
  if (!issues.length) return null;
  return el('section', { class: 'section', id: `s-${group.slug}` },
    el('div', { class: 'section-head' },
      el('h2', { text: group.name }),
      el('span', { class: 'count',
                   text: `${issues.length} item${issues.length === 1 ? '' : 's'}` }),
      group.path ? el('span', { class: 'crumb', text: group.path, title: group.path }) : null,
    ),
    el('div', { class: 'grid' }, issues.map(coverTile)),
  );
}

// ------------------------------------------------------------------ render

function visibleGroups() {
  return state.groups
    .filter((g) => !state.active || g.slug === state.active)
    .filter((g) => g.issues.some(matches));
}

function render({ more = false } = {}) {
  const host = $('#shelves');
  if (!more) {
    host.replaceChildren();
    state.io?.disconnect();
    state.shown = 0;
  } else {
    $('.load-more', host)?.remove();
  }

  const groups = visibleGroups();
  if (!groups.length) {
    host.replaceChildren(el('div', { class: 'empty' },
      el('h2', { text: 'Nothing matches' }),
      el('p', { text: 'Try a different name, or search the full text above.' })));
    return;
  }

  // Render in chunks: a folder-organised library can have hundreds of shelves
  // and thousands of tiles, which is far too much to build in one pass.
  const next = groups.slice(state.shown, state.shown + SHELF_CHUNK);
  host.append(...next.map(shelf).filter(Boolean));
  state.shown += next.length;

  if (state.shown < groups.length) {
    host.append(el('button', {
      class: 'btn load-more',
      text: `Show more folders (${state.shown} of ${groups.length})`,
      onclick: () => render({ more: true }),
    }));
  }
  state.io = lazyImages(host);
}

function renderGroupNav() {
  const nav = $('#series-nav');
  const pick = $('#group-pick');
  const select = $('#group-select');
  const groups = state.groups;

  if (groups.length > MANY_GROUPS) {
    nav.replaceChildren();
    pick.hidden = false;
    select.replaceChildren(
      el('option', { value: '', text: `All folders (${groups.length})` }),
      ...groups.map((g) => el('option', {
        value: g.slug,
        selected: state.active === g.slug,
        text: `${g.path ? `${g.path} / ` : ''}${g.name}  (${g.issues.length})`,
      })),
    );
    select.onchange = () => { state.active = select.value || null; render(); };
    return;
  }

  pick.hidden = true;
  const mk = (label, slug) => el('button', {
    class: `btn${state.active === slug ? ' active' : ''}`,
    text: label,
    onclick: () => { state.active = slug; renderGroupNav(); render(); },
  });
  nav.replaceChildren(
    mk('All', null),
    ...groups.map((g) => mk(`${g.name} (${g.issues.length})`, g.slug)),
  );
}

function renderLibs() {
  const host = $('#libs');
  if (state.libs.length < 2) { host.replaceChildren(); return; }

  const mk = (lib) => el('button', {
    class: `btn${state.lib === (lib?.id ?? null) ? ' active' : ''}`,
    title: lib ? `${lib.docs} documents · ${lib.pages.toLocaleString()} pages` : 'Everything',
    onclick: () => selectLib(lib?.id ?? null),
  },
    lib ? lib.name : 'All libraries',
    el('span', { class: 'n', text: String(lib ? lib.docs : totalDocs()) }),
  );
  host.replaceChildren(mk(null), ...state.libs.map(mk));
}

const totalDocs = () => state.libs.reduce((n, l) => n + l.docs, 0);

// ------------------------------------------------------------------ data

async function loadGroups() {
  const url = state.lib ? `/api/library?library=${encodeURIComponent(state.lib)}`
                        : '/api/library';
  const data = await api(url);
  state.groups = data.groups;
  state.active = null;
}

async function selectLib(libId) {
  state.lib = libId;
  if (libId) localStorage.setItem(LIB_KEY, libId);
  else localStorage.removeItem(LIB_KEY);
  renderLibs();
  $('#shelves').replaceChildren(el('div', { class: 'empty' },
    el('div', { class: 'spin', style: 'margin:0 auto' })));
  await loadGroups();
  renderGroupNav();
  render();
  updateSubtitle();
}

async function updateSubtitle() {
  const url = state.lib ? `/api/stats?library=${encodeURIComponent(state.lib)}`
                        : '/api/stats';
  try {
    const s = await api(url);
    const lib = state.libs.find((l) => l.id === state.lib);
    $('#stats').textContent =
      `${s.docs} docs · ${s.pages.toLocaleString()} pages · ${fmtBytes(s.bytes)}`;
    const parts = [
      `${lib ? lib.name : 'All libraries'}: ${s.docs} documents, ${s.pages.toLocaleString()} pages across ${s.groups} folders.`,
      `${s.with_text} searchable`,
    ];
    if (s.docs > s.with_text) parts.push(`${s.docs - s.with_text} without an OCR text layer`);
    $('#subtitle').textContent = parts.join(' · ') + '.';
  } catch { /* stats are decoration */ }
}

async function renderResume() {
  const { items } = await api('/api/continue');
  if (!items.length) return;
  $('#resume').hidden = false;
  $('#resume-strip').replaceChildren(...items.map((it) => {
    const pct = Math.min(100, Math.round((it.progress_page / it.pages) * 100));
    return el('a', { class: 'resume-card', href: `/read/${it.id}?page=${it.progress_page}` },
      el('img', { src: `/api/doc/${it.id}/cover`, alt: '', loading: 'lazy' }),
      el('div', { class: 'meta' },
        el('div', { class: 't', text: it.title }),
        el('div', { class: 's',
                    text: `p.${it.progress_page} of ${it.pages} · ${fmtWhen(it.updated_at)}` }),
        el('div', { class: 'bar' }, el('i', { style: `width:${pct}%` })),
      ),
    );
  }));
}

function initTileSize() {
  const input = $('#tile-size');
  const saved = localStorage.getItem(TILE_KEY);
  if (saved) input.value = saved;
  const apply = () => {
    document.documentElement.style.setProperty('--tile', `${input.value}px`);
    localStorage.setItem(TILE_KEY, input.value);
  };
  input.addEventListener('input', apply);
  apply();
}

/** Rebuild everything after the manager changes what libraries exist. */
async function reloadAll() {
  const { libraries } = await api('/api/libraries');
  state.libs = libraries.filter((l) => l.indexed);
  if (state.lib && !state.libs.some((l) => l.id === state.lib)) state.lib = null;
  if (!state.lib && state.libs.length === 1) state.lib = state.libs[0].id;
  renderLibs();
  await loadGroups();
  renderGroupNav();
  render();
  updateSubtitle();
}

async function main() {
  initTileSize();
  const input = $('#q');
  slashToFocus(input);

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && input.value.trim()) {
      const lib = state.lib ? `&library=${encodeURIComponent(state.lib)}` : '';
      location.href = `/search?q=${encodeURIComponent(input.value.trim())}${lib}`;
    }
  });
  input.addEventListener('input', debounce(() => {
    state.filter = input.value.trim();
    render();
  }, 130));

  try {
    const { libraries, admin } = await api('/api/libraries');
    state.libs = libraries.filter((l) => l.indexed);

    const wanted = new URLSearchParams(location.search).get('library')
                || localStorage.getItem(LIB_KEY);
    state.lib = state.libs.some((l) => l.id === wanted) ? wanted : null;
    // With a single library there is nothing to switch between, so pin to it.
    if (!state.lib && state.libs.length === 1) state.lib = state.libs[0].id;

    renderLibs();
    await loadGroups();
    renderGroupNav();
    render();
    updateSubtitle();
    renderResume().catch(() => {});

    // The manager only appears when the admin API is actually enabled.
    if (admin) {
      const manager = new LibraryManager(() => reloadAll().catch(() => {}));
      const button = $('#manage');
      button.hidden = false;
      button.onclick = () => manager.open();
    }
  } catch (err) {
    $('#shelves').replaceChildren(el('div', { class: 'empty' },
      el('h2', { text: 'Could not load the library' }),
      el('p', { text: String(err.message) }),
      el('p', { text: 'Add one with the Libraries button, or: scripts/library.py add "Name" /path/to/pdfs --index' })));
    $('#subtitle').textContent = '';
  }
}

main();
