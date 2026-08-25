#!/usr/bin/env python3
"""Разбудить трекер и показать, что подошло. Это запускает Планировщик Windows.

  python3 remind.py                    показать в консоль (проверка)
  python3 remind.py --channel windows  системный тост
  python3 remind.py --dry-run          только сказать, кого бы показал

Почему планировщик, а не своё приложение в трее: единственная работа трея —
жить в фоне и показывать уведомления, а планировщик делает и то и другое
штатными средствами системы. Он переживает перезагрузку, не требует второго
рантайма рядом с Python и не просит заказчика ничего держать открытым.

Порядок внутри: пересчитать сводку (статусы устаревают сами по себе, от того
что прошёл день), взять ленту, отобрать тех, кого пора показать, показать одним
уведомлением и записать факт показа. Записываем только после успешной отправки:
иначе упавший канал молча съест напоминание.
"""
import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import backup
import engine
import notify
import settings as cfg
import worktime


def _notification_settings():
    """Настройки уведомлений: файл — база, битый файл не роняет напоминание.
    Тот же приём, что у рабочих часов в `engine._work`."""
    try:
        return cfg.load(cfg.settings_path(engine.VAULT))["notifications"]
    except cfg.SettingsError:
        # Почему файл битый, разбирается в настройках-интерфейсе, а не тут.
        return cfg.defaults()["notifications"]


def повтор_минут(args, notif=None):
    """Через сколько минут напоминать повторно: аргумент вызова, иначе настройки
    вольта, иначе значение по умолчанию из `notify`.

    Раньше значение из настроек не читалось вовсе, и `repeat_minutes` лежал в
    `Настройки.json` мёртвым ключом: форма его не показывала, а повтор всё
    равно шёл через пятнадцать минут.
    """
    if getattr(args, "repeat", None) is not None:
        return args.repeat
    сохранённые = notif if notif is not None else _notification_settings()
    return сохранённые.get("repeat_minutes") or notify.ПОВТОР_МИНУТ


def _backup_settings():
    try:
        return cfg.load(cfg.settings_path(engine.VAULT))["backup"]
    except cfg.SettingsError:
        return cfg.defaults()["backup"]


def автобэкап(today):
    """Снять копию по расписанию, если пора. Требование R25/R28 —
    `backup.frequency_hours` хранился и валидировался, но копию по нему не
    снимал никто: не было ни своего Планировщика для бэкапа, ни вызова отсюда.

    Отдельного задания в Планировщике под это заводить не стали: `remind.py`
    и так просыпается каждые пять минут, `backup.due()` дешёвая проверка
    времени последней копии, а держать вторую задачу ради редкой операции —
    лишняя сущность на ровном месте. Тот же принцип, что уже применён к
    `cmd_recur` строчкой выше: пробуждение одно, обязанностей у него несколько.

    Сбой копии не должен останавливать напоминания — ради этого он ловится
    здесь же и уходит в лог, а не наверх.
    """
    настройки = _backup_settings()
    частота = настройки.get("frequency_hours")
    if not частота:
        return
    dest = Path(настройки["folder"]) if настройки.get("folder") \
        else engine.default_backup_dir()
    try:
        # «Пора или нет» решает сама `backup.backup` через `every_hours` —
        # второй такой проверки здесь не нужно, иначе они однажды разойдутся.
        backup.backup(engine.VAULT, dest, keep=настройки.get("keep_count")
                      or backup.KEEP_DEFAULT, every_hours=частота)
    except (backup.BackupError, OSError) as e:
        print(f"[автобэкап не снялся: {e}]", file=sys.stderr)


class ЛенивыйЛог:
    """Файл, который открывается и получает заголовок только при первой записи.

    Планировщик дёргает задачу каждые пять минут, то есть 288 раз в сутки. Если
    писать штамп времени безусловно, за год набежит сто тысяч строк, в которых
    настоящая ошибка потеряется. Поэтому в тихие прогоны файл вообще не трогаем.
    """

    def __init__(self, path):
        self.path = path
        self.поток = None

    def write(self, text):
        if not text.strip() and self.поток is None:
            return len(text)
        if self.поток is None:
            self.поток = open(self.path, "a", encoding="utf-8", buffering=1)
            self.поток.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S} ---\n")
        return self.поток.write(text)

    def flush(self):
        if self.поток:
            self.поток.flush()


def main():
    ap = argparse.ArgumentParser(description="Показать подошедшие шаги")
    ap.add_argument("--channel", default="console", choices=list(notify.КАНАЛЫ),
                    help="чем показывать")
    ap.add_argument("--url", default="http://127.0.0.1:8765/",
                    help="куда ведёт клик по уведомлению")
    # default=None, а не константа: иначе аргумент всегда «задан», и отличить
    # «человек попросил 30» от «никто не просил» нельзя — настройка из файла
    # оказывалась перекрыта дефолтом ещё до того, как её прочитали.
    ap.add_argument("--repeat", type=int, default=None,
                    help="через сколько минут напоминать повторно "
                         "(по умолчанию — из настроек вольта)")
    ap.add_argument("--dry-run", action="store_true", help="ничего не показывать")
    ap.add_argument("--quiet-empty", action="store_true",
                    help="молчать, когда показывать нечего — для планировщика")
    ap.add_argument("--now", help="считать этот момент текущим: «2026-08-18 10:00». "
                                  "Для проверки — вне рабочих часов иначе ничего "
                                  "не показывается и непонятно, работает ли вообще")
    ap.add_argument("--log", help="дописывать вывод в файл. Планировщик запускает "
                                  "задачу через pythonw, чтобы не мигало окно "
                                  "консоли, — а вместе с окном пропадает и вывод. "
                                  "Без лога разбираться, почему нет уведомлений, "
                                  "будет не по чем")
    args = ap.parse_args()

    if args.log:
        sys.stdout = sys.stderr = ЛенивыйЛог(args.log)

    now = worktime.as_datetime(args.now) if args.now else datetime.now()
    today = now.date()

    # Через `engine._work`, а не `worktime.settings()`: та отдаёт зашитые
    # 09:00–21:00, и напоминания жили по ним, чей бы вольт ни обслуживали.
    # Заказчик ставил конец дня в 18:00, лента и завал его слушались (они
    # считаются движком), а тосты продолжали приходить до девяти вечера —
    # настройка была, кнопка была, эффекта не было.
    work = engine._work(None)

    # Статус устаревает от того, что прошёл день, а не от того, что кто-то трогал
    # задачу. Без пересчёта лента считалась бы по вчерашним данным.
    engine.cmd_refresh(SimpleNamespace(force=False), today)

    # Повторяющиеся шаблоны продвигаются здесь же, до ленты: свежесозданный цикл
    # должен попасть в то же самое уведомление, а не ждать следующего пробуждения.
    # Без --name это прогон всех правил разом, force всегда False — «всё равно
    # создать» решает человек по конкретному циклу, а не планировщик молча.
    engine.cmd_recur(SimpleNamespace(name=None, force=False, limit=None), today)
    автобэкап(today)

    лента = engine.cmd_feed(SimpleNamespace(), today)
    items = лента["feed"]

    notif = _notification_settings()
    state = notify.load_state(engine.VAULT)
    показать = notify.pick(items, state, now, work, повтор_минут(args, notif))

    if not показать:
        if not args.quiet_empty:
            вне = "" if worktime.is_working_moment(now, work) else " (сейчас нерабочее время)"
            print(f"показывать нечего{вне}; в ленте {len(items)}, "
                  f"просрочено {лента['overdue_count']}")
        return 0

    if args.dry_run:
        for i in показать:
            print(f"показал бы: {i['title']} — {i['task']} ({i['show_at']})")
        return 0

    if not notify.channel(args.channel).send(показать, url=args.url,
                                             sound=notif.get("sound", True)):
        print("канал не смог показать — отметку не ставим, повторим в следующий раз",
              file=sys.stderr)
        return 1

    for i in показать:
        notify.record(state, i, now)
    notify.save_state(engine.VAULT, notify.forget_closed(state, items))
    return 0


if __name__ == "__main__":
    sys.exit(main())
