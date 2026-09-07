'use strict';

// База знаний — плоский список поверх /api/kb/notes (engine.cmd_kb_note_list).
// Страница не ходит на сервер за каждым нажатием клавиши: список грузится один
// раз, фильтр работает по уже загруженным данным. Тот же приём, что на «Всех
// задачах» (list.js), и по той же причине — записей базы знаний десятки, а не
// тысячи, и мигание списка на каждую букву дороже лишнего килобайта в памяти.
//
// Ищем по названию, синонимам и телу сразу. Это не полнотекстовый поиск с
// леммами (тот живёт в /api/search и знает про падежи) — здесь просто фильтр
// списка, который человек видит перед собой.

const $ = (s, r = document) => r.querySelector(s);

let записи = [];
let поиск = '';

async function get(url) {
  const r = await fetch(url);
  return r.json();
}

function словоСиноним(n) {
  const n10 = n % 10;
  const n100 = n % 100;
  if (n10 === 1 && n100 !== 11) return 'синоним';
  if (n10 >= 2 && n10 <= 4 && (n100 < 12 || n100 > 14)) return 'синонима';
  return 'синонимов';
}

function строка(note) {
  const li = document.createElement('li');
  li.className = 'row kb-row';
  li.tabIndex = 0;
  li.setAttribute('role', 'link');

  const main = document.createElement('div');
  main.className = 'row-main';

  const title = document.createElement('span');
  title.className = 'row-title';
  title.textContent = note.title;
  main.append(title);

  const sub = document.createElement('div');
  sub.className = 'row-sub';

  const aliases = note.aliases || [];
  if (aliases.length > 0) {
    const s = document.createElement('span');
    s.className = 'kb-aliases';
    s.textContent = `${aliases.length} ${словоСиноним(aliases.length)}`;
    sub.append(s);
  }

  const тело = (note.body || '').replace(/\s+/g, ' ').trim();
  if (тело) {
    const s = document.createElement('span');
    s.className = 'kb-preview';
    s.textContent = тело.length > 120 ? тело.slice(0, 120) + '…' : тело;
    sub.append(s);
  }

  if (sub.children.length > 0) main.append(sub);
  li.append(main);

  const открыть = () => { location.href = '/база/запись?id=' + encodeURIComponent(note.id); };
  li.addEventListener('click', открыть);
  li.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); открыть(); }
  });

  return li;
}

function отрисовать() {
  const q = поиск.trim().toLowerCase();
  const отфильтровано = записи.filter((n) => {
    if (!q) return true;
    const где = [n.title, ...(n.aliases || []), n.body || ''].join(' ').toLowerCase();
    return где.includes(q);
  });

  $('#notes-list').replaceChildren(...отфильтровано.map(строка));

  const list = $('#notes-list');
  const empty = $('#empty');
  if (отфильтровано.length === 0) {
    list.hidden = true;
    empty.hidden = false;
    $('#empty-text').textContent = записи.length === 0 ? 'Записей пока нет.' : 'Ничего не найдено.';
  } else {
    list.hidden = false;
    empty.hidden = true;
  }
}

$('#search').addEventListener('input', (e) => {
  поиск = e.target.value;
  отрисовать();
});

(async function старт() {
  try {
    const d = await get('/api/kb/notes');
    записи = d.notes || [];
    отрисовать();
  } catch (e) {
    $('#load-err').textContent = 'Не удалось загрузить базу знаний.';
    $('#load-err').hidden = false;
  }
})();
