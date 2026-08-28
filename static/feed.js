'use strict';

// Лента «Что сегодня», окно контроля и разбор завала. Раздел 6.1, 6.4, R20 ТЗ.
//
// Страница не вычисляет ничего: просрочку, состояние и время показа считает ядро,
// здесь только показ и отправка ответа. Это правило из КОНТРАКТ.md — иначе лента
// и завал однажды разойдутся, посчитав одно и то же по-разному.

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
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

// --- отрисовка ленты --------------------------------------------------------

function строка(item) {
  const li = document.createElement('li');
  li.className = 'row' + (item.stalled ? ' is-stalled' : '');

  const main = document.createElement('div');
  main.className = 'row-main';

  const title = document.createElement('div');
  title.className = 'row-title';
  title.textContent = item.title;
  title.title = item.title; // при обрезке длинного названия полный текст — в подсказке
  main.append(title);

  const sub = document.createElement('div');
  sub.className = 'row-sub';

  const task = document.createElement('span');
  // Подшаг параллельной группы несёт её название контекстом: «Сделка · Подписи».
  task.textContent = item.group ? `${item.task} · ${item.group}` : item.task;
  sub.append(task);

  if (item.show_at) {
    const t = document.createElement('span');
    t.className = 'row-time';
    t.textContent = new Date(item.show_at)
      .toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
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
    ['просрочено', d.counts.overdue, true, null],
    ['сегодня', d.counts.today, false, null],
    // «ждут» — единственный след всего бакета до issue #4: без ссылки сюда
    // задачи вроде «Нанять прораба...» (status waiting, дата контроля через
    // неделю) были видны только числом, дальше — только через поиск.
    ['ждут', d.counts.waiting, false, '/задачи'],
  ].map(([имя, n, горячий, ссылка]) => {
    const s = document.createElement(ссылка ? 'a' : 'span');
    if (ссылка) s.href = ссылка;
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

  $('#feed').replaceChildren(...d.feed.map((i) => строка(i)));

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

// --- разбор завала: общее состояние -----------------------------------------
//
// Список приходит с сервера уже отсортированным (самое давнее сверху, буксующее
// вперёд буксующего — cmd_backlog). Обе вкладки читают один и тот же массив и
// ничего в нём не переупорядочивают.

let завалСписок = [];              // текущий срез /api/backlog
let вкладкаЗавала = 'one';         // 'one' | 'list'
let индексОдного = 0;              // позиция в последовательном проходе
let последовательно = false;       // true, пока c-progress ведёт счёт «N из M»
let отвечено = false;              // защёлка: закрытие окна — ответ или Esc
let выбранныеЗавала = new Set();   // ключи выбранных строк в «Списком»
let режимПачки = null;             // 'defer' | 'fail' — какая форма открыта в «Списком»

function ключЭлемента(item) {
  return `${item.task}${item.step}`;
}

async function загрузитьЗавал() {
  const d = await get('/api/backlog');
  завалСписок = d.backlog;
  индексОдного = 0;
  выбранныеЗавала.clear();
}

async function показатьЗавал() {
  await загрузитьЗавал();
  последовательно = false;
  $('#backlog-view').hidden = false;
  $('#feed').hidden = true;
  $('#empty').hidden = true;
  переключитьВкладку(вкладкаЗавала, true);
}

function скрытьЗавал() {
  последовательно = false;
  $('#backlog-view').hidden = true;
  $('#feed').hidden = false;
  обновить();
}

function переключитьВкладку(таб, силой) {
  if (!силой && таб === вкладкаЗавала) return;
  вкладкаЗавала = таб;
  for (const b of $$('#backlog-tabs .tab')) {
    const on = b.dataset.tab === таб;
    b.classList.toggle('on', on);
    b.setAttribute('aria-selected', on ? 'true' : 'false');
  }
  $('#pane-one').hidden = таб !== 'one';
  $('#pane-list').hidden = таб !== 'list';
  if (таб === 'one') {
    начатьПоОдному();
  } else {
    последовательно = false;
    рендерСписок();
  }
}

// --- «По одному» — основной режим, R20 -------------------------------------

function рендерПрогресс() {
  const прогресс = $('#backlog-progress');
  const пусто = $('#backlog-empty');
  const кнопка = $('#backlog-resume');

  if (завалСписок.length === 0) {
    прогресс.textContent = '';
    пусто.hidden = false;
    кнопка.hidden = true;
    return;
  }
  пусто.hidden = true;

  if (индексОдного >= завалСписок.length) {
    прогресс.textContent = 'Разобрано всё.';
    кнопка.hidden = true;
    return;
  }
  прогресс.textContent = `${индексОдного + 1} из ${завалСписок.length}`;
  кнопка.hidden = false;
}

function начатьПоОдному() {
  рендерПрогресс();
  if (завалСписок.length === 0) return;
  if (индексОдного >= завалСписок.length) return завершитьЗавал();
  открытьПоследовательно();
}

function открытьПоследовательно() {
  последовательно = true;
  окно(завалСписок[индексОдного], true);
  рендерПрогресс();
}

function завершитьЗавал() {
  скрытьЗавал();
}

// --- «Списком» — таблица и массовые действия, R20 ---------------------------

function синхронизироватьSelectAll() {
  const all = $('#backlog-select-all');
  const n = завалСписок.length;
  const выбрано = выбранныеЗавала.size;
  all.checked = n > 0 && выбрано === n;
  all.indeterminate = выбрано > 0 && выбрано < n;
}

function обновитьПанельВыбора() {
  const n = выбранныеЗавала.size;
  $('#bulk-bar').hidden = n === 0;
  $('#bulk-count').textContent = `выбрано: ${n}`;
  синхронизироватьSelectAll();
  if (n === 0) формуПачки(false);
}

function рядЗавала(item) {
  const tr = document.createElement('tr');
  if (item.stalled) tr.className = 'is-stalled';
  const ключ = ключЭлемента(item);

  const tdCheck = document.createElement('td');
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = выбранныеЗавала.has(ключ);
  cb.addEventListener('change', () => {
    if (cb.checked) выбранныеЗавала.add(ключ);
    else выбранныеЗавала.delete(ключ);
    обновитьПанельВыбора();
  });
  tdCheck.append(cb);

  const tdStep = document.createElement('td');
  tdStep.textContent = item.title;

  const tdTask = document.createElement('td');
  tdTask.textContent = item.group ? `${item.task} · ${item.group}` : item.task;

  const tdWhen = document.createElement('td');
  tdWhen.className = 'row-time';
  tdWhen.textContent = item.show_at
    ? new Date(item.show_at).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })
    : '';

  const tdPostponed = document.createElement('td');
  tdPostponed.textContent = item.postponed > 0 ? String(item.postponed) : '—';
  if (item.stalled) tdPostponed.className = 'postponed';

  tr.append(tdCheck, tdStep, tdTask, tdWhen, tdPostponed);
  return tr;
}

function рендерСписок() {
  выбранныеЗавала.clear();
  $('#backlog-tbody').replaceChildren(...завалСписок.map(рядЗавала));
  обновитьПанельВыбора();
}

$('#backlog-select-all').addEventListener('change', (e) => {
  выбранныеЗавала.clear();
  if (e.target.checked) {
    for (const item of завалСписок) выбранныеЗавала.add(ключЭлемента(item));
  }
  for (const cb of $$('#backlog-tbody input[type=checkbox]')) cb.checked = e.target.checked;
  обновитьПанельВыбора();
});

function формуПачки(показать) {
  $('#bulk-form').hidden = !показать;
  $('#bulk-err').hidden = true;
  $('#bulk-reason').classList.remove('invalid');
  if (показать) {
    $('#bulk-date').value = '';
    $('#bulk-date-preview').textContent = '';
    $$('#bulk-presets button').forEach((b) => b.classList.remove('on'));
    $('#bulk-date-field').hidden = режимПачки === 'fail';
    $('#bulk-reason').focus();
  }
}

$('#bulk-done').addEventListener('click', () => действиеПачкой('done'));
$('#bulk-defer').addEventListener('click', () => { режимПачки = 'defer'; формуПачки(true); });
$('#bulk-fail').addEventListener('click', () => { режимПачки = 'fail'; формуПачки(true); });
$('#bulk-cancel').addEventListener('click', () => формуПачки(false));

$('#bulk-date').addEventListener('input', превьюПачки);

for (const b of $$('#bulk-presets button')) {
  b.addEventListener('click', () => {
    $$('#bulk-presets button').forEach((x) => x.classList.remove('on'));
    b.classList.add('on');
    $('#bulk-date').value = b.dataset.when;
    превьюПачки();
  });
}

let таймерПачки;
async function превьюПачки() {
  clearTimeout(таймерПачки);
  таймерПачки = setTimeout(async () => {
    const текст = $('#bulk-date').value.trim();
    const out = $('#bulk-date-preview');
    if (!текст) return (out.textContent = '');
    const r = await post('/api/parse-date', { text: текст });
    out.textContent = r.ok ? (r.label || '') : 'не понял дату';
  }, 220);
}

$('#bulk-save').addEventListener('click', () => {
  const причина = $('#bulk-reason').value;
  if (!причина) {
    $('#bulk-reason').classList.add('invalid');
    $('#bulk-err').textContent = 'Причина обязательна';
    $('#bulk-err').hidden = false;
    return;
  }
  const extra = { reason: причина };
  if (режимПачки === 'defer') extra.to = $('#bulk-date').value.trim();
  действиеПачкой(режимПачки, extra);
});

function итогПачки(op, r) {
  const слово = {
    done: 'отмечено сделанным',
    defer: 'перенесено',
    fail: 'отмечено «не будет сделано»',
  }[op] || 'готово';
  return r.fail_count > 0
    ? `${r.ok_count} из ${r.count}: ${слово}; ${r.fail_count} — с ошибкой`
    : `${r.ok_count} из ${r.count}: ${слово}`;
}

async function действиеПачкой(op, extra = {}) {
  const items = [...выбранныеЗавала].map((ключ) => {
    const [task, step] = ключ.split('');
    return { task, step };
  });
  if (items.length === 0) return;

  const r = await post('/api/backlog-bulk', { op, items, ...extra });
  // Успешный разбор пачки не несёт поля ok — оно есть только у отказа всей
  // пачки целиком (неизвестная операция, не разобраны причина/дата).
  if (r.errors) {
    const текст = r.errors.map((e) => e.error).join('; ');
    if ($('#bulk-form').hidden) return всплывашка(текст);
    $('#bulk-err').textContent = текст;
    $('#bulk-err').hidden = false;
    return;
  }

  формуПачки(false);
  всплывашка(итогПачки(op, r));
  // Пачка меняет и то, что попадает в завал, и то, что видно в шапке ленты
  // (просрочено/сегодня/ждут) — без этого счётчики держат старые числа, пока
  // не закроешь разбор или не перезагрузишь страницу.
  await Promise.all([загрузитьЗавал(), обновить()]);
  переключитьВкладку('list', true);
}

// --- события вкладок и выхода из разбора завала ------------------------------

$('#plate').addEventListener('click', показатьЗавал);
$('#close-backlog').addEventListener('click', скрытьЗавал);
$('#backlog-resume').addEventListener('click', начатьПоОдному);

for (const b of $$('#backlog-tabs .tab')) {
  b.addEventListener('click', () => переключитьВкладку(b.dataset.tab));
}

// --- окно контроля -----------------------------------------------------------

function окно(item, посл = false) {
  текущий = item;
  режим = null;
  $('#c-task').textContent = item.task;
  $('#c-step').textContent = item.title;

  const progress = $('#c-progress');
  if (посл) {
    progress.hidden = false;
    progress.textContent = `${индексОдного + 1} из ${завалСписок.length}`;
  } else {
    progress.hidden = true;
  }

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
  if (!dlg.open) dlg.showModal();
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
  всплывашка(итог(op, r));

  if (последовательно) {
    // R20: любой из четырёх ответов продвигает очередь на следующий элемент.
    // Модал НЕ закрываем здесь: close() у <dialog> доставляет событие 'close'
    // асинхронно, и если тут же переоткрыть окно на следующем шаге, старое
    // событие долетит уже после и обнулит «текущий» под новым элементом.
    // Вместо закрытия/переоткрытия просто подменяем содержимое того же
    // открытого диалога.
    индексОдного += 1;
    // Не ждём здесь: счётчики шапки (просрочено/сегодня/ждут) — фон, следующий
    // шаг очереди не должен ждать лишний круг до сервера.
    обновить();
    if (индексОдного >= завалСписок.length) {
      последовательно = false;
      отвечено = true;
      dlg.close();
      завершитьЗавал();
    } else {
      открытьПоследовательно();
    }
    return true;
  }

  отвечено = true;
  dlg.close();
  if (!$('#backlog-view').hidden) {
    await показатьЗавал();
  } else {
    обновить();
  }
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

// --- дата в форме одиночного окна --------------------------------------------

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

// --- события одиночного окна --------------------------------------------------

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

// Клик по подложке закрывает окно так же, как Esc — родное поведение
// <dialog> тут молчит, обработчик нужен явно. target === dlg только у клика
// вне карточки: сама карточка получает событие раньше и не даёт ему всплыть
// с себя как с dlg.
dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });

// Esc/крестик закрывают окно — и это «напомнить позже», а не ответ. В обычном
// режиме шаг просто остаётся в ленте; в разборе завала («по одному») счётчик
// не продвигается — очередь ждёт на месте, пока не нажмут «Продолжить разбор».
dlg.addEventListener('close', () => {
  текущий = null; режим = null;
  const былОтвет = отвечено;
  отвечено = false;
  if (последовательно && !былОтвет) {
    последовательно = false;
    рендерПрогресс();
  }
});

// «В шаблон» — не одно из четырёх действий про ответ на шаг (раздел 6.4), а
// отдельная операция над задачей целиком. Кнопка рядом, но окно не закрывает:
// человек может передумать и всё равно ответить.
$('#c-to-template').addEventListener('click', async () => {
  if (!текущий) return;
  const r = await post('/api/template-from-task', { task: текущий.task });
  const кнопка = $('#c-to-template');
  const исходный = кнопка.textContent;
  кнопка.textContent = r.ok ? `Готово: «${r.template}»` : 'Не вышло';
  кнопка.disabled = true;
  setTimeout(() => { кнопка.textContent = исходный; кнопка.disabled = false; }, 2500);
});

(async function старт() {
  const r = await get('/api/reasons');
  причины = r.reasons || [];
  for (const sel of [$('#c-reason'), $('#bulk-reason')]) {
    sel.replaceChildren(new Option('— выбери причину —', ''),
      ...причины.map((p) => new Option(p, p)));
  }
  await обновить();
})();
