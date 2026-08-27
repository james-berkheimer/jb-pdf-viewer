// Collection-wide search results.
import { $, el, api, debounce, slashToFocus } from './util.js';

const PAGE = 40;
const state = { q: '', library: null, offset: 0, total: 0, libs: [], stats: null };

/** Issues with no OCR text layer cannot match, which is worth saying out loud. */
function ocrCaveat() {
  const s = state.stats;
  if (!s || s.docs === s.with_text) return null;
  return `${s.docs - s.with_text} of ${s.docs} issues have no OCR text layer and cannot be searched yet.`;
}

function resultCard(h) {
  const isArticle = h.kind === 'article';
  return el('a', {
    class: 'result',
    href: `/read/${h.doc_id}?page=${h.page}`,
    title: `${h.title} — page ${h.page}`,
  },
    el('img', { src: `/api/doc/${h.doc_id}/cover`, alt: '', loading: 'lazy' }),
    el('div', { class: 'body' },
      el('div', { class: 'head' },
        el('span', { class: `kind${isArticle ? '' : ' page'}`, text: isArticle ? 'Article' : 'Page' }),
        el('span', { class: 'issue', text: h.title }),
        el('span', { class: 'pg', text: `p. ${h.page} of ${h.pages}` }),
        !state.library && h.group_name
          ? el('span', { class: 'pg', text: h.group_name }) : null,
      ),
      el('div', { class: 'ex', html: h.excerpt }),
    ),
  );
}

function renderFilters() {
  const host = $('#filters');
  if (!state.q || state.libs.length < 2) { host.replaceChildren(); return; }
  const mk = (label, id) => el('button', {
    class: `btn${state.library === id ? ' active' : ''}`,
    text: label,
    onclick: () => { state.library = id; state.offset = 0; syncUrl(); run(); },
  });
  host.replaceChildren(mk('All libraries', null),
    ...state.libs.map((l) => mk(`${l.name} (${l.docs})`, l.id)));
}

async function run({ append = false } = {}) {
  const results = $('#results');
  if (!state.q) {
    results.replaceChildren();
    $('#heading').textContent = 'Search';
    $('#meta').innerHTML =
      'Type a phrase to search the full text of every document. Use <code>"quotes"</code> for an exact phrase.';
    renderFilters();
    return;
  }

  const url = new URL('/api/search', location.origin);
  url.searchParams.set('q', state.q);
  url.searchParams.set('limit', PAGE);
  url.searchParams.set('offset', state.offset);
  if (state.library) url.searchParams.set('library', state.library);

  if (!append) results.replaceChildren(el('div', { class: 'empty' }, el('div', { class: 'spin', style: 'margin:0 auto' })));

  try {
    const res = await api(url.pathname + url.search);
    state.total = res.total;
    $('#heading').textContent = `“${state.q}”`;
    $('#meta').textContent = res.total
      ? `${res.total.toLocaleString()} match${res.total === 1 ? '' : 'es'} · ${res.ms} ms`
      : 'No matches.';

    const cards = res.hits.map(resultCard);
    if (append) {
      $('.more')?.remove();
      results.append(...cards);
    } else {
      results.replaceChildren(...cards);
    }

    if (!res.hits.length && !append) {
      results.replaceChildren(el('div', { class: 'empty' },
        el('h2', { text: 'Nothing found' }),
        el('p', { text: 'Try fewer words, or drop the quotes for a looser match.' }),
        ocrCaveat() ? el('p', { text: ocrCaveat() }) : null));
    } else if (state.offset + res.hits.length < res.total) {
      results.append(el('button', {
        class: 'btn more',
        text: `Show more (${(state.offset + res.hits.length).toLocaleString()} of ${res.total.toLocaleString()})`,
        onclick: () => { state.offset += PAGE; run({ append: true }); },
      }));
    }
    renderFilters();
  } catch (err) {
    results.replaceChildren(el('div', { class: 'empty' },
      el('h2', { text: 'Search failed' }), el('p', { text: String(err.message) })));
  }
}

function syncUrl() {
  const u = new URL(location.href);
  if (state.q) u.searchParams.set('q', state.q); else u.searchParams.delete('q');
  if (state.library) u.searchParams.set('library', state.library); else u.searchParams.delete('library');
  history.replaceState(null, '', u);
}

async function main() {
  const input = $('#q');
  slashToFocus(input);

  const params = new URLSearchParams(location.search);
  state.q = params.get('q') || '';
  state.library = params.get('library');
  input.value = state.q;

  api('/api/libraries').then(({ libraries }) => {
    state.libs = libraries.filter((l) => l.indexed);
    renderFilters();
  }).catch(() => {});
  api('/api/stats').then((s) => { state.stats = s; }).catch(() => {});

  const search = debounce(() => { state.offset = 0; syncUrl(); run(); }, 300);
  input.addEventListener('input', () => { state.q = input.value.trim(); search(); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { state.q = input.value.trim(); state.offset = 0; search.flush(); }
  });

  run();
  if (!state.q) input.focus();
}

main();
