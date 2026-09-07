'use strict';

const { $, $$, get, post, toast, shortDate, dateField } = Yd;

// Форма ввода задачи. Даты разбирает сервер, а не браузер: правила должны жить в
// одном месте, иначе форма примет то, что движок потом не поймёт.

const steps = $('#steps');
const tpl = $('#step-tpl');

// --- шаги ------------------------------------------------------------------
//
// Шаг — либо лист (название и дата контроля), либо группа подшагов: даты у
// подшагов, у группы только название и режим (в любом порядке / по очереди).
// Одна глубина: подшаг на свои подшаги не разбивается. Считает всё движок —
// форма собирает JSON той же формы, что принимает cmd_create.

function isGroup(li) {
  const block = $('.substeps-block', li);
  return !!block && !block.hidden;
}

function renumber() {
  [...steps.children].forEach((li, i) => {
    $('.step-num', li).textContent = i + 1;
    // Единственный шаг убрать нельзя: задача без шагов не имеет смысла, и лучше
    // спрятать кнопку, чем показать ошибку после нажатия.
    $('.drop', li).hidden = steps.children.length < 2;
    const sub = $('.substeps', li);
    if (sub) [...sub.children].forEach((sli, k) => {
      $('.step-num', sli).textContent = `${i + 1}.${k + 1}`;
    });
  });
}

function wireDate(li) {
  li._dateField = dateField({
    text: $('.step-date', li),
    preview: $('.date-preview', li),
    error: $('.step-err-date', li),
    onChange: async () => { await проверитьПорядок(); },
  });
}

function addSubstep(groupLi, after = null, focus = true) {
  const sub = $('.substeps', groupLi);
  const li = tpl.content.firstElementChild.cloneNode(true);
  // Подшаг — всегда лист, элементы группы из шаблона ему не нужны.
  $('.substeps-block', li).remove();
  $('.make-group', li).remove();
  $('.step-err-group', li).remove();
  const title = $('.step-title', li);
  const dateInput = $('.step-date', li);

  title.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      dateInput.focus();
    }
  });
  dateInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      const next = li.nextElementSibling;
      if (next) $('.step-title', next).focus();
      else addSubstep(groupLi, li);
    }
  });
  wireDate(li);

  $('.drop', li).addEventListener('click', () => {
    const focusAfter = li.nextElementSibling || li.previousElementSibling;
    li.remove();
    // Убрали последний подшаг — шаг снова обычный, с собственной датой.
    if (!sub.children.length) ungroup(groupLi);
    renumber();
    if (focusAfter) $('.step-title', focusAfter).focus();
  });

  if (after) after.after(li); else sub.append(li);
  renumber();
  if (focus) title.focus();
  return li;
}

function makeGroup(li) {
  $('.substeps-block', li).hidden = false;
  $('.make-group', li).hidden = true;
  // Дата группы не имеет смысла — контроль у подшагов, у каждого свой.
  $('.date-row', li).hidden = true;
  $('.step-date', li).value = '';
  $('.date-preview', li).textContent = '';
  $('.step-err-date', li).hidden = true;
  addSubstep(li);
}

function ungroup(li) {
  $('.substeps-block', li).hidden = true;
  $('.make-group', li).hidden = false;
  $('.date-row', li).hidden = false;
}

function addStep(after = null, focus = true) {
  const li = tpl.content.firstElementChild.cloneNode(true);
  const title = $('.step-title', li);
  const dateInput = $('.step-date', li);

  // Enter ведёт в дату этого же шага, а не на следующий шаг. Дата контроля —
  // суть трекера, и проскакивать её основной клавишей нельзя: иначе цепочка
  // шагов заводится без дат и система молчит вместо того, чтобы спрашивать.
  // У группы своей даты нет — Enter идёт в первый подшаг.
  title.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      if (isGroup(li)) {
        const first = $('.substeps', li).firstElementChild;
        if (first) $('.step-title', first).focus();
      } else {
        dateInput.focus();
      }
    }
  });

  dateInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      const next = li.nextElementSibling;
      if (next) $('.step-title', next).focus();
      else addStep(li);
    }
  });

  wireDate(li);

  $('.make-group', li).addEventListener('click', () => makeGroup(li));
  $('.substeps-mode', li).addEventListener('change', () => проверитьПорядок());
  $('.add-substep', li).addEventListener('click', () => addSubstep(li));

  $('.drop', li).addEventListener('click', () => {
    if (steps.children.length < 2) return;
    const focusAfter = li.nextElementSibling || li.previousElementSibling;
    li.remove();
    renumber();
    if (focusAfter) $('.step-title', focusAfter).focus();
  });

  if (after) after.after(li); else steps.append(li);
  renumber();
  if (focus) title.focus();
  return li;
}

// Порядок дат — сразу по мере ввода, а не только при нажатии «Создать»: даты
// разбирает и проверяет сервер, страница только подсвечивает, что он вернул.
// То же правило, что при сохранении, отдельным лёгким запросом без записи.
// Вызывается уже из debounce в preview() — свой таймер здесь не нужен, он
// только добавил бы задержку поверх уже отложенного вызова.
async function проверитьПорядок() {
  const r = await post('/api/steps-check', { steps: собратьШаги(true) });
  for (const e of r.errors || []) {
    const li = поПутиШага(e.field, 'control_date');
    if (!li) continue;
    // Порядок дат важнее, чем «вот как я понял дату»: перекрывает обычное
    // превью, которое showDate() уже успел поставить строчкой выше.
    const field = li._dateField;
    if (field) field.setPreview(e.error, { past: true });
    else {
      const out = $('.date-preview', li);
      out.textContent = e.error;
      out.classList.add('past');
    }
  }
}

// Путь ошибки движка → элемент шага. Понимает и лист («steps.1.control_date»),
// и подшаг («steps.1.steps.0.control_date»).
function поПутиШага(field, суффикс) {
  const m = new RegExp(`^steps\\.(\\d+)(?:\\.steps\\.(\\d+))?\\.${суффикс}$`).exec(field || '');
  if (!m) return null;
  let li = steps.children[+m[1]];
  if (li && m[2] !== undefined) li = $('.substeps', li)?.children[+m[2]];
  return li || null;
}

function собратьШаги(заглушки = false) {
  const поле = (li, cls) => $(cls, li).value;
  return [...steps.children].map((li) => {
    const title = заглушки ? (поле(li, '.step-title') || '·') : поле(li, '.step-title');
    if (isGroup(li)) {
      return {
        title,
        mode: $('.substeps-mode', li).value,
        steps: [...$('.substeps', li).children].map((sli) => ({
          title: заглушки ? (поле(sli, '.step-title') || '·') : поле(sli, '.step-title'),
          control_date: поле(sli, '.step-date'),
        })),
      };
    }
    return { title, control_date: поле(li, '.step-date') };
  });
}

// --- база знаний: автораспознавание (R17, разделы 5.7/5.8/7 ТЗ) ------------
//
// Гипотезы, смещения и морфологию считает движок — страница только рисует
// список и передаёт назад то же самое, что получила. До «Да»/«Нет» человека
// в базу ничего не пишется: «Да» только запоминает гипотезу локально, а
// зовёт /api/kb/confirm лишь submit(), когда у гипотезы появится source_id
// только что созданной задачи. «Нет» шлёт /api/kb/reject сразу — отклик
// нужен раньше, чем задача вообще заведена.

const kbBox = $('#kb-box');
const kbList = $('#kb-list');
let отмеченные = new Map(); // ключ гипотезы -> сама гипотеза, кто получил «Да»

function ключГипотезы(m) {
  return `${m.entry_id}\u0000${m.offset_start}\u0000${m.offset_end}`;
}

function отрисоватьГипотезы(hypotheses) {
  kbList.replaceChildren();
  kbBox.hidden = hypotheses.length === 0;
  for (const m of hypotheses) {
    const key = ключГипотезы(m);
    const li = document.createElement('li');
    li.className = 'kb-item';

    const текст = document.createElement('span');
    текст.className = 'kb-text';
    текст.innerHTML = `Похоже на «<span class="kb-title"></span>» — <span class="kb-matched"></span>`;
    $('.kb-title', текст).textContent = m.title;
    $('.kb-matched', текст).textContent = m.matched;

    const yes = document.createElement('button');
    yes.type = 'button';
    yes.className = 'kb-yes' + (отмеченные.has(key) ? ' is-active' : '');
    yes.textContent = 'Да';
    yes.setAttribute('aria-pressed', отмеченные.has(key) ? 'true' : 'false');
    yes.addEventListener('click', () => {
      отмеченные.set(key, m);
      yes.classList.add('is-active');
      yes.setAttribute('aria-pressed', 'true');
    });

    const no = document.createElement('button');
    no.type = 'button';
    no.className = 'kb-no';
    no.textContent = 'Нет';
    no.addEventListener('click', async () => {
      отмеченные.delete(key);
      li.remove();
      kbBox.hidden = kbList.children.length === 0;
      try { await post('/api/kb/reject', { mention: m, mute: false }); } catch { /* переживём */ }
    });

    li.append(текст, yes, no);
    kbList.append(li);
  }
}

let kbTimer;
async function сканироватьБазу() {
  const text = $('#body').value.trim();
  if (!text) {
    kbBox.hidden = true;
    kbList.replaceChildren();
    return;
  }
  let r;
  try {
    r = await post('/api/kb/scan', { text });
  } catch {
    return; // сервер недоступен — гипотезы просто не покажем
  }
  const hypotheses = r.hypotheses || [];
  const живые = new Set(hypotheses.map(ключГипотезы));
  for (const key of [...отмеченные.keys()]) {
    if (!живые.has(key)) отмеченные.delete(key); // текст сдвинулся — метка устарела
  }
  отрисоватьГипотезы(hypotheses);
}

function сброситьГипотезы() {
  отмеченные = new Map();
  kbBox.hidden = true;
  kbList.replaceChildren();
}

$('#body').addEventListener('input', () => {
  clearTimeout(kbTimer);
  kbTimer = setTimeout(сканироватьБазу, 300);
});

// --- отправка --------------------------------------------------------------


function collect() {
  return {
    title: $('#title').value,
    tags: $('#tags').value.split(',').map((s) => s.trim()).filter(Boolean),
    body: $('#body').value,
    steps: собратьШаги(),
  };
}

function clearErrors() {
  $('#err-title').hidden = true;
  $('#err-steps').hidden = true;
  $('#title').classList.remove('invalid');
  [...steps.querySelectorAll('li.step')].forEach((li) => {
    $('.step-title', li).classList.remove('invalid');
    $('.step-date', li).classList.remove('invalid');
    $('.step-err-title', li).hidden = true;
    $('.step-err-date', li).hidden = true;
    const group = $('.step-err-group', li);
    if (group) group.hidden = true;
  });
}

function showErrors(errors) {
  let first = null;
  for (const { field, error } of errors) {
    if (field === 'title') {
      $('#title').classList.add('invalid');
      $('#err-title').textContent = error;
      $('#err-title').hidden = false;
      first = first || $('#title');
      continue;
    }
    if (field === 'steps') {
      $('#err-steps').textContent = error;
      $('#err-steps').hidden = false;
      continue;
    }
    const заголовок = поПутиШага(field, 'title');
    const дата = поПутиШага(field, 'control_date');
    if (заголовок || дата) {
      const li = заголовок || дата;
      const input = $(заголовок ? '.step-title' : '.step-date', li);
      const box = $(заголовок ? '.step-err-title' : '.step-err-date', li);
      input.classList.add('invalid');
      box.textContent = error;
      box.hidden = false;
      first = first || input;
      continue;
    }
    // Ошибки самой группы (пустая, кривой режим) — под её блоком подшагов.
    const группа = поПутиШага(field, '(?:steps|mode|start_date)');
    if (группа) {
      const box = $('.step-err-group', группа);
      if (box) {
        box.textContent = error;
        box.hidden = false;
        continue;
      }
    }
    $('#err-title').textContent = error;
    $('#err-title').hidden = false;
  }
  if (first) first.focus();
}

async function submit() {
  clearErrors();
  const save = $('#save');
  save.disabled = true;
  try {
    const r = await post('/api/create', collect());
    if (!r.ok) return showErrors(r.errors || [{ field: null, error: 'не сохранилось' }]);

    let сообщение = `Создана: ${r.task} · шагов ${r.steps} · ${r.status}`;
    // Ссылки проставляются только сейчас: до этой строчки задачи ещё не было,
    // и /api/kb/confirm писать было некуда — раздел 7.1 запрещает это раньше.
    if (отмеченные.size > 0) {
      try {
        const k = await post('/api/kb/confirm', {
          source_type: 'task', source_id: r.task, mentions: [...отмеченные.values()],
        });
        if (k.ok) сообщение += ` · проставлено ссылок: ${k.links.length}`;
      } catch { /* задача уже создана, ссылки — не повод показывать ошибку формы */ }
    }

    const saved = $('#saved');
    saved.textContent = сообщение;
    saved.hidden = false;
    setTimeout(() => { saved.hidden = true; }, 6000);

    $('#form').reset();
    steps.replaceChildren();
    addStep();
    сброситьГипотезы();
    $('#title').focus();
  } catch (e) {
    showErrors([{ field: null, error: 'сервер не ответил: ' + e.message }]);
  } finally {
    save.disabled = false;
  }
}

// --- запуск ----------------------------------------------------------------

$('#add-step').addEventListener('click', () => addStep());

$('#form').addEventListener('submit', (e) => {
  e.preventDefault();
  submit();
});

// Ctrl+Enter из любого поля — сохранить, не таскаясь мышкой до кнопки.
document.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    submit();
  }
});

$('#title').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    $('#tags').focus();
  }
});

$('#tags').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    const first = steps.firstElementChild;
    if (first) $('.step-title', first).focus();
  }
});

addStep(null, false);
