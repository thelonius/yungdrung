#!/usr/bin/env python3
"""Резервные копии вольта и экспорт всей базы в JSON. Требования R25 и R11.

Две разные задачи, которые легко смешать.

**Копия (R25)** — снимок вольта как он есть, чтобы вернуть данные после сбоя.
Один zip на копию: вольт состоит из десятков файлов, и копировать их по одному
означает получить папку, в которой половина файлов от вторника, а половина от
среды. Один файл ещё и ротируется просто — удалить нужно ровно одну запись
каталога, а не обойти дерево.

**Экспорт в JSON (R11)** — данные без нашего формата хранения, чтобы будущая
версия продукта прочитала их и не заставила заказчика заводить всё заново.
Excel-выгрузка (`engine.cmd_export`) решает третью задачу: посмотреть глазами и
переслать. Друг друга они не заменяют, поэтому и лежат отдельно.

**Два вида хранилища.** Сейчас вольт — папка с markdown, спринт 1 плана переводит
его в один файл SQLite. Модуль умеет оба и решает по тому, что лежит на диске:
файл, начинающийся словом `SQLite format 3` и нулевым байтом, копируется штатным
`Connection.backup()`, всё остальное читается как есть. Смотрим на заголовок, а
не на расширение и не на настройку: имя файла при переезде выберет человек, а
заголовок пишет сама SQLite, когда заводит файл.

Почему база отдельно: она живёт в одном файле, страницы правятся по месту, и
прочитанный на ходу файл окажется из середины чужой транзакции. Такая копия
открывается битой, и узнаётся это в тот единственный день, когда она нужна.

Копии и восстановление намеренно не зависят от pyyaml: разбор frontmatter нужен
только экспорту, и он импортирует `engine` внутри функции. Сломанный разбор не
должен лишать заказчика возможности снять копию — это последняя защита данных.

  import backup
  backup.backup(vault, "D:/Копии", keep=7, every_hours=24)   # по расписанию
  backup.backup("D:/Вольт/вольт.db", "D:/Копии", keep=7)     # после переезда
  backup.write_export(vault, "выгрузка.json")                # одной кнопкой
  backup.restore("D:/Копии/вольт-2026-08-18-143005.zip", vault)
"""
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path, PurePosixPath

# Имя копии: «вольт-2026-08-18-143005.zip». Метка времени в имени, а не только во
# времени файла: время файла теряется при копировании папки на флешку и при
# распаковке архива, а разбирать завал копий заказчику придётся именно глазами.
PREFIX = "вольт"
STAMP = "%Y-%m-%d-%H%M%S"

# Копия, которую снимают перед восстановлением, лежит в той же папке, но под
# другим префиксом. Иначе ротация обычных копий однажды удалила бы именно её —
# то есть единственный след того, что было до восстановления.
SAFETY_PREFIX = "перед-восстановлением"

MANIFEST = "бэкап.json"
FORMAT = "yungdrung-vault"
EXPORT_FORMAT = "yungdrung-export"
EXPORT_VERSION = 1

KEEP_DEFAULT = 7        # неделя ежедневных копий
EVERY_HOURS_DEFAULT = 24

# Мусор сборки и служебные папки в копию не идут: .git в вольте заказчика может
# весить больше самих данных, а __pycache__ восстанавливать незачем. Настройки
# Obsidian (.obsidian) наоборот берём — заказчик их настраивал руками, и после
# восстановления вольт должен открыться таким же, а не голым.
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".ipynb_checkpoints"}

# Файлы, которые движок создаёт на время записи (`engine.write_file`). Живут
# доли секунды и к моменту распаковки не значат ничего.
SKIP_SUFFIX = (".tmp",)

RESTORE_DIR = ".восстановление-"

# Первые 16 байт файла базы. Формат заголовка зафиксирован SQLite и не менялся
# с третьей версии, так что проверка переживёт и смену версии, и переименование
# файла при переезде.
SQLITE_MAGIC = b"SQLite format 3\x00"

# Спутники базы: журнал упреждающей записи, разделяемый индекс и откатный
# журнал. В архив не идут — снимок уже содержит всё, что в них накопилось.
SQLITE_SIDECARS = ("-wal", "-shm", "-journal")

# Поля frontmatter, которые движок пересчитывает при каждой записи из шагов
# (`engine.save`). В экспорт не идут: два источника одного статуса рано или
# поздно разойдутся, а восстановить их из шагов может кто угодно.
DERIVED = {"schema", "type", "status", "current_step", "control_date",
           "progress", "stalled"}


class BackupError(Exception):
    """Ошибка с указанием поля — по правилу контракта «ошибки структурные».

    Настройки бэкапа заказчик задаёт формой (папка, частота, сколько копий), и
    форме нужно знать, какое поле подсветить. Строка «что-то не так» не годится
    ни ей, ни боту.
    """

    def __init__(self, field, message, **details):
        super().__init__(message)
        self.field = field
        self.message = message
        # Подробности нужны там, где одного поля мало: оборвавшееся
        # восстановление обязано назвать файл страховочной копии, иначе человек
        # знает, что стало плохо, но не знает, куда возвращаться.
        self.details = details

    def as_json(self):
        return {"field": self.field, "error": self.message, **self.details}


# --- имена копий -----------------------------------------------------------

def archive_name(moment, prefix=PREFIX):
    return f"{prefix}-{moment.strftime(STAMP)}.zip"


def parse_name(name, prefix=PREFIX):
    """Время из имени копии; `None`, если имя не наше.

    Разбор нарочно строгий, до последнего знака. По его результату решается,
    можно ли файл удалить при ротации, поэтому «похоже на нашу копию» здесь не
    годится: в папке копий у заказчика вполне окажется его собственный архив.
    """
    m = re.fullmatch(re.escape(prefix) + r"-(\d{4}-\d{2}-\d{2}-\d{6})\.zip", name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), STAMP)
    except ValueError:
        return None  # «вольт-2026-13-45-999999.zip» — цифры на месте, даты нет


# --- база SQLite -----------------------------------------------------------

def is_sqlite(path):
    """База ли это SQLite. Решаем по заголовку файла, а не по расширению.

    Имя файла базы выберет человек при переезде, и `.db`, `.sqlite`, `вольт.дан`
    и вовсе без расширения — всё это встречается. Заголовок при этом остаётся на
    месте: его пишет сама SQLite, когда заводит файл.
    """
    try:
        with open(path, "rb") as f:
            return f.read(len(SQLITE_MAGIC)) == SQLITE_MAGIC
    except OSError:
        return False


def storage_kind(vault):
    """Чем вольт является прямо сейчас: `folder`, `sqlite`, `file` или `missing`.

    Переезд на базу (спринт 1 плана) меняет путь в настройках, а не эту функцию:
    она смотрит на диск. Папка остаётся `folder`, даже если база лежит внутри
    неё, — база в этом случае попадёт в копию снимком вместе с остальными
    файлами.
    """
    vault = Path(vault)
    if vault.is_dir():
        return "folder"
    if vault.is_file():
        return "sqlite" if is_sqlite(vault) else "file"
    return "missing"


def sqlite_files(files):
    """Разложить список `collect` на базы и их спутников.

    Спутники нужны, чтобы не класть в архив `-wal` и `-shm` рядом со снимком:
    снимок содержит всё, что было в журнале, а положенный поверх восстановленной
    базы старый `-wal` заставит SQLite докатить прежние страницы и отменить
    восстановление.
    """
    базы = {путь for путь, _ in files if is_sqlite(путь)}
    спутники = set()
    for путь, _ in files:
        for суффикс in SQLITE_SIDECARS:
            if len(путь.name) > len(суффикс) and путь.name.endswith(суффикс) \
                    and путь.with_name(путь.name[:-len(суффикс)]) in базы:
                спутники.add(путь)
    return базы, спутники


def connect_ro(path):
    """Подключение к базе только на чтение.

    Снятие копии не имеет права менять базу заказчика, поэтому сначала
    `mode=ro`. Если база в режиме WAL, а служебного `-shm` рядом нет, только на
    чтение SQLite её не откроет: ему негде разместить разделяемый индекс. Тогда
    открываем обычным подключением — `backup()` в источник всё равно не пишет, а
    остаться без копии из-за режима журнала хуже.
    """
    адрес = Path(path).resolve().as_uri() + "?mode=ro"
    conn = None
    try:
        conn = sqlite3.connect(адрес, uri=True, timeout=5)
        conn.execute("select count(*) from sqlite_master").fetchone()
        return conn
    except sqlite3.Error:
        if conn is not None:
            conn.close()
        return sqlite3.connect(str(path), timeout=5)


def check_sqlite(path):
    """`PRAGMA quick_check` над файлом: `ok` или первая жалоба SQLite.

    Быстрая проверка вместо полного `integrity_check`: та перечитывает каждый
    индекс, и для копии по расписанию это заметная работа на файле, который
    только что записан страница в страницу.
    """
    try:
        conn = sqlite3.connect(str(path), timeout=5)
        try:
            строки = conn.execute("PRAGMA quick_check").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as e:
        return " ".join(str(e).split())[:200]
    ответ = [str(r[0]) for r in строки]
    return "ok" if ответ == ["ok"] else "; ".join(ответ or ["пусто"])[:200]


SNAPSHOT_TIMEOUT = 20  # секунд


def snapshot(src, dst, timeout=SNAPSHOT_TIMEOUT):
    """Согласованный снимок базы в файл `dst`. Возвращает `dst`.

    `Connection.backup()` копирует страницы под замком чтения и начинает заново,
    если источник изменился по ходу. На выходе состояние базы на какой-то один
    момент, даже если в неё писали всё время копирования.

    `VACUUM INTO` дал бы то же самое, но требует, чтобы файла назначения ещё не
    было, и пересобирает базу целиком. Для снимка по расписанию это лишняя
    работа, а место под временный файл `backup()` позволяет занять заранее.

    Снимок проверяется `quick_check` до того, как попадёт в архив. Битая копия,
    про которую известно, что она битая, — повод остановиться, пока оригинал ещё
    цел. Молча положить её в архив значит узнать всё то же самое через неделю,
    когда возвращаться будет уже некуда.

    У `Connection.backup()` нет предела ожидания: если источник держат под
    эксклюзивной блокировкой (антивирус, зависший процесс, другая копия), вызов
    спит и повторяет попытку бесконечно, не возвращая управление. Копия по
    расписанию из Планировщика при этом не падает и не создаётся — она просто
    исчезает, и заказчик продолжает считать себя защищённым. Поэтому копирование
    идёт в отдельном потоке с ограничением по времени: не дождались — явная
    ошибка вместо тишины. Поток при этом может остаться висеть на месте, но
    вызывающий код об этом уже не ждёт и может пробовать снова на следующем
    прогоне планировщика.
    """
    src, dst = Path(src), Path(dst)
    ошибка = []

    def копировать():
        # Оба соединения — и на чтение, и на запись — открываются внутри
        # потока: объекты sqlite3 нельзя передавать между потоками, только
        # пересоздавать на месте.
        источник = connect_ro(src)
        try:
            приёмник = sqlite3.connect(str(dst), timeout=5)
            try:
                источник.backup(приёмник)
            finally:
                приёмник.close()
        except BaseException as e:
            ошибка.append(e)
        finally:
            источник.close()

    поток = threading.Thread(target=копировать, daemon=True)
    поток.start()
    поток.join(timeout=timeout)
    if поток.is_alive():
        raise BackupError(
            "vault",
            f"база {src.name} под блокировкой дольше {timeout} с — копия не снята")
    if ошибка:
        raise ошибка[0]

    жалоба = check_sqlite(dst)
    if жалоба != "ok":
        raise BackupError("vault",
                          f"снимок базы {src.name} не прошёл проверку: {жалоба}")
    return dst


def drop_sidecars(db):
    """Убрать `-wal`, `-shm` и `-journal` рядом с базой. Возвращает убранное.

    Зовётся сразу после того, как база встала на место: журналы остались от
    прежней базы, а SQLite при открытии считает их своими и накатывает — то есть
    отменяет восстановление ровно тем способом, ради защиты от которого журналы
    и убираются. Снимок в архиве содержит всё, что в них накопилось, поэтому
    здесь не теряется ничего.

    Файл, который не удалился, останавливает восстановление ошибкой. Молчать
    здесь нельзя: сам по себе он не исчезнет, а первое же открытие базы испортит
    только что восстановленные данные. Держит его обычно открытый трекер.
    """
    убрано = []
    for суффикс in SQLITE_SIDECARS:
        рядом = db.with_name(db.name + суффикс)
        if not рядом.is_file():
            continue
        try:
            # С повторами: занятый антивирусом журнал через полсекунды обычно
            # отпускает, а ошибка здесь останавливает всё восстановление.
            retrying(рядом.unlink)
        except OSError as e:
            raise BackupError(
                "vault",
                f"рядом с базой остался журнал {рядом.name} "
                f"({' '.join(str(e).split())[:120]}): закройте трекер и "
                f"повторите восстановление",
                dropped=убрано)
        убрано.append(рядом.name)
    return убрано


# --- копия -----------------------------------------------------------------

def retrying(действие):
    """Повторить операцию над файлом, если её отбило PermissionError.

    Причина та же, что у повторов в `engine.write_file`: на Windows файл на
    доли секунды держит антивирус или индексатор OneDrive, и подстановка или
    удаление падают на ровном месте. Ждать четыре раза с нарастающей паузой
    дешевле, чем потерять копию или бросить восстановление на середине.

    Своё, а не импорт из движка: копии и восстановление работают, даже когда
    разбор frontmatter сломан и `engine` не импортируется вовсе.
    """
    for попытка in range(5):
        try:
            return действие()
        except PermissionError:
            if попытка == 4:
                raise
            time.sleep(0.2 * (попытка + 1))


def replace_file(tmp, target):
    """Атомарная подстановка с повторами.

    Отдельной функцией, потому что здесь это ещё и точка, в которой копия
    становится видимой под своим именем: до `os.replace` файл лежит под
    временным, и оборванная копия не получит имени целой.
    """
    retrying(lambda: os.replace(tmp, target))


def collect(vault, skip_dirs=()):
    """Файлы вольта: пары «путь, имя внутри архива». Имена в posix-виде.

    `skip_dirs` нужен для одного важного случая: папку копий заказчик вполне
    может указать внутри вольта. Тогда каждая следующая копия включала бы в себя
    все предыдущие, и через неделю архив весил бы гигабайты.
    """
    vault = Path(vault)
    исключить = set()
    for d in skip_dirs:
        try:
            исключить.add(Path(d).resolve())
        except OSError:
            pass
    files = []
    for root, dirs, names in os.walk(vault):
        корень = Path(root)
        dirs[:] = sorted(d for d in dirs
                         if d not in SKIP_DIRS
                         and not d.startswith(RESTORE_DIR)
                         and (корень / d).resolve() not in исключить)
        for name in sorted(names):
            if name.endswith(SKIP_SUFFIX):
                continue
            путь = корень / name
            files.append((путь, путь.relative_to(vault).as_posix()))
    return files


def zip_entry(name, mtime):
    """Запись архива с сохранённым временем файла.

    Zip держит дату в двух байтах и умеет годы с 1980 по 2107. Дата вне этих
    границ в вольте означает сбитые часы или кривой перенос файлов, и ронять
    из-за неё всю копию нельзя. Год вперёд опаснее года назад: `ZipInfo` с ним
    падает не нашей ошибкой, а `struct.error` из глубины `zipfile`, мимо
    `BackupError` и без поля для формы. Поэтому границы обе.

    `fromtimestamp` на явно битой метке (счётчик секунд, записанный как
    миллисекунды) поднимает `OverflowError` или `OSError` ещё до сравнения —
    такую дату тоже заменяем, а не роняем копию.
    """
    try:
        момент = datetime.fromtimestamp(mtime)
    except (OverflowError, OSError, ValueError):
        момент = datetime(1980, 1, 1)
    if момент.year < 1980:
        момент = datetime(1980, 1, 1)
    elif момент.year > 2107:
        момент = datetime(2107, 12, 31, 23, 59, 58)
    info = zipfile.ZipInfo(name, date_time=момент.timetuple()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def add_snapshot(z, src, name, mtime, workdir, raw_on_corrupt=False):
    """Положить в архив согласованный снимок базы `src` под именем `name`.

    Снимок делается во временный файл рядом с архивом и оттуда переливается в
    архив потоком. Держать базу в памяти целиком незачем: она растёт вместе с
    архивом закрытых задач (R29 — их пять тысяч), а поток обходится одним
    буфером независимо от размера.

    `raw_on_corrupt` предназначен только для страховочной копии перед
    восстановлением (см. `restore`). Обычная копия по расписанию на битой базе
    обязана падать — иначе заказчик получит архив без данных и решит, что всё
    сохранено. Но перед восстановлением цель другая: не потерять то, что было,
    а не подтвердить, что оно исправно. Возвращает True, если снимок пришлось
    класть сырыми байтами без проверки.
    """
    fd, tmp = tempfile.mkstemp(dir=str(workdir), suffix=".db.tmp")
    os.close(fd)
    tmp = Path(tmp)
    try:
        try:
            snapshot(src, tmp)
        except BackupError:
            if not raw_on_corrupt:
                raise
            with open(src, "rb") as f, z.open(zip_entry(name, mtime), "w") as вход:
                shutil.copyfileobj(f, вход)
            return True
        with open(tmp, "rb") as f, z.open(zip_entry(name, mtime), "w") as вход:
            shutil.copyfileobj(f, вход)
        return False
    finally:
        # Приёмник снимка мог оставить рядом свои служебные файлы — уносим их
        # вместе с временным, иначе папка копий обрастает мусором.
        for хвост in ("",) + SQLITE_SIDECARS:
            tmp.with_name(tmp.name + хвост).unlink(missing_ok=True)


def create(vault, dest, now=None, prefix=PREFIX, raw_on_corrupt=False):
    """Копия вольта в папку `dest`. Возвращает описание созданной копии.

    Вольтом может быть папка с файлами (сейчас) или один файл базы SQLite (после
    спринта 1). Что там на самом деле, функция смотрит сама — см. `storage_kind`.

    Атомарность трёхслойная.

    Снаружи: архив пишется во временный файл в той же папке и переименовывается
    в последний момент. Прерванная на середине копия не получит имени, по
    которому её потом примут за целую, — а с zip это особенно важно, потому что
    оглавление лежит в конце файла и недописанный архив не открывается вовсе.

    Обычный файл: читается одним `read_bytes`. Движок пишет через `os.replace`,
    то есть подменяет файл целиком, и прочитать половину старого с половиной
    нового нельзя. Разные файлы при этом могут оказаться из разных моментов —
    но движок за одну операцию трогает одну задачу, так что в архиве разойдутся
    независимые задачи, а не половинки одной.

    База SQLite: снимается через `snapshot`. `read_bytes` здесь не годится
    принципиально — база правится по месту, и прочитанный на ходу файл будет
    собран из страниц разных транзакций.
    """
    vault = Path(vault)
    вид = storage_kind(vault)
    if вид == "missing":
        raise BackupError("vault", f"нет вольта по пути: {vault}")
    dest = Path(dest)
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise BackupError("dest", f"не удалось создать папку копий: {e}")

    if вид == "folder":
        files = collect(vault, skip_dirs=[dest])
        if not files:
            # Пустой вольт означает, что путь указан не туда: диск не подключён,
            # папку унесло облако. Сделать такую копию и запустить ротацию —
            # значит за N дней вытеснить все настоящие копии пустыми.
            raise BackupError("vault", f"в папке {vault} нет файлов — копию не делаем")
    else:
        files = [(vault, vault.name)]
    базы, спутники = sqlite_files(files)

    момент = now or datetime.now()
    target = dest / archive_name(момент, prefix)
    while target.exists():
        # Две копии в одну секунду: заказчик дважды нажал «сделать сейчас».
        # Сдвигаем метку, а не пишем поверх — иначе прошлая копия исчезнет.
        момент += timedelta(seconds=1)
        target = dest / archive_name(момент, prefix)

    пропущено, снимки, сырые, записано = [], [], [], 0
    fd, tmp = tempfile.mkstemp(dir=str(dest), suffix=".tmp")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for путь, имя in files:
                if путь in спутники:
                    continue
                try:
                    st = путь.stat()
                    if путь in базы:
                        если_сыро = add_snapshot(z, путь, имя, st.st_mtime, dest,
                                                 raw_on_corrupt=raw_on_corrupt)
                        снимки.append(имя)
                        if если_сыро:
                            сырые.append(имя)
                    else:
                        z.writestr(zip_entry(имя, st.st_mtime), путь.read_bytes())
                except (OSError, sqlite3.Error) as e:
                    # Файл исчез или занят между обходом папки и чтением. Копия
                    # без одного файла лучше, чем упавшая по расписанию задача,
                    # про которую никто не узнает: список пропущенных уходит в
                    # ответ и в манифест. Битый снимок базы сюда не попадает —
                    # `snapshot` бросает `BackupError`, и копия не создаётся.
                    пропущено.append({"file": имя, "error": " ".join(str(e).split())[:200]})
                    continue
                записано += 1
            if not записано:
                # Ни одного файла прочитать не удалось: битая база (заголовок на
                # месте, страницы — нет), заблокированный вольт, отвалившийся
                # диск. Такая копия успешна на вид, а ротация после неё удаляет
                # настоящие — за `keep` запусков от них не остаётся ничего.
                raise BackupError("vault",
                                  f"ни один файл вольта {vault.name} не прочитался"
                                  f" — копию не делаем", skipped=пропущено)
            манифест = {
                "format": FORMAT,
                "version": 1,
                "created": момент.isoformat(timespec="seconds"),
                "vault": vault.name,
                "storage": вид,
                "databases": снимки,
                "raw_databases": сырые,
                "files": записано,
                "skipped": пропущено,
            }
            z.writestr(MANIFEST, json.dumps(манифест, ensure_ascii=False, indent=1))
        replace_file(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

    return {"file": str(target), "name": target.name,
            "created": манифест["created"], "files": манифест["files"],
            "storage": вид, "databases": снимки, "raw_databases": сырые,
            "skipped": пропущено, "bytes": target.stat().st_size}


# --- ротация ---------------------------------------------------------------

def rotate(dest, keep, prefix=PREFIX, protect=()):
    """Оставить `keep` последних копий, остальные удалить. Возвращает удалённые.

    Это единственное место во всём трекере, которое удаляет файлы у заказчика,
    поэтому ограничений здесь больше, чем логики:

    - удаляются только файлы, чьё имя разобралось как наша копия целиком;
      чужой архив, папка и наш же файл под другим префиксом не трогаются;
    - ссылки не удаляются: мы их не создаём, значит ссылка под нашим именем
      сделана человеком и ведёт в его собственный файл;
    - файл, который не удалился (антивирус держит, папка только для чтения),
      пропускается молча и в список удалённых не попадает: отчёт о ротации
      должен совпадать с тем, что на диске, иначе ошибку прав никто не заметит;
    - `keep` меньше единицы отвергается ошибкой; ноль в поле настроек чаще
      означает опечатку, чем желание стереть все копии;
    - в папку не заходим вглубь, подпапки не обходим;
    - порядок берётся из метки времени в имени, а не из времени файла: время
      файла меняется от копирования папки, и «самой старой» после переноса на
      другой диск оказалась бы случайная копия;
    - имена из `protect` не удаляются, даже если по метке они самые старые.
      Часы на ноутбуке уезжают назад — севшая батарейка BIOS, переустановка
      системы, поправленный часовой пояс, — и тогда только что снятая копия
      получает метку старше всех лежащих в папке. Без этой оговорки ротация
      съедала бы её сразу после создания.

    Вызывать после успешного создания новой копии, а не до: тогда в худшем
    случае копий останется больше нужного, а не меньше.
    """
    if isinstance(keep, bool) or not isinstance(keep, int):
        raise BackupError("keep", "сколько копий хранить — целое число")
    if keep < 1:
        raise BackupError("keep", "хранить нужно хотя бы одну копию")

    dest = Path(dest)
    if not dest.is_dir():
        raise BackupError("dest", f"нет папки копий: {dest}")

    наши = []
    for путь in dest.iterdir():
        if путь.is_symlink() or not путь.is_file():
            continue
        момент = parse_name(путь.name, prefix)
        if момент is None:
            continue
        наши.append((момент, путь.name, путь))
    наши.sort(key=lambda x: (x[0], x[1]))

    # Имя, а не путь: вызывающий передаёт то, что вернул `create`, и передать
    # он может как имя, так и полный путь к файлу.
    беречь = {Path(имя).name for имя in protect}
    удалены = []
    for _, имя, путь in наши[:-keep]:
        if имя in беречь:
            continue
        try:
            путь.unlink()
        except OSError:
            continue  # занят антивирусом — уйдёт при следующей ротации
        удалены.append(имя)
    return удалены


def copies(dest, prefix=PREFIX):
    """Список копий для интерфейса восстановления, новые сверху."""
    dest = Path(dest)
    if not dest.is_dir():
        return []
    найдено = []
    for путь in dest.iterdir():
        момент = parse_name(путь.name, prefix) if путь.is_file() else None
        if момент is None:
            continue
        найдено.append({"name": путь.name, "file": str(путь),
                        "created": момент.isoformat(timespec="seconds"),
                        "bytes": путь.stat().st_size})
    найдено.sort(key=lambda c: c["created"], reverse=True)
    return найдено


def due(dest, every_hours=EVERY_HOURS_DEFAULT, now=None, prefix=PREFIX):
    """Пора ли снимать очередную копию.

    Расписание держится на возрасте последней копии, а не на «запускались ли мы
    сегодня»: ноутбук заказчика выключен половину суток, и задача планировщика,
    пропустившая свой час, иначе не сработала бы до следующих суток.

    Копии с меткой из будущего в расчёт не идут. Часы на ноутбуке уезжают в обе
    стороны (та же севшая батарейка BIOS, что и в оговорке `protect` у
    `rotate`), и копия, снятая при сбитых часах, получает дату вперёд на месяцы.
    По ней «прошло меньше суток» будет верно до самой этой даты — то есть копии
    молча перестанут делаться ровно тогда, когда с машиной уже что-то не так.
    """
    сейчас = now or datetime.now()
    прошлые = [c for c in copies(dest, prefix)
               if datetime.fromisoformat(c["created"]) <= сейчас]
    if not прошлые:
        return True
    свежая = datetime.fromisoformat(прошлые[0]["created"])
    return сейчас - свежая >= timedelta(hours=every_hours)


def backup(vault, dest, keep=KEEP_DEFAULT, every_hours=None, now=None,
           force=False, prefix=PREFIX):
    """Копия по расписанию: снять, если пора, и подчистить старые.

    Само расписание живёт снаружи (Планировщик Windows будит трекер, как и для
    напоминаний). Здесь только решение «пора или нет» — тогда пропущенный из-за
    выключенного ноутбука срок догоняется при первом же запуске.

    Без `every_hours` копия делается всегда: это кнопка «сделать сейчас».
    """
    if every_hours and not force and not due(dest, every_hours, now, prefix):
        return {"file": None, "reason": "рано: копия за этот период уже есть",
                "rotated": []}
    итог = create(vault, dest, now=now, prefix=prefix)
    итог["rotated"] = rotate(dest, keep, prefix=prefix, protect=[итог["name"]])
    return итог


# --- экспорт в JSON --------------------------------------------------------

def jsonable(value):
    """YAML отдаёт даты объектами `date`, JSON их не умеет — переводим в ISO.

    Ключи словарей приводим к строке заранее. `json.dumps` сделал бы это сам, и
    тогда разобранный обратно экспорт перестал бы совпадать с тем, что отдали:
    целочисленный ключ ушёл бы числом, а вернулся строкой.
    """
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def tags_of(meta):
    tags = meta.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]  # один тег строкой — обычная запись в Obsidian
    return [str(t) for t in tags]


def strip_steps_block(body, start, end):
    """Убрать из тела блок шагов, который туда рисует движок.

    Блок целиком считается из `steps`, и в экспорте он был бы вторым описанием
    тех же данных — тем самым, которое разойдётся первым.
    """
    a = body.find(start)
    b = body.find(end)
    if a != -1 and b != -1 and b > a:
        body = body[:a] + body[b + len(end):]
    return body.strip()


def templates_of(vault, broken):
    """Шаблоны задач из файла шаблонов. Требование R11.

    Шаблоны — данные заказчика: он сам заводит цепочки шагов со сдвигами дат, и
    для повторяющейся работы это половина всего, что он в трекер вложил.
    Выгрузка без них заставила бы заводить их заново, то есть перестала бы быть
    тем, ради чего R11 существует.

    Файл читается здесь, а не через `templates.JsonStore`: тот на испорченном
    файле поднимает исключение, и правильно делает — иначе первое же сохранение
    затрёт шаблоны пустым списком. Но выгрузку задач испорченный файл шаблонов
    ронять не должен, поэтому он попадает в `broken` рядом со сломанными
    задачами. Путь берётся у модуля шаблонов: имя файла знает он.
    """
    import templates

    путь = templates.templates_path(vault)
    if not путь.is_file():
        return []
    try:
        данные = json.loads(путь.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError) as e:
        broken.append({"file": путь.name, "error": " ".join(str(e).split())[:200]})
        return []
    список = данные.get("templates") if isinstance(данные, dict) else None
    if not isinstance(список, list):
        broken.append({"file": путь.name, "error": "файл шаблонов не в том формате"})
        return []
    return jsonable(список)


def export(vault, now=None):
    """Вся база одной структурой, готовой к `json.dumps`. Требование R11.

    Формат самодостаточный: по нему вольт собирается заново без нашего кода.
    Задача — это файл с шагами; шаг несёт свой журнал; заметка базы знаний —
    файл с текстом. Пересчитываемые поля (статус, текущий шаг, прогресс) не
    экспортируются: они выводятся из шагов, и хранить их отдельно значит завести
    второй источник правды. Всё, что заказчик дописал во frontmatter сам,
    сохраняется в `extra` — при переносе в новую версию терять это нельзя.

    Пример структуры:

        {
          "format": "yungdrung-export",
          "version": 1,
          "schema": 1,
          "exported": "2026-08-18T14:30:05",
          "vault": "Вольт",
          "tasks": [
            {
              "file": "Задачи/Заявка на грант ФПГ.md",
              "name": "Заявка на грант ФПГ",
              "title": "Заявка на грант ФПГ",
              "created": "2026-08-05",
              "tags": ["гранты"],
              "body": "текст задачи, который писал заказчик",
              "extra": {"срок_по_договору": "2026-09-01"},
              "steps": [
                {
                  "id": 1,
                  "title": "Собрать пакет документов",
                  "status": "done",
                  "control_date": "2026-08-08",
                  "completed_date": "2026-08-07",
                  "log": [{"date": "2026-08-07", "event": "done"}]
                },
                {
                  "id": 2,
                  "title": "Отправить на согласование",
                  "status": "pending",
                  "control_date": "2026-08-15",
                  "completed_date": null,
                  "log": [{"date": "2026-08-13", "event": "defer",
                           "was": "2026-08-13", "to": "2026-08-15",
                           "reason": "Говнов в отъезде"}]
                }
              ]
            }
          ],
          "notes": [
            {"file": "База/6805.md", "name": "6805", "title": "6805",
             "tags": ["запчасти"], "body": "Типоразмер подшипника.", "extra": {}}
          ],
          "templates": [
            {"schema": 1, "name": "Продление сертификата", "tags": ["инфраструктура"],
             "body": "",
             "steps": [{"position": 1, "title": "Оплатить счёт",
                        "offset_days": 0, "time_of_day": "10:00"}]}
          ],
          "broken": [{"file": "Задачи/сломанная.md", "error": "..."}]
        }

    Значения полей: `status` шага — `pending` · `done` · `skipped` · `failed`;
    `event` в журнале — `done` · `not_done` · `defer` · `skipped` · `failed`;
    даты в ISO, `null` означает «дата не назначена». Шаги идут в том порядке, в
    каком выполняются, и следующий начинается после закрытия предыдущего.

    Чтобы собрать вольт заново по этому файлу, чужой программе хватает
    перечисленного: `file` даёт путь, `title`, `created`, `tags` и `extra` —
    frontmatter, `body` — тело записи, `steps` — шаги с журналом. Пересчитываемые
    поля она получит из шагов, как получает их движок.

    Три раздела, а не один: `tasks` с шагами и журналом переносов внутри каждого
    шага, `notes` — записи базы знаний, `templates` — заготовки задач из файла
    шаблонов. Журнал живёт в шаге (`log`), а не отдельным списком, потому что там
    же он и хранится; вынести его наружу значило бы связывать записи по
    идентификаторам, которые вне вольта ничего не значат.

    Чего в выгрузке нет намеренно: вложений и любых нетекстовых файлов вольта.
    Их место — в zip-копии (`create`), а не в JSON, где они раздули бы файл
    base64-мусором. Нет и служебного состояния доставки (`.доставка.json`,
    `notify`): оно описывает, какое уведомление уже показали, и после переноса
    в новую версию не значит ничего. Файлы, которые не разобрались, перечислены
    в `broken` — молча потерянная задача хуже видимой ошибки.
    """
    # Импорт внутри функции: разбор frontmatter требует pyyaml, а копии и
    # восстановление должны работать и без него.
    import engine

    vault = Path(vault)
    if not vault.is_dir():
        # Отдельная ветка для базы: после переезда путь вольта станет файлом, и
        # сообщение «нет папки вольта» отправило бы человека искать пропавшую
        # папку вместо того, чтобы сказать правду — читать базу движок пока не
        # умеет, а копии (`create`) с ней уже работают.
        if storage_kind(vault) == "sqlite":
            raise BackupError("vault", f"экспорт из базы {vault.name} ещё не написан: "
                                       "движок читает markdown")
        raise BackupError("vault", f"нет папки вольта: {vault}")

    tasks, notes, broken = [], [], []
    for путь, имя in collect(vault):
        if путь.suffix.lower() != ".md":
            continue
        try:
            raw = путь.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            broken.append({"file": имя, "error": " ".join(str(e).split())[:200]})
            continue
        if not raw.startswith("---"):
            continue  # обычный markdown рядом с вольтом, не запись трекера
        try:
            meta, body = engine.parse_file(путь)
        except Exception as e:
            # Молчаливая потеря опаснее падения: сломанный файл попадает в ответ,
            # чтобы человек увидел, что задача не уехала в новую версию.
            broken.append({"file": имя, "error": " ".join(str(e).split())[:200]})
            continue

        тип = meta.get("type")
        if тип not in ("task", "note"):
            continue
        # Пересчитываемые поля выкидываем только у задач: у заметки базы знаний
        # движок ничего не считает, и `status: черновик`, дописанный руками,
        # там данные заказчика, а не наша сводка.
        свои = {"title", "tags", "steps", "schema", "type"}
        if тип == "task":
            свои = свои | DERIVED | {"created"}
        # Порядок ключей задаём явно: файл экспорта открывают глазами, и читать
        # его удобнее сверху вниз — что за запись, чем названа, что внутри.
        #
        # Название приводим к строке: YAML читает `title: 6805` (это заметка про
        # типоразмер подшипника из примеров) как число, и на ту сторону оно
        # уехало бы числом в поле, где везде текст.
        запись = {"file": имя, "name": путь.stem,
                  "title": str(meta.get("title") or путь.stem)}
        if тип == "task":
            запись["created"] = jsonable(meta.get("created"))
        запись["tags"] = tags_of(meta)
        запись["body"] = (strip_steps_block(body, engine.STEPS_START, engine.STEPS_END)
                          if тип == "task" else body.strip())
        запись["extra"] = jsonable({k: v for k, v in meta.items() if k not in свои})
        if тип == "task":
            шаги = meta.get("steps") or []
            # Вольт правится руками, и `steps: перенести` вместо списка там
            # достижимо (ровно поэтому хранилище и переезжает в базу — решение в
            # CLAUDE.md). Без проверки строка разошлась бы по буквам, и на той
            # стороне у задачи оказалось бы девять шагов из одной опечатки.
            if not isinstance(шаги, list) or not all(isinstance(s, dict) for s in шаги):
                broken.append({"file": имя, "error": "поле steps — не список шагов"})
                continue
            запись["steps"] = [jsonable(s) for s in шаги]
            tasks.append(запись)
        else:
            notes.append(запись)

    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "schema": engine.SCHEMA,
        "exported": (now or datetime.now()).isoformat(timespec="seconds"),
        "vault": vault.name,
        "tasks": tasks,
        "notes": notes,
        "templates": templates_of(vault, broken),
        "broken": broken,
    }


def write_export(vault, out, now=None):
    """Экспорт в файл. UTF-8 явно и `ensure_ascii=False`: файл читает человек и
    чужая программа, а `\\u0417` не читает никто."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    данные = export(vault, now=now)
    fd, tmp = tempfile.mkstemp(dir=str(out.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(данные, f, ensure_ascii=False, indent=1)
        replace_file(tmp, out)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return {"file": str(out), "tasks": len(данные["tasks"]),
            "notes": len(данные["notes"]),
            "templates": len(данные["templates"]), "broken": данные["broken"]}


# --- восстановление --------------------------------------------------------

def safe_names(z):
    """Имена файлов из архива с проверкой, что ни одно не ведёт наружу.

    Распаковка чужого архива — классическая дыра: запись с именем `../../…`
    кладёт файл куда угодно на диске. Архив к нам приходит с диска заказчика, и
    испортить его может как злой умысел, так и просто кривой архиватор. Поэтому
    подозрительное имя останавливает восстановление целиком, а не молча
    исправляется: раз в архиве такое имя, доверия к остальному тоже нет.
    """
    имена = []
    for info in z.infolist():
        if info.is_dir():
            continue
        имя = info.filename
        if имя == MANIFEST:
            continue
        if "\\" in имя or ":" in имя or имя.startswith("/"):
            raise BackupError("archive", f"недопустимое имя в архиве: {имя}")
        части = PurePosixPath(имя).parts
        if ".." in части or not части:
            raise BackupError("archive", f"путь наружу папки в архиве: {имя}")
        имена.append(имя)
    if not имена:
        raise BackupError("archive", "в архиве нет файлов вольта")
    return имена


def manifest_of(z):
    """Манифест из архива; пустой словарь, если его нет или он не читается.

    Отсутствие манифеста не повод отказать в восстановлении: архив мог собрать
    руками архиватор заказчика. Поэтому здесь нет исключений — то, что удалось
    прочитать, читается, остальное считается неизвестным.
    """
    if MANIFEST not in z.namelist():
        return {}
    try:
        данные = json.loads(z.read(MANIFEST).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, OSError, zipfile.BadZipFile):
        return {}
    return данные if isinstance(данные, dict) else {}


def describe(archive):
    """Что это за копия — для списка в интерфейсе. Манифест необязателен:
    восстановить можно и архив, собранный руками."""
    archive = Path(archive)
    if not archive.is_file():
        raise BackupError("archive", f"нет файла копии: {archive}")
    try:
        with zipfile.ZipFile(archive) as z:
            имена = safe_names(z)
            манифест = manifest_of(z)
    except zipfile.BadZipFile:
        raise BackupError("archive", f"файл не открывается как архив: {archive.name}")
    return {"file": str(archive), "name": archive.name,
            "bytes": archive.stat().st_size, "files": len(имена),
            "created": манифест.get("created"),
            # Вид хранилища берётся из манифеста: по нему интерфейс скажет,
            # куда копия разворачивается — в папку вольта или в файл базы.
            # Собранный руками архив манифеста не имеет, и тогда это `None`.
            "storage": манифест.get("storage"),
            "databases": манифест.get("databases") or [],
            "ours": манифест.get("format") == FORMAT}


def target_entry(имена, vault):
    """Какая запись архива ложится в файл-вольт.

    Совпадение по имени — обычный случай. Единственная запись подходит и тогда,
    когда имя другое: базу переименовали после того, как копия была снята, и
    отказать здесь значит оставить человека с целой копией, которую некуда
    развернуть.
    """
    if vault.name in имена:
        return vault.name
    if len(имена) == 1:
        return имена[0]
    raise BackupError("archive",
                      f"в копии {len(имена)} файлов, а вольт — один файл "
                      f"{vault.name}: укажите папку вольта")


def restore(archive, vault, safety_dir=None, now=None):
    """Развернуть копию поверх вольта. Возвращает, что восстановлено.

    Первое, что делает восстановление, — снимает копию того, что собирается
    затереть. Человек нажимает «восстановить» в панике и часто по ошибке; без
    этой копии откатить ошибочный откат нечем. Копия кладётся под префиксом
    `перед-восстановлением`, поэтому обычная ротация её не удалит.

    Вольт, как и при снятии копии, бывает папкой и одним файлом базы. Для файла
    берётся подходящая запись архива (`target_entry`), для папки — все записи.

    Файлы, которые есть в вольте, но которых нет в архиве, не удаляются, а
    перечисляются в ответе полем `extra`. Они появились после копии — например,
    задача, заведённая сегодня утром. Лишний файл человек удалит сам, а
    удалённый нами он не вернёт ниоткуда.

    Каждый файл встаёт на место через `os.replace`, то есть по одному и целиком:
    прерванное на середине восстановление оставит часть файлов старыми, а не
    обрубленными. Если оно всё-таки оборвалось, ошибка несёт путь страховочной
    копии в поле `safety_copy`: вольт в этот момент наполовину старый, и человек
    должен знать, откуда взять целое состояние, а не догадываться.

    Рядом с восстановленной базой убираются `-wal` и `-shm`: журналы прежней
    базы SQLite при открытии считает своими и накатывает поверх положенных
    страниц. Убрать их не вышло — восстановление останавливается ошибкой, а не
    отчитывается об успехе (см. `drop_sidecars`).
    """
    archive = Path(archive)
    vault = Path(vault)
    if not archive.is_file():
        raise BackupError("archive", f"нет файла копии: {archive}")
    вид = storage_kind(vault)

    try:
        z = zipfile.ZipFile(archive)
    except zipfile.BadZipFile:
        raise BackupError("archive", f"файл не открывается как архив: {archive.name}")

    подстраховка = None
    восстановлено, убрано = [], []
    with z:
        # Проверка имён до страховочной копии: испорченный архив не должен
        # оставлять после себя ни следа работы.
        имена = safe_names(z)
        из_копии = manifest_of(z).get("storage")
        если_файл = вид in ("sqlite", "file")
        if вид == "missing":
            # Пути нет вовсе: новая машина или потерянный вольт. Чем он был,
            # знает манифест копии; у собранного руками архива вместо манифеста
            # остаётся единственная запись — она отвечает на тот же вопрос.
            # Папку молча не создаём: путь с опечаткой выглядел бы как успешное
            # восстановление в пустоту.
            if vault.parent.is_dir() and (из_копии in ("sqlite", "file")
                                          or (из_копии is None and len(имена) == 1)):
                если_файл = True
            else:
                raise BackupError("vault", f"нет вольта по пути: {vault}")
        # Копию папки и копию базы человек выбирает в форме сам, и перепутать их
        # нетрудно. Копия папки с единственной заметкой иначе легла бы поверх
        # файла базы — вольт заменён заметкой, и ни одной ошибки по дороге.
        elif из_копии == "folder" and если_файл:
            raise BackupError("archive",
                              f"копия снята с папки вольта, а {vault.name} — файл")
        elif из_копии in ("sqlite", "file") and not если_файл:
            raise BackupError("archive",
                              f"копия снята с файла вольта, а {vault} — папка")
        if если_файл:
            имена = [target_entry(имена, vault)]

        куда_копию = Path(safety_dir or archive.parent)
        врем_рядом = vault.parent if если_файл else vault
        # Пустой вольт восстанавливают на новой машине или после потери папки:
        # терять нечего, и требовать страховочную копию не с чего.
        есть_что_терять = (vault.is_file() if если_файл
                           else bool(collect(vault, skip_dirs=[куда_копию])))
        if есть_что_терять:
            # Вольт под замену может быть уже битым — это самый частый повод
            # вообще запускать восстановление. Обычный create() в этом случае
            # откажет: он специально не кладёт в архив расписания неполную
            # копию. Здесь смысл другой — не потерять то, что было, а не
            # подтвердить исправность, — поэтому raw_on_corrupt=True: если
            # проверка не прошла, страховка ложится сырыми байтами как есть.
            подстраховка = create(vault, куда_копию, now=now, prefix=SAFETY_PREFIX,
                                  raw_on_corrupt=True)
        врем = Path(tempfile.mkdtemp(dir=str(врем_рядом), prefix=RESTORE_DIR))
        try:
            # Сначала распаковываем целиком во временную папку рядом. Если архив
            # оборвётся на середине, вольт к этому моменту ещё не тронут.
            for имя in имена:
                z.extract(имя, врем)
            for имя in имена:
                цель = vault if если_файл else vault / имя
                цель.parent.mkdir(parents=True, exist_ok=True)
                replace_file(врем / имя, цель)
                восстановлено.append(имя)
                if is_sqlite(цель):
                    убрано += drop_sidecars(цель)
        except (OSError, zipfile.BadZipFile) as e:
            # В поле стоит имя операции: ни настройка, ни выбранный файл
            # здесь ни при чём, сорвалось само восстановление. Куда
            # возвращаться, говорят подробности.
            raise BackupError(
                "restore",
                f"восстановление оборвалось на «{имя}»: "
                f"{' '.join(str(e).split())[:200]}",
                restored=len(восстановлено),
                safety_copy=подстраховка["file"] if подстраховка else None)
        except BackupError as e:
            # Ошибка снизу (например, неудалимый журнал базы) знает свою причину,
            # но не знает, куда человеку возвращаться. Дописываем страховочную
            # копию, не трогая сообщение.
            e.details.setdefault("restored", len(восстановлено))
            e.details.setdefault("safety_copy",
                                 подстраховка["file"] if подстраховка else None)
            raise
        finally:
            shutil.rmtree(врем, ignore_errors=True)

    из_архива = set(имена)
    лишние = [] if если_файл else [
        имя for _, имя in collect(vault, skip_dirs=[куда_копию])
        if имя not in из_архива]
    return {"archive": str(archive),
            "safety_copy": подстраховка["file"] if подстраховка else None,
            "safety_copy_raw": bool(подстраховка and подстраховка.get("raw_databases")),
            "restored": len(восстановлено), "files": восстановлено,
            "dropped": убрано, "extra": лишние}
