// Разбор скобочного пути поля (`api/errors.py::bracket_path`, решение Р1
// SLICE2_SPEC.md): HTTP всегда отдаёт `steps[1].steps[0].control_date`,
// `tags[1]`, `mentions[0].title`. Ядро и CLI держат точечный формат — сюда
// он не долетает, разбирать его не нужно.
export type FieldPath = { indices: number[]; leaf: string };

const SEGMENT = /^([A-Za-zА-Яа-яЁё_][\w]*)(?:\[(\d+)\])?$/;

/**
 * `'steps[1].steps[0].control_date'` → `{indices:[1,0], leaf:'control_date'}`.
 * `'tags[1]'` → `{indices:[1], leaf:'tags'}` — сегмент сам себе лист, если у
 * него нет продолжения через точку (ошибка про элемент списка, не про его
 * поле). `'title'` → `{indices:[], leaf:'title'}`.
 */
export function parse(field: string): FieldPath {
  const segments = field.split('.');
  const indices: number[] = [];
  let leaf = '';
  segments.forEach((seg, i) => {
    const m = SEGMENT.exec(seg);
    if (!m) { leaf = seg; return; }
    const [, name, idx] = m;
    if (idx !== undefined) indices.push(Number(idx));
    if (i === segments.length - 1) leaf = name;
  });
  return { indices, leaf };
}
