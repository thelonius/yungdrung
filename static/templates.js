'use strict';

// Список шаблонов и развёртывание в задачу. Раздел 5.6 ТЗ, требование R22.
//
// Предпросмотр и разбор даты старта считает ядро — страница только показывает.
// То же правило, что на ленте: КОНТРАКТ.md запрещает оболочке вычислять то,
// что должно вычислять ядро.

const $ = (s, r = document) => r.querySelector(s);
const dlg = $('#expand');

let текущий = null; // имя шаблона, открытого в диалоге

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

function карточка(t) {
  const li = document.createElement('li');
  li.className = 'tpl-card';

  const info = document.createElement('div');
  info.className = 'tpl-info';
  const name = document.createElement('div');
  name.className = 'tpl-name';
  name.textContent = t.name;
  const meta = document.createElement('div');
  meta.className = 'tpl-meta';
  const слово = t.steps === 1 ? 'шаг' : t.steps < 5 ? 'шага' : 'шагов';
  meta.textContent = `${t.steps} ${слово}` + (t.tags.length ? ' · ' + t.tags.join(', ') : '');
  const recur = document.createElement('div');
  recur.className = 'tpl-recur ' + (t.recurrence ? 'is-set' : 'is-unset');
  recur.textContent = t.recurrence ? '↻ ' + t.recurrence.description : 'без повторения';
  info.append(name, meta, recur);

  const actions = document.createElement('div');
  actions.className = 'tpl-actions';

  const recurBtn = document.createElement('button');
  recurBtn.type = 'button';
  recurBtn.className = 'ghost small';
  recurBtn.textContent = t.recurrence ? 'Повторение' : 'Настроить повтор';
  recurBtn.addEventListener('click', () => открытьПовторение(t));

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'primary small';
  btn.textContent = 'Завести задачу';
  btn.addEventListener('click', () => открыть(t.name));

  actions.append(recurBtn, btn);
  li.append(info, actions);
  return li;
}

async function загрузить() {
  const d = await get('/api/templates');
  const list = $('#list');
  list.replaceChildren();
  $('#empty').hidden = d.templates.length > 0;
  for (const t of d.templates) list.append(карточка(t));
}

// --- диалог развёртывания ---------------------------------------------------

async function открыть(name) {
  текущий = name;
  $('#x-name').textContent = name;
  $('#x-start').value = '';
  $('#x-title').value = '';
  $('#x-title').placeholder = `по умолчанию — «${name}»`;
  $('#x-err').hidden = true;
  await обновитьПредпросмотр('сегодня');
  dlg.showModal();
}

async function обновитьПредпросмотр(текстДаты) {
  const d = await get(`/api/templates?start=${encodeURIComponent(текстДаты || 'сегодня')}`);
  const шаблон = d.templates.find((t) => t.name === текущий);
  const box = $('#x-preview');
  box.replaceChildren();
  if (!шаблон) return;
  for (const шаг of шаблон.preview) {
    const li = document.createElement('li');
    const title = document.createElement('span');
    title.className = 'p-title';
    title.textContent = шаг.title;
    const when = document.createElement('span');
    when.className = 'p-when' + (шаг.on_weekend ? ' weekend' : '');
    when.textContent = шаг.control_text || шаг.control_date || '';
    li.append(title, when);
    box.append(li);
  }
}

$('#x-start').addEventListener('input', (e) => {
  clearTimeout($('#x-start')._t);
  $('#x-start')._t = setTimeout(() => обновитьПредпросмотр(e.target.value), 200);
});

for (const btn of dlg.querySelectorAll('.presets button')) {
  btn.addEventListener('click', () => {
    $('#x-start').value = btn.dataset.when;
    обновитьПредпросмотр(btn.dataset.when);
  });
}

$('#x-back').addEventListener('click', () => dlg.close());

$('#x-save').addEventListener('click', async () => {
  $('#x-err').hidden = true;
  const save = $('#x-save');
  save.disabled = true;
  try {
    const r = await post('/api/from-template', {
      name: текущий,
      start: $('#x-start').value.trim() || 'сегодня',
      title: $('#x-title').value.trim() || null,
    });
    if (!r.ok) {
      const текст = (r.errors || []).map((e) => e.error).join('; ') || 'не сохранилось';
      $('#x-err').textContent = текст;
      $('#x-err').hidden = false;
      return;
    }
    dlg.close();
    location.href = '/';
  } catch (e) {
    $('#x-err').textContent = 'сервер не ответил: ' + e.message;
    $('#x-err').hidden = false;
  } finally {
    save.disabled = false;
  }
});

// --- настройка повторения ---------------------------------------------------
//
// Правило собирается на странице, но проверяет и описывает его ядро — тот же
// принцип, что у предпросмотра развёртывания. Форма не решает, что означает
// «раз в месяц» или подходит ли число месяца: она просто показывает ответ.

const recurDlg = $('#recur');
let повторЦелевой = null;

function собратьПравило() {
  const freq = $('#r-freq').value;
  const rule = { freq };
  const interval = parseInt($('#r-interval').value, 10);
  if (interval && interval !== 1) rule.interval = interval;

  if (freq === 'weekly') {
    const дни = [...$('#r-weekdays').querySelectorAll('input:checked')].map((c) => +c.value);
    if (дни.length) rule.byweekday = дни;
  }
  if (freq === 'monthly' || freq === 'yearly') {
    const текст = $('#r-monthday').value.trim();
    if (текст) {
      const числа = текст.split(',').map((s) => parseInt(s.trim(), 10)).filter((n) => !Number.isNaN(n));
      if (числа.length) rule.bymonthday = числа;
    }
  }
  return rule;
}

function показатьПоля() {
  const freq = $('#r-freq').value;
  $('#r-weekday-field').hidden = freq !== 'weekly';
  $('#r-monthday-field').hidden = !(freq === 'monthly' || freq === 'yearly');
}

async function обновитьОписание() {
  const r = await post('/api/recurrence-preview', {
    anchor: $('#r-anchor').value.trim() || null,
    rule: собратьПравило(),
  });
  const desc = $('#r-desc');
  const err = $('#r-err');
  if (r.ok) {
    err.hidden = true;
    const даты = r.preview.map((p) => p.date.slice(8, 10) + '.' + p.date.slice(5, 7)).join(', ');
    desc.textContent = `${r.description} — ближайшие: ${даты}`;
  } else {
    desc.textContent = '';
    err.textContent = (r.errors || []).map((e) => e.error).join('; ');
    err.hidden = false;
  }
}

function открытьПовторение(t) {
  повторЦелевой = t.name;
  $('#r-name').textContent = t.name;
  $('#r-err').hidden = true;

  const правило = t.recurrence;
  $('#r-freq').value = правило ? правило.freq : 'monthly';
  $('#r-interval').value = правило && правило.interval ? правило.interval : 1;
  $('#r-anchor').value = правило ? правило.anchor : '';
  for (const c of $('#r-weekdays').querySelectorAll('input')) {
    c.checked = !!(правило && правило.byweekday || []).includes(+c.value);
  }
  $('#r-monthday').value = правило && правило.bymonthday ? правило.bymonthday.join(', ') : '';

  показатьПоля();
  обновитьОписание();
  recurDlg.showModal();
}

$('#r-freq').addEventListener('change', () => { показатьПоля(); обновитьОписание(); });
for (const id of ['#r-interval', '#r-monthday', '#r-anchor']) {
  $(id).addEventListener('input', () => {
    clearTimeout($(id)._t);
    $(id)._t = setTimeout(обновитьОписание, 250);
  });
}
$('#r-weekdays').addEventListener('change', обновитьОписание);

$('#r-back').addEventListener('click', () => recurDlg.close());

$('#r-save').addEventListener('click', async () => {
  $('#r-err').hidden = true;
  const якорь = $('#r-anchor').value.trim();
  if (!якорь) {
    $('#r-err').textContent = 'Нужна дата первого цикла';
    $('#r-err').hidden = false;
    return;
  }
  const save = $('#r-save');
  save.disabled = true;
  try {
    const r = await post('/api/template-recurrence', {
      name: повторЦелевой, rule: { anchor: якорь, ...собратьПравило() },
    });
    if (!r.ok) {
      $('#r-err').textContent = (r.errors || []).map((e) => e.error).join('; ') || 'не сохранилось';
      $('#r-err').hidden = false;
      return;
    }
    recurDlg.close();
    await загрузить();
  } finally {
    save.disabled = false;
  }
});

$('#r-clear').addEventListener('click', async () => {
  const save = $('#r-clear');
  save.disabled = true;
  try {
    await post('/api/template-recurrence', { name: повторЦелевой, clear: true });
    recurDlg.close();
    await загрузить();
  } finally {
    save.disabled = false;
  }
});

загрузить();
