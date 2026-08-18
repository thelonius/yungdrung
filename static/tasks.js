'use strict';

// Все задачи — плоский список поверх /api/tasks (engine.cmd_list). Страница не
// вычисляет статус и просрочку сама, только показывает то, что отдало ядро —
// тот же принцип, что и на ленте (см. КОНТРАКТ.md).

const $ = (s, r = document) => r.querySelector(s);

// Человеко-читаемые подписи статусов. Порядок здесь же задаёт порядок кнопок
// фильтра сверху.
const СТАТУС_RU = {
  overdue: 'просрочена',
  due: 'сегодня',
  waiting: 'ждёт',
  empty: 'делается',
  no_date: 'без даты',
  done: 'сделана',
  cancelled: 'отменена',
};

let задачи = [];
let фильтрСтатус = 'all';
let поиск = '';

async function get(url) {
  const r = await fetch(url);
  return r.json();
}

function строка(item) {
  const li = document.createElement('li');
  li.className = 'row trow status-' + item.status;
  li.tabIndex = 0;
  li.setAttribute('role', 'link');

  const main = document.createElement('div');
  main.className = 'row-main';

  const titleRow = document.createElement('div');
  titleRow.className = 'trow-title-row';

  const title = document.createElement('span');
  title.className = 'row-title';
  title.textContent = item.task;
  titleRow.append(title);

  const pill = document.createElement('span');
  pill.className = 'status-pill status-pill-' + item.status;
  pill.textContent = СТАТУС_RU[item.status] || item.status;
  titleRow.append(pill);

  main.append(titleRow);

  const sub = document.createElement('div');
  sub.className = 'row-sub';

  const progress = document.createElement('span');
  progress.className = 'trow-progress';
  progress.textContent = `${item.steps_done}/${item.steps_total}`;
  sub.append(progress);

  const current = document.createElement('span');
  current.className = 'trow-current';
  current.textContent = item.current || 'без текущего шага';
  sub.append(current);

  for (const tag of item.category || []) {
    const s = document.createElement('span');
    s.className = 'tag';
    s.textContent = tag;
    sub.append(s);
  }

  main.append(sub);
  li.append(main);

  const открыть = () => { location.href = '/задача?name=' + encodeURIComponent(item.task); };
  li.addEventListener('click', открыть);
  li.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); открыть(); }
  });

  return li;
}

function отрисовать() {
  const q = поиск.trim().toLowerCase();
  const отфильтровано = задачи.filter((t) => {
    if (фильтрСтатус !== 'all' && t.status !== фильтрСтатус) return false;
    if (q && !t.task.toLowerCase().includes(q)) return false;
    return true;
  });

  $('#tasks-list').replaceChildren(...отфильтровано.map(строка));

  const list = $('#tasks-list');
  const empty = $('#empty');
  if (отфильтровано.length === 0) {
    list.hidden = true;
    empty.hidden = false;
    empty.textContent = задачи.length === 0 ? 'Задач пока нет.' : 'Ничего не найдено.';
  } else {
    list.hidden = false;
    empty.hidden = true;
  }
}

function построитьФильтры() {
  const счётчики = {};
  for (const t of задачи) счётчики[t.status] = (счётчики[t.status] || 0) + 1;

  const кнопки = [['all', 'Все', задачи.length],
    ...Object.keys(СТАТУС_RU)
      .filter((s) => счётчики[s])
      .map((s) => [s, СТАТУС_RU[s], счётчики[s]])];

  $('#status-filters').replaceChildren(...кнопки.map(([код, имя, n]) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'ghost small filter-btn' + (код === фильтрСтатус ? ' is-active' : '');
    b.textContent = `${имя} (${n})`;
    b.addEventListener('click', () => {
      фильтрСтатус = код;
      построитьФильтры();
      отрисовать();
    });
    $('#status-filters').append(b);
    return b;
  }));
}

$('#search').addEventListener('input', (e) => {
  поиск = e.target.value;
  отрисовать();
});

(async function старт() {
  try {
    const d = await get('/api/tasks');
    задачи = d.tasks || [];
    построитьФильтры();
    отрисовать();
  } catch (e) {
    $('#load-err').textContent = 'Не удалось загрузить список задач.';
    $('#load-err').hidden = false;
  }
})();
