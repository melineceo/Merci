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
import fcntl
import glob
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from contextlib import suppress

LXC_PATH = "/var/lib/waydroid/lxc"
BASE_NAME = "waydroid"
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


def container_name(number: int) -> str:
    return BASE_NAME if number == 1 else f"{BASE_NAME}{number}"


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
    return target


def ensure_data(home: str, number: int) -> str:
    """Пустой каталог данных: Android разложит его при первой загрузке."""
    data = data_dir(home, number)
    os.makedirs(data, exist_ok=True)
    os.chown(os.path.dirname(data), ANDROID_SYSTEM_UID, ANDROID_SYSTEM_UID)
    os.chown(data, ANDROID_SYSTEM_UID, ANDROID_SYSTEM_UID)
    return data


def ensure_adb_key(home: str, number: int) -> None:
    """Ключ хоста в списке доверенных.

    Без него adb отвечает «device unauthorized», а подтвердить запрос
    внутри Android некому: окна с диалогом ещё нет.
    """
    public_key = os.path.join(home, ".android/adbkey.pub")
    if not os.path.exists(public_key):
        # Ключ появляется при первом запуске adb; не наша забота его делать.
        return
    adb_dir = os.path.join(data_dir(home, number), "misc/adb")
    os.makedirs(adb_dir, exist_ok=True)
    keys = os.path.join(adb_dir, "adb_keys")
    with open(public_key, encoding="utf-8") as handle:
        content = handle.read().strip()
    existing = ""
    if os.path.exists(keys):
        with open(keys, encoding="utf-8") as handle:
            existing = handle.read()
    if content not in existing:
        with open(keys, "a", encoding="utf-8") as handle:
            handle.write(content + "\n")
    os.chown(adb_dir, ANDROID_SYSTEM_UID, ANDROID_SHELL_GID)
    os.chmod(adb_dir, 0o750)
    os.chown(keys, ANDROID_SYSTEM_UID, ANDROID_SHELL_GID)
    os.chmod(keys, 0o640)


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
    return sorted(set(numbers))


# -- команды -------------------------------------------------------------


def cmd_create(args) -> None:
    if not 2 <= args.number <= MAX_INSTANCE:
        fail(f"номер экземпляра — от 2 до {MAX_INSTANCE}")
    if not os.path.ismount("/var/lib/waydroid/rootfs"):
        fail("основной контейнер не запущен — образ Android не подключён")

    ensure_binder(args.number)
    ensure_data(args.home, args.number)
    make_config(args.number, args.home)
    ensure_adb_key(args.home, args.number)
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


def start_container(number: int, home: str) -> None:
    ensure_binder(number, fresh=True)
    make_config(number, home)
    ensure_adb_key(home, number)
    subprocess.Popen(
        ["lxc-start", "-P", LXC_PATH, "-n", container_name(number), "-F", "--", "/init"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def cmd_start(args) -> None:
    name = container_name(args.number)
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
    start_container(args.number, args.home)

    # Первый запуск иногда виснет: SurfaceFlinger внутри застревает на
    # инициализации графики, system_server за ним блокируется в
    # DisplayManagerService, и сторож убивает его по кругу. Сам контейнер
    # из этого не выйдет — снаружи видно только «окно не получило адрес».
    # Перезапуск лечит, поэтому делаем его здесь же: второй запрос пароля
    # человека бы не обрадовал.
    ip = wait_address(args.number, 90)
    перезапуск = False
    if not ip:
        перезапуск = True
        run(["lxc-stop", "-P", LXC_PATH, "-n", name, "-k"], check=False)
        for _ in range(30):
            if lxc_state(args.number) != "RUNNING":
                break
            time.sleep(1)
        time.sleep(3)
        start_container(args.number, args.home)
        ip = wait_address(args.number, 150)
    print(json.dumps({
        "number": args.number,
        "state": "RUNNING" if ip else "STARTING",
        "ip": ip,
        "restarted": перезапуск,
    }))


def cmd_stop(args) -> None:
    run(["lxc-stop", "-P", LXC_PATH, "-n", container_name(args.number), "-k"], check=False)
    print(json.dumps({"number": args.number, "state": lxc_state(args.number)}))


def cmd_restart(args) -> None:
    """Гасит окно и поднимает заново — одним заходом под root.

    Двумя командами это стоило бы двух запросов пароля: sudo его здесь не
    запоминает. А перезапуск нужен как раз тогда, когда в окне заклинило
    картинку, и человеку меньше всего хочется вводить пароль дважды.
    """
    if args.number == 1:
        fail("основное окно перезапускается вместе с сессией")
    run(["lxc-stop", "-P", LXC_PATH, "-n", container_name(args.number), "-k"], check=False)
    for _ in range(30):
        if lxc_state(args.number) != "RUNNING":
            break
        time.sleep(1)
    # Контейнер числится остановленным раньше, чем отпускает устройства
    # binder; без этой паузы новый запуск получает их с прежними ссылками.
    time.sleep(3)
    args.numbers = [args.number]
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
            run(["lxc-stop", "-P", LXC_PATH, "-n", container_name(number), "-k"], check=False)
            stopped.append(number)
    cmd_fix_overlays(args)
    if getattr(args, "width", 0) and getattr(args, "height", 0):
        cmd_set_size(args)
    print(json.dumps({"stopped": stopped}))


def cmd_remove(args) -> None:
    if args.number == 1:
        fail("основной контейнер удалять нельзя")
    cmd_stop(args)
    target = os.path.join(LXC_PATH, container_name(args.number))
    if os.path.isdir(target):
        shutil.rmtree(target)
    data = os.path.dirname(data_dir(args.home, args.number))
    if os.path.isdir(data) and f"waydroid{args.number}" in data:
        shutil.rmtree(data)
    print(json.dumps({"number": args.number, "state": "ABSENT"}))


def cmd_fix_overlays(args) -> None:
    """Возвращает mount_overlays = True в waydroid.cfg.

    Waydroid выключает оверлей после первой же неудачи монтирования и
    больше не пробует, а в оверлее лежит транслятор ARM. Неудача же
    случается сама собой, если в момент старта основной сессии работают
    дополнительные контейнеры: они держат прежнее монтирование образа.
    """
    path = "/var/lib/waydroid/waydroid.cfg"
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as failure:
        fail(f"не прочитать {path}: {failure}")
    fixed = re.sub(r"(?m)^mount_overlays\s*=\s*False\s*$", "mount_overlays = True", text)
    if fixed != text:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(fixed)
    print(json.dumps({"overlays": "True"}))


def cmd_set_size(args) -> None:
    """Записывает размер окна в общие свойства контейнеров.

    waydroid_base.prop читают ВСЕ контейнеры при старте — и уже созданные,
    и те, что появятся позже. Поэтому размер, записанный сюда, становится
    общим по-настоящему, а не «для этого окна, пока его не пересоздали».
    """
    path = "/var/lib/waydroid/waydroid_base.prop"
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as failure:
        fail(f"не прочитать {path}: {failure}")

    density = max(120, min(320, round(180 * args.height / 1080)))
    wanted = {
        "persist.waydroid.width": str(args.width),
        "persist.waydroid.height": str(args.height),
        "persist.waydroid.lcd_density": str(density),
    }
    kept = [
        line
        for line in lines
        if line.split("=", 1)[0].strip() not in wanted
    ]
    kept.extend(f"{key}={value}" for key, value in wanted.items())

    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(kept) + "\n")

    write_cfg_properties(wanted)
    print(json.dumps({"width": args.width, "height": args.height, "density": density}))


def write_cfg_properties(wanted: dict) -> None:
    """Дублирует свойства в waydroid.cfg — чтобы они пережили пересборку.

    waydroid_base.prop Waydroid собирает заново при каждом обновлении
    образа: из своих умолчаний плюс раздел [properties] отсюда. Всё,
    что записано только в base.prop, после такого обновления исчезает — так
    у человека и пропадал выбранный размер окон, причём молча.
    """
    path = "/var/lib/waydroid/waydroid.cfg"
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
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(kept) + "\n")


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
            run(["lxc-stop", "-P", LXC_PATH, "-n", container_name(number), "-k"], check=False)
            stopped.append(number)
    print(json.dumps({"stopped": stopped}))


def cmd_start_many(args) -> None:
    """Поднимает несколько окон одним заходом — снова ради одного пароля."""
    started = []
    зависли = []
    for number in args.numbers:
        if lxc_state(number) == "RUNNING":
            started.append(number)
            continue
        start_container(number, args.home)
        started.append(number)
    # Проверяем сеть у всех разом: висит обычно один, и перезапускать
    # стоит только его.
    for number in list(started):
        if wait_address(number, 90):
            continue
        run(["lxc-stop", "-P", LXC_PATH, "-n", container_name(number), "-k"], check=False)
        time.sleep(3)
        start_container(number, args.home)
        if not wait_address(number, 150):
            зависли.append(number)
    print(json.dumps({"started": started, "без_сети": зависли}))


def cmd_adb_key(args) -> None:
    """Кладёт ключ хоста в доверенные для указанного окна.

    Нужно, когда контейнер отвечает «unauthorized»: подтвердить запрос
    внутри Android некому — окна с вопросом ещё нет, а из основного
    контейнера строка ro.adb.secure=0 может пропасть при переписывании
    базовых свойств.
    """
    ensure_adb_key(args.home, args.number)
    if lxc_state(args.number) == "RUNNING":
        run(
            ["lxc-attach", "-P", LXC_PATH, "-n", container_name(args.number), "--",
             "/system/bin/sh", "-c",
             "export PATH=/system/bin:/vendor/bin; stop adbd; sleep 1; start adbd"],
            check=False,
        )
    print(json.dumps({"number": args.number, "key": "ok"}))


def cmd_restart_container(args) -> None:
    """Перезапускает службу контейнера Waydroid целиком.

    Иногда сессия поднимается, а Android внутри застревает на загрузке:
    адреса по DHCP нет, службы пакетов нет, сторож убивает system_server
    по кругу. Перезапуск сессии тут не помогает — помогает перезапуск
    самой службы.
    """
    run(["systemctl", "restart", "waydroid-container.service"], check=False)
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
        one.set_defaults(func=handler)

    listing = sub.add_parser("list")
    listing.set_defaults(func=cmd_list)

    restart = sub.add_parser("restart-container")
    restart.set_defaults(func=cmd_restart_container)

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

    start_many = sub.add_parser("start-many")
    start_many.add_argument("numbers", type=int, nargs="*")
    start_many.set_defaults(func=cmd_start_many)

    overlays = sub.add_parser("fix-overlays")
    overlays.set_defaults(func=cmd_fix_overlays)

    prepare = sub.add_parser("prepare")
    prepare.add_argument("width", type=int, nargs="?", default=0)
    prepare.add_argument("height", type=int, nargs="?", default=0)
    prepare.set_defaults(func=cmd_prepare)

    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
