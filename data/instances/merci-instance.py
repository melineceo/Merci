#!/usr/bin/env python3
"""Дополнительные контейнеры Waydroid — по одному Android на окно.

Зачем отдельные контейнеры, а не профили Android: профиль живёт внутри
одного Android, и показать их одновременно нельзя — система держит на
экране только текущего пользователя. Профилей-соседей у основного бывает
ровно два (клон и рабочий), это зашито во framework. Контейнер же — целый
Android со своими данными, своим binder и своим окном; сколько их поднять,
ограничено только памятью.

Что здесь делается на каждый экземпляр:

* свой набор устройств binder. Два Android не могут делить одни и те же:
  binderfs для того и существует, чтобы выдавать независимые наборы.
  Права 666 обязательны — службы Android работают не от root, и без них
  всё падает с «Binder driver could not be opened»;
* свой каталог данных. Пустого достаточно, Android разложит его сам;
* копия конфига LXC с разведёнными именем, MAC-адресом и точками
  монтирования. Хук post-stop убираем: он рассчитан на единственный
  контейнер и при выходе прибирает общее;
* ключ adb в data/misc/adb с владельцем 1000:2000 — иначе adb не пустят,
  а без adb Merci не может ни поставить приложение, ни запустить его.

Образ системы, мост waydroid0, сокет Wayland и сервер venus — общие.
Поэтому основной контейнер должен быть уже запущен: он их и готовит.

Запускается от root (Merci зовёт через sudo -A с системным диалогом).
"""

from __future__ import annotations

import argparse
import configparser
import fcntl
import glob
import gzip
import hashlib
import io
import json
import os
import pwd
import re
import select
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from contextlib import suppress

WAYDROID_DIR = "/var/lib/waydroid"
LXC_PATH = "/var/lib/waydroid/lxc"
BASE_NAME = "waydroid"
CFG_PATH = WAYDROID_DIR + "/waydroid.cfg"
BASE_PROP = WAYDROID_DIR + "/waydroid_base.prop"
# Больше десятка Android на одной машине — это уже не про окна, а про
# ферму; такую нагрузку не потянет ни память, ни видеокарта.
MAX_INSTANCE = 12

# Владелец каталога adb внутри Android: system:shell.
ANDROID_SYSTEM_UID = 1000
ANDROID_SHELL_GID = 2000


def fail(message: str) -> None:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def run(argv: list[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(argv, capture_output=True, text=True)
    if check and result.returncode != 0:
        fail(f"{' '.join(argv)}: {(result.stderr or result.stdout).strip()}")
    return result


def clone_number(number: int) -> int:
    """Номер дополнительного окна — и только он.

    Единица — это основной контейнер, и пускать её в команды для окон
    нельзя: запуск окна начинается с пересборки его конфига из конфига
    основного, то есть со стирания каталога. Для единицы источник и цель
    совпадают — основной контейнер остался бы без настроек.
    """
    if not 2 <= number <= MAX_INSTANCE:
        fail(f"номер окна — от 2 до {MAX_INSTANCE}")
    return number


def check_home(home: str) -> str:
    """Домашний каталог того, кто позвал sudo, — и ничей другой.

    Помощник правит владельцев и пишет файлы под этим каталогом с правами
    root. Поверь он любому пути из командной строки — им можно было бы
    указать на чужой дом или на системный каталог. Кто нас позвал, sudo
    сообщает сам (SUDO_UID), и подделать это вызывающий не может.
    """
    global CALLER_UID
    home = os.path.realpath(home)
    caller = caller_uid()
    if caller is not None:
        try:
            expected = os.path.realpath(pwd.getpwuid(caller).pw_dir)
        except KeyError:
            fail("не удалось узнать домашний каталог вызвавшего пользователя")
        if home != expected:
            fail("--home должен быть домашним каталогом вызвавшего пользователя")
    if not os.path.isdir(home):
        fail(f"нет каталога {home}")
    # Хозяин дома — тот, чьим файлам помощник вправе доверять за пределами
    # дома (см. walk_below).
    CALLER_UID = caller if caller is not None else os.stat(home).st_uid
    return home


# Кто позвал помощника: его номер. Выставляется в check_home.
CALLER_UID = -1


def caller_uid() -> int | None:
    """Номер пользователя, который поднял права. None — узнать не удалось
    (помощника запустили прямо от root).

    Способ подъёма прав на разных системах разный, и каждый сообщает о
    вызвавшем по-своему: sudo и run0 — в SUDO_UID, pkexec — в PKEXEC_UID,
    doas — только именем, в DOAS_USER. Раньше читался один SUDO_UID, и под
    pkexec или doas проверка домашнего каталога молча отключалась.
    """
    for name in ("SUDO_UID", "PKEXEC_UID"):
        value = os.environ.get(name, "")
        if value.isdigit() and int(value) != 0:
            return int(value)
    user = os.environ.get("DOAS_USER", "")
    if user and user != "root":
        with suppress(KeyError):
            return pwd.getpwnam(user).pw_uid
    return None


def walk_below(home: str, relative: str, create: bool = False) -> int:
    """Открывает каталог внутри дома так, чтобы оказаться именно в нём.

    Всё, что лежит в доме, принадлежит пользователю, и любую часть пути он
    волен заменить символической ссылкой. Root, идущий по такому пути
    обычным образом, меняет владельца или пишет файл там, куда ссылка
    ведёт, — хоть в /etc.

    Поэтому сначала выясняем, куда путь ведёт на самом деле, а затем идём
    по уже настоящему пути по одному шагу, от открытого каталога, запрещая
    ссылки на каждом шаге: подмена между проверкой и действием не пройдёт.

    Куда путь вправе привести:

    * внутрь дома — всегда;
    * за его пределы — только в каталог, принадлежащий самому вызвавшему
      (или пользователю Android, 1000). Данные Waydroid нередко держат на
      другом диске, сделав ~/.local/share/waydroid ссылкой туда, и Waydroid
      с этим работает. Чужой же каталог (/etc, чужой дом) хозяину не
      принадлежит — туда не идём, и создавать что-либо в чужом каталоге
      тоже не станем.

    Возвращает дескриптор; неудача — OSError.
    """
    target = os.path.realpath(os.path.join(home, relative))
    inside = os.path.commonpath([target, home]) == home
    trusted = {CALLER_UID, ANDROID_SYSTEM_UID} - {-1, 0}
    start = home if inside else "/"
    current = os.open(start, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in (piece for piece in os.path.relpath(target, start).split("/") if piece):
            if part in (".", ".."):
                continue
            if create:
                try:
                    os.stat(part, dir_fd=current, follow_symlinks=False)
                except FileNotFoundError:
                    # Новый каталог заводим только там, где это вправе делать
                    # сам хозяин: внутри дома либо в его же каталоге.
                    if not inside and os.fstat(current).st_uid not in trusted:
                        raise OSError(
                            f"{relative} ведёт в чужой каталог — создавать там нельзя"
                        ) from None
                    with suppress(FileExistsError):
                        os.mkdir(part, 0o755, dir_fd=current)
            following = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = following
        if not inside and os.fstat(current).st_uid not in trusted:
            raise OSError(f"{relative} ведёт в чужой каталог")
        return current
    except BaseException:
        os.close(current)
        raise


def open_below(home: str, relative: str, create: bool = False) -> int:
    """То же, но неудача завершает помощника внятным сообщением."""
    try:
        return walk_below(home, relative, create)
    except OSError as failure:
        fail(f"{os.path.join(home, relative)}: {failure.strerror or failure}")


def write_atomic(path: str, text: str) -> None:
    """Заменяет файл целиком: либо прежнее содержимое, либо новое.

    Настройки Waydroid читаются при каждом старте контейнера. Запись
    «открыл — стёр — пишу» оставляет окно, в которое файл пуст или обрезан;
    упади помощник в этот момент — Waydroid останется без настроек.
    """
    directory = os.path.dirname(path)
    temporary = os.path.join(directory, f".{os.path.basename(path)}.merci")
    try:
        previous = os.stat(path)
    except OSError:
        previous = None
    with suppress(FileNotFoundError):
        os.unlink(temporary)
    handle = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            if previous is not None:
                os.fchown(stream.fileno(), previous.st_uid, previous.st_gid)
                os.fchmod(stream.fileno(), stat.S_IMODE(previous.st_mode))
            else:
                os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(OSError):
            os.unlink(temporary)
        raise


def container_name(number: int) -> str:
    return BASE_NAME if number == 1 else f"{BASE_NAME}{number}"


def data_relative(number: int) -> str:
    """Каталог данных окна относительно дома."""
    return ".local/share/waydroid/data" if number == 1 else f".local/share/waydroid{number}/data"


def data_dir(home: str, number: int) -> str:
    if number == 1:
        return os.path.join(home, ".local/share/waydroid/data")
    return os.path.join(home, f".local/share/waydroid{number}/data")


def binderfs_dir(number: int) -> str:
    return "/dev/binderfs" if number == 1 else f"/dev/binderfs{number}"


def mac_for(number: int) -> str:
    """MAC на основе номера: адреса должны различаться, иначе мост
    отдаст двум контейнерам один и тот же IP."""
    return f"00:16:3e:f9:d3:{0x03 + number - 1:02x}"


# -- binder ---------------------------------------------------------------


def _binder_ctl_add() -> int:
    """Номер ioctl BINDER_CTL_ADD. Считаем так же, как ядро."""
    nrbits, typebits, sizebits = 8, 8, 14
    nrshift = 0
    typeshift = nrshift + nrbits
    sizeshift = typeshift + typebits
    dirshift = sizeshift + sizebits
    read_write = 0x1 | 0x2
    return (read_write << dirshift) | (98 << typeshift) | (1 << nrshift) | (264 << sizeshift)


def ensure_binder(number: int, fresh: bool = False) -> str:
    """Отдельный экземпляр binderfs с устройствами. Возвращает каталог.

    ``fresh`` пересоздаёт набор устройств перед запуском окна, и это не
    предосторожность впрок. Устройства binder переживают контейнер, а
    выключаем мы окна жёстко (`lxc-stop -k`) — иначе они не выключаются.
    В устройствах остаются ссылки от убитого Android, и следующий запуск
    получает их вместе с чужими остатками: вызовы к SurfaceFlinger по
    binder виснут насмерть, system_server блокируется в конструкторе
    DisplayManagerService, сторож убивает его по кругу. Окно при этом
    «работает»: контейнер поднят, а внутри ни сети, ни картинки — снаружи
    видно только «окно не получило адрес в сети».

    Размонтировать можно спокойно: пока контейнер стоит, эти устройства
    никому не нужны. Если не вышло — работаем с тем, что есть.
    """
    path = binderfs_dir(number)
    os.makedirs(path, exist_ok=True)
    if fresh and os.path.ismount(path):
        # Только что убитый контейнер отпускает устройства не сразу, и
        # обычный umount отвечает «занято». Ждём его несколько секунд, а
        # если так и не отпустил — отсоединяем отложенно: старый набор
        # доживёт свой век у прежних владельцев, а мы получим новый.
        for _ in range(6):
            if run(["umount", path], check=False).returncode == 0:
                break
            time.sleep(2)
        else:
            run(["umount", "-l", path], check=False)
    if not os.path.ismount(path):
        run(["mount", "-t", "binder", "binder", path])

    control = os.path.join(path, "binder-control")
    if not os.path.exists(control):
        fail(f"{control} не появился — ядро без поддержки binderfs?")

    with open(control, "rb") as handle:
        for node in ("binder", "hwbinder", "vndbinder"):
            with suppress(FileExistsError):
                fcntl.ioctl(
                    handle.fileno(),
                    _binder_ctl_add(),
                    struct.pack("256sII", node.encode(), 0, 0),
                )

    # Без этого Android падает сразу после старта служб.
    for node in ("binder", "hwbinder", "vndbinder"):
        node_path = os.path.join(path, node)
        if os.path.exists(node_path):
            os.chmod(node_path, 0o666)
    return path


# -- конфигурация контейнера ---------------------------------------------


def patch(path: str, pairs: list[tuple[str, str]]) -> None:
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    for old, new in pairs:
        text = text.replace(old, new)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def make_config(number: int, home: str) -> str:
    """Копия конфига основного контейнера с разведёнными путями."""
    source = os.path.join(LXC_PATH, BASE_NAME)
    if not os.path.isdir(source):
        fail("основной контейнер не настроен — сначала запустите Waydroid")

    target = os.path.join(LXC_PATH, container_name(number))
    if os.path.exists(target):
        shutil.rmtree(target)
    shutil.copytree(source, target)
    # Настройки основного контейнера пишет Waydroid, и от версии к версии
    # (а у waydroid-nvidia — от режима к режиму) их состав меняется. Если
    # нужных файлов нет, разводить нечего — и поднимать окно на неразведённых
    # настройках нельзя: два Android писали бы в одни данные.
    for name in ("config", "config_session", "config_nodes"):
        if not os.path.isfile(os.path.join(target, name)):
            shutil.rmtree(target, ignore_errors=True)
            fail(
                f"в настройках основного контейнера нет файла {name}: эта версия "
                "Waydroid устроена иначе, и окно на ней поднять нельзя"
            )

    data = data_dir(home, number)
    patch(
        os.path.join(target, "config"),
        [
            (f"{LXC_PATH}/{BASE_NAME}/", f"{LXC_PATH}/{container_name(number)}/"),
            (f"lxc.uts.name = {BASE_NAME}", f"lxc.uts.name = {container_name(number)}"),
            (mac_for(1), mac_for(number)),
        ],
    )
    # Хук post-stop прибирает общее для всех — второму контейнеру этого
    # делать нельзя, иначе он унесёт за собой основной.
    config_path = os.path.join(target, "config")
    with open(config_path, encoding="utf-8") as handle:
        lines = [line for line in handle if "waydroid-post-stop.sh" not in line]
    with open(config_path, "w", encoding="utf-8") as handle:
        handle.writelines(lines)

    patch(
        os.path.join(target, "config_session"),
        [(data_dir(home, 1), data)],
    )
    binder = binderfs_dir(number)
    patch(
        os.path.join(target, "config_nodes"),
        [
            ("/dev/binder ", f"{binder}/binder "),
            ("/dev/hwbinder ", f"{binder}/hwbinder "),
            ("/dev/vndbinder ", f"{binder}/vndbinder "),
        ],
    )
    # Проверяем, что развели на самом деле: подстановки ищут строки в том
    # виде, в каком их пишет нынешний Waydroid. Изменись этот вид — они
    # молча ничего не заменят, и окно поднялось бы на данных и устройствах
    # основного контейнера.
    with open(os.path.join(target, "config_session"), encoding="utf-8") as handle:
        text = handle.read()
    own_data = data in text and data_dir(home, 1) not in text
    with open(os.path.join(target, "config_nodes"), encoding="utf-8") as handle:
        text = handle.read()
    own_binder = all(
        f"{binder}/{name} " in text for name in ("binder", "hwbinder", "vndbinder")
    )
    if not (own_data and own_binder):
        shutil.rmtree(target, ignore_errors=True)
        fail(
            "настройки окна не удалось развести с основным контейнером ("
            + ("устройства binder" if own_data else "данные")
            + "): эта версия Waydroid пишет их иначе"
        )
    return target


def ensure_data(home: str, number: int) -> str:
    """Пустой каталог данных: Android разложит его при первой загрузке."""
    relative = data_relative(number)
    for piece in (os.path.dirname(relative), relative):
        handle = open_below(home, piece, create=True)
        try:
            os.fchown(handle, ANDROID_SYSTEM_UID, ANDROID_SYSTEM_UID)
        finally:
            os.close(handle)
    return data_dir(home, number)


def ensure_adb_key(home: str, number: int) -> None:
    """Ключ хоста в списке доверенных.

    Без него adb отвечает «device unauthorized», а подтвердить запрос
    внутри Android некому: окна с диалогом ещё нет.
    """
    content = read_public_key(home)
    if not content:
        # Ключ появляется при первом запуске adb; не наша забота его делать.
        return
    if not os.path.isdir(data_dir(home, number)):
        # Данных ещё нет: Android создаст их сам при первой загрузке, и
        # создавать каталоги за него — значит расставить им чужих владельцев.
        return
    adb_dir = open_below(home, data_relative(number) + "/misc/adb", create=True)
    try:
        # Файл открываем от каталога и без прохода по ссылке: на его месте
        # могла бы лежать ссылка на любой системный файл, и root дописал бы
        # в него строку.
        keys = os.open(
            "adb_keys",
            os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
            0o640,
            dir_fd=adb_dir,
        )
        try:
            info = os.fstat(keys)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                fail("adb_keys — не обычный файл")
            existing = os.pread(keys, 1 << 20, 0).decode("utf-8", "replace")
            if content not in existing:
                os.write(keys, (content + "\n").encode())
            os.fchown(keys, ANDROID_SYSTEM_UID, ANDROID_SHELL_GID)
            os.fchmod(keys, 0o640)
        finally:
            os.close(keys)
        os.fchown(adb_dir, ANDROID_SYSTEM_UID, ANDROID_SHELL_GID)
        os.fchmod(adb_dir, 0o750)
    finally:
        os.close(adb_dir)


def read_public_key(home: str) -> str:
    """Открытый ключ adb пользователя или пустая строка.

    Читаем его с правами root, поэтому — только обычный файл, только
    небольшой и только похожий на ключ: иначе на этом месте могла бы
    оказаться ссылка на файл, который пользователю читать не положено, и
    его содержимое уехало бы в каталог, который пользователь читает.
    """
    try:
        folder = walk_below(home, ".android")
    except OSError:
        return ""
    try:
        try:
            handle = os.open("adbkey.pub", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=folder)
        except OSError:
            return ""
        try:
            info = os.fstat(handle)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 8192:
                return ""
            content = os.read(handle, 8192).decode("ascii", "replace").strip()
        finally:
            os.close(handle)
    finally:
        os.close(folder)
    if not re.fullmatch(r"[A-Za-z0-9+/=]{100,}( [\x20-\x7e]{1,200})?", content):
        return ""
    return content


# -- состояние -----------------------------------------------------------


def lxc_state(number: int) -> str:
    result = run(
        ["lxc-info", "-P", LXC_PATH, "-n", container_name(number), "-s"], check=False
    )
    match = re.search(r"State:\s+(\w+)", result.stdout)
    return match.group(1) if match else "ABSENT"


def instance_ip(number: int) -> str:
    """Адрес по MAC: смотрим соседей на мосту."""
    mac = mac_for(number)
    result = run(["ip", "neigh", "show", "dev", "waydroid0"], check=False)
    for line in result.stdout.splitlines():
        if mac in line.lower():
            return line.split()[0]
    return ""


def known_instances() -> list[int]:
    numbers = []
    for path in sorted(glob.glob(os.path.join(LXC_PATH, f"{BASE_NAME}*"))):
        name = os.path.basename(path)
        if name == BASE_NAME:
            numbers.append(1)
        elif name[len(BASE_NAME):].isdigit():
            numbers.append(int(name[len(BASE_NAME):]))
    # Чужой каталог с похожим именем — не наше окно: трогать его нечем.
    return sorted(n for n in set(numbers) if 1 <= n <= MAX_INSTANCE)


# -- команды -------------------------------------------------------------


def cmd_create(args) -> None:
    clone_number(args.number)
    if not os.path.ismount("/var/lib/waydroid/rootfs"):
        fail("основной контейнер не запущен — образ Android не подключён")
    # Под этим номером могли остаться данные окна, о котором Merci уже не
    # помнит. Молча подобрать их значило бы выдать новое окно с чужими
    # приложениями и чужим входом в аккаунт.
    leftovers = data_dir(args.home, args.number)
    if os.path.isdir(leftovers) and os.listdir(leftovers):
        fail(f"номер {args.number} занят данными прежнего окна: {leftovers}")

    ensure_binder(args.number)
    ensure_data(args.home, args.number)
    make_config(args.number, args.home)
    ensure_adb_key(args.home, args.number)
    if getattr(args, "start", False):
        # Создать и сразу поднять — одним заходом: по отдельности это два
        # запроса пароля подряд на одно нажатие «+ Окно».
        cmd_start(args)
        return
    print(json.dumps({"number": args.number, "state": lxc_state(args.number)}))


def wait_address(number: int, seconds: int) -> str:
    """Ждёт, пока окно получит адрес и начнёт отвечать.

    Одного адреса мало: в таблице соседей он остаётся от прошлой жизни
    контейнера. Поэтому проверяем, отвечает ли он на самом деле.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        ip = instance_ip(number)
        if ip and run(["ping", "-c", "1", "-W", "1", ip], check=False).returncode == 0:
            return ip
        time.sleep(3)
    return ""


# На каком образе окно загружалось в прошлый раз. Первая загрузка на новом
# образе — это обновление системы внутри Android: пересмотр пакетов,
# пересборка кода приложений. Она идёт минутами, и сеть у окна появляется
# только в её конце.
WINDOW_STAMPS = WAYDROID_DIR + "/merci-windows.json"
FIRST_BOOT_PATIENCE = 300


def image_stamp() -> str:
    """Отметки о сборках образа, одной строкой; пусто — прочитать не удалось."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        with open(CFG_PATH, encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error):
        return ""
    return "-".join(
        parser.get("waydroid", key, fallback="0").strip()
        for key in ("system_datetime", "vendor_datetime")
    )


def window_stamps() -> dict:
    try:
        with open(WINDOW_STAMPS, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def first_boot_on_image(number: int) -> bool:
    """Впервые ли окно загружается на нынешнем образе."""
    stamp = image_stamp()
    return bool(stamp) and window_stamps().get(str(number)) != stamp


def remember_boot(number: int) -> None:
    """Окно загрузилось на нынешнем образе — в следующий раз ждём как обычно."""
    stamp = image_stamp()
    if not stamp:
        return
    stamps = window_stamps()
    if stamps.get(str(number)) != stamp:
        stamps[str(number)] = stamp
        with suppress(OSError):
            write_atomic(WINDOW_STAMPS, json.dumps(stamps))


def start_log(number: int) -> str:
    return os.path.join(LXC_PATH, container_name(number), "merci-start.log")


def start_container(number: int, home: str) -> None:
    clone_number(number)
    ensure_binder(number, fresh=True)
    make_config(number, home)
    ensure_adb_key(home, number)
    # Раньше всё, что lxc-start говорил о неудаче, уходило в /dev/null, и
    # от несостоявшегося запуска оставалось только «окно не получило
    # адрес». Теперь причина ложится в журнал рядом с конфигом (каталог
    # пересоздаётся при каждом запуске, так что журнал всегда свежий).
    log = start_log(number)
    with open(log, "wb") as errors:
        subprocess.Popen(
            ["lxc-start", "-P", LXC_PATH, "-n", container_name(number),
             "-o", log, "-F", "--", "/init"],
            stdout=subprocess.DEVNULL,
            stderr=errors,
            start_new_session=True,
        )


# Свойства, которые окно обязано взять у основного контейнера. Основной
# правят сессия Waydroid и сама Merci, а дополнительные поднимаются
# напрямую и об этих правках не знают: из общего файла свойств окно берёт,
# например, частоту обновления 180 Гц там, где у основного давно стоит 60.
INHERITED_PROPS = (
    "persist.waydroid.refresh_rate",
    "persist.waydroid.multi_windows",
    "persist.waydroid.adb",
)
# Общий размер окон: он записан в базовых свойствах, но у окна, которому
# размер когда-то задавали отдельно, своё значение перебивает общее.
SIZE_PROPS = (
    "persist.waydroid.width",
    "persist.waydroid.height",
    "persist.waydroid.lcd_density",
)


def container_prop(number: int, name: str) -> str:
    """Свойство Android в работающем контейнере (пусто — не ответил)."""
    result = run(
        ["lxc-attach", "-P", LXC_PATH, "-n", container_name(number), "--",
         "/system/bin/getprop", name],
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def base_properties() -> dict[str, str]:
    values: dict[str, str] = {}
    with suppress(OSError):
        with open(BASE_PROP, encoding="utf-8") as handle:
            for line in handle:
                name, sep, value = line.partition("=")
                if sep:
                    values[name.strip()] = value.strip()
    return values


def settle_props(number: int) -> list[str]:
    """Приводит свойства окна к свойствам основного контейнера.

    Отдаёт имена изменённых свойств. Читаются они при старте графики, так
    что после изменения окну нужен ещё один запуск.

    Раньше этим занималась сама Merci, уже после полной загрузки окна и
    через adb: находила расхождение, правила — и просила перезапуск, то
    есть второй запрос пароля и вторую загрузку Android. Здесь то же
    делается сразу после старта, под тем же паролем, и перезапуск обходится
    в несколько секунд: Android к этому моменту ещё не успел подняться.
    """
    # Сохраняемые свойства можно менять только после того, как Android
    # прочёл их с диска: записанное раньше он затрёт прочитанным.
    for _ in range(60):
        if container_prop(number, "ro.persistent_properties.ready") == "true":
            break
        if lxc_state(number) != "RUNNING":
            return []
        time.sleep(0.5)
    else:
        return []

    wanted: dict[str, str] = {}
    if lxc_state(1) == "RUNNING":
        for name in INHERITED_PROPS:
            value = container_prop(1, name)
            if value:
                wanted[name] = value
    base = base_properties()
    for name in SIZE_PROPS:
        if base.get(name):
            wanted[name] = base[name]

    changed = []
    for name, value in wanted.items():
        if not re.fullmatch(r"[A-Za-z0-9_.,:-]{1,64}", value):
            continue  # значение уходит в команду — только простые
        if container_prop(number, name) != value:
            run(
                ["lxc-attach", "-P", LXC_PATH, "-n", container_name(number), "--",
                 "/system/bin/setprop", name, value],
                check=False,
            )
            changed.append(name)
    return changed


def boot_container(number: int, home: str) -> list[str]:
    """Поднимает окно с теми же настройками, что у основного контейнера.

    Если свойства разошлись, окно тут же поднимается заново — уже с
    верными. Отдаёт имена поправленных свойств.
    """
    start_container(number, home)
    changed = settle_props(number)
    if changed:
        stop_container(number)
        time.sleep(3)
        start_container(number, home)
    return changed


def start_failure(number: int) -> str:
    """Последние строки журнала запуска — причина, по которой окно не встало."""
    try:
        with open(start_log(number), encoding="utf-8", errors="replace") as handle:
            lines = [line.strip() for line in handle if line.strip()]
    except OSError:
        return ""
    return " | ".join(lines[-3:])[:600]


def stop_container(number: int) -> None:
    """Гасит окно и ждёт, пока оно действительно остановится."""
    clone_number(number)
    run(["lxc-stop", "-P", LXC_PATH, "-n", container_name(number), "-k"], check=False)
    for _ in range(30):
        if lxc_state(number) != "RUNNING":
            break
        time.sleep(1)


def cmd_start(args) -> None:
    name = container_name(clone_number(args.number))
    if lxc_state(args.number) == "RUNNING":
        print(json.dumps({"number": args.number, "state": "RUNNING"}))
        return

    # Образ системы, мост и сокет готовит основной контейнер. Без него
    # окно поднимается в пустоту: Android стартует, но сети у него нет и
    # адреса он не получает — снаружи это выглядит как «окно не открылось».
    if not os.path.ismount("/var/lib/waydroid/rootfs"):
        fail("сначала должно работать основное окно: образ Android не подключён")
    if not os.path.isdir(os.path.join(LXC_PATH, name)):
        fail(f"контейнер {name} не создан")

    # Набор устройств binder пересоздаётся, а конфиг снимается заново: он
    # копия конфига основного контейнера, а тот сессия переписывает под
    # себя при каждом старте — там пути сокета, образа и данных. Старая
    # копия после перезагрузки машины ведёт в никуда.
    slow = first_boot_on_image(args.number)
    settled = boot_container(args.number, args.home)

    # Первый запуск иногда виснет: SurfaceFlinger внутри застревает на
    # инициализации графики, system_server за ним блокируется в
    # DisplayManagerService, и сторож убивает его по кругу. Сам контейнер
    # из этого не выйдет — снаружи видно только «окно не получило адрес».
    # Перезапуск лечит, поэтому делаем его здесь же: второй запрос пароля
    # человека бы не обрадовал.
    #
    # Но не на первой загрузке после смены образа: там Android минутами
    # обновляет сам себя, и убить его на этом значит испортить данные окна.
    # Ждём дольше и не трогаем.
    ip = wait_address(args.number, FIRST_BOOT_PATIENCE if slow else 90)
    перезапуск = False
    if not ip and not slow:
        перезапуск = True
        stop_container(args.number)
        time.sleep(3)
        boot_container(args.number, args.home)
        ip = wait_address(args.number, 150)
    if ip:
        remember_boot(args.number)
    print(json.dumps({
        "number": args.number,
        "state": "RUNNING" if ip else "STARTING",
        "ip": ip,
        "restarted": перезапуск,
        "settled": settled,
        "log": "" if ip else start_failure(args.number),
    }))


def cmd_stop(args) -> None:
    stop_container(args.number)
    print(json.dumps({"number": args.number, "state": lxc_state(args.number)}))


def cmd_stop_many(args) -> None:
    """Гасит несколько окон одним заходом — один пароль на все."""
    for number in args.numbers:
        stop_container(number)
    print(json.dumps({
        "stopped": [n for n in args.numbers if lxc_state(n) != "RUNNING"],
        "остались": [n for n in args.numbers if lxc_state(n) == "RUNNING"],
    }))


def cmd_restart(args) -> None:
    """Гасит окно и поднимает заново — одним заходом под root.

    Двумя командами это стоило бы двух запросов пароля: sudo его здесь не
    запоминает. А перезапуск нужен как раз тогда, когда в окне заклинило
    картинку, и человеку меньше всего хочется вводить пароль дважды.
    """
    args.numbers = [args.number]
    cmd_restart_many(args)


def cmd_restart_many(args) -> None:
    """То же для нескольких окон разом."""
    if not os.path.ismount("/var/lib/waydroid/rootfs"):
        fail("сначала должно работать основное окно: образ Android не подключён")
    for number in args.numbers:
        stop_container(number)
    # Контейнер числится остановленным раньше, чем отпускает устройства
    # binder; без этой паузы новый запуск получает их с прежними ссылками.
    time.sleep(3)
    cmd_start_many(args)


def cmd_prepare(args) -> None:
    """Готовит машину к старту основной сессии — одним заходом под root.

    Три дела разом: погасить лишние окна, вернуть оверлей и восстановить
    размер. По отдельности это три запроса пароля подряд, а пропущенный
    запрос оставляет машину на середине — с выключенным оверлеем и без
    транслятора ARM во всех окнах сразу.
    """
    stopped = []
    for number in known_instances():
        if number == 1:
            continue
        if lxc_state(number) == "RUNNING":
            stop_container(number)
            stopped.append(number)
    cmd_fix_overlays(args)
    if getattr(args, "width", 0) and getattr(args, "height", 0):
        cmd_set_size(args)
    print(json.dumps({"stopped": stopped}))


def remove_window(number: int, home: str) -> None:
    """Стирает окно: контейнер, его настройки и данные.

    Сначала всё проверяем, потом трогаем. Раньше настройки контейнера
    удалялись до проверки каталога данных: если та не проходила, окно
    оставалось без настроек, но с данными и с номером в списке — ни
    запустить его, ни удалить повторно.
    """
    clone_number(number)
    data = os.path.dirname(data_dir(home, number))
    real = os.path.realpath(data)
    if os.path.lexists(data):
        # Стираем только каталог данных окна — и только там, где ему место:
        # в доме либо в каталоге самого хозяина на другом диске (туда данные
        # выносят ссылкой). Окажись на пути ссылка куда-то ещё, под нож попал
        # бы каталог, на который она указывает.
        inside = os.path.commonpath([real, home]) == home
        trusted = {CALLER_UID, ANDROID_SYSTEM_UID} - {-1, 0}
        owned = os.path.isdir(real) and os.stat(real).st_uid in trusted
        if os.path.basename(real) != f"waydroid{number}" or not (inside or owned):
            fail(f"{data} ведёт в другое место ({real}) — удалите его вручную")
    stop_container(number)
    if lxc_state(number) == "RUNNING":
        fail("окно не остановилось — удалять его данные на ходу нельзя")
    target = os.path.join(LXC_PATH, container_name(number))
    if os.path.isdir(target) and not os.path.islink(target):
        shutil.rmtree(target)
    if os.path.isdir(real) and not os.path.islink(real):
        shutil.rmtree(real)
    if os.path.islink(data):
        os.unlink(data)


def cmd_remove(args) -> None:
    remove_window(args.number, args.home)
    print(json.dumps({"number": args.number, "state": "ABSENT"}))


def cmd_remove_many(args) -> None:
    """Стирает несколько окон одним заходом — один пароль на все."""
    for number in args.numbers:
        clone_number(number)
    for number in args.numbers:
        remove_window(number, args.home)
    print(json.dumps({"removed": args.numbers}))


def cmd_fix_overlays(args) -> None:
    """Возвращает mount_overlays = True в waydroid.cfg.

    Waydroid выключает оверлей после первой же неудачи монтирования и
    больше не пробует, а в оверлее лежит транслятор ARM. Неудача же
    случается сама собой, если в момент старта основной сессии работают
    дополнительные контейнеры: они держат прежнее монтирование образа.
    """
    fix_overlays()
    print(json.dumps({"overlays": "True"}))


def fix_overlays() -> None:
    path = CFG_PATH
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as failure:
        fail(f"не прочитать {path}: {failure}")
    fixed = re.sub(r"(?m)^mount_overlays\s*=\s*False\s*$", "mount_overlays = True", text)
    if fixed != text:
        write_atomic(path, fixed)


def cmd_set_size(args) -> None:
    """Записывает размер окна в общие свойства контейнеров.

    waydroid_base.prop читают ВСЕ контейнеры при старте — и уже созданные,
    и те, что появятся позже. Поэтому размер, записанный сюда, становится
    общим по-настоящему, а не «для этого окна, пока его не пересоздали».
    """
    density = set_size(args.width, args.height)
    print(json.dumps({"width": args.width, "height": args.height, "density": density}))


def set_size(width: int, height: int) -> int:
    """Пишет общий размер окон в оба файла свойств. Отдаёт плотность."""
    if not (320 <= width <= 16384 and 240 <= height <= 16384):
        fail("размер окна вне разумных пределов")
    density = max(120, min(320, round(180 * height / 1080)))
    wanted = {
        "persist.waydroid.width": str(width),
        "persist.waydroid.height": str(height),
        "persist.waydroid.lcd_density": str(density),
    }
    write_base_properties(wanted)
    write_cfg_properties(wanted)
    return density


def write_base_properties(wanted: dict) -> None:
    """Ставит свойства в waydroid_base.prop — их читает init контейнера."""
    path = BASE_PROP
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as failure:
        fail(f"не прочитать {path}: {failure}")
    kept = [line for line in lines if line.split("=", 1)[0].strip() not in wanted]
    kept.extend(f"{key}={value}" for key, value in wanted.items())
    write_atomic(path, "\n".join(kept) + "\n")


def write_cfg_properties(wanted: dict) -> None:
    """Дублирует свойства в waydroid.cfg — чтобы они пережили пересборку.

    waydroid_base.prop Waydroid собирает заново при каждом обновлении
    образа: из своих умолчаний плюс раздел [properties] отсюда. Всё,
    что записано только в base.prop, после такого обновления исчезает — так
    у человека и пропадал выбранный размер окон, причём молча.
    """
    path = CFG_PATH
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return
    if "[properties]" not in lines:
        lines += ["", "[properties]"]
    head = lines.index("[properties]")
    kept = [
        line
        for index, line in enumerate(lines)
        if not (index > head and line.split("=", 1)[0].strip() in wanted)
    ]
    head = kept.index("[properties]")
    for key, value in wanted.items():
        kept.insert(head + 1, f"{key} = {value}")
    write_atomic(path, "\n".join(kept) + "\n")


def cmd_apply_size(args) -> None:
    """Записывает общий размер и гасит работающие окна — за один заход.

    Каждый вызов помощника — отдельный запрос пароля: sudo здесь его не
    запоминает. Раньше смена размера означала до шести окон с паролем
    подряд, и достаточно было пропустить одно, чтобы всё встало на
    середине. Поэтому вся работа под root делается разом.
    """
    args.width, args.height = args.width, args.height
    cmd_set_size(args)

    stopped = []
    for number in known_instances():
        if number == 1:
            continue
        if lxc_state(number) == "RUNNING":
            stop_container(number)
            stopped.append(number)
    print(json.dumps({"stopped": stopped}))


def cmd_start_many(args) -> None:
    """Поднимает несколько окон одним заходом — снова ради одного пароля."""
    if not os.path.ismount("/var/lib/waydroid/rootfs"):
        fail("сначала должно работать основное окно: образ Android не подключён")
    started = []
    зависли = []
    причины = {}
    for number in args.numbers:
        clone_number(number)
        if not os.path.isdir(os.path.join(LXC_PATH, container_name(number))):
            fail(f"контейнер {container_name(number)} не создан")
    slow = {number for number in args.numbers if first_boot_on_image(number)}
    began = time.time()
    for number in args.numbers:
        if lxc_state(number) == "RUNNING":
            started.append(number)
            continue
        boot_container(number, args.home)
        started.append(number)
    # Проверяем сеть у всех разом: висит обычно один, и перезапускать
    # стоит только его. Срок общий — окна грузятся одновременно. Окно на
    # первой загрузке после смены образа ждём дольше и не перезапускаем
    # (см. cmd_start).
    for number in list(started):
        patience = FIRST_BOOT_PATIENCE if number in slow else 90
        if wait_address(number, max(5, int(patience - (time.time() - began)))):
            remember_boot(number)
            continue
        if number in slow:
            зависли.append(number)
            причины[str(number)] = (
                "первая загрузка на новом образе ещё идёт — подождите пару минут "
                "и откройте окно снова"
            )
            continue
        stop_container(number)
        time.sleep(3)
        boot_container(number, args.home)
        if wait_address(number, 150):
            remember_boot(number)
        else:
            зависли.append(number)
            причины[str(number)] = start_failure(number)
    print(json.dumps({"started": started, "без_сети": зависли, "log": причины}))


# -- транслятор ARM --------------------------------------------------------

# Архивы транслятора, которые помощник согласен ставить. Список живёт здесь,
# на стороне root: что бы ни передала непривилегированная часть Merci, в
# системный оверлей попадёт только содержимое с известной суммой SHA-256.
BRIDGE_ARCHIVES = {
    # libhoudini для Android 13 — из образа HPE UKW1.260829.001, действует до
    # 1 июля 2028. Источник: supremegamers/vendor_intel_proprietary_houdini,
    # коммит 2f8f088671182e17e67321e098e8411a3972a628.
    "2e82cdc88ddc4d418f7fb861aeebb7c49c83a91b97f57da10510b5e1146a4ed5": "libhoudini",
    # libndk_translation для Android 13. Источник:
    # supremegamers/vendor_google_proprietary_ndk_translation-prebuilt,
    # коммит 68734c52556d3d7a6db34c603dd9276915c29f2f.
    "a142d1586c9eafb5edf62277110f2128bc03066179a6783bcaa33ee322e1cbd0": "libndk",
}
# Архивы для образов Android 11 (те же хранилища, коммиты 81f2a51e… и
# 9324a891…). Снять с них SHA-256 было не с чего, поэтому они закреплены
# суммой MD5 — той, что указана в waydroid_script. Для этой задачи её хватает:
# слабость MD5 — в коллизиях, то есть в паре файлов, подобранных заранее
# вместе; подобрать же другой файл под сумму уже существующего (а подменить
# архив можно только так) для MD5 по-прежнему нереально.
BRIDGE_ARCHIVES_MD5 = {
    "fbff756612b4144797fbc99eadcb6653": "libhoudini",
    "c9572672d1045594448068079b34c350": "libndk",
}
BRIDGE_LIMIT = 512 * 1024 * 1024

# Свойства, с которыми Android принимает ARM-код и зовёт транслятор.
HOUDINI_PROPS = {
    "ro.product.cpu.abilist": "x86_64,x86,arm64-v8a,armeabi-v7a,armeabi",
    "ro.product.cpu.abilist32": "x86,armeabi-v7a,armeabi",
    "ro.product.cpu.abilist64": "x86_64,arm64-v8a",
    "ro.dalvik.vm.native.bridge": "libhoudini.so",
    "ro.enable.native.bridge.exec": "1",
    "ro.vendor.enable.native.bridge.exec": "1",
    "ro.vendor.enable.native.bridge.exec64": "1",
    "ro.dalvik.vm.isa.arm": "x86",
    "ro.dalvik.vm.isa.arm64": "x86_64",
}
NDK_PROPS = {
    "ro.product.cpu.abilist": "x86_64,x86,arm64-v8a,armeabi-v7a,armeabi",
    "ro.product.cpu.abilist32": "x86,armeabi-v7a,armeabi",
    "ro.product.cpu.abilist64": "x86_64,arm64-v8a",
    "ro.dalvik.vm.native.bridge": "libndk_translation.so",
    "ro.enable.native.bridge.exec": "1",
    "ro.vendor.enable.native.bridge.exec": "1",
    "ro.vendor.enable.native.bridge.exec64": "1",
    "ro.ndk_translation.version": "0.2.3",
    "ro.dalvik.vm.isa.arm": "x86",
    "ro.dalvik.vm.isa.arm64": "x86_64",
}
# Чем трансляторы различаются при установке: свойства, сама библиотека (по
# ней Merci узнаёт сборку) и что брать из каталога etc архива. У libhoudini
# оттуда нужна только регистрация binfmt — init-файл у него свой, HOUDINI_RC;
# у libndk — всё: его init-файл и настройки загрузчика ARM-библиотек.
#
# «own» — файлы, которые есть только у этого транслятора: при установке
# другого их убираем. Иначе оба init-файла регистрируют ARM-программы на
# себя, и побеждает тот, чьё имя раньше по алфавиту, — то есть прежний.
BRIDGES = {
    "libhoudini": {
        "props": HOUDINI_PROPS,
        "library": "libhoudini.so",
        "whole_etc": False,
        "own": ("bin/houdini", "bin/houdini64", "lib/libhoudini.so", "lib64/libhoudini.so",
                "etc/init/houdini.rc"),
    },
    "libndk": {
        "props": NDK_PROPS,
        "library": "libndk_translation.so",
        "whole_etc": True,
        "own": ("bin/ndk_translation_program_runner_binfmt_misc",
                "bin/ndk_translation_program_runner_binfmt_misc_arm64",
                "etc/ld.config.arm.txt", "etc/ld.config.arm64.txt",
                "etc/cpuinfo.arm.txt", "etc/cpuinfo.arm64.txt",
                "etc/init/ndk_translation.rc", "lib/libndk*", "lib64/libndk*"),
    },
}
# Каталоги, которые трансляторы делят: ARM-программы и ARM-библиотеки
# Android. Имена у них одни, содержимое у каждого своё, и набор разный —
# после установки там должно лежать ровно то, что принёс нынешний.
BRIDGE_SHARED = ("bin/arm", "bin/arm64", "etc/binfmt_misc", "lib/arm", "lib64/arm64")

# Регистрация ARM-исполняемых файлов в binfmt_misc. Свой файл в архиве
# рассчитан на раскладку /vendor и нам не годится, поэтому пишем этот.
HOUDINI_RC = r"""
on early-init
    mount binfmt_misc binfmt_misc /proc/sys/fs/binfmt_misc

on property:ro.enable.native.bridge.exec=1
    exec -- /system/bin/sh -c "echo ':arm_exe:M::\\x7f\\x45\\x4c\\x46\\x01\\x01\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x02\\x00\\x28::/system/bin/houdini:P' > /proc/sys/fs/binfmt_misc/register"
    exec -- /system/bin/sh -c "echo ':arm_dyn:M::\\x7f\\x45\\x4c\\x46\\x01\\x01\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x03\\x00\\x28::/system/bin/houdini:P' >> /proc/sys/fs/binfmt_misc/register"
    exec -- /system/bin/sh -c "echo ':arm64_exe:M::\\x7f\\x45\\x4c\\x46\\x02\\x01\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x02\\x00\\xb7::/system/bin/houdini64:P' >> /proc/sys/fs/binfmt_misc/register"
    exec -- /system/bin/sh -c "echo ':arm64_dyn:M::\\x7f\\x45\\x4c\\x46\\x02\\x01\\x01\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x03\\x00\\xb7::/system/bin/houdini64:P' >> /proc/sys/fs/binfmt_misc/register"
"""


def real_dir_below(base: str, path: str, create: bool = False) -> bool:
    """Настоящий ли ``path`` каталог внутри ``base`` — без ссылок по дороге.

    Оверлей принадлежит root, и ссылке взяться в нём неоткуда, кроме как от
    самого root (руками, чужим скриптом). Но пройти по такой ссылке значило
    бы писать и удалять за пределами оверлея — уже с нашими правами. Сам
    ``base`` может быть перенесён ссылкой: это решение хозяина машины.
    С ``create`` недостающие каталоги заводятся по дороге.
    """
    base = os.path.normpath(base)
    path = os.path.normpath(path)
    if os.path.commonpath([path, base]) != base:
        return False
    current = base
    for part in os.path.relpath(path, base).split(os.sep):
        if part in ("", "."):
            continue
        current = os.path.join(current, part)
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            if not create:
                return False
            os.mkdir(current, 0o755)
            continue
        except OSError:
            return False
        if not stat.S_ISDIR(info.st_mode):
            return False
    return True


def stage_file(dest: str, data: bytes, mode: int, gid: int = 0) -> str:
    """Пишет файл рядом с местом назначения и отдаёт путь к написанному.

    На место его ставит os.replace — отдельно, когда написано всё: так
    установка из многих файлов либо ложится целиком, либо не трогает
    ничего (кончилось место — прежние файлы как были).
    """
    folder = os.path.dirname(dest)
    os.makedirs(os.path.join(WAYDROID_DIR, "overlay"), mode=0o755, exist_ok=True)
    if not real_dir_below(os.path.join(WAYDROID_DIR, "overlay"), folder, create=True):
        raise OSError(f"в оверлее на месте каталога стоит ссылка или файл: {folder}")
    temp = dest + ".merci-new"
    with suppress(FileNotFoundError):
        os.unlink(temp)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            os.fchown(handle.fileno(), 0, gid)
            os.fchmod(handle.fileno(), mode)
    except BaseException:
        with suppress(OSError):
            os.unlink(temp)
        raise
    return temp


def _install_file(dest: str, data: bytes, mode: int, gid: int = 0) -> None:
    """Кладёт файл в оверлей целиком или никак: запись рядом и подмена.

    Подмена через rename не идёт по символической ссылке на месте
    назначения, а заменяет её саму — чужой файл так не перезаписать.
    """
    temp = stage_file(dest, data, mode, gid)
    try:
        os.replace(temp, dest)
    except BaseException:
        with suppress(OSError):
            os.unlink(temp)
        raise


def overlay_path(*parts: str) -> str:
    """Путь в оверлее Waydroid: то, что лежит здесь, Android видит поверх
    образа (overlay/system/… — это /system/…)."""
    return os.path.join(WAYDROID_DIR, "overlay", *parts)


def read_whole(path: str, what: str, limit: int) -> bytes:
    """Читает файл пользователя один раз и целиком.

    Дальше работаем только с прочитанным: между проверкой суммы и
    установкой файл на диске уже не подменить. Берём только обычный файл
    самого вызвавшего (или root — так остаются загрузки прежних версий
    Merci): под root незачем открывать то, что человеку не принадлежит.
    """
    try:
        # O_NONBLOCK: на месте файла мог бы лежать именованный канал, и
        # открытие ждало бы пишущего вечно.
        handle = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as failure:
        fail(f"не открыть {what}: {failure.strerror or failure}")
    try:
        info = os.fstat(handle)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            fail(f"{what} — не обычный файл")
        if CALLER_UID >= 0 and info.st_uid not in (CALLER_UID, 0):
            fail(f"{what} принадлежит другому пользователю")
        blocks = []
        while True:
            block = os.read(handle, 1024 * 1024)
            if not block:
                return b"".join(blocks)
            blocks.append(block)
    finally:
        os.close(handle)


def bridge_entries(archive: zipfile.ZipFile, whole_etc: bool) -> dict:
    """Что из архива транслятора идёт в систему: путь в /system → запись.

    Архивы устроены одинаково: <имя>/prebuilts/{bin,etc,lib,lib64}/…
    Исполняемые и библиотеки берём всегда, из etc — регистрацию binfmt, а
    остальное только если транслятору оно нужно (см. BRIDGES).
    """
    entries = {}
    for info in archive.infolist():
        parts = info.filename.split("/")
        if info.is_dir() or len(parts) < 4 or parts[1] != "prebuilts":
            continue
        rel = parts[2:]
        if any(part in ("", ".", "..") for part in rel):
            fail(f"подозрительный путь в архиве: {info.filename}")
        # Тип записи — в старших битах; у иных архивов там только права или
        # вовсе ноль, и это обычные файлы. Ссылки и прочее не берём.
        kind = stat.S_IFMT((info.external_attr >> 16) & 0xFFFF)
        if kind and kind != stat.S_IFREG:
            fail(f"в архиве не обычный файл: {info.filename}")
        if rel[0] == "etc":
            binfmt = len(rel) == 3 and rel[1] == "binfmt_misc"
            if not (binfmt or whole_etc):
                continue
        elif rel[0] not in ("bin", "lib", "lib64"):
            continue
        entries["/".join(rel)] = info
    return entries


def sweep_staged() -> int:
    """Убирает файлы, недописанные оборванной установкой (*.merci-new).

    Обычный отказ подчищает их сам; остаются они, только если помощника
    убили или пропало питание. Лежать им нельзя: init читает из
    /system/etc/init любой файл, как бы тот ни назывался.
    """
    system = overlay_path("system")
    if not real_dir_below(overlay_path(), system):
        return 0
    removed = 0
    for root, _dirs, files in os.walk(system):
        for leaf in files:
            if leaf.endswith(".merci-new"):
                with suppress(OSError):
                    os.unlink(os.path.join(root, leaf))
                    removed += 1
    return removed


def bridge_prune(target: str, keep: set) -> int:
    """Убирает из оверлея то, что осталось от другого транслятора.

    Отдаёт число убранных файлов. Трогает только заведомо трансляторское:
    собственные файлы другого транслятора (BRIDGES[…]["own"]) и лишнее в
    общих каталогах ARM-программ и ARM-библиотек (BRIDGE_SHARED).
    """
    system = overlay_path("system")
    removed = 0
    for name, bridge in BRIDGES.items():
        if name == target:
            continue
        for pattern in bridge["own"]:
            wanted = os.path.join(system, *pattern.split("/"))
            # Только в настоящих каталогах оверлея: по ссылке не ходим.
            if not real_dir_below(overlay_path(), os.path.dirname(wanted)):
                continue
            for path in glob.glob(wanted):
                if os.path.relpath(path, system) not in keep:
                    removed += remove_path(path)
    for shared in BRIDGE_SHARED:
        top = os.path.join(system, *shared.split("/"))
        if not real_dir_below(overlay_path(), top):
            continue
        for root, _dirs, files in os.walk(top, topdown=False):
            for leaf in files:
                path = os.path.join(root, leaf)
                if os.path.relpath(path, system) not in keep:
                    os.unlink(path)
                    removed += 1
            # Опустевший каталог (например, bin/arm после libndk) — тоже след.
            with suppress(OSError):
                os.rmdir(root)
    return removed


def cmd_bridge_install(args) -> None:
    """Ставит, обновляет или меняет транслятор ARM — из проверенного архива.

    Заменяет запуск стороннего waydroid_script под root: в оверлей идёт
    только содержимое архива с известной суммой, и только файлы самого
    транслятора. Какой именно транслятор ставится, определяет архив.

    Ложится он целиком или никак: все файлы сначала пишутся рядом со своими
    местами и только потом разом встают на них. Иначе обрыв посреди смены
    (кончилось место) оставлял бы половину библиотек от одного транслятора,
    половину от другого — при свойствах прежнего.
    """
    # Архив читаем один раз и дальше работаем только с прочитанным: между
    # проверкой суммы и распаковкой файл на диске уже не подменить.
    blob = read_whole(args.archive, "архив транслятора", BRIDGE_LIMIT)
    target = (
        BRIDGE_ARCHIVES.get(hashlib.sha256(blob).hexdigest())
        or BRIDGE_ARCHIVES_MD5.get(hashlib.md5(blob).hexdigest())
    )
    if target is None:
        fail("архив транслятора не из числа известных: под root ставится только "
             "архив с известной контрольной суммой")
    bridge = BRIDGES[target]
    try:
        archive = zipfile.ZipFile(io.BytesIO(blob))
        entries = bridge_entries(archive, bridge["whole_etc"])
    except zipfile.BadZipFile as failure:
        fail(f"архив транслятора повреждён: {failure}")
    library = f"lib64/{bridge['library']}"
    if library not in entries:
        fail("в архиве не нашлось файлов транслятора")
    needed = sum(info.file_size for info in entries.values())

    # Нижний слой подключённого оверлея менять нельзя: гасим окна, на время
    # работы останавливаем службу контейнера и отключаем образ (см.
    # release_image) — в том числе оставшийся подключённым после окон.
    uninterruptible()
    service_was_up = release_image()
    try:
        # В выключенном оверлее транслятор лежал бы без дела, а Waydroid
        # выключает оверлей сам — после первой же неудачи монтирования.
        fix_overlays()
        sweep_staged()
        # Новые файлы какое-то время лежат рядом с прежними.
        free = shutil.disk_usage(WAYDROID_DIR).free
        if free < needed + 32 * 1024 * 1024:
            fail(f"мало места: транслятору нужно около {needed // (1024 * 1024) + 32} МБ, "
                 f"свободно {free // (1024 * 1024)} МБ — прежний транслятор не тронут")

        print(f"ставим {target}: файлов — {len(entries)}", flush=True)
        staged = []
        keep = set(entries)
        try:
            for rel, info in entries.items():
                is_bin = rel.startswith("bin/")
                dest = overlay_path("system", *rel.split("/"))
                staged.append((
                    stage_file(dest, archive.read(info), 0o755 if is_bin else 0o644,
                               ANDROID_SHELL_GID if is_bin else 0),
                    dest,
                ))
            if target == "libhoudini":
                # Свой init-файл: тот, что в архиве, рассчитан на раскладку /vendor.
                init_rc = overlay_path("system", "etc", "init", "houdini.rc")
                keep.add("etc/init/houdini.rc")
                if not os.path.exists(init_rc):
                    staged.append((stage_file(init_rc, HOUDINI_RC.encode(), 0o644), init_rc))
        except OSError as failure:
            for temp, _dest in staged:
                with suppress(OSError):
                    os.unlink(temp)
            fail(f"не удалось записать файлы транслятора ({failure.strerror or failure}) — "
                 "прежний транслятор не тронут")
        try:
            for temp, dest in staged:
                os.replace(temp, dest)
            pruned = bridge_prune(target, keep)
        except OSError as failure:
            fail(f"транслятор записан не до конца ({failure.strerror or failure}): "
                 "повторите установку")

        # Свойства — последними: ими транслятор и включается. И в оба файла:
        # base.prop читает init контейнера, а из waydroid.cfg Waydroid собирает
        # base.prop заново при обновлении образа.
        write_base_properties(bridge["props"])
        write_cfg_properties(bridge["props"])
    finally:
        if service_was_up:
            container_service("start")

    with open(overlay_path("system", *library.split("/")), "rb") as handle:
        lib_md5 = hashlib.md5(handle.read()).hexdigest()
    print(json.dumps({"target": target, "installed": len(entries), "pruned": pruned,
                      "lib64_md5": lib_md5}))


# -- обновления ------------------------------------------------------------
#
# Обновляют Waydroid его же штатные средства: `waydroid upgrade` для образа
# Android, менеджер пакетов для самой программы, waydroid-nvidia-setup для
# ускорения. Здесь — то, чего они не делают и из-за чего обновление ломало бы
# Merci: погасить дополнительные окна (они держат прежний образ
# подключённым, и перезапись файла под ними портит им файловую систему),
# сохранить прежний образ на случай неудачи и вернуть на место настройки,
# которые обновление сбрасывает.

IMAGE_FILES = ("system.img", "vendor.img")
IMAGE_STAMPS = {"system.img": "system_datetime", "vendor.img": "vendor_datetime"}
BACKUP_DIR = "merci-backup"
# Каталоги, куда образ кладёт пакет дистрибутива: такие Waydroid сам не
# обновляет, и нам там делать нечего.
PREINSTALLED_IMAGES = ("/etc/waydroid-extra/images", "/usr/share/waydroid-extra/images")
SUBSURFACE_PROP = "persist.waydroid.use_subsurface"
MEGABYTE = 1024 * 1024


def load_cfg() -> configparser.ConfigParser:
    """waydroid.cfg — только на чтение. Пишем в него построчно (см.
    write_cfg_properties): Waydroid хранит там и свои ключи, и переписывать
    файл целиком своим разборщиком значит рисковать чужими строками."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        with open(CFG_PATH, encoding="utf-8") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error) as failure:
        fail(f"не прочитать {CFG_PATH}: {failure}")
    return parser


def cfg_value(parser: configparser.ConfigParser, key: str, fallback: str = "") -> str:
    return parser.get("waydroid", key, fallback=fallback).strip()


def set_cfg_values(wanted: dict) -> None:
    """Меняет значения в разделе [waydroid] — построчно, остальное как было."""
    try:
        with open(CFG_PATH, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as failure:
        fail(f"не прочитать {CFG_PATH}: {failure}")
    section = ""
    left = dict(wanted)
    last = -1
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            continue
        if section != "[waydroid]":
            continue
        last = index
        name = line.split("=", 1)[0].strip()
        if name in left:
            lines[index] = f"{name} = {left.pop(name)}"
    if left:
        if last < 0:
            fail(f"в {CFG_PATH} нет раздела [waydroid]")
        for name, value in left.items():
            last += 1
            lines.insert(last, f"{name} = {value}")
    write_atomic(CFG_PATH, "\n".join(lines) + "\n")


def images_dir(parser: configparser.ConfigParser) -> str:
    path = os.path.realpath(cfg_value(parser, "images_path", WAYDROID_DIR + "/images"))
    if path in PREINSTALLED_IMAGES:
        fail("образ Android поставлен пакетом дистрибутива и обновляется вместе с ним")
    if not os.path.isdir(path):
        fail(f"нет каталога образов {path}")
    return path


def stop_everything() -> list[int]:
    """Гасит дополнительные окна и убеждается, что основное выключено."""
    stopped = []
    for number in known_instances():
        if number != 1 and lxc_state(number) == "RUNNING":
            stop_container(number)
            if lxc_state(number) == "RUNNING":
                fail(f"окно {number} не остановилось — менять Waydroid под ним нельзя")
            stopped.append(number)
    if lxc_state(1) == "RUNNING":
        # Сессию гасит сама Merci, до вызова помощника; но её остановка
        # иногда упирается в таймаут D-Bus и оставляет контейнер работать.
        run([waydroid_tool(), "container", "stop"], check=False)
        for _ in range(30):
            if lxc_state(1) != "RUNNING":
                break
            time.sleep(1)
    if lxc_state(1) == "RUNNING":
        fail("основной контейнер не остановился: выключите Waydroid и повторите")
    return stopped


def file_signature(path: str) -> tuple:
    try:
        info = os.stat(path)
    except OSError:
        return ()
    return (info.st_size, info.st_mtime_ns, info.st_ino)


def copy_image(source: str, target: str, reflink: str = "auto") -> bool:
    """Копия образа. С reflink=always — только мгновенная (btrfs, xfs)."""
    # Разрежённость просим только у обычной копии: с --reflink её указывать
    # нельзя (cp отказывается), да и незачем — блоки там общие.
    extra = ["--sparse=always"] if reflink == "never" else []
    result = run(["cp", f"--reflink={reflink}", *extra, "--", source, target], check=False)
    if result.returncode != 0:
        with suppress(OSError):
            os.unlink(target)
    return result.returncode == 0


def backup_meta(images: str) -> dict:
    """Что лежит в резервной копии образов; пусто — копии нет."""
    path = os.path.join(images, BACKUP_DIR, "backup.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def backup_images(images: str, parser: configparser.ConfigParser, reserve: int) -> str:
    """Сохраняет нынешние образы рядом с ними. Отдаёт, как именно (или почему нет).

    На btrfs и xfs копия мгновенная и места не занимает, пока образы не
    разошлись. На остальных — обычная, и делается, только если после неё
    останется место на само обновление (``reserve`` байт).
    """
    sources = [os.path.join(images, name) for name in IMAGE_FILES]
    for source in sources:
        if not os.path.isfile(source) or os.path.islink(source):
            fail(f"нет образа {source} — обновлять нечего")
    # Место нужно в обоих случаях: обычная копия занимает его сразу, а
    # мгновенная — по мере того как новый образ расходится с прежним.
    total = sum(os.path.getsize(source) for source in sources)
    if shutil.disk_usage(images).free < total + reserve:
        return ""
    staging = os.path.join(images, BACKUP_DIR + ".new")
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, mode=0o755)

    how = "мгновенная копия"
    instant = all(
        copy_image(source, os.path.join(staging, os.path.basename(source)), "always")
        for source in sources
    )
    if not instant:
        how = "полная копия"
        for source in sources:
            if not copy_image(source, os.path.join(staging, os.path.basename(source)), "never"):
                shutil.rmtree(staging, ignore_errors=True)
                return ""

    meta = {
        "saved_at": int(time.time()),
        "system_datetime": cfg_value(parser, "system_datetime", "0"),
        "vendor_datetime": cfg_value(parser, "vendor_datetime", "0"),
        "size": sum(os.path.getsize(source) for source in sources),
    }
    with open(os.path.join(staging, "backup.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle)
    target = os.path.join(images, BACKUP_DIR)
    shutil.rmtree(target, ignore_errors=True)
    os.rename(staging, target)
    return how


def reassert(args) -> None:
    """Возвращает на место настройки Merci, которые обновление сбрасывает.

    Свойства контейнера Waydroid при обновлении собирает заново — из своих
    умолчаний и раздела [properties] своего конфига. То, что записано в оба
    места, переживает обновление; здесь мы в этом убеждаемся, а заодно
    возвращаем оверлей (в нём транслятор ARM).
    """
    fix_overlays()
    wanted = {}
    if getattr(args, "subsurface", "") in ("true", "false"):
        wanted[SUBSURFACE_PROP] = args.subsurface
    users = getattr(args, "max_users", 0)
    if users:
        if not 2 <= users <= 8:
            fail("число пользователей Android — от 2 до 8")
        # Предел пользователей прежние версии писали только в базовые
        # свойства — и первое же обновление образа его стирало: профили
        # оставались, а новые не создавались.
        wanted["fw.max_users"] = str(users)
    if wanted and os.path.isfile(BASE_PROP):
        write_base_properties(wanted)
        write_cfg_properties(wanted)
    width, height = getattr(args, "width", 0), getattr(args, "height", 0)
    if width and height and os.path.isfile(BASE_PROP):
        set_size(width, height)


def waydroid_tool() -> str:
    return shutil.which("waydroid") or "/usr/bin/waydroid"


def plain_env() -> dict:
    """Окружение для штатных средств: без буфера вывода (иначе ход
    загрузки приходит кусками по 8 КБ) и без перевода сообщений."""
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["LC_ALL"] = "C"
    env.pop("LANGUAGE", None)
    return env


class Tolerant:
    """Поток вывода, который не падает, когда читать уже некому.

    Вывод помощника читает Merci. Закрой её человек посреди обновления —
    первая же строка в оборванный канал уронила бы помощника с
    BrokenPipeError на полпути: пакет поставлен, настройка не начата.
    """

    def __init__(self, stream) -> None:
        self._stream = stream
        self._alive = True

    def write(self, text) -> int:
        if self._alive:
            try:
                return self._stream.write(text)
            except (OSError, ValueError):
                self._alive = False
        return len(text)

    def flush(self) -> None:
        if self._alive:
            try:
                self._stream.flush()
            except (OSError, ValueError):
                self._alive = False

    def raw(self, data: bytes) -> None:
        """Байты как есть — ход загрузки приходит возвратами каретки."""
        if not self._alive:
            return
        try:
            self._stream.flush()
            self._stream.buffer.write(data)
            self._stream.buffer.flush()
        except (OSError, ValueError, AttributeError):
            self._alive = False


# Сколько команда вправе молчать. Отменить обновление нельзя (см.
# uninterruptible), поэтому зависшую команду останавливаем сами: `waydroid
# upgrade` качает образ без таймаута и на оборванной связи ждал бы вечно —
# а с ним помощник, его замок и окно Merci.
SILENCE_LIMIT = 900


def stream(argv: list[str], silence: int = SILENCE_LIMIT) -> int:
    """Запускает команду и передаёт её вывод человеку — через себя.

    Через себя, а не напрямую: получи команда наш канал вывода, обрыв этого
    канала (закрыли Merci) убил бы её сигналом SIGPIPE — pacman посреди
    установки, `waydroid upgrade` посреди распаковки. Теперь оборваться
    может только наша пересылка, а команда дорабатывает до конца. По той же
    причине она в своём сеансе: сигнал группе помощника до неё не дойдёт.

    Команду, которая молчит дольше ``silence`` секунд, считаем зависшей и
    останавливаем: код возврата тогда 124.
    """
    sys.stdout.flush()
    out = sys.stdout if isinstance(sys.stdout, Tolerant) else Tolerant(sys.stdout)
    try:
        process = subprocess.Popen(
            argv, env=plain_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, start_new_session=True,
        )
    except OSError as failure:
        print(f"{argv[0]}: {failure}", flush=True)
        return 127
    descriptor = process.stdout.fileno()
    heard = time.monotonic()
    while True:
        try:
            ready, _w, _x = select.select([descriptor], [], [], 5)
        except InterruptedError:
            continue
        if not ready:
            if silence and time.monotonic() - heard > silence:
                print(f"{argv[0]} молчит больше {silence // 60} мин — останавливаем", flush=True)
                with suppress(OSError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                process.stdout.close()
                return 124
            continue
        try:
            chunk = os.read(descriptor, 4096)
        except InterruptedError:
            continue
        except OSError:
            break
        if not chunk:
            break
        heard = time.monotonic()
        out.raw(chunk)
    code = process.wait()
    process.stdout.close()
    return code


def uninterruptible() -> None:
    """Дальше помощника не оборвать: работа доводится до конца.

    Команды обновления меняют образ Android и настройки контейнера в
    несколько приёмов; остановленные на середине, они оставляют машину
    без рабочего Waydroid. Поэтому просьбы завершиться (закрыли Merci,
    нажали «Отмена», пропал терминал) с этого места не слушаем — сама
    работа конечна и о своём итоге сообщит. И не падаем на записи в
    оборванный канал вывода.
    """
    for number in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGPIPE):
        with suppress(OSError, ValueError):
            signal.signal(number, signal.SIG_IGN)
    if not isinstance(sys.stdout, Tolerant):
        sys.stdout = Tolerant(sys.stdout)
    if not isinstance(sys.stderr, Tolerant):
        sys.stderr = Tolerant(sys.stderr)


CONTAINER_SERVICE = "waydroid-container"


def container_service(action: str) -> bool:
    """Служба контейнера Waydroid: start, stop или is-active. Отдаёт успех.

    Пока идёт смена образа, служба стоит: работающая, она по первой же
    просьбе «подними сессию» (из трея, из другой программы) подключила бы
    образ, который в эту секунду переписывается.
    """
    if os.geteuid() != 0:
        # Без прав root systemctl пошёл бы спрашивать пароль через polkit —
        # у человека на экране, без объяснений. Помощник всегда работает от
        # root; сюда без него попадают только проверки.
        return False
    if shutil.which("systemctl"):
        argv = ["systemctl", action, f"{CONTAINER_SERVICE}.service"]
        if action == "is-active":
            argv.insert(2, "--quiet")
    elif shutil.which("rc-service"):
        argv = ["rc-service", CONTAINER_SERVICE, "status" if action == "is-active" else action]
    elif shutil.which("sv"):
        argv = ["sv", "status" if action == "is-active" else action, CONTAINER_SERVICE]
    else:
        return False
    return run(argv, check=False).returncode == 0


def drop_overlay_writes() -> None:
    """Стирает записи поверх образа: они относятся к прежнему образу, к
    новому не подойдут. Так же поступает сам Waydroid при смене образа."""
    for name in ("overlay_rw", "overlay_work"):
        shutil.rmtree(os.path.join(WAYDROID_DIR, name), ignore_errors=True)


LOCK_PATH = "/run/merci-helper.lock"
# Команды, которым нужен весь Waydroid целиком: рядом с ними не должно идти
# ничего — ни запуска окна, ни второй такой же команды.
EXCLUSIVE = {
    "update-images", "restore-images", "drop-image-backup", "refresh-config",
    "nvidia-setup", "nvidia-update", "secure-adb", "bridge-install",
    "restart-container", "apply-size", "set-size", "prepare",
    "magisk-install", "magisk-remove", "subsurface",
}


def take_lock(command: str):
    """Один помощник на одно дело.

    Каждое нажатие в Merci — отдельный вызов помощника под root, и ничто не
    мешало двум идти рядом: «Повторить» поверх ещё работающего обновления,
    запуск окна посреди смены образа. Команды, которым нужен весь Waydroid,
    берут замок целиком; команды одного окна — совместно, так что окна
    друг другу не мешают, а обновлению — да. Занято — отказываем сразу и
    говорим почему: ждать молча под окном пароля хуже.
    """
    if command == "list":
        return None
    try:
        handle = open(LOCK_PATH, "w")  # noqa: SIM115 — держим до выхода
    except OSError:
        return None  # замок — предосторожность, а не условие работы
    mode = fcntl.LOCK_EX if command in EXCLUSIVE else fcntl.LOCK_SH
    try:
        fcntl.flock(handle, mode | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        fail("помощник Merci уже занят другой операцией с Waydroid — дождитесь её конца")
    return handle


def cmd_update_images(args) -> None:
    """Обновляет образ Android штатным `waydroid upgrade` — бережно.

    Порядок: погасить окна, сохранить прежние образы, обновить, вернуть
    настройки. Если обновление оборвалось на распаковке, образ, который оно
    успело испортить, возвращается из копии сразу же.
    """
    parser = load_cfg()
    images = images_dir(parser)
    uninterruptible()
    stopped = stop_everything()
    if stopped:
        print(f"дополнительные окна выключены: {stopped}", flush=True)

    download = max(0, args.download_mb) * MEGABYTE
    margin = 300 * MEGABYTE
    free = shutil.disk_usage(images).free
    if free < download + margin:
        fail(
            f"мало места: на загрузку нужно около {(download + margin) // MEGABYTE} МБ, "
            f"свободно {free // MEGABYTE} МБ"
        )

    how = "" if args.no_backup else backup_images(images, parser, download + margin)
    if how:
        print(f"прежние образы сохранены ({how})", flush=True)
    elif not args.no_backup:
        print("прежние образы не сохранены: на копию не хватает места", flush=True)

    before = {name: file_signature(os.path.join(images, name)) for name in IMAGE_FILES}
    stamps = {name: cfg_value(parser, key, "0") for name, key in IMAGE_STAMPS.items()}

    print("обновляем образ Android", flush=True)
    service_was_up = container_service("is-active")
    if service_was_up:
        container_service("stop")
    try:
        code = stream([waydroid_tool(), "upgrade"])
    except BaseException:
        if service_was_up:
            container_service("start")
        raise

    after = load_cfg()
    updated = [
        name for name, key in IMAGE_STAMPS.items() if cfg_value(after, key, "0") != stamps[name]
    ]
    if code != 0:
        # Файл изменился, а отметка о сборке — нет: распаковка оборвалась на
        # середине, такой образ не загрузится.
        restored, lost = [], []
        backup = os.path.join(images, BACKUP_DIR)
        for name in IMAGE_FILES:
            target = os.path.join(images, name)
            saved = os.path.join(backup, name)
            if file_signature(target) == before[name] or name in updated:
                continue
            if how and os.path.isfile(saved) and copy_image(saved, target + ".restore"):
                os.replace(target + ".restore", target)
                restored.append(name)
            else:
                lost.append(name)
        if updated:
            # Обновилась одна часть из двух, и Waydroid на этом остановился:
            # записи поверх прежнего образа не стёрты, настройки контейнера
            # не пересобраны. Доделываем за него — иначе новая система
            # грузилась бы поверх чужих записей.
            drop_overlay_writes()
            stream([waydroid_tool(), "upgrade", "-o"])
        reassert(args)
        if service_was_up:
            container_service("start")
        detail = []
        if updated:
            detail.append(f"обновлено: {', '.join(updated)}")
        if restored:
            detail.append(f"возвращено из копии: {', '.join(restored)}")
        if lost:
            detail.append(
                f"повреждено и не восстановлено: {', '.join(lost)} — "
                "загрузите образ заново: sudo waydroid init -f"
            )
        fail("обновление образа не удалось" + (" (" + "; ".join(detail) + ")" if detail else "")
             + " — причина выше")

    reassert(args)
    if service_was_up:
        container_service("start")
    if updated:
        print(f"обновлено: {', '.join(updated)}", flush=True)
    else:
        print("новых образов не нашлось — настройки контейнера пересобраны", flush=True)
    print(json.dumps({"updated": updated, "backup": bool(how), "stopped": stopped}))


def cmd_restore_images(args) -> None:
    """Возвращает образы из резервной копии — если обновление не прижилось."""
    parser = load_cfg()
    images = images_dir(parser)
    backup = os.path.join(images, BACKUP_DIR)
    meta = backup_meta(images)
    if not meta:
        fail("резервной копии образов нет")
    for name in IMAGE_FILES:
        saved = os.path.join(backup, name)
        if not os.path.isfile(saved) or os.path.islink(saved):
            fail(f"в резервной копии нет {name}")
    stamps = {}
    for key in ("system_datetime", "vendor_datetime"):
        value = str(meta.get(key, ""))
        if not value.isdigit():
            fail("резервная копия повреждена: нет отметок о сборке")
        stamps[key] = value

    uninterruptible()
    stop_everything()
    total = sum(os.path.getsize(os.path.join(backup, name)) for name in IMAGE_FILES)
    # Сначала обе копии, потом обе замены: сорвись вторая копия после первой
    # замены, система и драйверы остались бы от разных сборок.
    staged = []
    for name in IMAGE_FILES:
        saved = os.path.join(backup, name)
        target = os.path.join(images, name)
        ready = copy_image(saved, target + ".restore", "always")
        if not ready and shutil.disk_usage(images).free >= total + 100 * MEGABYTE:
            ready = copy_image(saved, target + ".restore", "never")
        if not ready:
            for path in staged:
                with suppress(OSError):
                    os.unlink(path)
            fail(f"не удалось скопировать {name} из резервной копии (мало места?) — "
                 "нынешний образ не тронут")
        staged.append(target + ".restore")
    service_was_up = container_service("is-active")
    if service_was_up:
        container_service("stop")
    for name in IMAGE_FILES:
        target = os.path.join(images, name)
        os.replace(target + ".restore", target)
    set_cfg_values(stamps)
    drop_overlay_writes()
    print("пересобираем настройки контейнера", flush=True)
    code = stream([waydroid_tool(), "upgrade", "-o"])
    if service_was_up:
        container_service("start")
    if code != 0:
        fail("образы возвращены, но `waydroid upgrade -o` не выполнился — причина выше")
    reassert(args)
    print(json.dumps({"restored": list(IMAGE_FILES), "saved_at": meta.get("saved_at", 0)}))


def cmd_drop_image_backup(args) -> None:
    """Удаляет резервную копию образов — когда новый образ прижился."""
    images = images_dir(load_cfg())
    backup = os.path.join(images, BACKUP_DIR)
    existed = os.path.isdir(backup) and not os.path.islink(backup)
    if existed:
        shutil.rmtree(backup)
    print(json.dumps({"dropped": existed}))


def cmd_subsurface(args) -> None:
    """Включает или выключает подповерхности Wayland у контейнеров.

    С ними картинка замирает и обновляется только на ввод (см.
    subsurface_step в Merci). Пишем в оба файла: base.prop читает init
    контейнера, а из waydroid.cfg Waydroid собирает base.prop заново при
    обновлении образа — записанное только в первый файл пропадало бы.
    Действует со следующего запуска контейнера.

    Шаг, который это зовёт, следом перезапускает основную сессию. Под
    работающими дополнительными окнами так нельзя: они держат прежнее
    монтирование образа, оверлей Waydroid не встаёт, и тот выключает его
    насовсем — вместе с транслятором ARM во всех окнах. Поэтому, как и
    остальные команды с перезапуском, сначала гасим окна и возвращаем
    оверлей.
    """
    stopped = stop_everything()
    fix_overlays()
    wanted = {SUBSURFACE_PROP: args.value}
    write_base_properties(wanted)
    write_cfg_properties(wanted)
    print(json.dumps({"subsurface": args.value, "stopped": stopped}))


def cmd_refresh_config(args) -> None:
    """После обновления пакета waydroid: пересобрать настройки контейнера
    его новой версией и вернуть свои."""
    uninterruptible()
    stop_everything()
    print("пересобираем настройки контейнера", flush=True)
    if stream([waydroid_tool(), "upgrade", "-o"]) != 0:
        fail("`waydroid upgrade -o` не выполнился — причина выше")
    reassert(args)
    print(json.dumps({"config": "refreshed"}))


NVIDIA_SETUP = "/usr/bin/waydroid-nvidia-setup"
NVIDIA_MODES = ("nvidia-prolapse", "glibc-shim", "venus")
NVIDIA_PACKAGES = ("waydroid-nvidia-bin", "waydroid-nvidia", "waydroid-nvidia-git")


def nvidia_setup(args) -> None:
    """Запускает настройку ускорения waydroid-nvidia и возвращает то, что
    она сбрасывает.

    Настройку надо повторять после каждого обновления пакета и драйвера
    NVIDIA — без этого контейнер не стартует. Но она же заново включает
    подповерхности Wayland, а Merci выключает их там, где они дают
    мерцание: значение, стоявшее до настройки, возвращаем на место.
    """
    try:
        info = os.stat(NVIDIA_SETUP)
    except OSError:
        fail("waydroid-nvidia не установлен: нет /usr/bin/waydroid-nvidia-setup")
    if info.st_uid != 0 or info.st_mode & 0o022 or not stat.S_ISREG(info.st_mode):
        fail(f"{NVIDIA_SETUP} доступен на запись не только root — запускать его не будем")
    with open(NVIDIA_SETUP, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    knows_modes = "--mode" in text

    argv = [NVIDIA_SETUP]
    if args.mode:
        if args.mode not in NVIDIA_MODES:
            fail("режим ускорения — nvidia-prolapse, glibc-shim или venus")
        if knows_modes:
            argv += ["--mode", args.mode]
        elif args.mode != "venus":
            fail("установленная версия waydroid-nvidia умеет только режим venus")
    if args.refresh:
        if args.refresh == "auto":
            if knows_modes:
                argv += ["--refresh", "auto"]
        elif args.refresh.isdigit() and 1 <= int(args.refresh) <= 1000:
            argv += ["--refresh", args.refresh]
        else:
            fail("частота обновления — число от 1 до 1000 или auto")

    before = ""
    uninterruptible()
    if os.path.isfile(CFG_PATH):
        parser = load_cfg()
        if parser.has_section("properties"):
            before = parser.get("properties", SUBSURFACE_PROP, fallback="").strip()
        stop_everything()

    print("настраиваем ускорение NVIDIA: " + " ".join(argv[1:]), flush=True)
    code = stream(argv)
    if before == "false" and not getattr(args, "subsurface", ""):
        args.subsurface = "false"
    if code != 0:
        # Настройка могла упасть, уже остановив службу контейнера и уже
        # переписав настройки (подповерхности она включает первым делом).
        # Возвращаем что можем: свои настройки и службу — с ней Waydroid
        # хотя бы скажет, чего ему не хватает.
        with suppress(SystemExit):
            reassert(args)
        container_service("start")
        fail("waydroid-nvidia-setup завершился с ошибкой — причина выше. "
             "Повторить настройку можно из «Подготовить Waydroid»")
    reassert(args)


def cmd_nvidia_setup(args) -> None:
    nvidia_setup(args)
    print(json.dumps({"nvidia": "configured", "mode": args.mode}))


def cmd_nvidia_update(args) -> None:
    """Ставит собранный пакет waydroid-nvidia и заново настраивает ускорение
    — одним заходом под root, то есть с одним запросом пароля."""
    source = os.path.realpath(args.package)
    if os.path.commonpath([source, args.home]) != args.home:
        fail("пакет должен лежать в домашнем каталоге вызвавшего пользователя")
    if not re.search(r"\.pkg\.tar(\.(zst|xz|gz|bz2))?$", source):
        fail("это не пакет pacman")
    # Забираем файл к себе: проверять одно, а ставить другое нельзя — между
    # проверкой и установкой файл в каталоге пользователя могли бы подменить.
    workdir = tempfile.mkdtemp(prefix="merci-package.", dir="/run")
    try:
        local = os.path.join(workdir, "package.pkg.tar" + source.rsplit(".pkg.tar", 1)[1])
        try:
            handle = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as failure:
            fail(f"не открыть пакет: {failure}")
        try:
            info = os.fstat(handle)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 2048 * MEGABYTE:
                fail("пакет — не обычный файл")
            with os.fdopen(os.dup(handle), "rb") as reader, open(local, "wb") as writer:
                shutil.copyfileobj(reader, writer, 1024 * 1024)
        finally:
            os.close(handle)
        if args.sha256:
            # Сумму Merci сняла сразу после сборки, до запроса пароля, и
            # передала в командной строке: подмена файла, пока человек
            # вводил пароль, здесь и обнаружится. От подмены самого описания
            # пакета до сборки это не спасает — как и у любого помощника AUR.
            digest = hashlib.sha256()
            with open(local, "rb") as reader:
                for block in iter(lambda: reader.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != args.sha256.lower():
                fail("пакет изменился после сборки — установка отменена")
        name = run(["pacman", "-Qqp", "--", local], check=False).stdout.strip()
        if name not in NVIDIA_PACKAGES:
            fail(f"в файле пакет «{name or '?'}», а ждали waydroid-nvidia")
        uninterruptible()
        stop_everything()
        print(f"ставим пакет {name}", flush=True)
        argv = ["pacman", "-U", "--noconfirm"]
        installed = run(["pacman", "-Qq"], check=False).stdout.split()
        if "waydroid" in installed:
            # Пакет заменяет обычный waydroid, а на вопрос «удалить
            # конфликтующий?» --noconfirm отвечает отказом. --ask 4 меняет
            # ответ именно на этот вопрос: замена идёт одной операцией, и
            # при неудаче система не остаётся без Waydroid вовсе.
            argv += ["--ask", "4"]
        if stream([*argv, "--", local]) != 0:
            fail("pacman не поставил пакет — причина выше")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    nvidia_setup(args)
    print(json.dumps({"nvidia": "updated", "package": name, "mode": args.mode}))


# -- root в контейнере (Magisk) ---------------------------------------------
#
# Раньше этим занимался сторонний waydroid_script: Merci клонировала его с
# GitHub и исполняла от root. Сам Magisk он качал без контрольной суммы — из
# репозитория, который в ноябре 2026 исчез: по адресу теперь 404, а
# освободившееся имя может занять кто угодно. И он же считает провалом любую
# строку в stderr: после обновления waydroid-nvidia до 0.2.0 команда
# `waydroid upgrade -o` пишет туда режим ускорения, и удаление root, уже
# сделав своё дело, трижды докладывало об ошибке и оставляло Waydroid
# выключенным.
#
# Здесь то же делается своими руками и только в режиме оверлея (другого Merci
# не держит): в систему идут файлы из APK с известной суммой SHA-256 и больше
# ничего. Порядок действий — как у waydroid_script (casualsnek,
# stuff/magisk.py); строки init — те, которыми Magisk Delta сам встраивается в
# систему при установке в раздел system.

# Сборки Magisk, которые помощник согласен ставить. Как и у транслятора,
# список живёт на стороне root: что бы ни передала непривилегированная часть
# Merci, в систему попадёт только файл с известной суммой.
MAGISK_APKS = {
    # Magisk Delta, сборка 0fe46c5a-delta (код версии 26301), подписана
    # ключом автора: OU=HuskyDG, сертификат от 11.06.2022. Именно этот файл
    # waydroid_script раздавал из mistrmochov/magiskdeltaorig.
    "a3c37c4172b6ce7fdfbcccb2a608ef84b0a267d7aaef7c183047adceb8494c80": "Magisk Delta 0fe46c5a",
}
# Какой набор библиотек брать из APK. Строки init ниже зовут magisk64, так
# что годятся только 64-разрядные системы.
MAGISK_ABIS = {"x86_64": "x86_64", "aarch64": "arm64-v8a"}
MAGISK_ASSETS = ("addon.d.sh", "boot_patch.sh", "stub.apk", "util_functions.sh")
MAGISK_NEEDED = ("busybox", "magisk64", "magiskinit", "magiskpolicy")
MAGISK_LIMIT = 64 * MEGABYTE
# Что Magisk мог дописать поверх образа сам (обновляясь из своего
# приложения) — относительно overlay_rw.
MAGISK_UPPER = (
    "system/system/etc/init/bootanim.rc",
    "system/system/etc/init/bootanim.rc.gz",
    "system/system/etc/init/magisk",
    "system/system/addon.d/99-magisk.sh",
    "vendor/etc/selinux/precompiled_sepolicy",
)

# bootanim.rc образа как он есть. Его же, сжатым, Magisk ищет рядом как
# «исходный», когда обновляется из своего приложения.
BOOTANIM_RC = """
service bootanim /system/bin/bootanimation
    class core animation
    user graphics
    group graphics audio
    disabled
    oneshot
    ioprio rt 0
    task_profiles MaxPerformance
"""

# То, что к нему дописывается: запуск Magisk на нужных стадиях загрузки.
MAGISK_RC = """
on post-fs-data
    start logd
    exec u:r:su:s0 root root -- /system/etc/init/magisk/magiskpolicy --live --magisk
    exec u:r:magisk:s0 root root -- /system/etc/init/magisk/magiskpolicy --live --magisk
    exec u:r:update_engine:s0 root root -- /system/etc/init/magisk/magiskpolicy --live --magisk
    mkdir /dev/magisk_iqeoVo2mDrO 700
    exec u:r:su:s0 root root -- /system/etc/init/magisk/magisk64 --auto-selinux --setup-sbin /system/etc/init/magisk /dev/magisk_iqeoVo2mDrO
    exec u:r:su:s0 root root -- /dev/magisk_iqeoVo2mDrO/magisk --auto-selinux --post-fs-data

on nonencrypted
    exec u:r:su:s0 root root -- /dev/magisk_iqeoVo2mDrO/magisk --auto-selinux --service

on property:vold.decrypt=trigger_restart_framework
    exec u:r:su:s0 root root -- /dev/magisk_iqeoVo2mDrO/magisk --auto-selinux --service

on property:sys.boot_completed=1
    mkdir /data/adb/magisk 755
    exec u:r:su:s0 root root -- /dev/magisk_iqeoVo2mDrO/magisk --auto-selinux --boot-complete

on property:init.svc.zygote=restarting
    exec u:r:su:s0 root root -- /dev/magisk_iqeoVo2mDrO/magisk --auto-selinux --zygote-restart

on property:init.svc.zygote=stopped
    exec u:r:su:s0 root root -- /dev/magisk_iqeoVo2mDrO/magisk --auto-selinux --zygote-restart
"""


def remove_path(path: str) -> int:
    """Удаляет файл или каталог в хозяйстве root. Отдаёт 1, если было что."""
    try:
        info = os.lstat(path)
    except OSError:
        return 0
    if stat.S_ISDIR(info.st_mode):
        shutil.rmtree(path)
    else:
        os.unlink(path)
    return 1


def remove_at(directory: int, name: str) -> int:
    """То же по имени внутри открытого каталога — для данных Android.

    Они лежат в доме пользователя, поэтому ни по одной ссылке не идём:
    ссылку удаляем саму, в каталог входим только настоящий.
    """
    try:
        info = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except OSError:
        return 0
    if not stat.S_ISDIR(info.st_mode):
        os.unlink(name, dir_fd=directory)
        return 1
    inner = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
    try:
        for entry in os.listdir(inner):
            remove_at(inner, entry)
    finally:
        os.close(inner)
    os.rmdir(name, dir_fd=directory)
    return 1


def write_at(directory: int, name: str, data: bytes, mode: int) -> None:
    """Новый файл внутри открытого каталога, владелец — root."""
    handle = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory
    )
    with os.fdopen(handle, "wb") as writer:
        writer.write(data)
        os.fchown(writer.fileno(), 0, 0)
        os.fchmod(writer.fileno(), mode)


def data_adb(home: str, number: int, create: bool = False) -> int | None:
    """Каталог /data/adb окна — открытым. None — его или самих данных нет.

    Данных ещё нет — не заводим: Android создаёт их сам при первой
    загрузке, и каталоги, сделанные за него, достались бы ему с чужими
    владельцами.
    """
    relative = data_relative(number)
    if not os.path.isdir(os.path.join(home, relative)):
        return None
    # Только внутри дома. Данные, вынесенные ссылкой на другой диск, не
    # трогаем: /data/adb принадлежит root, а чужой каталог root (хоть /etc)
    # по владельцу от него не отличить.
    target = os.path.realpath(os.path.join(home, relative, "adb"))
    if os.path.commonpath([target, home]) != home:
        return None
    existed = os.path.isdir(os.path.join(home, relative, "adb"))
    if not existed and not create:
        return None
    try:
        handle = walk_below(home, relative + "/adb", create)
    except OSError:
        return None
    # Сверяем уже открытый каталог: путь могли подменить между проверкой
    # выше и открытием, а открытое не подменить.
    try:
        opened = os.readlink(f"/proc/self/fd/{handle}")
        inside = os.path.commonpath([opened, home]) == home
    except (OSError, ValueError):
        inside = False
    if not inside:
        os.close(handle)
        return None
    if not existed:
        # Как его завёл бы сам Android: только для root.
        os.fchown(handle, 0, 0)
        os.fchmod(handle, 0o700)
    return handle


def release_image() -> bool:
    """Гасит всё и отключает образ Android. Отдаёт, работала ли служба
    контейнера: после работы её надо вернуть.

    Оверлей — нижний слой подключённой системы, и менять его под ней
    нельзя. Службу контейнера останавливаем по той же причине, что и при
    смене образа: работающая, она по первой же просьбе (из трея, из другой
    программы) подняла бы сессию и подключила оверлей, который в эту секунду
    переписывается. Заодно её остановка отключает образ, оставшийся
    подключённым после дополнительных окон.
    """
    stop_everything()
    was_up = container_service("is-active")
    if was_up:
        container_service("stop")
    rootfs = os.path.join(WAYDROID_DIR, "rootfs")
    for attempt in range(20):
        if not os.path.ismount(rootfs):
            return was_up
        if attempt == 10:
            # Все контейнеры стоят (stop_everything это проверил), а образ
            # подключён: осталось от сорванной остановки. Отключаем сами —
            # так же поступает `waydroid container stop`.
            run(["umount", "-R", rootfs], check=False)
        time.sleep(1)
    if was_up:
        container_service("start")
    fail("образ Android всё ещё подключён: выключите Waydroid и повторите")


def magisk_files(archive: zipfile.ZipFile, abi: str) -> dict:
    """Что из APK идёт в систему: имя внутри каталога Magisk → содержимое.

    Библиотеки в APK — на самом деле исполняемые файлы: libmagisk64.so
    становится magisk64, и так далее.
    """
    files: dict[str, bytes] = {}
    libraries = f"lib/{abi}/"
    for info in archive.infolist():
        name = info.filename
        if info.is_dir():
            continue
        if name.startswith(libraries):
            match = re.fullmatch(r"lib([a-z0-9]+)\.so", name[len(libraries):])
            if match:
                files[match.group(1)] = archive.read(info)
        elif name.startswith("assets/chromeos/"):
            leaf = name[len("assets/chromeos/"):]
            if re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9._-]*", leaf):
                files["chromeos/" + leaf] = archive.read(info)
        elif name.startswith("assets/") and name[len("assets/"):] in MAGISK_ASSETS:
            files[name[len("assets/"):]] = archive.read(info)
    missing = [name for name in (*MAGISK_NEEDED, *MAGISK_ASSETS) if name not in files]
    if missing:
        fail("в файле Magisk не хватает: " + ", ".join(missing))
    return files


def magisk_unplug() -> int:
    """Убирает Magisk из системы Android. Отдаёт, сколько нашлось остатков.

    Первыми — строки запуска: без них Magisk не стартует, что бы ни
    осталось лежать, если работу оборвут на середине.
    """
    removed = 0
    init = overlay_path("system", "etc", "init")
    # Только в настоящих каталогах оверлея: по ссылке (ей там взяться
    # неоткуда, кроме как от самого root) удалять пришлось бы за его
    # пределами.
    if real_dir_below(overlay_path(), init):
        rc = os.path.join(init, "bootanim.rc")
        try:
            handle = os.open(rc, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(handle, encoding="utf-8", errors="replace") as reader:
                ours = "magisk" in reader.read()
        except OSError:
            ours = False
        if ours:
            # Чужой bootanim.rc без единого слова о Magisk — не наша забота.
            removed += remove_path(rc)
        removed += remove_path(rc + ".gz")
        removed += remove_path(os.path.join(init, "magisk"))
    upper = os.path.join(WAYDROID_DIR, "overlay_rw")
    for relative in MAGISK_UPPER:
        path = os.path.join(upper, relative)
        if real_dir_below(upper, os.path.dirname(path)):
            removed += remove_path(path)
    return removed


def magisk_to_data(home: str, files: dict) -> bool:
    """Кладёт файлы Magisk ещё и в данные Android, в /data/adb/magisk.

    Оттуда их берёт сам Magisk: без них его приложение при первом открытии
    требует «дополнительную настройку». Права — исполняемые: waydroid_script
    оставлял 0644, и Zygisk из-за этого молча не запускался.
    """
    adb = data_adb(home, 1, create=True)
    if adb is None:
        return False
    try:
        remove_at(adb, "magisk")
        try:
            os.mkdir("magisk", 0o755, dir_fd=adb)
            target = os.open(
                "magisk", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=adb
            )
            try:
                os.fchown(target, 0, 0)
                for name, data in files.items():
                    folder, _slash, leaf = name.rpartition("/")
                    if not folder:
                        write_at(target, leaf, data, 0o755)
                        continue
                    with suppress(FileExistsError):
                        os.mkdir(folder, 0o755, dir_fd=target)
                    inner = os.open(
                        folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=target
                    )
                    try:
                        os.fchown(inner, 0, 0)
                        write_at(inner, leaf, data, 0o755)
                    finally:
                        os.close(inner)
            finally:
                os.close(target)
        except OSError:
            # Половина набора хуже пустого места: без каталога Magisk хотя бы
            # сам попросит «дополнительную настройку», а с неполным — молча
            # работает как попало.
            with suppress(OSError):
                remove_at(adb, "magisk")
            raise
    finally:
        os.close(adb)
    return True


def cmd_magisk_install(args) -> None:
    """Ставит root (Magisk) в систему Android — из проверенного файла."""
    abi = MAGISK_ABIS.get(os.uname().machine)
    if abi is None:
        fail("root через Magisk ставится только на 64-разрядных x86 и ARM")
    blob = read_whole(args.apk, "файл Magisk", MAGISK_LIMIT)
    build = MAGISK_APKS.get(hashlib.sha256(blob).hexdigest())
    if build is None:
        fail("этот файл — не та сборка Magisk, которой Merci доверяет: под root "
             "ставится только файл с известной контрольной суммой")
    try:
        files = magisk_files(zipfile.ZipFile(io.BytesIO(blob)), abi)
    except zipfile.BadZipFile as failure:
        fail(f"файл Magisk повреждён: {failure}")
    # Сам APK тоже едет в систему: из него Magisk ставит своё приложение.
    files["magisk.apk"] = blob

    uninterruptible()
    service_was_up = release_image()
    try:
        # В выключенном оверлее Magisk лежал бы без дела, а Waydroid
        # выключает оверлей сам — после первой же неудачи монтирования.
        fix_overlays()
        sweep_staged()
        print(f"ставим {build} в систему Android", flush=True)
        in_data = False
        try:
            magisk_unplug()
            if not real_dir_below(overlay_path(), overlay_path("sbin"), create=True):
                raise OSError("в оверлее на месте каталога sbin стоит ссылка или файл")
            target = overlay_path("system", "etc", "init", "magisk")
            for name, data in files.items():
                _install_file(
                    os.path.join(target, *name.split("/")), data, 0o755, ANDROID_SHELL_GID
                )
            chromeos = os.path.join(target, "chromeos")
            if os.path.isdir(chromeos):
                os.chown(chromeos, 0, ANDROID_SHELL_GID)
            try:
                in_data = magisk_to_data(args.home, files)
            except OSError as failure:
                print(f"данные Android: {failure.strerror or failure}", flush=True)
            if not in_data:
                # Данных ещё нет, они вынесены на другой диск или запись не
                # удалась — root от этого работать не перестанет.
                print("в данные Android файлы Magisk не положены: его приложение при "
                      "первом открытии попросит «дополнительную настройку»", flush=True)
            # Включаем последним: сорвись что-то выше, Magisk остался бы
            # просто лежать, не запускаясь, а Android грузился бы как раньше.
            rc = overlay_path("system", "etc", "init", "bootanim.rc")
            _install_file(rc + ".gz", gzip.compress(BOOTANIM_RC.encode(), mtime=0), 0o644)
            _install_file(rc, (BOOTANIM_RC + MAGISK_RC).encode(), 0o644)
        except OSError as failure:
            # Половина Magisk хуже, чем никакого: убираем, что успели.
            with suppress(OSError):
                magisk_unplug()
            fail(f"не удалось записать файлы Magisk ({failure.strerror or failure}) — "
                 "root не установлен, Android загрузится как раньше")
    finally:
        if service_was_up:
            container_service("start")
    print(json.dumps({"magisk": "installed", "build": build, "files": len(files),
                      "data": in_data}))


def image_has_magisk() -> bool:
    """Записан ли Magisk в сам образ Android, а не в оверлей.

    Так выходило у waydroid_script, если Waydroid к тому времени выключил
    оверлеи: скрипт подключал образ и писал прямо в него. Оттуда Magisk
    удалением файлов оверлея не убрать — и сказать «root убран» было бы
    неправдой. Образ читаем как есть, не подключая.
    """
    tool = shutil.which("debugfs")
    if tool is None or not os.path.isfile(CFG_PATH):
        return False
    images = cfg_value(load_cfg(), "images_path", WAYDROID_DIR + "/images")
    image = os.path.join(os.path.realpath(images), "system.img")
    if not os.path.isfile(image):
        return False
    found = run([tool, "-c", "-R", "stat /system/etc/init/magisk", image], check=False)
    return found.returncode == 0 and "Inode:" in found.stdout


def cmd_magisk_remove(args) -> None:
    """Убирает root (Magisk) из системы Android и из данных всех окон.

    Ничего не качает и ничего чужого не исполняет: удаление — это удаление
    файлов. Запускать можно сколько угодно раз: что уже убрано, пропускается.
    """
    uninterruptible()
    service_was_up = release_image()
    try:
        print("убираем Magisk из системы Android", flush=True)
        try:
            system = magisk_unplug()
        except OSError as failure:
            fail(f"не удалось убрать файлы Magisk: {failure.strerror or failure}")
        # Пустой /sbin заводили под Magisk; непустой — не наш.
        with suppress(OSError):
            os.rmdir(overlay_path("sbin"))
            system += 1
        data = 0
        # Система у окон общая, а данные у каждого свои.
        for number in sorted({1, *known_instances()}):
            adb = data_adb(args.home, number)
            if adb is None:
                continue
            try:
                for name in ("magisk.db", "magisk"):
                    data += remove_at(adb, name)
            except OSError as failure:
                print(f"данные окна {number}: {failure.strerror or failure}", flush=True)
            finally:
                os.close(adb)
    finally:
        if service_was_up:
            container_service("start")
    if image_has_magisk():
        fail("Magisk записан ещё и в сам образ Android (он ставился, когда оверлеи "
             "Waydroid были выключены), и оттуда удалением файлов его не убрать. "
             "Поможет замена образа: «Обновления» — обновить или вернуть образ, "
             "либо `sudo waydroid init -f`.")
    if system or data:
        print("root убран", flush=True)
    else:
        print("Magisk не найден — убирать нечего", flush=True)
    print(json.dumps({"magisk": "removed", "system": system, "data": data}))


def cmd_adb_key(args) -> None:
    """Кладёт ключ хоста в доверенные для указанного окна.

    Нужно, когда контейнер отвечает «unauthorized»: подтвердить запрос
    внутри Android некому — окна с вопросом ещё нет, а из основного
    контейнера строка ro.adb.secure=0 может пропасть при переписывании
    базовых свойств.
    """
    if not 1 <= args.number <= MAX_INSTANCE:
        fail(f"номер окна — от 1 до {MAX_INSTANCE}")
    ensure_adb_key(args.home, args.number)
    if lxc_state(args.number) == "RUNNING":
        run(
            ["lxc-attach", "-P", LXC_PATH, "-n", container_name(args.number), "--",
             "/system/bin/sh", "-c",
             "export PATH=/system/bin:/vendor/bin; stop adbd; sleep 1; start adbd"],
            check=False,
        )
    print(json.dumps({"number": args.number, "key": "ok"}))


def cmd_secure_adb(args) -> None:
    """Возвращает adbd контейнеров проверку ключа и раздаёт ключ хоста.

    Раньше Merci ставила ro.adb.secure=0, чтобы не упираться в окно
    «Разрешить отладку?» внутри Android. Но без проверки ключа к adbd на
    порту 5555 подключается кто угодно: любое приложение внутри самого
    контейнера (а Merci существует ради запуска чужих APK) и любая машина
    в локальной сети, знающая маршрут до моста. Это оболочка в Android без
    пароля — установка приложений, чтение данных, снимки экрана.

    Ключ хоста, положенный в доверенные заранее, даёт то же удобство без
    этой дыры: Merci подключается молча, остальные — нет.

    С ``--users`` заодно разрешает несколько пользователей Android:
    оба свойства читаются при загрузке, и перезапуск нужен один.
    """
    if not read_public_key(args.home):
        fail(
            "у пользователя нет ключа adb (~/.android/adbkey.pub): "
            "запустите adb один раз и повторите"
        )
    stopped = []
    for number in known_instances():
        if number != 1 and lxc_state(number) == "RUNNING":
            stop_container(number)
            stopped.append(number)

    wanted = {"ro.adb.secure": "1"}
    if args.users:
        if not 2 <= args.users <= 8:
            fail("число пользователей Android — от 2 до 8")
        wanted["fw.max_users"] = str(args.users)
    else:
        # Прежние версии писали предел пользователей только в базовые
        # свойства, а их Waydroid пересобирает из своего конфига при
        # обновлении образа. Переносим уже заданное значение туда же, иначе
        # после обновления перестанут создаваться новые профили.
        existing = base_properties().get("fw.max_users", "")
        if existing.isdigit() and int(existing) > 1:
            wanted["fw.max_users"] = existing

    # Сначала ключи, потом свойства. Сорвись раздача ключа после того, как
    # проверка уже включена, Merci сама осталась бы за дверью.
    problems = []
    try:
        ensure_adb_key(args.home, 1)
    except SystemExit:
        fail("не удалось положить ключ adb в основной контейнер — проверку не включаем")
    for number in known_instances():
        if number == 1:
            continue
        try:
            ensure_adb_key(args.home, number)
        except SystemExit:
            # Одно окно с испорченными данными не должно срывать всё: ключ
            # ему положит следующий запуск окна.
            problems.append(number)

    # В оба файла: waydroid_base.prop Waydroid пересобирает при обновлении
    # образа из раздела [properties] своего конфига.
    write_base_properties(wanted)
    write_cfg_properties(wanted)

    # Через container_service: служба контейнера бывает не только у systemd
    # (OpenRC, runit), и голый systemctl там молча ничего не перезапускал.
    restarted = container_service("restart")
    print(json.dumps({
        "adb": "secure", "stopped": stopped, "users": wanted.get("fw.max_users", ""),
        "без_ключа": problems, "restarted": restarted,
    }))


def cmd_restart_container(args) -> None:
    """Перезапускает службу контейнера Waydroid целиком.

    Иногда сессия поднимается, а Android внутри застревает на загрузке:
    адреса по DHCP нет, службы пакетов нет, сторож убивает system_server
    по кругу. Перезапуск сессии тут не помогает — помогает перезапуск
    самой службы.
    """
    # Дополнительные окна держат прежнее монтирование образа: при них
    # основной контейнер не поднимется вовсе, а оверлей с транслятором
    # Waydroid отключит насовсем. Поэтому — погасить всё, вернуть оверлей и
    # только потом перезапустить службу; одним заходом, то есть с одним
    # запросом пароля.
    stop_everything()
    fix_overlays()
    if not container_service("restart"):
        fail("служба контейнера Waydroid не перезапустилась")
    print(json.dumps({"container": "restarted"}))


def cmd_list(args) -> None:
    items = []
    for number in known_instances():
        items.append(
            {
                "number": number,
                "state": lxc_state(number),
                "ip": instance_ip(number),
                "data": data_dir(args.home, number),
            }
        )
    print(json.dumps(items))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", required=True, help="домашний каталог пользователя")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler in (
        ("create", cmd_create),
        ("start", cmd_start),
        ("stop", cmd_stop),
        ("restart", cmd_restart),
        ("remove", cmd_remove),
    ):
        one = sub.add_parser(name)
        one.add_argument("number", type=int)
        if name == "create":
            one.add_argument("--start", action="store_true")
        one.set_defaults(func=handler)

    listing = sub.add_parser("list")
    listing.set_defaults(func=cmd_list)

    restart = sub.add_parser("restart-container")
    restart.set_defaults(func=cmd_restart_container)

    secure = sub.add_parser("secure-adb")
    secure.add_argument("--users", type=int, default=0)
    secure.set_defaults(func=cmd_secure_adb)

    key = sub.add_parser("adb-key")
    key.add_argument("number", type=int)
    key.set_defaults(func=cmd_adb_key)

    size = sub.add_parser("set-size")
    size.add_argument("width", type=int)
    size.add_argument("height", type=int)
    size.set_defaults(func=cmd_set_size)

    apply_size = sub.add_parser("apply-size")
    apply_size.add_argument("width", type=int)
    apply_size.add_argument("height", type=int)
    apply_size.set_defaults(func=cmd_apply_size)

    for name, handler in (
        ("start-many", cmd_start_many),
        ("stop-many", cmd_stop_many),
        ("restart-many", cmd_restart_many),
        ("remove-many", cmd_remove_many),
    ):
        many = sub.add_parser(name)
        many.add_argument("numbers", type=int, nargs="*")
        many.set_defaults(func=handler)

    overlays = sub.add_parser("fix-overlays")
    overlays.set_defaults(func=cmd_fix_overlays)

    bridge = sub.add_parser("bridge-install")
    bridge.add_argument("archive")
    bridge.set_defaults(func=cmd_bridge_install)

    magisk = sub.add_parser("magisk-install")
    magisk.add_argument("apk")
    magisk.set_defaults(func=cmd_magisk_install)

    unroot = sub.add_parser("magisk-remove")
    unroot.set_defaults(func=cmd_magisk_remove)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("width", type=int, nargs="?", default=0)
    prepare.add_argument("height", type=int, nargs="?", default=0)
    prepare.set_defaults(func=cmd_prepare)

    def keeps_settings(command) -> None:
        """Настройки Merci, которые команда вернёт на место после работы."""
        command.add_argument("--width", type=int, default=0)
        command.add_argument("--height", type=int, default=0)
        command.add_argument("--subsurface", choices=("", "true", "false"), default="")
        command.add_argument("--max-users", type=int, default=0)

    images = sub.add_parser("update-images")
    images.add_argument("--download-mb", type=int, default=0)
    images.add_argument("--no-backup", action="store_true")
    keeps_settings(images)
    images.set_defaults(func=cmd_update_images)

    restore = sub.add_parser("restore-images")
    keeps_settings(restore)
    restore.set_defaults(func=cmd_restore_images)

    drop = sub.add_parser("drop-image-backup")
    drop.set_defaults(func=cmd_drop_image_backup)

    refresh = sub.add_parser("refresh-config")
    keeps_settings(refresh)
    refresh.set_defaults(func=cmd_refresh_config)

    subsurface = sub.add_parser("subsurface")
    subsurface.add_argument("value", choices=("true", "false"))
    subsurface.set_defaults(func=cmd_subsurface)

    for name, handler in (("nvidia-setup", cmd_nvidia_setup), ("nvidia-update", cmd_nvidia_update)):
        nvidia = sub.add_parser(name)
        if name == "nvidia-update":
            nvidia.add_argument("--package", required=True)
            nvidia.add_argument("--sha256", default="")
        nvidia.add_argument("--mode", default="")
        nvidia.add_argument("--refresh", default="")
        keeps_settings(nvidia)
        nvidia.set_defaults(func=handler)

    args = parser.parse_args()
    if os.geteuid() != 0:
        fail("помощнику нужны права root")
    # Маску прав задаём сами, какой бы она ни была у вызвавшего. Каталоги
    # для контейнера создаёт LXC — от нашего имени и с нашей маской; при
    # маске 077 каталог с сокетом Wayland (/run/xdg) выходит закрытым для
    # всех, кроме root, композитор Android не может открыть экран, и окно
    # остаётся без картинки и без сети.
    os.umask(0o022)
    args.home = check_home(args.home)
    lock = take_lock(args.command)
    try:
        args.func(args)
    finally:
        if lock is not None:
            lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
