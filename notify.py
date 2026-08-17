#!/usr/bin/env python3
"""Доставка напоминаний: кого будить, когда повторить и чем показать.

Разделено надвое намеренно. Решение «кого и когда» — чистая логика над датами,
она здесь и покрыта тестами. Способ показа — сменный канал: системный тост,
телеграм, консоль. Ядро говорит, **что** доставить; канал решает, **как**.
Заблокируют телеграм — меняется одна строка настроек, а не логика.

Повтор устроен по разделу 8.4 ТЗ: показанное и не отвеченное возвращается через
15 минут, и так до ответа или до конца рабочего дня. После конца рабочего дня
элемент становится просроченным и уходит в завал — там его показывает разбор,
а не уведомления.

Факт ответа не отмечается отдельной командой, а выводится из состояния шага.
Любой ответ — сделано, не сделано, перенос, провал — меняет либо статус шага,
либо его дату контроля. Поэтому запись о доставке привязана к `control_at`:
дата поменялась, значит человек ответил, и это уже другое уведомление. Отдельный
`mark_answered` был бы четвёртым местом, где надо не забыть отметиться.
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import worktime

ПОВТОР_МИНУТ = 15

# Файл доставки лежит рядом с вольтом, но частью его не является: это служебные
# отметки «когда показывали», а не данные заказчика. Потеря файла означает лишний
# показ, а не потерю задачи, — поэтому в вольт его не мешаем.
def state_path(vault):
    return Path(vault) / ".доставка.json"


def load_state(vault):
    path = state_path(vault)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        # Битый файл доставки — не повод падать: хуже показать лишний раз, чем
        # не показать вовсе. Начинаем с чистого.
        return {}


def save_state(vault, state):
    path = state_path(vault)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def key(item):
    return f"{item['task']}::{item['step']}"


def should_send(item, state, now, work, repeat_minutes=ПОВТОР_МИНУТ):
    """Пора ли показывать этот элемент.

    Три причины не показывать: время показа ещё не наступило; уже показали меньше
    четверти часа назад; рабочий день кончился — дальше это забота завала.
    """
    показ = item.get("show_at")
    if not показ:
        return False
    if worktime.as_datetime(показ) > now:
        return False
    if now >= worktime.working_day_end(now, work):
        return False

    запись = state.get(key(item))
    if not запись:
        return True
    # Дата контроля изменилась — значит человек ответил, и это уже другое
    # уведомление, а не повтор старого.
    if запись.get("control_at") != item.get("control_at"):
        return True
    было = запись.get("last_sent")
    if not было:
        return True
    return now - worktime.as_datetime(было) >= timedelta(minutes=repeat_minutes)


def record(state, item, now):
    запись = state.setdefault(key(item), {})
    повтор = запись.get("control_at") == item.get("control_at")
    запись["control_at"] = item.get("control_at")
    запись["last_sent"] = now.isoformat(timespec="seconds")
    запись["count"] = (запись.get("count", 0) + 1) if повтор else 1
    return state


def forget_closed(state, живые):
    """Убрать отметки о шагах, которых больше нет в работе — иначе файл растёт
    вечно, а закрытая полгода назад задача занимает в нём место."""
    ключи = {key(i) for i in живые}
    return {k: v for k, v in state.items() if k in ключи}


def pick(items, state, now, work, repeat_minutes=ПОВТОР_МИНУТ):
    return [i for i in items if should_send(i, state, now, work, repeat_minutes)]


# --- каналы ----------------------------------------------------------------

def подпись(items):
    """Заголовок и текст уведомления. Один показ на все накопившиеся элементы:
    пять отдельных тостов подряд человек закроет не глядя."""
    if len(items) == 1:
        i = items[0]
        хвост = f" · переносов {i['postponed']}" if i.get("postponed") else ""
        return i["task"], f"{i['title']}{хвост}"
    строки = [f"• {i['title']} — {i['task']}" for i in items[:4]]
    if len(items) > 4:
        строки.append(f"…и ещё {len(items) - 4}")
    return f"Задач на сейчас: {len(items)}", "\n".join(строки)


class Console:
    """Работает везде и ничего не требует. На ней проверяется вся логика."""

    name = "console"

    def send(self, items, url=None):
        заголовок, текст = подпись(items)
        print(f"\n=== {заголовок} ===\n{текст}\n", flush=True)
        return True


class WindowsToast:
    """Системный тост Windows через PowerShell, без сторонних модулей.

    Не проверено на живой Windows — писалось на маке. Если не покажется,
    смотреть в первую очередь: включены ли уведомления для PowerShell в
    параметрах системы и не блокирует ли политика запуск скриптов.
    """

    name = "windows"

    PS = r'''
$ErrorActionPreference = "Stop"
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime] | Out-Null
$xml = @"
<toast activationType="protocol" launch="{URL}">
  <visual><binding template="ToastGeneric">
    <text>{HEAD}</text>
    <text>{BODY}</text>
  </binding></visual>
</toast>
"@
$doc = New-Object Windows.Data.Xml.Dom.XmlDocument
$doc.LoadXml($xml)
$toast = New-Object Windows.UI.Notifications.ToastNotification $doc
$appId = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)
'''

    @staticmethod
    def escape(s):
        """XML внутри PowerShell: название задачи вполне может содержать «&»."""
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                 .replace('"', "&quot;"))

    def send(self, items, url=None):
        заголовок, текст = подпись(items)
        скрипт = (self.PS
                  .replace("{HEAD}", self.escape(заголовок))
                  .replace("{BODY}", self.escape(текст))
                  .replace("{URL}", self.escape(url or "http://127.0.0.1:8765/")))
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-Command", скрипт],
                capture_output=True, text=True, encoding="utf-8", timeout=30)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            print(f"[тост не показан: {e}]", file=sys.stderr)
            return False
        if r.returncode != 0:
            print(f"[тост не показан: {r.stderr.strip()[:300]}]", file=sys.stderr)
            return False
        return True


class Telegram:
    """Заглушка. Канал заложен, чтобы переключение не требовало переделки —
    сам он появится вместе с ботом."""

    name = "telegram"

    def send(self, items, url=None):
        print("[канал telegram ещё не готов]", file=sys.stderr)
        return False


КАНАЛЫ = {c.name: c for c in (Console, WindowsToast, Telegram)}


def channel(name):
    if name not in КАНАЛЫ:
        raise ValueError(f"нет канала «{name}», есть: {', '.join(КАНАЛЫ)}")
    return КАНАЛЫ[name]()
