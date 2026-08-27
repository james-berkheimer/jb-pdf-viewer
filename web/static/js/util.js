// Shared helpers.

export const $  = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

export function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === 'class') node.className = v;
    else if (k === 'html') node.innerHTML = v;
    else if (k === 'text') node.textContent = v;
    else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
    else if (k === 'dataset') Object.assign(node.dataset, v);
    else node.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return node;
}

export async function api(path, opts) {
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail ?? detail; } catch { /* not json */ }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json();
}

export const clamp = (n, lo, hi) => Math.min(hi, Math.max(lo, n));

export function debounce(fn, ms = 220) {
  let t;
  const wrapped = (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  wrapped.cancel = () => clearTimeout(t);
  wrapped.flush = (...a) => { clearTimeout(t); fn(...a); };
  return wrapped;
}

export function fmtBytes(n) {
  if (!n) return '—';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n < 10 && i > 0 ? n.toFixed(1) : Math.round(n)} ${u[i]}`;
}

export function fmtWhen(ts) {
  if (!ts) return '';
  const secs = Date.now() / 1000 - ts;
  if (secs < 90) return 'just now';
  const steps = [[60, 'min'], [24, 'hr'], [7, 'day'], [4.35, 'wk'], [12, 'mo']];
  let v = secs / 60, unit = 'min';
  for (const [div, name] of steps) {
    if (v < div) break;
    v /= div; unit = name;
  }
  const r = Math.round(v);
  return `${r} ${unit}${r === 1 ? '' : 's'} ago`;
}

/** Lazily load images only as they scroll into view. */
export function lazyImages(root = document) {
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const img = e.target;
      io.unobserve(img);
      img.src = img.dataset.src;
      img.addEventListener('load', () => img.classList.add('ready'), { once: true });
      img.addEventListener('error', () => img.classList.add('ready'), { once: true });
    }
  }, { rootMargin: '400px 0px' });
  for (const img of $$('img[data-src]', root)) io.observe(img);
  return io;
}

/** Focus the given input when "/" is pressed outside a field. */
export function slashToFocus(input) {
  addEventListener('keydown', (e) => {
    if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return;
    const t = e.target;
    if (t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement) return;
    e.preventDefault();
    input.focus();
    input.select();
  });
}
