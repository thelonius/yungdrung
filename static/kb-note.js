'use strict';

const { $, $$, get, post, toast, shortDate, dateField } = Yd;

// Карточка записи базы знаний. Режим определяется query-параметром "id": он
// есть — правим существующую запись (GET /api/kb/note?id=...), его нет —
// пустая форма создания. Страница не считает ничего сама: собирает введённое
// и показывает ответ ядра как есть.
//
// Идентификатор числовой и при правке не меняется — в том числе когда меняют
// название. Поэтому «под каким id запись лежит» и «во что её переименовывают»
// здесь не расходятся, и id уезжает в тело запроса вместе с остальными полями.


const idИзURL = new URLSearchParams(location.search).get('id') || '';
let режим = idИзURL ? 'edit' : 'create';
let idЗаписи = idИзURL;



// --- загрузка и отрисовка ---------------------------------------------------

function пустаяФорма() {
  $('#card').hidden = false;
  $('#page-title').textContent = 'Новая запись';
  $('#delete-note').hidden = true;
  $('#title').value = '';
  $('#aliases').value = '';
  $('#body').value = '';
  $('#title').focus();
}

async function загрузить() {
  if (режим !== 'edit') return пустаяФорма();

  const d = await get('/api/kb/note?id=' + encodeURIComponent(idЗаписи));
  if (d.ok === false) {
    $('#load-err').textContent = (d.errors || [{}]).map((e) => e.error).join('; ') ||
      'запись не найдена';
    $('#load-err').hidden = false;
    $('#card').hidden = true;
    return;
  }
  отрисовать(d);
}

function отрисовать(note) {
  $('#load-err').hidden = true;
  $('#card').hidden = false;
  $('#page-title').textContent = note.title;
  $('#delete-note').hidden = false;

  $('#title').value = note.title;
  $('#aliases').value = (note.aliases || []).join(', ');
  $('#body').value = note.body || '';
}

// --- ошибки -------------------------------------------------------------

function очиститьОшибки() {
  for (const id of ['err-title', 'err-general']) $('#' + id).hidden = true;
  $('#title').classList.remove('invalid');
}

function показатьОшибки(errors) {
  очиститьОшибки();
  let первая = null;
  for (const { field, error } of errors) {
    if (field === 'title') {
      $('#title').classList.add('invalid');
      $('#err-title').textContent = error;
      $('#err-title').hidden = false;
      первая = первая || $('#title');
      continue;
    }
    $('#err-general').textContent = error;
    $('#err-general').hidden = false;
  }
  if (первая) первая.focus();
}

// --- сохранение и удаление ------------------------------------------------

async function сохранить() {
  очиститьОшибки();
  const save = $('#save-note');
  save.disabled = true;

  const данные = {
    title: $('#title').value.trim(),
    body: $('#body').value,
    aliases: $('#aliases').value.split(',').map((s) => s.trim()).filter(Boolean),
  };
  if (режим === 'edit') данные.id = idЗаписи;

  try {
    const url = режим === 'edit' ? '/api/kb/note-update' : '/api/kb/note-create';
    const r = await post(url, данные);
    if (!r.ok) return показатьОшибки(r.errors || [{ field: null, error: 'не сохранилось' }]);

    toast('Сохранено');
    режим = 'edit';
    idЗаписи = String(r.note);
    history.replaceState(null, '', '/база/запись?id=' + encodeURIComponent(idЗаписи));
    await загрузить();
  } catch (e) {
    показатьОшибки([{ field: null, error: 'сервер не ответил: ' + e.message }]);
  } finally {
    save.disabled = false;
  }
}

$('#save-note').addEventListener('click', сохранить);

$('#delete-note').addEventListener('click', async () => {
  const заголовок = $('#title').value || idЗаписи;
  if (!confirm(`Удалить «${заголовок}» насовсем? Это нельзя отменить.`)) return;
  const r = await post('/api/kb/note-delete', { id: idЗаписи });
  if (r.ok) location.href = '/база';
  else toast((r.errors || [{}])[0].error || 'не получилось');
});

// --- старт ------------------------------------------------------------------

загрузить();
