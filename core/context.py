"""Контекст операции: где лежит стор и настройки.

Единственное, что ядро знает о внешнем мире, — путь к каталогу данных.
`engine.py` строит контекст от своего `VAULT` при каждом вызове (тесты
подменяют `VAULT` через monkeypatch уже после импорта), `api/` — от
конфигурации приложения. Сам `core` переменных окружения не читает.
"""
from dataclasses import dataclass
from pathlib import Path

import settings as cfg
import store
import worktime


@dataclass(frozen=True)
class Context:
    vault: Path

    @property
    def store(self) -> store.Store:
        return store.Store(self.vault / "стор.db")

    def settings_path(self) -> Path:
        return cfg.settings_path(self.vault)

    def reasons(self) -> list[str]:
        """Справочник причин, раздел 5.4 ТЗ. Редактируется в настройках, здесь
        только чтение — захардкоженный список нельзя было бы переименовать или
        заархивировать из интерфейса."""
        return cfg.active_reason_names(self.settings_path())

    def work(self, *, start=None, end=None, weekends=None) -> dict:
        """Рабочие часы: настройки из файла — база, явные аргументы — оверрайд
        поверх них. Битый файл настроек не должен останавливать ленту и завал:
        почему он битый, разбирается в настройках-интерфейсе, а не здесь."""
        try:
            сохранённые = cfg.load(self.settings_path())["notifications"]
        except cfg.SettingsError:
            сохранённые = cfg.defaults()["notifications"]

        def выбрать(из_аргумента, ключ):
            return из_аргумента if из_аргумента is not None else сохранённые.get(ключ)

        return worktime.settings(
            start=выбрать(start, "start"),
            end=выбрать(end, "end"),
            weekends=выбрать(weekends, "weekends"),
        )

    def sync_tags(self, tags) -> None:
        """Тег, вписанный в карточку задачи (свободное поле «через запятую»),
        обязан появиться в справочнике — иначе tags-rename/tags-merge (issue #7)
        не находят теги, которые реально стоят на задачах.

        `add_tag` на уже существующем имени — `SettingsError` (дубликат), это
        штатный повтор, а не сбой сохранения задачи, поэтому любая
        `SettingsError` глотается: карточка не должна не сохраниться из-за
        строки в другом файле."""
        path = self.settings_path()
        for имя in tags:
            try:
                cfg.add_tag(имя, "gray", pinned=False, path=path)
            except cfg.SettingsError:
                pass
