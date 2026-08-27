// Library manager: add, edit, remove and rescan libraries from the browser.
import { $, $$, el, api } from './util.js';

const ICON_FOLDER = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
  + 'stroke-width="1.8"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
const ICON_TRASH = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" '
  + 'stroke-width="2" stroke-linecap="round"><path d="M4 7h16M9 7V5h6v2M6 7l1 13h10l1-13"/></svg>';

/**
 * The server stops counting after a short budget, so a big folder reports a
 * lower bound rather than a total. Saying "0+ PDFs" would read as "none here",
 * which is the opposite of what a capped count means.
 */
function pdfLabel(b) {
  if (!b.capped) return `${b.pdfs.toLocaleString()} PDF${b.pdfs === 1 ? '' : 's'}`;
  if (b.pdfs > 0) return `${b.pdfs.toLocaleString()}+ PDFs`;
  return 'large folder — too big to count, open a sub-folder';
}

export class LibraryManager {
  /** @param {() => void} onChanged called when the catalogue may have changed */
  constructor(onChanged) {
    this.onChanged = onChanged || (() => {});
    this.libs = [];
    this.profiles = ['auto', 'magazine', 'folders', 'flat'];
    this.browse = null;        // {path, parent, entries, pdfs}
    this.picked = '';          // chosen root for the add form
    this.poll = null;
    this.scan = null;
    this.msg = null;
    this.sheet = null;
  }

  // ---------------------------------------------------------------- open

  async open() {
    this._ensureSheet();
    this.sheet.hidden = false;
    this.msg = null;
    await this.refresh();
    await this.openFolder(null);
    this._pollScan();
  }

  close() {
    if (this.sheet) this.sheet.hidden = true;
    clearTimeout(this.poll);
    this.poll = null;
  }

  async refresh() {
    try {
      const [admin, listed] = await Promise.all([
        api('/api/admin/libraries'),
        api('/api/libraries').catch(() => ({ libraries: [] })),
      ]);
      const counts = new Map(listed.libraries.map((l) => [l.id, l]));
      this.profiles = admin.profiles;
      this.libs = admin.libraries.map((l) => ({ ...l, ...(counts.get(l.id) || {}) }));
    } catch (err) {
      this.msg = { kind: 'err', text: err.message };
      this.libs = [];
    }
    this.render();
  }

  // ---------------------------------------------------------------- markup

  _ensureSheet() {
    if (this.sheet) return;
    this.sheet = el('div', { class: 'sheet', id: 'lib-sheet', hidden: true,
      onclick: (e) => { if (e.target === this.sheet) this.close(); } },
      el('div', { class: 'card' },
        el('div', { class: 'card-head' },
          el('h3', { text: 'Libraries' }),
          el('span', { class: 'sub', id: 'lib-sub' }),
          el('span', { class: 'spacer', style: 'flex:1' }),
          el('button', { class: 'btn', text: 'Close', onclick: () => this.close() }),
        ),
        el('div', { class: 'card-body', id: 'lib-body' }),
      ),
    );
    document.body.append(this.sheet);
    addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && this.sheet && !this.sheet.hidden) this.close();
    });
  }

  render() {
    const body = $('#lib-body');
    if (!body) return;
    const nodes = [];

    if (this.scan) nodes.push(this._scanBar());
    if (this.msg) {
      nodes.push(el('div', { class: `msg ${this.msg.kind}`, text: this.msg.text }));
    }

    nodes.push(...this.libs.map((l) => this._libRow(l)));
    if (!this.libs.length) {
      nodes.push(el('p', { style: 'color:var(--fg-3);font-size:13px',
                           text: 'No libraries yet. Add one below.' }));
    }

    nodes.push(el('div', { class: 'divider', text: 'Add a library' }));
    nodes.push(this._addForm());

    body.replaceChildren(...nodes);
    const sub = $('#lib-sub');
    if (sub) {
      const docs = this.libs.reduce((n, l) => n + (l.docs || 0), 0);
      sub.textContent = `${this.libs.length} configured · ${docs.toLocaleString()} documents`;
    }
  }

  _scanBar() {
    const s = this.scan;
    const cls = s.state === 'error' ? 'err' : s.state === 'done' ? 'done' : '';
    if (s.state === 'running') {
      return el('div', { class: 'scan-bar' },
        el('div', { class: 'spin' }),
        el('span', { text: `Scanning ${s.label}` }),
        el('div', { class: 'track' }, el('i', { style: `width:${s.percent || 2}%` })),
        el('span', { style: 'font-family:var(--mono);font-size:11px',
                     text: s.total ? `${s.done}/${s.total}` : 'reading folders…' }),
      );
    }
    if (s.state === 'error') {
      return el('div', { class: 'scan-bar err' }, el('span', { text: `Scan failed: ${s.error}` }));
    }
    const r = s.result || {};
    return el('div', { class: 'scan-bar done' },
      el('span', { text: `Scanned ${s.label}: ${r.indexed || 0} indexed, `
        + `${(r.pages || 0).toLocaleString()} pages in ${r.elapsed?.toFixed?.(1) ?? '?'}s`
        + (r.errors ? ` · ${r.errors} errors` : '') }),
    );
  }

  _libRow(lib) {
    const busy = this.scan?.state === 'running';
    const bits = [];
    if (!lib.exists) bits.push(el('span', { class: 'warn', text: 'folder missing · ' }));
    bits.push(el('span', { text: lib.docs
      ? `${lib.docs.toLocaleString()} docs · ${(lib.pages || 0).toLocaleString()} pages · ${lib.profile}`
      : `not scanned yet · ${lib.profile}` }));

    return el('div', { class: `lib-row${lib.enabled ? '' : ' off'}` },
      el('div', { class: 'info' },
        el('div', { class: 'nm', text: lib.name }),
        el('div', { class: 'rt', text: lib.root, title: lib.root }),
        el('div', { class: 'st' }, bits),
      ),
      el('div', { class: 'acts' },
        el('select', {
          class: 'btn', style: 'height:29px;padding:0 6px',
          title: 'Grouping profile',
          onchange: (e) => this._patch(lib.id, { profile: e.target.value }),
        }, this.profiles.map((p) => el('option', {
          value: p, selected: p === lib.profile, text: p }))),
        el('button', {
          class: 'btn', text: lib.enabled ? 'On' : 'Off',
          title: lib.enabled ? 'Hide this library' : 'Show this library',
          onclick: () => this._patch(lib.id, { enabled: !lib.enabled }),
        }),
        el('button', {
          class: 'btn', text: 'Scan', disabled: busy || !lib.exists,
          title: 'Scan this library for new or changed files',
          onclick: () => this._scan(lib.id),
        }),
        el('button', {
          class: 'btn icon', html: ICON_TRASH, disabled: busy,
          title: `Remove ${lib.name} (does not delete any files)`,
          'aria-label': `Remove ${lib.name}`,
          onclick: () => this._remove(lib),
        }),
      ),
    );
  }

  _addForm() {
    const wrap = el('div', { class: 'form-grid' },
      el('label', { for: 'lib-name', text: 'Name' }),
      el('input', { type: 'text', id: 'lib-name', placeholder: 'Pathfinder' }),

      el('label', { for: 'lib-path', text: 'Folder' }),
      el('div', { class: 'path-row' },
        el('input', { type: 'text', id: 'lib-path', value: this.picked,
                      placeholder: '/mnt/media/books/…',
                      oninput: (e) => { this.picked = e.target.value; } }),
      ),

      el('label', { for: 'lib-profile', text: 'Grouping' }),
      el('select', { id: 'lib-profile' }, this.profiles.map((p) => el('option', {
        value: p, text: p === 'auto' ? 'auto — detect from filenames' : p }))),
      el('div', { class: 'hint',
        text: 'folders = group by containing folder · magazine = series and issue number' }),
    );

    const browser = el('div', { style: 'margin-top:14px' },
      el('div', { class: 'crumbs', id: 'lib-crumbs' }),
      el('div', { class: 'browser', id: 'lib-browser' }),
    );

    const foot = el('div', { class: 'card-foot', style: 'padding:14px 0 0;background:none;border:0' },
      el('button', {
        class: 'btn primary', id: 'lib-add', text: 'Add and scan',
        disabled: this.scan?.state === 'running',
        onclick: () => this._add(true),
      }),
      el('button', { class: 'btn', text: 'Add without scanning',
                     onclick: () => this._add(false) }),
    );

    const box = el('div', {}, wrap, browser, foot);
    queueMicrotask(() => this._renderBrowser());
    return box;
  }

  // ---------------------------------------------------------------- browser

  async openFolder(path) {
    try {
      const url = path ? `/api/admin/browse?path=${encodeURIComponent(path)}`
                       : '/api/admin/browse';
      this.browse = await api(url);
    } catch (err) {
      this.browse = { path, parent: null, entries: [], pdfs: 0, error: err.message };
    }
    this._renderBrowser();
  }

  _renderBrowser() {
    const crumbs = $('#lib-crumbs');
    const list = $('#lib-browser');
    if (!crumbs || !list || !this.browse) return;
    const b = this.browse;

    const parts = [];
    parts.push(el('button', { text: 'Places', onclick: () => this.openFolder(null) }));
    if (b.path) {
      const segs = b.path.split('/').filter(Boolean);
      let acc = '';
      for (const seg of segs) {
        acc += `/${seg}`;
        const target = acc;
        parts.push(el('span', { class: 'sep', text: '/' }));
        parts.push(el('button', { text: seg, onclick: () => this.openFolder(target) }));
      }
      parts.push(el('span', { class: 'sep', text: `  — ${pdfLabel(b)}` }));
    }
    crumbs.replaceChildren(...parts);

    const rows = [];
    if (b.error) {
      rows.push(el('div', { class: 'empty-note', text: b.error }));
    } else {
      if (b.path) {
        rows.push(el('button', {
          style: 'color:var(--accent-2);font-weight:600',
          onclick: () => { this._use(b.path); },
        }, el('span', { html: ICON_FOLDER }),
           `Use this folder  (${pdfLabel(b)})`));
      }
      for (const entry of b.entries) {
        rows.push(el('button', { onclick: () => this.openFolder(entry.path) },
          el('span', { html: ICON_FOLDER }), entry.name));
      }
      if (!rows.length) rows.push(el('div', { class: 'empty-note', text: 'No sub-folders here.' }));
    }
    list.replaceChildren(...rows);
  }

  _use(path) {
    this.picked = path;
    const input = $('#lib-path');
    if (input) input.value = path;
    const name = $('#lib-name');
    if (name && !name.value.trim()) {
      // A sensible default the user can overwrite.
      const leaf = path.split('/').filter(Boolean).pop() || '';
      name.value = leaf.replace(/[_-]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    }
  }

  // ---------------------------------------------------------------- actions

  async _add(thenScan) {
    const name = $('#lib-name')?.value.trim();
    const root = $('#lib-path')?.value.trim();
    const profile = $('#lib-profile')?.value || 'auto';
    if (!name || !root) {
      this.msg = { kind: 'err', text: 'Give the library a name and pick a folder.' };
      this.render();
      return;
    }
    try {
      const created = await api('/api/admin/libraries', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ name, root, profile }),
      });
      this.msg = { kind: 'ok',
        text: `Added ${created.name} — ${created.files} PDFs found`
            + ` (grouping: ${created.resolved_profile}).` };
      this.picked = '';
      await this.refresh();
      if (thenScan) await this._scan(created.id);
      this.onChanged();
    } catch (err) {
      this.msg = { kind: 'err', text: err.message };
      this.render();
    }
  }

  async _patch(id, changes) {
    try {
      await api(`/api/admin/libraries/${id}`, {
        method: 'PATCH',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(changes),
      });
      this.msg = null;
      await this.refresh();
      this.onChanged();
    } catch (err) {
      this.msg = { kind: 'err', text: err.message };
      this.render();
    }
  }

  async _remove(lib) {
    const ok = confirm(
      `Remove "${lib.name}" from the viewer?\n\n`
      + `Its ${(lib.docs || 0).toLocaleString()} documents leave the catalogue.\n`
      + `No files on disk are deleted.`);
    if (!ok) return;
    try {
      const res = await api(`/api/admin/libraries/${lib.id}`, { method: 'DELETE' });
      this.msg = { kind: 'ok', text: `Removed ${res.name} (${res.removed} documents).` };
      await this.refresh();
      this.onChanged();
    } catch (err) {
      this.msg = { kind: 'err', text: err.message };
      this.render();
    }
  }

  async _scan(libId) {
    try {
      this.scan = await api('/api/admin/scan', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ library: libId || null }),
      });
      this.render();
      this._pollScan();
    } catch (err) {
      this.msg = { kind: 'err', text: err.message };
      this.render();
    }
  }

  _pollScan() {
    clearTimeout(this.poll);
    this.poll = setTimeout(async () => {
      try {
        const s = await api('/api/admin/scan');
        if (s.state === 'idle') return;
        const finished = this.scan?.state === 'running' && s.state !== 'running';
        this.scan = s;
        this.render();
        if (s.state === 'running') this._pollScan();
        if (finished) { await this.refresh(); this.onChanged(); }
      } catch { /* the sheet may have closed */ }
    }, 700);
  }
}
