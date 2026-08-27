// Selectable / highlightable text overlay drawn on top of a page image.
//
// The page arrives as a raster image, so real text selection needs a layer of
// invisible, absolutely-positioned word boxes in the same place the words sit
// on the page. Coordinates come from the server in PDF points and are scaled
// to whatever pixel width the page is currently drawn at.

const cache = new Map();
const MAX_CACHED = 60;

export async function loadWords(docId, page) {
  const key = `${docId}/${page}`;
  if (cache.has(key)) return cache.get(key);

  const promise = fetch(`/api/doc/${docId}/text/${page}`)
    .then((r) => (r.ok ? r.json() : null))
    .catch(() => null);

  cache.set(key, promise);
  if (cache.size > MAX_CACHED) cache.delete(cache.keys().next().value);
  return promise;
}

function normalise(s) {
  return s.toLowerCase().replace(/[‘’]/g, "'").replace(/[^\w'-]/g, '');
}

/**
 * Build the overlay for one page.
 * @param {HTMLElement} host   .textlayer element, already sized to the image
 * @param {object} data        {width, height, words:[{x,y,w,h,t}]}
 * @param {number} pxWidth     rendered width of the page image in CSS pixels
 * @param {string[]} terms     words to highlight, already lower-cased
 */
export function paint(host, data, pxWidth, terms = []) {
  host.replaceChildren();
  if (!data?.words?.length) return 0;

  const scale = pxWidth / data.width;
  const wanted = terms.filter(Boolean).map(normalise).filter(Boolean);
  const frag = document.createDocumentFragment();
  let hits = 0;

  for (const w of data.words) {
    const span = document.createElement(wanted.length && isHit(w.t, wanted) ? 'mark' : 'span');
    if (span.tagName === 'MARK') hits++;
    span.textContent = w.t;
    // Font size is derived from the word's box height so browser text
    // selection lands on roughly the right glyphs.
    span.style.cssText =
      `left:${(w.x * scale).toFixed(1)}px;` +
      `top:${(w.y * scale).toFixed(1)}px;` +
      `width:${(w.w * scale).toFixed(1)}px;` +
      `height:${(w.h * scale).toFixed(1)}px;` +
      `font-size:${Math.max(4, w.h * scale * 0.86).toFixed(1)}px;`;
    span.style.position = 'absolute';
    frag.append(span);
    frag.append(document.createTextNode(' '));
  }
  host.append(frag);
  return hits;
}

function isHit(text, wanted) {
  const n = normalise(text);
  if (!n) return false;
  return wanted.some((w) => n.includes(w));
}

export function termsFrom(query) {
  return (query || '')
    .toLowerCase()
    .split(/[\s"]+/)
    .map(normalise)
    .filter((t) => t.length > 1);
}
