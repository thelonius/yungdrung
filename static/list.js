'use strict';

// Все задачи — issue #4: бакет «ждут» (и бездатные задачи внутри него) не был
// достижим ни с ленты, ни с архива, только числом в счётчике. Страница зовёт
// `/api/tasks`, который уже был готов на сервере (cmd_list), но им никто не
// пользовался — сюда ничего не добавлено, кроме фильтра по статусу, который
// считает движок (см. КОНТРАКТ.md: оболочка не решает, что просрочено).

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

async function get(url) {
  const r = await fetch(url);
  return r.json();
}

function короткаяДата(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-');
  return `${d}.${m}.${y}`;
}

function строкаЗадачи(item) {
  const li = document.createElement('li');
  li.className = 'archive-card';

  const a = document.createElement('a');
  a.className = 'archive-row';
  a.href = `/задача?name=${encodeURIComponent(item.task)}`;

  const info = document.createElement('div');
  info.className = 'archive-row-info';
  const title = document.createElement('span');
  title.className = 'archive-row-title';
  title.textContent = item.task;
  info.append(title);

  const meta = document.createElement('span');
  meta.className = 'archive-row-meta';
  const части = [`${item.steps_done}/${item.steps_total} шагов`];
  if (item.current) части.push(item.current);
  if (item.category.length) части.push(item.category.join(', '));
  meta.textContent = части.join(' · ');
  info.append(meta);

  const право = document.createElement('div');
  право.style.cssText = 'display:flex; align-items:center; gap:10px; flex-shrink:0;';

  const статус = document.createElement('span');
  статус.className = `archive-status is-${item.status.replace('_', '-')}`;
  статус.textContent = item.status_text;

  const дата = document.createElement('span');
  дата.className = 'archive-row-date';
  дата.textContent = item.control_date ? короткаяДата(item.control_date) : 'без даты';

  право.append(статус, дата);
  a.append(info, право);
  li.append(a);
  return li;
}

let текущийСтатус = 'waiting';

async function загрузить() {
  const qs = текущийСтатус ? `?status=${encodeURIComponent(текущийСтатус)}` : '';
  const d = await get('/api/tasks' + qs);
  const list = $('#list');
  list.replaceChildren();

  $('#empty').hidden = d.tasks.length > 0;
  $('#count').textContent = d.tasks.length ? `задач: ${d.tasks.length}` : '';
  list.append(...d.tasks.map(строкаЗадачи));
}

for (const tab of $$('#tabs .tab')) {
  tab.addEventListener('click', () => {
    if (tab.dataset.status === текущийСтатус) return;
    текущийСтатус = tab.dataset.status;
    for (const b of $$('#tabs .tab')) {
      const on = b === tab;
      b.classList.toggle('on', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    }
    загрузить();
  });
}

загрузить();
