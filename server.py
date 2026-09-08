#!/usr/bin/env python3
"""Запуск веб-интерфейса. Локальный сервер, никуда не смотрит наружу.

  python3 server.py            поднять на 127.0.0.1:8765 и открыть браузер
  python3 server.py --port 9000 --no-open

Само приложение — `api/app.py` (FastAPI): страницы из `static/`, прежние
маршруты `/api/*` для них и типизированный `/api/v1`. Этот файл только
разбирает аргументы, проверяет стор, открывает браузер и зовёт `uvicorn`.
Имя и флаги сохранены: на ноутбуке заказчика ярлык и README зовут
`python server.py`, и менять их ради переезда на FastAPI незачем.

Сервер ничего не пишет в стор сам: он зовёт ядро (`core/`) и движок. Правило
единственного писателя остаётся в силе (`CONTRACT.md`).

Слушает только петлевой адрес. Наружу этот сервис смотреть не должен: он пишет
в стор и не имеет никакой аутентификации.
"""
import argparse
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import engine  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Веб-интерфейс Yungdrung")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="не открывать браузер")
    args = ap.parse_args()

    if not engine.VAULT.is_dir():
        sys.exit(f"нет папки стора: {engine.VAULT}")

    try:
        import uvicorn
    except ImportError:
        sys.exit("нужны fastapi и uvicorn: pip install -r requirements.txt")

    адрес = f"http://127.0.0.1:{args.port}/"
    print(f"Веб-интерфейс: {адрес}")
    print(f"Стор: {engine.VAULT}")
    print("Остановить — Ctrl+C")
    if not args.no_open:
        threading.Timer(0.5, webbrowser.open, [адрес]).start()
    # Лог доступа выключен, как и раньше: строка на каждый запрос, включая опрос
    # разбора даты при каждом нажатии клавиши, — это шум, а не журнал.
    uvicorn.run("api.app:app", host="127.0.0.1", port=args.port,
                log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
