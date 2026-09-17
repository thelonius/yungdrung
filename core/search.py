"""Поиск по истории — R24, главный ответ на «как я это делал в прошлый раз».
Ищет по задачам, их заметкам, названиям шагов и записям базы знаний."""
import kb
from core.context import Context
from core.errors import ValidationError
from core.models import SearchResult


def query_of(text) -> str:
    """Человеческий запрос → выражение FTS5.

    Слова приводятся к тем же леммам, что и текст в индексе: иначе «гранту» в
    поиске не нашло бы «грант» в задаче, ради чего лемматизация и заводилась.
    Слова соединяются через AND — человек, набравший два слова, ищет то, где
    есть оба. Каждое слово берётся в кавычки: в запрос попадают знаки, которые
    FTS5 считает синтаксисом (дефис, звёздочка, скобки)."""
    слова = [w for w, _, _ in kb.tokenize(text or "")]
    if not слова:
        return ""
    return " AND ".join(f'"{kb.lemma(w)}"' for w in слова)


def search(ctx: Context, text, *, kind=None, limit=50) -> SearchResult:
    запрос = query_of(text)
    if not запрос:
        raise ValidationError.single("text", "Пустой запрос")
    найдено = ctx.store.search(запрос, limit=int(limit or 50),
                               source_types=[kind] if kind else None)
    return SearchResult(query=запрос, count=len(найдено), results=найдено)
