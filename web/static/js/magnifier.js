// Magnifier: a loupe that follows the pointer, and click-to-zoom for reading.
//
// Scanned magazine text is often too small to read at fit-page size. Both
// tools here source a high-resolution render of the page rather than scaling
// up the image already on screen, which would just show bigger blur.

import { $, el, clamp } from './util.js';

const MIN_MAG = 1.5;
const MAX_MAG = 6;
const STEP = 0.25;
// Gentle by default: enough to read small scanned type without the loupe
// showing so little of the page that you lose your place.
const DEFAULT_MAG = 1.5;

export class Magnifier {
  /**
   * @param {object} opts
   *   docId        document id, for building page URLs
   *   magnifyWidth pixel width to request for the high-res source
   *   onZoomTo     (page, relX, relY, magnification) => void  - click-to-zoom
   *   onStateChange (armed) => void
   */
  constructor(opts) {
    this.docId = opts.docId;
    this.magnifyWidth = opts.magnifyWidth || 3200;
    this.onZoomTo = opts.onZoomTo || (() => {});
    this.onStateChange = opts.onStateChange || (() => {});

    this.armed = false;
    this.mag = DEFAULT_MAG;
    this.current = null;         // page whose high-res render is loaded
    this.sources = new Map();    // page -> {url, status}
    this.lens = null;
    this._raf = null;
    this._pending = null;

    this._onMove = this._onMove.bind(this);
    this._onLeave = this._onLeave.bind(this);
    this._onWheel = this._onWheel.bind(this);
    this._onClick = this._onClick.bind(this);
  }

  // ---------------------------------------------------------------- state

  toggle() { this.armed ? this.disarm() : this.arm(); }

  arm() {
    if (this.armed) return;
    this.armed = true;
    document.body.dataset.magnify = 'on';
    const stage = $('#stage');
    stage.addEventListener('pointermove', this._onMove);
    stage.addEventListener('pointerleave', this._onLeave);
    stage.addEventListener('wheel', this._onWheel, { passive: false });
    stage.addEventListener('click', this._onClick, true);
    this.onStateChange(true);
  }

  disarm() {
    if (!this.armed) return;
    this.armed = false;
    delete document.body.dataset.magnify;
    const stage = $('#stage');
    stage.removeEventListener('pointermove', this._onMove);
    stage.removeEventListener('pointerleave', this._onLeave);
    stage.removeEventListener('wheel', this._onWheel);
    stage.removeEventListener('click', this._onClick, true);
    this._hide();
    this.onStateChange(false);
  }

  setMagnification(value) {
    this.mag = clamp(Number(value.toFixed(2)), MIN_MAG, MAX_MAG);
    if (this._pending) this._draw(this._pending);
    return this.mag;
  }

  // ---------------------------------------------------------------- source

  /** Kick off (or reuse) the high-res render for a page. */
  source(page) {
    let entry = this.sources.get(page);
    if (entry) return entry;

    const url = `/api/doc/${this.docId}/page/${page}?w=${this.magnifyWidth}&prefetch=0`;
    entry = { url, status: 'loading' };
    this.sources.set(page, entry);

    const img = new Image();
    img.decoding = 'async';
    img.addEventListener('load', () => {
      entry.status = 'ready';
      entry.width = img.naturalWidth;
      entry.height = img.naturalHeight;
      if (this._pending?.page === page) this._draw(this._pending);
      this._syncBusy();
    }, { once: true });
    img.addEventListener('error', () => {
      entry.status = 'error';
      this._syncBusy();
    }, { once: true });
    img.src = url;
    entry.img = img;
    this._syncBusy();
    return entry;
  }

  /** Warm the pages currently on screen so the loupe is instant. */
  preload(pages) {
    for (const p of pages) this.source(p);
  }

  _syncBusy() {
    if (!this.lens) return;
    const entry = this._pending ? this.sources.get(this._pending.page) : null;
    this.lens.classList.toggle('busy', !!entry && entry.status === 'loading');
  }

  // ---------------------------------------------------------------- lens

  _ensureLens() {
    if (this.lens) return this.lens;
    this.lens = el('div', { class: 'loupe', 'aria-hidden': 'true' },
      el('div', { class: 'loupe-img' }),
      el('div', { class: 'loupe-spin' }, el('div', { class: 'spin' })),
      el('div', { class: 'loupe-mag' }),
    );
    document.body.append(this.lens);
    return this.lens;
  }

  _hide() {
    this.lens?.classList.remove('on');
    this._pending = null;
  }

  _onLeave() { this._hide(); }

  _onMove(event) {
    const leaf = event.target.closest?.('.leaf');
    if (!leaf) { this._hide(); return; }
    const page = Number(leaf.dataset.page);
    if (!page) { this._hide(); return; }

    this._pending = { leaf, page, x: event.clientX, y: event.clientY };
    this.source(page);

    // Pointer events fire far faster than the screen refreshes.
    if (this._raf) return;
    this._raf = requestAnimationFrame(() => {
      this._raf = null;
      if (this._pending) this._draw(this._pending);
    });
  }

  _draw({ leaf, page, x, y }) {
    const entry = this.sources.get(page);
    const lens = this._ensureLens();
    const rect = leaf.getBoundingClientRect();
    if (!rect.width) return;

    lens.classList.add('on');
    lens.classList.toggle('busy', !entry || entry.status !== 'ready');
    lens.querySelector('.loupe-mag').textContent = `${this.mag.toFixed(1)}×`;

    const lw = lens.offsetWidth;
    const lh = lens.offsetHeight;

    // Keep the lens on screen near the edges of the viewport.
    lens.style.left = `${clamp(x, lw / 2 + 4, innerWidth - lw / 2 - 4)}px`;
    lens.style.top = `${clamp(y, lh / 2 + 4, innerHeight - lh / 2 - 4)}px`;

    if (!entry || entry.status !== 'ready') return;

    const view = lens.querySelector('.loupe-img');
    // Where the pointer sits on the page, 0..1 in each axis.
    const rx = clamp((x - rect.left) / rect.width, 0, 1);
    const ry = clamp((y - rect.top) / rect.height, 0, 1);

    // The page drawn at `mag` times its on-screen size.
    const pageW = rect.width * this.mag;
    const pageH = rect.height * this.mag;

    view.style.backgroundImage = `url("${entry.url}")`;
    view.style.backgroundSize = `${pageW}px ${pageH}px`;
    view.style.backgroundPosition =
      `${lw / 2 - rx * pageW}px ${lh / 2 - ry * pageH}px`;
  }

  // ---------------------------------------------------------------- input

  _onWheel(event) {
    if (!this._pending) return;
    event.preventDefault();
    this.setMagnification(this.mag + (event.deltaY < 0 ? STEP : -STEP));
  }

  _onClick(event) {
    const leaf = event.target.closest?.('.leaf');
    if (!leaf) return;
    // Swallow the click so it does not also trigger a page turn.
    event.preventDefault();
    event.stopPropagation();

    const rect = leaf.getBoundingClientRect();
    const rx = clamp((event.clientX - rect.left) / rect.width, 0, 1);
    const ry = clamp((event.clientY - rect.top) / rect.height, 0, 1);
    const page = Number(leaf.dataset.page);
    this.disarm();
    this.onZoomTo(page, rx, ry, this.mag);
  }

  destroy() {
    this.disarm();
    this.lens?.remove();
    this.lens = null;
    this.sources.clear();
  }
}
