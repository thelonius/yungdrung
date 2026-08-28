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
  meta.textContent = `${t.steps} ${слово}`
    + (t.attachments ? ` · файлов: ${t.attachments}` : '')
    + (t.tags.length ? ' · ' + t.tags.join(', ') : '');
  const recur = document.createElement('div');
  recur.className = 'tpl-recur ' + (t.recurrence ? 'is-set' : 'is-unset');
  recur.textContent = t.recurrence ? '↻ ' + t.recurrence.description : 'без повторения';
  info.append(name, meta, recur);

  const actions = document.createElement('div');
  actions.className = 'tpl-actions';

  const filesBtn = document.createElement('button');
  filesBtn.type = 'button';
  filesBtn.className = 'ghost small';
  filesBtn.textContent = 'Файлы';
  filesBtn.addEventListener('click', () => открытьФайлы(t.name));

  const recurBtn = document.createElement('button');
  recurBtn.type = 'button';
  recurBtn.className = 'ghost small';
  recurBtn.textContent = t.recurrence ? 'Повторение' : 'Настроить повтор';
  recurBtn.addEventListener('click', () => открытьПовторение(t));

  const delBtn = document.createElement('button');
  delBtn.type = 'button';
  delBtn.className = 'ghost small danger-hover';
  delBtn.textContent = 'Удалить';
  delBtn.addEventListener('click', () => удалить(t.name));

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'primary small';
  btn.textContent = 'Завести задачу';
  btn.addEventListener('click', () => открыть(t.name));

  actions.append(filesBtn, recurBtn, delBtn, btn);
  li.append(info, actions);
  return li;
}

async function удалить(name) {
  if (!confirm(`Удалить шаблон «${name}» насовсем? Это нельзя отменить.`)) return;
  const r = await post('/api/template-delete', { name });
  if (r.ok) await загрузить();
  else alert((r.errors || [{}])[0].error || 'не получилось');
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

// Поля правила, под которые в форме нет виджета: перенос с выходного, задел,
// дата окончания, пауза. Экзотику сюда сознательно не выводили (вопрос Q26 в
// ТЗ), но потерять её нельзя — она приезжает и из разбора текста («последний
// рабочий день месяца» это перенос назад), и из уже сохранённого правила.
// Молча уронив её, форма показала бы календарное 31.10 вместо рабочего 30.10.
// Видно её в подписи под полями: `describe` проговаривает все эти поля словами.
let повторДопПоля = {};

const ДОП_ПОЛЯ = ['holiday_shift', 'lead_days', 'until', 'paused'];

function запомнитьДопПоля(правило) {
  повторДопПоля = {};
  for (const k of ДОП_ПОЛЯ) {
    if (правило && правило[k] !== undefined && правило[k] !== null) {
      повторДопПоля[k] = правило[k];
    }
  }
}

function собратьПравило() {
  const freq = $('#r-freq').value;
  const rule = { ...повторДопПоля, freq };
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

// Текст разбирает ядро (`recurrence.parse_text`), а не страница: правило
// одинаково понимают форма, CLI и будущий бот. Разобранное раскладывается по
// тем же селекторам — человек видит, как его поняли, и может поправить руками.
// Обратно текст из полей не собирается: подпись под ними и так это показывает,
// а вписывать в поле ввода то, чего человек не печатал, значит спорить с ним.
async function разобратьТекстПовторения() {
  const текст = $('#r-text').value.trim();
  const ошибка = $('#r-text-err');
  if (!текст) { ошибка.hidden = true; return; }

  const r = await post('/api/recurrence-parse', { text: текст });
  if (!r.ok) {
    ошибка.textContent = (r.errors || [{}])[0].error || 'не разобрал';
    ошибка.hidden = false;
    return;
  }
  ошибка.hidden = true;
  разложитьПравило(r.rule);
  показатьПоля();
  обновитьОписание();
}

// Правило → селекторы. Общая для разбора текста и для открытия сохранённого
// правила: раскладка одна, и расходиться этим двум путям негде.
function разложитьПравило(правило) {
  запомнитьДопПоля(правило);
  $('#r-freq').value = правило ? правило.freq : 'monthly';
  $('#r-interval').value = (правило && правило.interval) || 1;
  for (const c of $('#r-weekdays').querySelectorAll('input')) {
    c.checked = ((правило && правило.byweekday) || []).includes(+c.value);
  }
  $('#r-monthday').value = правило && правило.bymonthday
    ? правило.bymonthday.join(', ') : '';
}

function открытьПовторение(t) {
  повторЦелевой = t.name;
  $('#r-name').textContent = t.name;
  $('#r-err').hidden = true;
  $('#r-text').value = '';
  $('#r-text-err').hidden = true;

  разложитьПравило(t.recurrence);
  $('#r-anchor').value = t.recurrence ? t.recurrence.anchor : '';

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

$('#r-text').addEventListener('input', () => {
  clearTimeout($('#r-text')._t);
  $('#r-text')._t = setTimeout(разобратьТекстПовторения, 300);
});

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

// --- файлы шаблона -----------------------------------------------------------
//
// Тот же протокол, что у вложений задачи: файл уходит base64-строкой в теле
// обычного JSON-запроса, хранит и раздаёт байты движок. Владелец — шаблон,
// адресуется именем (`--template` в `cmd_attach`).

function размерФайла(bytes) {
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}

function файлВBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    // readAsDataURL отдаёт «data:тип;base64,XXXX» — серверу нужна только часть
    // после запятой, префикс он не разбирает и не ждёт.
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] || '');
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

function строкаФайла(имяФайла, размер, ссылка, убрать) {
  const li = document.createElement('li');
  li.className = 'attach-row';

  const подпись = document.createElement(ссылка ? 'a' : 'span');
  подпись.textContent = имяФайла;
  if (ссылка) {
    подпись.href = ссылка;
    подпись.target = '_blank';
    подпись.rel = 'noopener';
  }

  const вес = document.createElement('span');
  вес.className = 'attach-size';
  вес.textContent = размерФайла(размер);

  const крестик = document.createElement('button');
  крестик.type = 'button';
  крестик.className = 'attach-remove';
  крестик.title = 'Убрать файл';
  крестик.textContent = '×';
  крестик.addEventListener('click', убрать);

  li.append(подпись, вес, крестик);
  return li;
}

async function загрузитьФайлыШаблона(listEl, имяШаблона) {
  const d = await get('/api/attachments?template=' + encodeURIComponent(имяШаблона));
  listEl.replaceChildren(...(d.attachments || []).map((a) =>
    строкаФайла(a.filename, a.bytes, `/вложение/${a.id}`, async () => {
      await post('/api/attachments-delete', { id: a.id });
      await загрузитьФайлыШаблона(listEl, имяШаблона);
      await загрузить();
    })));
}

async function отправитьФайл(file, владелец, errEl) {
  let data;
  try {
    data = await файлВBase64(file);
  } catch {
    return { ok: false, errors: [{ error: 'не удалось прочитать файл' }] };
  }
  const r = await post('/api/attachments-add', { ...владелец, filename: file.name, data });
  if (!r.ok && errEl) {
    errEl.textContent = (r.errors || [{}])[0].error || 'не получилось';
    errEl.hidden = false;
  }
  return r;
}

// --- файлы уже сохранённого шаблона ------------------------------------------

const tfDlg = $('#tpl-files');
let файловыйЦелевой = null;

async function открытьФайлы(name) {
  файловыйЦелевой = name;
  $('#tf-name').textContent = name;
  $('#tf-err').hidden = true;
  await загрузитьФайлыШаблона($('#tf-list'), name);
  tfDlg.showModal();
}

$('#tf-add').addEventListener('click', () => $('#tf-input').click());
$('#tf-back').addEventListener('click', () => tfDlg.close());

$('#tf-input').addEventListener('change', async (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  $('#tf-err').hidden = true;
  const r = await отправитьФайл(file, { template: файловыйЦелевой }, $('#tf-err'));
  if (r.ok) {
    await загрузитьФайлыШаблона($('#tf-list'), файловыйЦелевой);
    await загрузить();
  }
});

// --- новый шаблон с нуля -----------------------------------------------------
//
// Шаги здесь без групп (par/seq): у шаблона это поддерживает только развёрнутая
// задача, `templates.py` про группы не знает. Проверяет и приводит к канону
// `validate_template`/`normalize_template` на сервере — форма только собирает
// JSON той же формы и показывает ошибки по полю, как страница «Новая задача».
//
// В полях человек набирает промежутки: «через сколько дней после предыдущего
// шага». Хранится же сдвиг от даты старта — так требует `templates.py`, и это
// правильно, иначе правка одного шага молча двигала бы все следующие. Промежутки
// в форме потому, что процедуру описывают именно ими: «позвонить, через три дня
// подготовить, через неделю сдать». Складывает их в сдвиг страница — счёт по
// календарю (какая это дата, рабочий ли день) остаётся за ядром, а сумма
// введённых чисел никакого календаря не знает.

const ntDlg = $('#new-template');
const ntSteps = $('#nt-steps');
const ntStepTpl = $('#nt-step-tpl');
// Файлы, выбранные до сохранения. Прикрепить их некуда, пока у шаблона нет
// имени: владелец вложения — имя шаблона. Тот же приём, что у гипотез базы
// знаний в форме задачи: копим в памяти, отправляем после успешного создания.
let ntФайлы = [];

function ntRenumber() {
  [...ntSteps.children].forEach((li, i) => {
    $('.step-num', li).textContent = i + 1;
    $('.drop', li).hidden = ntSteps.children.length < 2;
    // Первому шагу отсчитывать не от чего, кроме дня старта.
    $('.step-gap-label', li).textContent = i === 0 ? '' : 'через';
    $('.step-gap-unit', li).textContent = i === 0 ? 'дн. от старта' : 'дн. после предыдущего';
  });
}

function ntAddStep(after = null, focus = true) {
  const li = ntStepTpl.content.firstElementChild.cloneNode(true);
  const title = $('.step-title', li);
  const offset = $('.step-offset', li);
  const time = $('.step-time', li);

  title.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) { e.preventDefault(); offset.focus(); }
  });
  offset.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) { e.preventDefault(); time.focus(); }
  });
  time.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      const next = li.nextElementSibling;
      if (next) $('.step-title', next).focus();
      else ntAddStep(li);
    }
  });

  for (const поле of [offset, time]) поле.addEventListener('input', ntПересчитать);

  $('.drop', li).addEventListener('click', () => {
    if (ntSteps.children.length < 2) return;
    const focusAfter = li.nextElementSibling || li.previousElementSibling;
    li.remove();
    ntRenumber();
    ntПересчитать();
    if (focusAfter) $('.step-title', focusAfter).focus();
  });

  if (after) after.after(li); else ntSteps.append(li);
  ntRenumber();
  ntПересчитать();
  if (focus) title.focus();
  return li;
}

function ntСобратьШаги() {
  // Промежутки складываются в сдвиг от старта — накопленной суммой по списку.
  // Нечисловой промежуток не превращаем в ноль: пусть уедет как есть и ядро
  // ответит ошибкой поля, иначе человек увидит правдоподобную дату вместо
  // жалобы на опечатку. С этого места сумма теряет смысл, поэтому дальше по
  // списку идёт то же неразобранное значение.
  let накоплено = 0;
  let сломалось = false;
  return [...ntSteps.children].map((li) => {
    const сырое = $('.step-offset', li).value.trim();
    let сдвиг;
    if (сломалось || !/^[+-]?\d+$/.test(сырое)) {
      сломалось = true;
      сдвиг = сырое;
    } else {
      накоплено += parseInt(сырое, 10);
      сдвиг = накоплено;
    }
    return {
      title: $('.step-title', li).value,
      offset_days: сдвиг,
      time_of_day: $('.step-time', li).value.trim() || null,
    };
  });
}

function ntСобрать() {
  return {
    name: $('#nt-name').value,
    tags: $('#nt-tags').value.split(',').map((s) => s.trim()).filter(Boolean),
    body: $('#nt-body').value,
    steps: ntСобратьШаги(),
  };
}

// Даты считает ядро (`/api/template-preview`), страница их только раскладывает
// по строкам шагов. Ошибки предпросмотра молчаливые: человек ещё набирает, и
// ругаться на недописанное поле рано — их покажет сохранение.
async function ntОбновитьДаты() {
  const строки = [...ntSteps.children];
  const данные = ntСобрать();
  // Заглушка вместо пустого названия — тот же приём, что у `/api/steps-check`
  // в форме задачи. Промежутки набирают раньше названий, и молчать про даты
  // из-за недописанного заголовка значит не показать их вовсе.
  данные.steps = данные.steps.map((s) => ({ ...s, title: s.title.trim() || '·' }));
  const r = await post('/api/template-preview', {
    ...данные, start: $('#nt-start').value.trim() || 'сегодня',
  });
  if (!r.ok) {
    строки.forEach((li) => { $('.step-when', li).textContent = ''; });
    $('#nt-start-note').textContent = '';
    return;
  }
  $('#nt-start-note').textContent = r.start_text;
  (r.steps || []).forEach((шаг, i) => {
    const li = строки[i];
    if (!li) return;
    const поле = $('.step-when', li);
    поле.textContent = шаг.control_text;
    поле.classList.toggle('past', !!шаг.on_weekend);
  });
}

let ntТаймер;
function ntПересчитать() {
  clearTimeout(ntТаймер);
  ntТаймер = setTimeout(ntОбновитьДаты, 220);
}

function ntПоПутиШага(field, суффикс) {
  const m = new RegExp(`^steps\\.(\\d+)\\.${суффикс}$`).exec(field || '');
  return m ? ntSteps.children[+m[1]] || null : null;
}

function ntClearErrors() {
  $('#nt-err-name').hidden = true;
  $('#nt-err-steps').hidden = true;
  $('#nt-err-general').hidden = true;
  $('#nt-name').classList.remove('invalid');
  [...ntSteps.querySelectorAll('li.step')].forEach((li) => {
    $('.step-title', li).classList.remove('invalid');
    $('.step-offset', li).classList.remove('invalid');
    $('.step-time', li).classList.remove('invalid');
    $('.step-err-title', li).hidden = true;
    $('.step-err-offset', li).hidden = true;
  });
}

function ntShowErrors(errors) {
  let first = null;
  for (const { field, error } of errors) {
    if (field === 'name') {
      $('#nt-name').classList.add('invalid');
      $('#nt-err-name').textContent = error;
      $('#nt-err-name').hidden = false;
      first = first || $('#nt-name');
      continue;
    }
    if (field === 'steps') {
      $('#nt-err-steps').textContent = error;
      $('#nt-err-steps').hidden = false;
      continue;
    }
    const заголовок = ntПоПутиШага(field, 'title');
    if (заголовок) {
      const input = $('.step-title', заголовок);
      const box = $('.step-err-title', заголовок);
      input.classList.add('invalid');
      box.textContent = error;
      box.hidden = false;
      first = first || input;
      continue;
    }
    // offset_days и time_of_day делят одну строку ошибок под полями сдвига:
    // два отдельных места на строку раздули бы форму ради редкого случая.
    const строка = ntПоПутиШага(field, '(?:offset_days|time_of_day)');
    if (строка) {
      const поле = /time_of_day$/.test(field) ? '.step-time' : '.step-offset';
      $(поле, строка).classList.add('invalid');
      const box = $('.step-err-offset', строка);
      box.textContent = error;
      box.hidden = false;
      first = first || $(поле, строка);
      continue;
    }
    $('#nt-err-general').textContent = error;
    $('#nt-err-general').hidden = false;
    first = first || $('#nt-err-general');
  }
  if (first) first.focus?.();
}

function ntПерерисоватьФайлы() {
  $('#nt-attachments').replaceChildren(...ntФайлы.map((f) =>
    строкаФайла(f.name, f.size, null, () => {
      ntФайлы = ntФайлы.filter((x) => x !== f);
      ntПерерисоватьФайлы();
    })));
}

function ntОткрыть() {
  ntClearErrors();
  $('#nt-name').value = '';
  $('#nt-tags').value = '';
  $('#nt-body').value = '';
  $('#nt-start').value = '';
  $('#nt-start-note').textContent = '';
  ntФайлы = [];
  ntПерерисоватьФайлы();
  ntSteps.replaceChildren();
  ntAddStep(null, false);
  ntDlg.showModal();
  $('#nt-name').focus();
}

$('#new-template-btn').addEventListener('click', ntОткрыть);
$('#nt-add-step').addEventListener('click', () => ntAddStep());
$('#nt-back').addEventListener('click', () => ntDlg.close());
$('#nt-start').addEventListener('input', ntПересчитать);
$('#nt-attach-btn').addEventListener('click', () => $('#nt-attach-input').click());

$('#nt-attach-input').addEventListener('change', (e) => {
  const file = e.target.files[0];
  e.target.value = '';
  if (!file) return;
  $('#nt-err-attach').hidden = true;
  ntФайлы.push(file);
  ntПерерисоватьФайлы();
});

$('#nt-name').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) { e.preventDefault(); $('#nt-tags').focus(); }
});
$('#nt-tags').addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.ctrlKey && !e.metaKey) {
    e.preventDefault();
    const first = ntSteps.firstElementChild;
    if (first) $('.step-title', first).focus();
  }
});

$('#nt-save').addEventListener('click', async () => {
  ntClearErrors();
  const save = $('#nt-save');
  save.disabled = true;
  try {
    const r = await post('/api/template-create', ntСобрать());
    if (!r.ok) return ntShowErrors(r.errors || [{ field: null, error: 'не сохранилось' }]);

    // Файлы уходят только сейчас: до этой строчки шаблона не было, а владелец
    // вложения — его имя. Шаблон уже сохранён, поэтому неудачная загрузка
    // файла — повод показать, какие именно не доехали, а не откатывать всё.
    const непрошедшие = [];
    for (const file of ntФайлы) {
      const a = await отправитьФайл(file, { template: r.template }, null);
      if (!a.ok) непрошедшие.push(file.name);
    }
    if (непрошедшие.length) {
      $('#nt-err-attach').textContent = 'Шаблон сохранён, но файлы не прикрепились: '
        + непрошедшие.join(', ');
      $('#nt-err-attach').hidden = false;
      ntФайлы = ntФайлы.filter((f) => непрошедшие.includes(f.name));
      ntПерерисоватьФайлы();
      await загрузить();
      return;
    }

    ntDlg.close();
    await загрузить();
  } catch (e) {
    ntShowErrors([{ field: null, error: 'сервер не ответил: ' + e.message }]);
  } finally {
    save.disabled = false;
  }
});

загрузить();
