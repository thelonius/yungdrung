'use strict';

// Резервные копии, восстановление и экспорт в JSON. Раздел 9 ТЗ, R25 и R11.
//
// Страница не решает, что где лежит и что откуда восстанавливать — она зовёт
// ядро и показывает ответ. Дефолтный путь копий и текст ошибки тоже считает
// движок; здесь только перевод байтов в КБ/МБ для глаз, это не расчёт данных.

const $ = (s, r = document) => r.querySelector(s);
const confirmDlg = $('#confirm-restore');
const tagDlg = $('#tag-action');

let восстановитьЦель = null; // копия, которую подтверждаем в диалоге
let тегДействие = null; // {mode: 'rename'|'merge', tag} — на что отвечает tag-action

async function get(url) {
  const r = await fetch(url);
  return r.json();
}

async function post(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
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

// Байты для человека. Движок отдаёт число как есть — округление и подпись
// «КБ/МБ» это не вычисление данных, а просто другая запись того же числа.
function размерЧитаемо(bytes) {
  if (bytes < 1024) return `${bytes} Б`;
  const кб = bytes / 1024;
  if (кб < 1024) return `${кб.toFixed(1)} КБ`;
  return `${(кб / 1024).toFixed(1)} МБ`;
}

function датаЧитаемо(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}

// --- список копий ------------------------------------------------------

function карточка(c) {
  const li = document.createElement('li');
  li.className = 'tpl-card';

  const info = document.createElement('div');
  info.className = 'backup-info';
  const date = document.createElement('div');
  date.className = 'backup-date';
  date.textContent = датаЧитаемо(c.created);
  const meta = document.createElement('div');
  meta.className = 'backup-meta';
  meta.textContent = `${размерЧитаемо(c.bytes)} · ${c.name}`;
  info.append(date, meta);

  const actions = document.createElement('div');
  actions.className = 'tpl-actions';
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'ghost small';
  btn.textContent = 'Восстановить';
  btn.addEventListener('click', () => открытьПодтверждение(c));
  actions.append(btn);

  li.append(info, actions);
  return li;
}

async function загрузитьКопии() {
  const d = await get('/api/backups');
  $('#backup-dest').textContent = `Папка копий: ${d.dest}`;
  const list = $('#backup-list');
  list.replaceChildren(...d.copies.map(карточка));
  $('#backup-empty').hidden = d.copies.length > 0;
}

$('#backup-now').addEventListener('click', async () => {
  const btn = $('#backup-now');
  btn.disabled = true;
  try {
    const r = await post('/api/backup', { force: true });
    if (!r.ok) {
      всплывашка((r.errors || []).map((e) => e.error).join('; ') || 'копия не снялась');
      return;
    }
    всплывашка(`Копия снята: ${r.name}`);
    await загрузитьКопии();
  } finally {
    btn.disabled = false;
  }
});

// --- восстановление ------------------------------------------------------

function открытьПодтверждение(c) {
  восстановитьЦель = c;
  $('#cr-text').textContent =
    `Заменить текущие данные копией от ${датаЧитаемо(c.created)}? ` +
    `Текущее состояние сохранится отдельно, но лучше быть уверенным.`;
  $('#cr-err').hidden = true;
  confirmDlg.showModal();
}

$('#cr-no').addEventListener('click', () => confirmDlg.close());

// Клик по подложке — то же самое, что «Отмена»: target === confirmDlg только
// у клика вне карточки, содержимое диалога событие до этого не пропускает.
confirmDlg.addEventListener('click', (e) => {
  if (e.target === confirmDlg) confirmDlg.close();
});

$('#cr-yes').addEventListener('click', async () => {
  if (!восстановитьЦель) return;
  const btn = $('#cr-yes');
  btn.disabled = true;
  try {
    const r = await post('/api/restore', { file: восстановитьЦель.file });
    if (!r.ok) {
      $('#cr-err').textContent = (r.errors || []).map((e) => e.error).join('; ') || 'не вышло';
      $('#cr-err').hidden = false;
      return;
    }
    confirmDlg.close();
    показатьУспехВосстановления(r);
  } finally {
    btn.disabled = false;
  }
});

function показатьУспехВосстановления(r) {
  const el = document.createElement('div');
  el.className = 'toast';
  const текст = document.createElement('span');
  текст.textContent = `Восстановлено файлов: ${r.restored}. `;
  const кнопка = document.createElement('button');
  кнопка.type = 'button';
  кнопка.className = 'quiet';
  кнопка.style.color = 'inherit';
  кнопка.textContent = 'Перезагрузить страницу';
  кнопка.addEventListener('click', () => location.reload());
  el.append(текст, кнопка);
  document.body.append(el);
}

// --- экспорт ------------------------------------------------------------

$('#export-json').addEventListener('click', async () => {
  const btn = $('#export-json');
  btn.disabled = true;
  try {
    const r = await post('/api/export-json', {});
    const out = $('#export-result');
    if (!r.ok) {
      out.textContent = (r.errors || []).map((e) => e.error).join('; ') || 'не вышло';
      return;
    }
    out.textContent = `Сохранено: ${r.file} (задач: ${r.tasks}, заметок: ${r.notes}, шаблонов: ${r.templates})`;
  } finally {
    btn.disabled = false;
  }
});

// --- рабочее время --------------------------------------------------------
//
// Правило «начало раньше конца» и сами дефолты считает worktime.py через
// settings.py — страница не проверяет часы сама, только показывает ответ.

let текущиеНастройки = null;

async function загрузитьНастройки() {
  текущиеНастройки = await get('/api/settings');
  if (текущиеНастройки.error) {
    всплывашка('Файл настроек повреждён: ' + текущиеНастройки.error);
    return;
  }
  const n = текущиеНастройки.notifications;
  $('#work-start').value = n.start;
  $('#work-end').value = n.end;
  $('#work-weekends').checked = !!n.weekends;
  отрисоватьПричины(текущиеНастройки.reasons);
  отрисоватьТеги(текущиеНастройки.tags);
}

$('#work-save').addEventListener('click', async () => {
  $('#work-err').hidden = true;
  const данные = {
    ...текущиеНастройки,
    notifications: {
      ...текущиеНастройки.notifications,
      start: $('#work-start').value.trim(),
      end: $('#work-end').value.trim(),
      weekends: $('#work-weekends').checked,
    },
  };
  const r = await post('/api/settings', данные);
  if (!r.ok) {
    $('#work-err').textContent = (r.errors || []).map((e) => e.error).join('; ') || 'не сохранилось';
    $('#work-err').hidden = false;
    return;
  }
  текущиеНастройки.notifications = данные.notifications;
  const метка = $('#work-saved');
  метка.hidden = false;
  setTimeout(() => { метка.hidden = true; }, 2500);
  if (r.warnings && r.warnings.length) всплывашка(r.warnings.map((w) => w.warning).join(' · '));
});

// --- причины переноса ------------------------------------------------------

function отрисоватьПричины(reasons) {
  $('#reasons-list').replaceChildren(...(reasons || []).map(причинаСтрокой));
}

function причинаСтрокой(r) {
  const li = document.createElement('li');
  li.className = 'reason-row' + (r.archived ? ' is-archived' : '');

  const name = document.createElement('span');
  name.className = 'reason-name';
  name.textContent = r.name;

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'ghost small';
  btn.textContent = r.archived ? 'В архиве' : 'В архив';
  btn.disabled = r.archived;
  btn.addEventListener('click', async () => {
    const ответ = await post('/api/reasons-archive', { name: r.name });
    if (!ответ.ok) return всплывашка((ответ.errors || []).map((e) => e.error).join('; '));
    отрисоватьПричины(ответ.result);
    текущиеНастройки.reasons = ответ.result;
  });

  li.append(name, btn);
  return li;
}

$('#reason-add').addEventListener('click', async () => {
  const input = $('#reason-new');
  const имя = input.value.trim();
  $('#reasons-err').hidden = true;
  if (!имя) return;
  const r = await post('/api/reasons-add', { name: имя });
  if (!r.ok) {
    $('#reasons-err').textContent = (r.errors || []).map((e) => e.error).join('; ') || 'не вышло';
    $('#reasons-err').hidden = false;
    return;
  }
  input.value = '';
  отрисоватьПричины(r.result);
  текущиеНастройки.reasons = r.result;
});

$('#reason-new').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); $('#reason-add').click(); }
});

// --- отклонённые совпадения базы знаний ------------------------------------
//
// Список того, на что заказчик ответил «нет» или «заглушить» (kb.py,
// ExclusionStore). Без отмены заглушённое слово молча пропадает из
// распознавания навсегда — карточка отдаёт ключ ровно в том виде, в каком
// его вернул /api/kb/exclusions, и передаёт его в /api/kb/forget как есть,
// без пересборки: у файлового и БД-склада разный тип kb_entry_id.

function отказСтрокой(и) {
  const li = document.createElement('li');
  li.className = 'reason-row';

  const name = document.createElement('span');
  name.className = 'reason-name';
  const метка = и.scope === 'word' ? 'слово целиком' : (и.title || 'запись удалена');
  name.textContent = `«${и.text}» — ${метка}`;

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'ghost small';
  btn.textContent = 'Вернуть';
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    $('#kb-exclusions-err').hidden = true;
    try {
      const r = await post('/api/kb/forget', {
        key: { kb_entry_id: и.kb_entry_id, text: и.text },
      });
      if (!r.ok) {
        $('#kb-exclusions-err').textContent =
          (r.errors || []).map((e) => e.error).join('; ') || 'не вышло';
        $('#kb-exclusions-err').hidden = false;
        return;
      }
      await загрузитьОтказыБазы();
    } finally {
      btn.disabled = false;
    }
  });

  li.append(name, btn);
  return li;
}

async function загрузитьОтказыБазы() {
  const d = await get('/api/kb/exclusions');
  if (!d.ok) {
    $('#kb-exclusions-err').textContent =
      (d.errors || []).map((e) => e.error).join('; ') || 'не загрузилось';
    $('#kb-exclusions-err').hidden = false;
    return;
  }
  $('#kb-exclusions-list').replaceChildren(...d.exclusions.map(отказСтрокой));
  $('#kb-exclusions-empty').hidden = d.exclusions.length > 0;
}

// --- теги --------------------------------------------------------------
//
// Тег заводится сам при сохранении задачи с новым тегом (engine.py,
// Issue #7) — этот раздел про то, что нельзя автоматом: закрепление,
// переименование и объединение задач с одним тегом на другой.

function отрисоватьТеги(tags) {
  $('#tags-list').replaceChildren(...(tags || []).map(тегСтрокой));
}

function тегСтрокой(t) {
  const li = document.createElement('li');
  li.className = 'reason-row';

  const swatch = document.createElement('span');
  swatch.className = 'tag-swatch';
  swatch.style.background = t.color;

  const name = document.createElement('span');
  name.className = 'reason-name';
  name.textContent = t.name;

  const pin = document.createElement('button');
  pin.type = 'button';
  pin.className = 'ghost small';
  pin.textContent = t.pinned ? 'Закреплён' : 'Закрепить';
  pin.addEventListener('click', async () => {
    const r = await post('/api/tags-toggle-pinned', { name: t.name });
    if (!r.ok) return всплывашка((r.errors || []).map((e) => e.error).join('; '));
    текущиеНастройки.tags = r.result;
    отрисоватьТеги(r.result);
  });

  const rename = document.createElement('button');
  rename.type = 'button';
  rename.className = 'ghost small';
  rename.textContent = 'Переименовать';
  rename.addEventListener('click', () => открытьДействиеТега('rename', t));

  const merge = document.createElement('button');
  merge.type = 'button';
  merge.className = 'ghost small';
  merge.textContent = 'Объединить';
  merge.disabled = (текущиеНастройки.tags || []).length < 2;
  merge.addEventListener('click', () => открытьДействиеТега('merge', t));

  li.append(swatch, name, pin, rename, merge);
  return li;
}

function открытьДействиеТега(mode, t) {
  тегДействие = { mode, tag: t };
  $('#ta-err').hidden = true;
  if (mode === 'rename') {
    $('#ta-title').textContent = `Переименовать «${t.name}»`;
    $('#ta-text').textContent = 'Название изменится и в справочнике, и на всех задачах с этим тегом.';
    $('#ta-rename-field').hidden = false;
    $('#ta-merge-field').hidden = true;
    $('#ta-new-name').value = t.name;
  } else {
    $('#ta-title').textContent = `Объединить «${t.name}» с другим тегом`;
    $('#ta-text').textContent = 'Все задачи с этим тегом получат целевой тег, исходный пропадёт из справочника.';
    $('#ta-rename-field').hidden = true;
    $('#ta-merge-field').hidden = false;
    const select = $('#ta-target');
    select.replaceChildren(...(текущиеНастройки.tags || [])
      .filter((x) => x.name !== t.name)
      .map((x) => {
        const opt = document.createElement('option');
        opt.value = x.name;
        opt.textContent = x.name;
        return opt;
      }));
  }
  tagDlg.showModal();
}

$('#ta-no').addEventListener('click', () => tagDlg.close());

$('#ta-yes').addEventListener('click', async () => {
  if (!тегДействие) return;
  const btn = $('#ta-yes');
  btn.disabled = true;
  try {
    const { mode, tag } = тегДействие;
    const route = mode === 'rename' ? '/api/tags-rename' : '/api/tags-merge';
    const body = mode === 'rename'
      ? { old_name: tag.name, new_name: $('#ta-new-name').value.trim() }
      : { source: tag.name, target: $('#ta-target').value };
    const r = await post(route, body);
    if (!r.ok) {
      $('#ta-err').textContent = (r.errors || []).map((e) => e.error).join('; ') || 'не вышло';
      $('#ta-err').hidden = false;
      return;
    }
    tagDlg.close();
    текущиеНастройки.tags = r.result;
    отрисоватьТеги(r.result);
    всплывашка(mode === 'rename' ? 'Тег переименован'
      : `Тег объединён, задач затронуто: ${r.tasks_updated}`);
  } finally {
    btn.disabled = false;
  }
});

$('#tag-add').addEventListener('click', async () => {
  const input = $('#tag-new');
  const имя = input.value.trim();
  $('#tags-err').hidden = true;
  if (!имя) return;
  const r = await post('/api/tags-add', {
    name: имя, color: $('#tag-new-color').value, pinned: false,
  });
  if (!r.ok) {
    $('#tags-err').textContent = (r.errors || []).map((e) => e.error).join('; ') || 'не вышло';
    $('#tags-err').hidden = false;
    return;
  }
  input.value = '';
  текущиеНастройки.tags = r.result;
  отрисоватьТеги(r.result);
});

$('#tag-new').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); $('#tag-add').click(); }
});

загрузитьКопии();
загрузитьНастройки();
загрузитьОтказыБазы();
