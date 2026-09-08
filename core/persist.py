"""Единственный путь записи задачи. Правило единственного писателя из
`CLAUDE.md` теперь держится на том, что `store.save_task` зовут только отсюда.
"""
import kb
from core.context import Context
from domain.steps import steps_of, task_summary

# Версия формы задачи в `meta["schema"]`. Не путать со `store.SCHEMA` —
# версией структуры БД.
TASK_SCHEMA = 1


def search_text_of_task(task) -> str:
    """Что от задачи попадает в поиск: заголовок, заметка и названия шагов.

    Причины переносов сюда не идут — решение по Q21 («пока не нужно»). Когда
    понадобятся, они станут отдельными строками индекса со своим `source_type`,
    и ни эта функция, ни форма таблицы не изменятся.
    """
    куски = [task["path"].stem, (task.get("body") or "")]
    куски += [s.get("title") or "" for s in steps_of(task)]
    return "\n".join(к for к in куски if к)


def index_task(склад, task) -> None:
    """Обновить поисковый индекс по одной задаче — сразу после её записи.

    Одна строка, а не пересборка всего: `reindex` нужен после переезда и для
    починки, но платить им за каждую отметку шага нельзя. Сбой индексации не
    роняет запись: задача уже в базе, и потерять её из-за того, что не
    собрались леммы, было бы хуже, чем разойтись с индексом — индекс чинится
    командой `reindex`, а задача ничем.
    """
    try:
        склад.search_replace(
            "task", task["path"].stem, task["path"].stem,
            (task.get("body") or "").strip()[:200],
            kb.lemmatize_text(search_text_of_task(task)))
    except Exception:
        pass


def save_task(ctx: Context, task, today, expected_step=None) -> None:
    """Статус и сводка пересчитываются при каждой записи, руками их никто не
    ставит. Сводка в БД не хранится — колонок под неё нет; здесь она мержится
    в `task["meta"]`, потому что вызывающий код читает `task["meta"]["status"]`
    сразу после записи. `expected_step` — сторож от гонки при одновременной
    отметке, см. `store.save_task`."""
    meta = task["meta"]
    meta["schema"] = TASK_SCHEMA
    meta.update(task_summary(task, today))
    ctx.sync_tags(meta.get("tags") or [])
    склад = ctx.store
    склад.save_task(task, today, expected_step=expected_step)
    index_task(склад, task)
