// Keyboard bindings: defaults, user overrides, and display formatting.
//
// Actions are named so a binding can move without the reader caring which key
// triggered it. Overrides live in localStorage and only record what differs
// from the defaults, so new actions pick up their default binding rather than
// arriving unbound for anyone who has customised a key.

const STORE = 'pdfv.keys';

/** Ordered for display; `group` becomes a heading in the shortcuts editor. */
export const ACTIONS = [
  { id: 'nextPage',   group: 'Reading',   label: 'Next page' },
  { id: 'prevPage',   group: 'Reading',   label: 'Previous page' },
  { id: 'firstPage',  group: 'Reading',   label: 'First page' },
  { id: 'lastPage',   group: 'Reading',   label: 'Last page' },
  { id: 'gotoPage',   group: 'Reading',   label: 'Go to page…' },
  { id: 'prevIssue',  group: 'Reading',   label: 'Previous document' },
  { id: 'nextIssue',  group: 'Reading',   label: 'Next document' },

  { id: 'modeSingle', group: 'View',      label: 'Single page' },
  { id: 'modeSpread', group: 'View',      label: 'Two-page spread' },
  { id: 'modeScroll', group: 'View',      label: 'Continuous scroll' },
  { id: 'spreadOffset', group: 'View',    label: 'Offset spread pairing' },
  { id: 'surface',    group: 'View',      label: 'Reading surface' },
  { id: 'fullscreen', group: 'View',      label: 'Fullscreen' },

  { id: 'magnifier',  group: 'Zoom',      label: 'Magnifier' },
  { id: 'zoomIn',     group: 'Zoom',      label: 'Zoom in' },
  { id: 'zoomOut',    group: 'Zoom',      label: 'Zoom out' },
  { id: 'zoomFit',    group: 'Zoom',      label: 'Fit page / fit width' },

  { id: 'sidebar',    group: 'Panels',    label: 'Toggle sidebar' },
  { id: 'find',       group: 'Panels',    label: 'Find in document' },
  { id: 'help',       group: 'Panels',    label: 'Keyboard shortcuts' },
];

export const DEFAULTS = {
  nextPage:   ['ArrowRight', 'PageDown', ' ', 'l', 'j'],
  prevPage:   ['ArrowLeft', 'PageUp', 'h', 'k'],
  firstPage:  ['Home'],
  lastPage:   ['End'],
  gotoPage:   ['g'],
  prevIssue:  ['['],
  nextIssue:  [']'],

  modeSingle: ['1'],
  modeSpread: ['2'],
  modeScroll: ['3'],
  spreadOffset: ['o'],
  surface:    ['b'],
  fullscreen: ['f'],

  magnifier:  ['z'],
  zoomIn:     ['+', '='],
  zoomOut:    ['-'],
  zoomFit:    ['0'],

  sidebar:    ['t'],
  find:       ['/'],
  help:       ['?'],
};

// Escape is the universal "get me out" and stays unbindable so the reader can
// never be left with no way to cancel a mode.
export const RESERVED = new Set(['Escape', 'Tab', 'Enter', 'Shift', 'Control',
  'Alt', 'Meta', 'CapsLock', 'Dead']);

const PRETTY = {
  ' ': 'Space', ArrowRight: '→', ArrowLeft: '←', ArrowUp: '↑', ArrowDown: '↓',
  PageDown: 'PgDn', PageUp: 'PgUp', Escape: 'Esc',
};

export function pretty(key) {
  return PRETTY[key] ?? (key.length === 1 ? key.toUpperCase() : key);
}

function loadOverrides() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORE) || '{}');
    return raw && typeof raw === 'object' ? raw : {};
  } catch {
    return {};
  }
}

export class Keymap {
  constructor() {
    this.overrides = loadOverrides();
  }

  /** Keys bound to an action, falling back to its default. */
  keysFor(id) {
    const custom = this.overrides[id];
    return Array.isArray(custom) ? custom : (DEFAULTS[id] ?? []);
  }

  /** Which action a keydown should run, or null. */
  actionFor(key) {
    for (const { id } of ACTIONS) {
      if (this.keysFor(id).includes(key)) return id;
    }
    return null;
  }

  /** The action currently using `key`, ignoring `exceptId`. */
  conflict(key, exceptId) {
    for (const { id } of ACTIONS) {
      if (id !== exceptId && this.keysFor(id).includes(key)) return id;
    }
    return null;
  }

  /**
   * Bind `key` to `id`. A key can only drive one action, so binding one that
   * is already in use takes it from the other action rather than leaving two
   * handlers racing for the same press.
   * @returns {string|null} the action the key was taken from
   */
  assign(id, key, { replace = null } = {}) {
    const stolenFrom = this.conflict(key, id);
    if (stolenFrom) {
      this.overrides[stolenFrom] =
        this.keysFor(stolenFrom).filter((k) => k !== key);
    }
    const current = this.keysFor(id);
    const next = replace
      ? current.map((k) => (k === replace ? key : k))
      : [...current, key];
    this.overrides[id] = [...new Set(next)];
    this.save();
    return stolenFrom;
  }

  unbind(id, key) {
    this.overrides[id] = this.keysFor(id).filter((k) => k !== key);
    this.save();
  }

  reset(id = null) {
    if (id) delete this.overrides[id];
    else this.overrides = {};
    this.save();
  }

  isCustom(id) {
    return Array.isArray(this.overrides[id]);
  }

  get customised() {
    return Object.keys(this.overrides).length > 0;
  }

  save() {
    // Drop entries that match the default so defaults can evolve later.
    for (const [id, keys] of Object.entries(this.overrides)) {
      const def = DEFAULTS[id] ?? [];
      if (keys.length === def.length && keys.every((k, i) => k === def[i])) {
        delete this.overrides[id];
      }
    }
    try {
      localStorage.setItem(STORE, JSON.stringify(this.overrides));
    } catch { /* private mode; bindings last for this session only */ }
  }
}
