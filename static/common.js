'use strict';

// Общий фронтенд-модуль: API, уведомления, стейт и поле даты.
// Текстовый ввод остаётся главным («+3», «завтра в полдесятого»), нативные
// <input type="date"> и type="time"> — для наглядности и точного выбора.
// Разбор дат — только через /api/parse-date (движок), не в браузере.

const Yd = {};

Yd.$ = (sel, root = document) => root.querySelector(sel);
Yd.$$ = (sel, root = document) => [...root.querySelectorAll(sel)];

Yd.get = async function get(url) {
  const r = await fetch(url);
  return r.json();
};

Yd.post = async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
  return r.json();
};

Yd.toast = function toast(текст) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = текст;
  document.body.append(el);
  setTimeout(() => el.remove(), 3500);
};

Yd.shortDate = function shortDate(iso) {
  if (!iso) return '';
  const [y, m, d] = String(iso).slice(0, 10).split('-');
  return `${d}.${m}.${y}`;
};

/** Простой реактивный стор: setState мержит патч, подписчики получают снимок. */
Yd.createStore = function createStore(initial = {}) {
  let state = { ...initial };
  const listeners = new Set();
  return {
    getState: () => state,
    setState(patch) {
      state = { ...state, ...patch };
      for (const fn of listeners) fn(state);
    },
    subscribe(fn) {
      listeners.add(fn);
      return () => listeners.delete(fn);
    },
  };
};

function el(tag, cls, attrs = {}) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function asNode(x) {
  return typeof x === 'string' ? Yd.$(x) : x;
}

function splitIso(iso) {
  if (!iso) return { date: '', time: '' };
  const s = String(iso);
  const [datePart, timePart] = s.includes('T') ? s.split('T') : s.split(' ');
  const time = (timePart || '').slice(0, 5);
  return { date: datePart || '', time: time && time !== '00:00' ? time : '' };
}

function buildFromPickers(dateVal, timeVal, withTime) {
  if (!dateVal) return '';
  if (!withTime || !timeVal) return dateVal;
  return `${dateVal} ${timeVal}`;
}

/**
 * Поле даты: текст + нативные пикеры + пресеты + превью через API.
 */
Yd.DateField = class DateField {
  constructor(opts) {
    this.text = asNode(opts.text);
    if (!this.text) throw new Error('DateField: нет text input');

    this.preview = opts.preview ? asNode(opts.preview) : null;
    this.presets = opts.presets ? asNode(opts.presets) : null;
    this.errorEl = opts.error ? asNode(opts.error) : null;
    this.invalidHint = opts.invalidHint
      || 'Не понял дату. Можно: 18.08 · 15 марта · завтра · +3 · пн · полдесятого';
    this.withTime = opts.withTime !== false;
    this.debounceMs = opts.debounce ?? 220;
    this.onChange = opts.onChange || (() => {});

    this._timer = null;
    this._syncing = false;
    this._lastParsed = null;

    if (opts.withPickers !== false) this._mountPickers();
    this._wire();
  }

  get value() {
    return this.text.value.trim();
  }

  set value(v) {
    this.text.value = v ?? '';
    this.refresh();
  }

  clear() {
    this.text.value = '';
    this._clearPreview();
    this._syncPickers('', '');
    this.resetPresets();
    this.onChange('');
  }

  resetPresets() {
    if (!this.presets) return;
    Yd.$$('button', this.presets).forEach((b) => b.classList.remove('on'));
  }

  async refresh() {
    clearTimeout(this._timer);
    await this._parse();
  }

  setPreview(text, { past = false } = {}) {
    if (!this.preview) return;
    this.preview.textContent = text || '';
    this.preview.classList.toggle('past', !!past);
  }

  _mountPickers() {
    const row = this.text.closest('.date-row') || this.text.parentElement;
    if (!row || row.querySelector('.date-field-pickers')) return;

    const pickers = el('div', 'date-field-pickers');
    this.datePicker = el('input', 'date-picker-native', { type: 'date', title: 'Календарь' });
    pickers.append(this.datePicker);
    if (this.withTime) {
      this.timePicker = el('input', 'date-picker-native', { type: 'time', title: 'Время' });
      pickers.append(this.timePicker);
    }

    if (this.preview && this.preview.parentElement === row) {
      row.insertBefore(pickers, this.preview);
    } else {
      this.text.after(pickers);
    }

    this.datePicker.addEventListener('change', () => this._fromPickers());
    if (this.timePicker) {
      this.timePicker.addEventListener('change', () => this._fromPickers());
    }
  }

  _wire() {
    this.text.addEventListener('input', () => this._schedule());
    this.text.addEventListener('blur', () => this.refresh());

    if (this.presets) {
      for (const btn of Yd.$$('button', this.presets)) {
        btn.addEventListener('click', () => {
          Yd.$$('button', this.presets).forEach((x) => x.classList.remove('on'));
          btn.classList.add('on');
          this.text.value = btn.dataset.when || '';
          this.refresh();
        });
      }
    }
  }

  _schedule() {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this._parse(), this.debounceMs);
  }

  _fromPickers() {
    if (!this.datePicker) return;
    const built = buildFromPickers(
      this.datePicker.value,
      this.timePicker?.value,
      this.withTime,
    );
    this._syncing = true;
    this.text.value = built;
    this._syncing = false;
    this.refresh();
  }

  _syncPickers(dateVal, timeVal) {
    if (!this.datePicker) return;
    this.datePicker.value = dateVal || '';
    if (this.timePicker) this.timePicker.value = timeVal || '';
  }

  _clearPreview() {
    this.text.classList.remove('invalid');
    if (this.preview) {
      this.preview.textContent = '';
      this.preview.classList.remove('past');
    }
    if (this.errorEl) this.errorEl.hidden = true;
  }

  async _parse() {
    const text = this.value;
    this._clearPreview();
    if (!text) {
      this._syncPickers('', '');
      this._lastParsed = null;
      this.onChange('');
      return;
    }

    try {
      const r = await Yd.post('/api/parse-date', { text });
      if (r.ok) {
        this._lastParsed = r.date;
        if (this.preview) {
          this.preview.textContent = r.label || '';
          this.preview.classList.toggle('past', !!r.past);
        }
        if (r.date && !this._syncing) {
          const { date, time } = splitIso(r.date);
          this._syncPickers(date, time);
        }
        this.onChange(text, r);
        return;
      }
      if (this.preview) this.preview.textContent = '';
      this.text.classList.add('invalid');
      if (this.errorEl) {
        this.errorEl.textContent = this.invalidHint;
        this.errorEl.hidden = false;
      } else if (this.preview) {
        this.preview.textContent = 'не понял дату';
      }
      this.onChange(text, r);
    } catch {
      if (this.preview) this.preview.textContent = '';
      this.onChange(text, null);
    }
  }
};

Yd.dateField = (opts) => new Yd.DateField(opts);
