'use strict';

// Карточка задачи — раздел 6.3 ТЗ. Страница не считает ничего сама: статус,
// просрочку, дефолт даты начала и порядок шагов пересчитывает ядро при каждом
// сохранении. Здесь только сбор того, что человек поменял, и показ ответа.

const $ = (s, r = document) => r.querySelector(s);
const dlg = $('#control');
const tpl = $('#step-tpl');
const checklist = $('#checklist');

const имя = new URLSearchParams(location.search).get('name') || '';
let шаги = [];          // рабочая копия шагов — сюда же попадают ещё не сохранённые
let причины = [];
let текущийШаг = null;  // для окна контроля
let режим = null;
let перетаскиваемый = null;

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

function всплывашка(текст) {
  const el = document.createElement('div');
  el.className = 'toast';
  el.textContent = текст;
  document.body.append(el);
  setTimeout(() => el.remove(), 3500);
}

// --- вложения ----------------------------------------------------------------
//
// Файл целиком уходит на сервер base64-строкой в теле того же JSON-запроса,
// что и остальные POST формы — без multipart и без `cgi` (модуль убран из
// стандартной библиотеки в новых версиях Python). Хранит и раздаёт байты
// движок (`attachments.py`), страница только собирает файл и рисует список.

function размерФайла(bytes) {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}

function файлВBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    // readAsDataURL отдаёт «data:тип;base64,XXXX» — серверу нужна только
    // часть после запятой, префикс он не разбирает и не ждёт.
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '');
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function строкаВложения(a, onDelete) {
  const li = document.createElement('li');
  li.className = 'attach-row';

  const ссылка = document.createElement('a');
  ссылка.href = `/вложение/${a.id}`;
  ссылка.target = '_blank';
  ссылка.rel = 'noopener';
  ссылка.textContent = a.filename;
  if (a.caption) ссылка.title = a.caption;

  // Картинку видно сразу, а не по клику: вложение чаще всего схема, и ходить
  // за ней в соседнюю вкладку значит не смотреть на неё вовсе. Тип берём тот,
  // что вернуло ядро; всё, что не картинка, остаётся строкой со ссылкой.
  if ((a.mime || '').startsWith('image/')) {
    const превью = document.createElement('img');
    превью.className = 'attach-thumb';
    превью.src = `/вложение/${a.id}`;
    превью.alt = a.caption || a.filename;
    превью.loading = 'lazy';
    // Битый или потерянный на диске файл не должен оставлять сломанную
    // иконку: строка со ссылкой сама по себе рабочая.
    превью.addEventListener('error', () => превью.remove());
    li.classList.add('has-thumb');
    li.append(превью);
  }

  const размер = document.createElement('span');
  размер.className = 'attach-size';
  размер.textContent = размерФайла(a.bytes);

  const убрать = document.createElement('button');
  убрать.type = 'button';
  убрать.className = 'attach-remove';
  убрать.title = 'Удалить вложение';
  убрать.textContent = '×';
  убрать.addEventListener('click', async () => {
    const r = await post('/api/attachments-delete', { id: a.id });
    if (r.ok) onDelete();
    else всплывашка((r.errors || [{}])[0].error || 'не получилось');
  });

  li.append(ссылка, размер, убрать);
  return li;
}

async function загрузитьВложения(listEl, owner) {
  const qs = new URLSearchParams({ task: owner.task });
  if (owner.step != null) qs.set('step', owner.step);
  const d = await get('/api/attachments?' + qs.toString());
  listEl.replaceChildren(...(d.attachments || [])
    .map((a) => строкаВложения(a, () => загрузитьВложения(listEl, owner))));
}

async function прикрепитьФайл(file, owner, listEl, errEl) {
  if (errEl) errEl.hidden = true;
  let data;
  try {
    data = await файлВBase64(file);
  } catch {
    всплывашка('Не удалось прочитать файл');
    return;
  }
  const payload = { task: owner.task, filename: file.name, data };
  if (owner.step != null) payload.step = owner.step;
  const r = await post('/api/attachments-add', payload);
  if (!r.ok) {
    const текст = (r.errors || [{}])[0].error || 'не получилось';
    if (errEl) { errEl.textContent = текст; errEl.hidden = false; }
    else всплывашка(текст);
    return;
  }
  await загрузитьВложения(listEl, owner);
}

function короткаяДата(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const есть_время = iso.length > 10;
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric' }) +
    (есть_время ? ', ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : '');
}

// --- загрузка и отрисовка ---------------------------------------------------

async function загрузить() {
  const d = await get('/api/task?name=' + encodeURIComponent(имя));
  if (d.error) {
    $('#load-err').textContent = 'Задача не найдена: ' + d.error;
    $('#load-err').hidden = false;
    return;
  }
  $('#card').hidden = false;
  отрисовать(d);
}

function отрисовать(d) {
  const meta = d.meta;
  $('#title').value = meta.title;

  // d.status — внутреннее английское значение (overdue/waiting/done/...), по нему
  // удобно матчить классы. Текст на плашке — meta.status, его уже перевёл движок.
  const pill = $('#status-pill');
  pill.textContent = meta.status;
  pill.className = 'status-pill' +
    (d.status === 'overdue' ? ' is-overdue' :
     d.status === 'done' ? ' is-done' :
     d.status === 'cancelled' ? ' is-cancelled' : '');

  $('#due-line').textContent = meta.control_date
    ? 'срок: ' + короткаяДата(meta.control_date) : 'срок не вычислен';

  const [сделано, всего] = (meta.progress || '0/0').split('/').map(Number);
  $('#progress-fill').style.width = всего ? `${100 * сделано / всего}%` : '0%';
  $('#progress-text').textContent = `${сделано} из ${всего}`;

  $('#start-date').value = meta.start_date || '';
  $('#tags').value = (meta.tags || []).join(', ');
  $('#body').value = d.body || '';

  const отменена = !!meta.cancelled;
  $('#cancel-task').hidden = отменена;
  $('#cancel-task').textContent = отменена ? 'Уже отменена' : 'Отменить задачу';
  $('#cancel-task').disabled = отменена;

  шаги = d.steps.map((s) => ({ ...s }));
  перерисоватьЧеклист();
  отрисоватьИсторию(d.steps);
  загрузитьВложения($('#task-attachments'), { task: имя });
}

function перерисоватьЧеклист() {
  checklist.replaceChildren(...шаги.map((s, i) => строкаШага(s, i)));
}

// Плоский массив шагов хранится в порядке обхода дерева: дети группы идут
// подряд сразу за ней. Все перестройки ниже обязаны сохранять этот порядок —
// на нём держится и отрисовка, и соответствие путей ошибок движка строкам.

function этоГруппа(s) { return !!s.mode; }

function длинаБлока(индекс) {
  const s = шаги[индекс];
  let len = 1;
  if (этоГруппа(s)) {
    let j = индекс + 1;
    while (j < шаги.length && шаги[j].parent === s.id) { j++; len++; }
  }
  return len;
}

// Данные для движка — вложенная форма, та же, что у cmd_create/cmd_update:
// у группы steps и mode, у листа даты. Даты группы не отправляются вообще.
function вложенныеШаги() {
  const дети = new Map();
  for (const s of шаги) {
    const p = s.parent ?? null;
    if (!дети.has(p)) дети.set(p, []);
    дети.get(p).push(s);
  }
  const узел = (s) => этоГруппа(s)
    ? { id: s.id, title: s.title, mode: s.mode, note: s.note,
        steps: (дети.get(s.id) || []).map(узел) }
    : { id: s.id, title: s.title, start_date: s.start_date,
        control_date: s.control_date, note: s.note };
  return (дети.get(null) || []).map(узел);
}

// Путь ошибки движка («steps.1.steps.0.control_date») → индекс в плоском
// массиве. Дети идут подряд за группой, так что арифметика прямая.
function индексПоПути(field, суффикс) {
  const m = new RegExp(`^steps\\.(\\d+)(?:\\.steps\\.(\\d+))?\\.(?:${суффикс})$`).exec(field || '');
  if (!m) return null;
  const верхние = [...шаги.keys()].filter((i) => (шаги[i].parent ?? null) === null);
  let i = верхние[+m[1]];
  if (i === undefined) return null;
  if (m[2] !== undefined) i = i + 1 + (+m[2]);
  return шаги[i] ? i : null;
}

function строкаШага(s, индекс) {
  const li = tpl.content.firstElementChild.cloneNode(true);
  const группа = этоГруппа(s);
  // Закрытие группы вычисляет ядро (поле closed из cmd_show): у неё нет
  // своего статуса, и страница не должна выводить его из детей сама.
  const закрыт = группа ? !!s.closed : ['done', 'skipped', 'failed'].includes(s.status);
  li.classList.toggle('is-closed', закрыт);
  li.classList.toggle('is-overdue', s.state === 'overdue');
  li.classList.toggle('is-group', группа);
  li.classList.toggle('is-substep', (s.parent ?? null) !== null);
  li.dataset.index = индекс;

  const row = $('.checkline-row', li);
  row.draggable = true;

  const check = $('.step-check', li);
  if (группа) {
    // У группы нет чекбокса: отметить можно только её подшаги, закрытие
    // приходит вычисленным. Кликабельный чекбокс здесь был бы вторым
    // писателем одного поля.
    check.hidden = true;
  } else {
    check.checked = s.status === 'done';
    check.disabled = s.status === 'failed' || s.status === 'skipped';
    check.addEventListener('click', (e) => {
      e.stopPropagation();
      if (s.status === 'done') { check.checked = true; return переоткрыть(s); }
      действие('done', s);
    });
  }

  $('.checkline-title', li).textContent = s.title;
  $('.checkline-date', li).textContent = группа
    ? (s.mode === 'seq' ? 'подшаги по очереди' : 'подшаги в любом порядке')
    : (s.control_date ? короткаяДата(s.control_date) : '—');

  const счётчик = $('.checkline-postponed', li);
  // Считает движок (`stall_count`), карточка показывает. Раньше считала сама и
  // расходилась с лентой: обычный `defer` она брала в счёт, а движок нет.
  const переносов = s.stalled || 0;
  if (переносов > 0) { счётчик.textContent = `переносов: ${переносов}`; }
  else счётчик.remove();

  $('.checkline-note-icon', li).hidden = !s.note;

  const more = $('.checkline-more', li);
  if (группа) {
    more.hidden = true;
  } else if (s.status === 'done') {
    more.textContent = '↺';
    more.title = 'Переоткрыть';
    more.addEventListener('click', (e) => { e.stopPropagation(); переоткрыть(s); });
  } else if (закрыт) {
    more.hidden = true;
  } else {
    more.addEventListener('click', (e) => { e.stopPropagation(); окно(s); });
  }

  row.addEventListener('click', () => переключитьРедактор(li, s, закрыт));
  заполнитьРедактор(li, s, закрыт);
  настроитьПеретаскивание(li);
  return li;
}

function переключитьРедактор(li, s, закрыт) {
  const ed = $('.checkline-editor', li);
  ed.hidden = !ed.hidden;
}

function заполнитьРедактор(li, s, закрыт) {
  const группа = этоГруппа(s);
  $('.edit-title', li).value = s.title;
  $('.edit-start', li).value = s.start_date || '';
  $('.edit-control', li).value = s.control_date || '';
  $('.edit-note', li).value = s.note || '';

  // У группы вместо дат — режим и добавление подшагов; разбить на подшаги
  // можно только верхний шаг: одна глубина вложенности.
  $('.edit-dates', li).hidden = группа;
  $('.edit-mode-field', li).hidden = !группа;
  $('.edit-mode', li).value = s.mode || 'par';
  $('.add-substep', li).hidden = !группа;
  $('.make-group', li).hidden = группа || (s.parent ?? null) !== null;

  $('.edit-mode', li).addEventListener('change', () => {
    s.mode = $('.edit-mode', li).value;
    перерисоватьЧеклист();
    проверитьПорядок();
  });

  $('.add-substep', li).addEventListener('click', () => {
    const индекс = Number(li.dataset.index);
    const конец = индекс + длинаБлока(индекс);
    шаги.splice(конец, 0, { id: null, title: '', status: 'pending',
      start_date: null, control_date: null, note: null,
      parent: s.id, mode: null, log: [] });
    перерисоватьЧеклист();
    const строка = checklist.children[конец];
    $('.checkline-editor', строка).hidden = false;
    $('.edit-title', строка).focus();
  });

  $('.make-group', li).addEventListener('click', () => {
    // Новый шаг без id разбивать нельзя: подшагу нужен parent, а id раздаёт
    // движок при сохранении. Сначала сохранить, потом разбивать.
    if (s.id == null) { всплывашка('Сначала сохрани задачу, потом разбивай шаг'); return; }
    const индекс = Number(li.dataset.index);
    s.mode = 'par';
    s.start_date = null;
    s.control_date = null;
    шаги.splice(индекс + 1, 0, { id: null, title: '', status: 'pending',
      start_date: null, control_date: null, note: null,
      parent: s.id, mode: null, log: [] });
    перерисоватьЧеклист();
    const строка = checklist.children[индекс + 1];
    $('.checkline-editor', строка).hidden = false;
    $('.edit-title', строка).focus();
  });

  // Вложения адресуются id шага — до первого сохранения его ещё нет
  // (`s.id == null` у только что добавленного шага), прикреплять некуда.
  const attachField = $('.edit-attach-field', li);
  if (s.id == null) {
    attachField.hidden = true;
  } else {
    attachField.hidden = false;
    const attachList = $('.edit-attachments', li);
    загрузитьВложения(attachList, { task: имя, step: s.id });
    $('.edit-attach-input', li).addEventListener('change', async (e) => {
      const file = e.target.files[0];
      e.target.value = '';
      if (!file) return;
      await прикрепитьФайл(file, { task: имя, step: s.id }, attachList, null);
    });
  }

  const ro = $('.edit-readonly', li);
  if (закрыт) {
    ro.hidden = false;
    ro.textContent = `Шаг уже ${s.status === 'done' ? 'сделан' : s.status === 'failed' ? 'провален' : 'снят'} — правка полей не меняет эту отметку.`;
  } else {
    ro.hidden = true;
  }

  const сохранить = () => {
    const индекс = Number(li.dataset.index);
    шаги[индекс] = {
      ...шаги[индекс],
      title: $('.edit-title', li).value,
      start_date: $('.edit-start', li).value.trim() || null,
      control_date: $('.edit-control', li).value.trim() || null,
      note: $('.edit-note', li).value.trim() || null,
    };
    $('.checkline-title', li).textContent = шаги[индекс].title;
    $('.checkline-date', li).textContent =
      шаги[индекс].control_date ? короткаяДата(шаги[индекс].control_date) : '—';
    $('.checkline-note-icon', li).hidden = !шаги[индекс].note;
  };
  $('.edit-title', li).addEventListener('input', сохранить);
  $('.edit-note', li).addEventListener('input', сохранить);

  предпросмотрДаты($('.edit-start', li), $('.edit-start-preview', li), сохранить);
  предпросмотрДаты($('.edit-control', li), $('.edit-control-preview', li), сохранить);
}

let таймерыПредпросмотра = new WeakMap();
function предпросмотрДаты(input, out, доп) {
  input.addEventListener('input', () => {
    clearTimeout(таймерыПредпросмотра.get(input));
    таймерыПредпросмотра.set(input, setTimeout(async () => {
      const текст = input.value.trim();
      доп();
      if (!текст) { out.textContent = ''; }
      else {
        const r = await post('/api/parse-date', { text: текст });
        out.textContent = r.ok ? (r.label || '') : 'не понял дату';
      }
      // Порядок дат проверяем при любой правке — не только при сохранении.
      // Значение уже разобрано и лежит в шагах через доп(), можно спрашивать сразу.
      проверитьПорядок();
    }, 220));
  });
}

// Порядок дат — сразу по мере ввода, а не только на «Сохранить»: контроль
// раньше даты начала виден в тот же момент, когда его напечатали, а не после
// круга «сохранил → откатили → чини». Правило то же, что и на сервере при
// записи — сервер и здесь единственный, кто его вычисляет, страница только
// подсвечивает то, что он вернул.
// Вызывается уже из debounce в предпросмотрДаты() — свой таймер здесь не
// нужен, он только добавил бы задержку поверх уже отложенного вызова.
async function проверитьПорядок() {
  const без_названий = (узлы) => узлы.map((u) => ({ ...u, title: u.title || '·',
    ...(u.steps ? { steps: без_названий(u.steps) } : {}) }));
  const payload = {
    task: имя,
    start_date: $('#start-date').value.trim(),
    steps: без_названий(вложенныеШаги()),
  };
  const r = await post('/api/steps-check', payload);
  checklist.querySelectorAll('.edit-control').forEach((el) => el.classList.remove('invalid'));
  checklist.querySelectorAll('.edit-control-preview').forEach((el) => el.classList.remove('past'));
  for (const e of r.errors || []) {
    const i = индексПоПути(e.field, 'control_date');
    if (i === null) continue;
    const li = checklist.children[i];
    if (!li) continue;
    $('.edit-control', li).classList.add('invalid');
    const preview = $('.edit-control-preview', li);
    preview.textContent = e.error;
    preview.classList.add('past');
  }
}

// --- перетаскивание ---------------------------------------------------------

function настроитьПеретаскивание(li) {
  const row = $('.checkline-row', li);
  row.addEventListener('dragstart', (e) => {
    перетаскиваемый = Number(li.dataset.index);
    li.classList.add('is-dragging');
    e.dataTransfer.effectAllowed = 'move';
  });
  row.addEventListener('dragend', () => {
    li.classList.remove('is-dragging');
    checklist.querySelectorAll('.checkline').forEach((x) => x.classList.remove('is-drop-target'));
  });
  li.addEventListener('dragover', (e) => {
    e.preventDefault();
    if (перетаскиваемый === null) return;
    li.classList.add('is-drop-target');
  });
  li.addEventListener('dragleave', () => li.classList.remove('is-drop-target'));
  li.addEventListener('drop', (e) => {
    e.preventDefault();
    li.classList.remove('is-drop-target');
    const цель = Number(li.dataset.index);
    if (перетаскиваемый === null || цель === перетаскиваемый) return;
    // Перенос только среди соседей одного уровня: подшаг не выдёргивается из
    // группы перетаскиванием, группа переезжает целиком вместе с детьми.
    if ((шаги[перетаскиваемый].parent ?? null) !== (шаги[цель].parent ?? null)) {
      перетаскиваемый = null;
      return;
    }
    const длина = длинаБлока(перетаскиваемый);
    const начало = перетаскиваемый;
    const взятые = шаги.splice(начало, длина);
    let место;
    if (цель > начало) {
      const цельПосле = цель - длина;
      место = цельПосле + длинаБлока(цельПосле);
    } else {
      место = цель;
    }
    шаги.splice(место, 0, ...взятые);
    перетаскиваемый = null;
    перерисоватьЧеклист();
    проверитьПорядок();  // порядок сменился без единой правки поля — проверить сразу
  });
}

// --- история -----------------------------------------------------------------

const СОБЫТИЕ_RU = { done: 'сделан', not_done: 'не сделан', defer: 'перенесён',
  failed: 'провален', skipped: 'снят', reopened: 'переоткрыт' };

function отрисоватьИсторию(steps) {
  const события = [];
  for (const s of steps) {
    for (const e of s.log || []) {
      события.push({ ...e, шаг: s.title });
    }
  }
  события.sort((a, b) => (a.date || '').localeCompare(b.date || ''));
  $('#history-count').textContent = события.length ? `(${события.length})` : '';
  $('#history-list').replaceChildren(...события.reverse().map((e) => {
    const li = document.createElement('li');
    const when = document.createElement('span');
    when.className = 'h-when';
    when.textContent = e.date || '';
    const текст = document.createElement('span');
    const step = document.createElement('span');
    step.className = 'h-step';
    step.textContent = e.шаг;
    текст.append(step, document.createTextNode(' — ' + (СОБЫТИЕ_RU[e.event] || e.event)
      + (e.reason ? `: ${e.reason}` : '')));
    li.append(when, текст);
    return li;
  }));
}

$('#history-toggle').addEventListener('click', () => {
  $('#history-list').hidden = !$('#history-list').hidden;
});

// --- добавление и сохранение --------------------------------------------------

$('#add-step').addEventListener('click', () => {
  шаги.push({ id: null, title: '', status: 'pending', start_date: null,
             control_date: null, note: null, parent: null, mode: null, log: [] });
  перерисоватьЧеклист();
  const последняя = checklist.lastElementChild;
  $('.checkline-editor', последняя).hidden = false;
  $('.edit-title', последняя).focus();
});

$('#task-attach-input').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  await прикрепитьФайл(file, { task: имя }, $('#task-attachments'), $('#err-attach'));
});

// Скриншот схемы попадает в задачу вставкой, без похода через «сохранить файл
// на диск и выбрать его в диалоге». Всегда к задаче целиком, даже если открыт
// редактор шага: правило должно быть одно и предсказуемое, иначе картинка
// уедет не туда в зависимости от того, где стоял курсор. Текстовая вставка
// сюда не попадает — в буфере нет файлов, выходим сразу.
document.addEventListener('paste', async (e) => {
  const files = [...(e.clipboardData?.files || [])];
  if (!files.length) return;
  e.preventDefault();
  for (const file of files) {
    // У картинки из буфера имени нет — браузер отдаёт «image.png». Ставим
    // своё, иначе список превращается в десять одинаковых строк.
    const своё = file.name && file.name !== 'image.png'
      ? file
      : new File([file], `вставка-${new Date().toISOString().slice(0, 19)
          .replace(/[:T]/g, '-')}.png`, { type: file.type });
    await прикрепитьФайл(своё, { task: имя }, $('#task-attachments'), $('#err-attach'));
  }
  всплывашка(files.length === 1 ? 'Картинка прикреплена' : `Прикреплено файлов: ${files.length}`);
});

function очиститьОшибки() {
  for (const id of ['err-start', 'err-steps', 'err-general']) {
    $('#' + id).hidden = true;
  }
  checklist.querySelectorAll('.invalid').forEach((el) => el.classList.remove('invalid'));
}

function показатьОшибки(errors, разрешитьForce) {
  очиститьОшибки();
  let первая = null;
  for (const { field, error } of errors) {
    if (field === 'start_date') {
      $('#err-start').textContent = error; $('#err-start').hidden = false;
      first_or(() => $('#start-date'));
      continue;
    }
    const i = индексПоПути(field, 'title|start_date|control_date');
    if (i !== null) {
      const li = checklist.children[i];
      if (li) {
        $('.checkline-editor', li).hidden = false;
        const поле = field.endsWith('.title') ? '.edit-title'
          : field.endsWith('.start_date') ? '.edit-start' : '.edit-control';
        $(поле, li).classList.add('invalid');
        первая = первая || $(поле, li);
      }
      continue;
    }
    if (field === 'steps') {
      $('#err-steps').textContent = errors.filter((e) => e.field === 'steps')
        .map((e) => e.error).join(' · ');
      $('#err-steps').hidden = false;
      if (разрешитьForce) показатьForce();
      continue;
    }
    $('#err-general').textContent = error;
    $('#err-general').hidden = false;
  }
  if (первая) первая.focus();
  function first_or(f) { первая = первая || f(); }
}

function показатьForce() {
  if ($('#force-save')) return;
  const b = document.createElement('button');
  b.type = 'button';
  b.id = 'force-save';
  b.className = 'ghost small';
  b.textContent = 'Всё равно сохранить, шаг больше не нужен';
  b.addEventListener('click', () => сохранить(true));
  $('#err-steps').after(b);
}

async function сохранить(force = false) {
  очиститьОшибки();
  const старое = $('#force-save');
  if (старое) старое.remove();

  const данные = {
    title: $('#title').value,
    start_date: $('#start-date').value.trim() || null,
    tags: $('#tags').value.split(',').map((s) => s.trim()).filter(Boolean),
    body: $('#body').value,
    steps: вложенныеШаги(),
  };

  const [{ warnings }, r] = await Promise.all([
    post('/api/task-warnings', данные),
    post('/api/task-update', { task: имя, data: данные, force }),
  ]);

  if (!r.ok) return показатьОшибки(r.errors || [], !force);

  const переименовано = r.task !== имя;
  всплывашка(переименовано ? `Сохранено, переименовано в «${r.task}»` : 'Сохранено');
  if (warnings && warnings.length) {
    всплывашка(warnings.map((w) => w.warning).join(' · '));
  }
  if (переименовано) {
    history.replaceState(null, '', '/задача?name=' + encodeURIComponent(r.task));
  }
  загрузить();
}

$('#save-card').addEventListener('click', () => сохранить(false));

// --- отмена, удаление, шаблон -------------------------------------------------

$('#cancel-task').addEventListener('click', async () => {
  if (!confirm('Отменить задачу целиком?')) return;
  const r = await post('/api/task-cancel', { task: имя });
  if (r.ok) { всплывашка('Задача отменена'); загрузить(); }
  else всплывашка((r.errors || [{}])[0].error || 'не получилось');
});

$('#delete-task').addEventListener('click', async () => {
  if (!confirm(`Удалить «${имя}» насовсем? Это нельзя отменить.`)) return;
  const r = await post('/api/task-delete', { task: имя });
  if (r.ok) location.href = '/';
  else всплывашка((r.errors || [{}])[0].error || 'не получилось');
});

$('#save-template').addEventListener('click', async () => {
  const r = await post('/api/template-from-task', { task: имя });
  всплывашка(r.ok ? `Сохранено как шаблон «${r.template}»` : 'Не вышло');
});

// --- переоткрытие -------------------------------------------------------------

async function переоткрыть(s) {
  const r = await post('/api/task-reopen', { task: имя, step: s.id });
  if (r.ok) { всплывашка('Шаг переоткрыт'); загрузить(); }
  else всплывашка((r.errors || [{}])[0].error || 'не получилось');
}

// --- окно контроля (done/notdone/defer/fail) — то же, что на ленте -----------

function окно(s) {
  текущийШаг = s;
  режим = null;
  $('#c-task').textContent = имя;
  $('#c-step').textContent = s.title;
  const note = $('#c-note');
  note.hidden = !s.note;
  note.textContent = s.note || '';
  $('#c-meta').textContent = s.control_date ? 'контроль ' + короткаяДата(s.control_date) : '';
  формаОкна(false);
  dlg.showModal();
}

function формаОкна(показать) {
  $('#c-form').hidden = !показать;
  $('#c-err').hidden = true;
  $('#c-reason').classList.remove('invalid');
  if (показать) {
    $('#c-date').value = '';
    $('#c-date-preview').textContent = '';
    $('#c-presets').querySelectorAll('button').forEach((b) => b.classList.remove('on'));
    $('#c-date-field').hidden = режим === 'fail';
    $('#c-reason').focus();
  }
}

async function действие(op, s, extra = {}) {
  const r = await post('/api/action', { op, task: имя, step: s.id, ...extra });
  if (!r.ok) {
    const текст = (r.errors || []).map((e) => e.error).join('; ') || 'не получилось';
    if ($('#c-form').hidden) return всплывашка(текст);
    $('#c-err').textContent = текст;
    $('#c-err').hidden = false;
    return;
  }
  dlg.close();
  всплывашка('Готово');
  загрузить();
}

for (const b of document.querySelectorAll('[data-op]')) {
  b.addEventListener('click', () => {
    const op = b.dataset.op;
    if (op === 'done') return действие('done', текущийШаг);
    режим = op;
    формаОкна(true);
  });
}

$('#c-back').addEventListener('click', () => формаОкна(false));

let таймерОкна;
$('#c-date').addEventListener('input', () => {
  clearTimeout(таймерОкна);
  таймерОкна = setTimeout(async () => {
    const текст = $('#c-date').value.trim();
    if (!текст) return ($('#c-date-preview').textContent = '');
    const r = await post('/api/parse-date', { text: текст });
    $('#c-date-preview').textContent = r.ok ? (r.label || '') : 'не понял дату';
  }, 220);
});

for (const b of $('#c-presets').querySelectorAll('button')) {
  b.addEventListener('click', () => {
    $('#c-presets').querySelectorAll('button').forEach((x) => x.classList.remove('on'));
    b.classList.add('on');
    $('#c-date').value = b.dataset.when;
    $('#c-date').dispatchEvent(new Event('input'));
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
  действие(режим, текущийШаг, { reason: причина, to: $('#c-date').value.trim() });
});

dlg.addEventListener('close', () => { текущийШаг = null; режим = null; });

// --- старт ---------------------------------------------------------------

предпросмотрДаты($('#start-date'), $('#start-preview'), () => {});

(async function старт() {
  if (!имя) {
    $('#load-err').textContent = 'Не указана задача — открой карточку из ленты.';
    $('#load-err').hidden = false;
    return;
  }
  const r = await get('/api/reasons');
  причины = r.reasons || [];
  $('#c-reason').replaceChildren(new Option('— выбери причину —', ''),
    ...причины.map((p) => new Option(p, p)));
  await загрузить();
})();
