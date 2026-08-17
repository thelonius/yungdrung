'use strict';

// Лента «Что сегодня» и окно контроля. Раздел 6.1 и 6.4 ТЗ.
//
// Страница не вычисляет ничего: просрочку, состояние и время показа считает ядро,
// здесь только показ и отправка ответа. Это правило из КОНТРАКТ.md — иначе лента
// и завал однажды разойдутся, посчитав одно и то же по-разному.

const $ = (s, r = document) => r.querySelector(s);
const dlg = $('#control');

let текущий = null;     // шаг, про который открыто окно
let режим = null;       // 'notdone' | 'defer' | 'fail'
let причины = [];

async function get(url) {
  const r = await fetch(url);
  return r.json();
}

async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return r.json();
}

// --- отрисовка -------------------------------------------------------------

function строка(item, вЗавале) {
  const li = document.createElement('li');
  li.className = 'row' + (item.stalled ? ' is-stalled' : '');

  const main = document.createElement('div');
  main.className = 'row-main';

  const title = document.createElement('div');
  title.className = 'row-title';
  title.textContent = item.title;
  main.append(title);

  const sub = document.createElement('div');
  sub.className = 'row-sub';

  const task = document.createElement('span');
  task.textContent = item.task;
  sub.append(task);

  if (item.show_at) {
    const t = document.createElement('span');
    t.className = 'row-time';
    const d = new Date(item.show_at);
    t.textContent = вЗавале
      ? d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })
      : d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
    sub.append(t);
  }

  for (const tag of item.tags || []) {
    const s = document.createElement('span');
    s.className = 'tag';
    s.textContent = tag;
    sub.append(s);
  }

  if (item.postponed > 0) {
    const p = document.createElement('span');
    p.className = 'postponed';
    p.textContent = item.stalled
      ? `буксует, переносов ${item.postponed}`
      : `переносов ${item.postponed}`;
    sub.append(p);
  }

  main.append(sub);
  li.append(main);

  const actions = document.createElement('div');
  actions.className = 'row-actions';

  const tick = document.createElement('button');
  tick.type = 'button';
  tick.className = 'tick';
  tick.title = 'Сделано';
  tick.textContent = '✓';
  tick.addEventListener('click', () => действие('done', item));

  const open = document.createElement('button');
  open.type = 'button';
  open.className = 'open';
  open.title = 'Открыть окно контроля';
  open.textContent = '⋯';
  open.addEventListener('click', () => окно(item));

  actions.append(tick, open);
  li.append(actions);
  return li;
}

async function обновить() {
  const d = await get('/api/feed');

  $('#counters').replaceChildren(...[
    ['просрочено', d.counts.overdue, true],
    ['сегодня', d.counts.today, false],
    ['ждут', d.counts.waiting, false],
  ].map(([имя, n, горячий]) => {
    const s = document.createElement('span');
    if (горячий && n > 0) s.className = 'hot';
    s.innerHTML = `${имя} <b>${n}</b>`;
    return s;
  }));

  const plate = $('#plate');
  if (d.overdue_count > 0) {
    plate.hidden = false;
    plate.innerHTML = `<span>Просрочено: ${d.overdue_count}</span><span class="go">Разобрать →</span>`;
  } else {
    plate.hidden = true;
  }

  $('#feed').replaceChildren(...d.feed.map((i) => строка(i, false)));

  const empty = $('#empty');
  if (d.feed.length === 0) {
    empty.hidden = false;
    empty.textContent = d.next_ahead
      ? `На сегодня всё. Дальше: «${d.next_ahead.title}» — ${d.next_ahead.task}.`
      : 'На сегодня всё.';
  } else {
    empty.hidden = true;
  }
}

async function показатьЗавал() {
  const d = await get('/api/backlog');
  $('#backlog-list').replaceChildren(...d.backlog.map((i) => строка(i, true)));
  $('#backlog-view').hidden = false;
  $('#feed').hidden = true;
  $('#empty').hidden = true;
}

function скрытьЗавал() {
  $('#backlog-view').hidden = true;
  $('#feed').hidden = false;
  обновить();
}

// --- окно контроля ---------------------------------------------------------

function окно(item) {
  текущий = item;
  режим = null;
  $('#c-task').textContent = item.task;
  $('#c-step').textContent = item.title;

  const note = $('#c-note');
  note.hidden = !item.note;
  note.textContent = item.note || '';

  const meta = $('#c-meta');
  const части = [];
  if (item.show_at) {
    части.push('контроль ' + new Date(item.show_at)
      .toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }));
  }
  if (item.postponed > 0) части.push(`переносов: ${item.postponed}`);
  meta.innerHTML = части.join(' · ') +
    (item.stalled ? ' <span class="warn">— буксует, нужен другой ход</span>' : '');

  форму(false);
  dlg.showModal();
}

function форму(показать) {
  $('#c-form').hidden = !показать;
  $('#c-err').hidden = true;
  $('#c-reason').classList.remove('invalid');
  if (показать) {
    $('#c-date').value = '';
    $('#c-date-preview').textContent = '';
    $('.presets').querySelectorAll('button').forEach((b) => b.classList.remove('on'));
    // «Не будет сделано» закрывает шаг: новая дата ему не нужна, нужна причина.
    $('#c-date-field').hidden = режим === 'fail';
    $('#c-reason').focus();
  }
}

async function действие(op, item, extra = {}) {
  const r = await post('/api/action', {
    op, task: item.task, step: item.step, ...extra,
  });
  if (!r.ok) {
    const текст = (r.errors || []).map((e) => e.error).join('; ') || 'не получилось';
    if ($('#c-form').hidden) return всплывашка(текст);
    $('#c-err').textContent = текст;
    $('#c-err').hidden = false;
    return false;
  }
  dlg.close();
  всплывашка(итог(op, r));
  $('#backlog-view').hidden ? обновить() : показатьЗавал();
  return true;
}

function итог(op, r) {
  if (op === 'done') {
    return r.task_status === 'done'
      ? `«${r.task}» закрыта целиком`
      : `Сделано. Следующий шаг — ${r.next_step ?? '—'}`;
  }
  if (op === 'notdone') return r.hint ? `Отмечено. ${r.hint}` : 'Отмечено, вернёмся к нему';
  if (op === 'defer') return `Перенесено на ${r.next_check}`;
  if (op === 'fail') return 'Шаг закрыт как несостоявшийся, задача идёт дальше';
  return 'Готово';
}

function всплывашка(текст) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = текст;
  document.body.append(el);
  setTimeout(() => el.remove(), 3500);
}

// --- дата в форме ----------------------------------------------------------

let таймер;
async function превью() {
  clearTimeout(таймер);
  таймер = setTimeout(async () => {
    const текст = $('#c-date').value.trim();
    const out = $('#c-date-preview');
    if (!текст) return (out.textContent = '');
    const r = await post('/api/parse-date', { text: текст });
    out.textContent = r.ok ? (r.label || '') : 'не понял дату';
  }, 220);
}

// --- события ---------------------------------------------------------------

$('#plate').addEventListener('click', показатьЗавал);
$('#close-backlog').addEventListener('click', скрытьЗавал);

for (const b of document.querySelectorAll('[data-op]')) {
  b.addEventListener('click', () => {
    const op = b.dataset.op;
    if (op === 'done') return действие('done', текущий);
    режим = op;
    форму(true);
  });
}

$('#c-back').addEventListener('click', () => форму(false));
$('#c-date').addEventListener('input', превью);

for (const b of $('#c-presets').querySelectorAll('button')) {
  b.addEventListener('click', () => {
    $('#c-presets').querySelectorAll('button').forEach((x) => x.classList.remove('on'));
    b.classList.add('on');
    $('#c-date').value = b.dataset.when;
    превью();
  });
}

$('#c-save').addEventListener('click', () => {
  const причина = $('#c-reason').value;
  if (!причина) {
    $('#c-reason').classList.add('invalid');
    $('#c-err').textContent = 'Причина обязательна';
    $('#c-err').hidden = false;
    return;
  }
  действие(режим, текущий, { reason: причина, to: $('#c-date').value.trim() });
});

// Esc закрывает окно — и это «напомнить позже», а не ответ. Шаг остаётся в ленте.
dlg.addEventListener('close', () => { текущий = null; режим = null; });

(async function старт() {
  const r = await get('/api/reasons');
  причины = r.reasons || [];
  const sel = $('#c-reason');
  sel.replaceChildren(new Option('— выбери причину —', ''),
    ...причины.map((p) => new Option(p, p)));
  await обновить();
})();
