'use strict';

// Форма ввода задачи. Даты разбирает сервер, а не браузер: правила должны жить в
// одном месте, иначе форма примет то, что движок потом не поймёт.

const $ = (sel, root = document) => root.querySelector(sel);
const steps = $('#steps');
const tpl = $('#step-tpl');

// --- шаги ------------------------------------------------------------------

function renumber() {
  [...steps.children].forEach((li, i) => {
    $('.step-num', li).textContent = i + 1;
    // Единственный шаг убрать нельзя: задача без шагов не имеет смысла, и лучше
    // спрятать кнопку, чем показать ошибку после нажатия.
    $('.drop', li).hidden = steps.children.length < 2;
  });
}

function addStep(after = null, focus = true) {
  const li = tpl.content.firstElementChild.cloneNode(true);
  const title = $('.step-title', li);
  const dateInput = $('.step-date', li);

  // Enter ведёт в дату этого же шага, а не на следующий шаг. Дата контроля —
  // суть трекера, и проскакивать её основной клавишей нельзя: иначе цепочка
  // шагов заводится без дат и система молчит вместо того, чтобы спрашивать.
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
      else addStep(li);
    }
  });

  let timer;
  const preview = () => {
    clearTimeout(timer);
    timer = setTimeout(() => showDate(li), 220);
  };
  dateInput.addEventListener('input', preview);
  dateInput.addEventListener('blur', () => showDate(li));

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

async function showDate(li) {
  const input = $('.step-date', li);
  const out = $('.date-preview', li);
  const err = $('.step-err-date', li);
  const text = input.value.trim();

  input.classList.remove('invalid');
  err.hidden = true;
  if (!text) { out.textContent = ''; out.classList.remove('past'); return; }

  try {
    const r = await post('/api/parse-date', { text });
    if (r.ok) {
      out.textContent = r.label || '';
      out.classList.toggle('past', !!r.past);
    } else {
      out.textContent = '';
      out.classList.remove('past');
      input.classList.add('invalid');
      err.textContent = 'Не понял дату. Можно: 18.08 · завтра · +3 · пн';
      err.hidden = false;
    }
  } catch {
    out.textContent = '';
  }
}

// --- отправка --------------------------------------------------------------

async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return r.json();
}

function collect() {
  return {
    title: $('#title').value,
    tags: $('#tags').value.split(',').map((s) => s.trim()).filter(Boolean),
    body: $('#body').value,
    steps: [...steps.children].map((li) => ({
      title: $('.step-title', li).value,
      control_date: $('.step-date', li).value,
    })),
  };
}

function clearErrors() {
  $('#err-title').hidden = true;
  $('#err-steps').hidden = true;
  $('#title').classList.remove('invalid');
  [...steps.children].forEach((li) => {
    $('.step-title', li).classList.remove('invalid');
    $('.step-date', li).classList.remove('invalid');
    $('.step-err-title', li).hidden = true;
    $('.step-err-date', li).hidden = true;
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
    const m = /^steps\.(\d+)\.(title|control_date)$/.exec(field || '');
    if (m) {
      const li = steps.children[+m[1]];
      if (!li) continue;
      const isTitle = m[2] === 'title';
      const input = $(isTitle ? '.step-title' : '.step-date', li);
      const box = $(isTitle ? '.step-err-title' : '.step-err-date', li);
      input.classList.add('invalid');
      box.textContent = error;
      box.hidden = false;
      first = first || input;
      continue;
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

    const saved = $('#saved');
    saved.textContent = `Создана: ${r.task} · шагов ${r.steps} · ${r.status}`;
    saved.hidden = false;
    setTimeout(() => { saved.hidden = true; }, 6000);

    $('#form').reset();
    steps.replaceChildren();
    addStep();
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
