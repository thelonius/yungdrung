'use strict';

// Карточка записи базы знаний. Режим определяется query-параметром "slug":
// он есть — правим существующую запись (GET /api/kb/note?slug=...), его нет —
// пустая форма создания. Страница не считает ничего сама — только собирает,
// что человек ввёл, и показывает ответ ядра как есть.

const $ = (s, r = document) => r.querySelector(s);

const slugИзURL = new URLSearchParams(location.search).get('slug') || '';
let режим = slugИзURL ? 'edit' : 'create';
let оригинальныйSlug = slugИзURL;  // slug, под которым запись сейчас лежит в базе

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

// --- загрузка и отрисовка ---------------------------------------------------

function пустаяФорма() {
  $('#card').hidden = false;
  $('#page-title').textContent = 'Новая запись';
  $('#delete-note').hidden = true;
  $('#slug').value = '';
  $('#title').value = '';
  $('#tags').value = '';
  $('#aliases').value = '';
  $('#body').value = '';
  $('#title').focus();
}

async function загрузить() {
  if (режим !== 'edit') return пустаяФорма();

  const d = await get('/api/kb/note?slug=' + encodeURIComponent(оригинальныйSlug));
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

  $('#slug').value = note.slug;
  $('#title').value = note.title;
  $('#tags').value = (note.tags || []).join(', ');
  $('#aliases').value = (note.aliases || []).join(', ');
  $('#body').value = note.body || '';
}

// --- ошибки -------------------------------------------------------------

function очиститьОшибки() {
  for (const id of ['err-slug', 'err-title', 'err-general']) {
    $('#' + id).hidden = true;
  }
  $('#slug').classList.remove('invalid');
  $('#title').classList.remove('invalid');
}

function показатьОшибки(errors) {
  очиститьОшибки();
  let первая = null;
  for (const { field, error } of errors) {
    if (field === 'slug') {
      $('#slug').classList.add('invalid');
      $('#err-slug').textContent = error;
      $('#err-slug').hidden = false;
      первая = первая || $('#slug');
      continue;
    }
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
    slug: $('#slug').value.trim(),
    title: $('#title').value.trim(),
    body: $('#body').value,
    tags: $('#tags').value.split(',').map((s) => s.trim()).filter(Boolean),
    aliases: $('#aliases').value.split(',').map((s) => s.trim()).filter(Boolean),
  };

  try {
    const url = режим === 'edit'
      ? '/api/kb/note-update?slug=' + encodeURIComponent(оригинальныйSlug)
      : '/api/kb/note-create';
    const r = await post(url, данные);
    if (!r.ok) return показатьОшибки(r.errors || [{ field: null, error: 'не сохранилось' }]);

    всплывашка('Сохранено');
    режим = 'edit';
    оригинальныйSlug = r.note;
    history.replaceState(null, '', '/база/запись?slug=' + encodeURIComponent(r.note));
    await загрузить();
  } catch (e) {
    показатьОшибки([{ field: null, error: 'сервер не ответил: ' + e.message }]);
  } finally {
    save.disabled = false;
  }
}

$('#save-note').addEventListener('click', сохранить);

$('#delete-note').addEventListener('click', async () => {
  const заголовок = $('#title').value || оригинальныйSlug;
  if (!confirm(`Удалить «${заголовок}» насовсем? Это нельзя отменить.`)) return;
  const r = await post('/api/kb/note-delete', { slug: оригинальныйSlug });
  if (r.ok) location.href = '/база';
  else всплывашка((r.errors || [{}])[0].error || 'не получилось');
});

// --- старт ------------------------------------------------------------------

загрузить();
