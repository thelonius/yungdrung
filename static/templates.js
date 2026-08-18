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
  info.append(name, meta);

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'primary small';
  btn.textContent = 'Завести задачу';
  btn.addEventListener('click', () => открыть(t.name));

  li.append(info, btn);
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

загрузить();
