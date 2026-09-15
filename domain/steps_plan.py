"""План шагов задачи: чистые функции над dict-формой стора (§3.2 спецификации
среза 2). Владелец B1.

Сюда переезжают из `engine.py` файлом, без изменения логики:
`_parse_optional_date`, `default_step_start`, `MODES`, `resolve_steps`,
`_финиш_узла`, `walk_resolved`, `validate_new_task` (правило имени через
`domain.names.title_error`), `soft_warnings`, `build_task`, `validate_task_edit`,
`apply_task_edit` (плюс нормализация листа, ставшего группой, Р14, и подсчёт
пропавших шагов до мутации), `_strip_steps_block` → `strip_steps_block`.
Новое: `plan(steps_data, start, today, *, old=None)` и `to_planned(nodes)` для
предпросмотра формы. `engine.py` импортирует всё обратно под прежними именами.

Пути ошибок здесь точечные (`steps.1.steps.0.control_date`); в скобки их
переводит `api/errors.py::bracket_path` (Р1). Заготовка шага 0.
"""
