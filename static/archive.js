'use strict';

// Архив (история) и поиск. Раздел 5.9 ТЗ, требование R24.
//
// Список истории и результаты поиска — разные запросы к ядру, не два вида
// одного и того же: `/api/archive` группирует циклы повторения и понимает
// фильтры по тегу и периоду, `/api/search` ищет по леммам во всём вольте.
// Показывается одно или другое, а не оба разом.

const $ = (s, r = document) => r.querySelector(s);

async function get(url) {
  const r = await fetch(url);
  return r.json();
}

function короткаяДата(iso) {
  if (!iso) return '';
  const [y, m, d] = iso.split('-');
  return `${d}.${m}.${y}`;
}

// --- строка истории ----------------------------------------------------------

function строкаЗадачи(item, компактно = false) {
  const a = document.createElement('a');
  a.className = 'archive-row';
  a.href = `/задача?name=${encodeURIComponent(item.task)}`;

  const info = document.createElement('div');
  info.className = 'archive-row-info';
  const title = document.createElement('span');
  title.className = 'archive-row-title';
  title.textContent = item.task;
  info.append(title);
  if (!компактно) {
    const meta = document.createElement('span');
    meta.className = 'archive-row-meta';
    const слово = item.steps_total === 1 ? 'шаг' : item.steps_total < 5 ? 'шага' : 'шагов';
    meta.textContent = `${item.steps_total} ${слово}` +
      (item.category.length ? ' · ' + item.category.join(', ') : '');
    info.append(meta);
  }

  const право = document.createElement('div');
  право.style.cssText = 'display:flex; align-items:center; gap:10px; flex-shrink:0;';
  const статус = document.createElement('span');
  статус.className = 'archive-status' + (item.status === 'отменена' ? ' is-cancelled' : '');
  статус.textContent = item.status;
  const дата = document.createElement('span');
  дата.className = 'archive-row-date';
  дата.textContent = короткаяДата(item.date);
  право.append(статус, дата);

  a.append(info, право);
  return a;
}

function карточкаГруппы(группа, tpl) {
  const li = tpl.content.firstElementChild.cloneNode(true);
  $('.archive-group-title', li).textContent = группа.template_name;
  $('.archive-group-count', li).textContent = `${группа.count} циклов`;

  const список = $('.archive-cycles', li);
  список.append(...группа.tasks.map((t) => {
    const строкаLi = document.createElement('li');
    строкаLi.append(строкаЗадачи(t, true));
    return строкаLi;
  }));

  const кнопка = $('.archive-group-head', li);
  кнопка.addEventListener('click', () => {
    const открыто = !список.hidden;
    список.hidden = открыто;
    $('.archive-group-toggle', li).textContent = открыто ? 'Показать циклы ▾' : 'Свернуть ▴';
  });

  return li;
}

// --- список истории ------------------------------------------------------

const cycleTpl = $('#cycle-tpl');

function параметрыФильтра() {
  const qs = new URLSearchParams();
  const тег = $('#f-tag').value.trim();
  const сИ = $('#f-since').value.trim();
  const поИ = $('#f-until').value.trim();
  if (тег) qs.set('tag', тег);
  if (сИ) qs.set('since', сИ);
  if (поИ) qs.set('until', поИ);
  return qs;
}

async function загрузитьАрхив() {
  const d = await get('/api/archive?' + параметрыФильтра().toString());
  const list = $('#list');
  list.replaceChildren();
  if (!d.ok) {
    $('#empty').hidden = true;
    $('#count').textContent = (d.errors || []).map((e) => e.error).join('; ') || 'не получилось';
    return;
  }
  $('#empty').hidden = d.count > 0;
  $('#count').textContent = d.count ? `найдено: ${d.count}` : '';

  for (const item of d.items) {
    if (item.kind === 'cycle_group') {
      list.append(карточкаГруппы(item, cycleTpl));
      continue;
    }
    const li = document.createElement('li');
    li.className = 'archive-card';
    li.append(строкаЗадачи(item));
    list.append(li);
  }
}

// --- поиск -----------------------------------------------------------------

function строкаПоиска(r) {
  const li = document.createElement('li');
  li.className = 'archive-card';
  if (r.source_type === 'task') {
    li.append(строкаЗадачи({
      task: r.source_id, status: '', date: null, category: [], steps_total: 0,
    }, true));
    return li;
  }
  // Запись базы знаний: своей страницы у неё пока нет (см. открытые вопросы
  // по R3), поэтому просто показываем находку без ссылки, а не выдумываем
  // адрес, которого не существует.
  const обёртка = document.createElement('div');
  обёртка.className = 'archive-row';
  const info = document.createElement('div');
  info.className = 'archive-row-info';
  const вид = document.createElement('span');
  вид.className = 'search-kind';
  вид.textContent = 'запись базы знаний';
  const title = document.createElement('span');
  title.className = 'archive-row-title';
  title.textContent = r.title;
  info.append(вид, title);
  обёртка.append(info);
  li.append(обёртка);
  return li;
}

let поискТаймер;
async function поискИлиАрхив() {
  const текст = $('#q').value.trim();
  $('#filters').hidden = !!текст;
  if (!текст) return загрузитьАрхив();

  const d = await get('/api/search?q=' + encodeURIComponent(текст));
  const list = $('#list');
  list.replaceChildren();
  if (!d.ok) {
    $('#empty').hidden = false;
    $('#count').textContent = '';
    return;
  }
  $('#empty').hidden = d.count > 0;
  $('#count').textContent = d.count ? `найдено: ${d.count}` : '';
  list.append(...d.results.map(строкаПоиска));
}

$('#q').addEventListener('input', () => {
  clearTimeout(поискТаймер);
  поискТаймер = setTimeout(поискИлиАрхив, 220);
});

for (const id of ['#f-tag', '#f-since', '#f-until']) {
  $(id).addEventListener('input', () => {
    clearTimeout($(id)._t);
    $(id)._t = setTimeout(загрузитьАрхив, 250);
  });
}

$('#f-clear').addEventListener('click', () => {
  $('#f-tag').value = '';
  $('#f-since').value = '';
  $('#f-until').value = '';
  загрузитьАрхив();
});

загрузитьАрхив();
