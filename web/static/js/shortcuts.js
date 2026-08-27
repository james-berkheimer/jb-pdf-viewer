// The keyboard-shortcuts sheet: shows every binding and lets you change it.
import { $, el } from './util.js';
import { ACTIONS, RESERVED, pretty } from './keymap.js';

export class ShortcutsSheet {
  /**
   * @param {import('./keymap.js').Keymap} keymap
   * @param {() => void} onChange called after any binding changes
   */
  constructor(keymap, onChange) {
    this.keymap = keymap;
    this.onChange = onChange || (() => {});
    this.capturing = null;   // {id, replace} while waiting for a keypress
    this.note = null;
    this.sheet = $('#help');
    this.body = $('#help-body');
    this.sheet.addEventListener('click', (e) => {
      if (e.target === this.sheet) this.close();
    });
    $('#help-reset').onclick = () => {
      this.keymap.reset();
      this.note = 'All shortcuts restored to their defaults.';
      this.onChange();
      this.render();
    };
  }

  get open() { return !this.sheet.hidden; }

  toggle() { this.open ? this.close() : this.show(); }

  show() {
    this.note = null;
    this.capturing = null;
    this.render();
    this.sheet.hidden = false;
  }

  close() {
    this.capturing = null;
    this.sheet.hidden = true;
  }

  /**
   * Handle a keypress while the sheet is capturing a new binding.
   * @returns {boolean} true if the key was consumed
   */
  handleKey(event) {
    if (!this.capturing) return false;
    const key = event.key;

    if (key === 'Escape') {
      this.capturing = null;
      this.render();
      return true;
    }
    if (RESERVED.has(key)) {
      this.note = `${pretty(key)} is reserved and cannot be reassigned.`;
      this.capturing = null;
      this.render();
      return true;
    }

    const { id, replace } = this.capturing;
    const stolen = this.keymap.assign(id, key, { replace });
    const label = ACTIONS.find((a) => a.id === id)?.label ?? id;
    this.note = stolen
      ? `${pretty(key)} → ${label}, taken from ${
        ACTIONS.find((a) => a.id === stolen)?.label ?? stolen}.`
      : `${pretty(key)} → ${label}.`;
    this.capturing = null;
    this.onChange();
    this.render();
    return true;
  }

  // ---------------------------------------------------------------- render

  render() {
    if (!this.body) return;
    const groups = [];
    for (const action of ACTIONS) {
      let g = groups.find((x) => x.name === action.group);
      if (!g) groups.push(g = { name: action.group, rows: [] });
      g.rows.push(action);
    }

    const nodes = [];
    if (this.note) nodes.push(el('div', { class: 'key-note', text: this.note }));
    if (this.capturing) {
      nodes.push(el('div', { class: 'key-note capturing' },
        el('span', { text: 'Press the key you want to use — Esc to cancel' })));
    }

    for (const group of groups) {
      nodes.push(el('div', { class: 'key-group', text: group.name }));
      for (const action of group.rows) nodes.push(this._row(action));
    }
    this.body.replaceChildren(...nodes);

    const reset = $('#help-reset');
    if (reset) reset.disabled = !this.keymap.customised;
  }

  _row(action) {
    const keys = this.keymap.keysFor(action.id);
    const waiting = this.capturing?.id === action.id;

    const chips = keys.map((key) => el('button', {
      class: 'key-chip',
      title: `Change this key (${keys.length > 1 ? 'or remove it' : ''})`.trim(),
      onclick: () => { this.capturing = { id: action.id, replace: key }; this.note = null; this.render(); },
    },
      el('kbd', { class: 'k', text: pretty(key) }),
      keys.length > 1
        ? el('span', {
          class: 'drop', title: 'Remove this key', text: '×',
          onclick: (e) => {
            e.stopPropagation();
            this.keymap.unbind(action.id, key);
            this.note = `${pretty(key)} removed from ${action.label}.`;
            this.onChange();
            this.render();
          },
        })
        : null,
    ));

    if (!keys.length) {
      chips.push(el('span', { class: 'key-none', text: 'unbound' }));
    }

    return el('div', { class: `key-row${waiting ? ' waiting' : ''}` },
      el('span', { class: 'key-label' },
        action.label,
        this.keymap.isCustom(action.id)
          ? el('span', { class: 'key-changed', title: 'Changed from the default', text: '•' })
          : null),
      el('span', { class: 'key-chips' }, chips),
      el('button', {
        class: 'key-add', title: `Add another key for ${action.label}`,
        'aria-label': `Add a key for ${action.label}`, text: '+',
        onclick: () => { this.capturing = { id: action.id, replace: null }; this.note = null; this.render(); },
      }),
    );
  }
}
