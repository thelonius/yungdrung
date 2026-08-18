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
}

function перерисоватьЧеклист() {
  checklist.replaceChildren(...шаги.map((s, i) => строкаШага(s, i)));
}

function строкаШага(s, индекс) {
  const li = tpl.content.firstElementChild.cloneNode(true);
  const закрыт = ['done', 'skipped', 'failed'].includes(s.status);
  li.classList.toggle('is-closed', закрыт);
  li.classList.toggle('is-overdue', s.state === 'overdue');
  li.dataset.index = индекс;

  const row = $('.checkline-row', li);
  row.draggable = true;

  const check = $('.step-check', li);
  check.checked = s.status === 'done';
  check.disabled = s.status === 'failed' || s.status === 'skipped';
  check.addEventListener('click', (e) => {
    e.stopPropagation();
    if (s.status === 'done') { check.checked = true; return переоткрыть(s); }
    действие('done', s);
  });

  $('.checkline-title', li).textContent = s.title;
  $('.checkline-date', li).textContent = s.control_date ? короткаяДата(s.control_date) : '—';

  const счётчик = $('.checkline-postponed', li);
  const переносов = (s.log || []).filter((e) => e.event === 'not_done' || e.event === 'defer').length;
  if (переносов > 0) { счётчик.textContent = `переносов: ${переносов}`; }
  else счётчик.remove();

  $('.checkline-note-icon', li).hidden = !s.note;

  const more = $('.checkline-more', li);
  if (s.status === 'done') {
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
  $('.edit-title', li).value = s.title;
  $('.edit-start', li).value = s.start_date || '';
  $('.edit-control', li).value = s.control_date || '';
  $('.edit-note', li).value = s.note || '';

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
      if (!текст) { out.textContent = ''; return; }
      const r = await post('/api/parse-date', { text: текст });
      out.textContent = r.ok ? (r.label || '') : 'не понял дату';
    }, 220));
  });
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
    const [взятый] = шаги.splice(перетаскиваемый, 1);
    шаги.splice(цель, 0, взятый);
    перетаскиваемый = null;
    перерисоватьЧеклист();
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
             control_date: null, note: null, log: [] });
  перерисоватьЧеклист();
  const последняя = checklist.lastElementChild;
  $('.checkline-editor', последняя).hidden = false;
  $('.edit-title', последняя).focus();
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
    const m = /^steps\.(\d+)\.(title|start_date|control_date)$/.exec(field || '');
    if (m) {
      const li = checklist.children[+m[1]];
      if (li) {
        $('.checkline-editor', li).hidden = false;
        const поле = { title: '.edit-title', start_date: '.edit-start',
          control_date: '.edit-control' }[m[2]];
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
    steps: шаги.map((s) => ({ id: s.id, title: s.title, start_date: s.start_date,
                              control_date: s.control_date, note: s.note })),
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
