"""Всё, что касается Waydroid: проверка готовности, установка и запуск APK.

Waydroid — служба хоста: контейнер с полноценным Android, свой bionic, свой
ART и binder в ядре. Именно поэтому в него можно подставить
libndk_translation и получить настоящую трансляцию ARM64 → x86_64, а рисует
при этом хостовый драйвер. Во flatpak его не завернуть, поэтому Merci с ним
разговаривает через flatpak-spawn --host.

Пароль root Merci не видит и не запрашивает: шаги с правами идут через
sudo -A, пароль спрашивает системный диалог и отдаёт его напрямую sudo.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from contextlib import suppress
from dataclasses import dataclass, field

from gi.repository import Gio, GLib

from . import cloner
from .hostexec import Cache, host_argv, in_thread, run as _run, succeeds as _ok
from .i18n import tr
from .library import Entry, data_root


class WaydroidError(Exception):
    pass


# Состояние меняется редко, а спрашивают его на каждый показ карточки.
_state_cache = Cache(ttl=4.0)
_bridge_cache = Cache(ttl=10.0)


# -- состояние -----------------------------------------------------------


def status(use_cache: bool = True) -> tuple[bool, str]:
    """(готов ли Waydroid к запуску приложений, что показать пользователю).

    Блокирующая: вызывать только из потока, см. status_async.
    """
    cached = _state_cache.get() if use_cache else None
    if cached is not None:
        return cached

    result = _probe()
    _state_cache.set(result)
    return result


def _probe() -> tuple[bool, str]:
    # Описание возвращаем русским исходником, без перевода: по нему plan()
    # решает, нужен ли шаг загрузки образа. Переведи мы здесь — сравнение
    # перестало бы совпадать при английском языке, и шаг молча пропал бы.
    # Переводит тот, кто показывает.
    if not _ok(["sh", "-c", "command -v waydroid"]):
        return False, "не установлен"
    result = _run(["waydroid", "status"])
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        return False, lines[-1] if lines else "недоступен"

    # waydroid отвечает по-разному в зависимости от версии и подкоманды:
    # "Session:\tRUNNING", "UNINITIALIZED" либо фразой
    # 'Waydroid is not initialized, run "waydroid init"'.
    output = result.stdout.upper()
    if "NOT INITIALIZED" in output or "UNINITIALIZED" in output:
        return False, "образ Android не загружен"
    if "RUNNING" in output:
        return True, "сессия запущена"
    if "STOPPED" in output:
        return False, "сессия остановлена"
    return False, "состояние неясно"


def _prop(name: str) -> str:
    """Одно свойство контейнера или пустая строка, если спросить не вышло.

    waydroid отвечает нулевым кодом и на «Failed to get service
    waydroidplatform» — служба Android внутри контейнера иногда не отвечает,
    хотя сам контейнер запущен. Принять такой ответ за значение — значит
    уверенно показать неправду; пустая строка честнее.
    """
    result = _run(["waydroid", "prop", "get", name])
    value = result.stdout.strip()
    if result.returncode != 0 or not value:
        return ""
    low = value.lower()
    if any(mark in low for mark in ("failed", "error", "not initialized", "trying again")):
        return ""
    return value


def _bridge_from_config() -> str:
    """Имя транслятора из файлов настройки контейнера.

    Запасной путь на случай, когда служба Android внутри не отвечает:
    ro.* свойства всё равно берутся из этих файлов при загрузке, так что
    ответ отсюда не менее верен, чем от самого контейнера.
    """
    result = _run(
        [
            "sh",
            "-c",
            "cat /var/lib/waydroid/waydroid_base.prop "
            "\"$HOME\"/.local/share/waydroid/waydroid.cfg 2>/dev/null",
        ]
    )
    for line in result.stdout.splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "ro.dalvik.vm.native.bridge":
            return value.strip()
    return ""


def native_bridge() -> str:
    """Имя транслятора ARM64 → x86_64 внутри контейнера, если он настроен."""
    cached = _bridge_cache.get()
    if cached is not None:
        return cached
    value = _prop("ro.dalvik.vm.native.bridge") or _bridge_from_config()
    # За имя транслятора принимаем только то, что им выглядит.
    if value in ("", "0", "none") or " " in value or not value.endswith(".so"):
        value = ""
    # Свойства мало: сама библиотека лежит в оверлее Waydroid, а тот
    # отключается при первой же неудаче монтирования и запись об этом
    # остаётся только в waydroid.cfg. Тогда свойство обещает трансляцию,
    # которой нет, а ARM-приложения падают с UnsatisfiedLinkError —
    # «dlopen ... is for EM_AARCH64 instead of EM_X86_64».
    if value and adb_available():
        with suppress(WaydroidError):
            adb_connect()
            # Команду отдаём одной строкой: adb склеивает аргументы и
            # заново делит их на своей стороне, поэтому вложенный sh -c
            # терял путь, и ls показывал корень.
            found = _adb(["shell", f"ls /system/lib64/{value}"], timeout=60)
            if value not in found.stdout:
                value = ""
    _bridge_cache.set(value)
    return value


def overlays_disabled() -> bool:
    """Отключён ли оверлей Waydroid — тот, в котором живёт транслятор.

    Waydroid выключает его сам, если монтирование однажды не удалось, и
    больше не пробует. Снаружи это выглядит как «ARM-приложения перестали
    запускаться», причём во всех контейнерах разом.
    """
    for line in _host_file("/var/lib/waydroid/waydroid.cfg").splitlines():
        if line.strip().lower().startswith("mount_overlays"):
            return line.split("=", 1)[1].strip().lower() == "false"
    return False


def overlay_step() -> Step:
    """Возвращает оверлей на место и перезапускает сессию."""
    helper = askpass_helper()
    sudo = f"env SUDO_ASKPASS={_quote(helper)} sudo -A " if helper else "sudo "
    script = (
        f"{sudo}sed -i 's/^mount_overlays = False/mount_overlays = True/' "
        "/var/lib/waydroid/waydroid.cfg; "
        "waydroid session stop >/dev/null 2>&1; sleep 3; "
        "setsid waydroid session start >/dev/null 2>&1 & sleep 25; true"
    )
    return Step(
        key="overlay",
        title=tr("Вернуть транслятор ARM64"),
        hint=tr("Waydroid отключил оверлей, а в нём libhoudini — контейнер перезапустится"),
        argv=["sh", "-c", script],
        root=True,
        minutes=2,
    )


def forget_state() -> None:
    """Сбросить кеш — после установки или запуска сессии."""
    _state_cache.clear()
    _bridge_cache.clear()


def state_async(needs_bridge: bool, callback) -> None:
    """Опрашивает Waydroid в потоке: (готов, описание, транслятор, сеть)."""

    def work():
        ready, detail = status()
        bridge = native_bridge() if (ready and needs_bridge) else ""
        network = not firewall_blocks_container() if ready else True
        return ready, detail, bridge, network

    def done(result, error):
        if error is not None or result is None:
            callback(False, "состояние неизвестно", "", True)
            return
        callback(*result)

    in_thread(work, done)


def plan_async(needs_bridge: bool, callback, monitor: tuple[int, int] = (0, 0)) -> None:
    """Строит план установки в потоке: там десяток обращений к хосту."""
    in_thread(
        lambda: plan(needs_bridge, monitor),
        lambda result, error: callback(result or []),
    )


def ensure_android_user(entry: Entry) -> int:
    """Номер профиля Android для этой записи, при надобности заводит его.

    Отрицательное значение в записи означает «нужен свой профиль, номер ещё
    не выдан»: APK добавляют и при выключенном контейнере, а завести
    пользователя можно только в работающем.
    """
    users = android_users()
    if entry.profile and entry.profile in users:
        return entry.profile
    number = create_android_user(entry.name)
    entry.android_user = number
    return number


def install_and_launch_async(
    entry: Entry, callback, on_stage=None, multiuser: bool = False
) -> None:
    """Ставит APK в контейнер (если его там нет) и запускает.

    Переустанавливать каждый раз незачем: APK на полтораста мегабайт
    ставится десятки секунд, и всё это время кажется, что ничего не
    происходит.

    В многопользовательском режиме запись может жить в своём профиле
    Android — тогда и установка, и запуск идут через adb с номером этого
    профиля, а контейнер сперва на него переключается.
    """

    def stage(text):
        if on_stage is not None:
            GLib.idle_add(lambda: (on_stage(text), False)[1])

    def check_build() -> None:
        """Убеждается, что в контейнере стоит именно эта сборка.

        Профили делят между собой код приложения, поэтому проверка одна на
        весь контейнер: если установлено что-то другое, ни один профиль эту
        сборку не откроет — Android даст поставить её только вместо той.
        """
        current, other = installed_build(entry.package)
        if current and entry.file_hash and current != entry.file_hash:  # None — не знаем
            raise InstallConflict(
                f"в контейнере стоит другая сборка {entry.package}"
                + (f" (версия {other})" if other else "")
            )

    def work():
        ensure_container_alive(stage)
        if multiuser and entry.profile:
            ensure_session(stage)
            stage(tr("Готовим профиль Android…"))
            user = ensure_android_user(entry)
            check_build()
            if entry.package not in packages_for_user(user):
                stage(tr("Устанавливаем в профиль…"))
                install_for_user(entry, user)
            stage(tr("Переключаем профиль…"))
            switch_android_user(user)
            # Роль браузера у каждого профиля своя: без этого ссылки из игры
            # открывались внутренним браузером Android, а не на хосте.
            if URLFORWARD_PACKAGE in packages_for_user(0):
                set_browser_role(user)
            stage(tr("Открываем…"))
            launch_for_user(entry, user, stage)
            return True

        # Запись запускается в основном профиле, а контейнер мог остаться в
        # чужом — от прошлого запуска или от выключенного с тех пор режима
        # MultiUser. Тогда приложение честно стартует в нулевом профиле, а
        # на экране виден чужой: окна нет вовсе. Возвращаем контейнер сами,
        # и делаем это независимо от настройки — состояние-то общее.
        if adb_available():
            try:
                if current_android_user() > 0:
                    stage(tr("Возвращаемся в основной профиль…"))
                    switch_android_user(0)
            except WaydroidError:
                pass

        check_build()
        if entry.package not in installed_packages():
            stage(tr("Устанавливаем в контейнер…"))
            install_apk(entry)
            _installed_cache.clear()
        stage(tr("Открываем…"))
        launch_apk(entry, stage)
        return True

    in_thread(work, lambda _result, error: callback(error))


def uninstall_everywhere(package: str) -> None:
    """Убирает пакет из всех профилей сразу.

    Именно так, а не по профилям: код у пакета один на всё устройство, и
    поставить другую сборку можно только сняв прежнюю целиком.
    """
    if adb_available():
        try:
            adb_connect()
            result = _adb(["uninstall", package], timeout=300)
            _installed_cache.clear()
            if "Success" in (result.stdout + result.stderr):
                return
        except WaydroidError:
            pass
    uninstall_apk(package)


def replace_install_async(entry: Entry, callback, on_stage=None, multiuser: bool = False) -> None:
    """Меняет установленную сборку пакета на эту и запускает её."""

    def stage(text):
        if on_stage is not None:
            GLib.idle_add(lambda: (on_stage(text), False)[1])

    def work():
        stage(tr("Убираем прежнюю сборку…"))
        uninstall_everywhere(entry.package)
        return True

    def then(_result, error):
        if error is not None:
            callback(error)
            return
        install_and_launch_async(entry, callback, on_stage=on_stage, multiuser=multiuser)

    in_thread(work, then)


def uninstall_async(entry: Entry, callback) -> None:
    """Убирает приложение из контейнера — из своего профиля или из общего."""

    def work():
        if entry.profile:
            uninstall_for_user(entry.package, entry.profile)
        else:
            uninstall_apk(entry.package)
        return True

    in_thread(work, lambda _result, error: callback(error))


_SUDO = "sudo "  # вынесено, чтобы тесты могли пройти путь без прав

# waydroid написан на Python, а Python при выводе в конвейер буферизует
# блоками по 8 КБ. Из-за этого прогресс загрузки не появлялся ни в журнале
# Merci, ни в самом терминале — вывод просто лежал в буфере. Переменная
# отключает буферизацию, и строки приходят сразу.
_UNBUFFERED = "env PYTHONUNBUFFERED=1 "

# Терминалы, в которых можно спросить пароль: pkexec в таких сеансах,
# как Hyprland, часто висит молча — polkit считает агента уже
# зарегистрированным, а спросить пароль фактически некому.
_TERMINALS = (
    ("kitty", ["kitty", "--title", "Merci — установка", "sh", "-lc"]),
    ("alacritty", ["alacritty", "--title", "Merci — установка", "-e", "sh", "-lc"]),
    ("foot", ["foot", "-T", "Merci — установка", "sh", "-lc"]),
    ("konsole", ["konsole", "-e", "sh", "-lc"]),
    ("gnome-terminal", ["gnome-terminal", "--", "sh", "-lc"]),
    ("xterm", ["xterm", "-e", "sh", "-lc"]),
)


# Помощник для sudo -A: печатает пароль в свой stdout, откуда его читает
# сам sudo. Внутри — exec системного диалога, поэтому ни одной нашей строки
# кода в передаче пароля не участвует.
_ASKPASS = """#!/bin/sh
# Спрашивает пароль системным диалогом и отдаёт его напрямую sudo.
# exec обязателен: после него в цепочке передачи пароля нет ни одной
# нашей команды — только диалог и сам sudo.
if command -v kdialog >/dev/null 2>&1; then
  exec kdialog --password "$1" --title "Merci"
fi
if command -v zenity >/dev/null 2>&1; then
  exec zenity --password --title "Merci"
fi
echo "не найдено ни kdialog, ни zenity" >&2
exit 1
"""


def askpass_helper() -> str:
    """Путь к помощнику. Пустая строка, если спросить пароль окном нечем."""
    if not _ok(["sh", "-c", "command -v kdialog >/dev/null || command -v zenity >/dev/null"]):
        return ""
    path = os.path.join(_data_root(), "askpass.sh")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_ASKPASS)
        os.chmod(path, 0o755)
    except OSError:
        return ""
    return path


def host_terminal() -> list[str] | None:
    """Первый найденный терминал на хосте, в котором можно ввести пароль."""
    for name, argv in _TERMINALS:
        if _ok(["sh", "-c", f"command -v {name} >/dev/null"]):
            return argv
    return None


# Сервер-указатель Waydroid: раздаёт JSON со ссылками на образы. Именно с
# него начинается waydroid init, и делает он это через urllib без таймаута —
# если сервер недоступен, команда висит бесконечно и молча.
OTA_URL = "https://ota.waydro.id/system"

def custom_channels() -> tuple[str, str]:
    """Свои адреса каналов, если пользователь их задал.

    Файл ota.conf в данных Merci: две строки — system и vendor. Нужен тем,
    у кого официальный сервер заблокирован, но есть зеркало.
    """
    path = os.path.join(_data_root(), "ota.conf")
    try:
        with open(path, encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip()]
    except OSError:
        return "", ""
    return (lines[0] if lines else ""), (lines[1] if len(lines) > 1 else "")


def ota_reachable(timeout: int = 12) -> bool:
    """Отвечает ли сервер-указатель. Проверяем сами, чтобы не ждать вечно."""
    return _ok(
        [
            "sh",
            "-c",
            f"curl -fsS --max-time {timeout} -o /dev/null {OTA_URL}",
        ],
        timeout=timeout + 5,
    )


# Каталог, куда waydroid складывает скачиваемые образы. Его размер — самый
# надёжный признак, что загрузка идёт: он не зависит от того, что и когда
# печатает сама программа.
_CACHE_DIR = "/var/lib/waydroid/cache_http"


def downloaded_mb() -> float:
    """Сколько мегабайт уже лежит в кеше образов waydroid."""
    result = _run(["sh", "-c", f"du -sk {_CACHE_DIR} 2>/dev/null | cut -f1"], timeout=8)
    try:
        return int(result.stdout.strip()) / 1024
    except (ValueError, AttributeError):
        return 0.0


def firewall_blocks_container() -> bool:
    """Мешает ли ufw контейнеру выйти в сеть.

    По умолчанию у ufw и INPUT, и FORWARD стоят в DROP. Первый рубит DHCP и
    DNS к хосту (контейнер не получает даже адрес), второй — выход наружу.
    Правило для waydroid0 в конфигурации ufw снимает оба ограничения, и
    только для этого интерфейса.
    """
    if not _ok(["sh", "-c", "command -v ufw >/dev/null"]):
        return False
    if not _ok(["sh", "-c", "grep -q '^ENABLED=yes' /etc/ufw/ufw.conf 2>/dev/null"]):
        return False
    return not _ok(
        [
            "sh",
            "-c",
            "grep -rq waydroid0 /etc/ufw/user.rules /etc/ufw/user6.rules 2>/dev/null",
        ]
    )


def _firewall_step() -> Step:
    return Step(
        key="firewall",
        title="Доступ в интернет для контейнера",
        hint="ufw блокирует DHCP и выход наружу с waydroid0",
        argv=[
            "sh",
            "-c",
            # Открываем ровно один интерфейс — тот, на котором живёт контейнер.
            "ufw allow in on waydroid0; "
            "ufw route allow in on waydroid0; "
            # Сессию перезапускаем: адрес контейнер получает только при старте.
            "waydroid session stop || true",
        ],
        root=True,
        minutes=1,
    )


def kernel_has_binder() -> bool:
    """binder может быть вкомпилирован в ядро — тогда никакой DKMS не нужен."""
    if _ok(["sh", "-c", "ls /dev/binderfs /dev/binder 2>/dev/null | head -1"]):
        return True
    if _ok(["sh", "-c", "zgrep -q '^CONFIG_ANDROID_BINDERFS=y' /proc/config.gz"]):
        return True
    return _ok(["sh", "-c", "modinfo binder_linux >/dev/null 2>&1"])


# -- шаги установки ------------------------------------------------------


@dataclass
class Step:
    """Шаг подготовки Waydroid."""

    key: str
    title: str
    hint: str = ""
    argv: list[str] = field(default_factory=list)
    root: bool = False
    downloads: str = ""  # что и откуда качается, если качается
    minutes: int = 1  # ожидаемая длительность, для текста в интерфейсе
    # Шаг, который не завершается сам: waydroid session start — это и есть
    # процесс сессии, он держит контейнер. Ждать его выхода бессмысленно,
    # готовность видно по waydroid status.
    background: bool = False

    def command_line(self) -> str:
        """Команда так, как её и правда выполняют."""
        prefix = "sudo " if self.root else ""
        return prefix + " ".join(self.argv)


URLFORWARD_PACKAGE = "xyz.hackerstone.merci.urlopen"
_URLFORWARD_SRC = "/app/share/merci/urlforward"


def urlforward_installed() -> bool:
    """Стоит ли перехватчик ссылок в контейнере.

    По списку приложений его не найти: `waydroid app list` показывает только
    то, что имеет иконку в меню, а перехватчик её не имеет — он ловит
    VIEW-интент и сразу закрывается. Поэтому смотрим на каталог данных,
    который Android создаёт при установке.
    """
    return _ok(
        [
            "sh",
            "-c",
            "test -d "
            f'"$HOME/.local/share/waydroid/data/data/{URLFORWARD_PACKAGE}"',
        ]
    )


def urlforward_step() -> Step:
    """Ссылки из Android открываются браузером хоста.

    В Waydroid такой возможности нет вовсе: работает только направление
    хост → контейнер (waydroid app intent), а обратного нет — открытая
    заявка waydroid/waydroid#210. Поэтому здесь собирается маленькое
    Android-приложение, которое ловит VIEW-интент и отдаёт адрес службе на
    хосте, а та вызывает xdg-open.
    """
    helper = askpass_helper()
    sudo = f"env SUDO_ASKPASS={_quote(helper)} sudo -A " if helper else "sudo "
    workdir = os.path.join(_data_root(), "urlforward")
    apk = os.path.join(workdir, "urlforward.apk")

    # Исходники лежат в /app, а скрипт шага выполняется на хосте, где этого
    # пути нет. Поэтому копируем их отсюда, из песочницы, в общий каталог
    # данных — он виден с обеих сторон.
    if os.path.isdir(_URLFORWARD_SRC):
        shutil.copytree(_URLFORWARD_SRC, workdir, dirs_exist_ok=True)
    elif not os.path.exists(os.path.join(workdir, "build-apk.sh")):
        # Вне флатпака /app нет; тогда рассчитываем на то, что исходники уже
        # лежат в данных с прошлого раза.
        raise WaydroidError(tr("исходники перехватчика не найдены"))

    script = (
        "set -e; "
        f"W={_quote(workdir)}; "
        'echo "проверяем инструменты сборки"; '
        # Три пакета: JDK из репозитория, остальное из AUR. Ставим только
        # недостающее — повторный запуск ничего не пересобирает.
        "command -v javac >/dev/null || "
        f"{{ echo 'ставим jdk-openjdk'; {sudo}pacman -S --needed --noconfirm jdk-openjdk; }}; "
        # android.jar тянем отдельным файлом: пакет android-platform зависит
        # от android-sdk и android-sdk-platform-tools — это половина SDK
        # ради одной библиотеки, нужной только на время компиляции.
        'if [ ! -s "$W/android.jar" ]; then '
        '  echo "качаем android.jar"; '
        '  rm -rf "$W/platform"; mkdir -p "$W/platform"; '
        "  curl -fL --progress-bar --retry 30 --retry-all-errors --retry-delay 3 "
        '    -C - -o "$W/platform.zip" '
        '    https://dl.google.com/android/repository/platform-36_r02.zip; '
        '  unzip -q -o -j "$W/platform.zip" "*/android.jar" -d "$W/platform"; '
        '  mv "$W/platform/android.jar" "$W/android.jar"; '
        '  rm -rf "$W/platform" "$W/platform.zip"; '
        "fi; "
        "for pkg in android-sdk-build-tools; do "
        '  pacman -Qq "$pkg" >/dev/null 2>&1 && continue; '
        '  echo "собираем $pkg из AUR"; '
        '  D="$W/aur/$pkg"; rm -rf "$D"; mkdir -p "$W/aur"; '
        "  for try in 1 2 3 4 5; do "
        '    git clone --depth 1 "https://aur.archlinux.org/$pkg.git" "$D" && break; '
        '    rm -rf "$D"; sleep 5; '
        "  done; "
        '  cd "$D"; '
        # Источники качаем сами: makepkg берёт их одним запросом и умирает
        # на первом обрыве, а канал бывает рваным.
        "  makepkg --printsrcinfo | awk '$1 == \"source\" {print $3}' "
        "| while read -r src; do "
        '    name="${src%%::*}"; url="${src#*::}"; '
        '    case "$src" in *::*) ;; *) url="$src"; name="${src##*/}";; esac; '
        '    case "$url" in http*) ;; *) continue;; esac; '
        '    [ -s "$name" ] && continue; '
        "    curl -fL --progress-bar --retry 30 --retry-all-errors --retry-delay 3 "
        '      -C - -o "$name" "$url"; '
        "  done; "
        "  makepkg -s --noconfirm --needed; "
        '  PKG=$(makepkg --packagelist | head -1); '
        f'  {sudo}pacman -U --noconfirm "$PKG"; '
        "done; "
        # Уже собранный APK не трогаем: сборка требует всех инструментов,
        # а готовый файл ставится и без них.
        f'if [ -s {_quote(apk)} ]; then echo "APK уже собран"; else '
        '  echo "собираем APK"; '
        f'  cd "$W"; ANDROID_JAR="$W/android.jar" sh ./build-apk.sh "$W" {_quote(apk)}; '
        "fi; "
        'echo "ставим приложение в контейнер"; '
        f"waydroid app install {_quote(apk)}; "
        'echo "поднимаем службу на хосте"; '
        'install -Dm755 "$W/merci-url-listener.py" '
        '"$HOME/.local/share/merci/urlforward/merci-url-listener.py"; '
        'install -Dm644 "$W/merci-url-listener.service" '
        '"$HOME/.config/systemd/user/merci-url-listener.service"; '
        "systemctl --user daemon-reload; "
        "systemctl --user enable --now merci-url-listener.service; "
        # Назначаем перехватчик браузером по умолчанию во всех профилях
        # Android. Без этого при каждой ссылке Android спрашивает, чем
        # открыть, и «всегда» приходится выбирать руками — а после
        # переустановки заново.
        'if command -v adb >/dev/null; then '
        "  IP=$(waydroid status | awk '/IP address/{print $NF}'); "
        '  adb connect "$IP:5555" >/dev/null 2>&1; '
        '  for u in $(adb -s "$IP:5555" shell pm list users 2>/dev/null '
        '      | grep -o "UserInfo{[0-9]*" | grep -o "[0-9]*"); do '
        f'    adb -s "$IP:5555" shell cmd role add-role-holder --user "$u" '
        f'      android.app.role.BROWSER {URLFORWARD_PACKAGE} >/dev/null 2>&1 '
        '      && echo "браузер по умолчанию в профиле $u"; '
        "  done; "
        "else "
        '  echo "adb не найден: назначить перехватчик браузером по умолчанию '
        'нечем — включите MultiUser в настройках, он ставит android-tools"; '
        "fi; "
        'echo "готово: ссылки будут открываться браузером хоста"'
    )
    return Step(
        key="urlforward",
        title="Открывать ссылки в браузере хоста",
        hint="собирает и ставит перехватчик ссылок, поднимает службу",
        argv=["sh", "-c", script],
        downloads="инструменты сборки Android (AUR)",
        minutes=10,
    )


def subsurface_flicker() -> bool:
    """Включён ли режим подповерхностей — источник мерцания картинки.

    Android-слои в этом режиме живут в wl_subsurface, а синхронная
    подповерхность показывается только когда коммитит родительская
    поверхность. Родитель молчит — кадр замирает, и картинка обновляется
    лишь на события: нажатие любой клавиши, вход курсора в окно, появление
    экранной клавиатуры. waydroid-nvidia включает этот режим по умолчанию.
    """
    return _prop("persist.waydroid.use_subsurface") == "true"


def subsurface_step() -> Step:
    """Выключает подповерхности в обоих файлах и перезапускает контейнер.

    Правим оба: waydroid.cfg применяется при старте сессии, а
    waydroid_base.prop читает init при загрузке контейнера — иначе
    перезапуск вернёт прежнее значение.
    """
    helper = askpass_helper()
    sudo = f"env SUDO_ASKPASS={_quote(helper)} sudo -A " if helper else "sudo "
    script = (
        "set -e; "
        f"{sudo}sed -i 's/use_subsurface=true/use_subsurface=false/' "
        "/var/lib/waydroid/waydroid_base.prop; "
        f"{sudo}sed -i 's/use_subsurface = true/use_subsurface = false/' "
        "/var/lib/waydroid/waydroid.cfg; "
        'echo "перезапускаем контейнер"; '
        f"{sudo}systemctl restart waydroid-container.service; "
        "sleep 3; "
        # Сессию поднимаем от пользователя: под root она не работает.
        "setsid waydroid session start >/dev/null 2>&1 &"
    )
    return Step(
        key="subsurface",
        title="Исправить мерцание картинки",
        hint="выключает подповерхности: из-за них кадр обновляется только на ввод",
        argv=["sh", "-c", script],
        minutes=2,
        background=True,
    )


def display_step(monitor: tuple[int, int]) -> Step | None:
    """Подгонка картинки под экран.

    Если gamescope на этой машине работает — рисуем меньше пикселей и
    растягиваем его силами. Если нет (с драйвером NVIDIA он падает), то
    честный вариант один: окно контейнера размером с монитор. Серых полей
    не будет ни там, ни там — они появляются только от wm size, поэтому
    его переопределение мы снимаем.
    """
    # Размер окон теперь выбирает человек, и он общий для всех
    # (waydroid_base.prop). Пока этот шаг об этом не знал, он при каждом
    # запуске мастера молча возвращал размер монитора — то есть отменял
    # выбранное. Если размер задан, мастеру здесь делать нечего.
    if all(global_size()):
        return None

    stretch = gamescope_works()
    render = recommended_render(monitor) if stretch else monitor
    if not render[0]:
        return None
    if resolution() == render and (not stretch or gamescope_running()):
        return None

    width, height = render
    monitor_width, monitor_height = monitor

    helper = askpass_helper()
    sudo = f"env SUDO_ASKPASS={_quote(helper)} sudo -A " if helper else "sudo "
    start = (
        _GAMESCOPE_SESSION.replace("__MW__", str(monitor_width))
        .replace("__MH__", str(monitor_height))
        .replace("__W__", str(width))
        .replace("__H__", str(height))
        if stretch
        else "setsid waydroid session start >/dev/null 2>&1 &"
    )

    script = (
        f"waydroid prop set persist.waydroid.width {width}; "
        f"waydroid prop set persist.waydroid.height {height}; "
        "waydroid session stop >/dev/null 2>&1; "
        + start
        # wm size оставлял бы картинку в углу большой поверхности — снимаем.
        + " sleep 12; "
        + f"{sudo}waydroid shell -- wm size reset >/dev/null 2>&1; "
        + f"{sudo}waydroid shell -- wm density reset >/dev/null 2>&1; true"
    )

    hint = (
        tr(
            "рендер {width}×{height}, растянутый на {screen_width}×{screen_height}",
            width=width,
            height=height,
            screen_width=monitor_width,
            screen_height=monitor_height,
        )
        if stretch
        else tr(
            "окно {width}×{height} — gamescope на этой машине не работает, "
            "растягивать нечем",
            width=width,
            height=height,
        )
    )
    return Step(
        key="display",
        title="Подгонка под экран",
        hint=hint,
        argv=["sh", "-c", script],
        root=False,
        minutes=2,
        background=True,
    )


def plan(needs_bridge: bool, monitor: tuple[int, int] = (0, 0)) -> list[Step]:
    """Чего не хватает — в порядке выполнения. Готовое не попадает.

    Проверки идут через adb, и каждая ждёт своего таймаута. Пока Android
    внутри не отвечает, это складывается в минуты молчаливого ожидания —
    мастер выглядит зависшим. Поэтому сначала короткий вопрос: жива ли
    система вообще.
    """
    steps: list[Step] = []
    if session_running() and not system_ready(container_ip(), timeout=10):
        return [container_restart_step()]

    if not kernel_has_binder():
        steps.append(
            Step(
                key="binder",
                title="Модуль ядра binder",
                hint="ядро без поддержки binder — нужен внешний модуль из AUR",
                argv=["yay", "-S", "--needed", "binder_linux-dkms"],
                minutes=5,
            )
        )

    installed = _ok(["sh", "-c", "command -v waydroid"])
    if not installed:
        steps.append(
            Step(
                key="package",
                title="Пакет waydroid",
                hint="ставится из репозитория extra",
                argv=["sh", "-c", _pacman_script("waydroid")],
                root=True,
                minutes=1,
            )
        )

    ready, detail = status(use_cache=False) if installed else (False, "не установлен")
    if not ready and detail in ("образ Android не загружен", "не установлен"):
        system, vendor = custom_channels()
        if not system:
            # Отдельный шаг проверки связи: waydroid init начинает именно с
            # этого сервера и, если тот недоступен, висит без вывода
            # бесконечно — urllib там вызывается без таймаута.
            steps.append(
                Step(
                    key="network",
                    title="Связь с сервером образов",
                    hint=tr("проверка {url}", url=OTA_URL),
                    argv=[
                        "sh",
                        "-c",
                        f"curl -fsS --max-time 15 -o /dev/null {OTA_URL} "
                        '|| { echo "сервер образов не отвечает"; exit 1; }',
                    ],
                    minutes=1,
                )
            )

        init_argv = ["waydroid", "init"]
        if system:
            init_argv += ["-c", system]
        if vendor:
            init_argv += ["-v", vendor]
        steps.append(
            Step(
                key="init",
                title="Образ Android",
                hint="загрузка системного образа LineageOS",
                argv=init_argv,
                root=True,
                downloads="образ Android (~1 ГБ)",
                minutes=10,
            )
        )

    if not ready:
        steps.append(
            Step(
                key="session",
                title="Сессия Waydroid",
                hint="запуск контейнера, прав root не требует",
                argv=["waydroid", "session", "start"],
                minutes=2,
                background=True,
            )
        )

    # Отключённый оверлей чинить надо раньше всего: без него нет
    # транслятора, и ARM-приложения падают во всех контейнерах сразу.
    if overlays_disabled():
        steps.append(overlay_step())

    if firewall_blocks_container():
        steps.append(_firewall_step())
        # После правил сессию надо поднять заново, иначе адреса не будет.
        steps.append(
            Step(
                key="session",
                title="Сессия Waydroid",
                hint="перезапуск после правки правил",
                argv=["waydroid", "session", "start"],
                minutes=2,
                background=True,
            )
        )

    # gamescope предлагаем ставить только когда его нет: если он уже стоит и
    # при этом не работает (NVIDIA), переустановка ничего не изменит.
    if monitor[0] and recommended_render(monitor) != monitor and not gamescope_available():
        steps.append(gamescope_step())

    if needs_bridge and not native_bridge():
        steps.append(_ndk_step(DEFAULT_BRIDGE))
        steps.append(
            Step(
                key="bridge",
                title="Трансляция ARM64 → x86_64",
                hint="libndk_translation через waydroid_script",
                argv=["sh", "-c", _bridge_script(DEFAULT_BRIDGE)],
                root=True,
                downloads="waydroid_script с github.com/casualsnek/waydroid_script "
                "и libndk из образа Android x86_64",
                minutes=8,
            )
        )

    display = display_step(monitor) if monitor[0] else None
    if display is not None:
        steps.append(display)

    # Мерцание чинится последним: шаг перезапускает контейнер, и делать это
    # раньше остальных настроек незачем.
    if subsurface_flicker():
        steps.append(subsurface_step())

    return steps


# Скрипт установки native bridge. Одной строкой, потому что уходит на хост
# через pkexec: клонируем во временный каталог и просим поставить libndk.
# Архив libndk_translation. Адреса и контрольные суммы взяты из самого
# waydroid_script (stuff/ndk.py): он кладёт файл в кеш и, если md5 совпадает,
# заново не качает. Поэтому Merci скачивает архив сама — curl умеет докачку
# и повторы, а waydroid_script тянет одним запросом и падает на любом обрыве.
# Адреса и контрольные суммы обоих трансляторов — из исходников
# waydroid_script (stuff/ndk.py и stuff/houdini.py). Скрипт кладёт архив в
# кеш и, если md5 совпадает, заново не качает: этим и пользуемся, потому что
# сам он тянет одним запросом и падает на любом обрыве.
_ARCHIVES = {
    "libndk": {
        "file": "libndktranslation.zip",
        "11": (
            "https://github.com/supremegamers/vendor_google_proprietary_ndk_translation"
            "-prebuilt/archive/9324a8914b649b885dad6f2bfd14a67e5d1520bf.zip",
            "c9572672d1045594448068079b34c350",
        ),
        "13": (
            "https://github.com/supremegamers/vendor_google_proprietary_ndk_translation"
            "-prebuilt/archive/68734c52556d3d7a6db34c603dd9276915c29f2f.zip",
            "0b2207c490fcb400aa5c87fcf0d52d38",
        ),
    },
    "libhoudini": {
        "file": "libhoudini.zip",
        "11": (
            "https://github.com/supremegamers/vendor_intel_proprietary_houdini"
            "/archive/81f2a51ef539a35aead396ab7fce2adf89f46e88.zip",
            "fbff756612b4144797fbc99eadcb6653",
        ),
        "13": (
            "https://github.com/supremegamers/vendor_intel_proprietary_houdini"
            "/archive/9e77896350caccd228b36b2e1b4a994aa4bd48da.zip",
            "3807fe029559db3037efe245d9e74270",
        ),
    },
}


# Транслятор по умолчанию. libndk падает на коде, который приложение
# генерирует на лету (у Roblox так работает JIT Luau): проверено, стабильный
# SIGSEGV в ndk_translation_HandleNoExec. libhoudini тот же код переваривает.
DEFAULT_BRIDGE = "libhoudini"


# Magisk лежит отдельно: это APK, без контрольной суммы в самом скрипте.
_ARCHIVES_EXTRA = {
    "magisk": {
        "file": "magisk.apk",
        "url": "https://github.com/mistrmochov/magiskdeltaorig/raw/main/app-release.apk",
    }
}


def archive_for(target: str) -> tuple[str, str, str]:
    """(адрес, md5, путь в кеше) для транслятора или Magisk."""
    if target in _ARCHIVES_EXTRA:
        extra = _ARCHIVES_EXTRA[target]
        url = extra["url"]
        mirror = github_mirror()
        if mirror:
            url = mirror.rstrip("/") + "/" + url
        path = os.path.join(
            _script_cache(), "waydroid-script", "downloads", extra["file"]
        )
        return url, "", path

    entry = _ARCHIVES[target]
    url, md5 = entry[_android_version()]
    mirror = github_mirror()
    if mirror:
        url = mirror.rstrip("/") + "/" + url
    path = os.path.join(
        _script_cache(), "waydroid-script", "downloads", entry["file"]
    )
    return url, md5, path


_NDK_ARCHIVE = {
    "11": (
        "https://github.com/supremegamers/vendor_google_proprietary_ndk_translation"
        "-prebuilt/archive/9324a8914b649b885dad6f2bfd14a67e5d1520bf.zip",
        "c9572672d1045594448068079b34c350",
    ),
    "13": (
        "https://github.com/supremegamers/vendor_google_proprietary_ndk_translation"
        "-prebuilt/archive/68734c52556d3d7a6db34c603dd9276915c29f2f.zip",
        "0b2207c490fcb400aa5c87fcf0d52d38",
    ),
}

# waydroid_script берёт каталог загрузок из XDG_CACHE_HOME, а если её нет —
# лезет в /home/$SUDO_USER/.cache. Гадать про домашний каталог под sudo не
# нужно: задаём переменную явно и указываем на свои данные. Тогда архив
# лежит там, где скрипт его и ищет, и качать ему нечего.
def _script_cache() -> str:
    return os.path.join(_data_root(), "cache")


_NDK_SIZE_MB = 18.0  # размер архива: нужен, чтобы показать проценты


def _ndk_local() -> str:
    return os.path.join(
        _script_cache(), "waydroid-script", "downloads", "libndktranslation.zip"
    )


def github_mirror() -> str:
    """Префикс зеркала GitHub из mirror.conf, если пользователь его задал.

    Например https://ghproxy.example/ — тогда адрес архива станет
    https://ghproxy.example/https://github.com/... Пусто — качаем напрямую.
    """
    try:
        with open(os.path.join(_data_root(), "mirror.conf"), encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _android_version() -> str:
    """11 или 13 — от этого зависит, какой архив транслятора нужен."""
    sdk = _prop("ro.build.version.sdk")
    return "11" if sdk == "30" else "13"


def _ndk_step(target: str = DEFAULT_BRIDGE) -> Step:
    url, md5, local = archive_for(target)

    # Три источника по порядку: уже скачанное нами, файл из каталога загрузок
    # (браузер справляется там, где падает waydroid_script), и только потом
    # своя загрузка — с докачкой после обрывов и повторами.
    script = (
        f'set -e; mkdir -p "$(dirname {_quote(local)})"; '
        f'check() {{ [ -f "$1" ] && echo "{md5}  $1" | md5sum -c - >/dev/null 2>&1; }}; '
        f'if check {_quote(local)}; then echo "архив уже скачан"; exit 0; fi; '
        'for d in "$HOME/Загрузки" "$HOME/Downloads" "$HOME/загрузки"; do '
        '  for f in "$d"/*ndk_translation*.zip; do '
        f'    if check "$f"; then echo "берём готовый файл: $f"; '
        f'      cp "$f" {_quote(local)}; exit 0; fi; '
        "  done; "
        "done; "
        'echo "качаем архив"; '
        "curl -fL --progress-bar --retry 30 --retry-all-errors --retry-delay 3 "
        f'-C - -o {_quote(local)} "{url}"; '
        f'check {_quote(local)} || {{ echo "архив повреждён"; rm -f {_quote(local)}; exit 1; }}'
    )
    return Step(
        key="ndk",
        title=tr("Архив транслятора ({target})", target=target),
        hint="берём из загрузок или качаем с докачкой",
        argv=["sh", "-c", script],
        downloads=os.path.basename(local),
        minutes=3,
    )


# Установка транслятора ARM. waydroid_script качает архив с GitHub одним
# запросом через requests: на нестабильном канале это регулярно рвётся с
# SSL: RECORD_LAYER_FAILURE. Поэтому попытки повторяются, а если libndk так
# и не дался — пробуем libhoudini: он лежит на других серверах.
# Установка транслятора ARM. Сетевая часть (клон и pip) делается от имени
# пользователя: под root pip к pypi.org не пробился, а от пользователя всё
# качается. Права нужны только самой установке в контейнер.
_BRIDGE_TEMPLATE = (
    "set -e; "
    'U=${SUDO_USER:-$(id -un)}; D=__WORKDIR__; '
    # waydroid_script считает провалом ЛЮБОЙ вывод в stderr, даже при
    # нулевом коде возврата. А `waydroid container stop` пишет туда обычное
    # сообщение «Stopping container» — но только когда контейнер работает.
    # Отсюда отказ на ровном месте: «returned non-zero exit status 0».
    # Гасим контейнер заранее, тогда его собственный stop промолчит.
    'echo "останавливаем контейнер перед установкой"; '
    "waydroid session stop >/dev/null 2>&1 || true; "
    "waydroid container stop >/dev/null 2>&1 || true; "
    "sleep 2; "
    'echo "готовим waydroid_script от имени $U"; '
    # Чистим каталог здесь, под root: прошлый запуск оставил в нём
    # __pycache__ от имени root, и пользователь такое удалить не может.
    'rm -rf "$D"; '
    'install -d -o "$U" -g "$U" "$(dirname "$D")"; '
    # Архив тянем сами, от имени пользователя: с докачкой и повторами.
    # waydroid_script качает одним запросом и умирает на первом же обрыве,
    # а найденный в кеше файл с верной суммой он просто использует.
    'runuser -u "$U" -- sh -c \''
    # Клонирование тоже повторяем: codeload.github.com отваливается ровно
    # так же, как остальные адреса на нестабильном канале.
    'for try in 1 2 3 4 5; do '
    '  git clone --depth 1 https://github.com/casualsnek/waydroid_script "$0" && break; '
    '  echo "клонирование не удалось, попытка $try"; rm -rf "$0"; sleep 5; '
    'done; '
    '[ -d "$0/.git" ] || { echo "не удалось скачать waydroid_script"; exit 1; }; '
    'cd "$0"; '
    # venv с системными пакетами: requests и tqdm уже есть, из сети нужен
    # только InquirerPy.
    "python3 -m venv --system-site-packages venv; "
    "for try in 1 2 3; do ./venv/bin/pip install --no-cache-dir --quiet InquirerPy "
    "&& break; sleep 4; done; "
    'mkdir -p "$(dirname __ARCHIVE__)"; '
    'if [ -s __ARCHIVE__ ] && { [ -z "__MD5__" ] '
    '|| echo "__MD5__  __ARCHIVE__" | md5sum -c - >/dev/null 2>&1; }; '
    'then echo "файл уже скачан"; else '
    '  echo "качаем нужный файл"; '
    "  curl -fL --progress-bar --retry 30 --retry-all-errors --retry-delay 3 "
    '  -C - -o __ARCHIVE__ "__URL__"; '
    "fi"
    '\' "$D"; '
    'cd "$D"; '
    './venv/bin/python3 -c "import InquirerPy" 2>/dev/null '
    '|| { echo "не удалось получить InquirerPy с pypi.org"; exit 1; }; '
    # waydroid_script для Magisk удаляет готовый файл и качает заново, без
    # проверки суммы. На рваном канале это верный провал, поэтому в своей
    # копии скрипта заменяем удаление и делаем загрузку условной.
    "sed -i 's/os.remove(self.download_loc)/pass/' stuff/magisk.py 2>/dev/null; "
    "sed -i 's|download_file(self.dl_link, self.download_loc)|"
    "(os.path.isfile(self.download_loc) or download_file(self.dl_link, self.download_loc))|' "
    "stuff/magisk.py 2>/dev/null; "
    "for try in 1 2 3; do "
    '  echo "== попытка $try: __ACTION__ __TARGET__"; '
    "  XDG_CACHE_HOME=__CACHE_HOME__ PYTHONDONTWRITEBYTECODE=1 "
  "./venv/bin/python3 main.py __ACTION__ __TARGET__ "
    "&& exit 0; "
    "  sleep 5; "
    "done; "
    'echo "не удалось выполнить: __ACTION__ __TARGET__"; exit 1'
)


def _bridge_script(target: str = "libndk", action: str = "install") -> str:
    workdir = os.path.join(_data_root(), "waydroid_script")
    url, md5, archive = archive_for(target)
    return (
        _BRIDGE_TEMPLATE.replace("__ACTION__", action)
        .replace("__CACHE_HOME__", _quote(_script_cache()))
        .replace("__WORKDIR__", _quote(workdir))
        .replace("__ARCHIVE__", _quote(archive))
        .replace("__MD5__", md5)
        .replace("__URL__", url)
        .replace("__TARGET__", target)
    )


def _quote(argument: str) -> str:
    """Простое экранирование для передачи команды в sh -c."""
    return "'" + argument.replace("'", "'\\''") + "'"


class StepRunner:
    """Выполняет шаг на хосте и рассказывает, что происходит.

    Вывод разбирается на ходу: waydroid печатает прогресс загрузки строкой
    вида "[Downloading] 123.45 MB/1234.56 MB   12.34 MB/s", и из неё
    получается настоящая полоса с процентами, скоростью и остатком времени.

    Права поднимаются двумя способами. Если в сессии есть polkit-агент —
    через pkexec, и вывод приходит прямо в наш канал. Если агента нет,
    pkexec будет ждать пароль вечно, поэтому команда уходит в терминал
    хоста, а вывод дублируется в файл внутри наших данных — оттуда мы его
    и читаем, чтобы прогресс выглядел так же.
    """

    def __init__(self, step: Step, on_line, on_done, on_progress=None) -> None:
        self.step = step
        self._on_line = on_line
        self._on_done = on_done
        self._on_progress = on_progress or (lambda fraction, detail: None)
        self._process: Gio.Subprocess | None = None
        self._cancel = Gio.Cancellable()
        self._done = False
        self._silent_since = GLib.get_monotonic_time()
        self._file_log = ""
        self._file_handle = None
        self._stream = None
        self._buffer = b""
        self._eof = False
        self._exited = False
        self._signalled = False
        self._code = -1
        self._tail_path = ""
        self._tail_offset = 0
        self._tail_source = 0
        self._exit_code: int | None = None
        self._nudges = 0
        self._cache_seen = 0.0
        self._waited = 0
        self._terminal_name = "терминала"
        self._terminal_closed_at = 0

    # -- запуск ----------------------------------------------------------

    def start(self) -> None:
        # Порядок такой. Пароль спрашивает системное окно через sudo -A: оно
        # само забирает фокус, ввод скрыт, и пароль идёт прямо в sudo.
        # Терминал остаётся запасным путём, если ни kdialog, ни zenity нет.
        # pkexec не используется: polkit в сеансах вроде Hyprland часто
        # считает агента зарегистрированным, а спросить пароль некому — и
        # тогда pkexec висит молча и бесконечно.
        if self.step.background:
            self._start_background()
            return

        if not self.step.root:
            self._start_directly()
            return

        helper = askpass_helper()
        if helper:
            self._start_directly(askpass=helper)
        else:
            self._start_in_terminal()

    def _start_background(self) -> None:
        """Запускает и отвязывает процесс, затем ждёт готовности.

        setsid обязателен: сессия должна пережить и наш процесс, и закрытие
        окна установщика, иначе контейнер погаснет вместе с ними.
        """
        command = " ".join(_quote(a) for a in self.step.argv)
        self._on_line(f"$ {command} (в фоне)")
        self._on_progress(None, tr("Поднимаем контейнер Android"))
        try:
            Gio.Subprocess.new(
                host_argv(["sh", "-c", f"setsid {command} >/dev/null 2>&1 &"]),
                Gio.SubprocessFlags.NONE,
            )
        except GLib.Error as exc:
            self._fail(f"не удалось запустить: {exc.message}")
            return

        self._waited = 0
        GLib.timeout_add_seconds(3, self._await_ready)

    def _await_ready(self) -> bool:
        if self._done:
            return False
        self._waited += 3
        if self._waited > 120:
            self._fail("контейнер не поднялся за две минуты")
            return False

        def check():
            forget_state()
            return status(use_cache=False)[0]

        def report(ready, _error):
            if self._done:
                return
            if ready:
                self._on_line("контейнер запущен")
                self._done = True
                self._on_done(True)

        in_thread(check, report)
        return not self._done

    def _start_directly(self, askpass: str = "") -> None:
        argv = list(self.step.argv)
        # Пишем всё в файл: в окне журнал ограничен по высоте, и хвост
        # ошибки легко теряется, а разбираться потом надо по полному тексту.
        self._file_log = os.path.join(_log_dir(), f"step-{self.step.key}.log")
        try:
            os.makedirs(os.path.dirname(self._file_log), exist_ok=True)
            self._file_handle = open(self._file_log, "w", encoding="utf-8")
            self._file_handle.write(f"$ {self.step.command_line()}\n")
        except OSError:
            self._file_handle = None
        if askpass:
            # SUDO_ASKPASS задаём командой env, а не окружением процесса:
            # через flatpak-spawn --host переменные до хоста доходят не
            # гарантированно, а так значение видно точно.
            argv = [
                "env",
                f"SUDO_ASKPASS={askpass}",
                "sudo",
                "-A",
                "env",
                "PYTHONUNBUFFERED=1",
                *argv,
            ]
            self._on_line("пароль спросит системное окно")
            self._on_progress(None, tr("Подтвердите пароль в системном окне"))

        launcher = Gio.SubprocessLauncher.new(
            Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_MERGE
        )
        # По той же причине, что и в терминале: без этого вывод waydroid
        # застревает в буфере на нашем канале.
        launcher.setenv("PYTHONUNBUFFERED", "1", True)
        if askpass:
            launcher.setenv("SUDO_ASKPASS", askpass, True)
        try:
            self._process = launcher.spawnv(host_argv(argv))
        except GLib.Error as exc:
            self._fail(f"не удалось запустить: {exc.message}")
            return

        # Ссылку держим на себе: локальную переменную сборщик мусора
        # может забрать вместе с каналом процесса.
        self._stream = self._process.get_stdout_pipe()
        self._read(self._stream)
        self._process.wait_async(None, self._finished)
        self._watch_silence()

    def _start_in_terminal(self) -> None:
        terminal = host_terminal()
        if terminal is None:
            self._fail(
                "в сессии нет polkit-агента и не найдено ни одного терминала. "
                f"Выполните вручную: sudo {' '.join(self.step.argv)}"
            )
            return

        self._tail_path = os.path.join(_log_dir(), f"step-{self.step.key}.log")
        os.makedirs(os.path.dirname(self._tail_path), exist_ok=True)
        with open(self._tail_path, "w", encoding="utf-8"):
            pass  # начинаем с чистого файла

        self._terminal_name = terminal[0]
        self._on_line(
            f"открыто окно {terminal[0]}: если спросит пароль, введите его там"
        )
        self._on_progress(None, tr("Подтвердите пароль в окне {app}", app=terminal[0]))
        command = " ".join(_quote(a) for a in self.step.argv)
        log = _quote(self._tail_path)
        # Вывод дублируем в файл: терминал печатает в своё окно, а прогресс
        # нужен и здесь. Код возврата передаём отдельной строкой.
        #
        # --foreground обязателен: без него timeout уводит команду в свою
        # группу процессов, для терминала она фоновая, и на попытке прочитать
        # пароль ядро присылает SIGTTIN — sudo останавливается, напечатав
        # приглашение, а ввод остаётся непрочитанным.
        #
        # При успехе окно закрывается само, при ошибке остаётся открытым:
        # текст вроде «неверный пароль» или «время ожидания истекло» должен
        # быть виден, а не исчезать вместе с окном.
        script = (
            f'{{ timeout --foreground 3600 {_SUDO}{_UNBUFFERED}{command}; echo "MERCI_EXIT:$?"; }} 2>&1 | tee -a {log}; '
            f'if tail -n 3 {log} | grep -q "MERCI_EXIT:0"; then sleep 1; '
            f'else printf "\n== шаг не выполнен, нажмите Enter ==\n"; read _ignored; fi'
        )
        try:
            self._process = Gio.Subprocess.new(
                host_argv([*terminal, script]), Gio.SubprocessFlags.NONE
            )
        except GLib.Error as exc:
            self._fail(f"не удалось открыть терминал: {exc.message}")
            return

        self._tail_source = GLib.timeout_add(400, self._tail)
        self._watch_silence()

    # -- чтение вывода ---------------------------------------------------

    def _read(self, stream) -> None:
        """Читаем сырыми кусками, без разделителей.

        Раньше здесь был DataInputStream с read_upto_async и синхронным
        read_byte для съедания разделителя. Стоило чтению один раз не
        перезаказаться — поток терял последнюю ссылку, сборщик мусора его
        закрывал вместе с каналом, и процесс на следующей строке получал
        SIGPIPE и умирал с кодом 141. Ссылку держим на себе, а строки
        собираем сами.
        """
        stream.read_bytes_async(4096, GLib.PRIORITY_DEFAULT, self._cancel, self._on_read)

    def _on_read(self, stream, result) -> None:
        try:
            data = stream.read_bytes_finish(result)
        except GLib.Error:
            return
        if data is None or data.get_size() == 0:
            self._on_eof()
            return

        self._buffer += data.get_data()
        # \r, а не только \n: прогресс загрузки рисуется возвратом каретки.
        parts = re.split(rb"[\r\n]", self._buffer)
        self._buffer = parts.pop()
        for piece in parts:
            text = piece.decode("utf-8", "replace")
            if text.strip():
                self._handle(text)

        self._read(stream)

    def _tail(self) -> bool:
        """Читает новые байты из файла, куда пишет терминал."""
        if self._done:
            return False
        try:
            with open(self._tail_path, "rb") as handle:
                handle.seek(self._tail_offset)
                chunk = handle.read()
                self._tail_offset = handle.tell()
        except OSError:
            return True

        for piece in re.split(rb"[\r\n]", chunk):
            if piece.strip():
                self._handle(piece.decode("utf-8", "replace"))

        if self._exit_code is not None:
            self._done = True
            self._on_done(self._exit_code == 0)
            return False
        return True

    def _handle(self, text: str) -> None:
        text = text.strip()
        if not text:
            return

        marker = "MERCI_EXIT:"
        if text.startswith(marker):
            try:
                self._exit_code = int(text[len(marker):].strip())
            except ValueError:
                self._exit_code = 1
            return

        self._silent_since = GLib.get_monotonic_time()
        if self._file_handle is not None:
            try:
                self._file_handle.write(text + "\n")
                self._file_handle.flush()
            except (OSError, ValueError):
                self._file_handle = None

        progress = _parse_progress(text)
        if progress is not None:
            self._on_progress(*progress)
            return

        phase = _parse_phase(text)
        if phase:
            self._on_progress(None, tr(phase))
        self._on_line(text)

    # -- завершение ------------------------------------------------------

    def _watch_silence(self) -> None:
        GLib.timeout_add_seconds(20, self._nudge)
        if self.step.key == "init":
            # Пока waydroid молчит, о ходе дела рассказывает размер кеша.
            GLib.timeout_add_seconds(5, self._watch_cache)
        elif self.step.key == "ndk":
            # Полоса curl в конвейере процентов не печатает, поэтому смотрим
            # на растущий файл: размер архива известен заранее.
            GLib.timeout_add_seconds(2, self._watch_archive)

    def _watch_archive(self) -> bool:
        if self._done:
            return False
        in_thread(_archive_megabytes, self._show_archive)
        return True

    def _show_archive(self, megabytes, _error) -> None:
        if self._done or not megabytes:
            return
        share = min(float(megabytes) / _NDK_SIZE_MB, 1.0)
        self._on_progress(
            share,
            tr("{done} из {total} МБ", done=f"{megabytes:.1f}", total=f"{_NDK_SIZE_MB:.0f}"),
        )

    def _watch_cache(self) -> bool:
        if self._done:
            return False
        in_thread(downloaded_mb, self._show_cache)
        return True

    def _show_cache(self, megabytes, _error) -> None:
        if self._done or not megabytes:
            return
        self._cache_seen = max(self._cache_seen, float(megabytes))
        self._on_progress(None, tr("Скачано {mb} МБ образа", mb=f"{self._cache_seen:.0f}"))

    def _nudge(self) -> bool:
        if self._done:
            return False
        if GLib.get_monotonic_time() - self._silent_since < 19_000_000:
            return True  # вывод идёт, всё в порядке
        self._nudges += 1
        if self._tail_path and self._nudges == 1:
            self._on_line(
                f"вывода пока нет: переключитесь в окно {self._terminal_name} "
                "и введите пароль"
            )
        elif self._nudges == 1 and self.step.key == "init":
            self._on_line(
                "waydroid печатает прогресс не сразу: сперва он молча "
                "запрашивает указатель на образы у ota.waydro.id"
            )
        elif self._nudges == 1:
            self._on_line("вывода пока нет — шаг занимает время")
        self._silent_since = GLib.get_monotonic_time()
        return True

    def cancel(self) -> None:
        self._done = True
        self._cancel.cancel()
        if self.step.background:
            return  # сессию оставляем жить: её держит отдельный процесс
        if self._tail_source:
            GLib.source_remove(self._tail_source)
            self._tail_source = 0
        if self._process is not None:
            try:
                self._process.send_signal(15)
            except GLib.Error:
                pass

    def _after_terminal(self) -> bool:
        if self._done:
            return False
        if self._exit_code is None:
            self._on_line("окно терминала закрыто, команда не завершилась")
            self._done = True
            self._on_done(False)
        return False

    def _fail(self, message: str) -> None:
        self._on_line(message)
        self._done = True
        self._on_done(False)

    def _on_eof(self) -> None:
        """Канал закрыт писателем — весь вывод получен."""
        self._eof = True
        self._maybe_finish()

    def _finished(self, process: Gio.Subprocess, result) -> None:
        if self._tail_path:
            # Окно закрыли: даём чтению догнать хвост файла, и если кода
            # возврата так и нет — считаем шаг невыполненным.
            self._terminal_closed_at = GLib.get_monotonic_time()
            GLib.timeout_add_seconds(2, self._after_terminal)
            return

        try:
            process.wait_finish(result)
            self._signalled = process.get_if_signaled()
            self._code = (
                process.get_term_sig() if self._signalled else process.get_exit_status()
            )
        except GLib.Error:
            self._signalled, self._code = False, -1

        self._exited = True
        # Если поток почему-то не дойдёт до конца, не ждать же вечно.
        GLib.timeout_add_seconds(3, self._force_finish)
        self._maybe_finish()

    def _force_finish(self) -> bool:
        if not self._done:
            self._eof = True
            self._maybe_finish()
        return False

    def _maybe_finish(self) -> None:
        """Шаг закончен, когда процесс вышел и весь вывод прочитан.

        Порядок важен: раньше о завершении сообщалось сразу по выходу
        процесса, и хвост вывода — то есть текст ошибки — терялся.
        """
        if self._done or not (self._exited and self._eof):
            return
        self._done = True

        if self._buffer.strip():
            self._handle(self._buffer.decode("utf-8", "replace"))
            self._buffer = b""

        self._handle(
            f"процесс убит сигналом {self._code}"
            if self._signalled
            else f"код возврата: {self._code}"
        )

        if self._file_handle is not None:
            try:
                self._file_handle.close()
            except OSError:
                pass
            self._file_handle = None

        if self._code == 126 and not self._signalled:
            self._on_line(
                "polkit отказал или запрос пароля отменён. Ту же команду можно "
                f"выполнить вручную: {self.step.command_line()}"
            )
        self._on_done(not self._signalled and self._code == 0)


def _data_root() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "merci")


def _log_dir() -> str:
    return os.path.join(_data_root(), "install")


# "[Downloading]  123.45 MB/1234.56 MB      12.34 MB/s(approx.)"
_PROGRESS_RE = re.compile(
    r"\[Downloading\]\s+([\d.]+)\s*MB/([\d.]+)\s*MB\s+([\d.]+)\s*(MB|kB)/s"
)

_PHASES = (
    ("Downloading", "Загружаем образ системы"),
    ("Validating", "Проверяем контрольную сумму"),
    ("Extracting", "Распаковываем образ"),
    ("Setting up", "Настраиваем контейнер"),
    ("Initializing", "Готовим контейнер"),
    ("Starting", "Запускаем сессию"),
    ("Installing", "Устанавливаем"),
    ("resolving dependencies", "Разбираем зависимости"),
    ("installing", "Ставим пакет"),
)


_CURL_RE = re.compile(r"#\s+(\d+(?:\.\d+)?)%")


def _parse_progress(text: str) -> tuple[float, str] | None:
    curl = _CURL_RE.search(text)
    if curl is not None:
        share = float(curl.group(1)) / 100
        return share, tr("скачано {percent}%", percent=curl.group(1))

    """Из строки прогресса waydroid делает (доля, человеческое описание)."""
    match = _PROGRESS_RE.search(text)
    if match is None:
        return None
    done, total, speed, unit = match.groups()
    done_mb, total_mb, speed_value = float(done), float(total), float(speed)
    if total_mb <= 0:
        return None

    fraction = min(done_mb / total_mb, 1.0)
    speed_mb = speed_value if unit == "MB" else speed_value / 1000
    detail = tr(
        "{done} из {total} МБ · {speed} {unit}/с",
        done=f"{done_mb:.0f}",
        total=f"{total_mb:.0f}",
        speed=f"{speed_value:.1f}",
        unit=unit,
    )
    if speed_mb > 0.05:
        left = int((total_mb - done_mb) / speed_mb)
        detail += (
            tr(" · осталось ~{n} мин", n=left // 60)
            if left >= 60
            else tr(" · осталось ~{n} с", n=left)
        )
    return fraction, detail


def _parse_phase(text: str) -> str:
    for needle, human in _PHASES:
        if needle in text:
            return human
    return ""


# -- запуск приложений --------------------------------------------------


_installed_cache = Cache(ttl=15.0)


def installed_packages() -> set[str]:
    """Что уже стоит в контейнере. Список меняется редко — кешируем."""
    cached = _installed_cache.get()
    if cached is not None:
        return cached
    result = _run(["waydroid", "app", "list"], timeout=60)
    packages = {
        line.split(":", 1)[1].strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith("packageName:")
    }
    if not packages:
        # Пустой список — не «ничего не установлено», а «служба не
        # ответила»: системные приложения в контейнере есть всегда. Раньше
        # Merci принимала молчание за чистую страницу и ставила APK заново —
        # долгая установка вместо внятного отказа.
        raise ContainerUnreachable(
            tr("контейнер не отвечает: список приложений пуст")
        )
    _installed_cache.set(packages)
    return packages


def install_apk(entry: Entry) -> None:
    if not os.path.exists(entry.apk_path):
        raise WaydroidError(tr("файл APK пропал из библиотеки"))
    result = _run(["waydroid", "app", "install", entry.apk_path], timeout=300)
    text = (result.stdout + result.stderr).strip()
    # Служба Waydroid и здесь умеет отказать, выйдя с нулевым кодом.
    if result.returncode != 0 or any(m in text for m in _CONFLICT_MARKERS):
        _raise_install_error(text)


class PictureStuck(WaydroidError):
    """Приложение работает, а картинки нет: контейнер потерял поверхность."""


class NeedsConfirmation(WaydroidError):
    """Удаление продолжается в окне Android и ждёт нажатия пользователя."""


class ContainerUnreachable(WaydroidError):
    """Контейнер числится запущенным, но не отвечает.

    Так он иногда просыпается после перезагрузки машины и так же виснет
    после долгой работы: Android внутри жив, а сеть и службы не отвечают —
    ARP до него не проходит, `waydroid prop` отвечает «Failed to get service
    waydroidplatform». Перезапуска сессии тут мало, потому что сам Android
    её переживает; помогает перезапуск контейнера, а это уже с правами root.
    """


class InstallConflict(WaydroidError):
    """В контейнере стоит другая сборка этого же пакета.

    Android держит одно имя пакета как одну установку на всё устройство:
    профили делят между собой код приложения и различаются только данными.
    Поэтому обычный клиент и модифицированный — а у них и подписи разные —
    рядом стоять не могут ни в каком профиле:

        INSTALL_FAILED_UPDATE_INCOMPATIBLE: signatures do not match
        INSTALL_FAILED_VERSION_DOWNGRADE: version code 2872 older than 2904

    Выход один: убрать ту установку и поставить эту.
    """


def uninstall_apk(package: str) -> None:
    """Убирает пакет из контейнера — вместе с его данными внутри Android.

    Три пути, от тихого к заметному:

    * через adb, если он есть, — самый надёжный;
    * штатной службой Waydroid. Она умеет отказывать, не говоря об этом:
      пишет «Failed with code: -3» и выходит с нулём, поэтому проверяем не
      код возврата, а вывод;
    * намерением ACTION_DELETE — тогда Android сам показывает свой вопрос
      «удалить приложение?» в окне контейнера, и нажать надо там.
    """
    if not package:
        raise WaydroidError(tr("неизвестно имя пакета"))

    if adb_available():
        try:
            adb_connect()
            result = _adb(["uninstall", package], timeout=180)
            _installed_cache.clear()
            if "Success" in (result.stdout + result.stderr):
                return
        except WaydroidError:
            pass  # adb есть, но не подключился — идём дальше

    result = _run(["waydroid", "app", "remove", package], timeout=180)
    _installed_cache.clear()
    text = (result.stdout + result.stderr).strip()
    if result.returncode == 0 and "ailed" not in text:
        return

    intent = _run(
        ["waydroid", "app", "intent", "android.intent.action.DELETE", f"package:{package}"],
        timeout=120,
    )
    if intent.returncode != 0:
        raise WaydroidError(text or "удалить не удалось")
    raise NeedsConfirmation(
        tr("контейнер не дал убрать приложение сам, поэтому Android спросит "
        "об этом в своём окне — подтвердите удаление там")
    )


# -- несколько копий одного пакета ---------------------------------------
#
# Два клиента Roblox — обычный и модифицированный — это один и тот же пакет
# com.roblox.client. Android держит по одной копии пакета на пользователя,
# поэтому второй такой APK вытесняет первый, и переустанавливать приходится
# при каждом переключении. Пользователи Android — штатное решение: у каждого
# своя копия пакета и свои данные.
#
# Служба Waydroid, через которую идут обычные install/launch, работает
# только с нулевым пользователем и про остальных ничего не знает. Зато в
# контейнере слушает adbd, и через него доступны pm и am — с нужным
# пользователем и без прав root на хосте.

_ADB_PORT = 5555


def session_running() -> bool:
    """Запущена ли сессия — по своей строке, а не по слову RUNNING в выводе.

    В ответе waydroid две строки состояния, и у остановленной сессии
    контейнер вполне может быть запущен:

        Session:    STOPPED
        Container:  RUNNING
    """
    result = _run(["waydroid", "status"], timeout=40)
    for line in result.stdout.splitlines():
        if line.strip().startswith("Session:"):
            return "RUNNING" in line
    return False


def ensure_session(stage=None) -> None:
    """Поднимает сессию контейнера и дожидается его адреса в сети.

    Обычный путь запуска этим не занимается — за него это делает сам
    ``waydroid app launch``. А запуск в профиле идёт через adb, и на
    выключенном контейнере он раньше просто отвечал «контейнер не запущен»:
    нажатие «Выключить Waydroid», а следом «Запустить» превращалось в
    ошибку там, где должно быть ожидание.

    Отдельный случай — свежая загрузка машины: сессия запускается, а сеть
    внутри не поднимается вовсе, и адрес так и остаётся UNKNOWN. Лечится
    перезапуском сессии, что здесь и делается.
    """
    if not session_running():
        # Порядок обязателен: пока работают дополнительные окна, оверлей
        # Waydroid не встанет, и транслятор ARM пропадёт у всех.
        prepare_session_start(stage)
        if stage is not None:
            stage(tr("Поднимаем контейнер…"))
        _run(["sh", "-c", "setsid waydroid session start >/dev/null 2>&1 &"], timeout=30)
        for _ in range(60):
            time.sleep(2)
            if session_running():
                forget_state()
                break
        else:
            raise WaydroidError(tr("контейнер не поднялся"))

    # Android внутри поднимается ещё некоторое время после сессии, и adb до
    # этого момента отвечает отказом.
    if wait_for_ip(45):
        return
    restart_session(stage)


def ensure_container_alive(stage=None) -> None:
    """Приводит контейнер в состояние, в котором можно ставить и запускать.

    Проверка стоит копейки, а спасает от самого долгого способа ничего не
    добиться. Контейнер бывает наполовину жив: system_server уже убит
    сторожем, или SurfaceFlinger заклинило после закрытия окна — и тогда
    каждая следующая команда молча выжидает свой таймаут adb. Установка
    при этом «не находит» ни одного приложения и начинается заново, запуск
    отвечает успехом, а окна нет. Со стороны это выглядит как бесконечная
    проверка, из которой ничего не следует.
    """
    if not session_running():
        return  # выключенный контейнер поднимут ensure_session и app launch
    ip = container_ip()
    if ip and system_ready(ip, 10) and screen_state(ip=ip) != "затор":
        return
    if stage is not None:
        stage(tr("Android не отвечает — возвращаем…"))
    restart_session(stage)


_IPV4 = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")


def _bridge_neighbours() -> dict[str, str]:
    """{адрес: MAC} для всего, что видно на мосту контейнеров."""
    found: dict[str, str] = {}
    result = _run(["ip", "neigh", "show", "dev", "waydroid0"], timeout=30)
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "lladdr" and "FAILED" not in line:
            found[parts[0]] = parts[2].lower()
    return found


def container_ip() -> str:
    """Адрес контейнера в сети или пустая строка.

    Пока сеть внутри не поднялась, waydroid пишет в это поле ``UNKNOWN`` —
    и сразу после включения машины это обычное состояние, которое может
    продержаться долго. Раньше слово уезжало прямо в adb:

        error: device 'UNKNOWN:5555' not found

    Поэтому за адрес принимаем только то, что им выглядит.
    """
    reported = ""
    for line in _run(["waydroid", "status"], timeout=40).stdout.splitlines():
        if "IP address" in line:
            value = line.split(":", 1)[1].strip()
            reported = value if _IPV4.match(value) else ""
            break

    # Когда рядом работают дополнительные контейнеры, waydroid status
    # показывает адрес первого попавшегося соседа на мосту — то есть может
    # назвать чужой. Свои контейнеры мы знаем по MAC: если он назвал один
    # из них, ищем на мосту тот, что не наш.
    ours = {instance_mac(number) for number in _registry()}
    if not ours:
        return reported

    # Живые соседи, которые не наши дополнительные окна. Верить одному
    # только waydroid status нельзя: после перезапуска он называет и вовсе
    # исчезнувшие адреса из старых аренд DHCP.
    neighbours = _bridge_neighbours()
    mine = [address for address, mac in neighbours.items() if mac not in ours]

    # Сразу после перезапуска таблица соседей пуста: контейнер ещё не
    # сказал ни слова, а waydroid status уже называет адрес — иногда из
    # старой аренды DHCP. Стучимся по нему один раз: если там кто-то есть,
    # в таблице появится MAC, и его уже можно проверить.
    if not mine and reported:
        _run(["ping", "-c", "1", "-W", "1", reported], timeout=15)
        neighbours = _bridge_neighbours()
        mine = [address for address, mac in neighbours.items() if mac not in ours]

    if reported in mine:
        return reported
    if mine:
        return mine[0]
    # Проверить не удалось: адрес называет только waydroid status, а на
    # мосту его нет даже после запроса. Верить такому нельзя — рядом с
    # дополнительными окнами это чаще всего чужая или отжившая аренда.
    # Пустой ответ честнее: вызывающий подождёт и спросит ещё раз.
    return ""


def wait_for_ip(seconds: int = 60) -> str:
    """Ждёт адрес контейнера — и не любой, а отвечающий.

    Пока рядом живут дополнительные окна, в таблице соседей и в ответе
    waydroid status попадаются адреса из прежних аренд DHCP. Принять такой
    значит потом получить «device not found» от adb на ровном месте,
    поэтому адрес считается годным только когда по нему отзывается adb.
    """
    deadline = time.time() + seconds
    fallback = ""
    while True:
        address = container_ip()
        if address:
            fallback = address
            if not adb_available():
                return address
            _run(["adb", "connect", f"{address}:{_ADB_PORT}"], timeout=30)
            state = _run(["adb", "-s", f"{address}:{_ADB_PORT}", "get-state"], timeout=20)
            if state.stdout.strip() == "device":
                return address
        if time.time() >= deadline:
            return fallback
        time.sleep(2)


_size_healed = False


def heal_main_size(stage=None) -> None:
    """Приводит основной контейнер к общему размеру окон.

    Размер мог разойтись: раньше это делал шаг мастера, а вообще любое
    прямое обращение к waydroid prop. Правим один раз за запуск Merci —
    иначе легко получить круг из перезапусков.
    """
    global _size_healed
    if _size_healed:
        return
    wanted = global_size()
    if not all(wanted):
        return
    ip = container_ip()
    if not ip or display_size(ip) == wanted:
        return

    _size_healed = True
    if stage is not None:
        stage(tr("Приводим размер основного окна к общему…"))
    _run(["waydroid", "prop", "set", "persist.waydroid.width", str(wanted[0])], timeout=90)
    _run(["waydroid", "prop", "set", "persist.waydroid.height", str(wanted[1])], timeout=90)
    _apply_size_inside(ip, wanted[0], wanted[1])
    restart_session(stage)


def prepare_session_start(stage=None) -> None:
    """Готовит машину к старту основной сессии.

    Порядок здесь не вкусовщина, а требование: пока работают
    дополнительные контейнеры, они держат прежнее монтирование образа, и
    оверлей Waydroid не встаёт. Waydroid на это отвечает раз и навсегда —
    пишет в конфиг mount_overlays = False и больше не пробует. Вместе с
    оверлеем пропадает libhoudini, и ARM-приложения перестают
    запускаться во ВСЕХ контейнерах, включая основной.
    """
    from .settings import Settings

    extra = [
        item["number"]
        for item in instances()
        if item["number"] != 1 and item["state"] == "RUNNING"
    ]
    # Размер окон живёт в waydroid_base.prop, а этот файл Waydroid
    # пересобирает с нуля при обновлении образа. Выбор человека хранится у
    # нас, поэтому возвращаем его на место, если пропал.
    chosen = Settings().window_size
    need_size = all(chosen) and global_size() != chosen

    if not extra and not need_size and not overlays_disabled():
        return

    # Всё под root делается одной командой: по отдельности это три запроса
    # пароля подряд, и достаточно пропустить один, чтобы машина осталась на
    # середине — с выключенным оверлеем и без транслятора ARM.
    if stage is not None:
        stage(tr("Готовим машину к старту контейнера…"))
    width, height = chosen if need_size else (0, 0)
    with suppress(WaydroidError):
        _instance_call("prepare", str(width), str(height), timeout=420)


def restart_session(stage=None) -> None:
    """Перезапускает сессию контейнера и ждёт его адрес.

    Это же лекарство от состояния «сессия запущена, а сети нет», в котором
    контейнер иногда просыпается после перезагрузки машины.
    """
    prepare_session_start(stage)
    if stage is not None:
        stage(tr("Перезапускаем контейнер…"))
    _run(["waydroid", "session", "stop"], timeout=60)
    _run(["sh", "-c", "pkill -9 -f '[w]aydroid session start' || true"], timeout=15)

    # Убеждаемся, что сессия правда легла: `session stop` регулярно упирается
    # в таймаут D-Bus и молча оставляет всё как было. Запустить поверх живой
    # сессии — значит не перезапустить ничего, а отрапортовать об успехе.
    for _ in range(15):
        forget_state()
        if not session_running():
            break
        time.sleep(2)
    else:
        raise WaydroidError(tr("сессия не остановилась"))

    time.sleep(3)
    _run(["sh", "-c", "setsid waydroid session start >/dev/null 2>&1 &"], timeout=30)
    forget_state()
    for _ in range(60):
        time.sleep(2)
        if session_running():
            break
    if stage is not None:
        stage(tr("Ждём сеть контейнера…"))
    if not wait_for_ip(120):
        raise ContainerUnreachable(tr("контейнер не получил адрес в сети"))

    # Адреса мало: waydroid показывает последний известный, даже когда
    # внутри всё легло. Проверяем, что контейнер отвечает на самом деле.
    if adb_available():
        try:
            adb_connect()
        except WaydroidError as failure:
            raise ContainerUnreachable(str(failure)) from failure

    # Android внутри иногда застревает на загрузке: адреса по DHCP нет,
    # службы пакетов нет, сторож убивает system_server по кругу. Сессию
    # перезапускать бесполезно — помогает перезапуск службы контейнера.
    # Делаем это один раз и молча, иначе человек остаётся с мёртвым окном
    # и сообщением, из которого ничего не следует.
    if not wait_system_ready(container_ip(), 90):
        if stage is not None:
            stage(tr("Android не поднялся — перезапускаем контейнер целиком…"))
        with suppress(WaydroidError):
            _instance_call("restart-container", timeout=180)
        time.sleep(5)
        _run(["sh", "-c", "setsid waydroid session start >/dev/null 2>&1 &"], timeout=30)
        forget_state()
        for _ in range(60):
            time.sleep(2)
            if session_running():
                break
        wait_for_ip(120)
        if not wait_system_ready(container_ip(), 120):
            raise ContainerUnreachable(
                tr("Android внутри не отвечает — помогает полный перезапуск контейнера")
            )


def restart_session_async(callback, on_stage=None) -> None:
    def stage(text):
        if on_stage is not None:
            GLib.idle_add(lambda: (on_stage(text), False)[1])

    in_thread(lambda: restart_session(stage), lambda _result, error: callback(error))


def adb_available() -> bool:
    return _ok(["sh", "-c", "command -v adb >/dev/null"])


def _require_ip() -> str:
    """Адрес контейнера или внятная ошибка вместо него.

    Пустой адрес значит две очень разные вещи. Контейнер может быть
    выключен — тогда так и говорим. А может уже числиться запущенным, пока
    сеть внутри поднимается: первые секунды после старта waydroid пишет
    «IP address: UNKNOWN». Раньше в обоих случаях выходило «контейнер не
    запущен» — и человек видел это в карточке, глядя на работающую игру.
    """
    ip = container_ip()
    if ip:
        return ip
    if status()[0]:
        raise ContainerUnreachable(
            tr("контейнер ещё поднимается — сеть внутри не готова")
        )
    raise WaydroidError(tr("контейнер не запущен"))


def _adb(args: list[str], timeout: int = 120):
    ip = _require_ip()
    return _run(["adb", "-s", f"{ip}:{_ADB_PORT}", *args], timeout=timeout)


def adb_connect() -> None:
    """Подключается к adbd контейнера.

    Здесь важно уметь восстанавливаться. Контейнер за время работы Merci
    перезапускается не раз — от «Выключить Waydroid» до шагов подготовки, —
    и adb на своей стороне оставляет запись о прежнем подключении:

        192.168.240.112:5555   offline

    Дальше `adb connect` отвечает «already connected», а каждая команда —
    «error: device offline», и всё, что идёт через adb, перестаёт работать
    до ручного `adb disconnect`. Поэтому при неудаче рвём подключение сами и
    пробуем заново.

    Отказы здесь разного смысла: «unauthorized» значит, что контейнер не
    знает наш ключ. Подтвердить запрос внутри Android некому — окна с
    вопросом нет, — поэтому ключ кладём сами, через помощника с правами
    root, и пробуем ещё раз.
    """
    ip = _require_ip()
    target = f"{ip}:{_ADB_PORT}"
    text = ""
    # Попыток несколько и с паузами: адрес у контейнера появляется раньше,
    # чем внутри поднимается adbd, и сразу после перезапуска сессии первые
    # попытки честно отвечают «device not found».
    for attempt in range(8):
        if attempt:
            _run(["adb", "disconnect", target], timeout=30)
            time.sleep(3)
        _run(["adb", "connect", target], timeout=60)
        state = _run(["adb", "-s", target, "get-state"], timeout=30)
        text = (state.stdout + state.stderr).strip()
        last = text.splitlines()[-1].strip() if text else ""
        if state.returncode == 0 and last == "device":
            return
        if "unauthorized" in text.lower() and attempt == 0:
            with suppress(WaydroidError):
                _instance_call("adb-key", "1", timeout=180)
            time.sleep(3)
        if "unauthorized" in text:
            raise WaydroidError(
                tr("контейнер просит разрешить отладку: откройте окно Android и "
                "нажмите «Разрешить» в появившемся вопросе")
            )
    raise WaydroidError(text or "adb не подключился к контейнеру")


def android_users() -> dict[int, str]:
    """Пользователи Android внутри контейнера: {номер: имя}."""
    adb_connect()
    result = _adb(["shell", "pm", "list", "users"], timeout=60)
    users: dict[int, str] = {}
    for match in re.finditer(r"UserInfo\{(\d+):([^:]*):", result.stdout):
        users[int(match.group(1))] = match.group(2)
    return users


def create_android_user(name: str) -> int:
    """Заводит пользователя Android и возвращает его номер."""
    adb_connect()
    safe = re.sub(r"[^\w -]", "", name)[:20].strip() or "merci"
    result = _adb(["shell", "pm", "create-user", safe], timeout=120)
    match = re.search(r"id (\d+)", result.stdout)
    if not match:
        text = (result.stdout + result.stderr).strip()
        if "max" in text.lower() or "limit" in text.lower():
            raise WaydroidError(
                tr("Android не даёт завести ещё одного пользователя — "
                "предел уже достигнут")
            )
        raise WaydroidError(text or "не удалось завести пользователя Android")
    number = int(match.group(1))
    # Пользователя надо ещё и запустить: без этого установка в него
    # заканчивается «user is not running».
    _adb(["shell", "am", "start-user", str(number)], timeout=120)
    # Роль браузера у каждого профиля своя, поэтому новому её тоже выдаём —
    # иначе ссылки в нём снова начнут спрашивать, чем открыть.
    set_browser_role(number)
    return number


def remove_android_user(user: int) -> None:
    """Убирает пользователя Android вместе со всем, что в нём стояло."""
    if user <= 0:
        return
    adb_connect()
    if current_android_user() == user:
        # Себя Android удалить не даст, поэтому сперва уходим в основной.
        switch_android_user(0)
    _adb(["shell", "pm", "remove-user", str(user)], timeout=120)


# Тип профиля-клона из Android 13. Клон — это профиль пользователя 0, а не
# отдельный пользователь: он работает ОДНОВРЕМЕННО с основным, поэтому копия
# приложения живёт рядом с оригиналом. Обычные профили так не умеют — на них
# контейнер приходится переключать, и оригинал уходит с экрана.
CLONE_USER_TYPE = "android.os.usertype.profile.CLONE"

# Потолок на всякий случай: каждый клон — это ещё один набор данных
# приложения, и бесконечно их плодить незачем.
MAX_USER_SLOTS = 16

_USER_INFO = re.compile(r"UserInfo\{(\d+):([^:]*):")


def multi_windows_on() -> bool:
    """Показывает ли Waydroid каждое приложение отдельным окном.

    От этого зависит, увидит ли человек копию и оригинал одновременно:
    в однооконном режиме контейнер рисует что-то одно.
    """
    return _prop("persist.waydroid.multi_windows") == "true"


def set_multi_windows(enabled: bool) -> None:
    """Переключает многооконный режим и перезапускает сессию.

    Свойство persist.* читается при старте сессии, поэтому без перезапуска
    переключатель бы ничего не изменил, а выглядел бы рабочим.
    """
    _run(
        ["waydroid", "prop", "set", "persist.waydroid.multi_windows",
         "true" if enabled else "false"],
        timeout=90,
    )
    if status(use_cache=False)[0]:
        restart_session()


def app_resizable(package: str) -> bool:
    """Разрешает ли приложение менять размер своего окна.

    Игры почти всегда объявляют обратное
    (PRIVATE_FLAG_ACTIVITIES_RESIZE_MODE_UNRESIZEABLE), и тогда Android
    держит их задачи полноэкранными, а такие показываются строго по одной.
    Значит копия и оригинал будут сменять друг друга, а не стоять рядом, —
    и об этом честнее сказать заранее, чем показать пустой экран.
    """
    if not adb_available():
        return True
    try:
        adb_connect()
        result = _adb(["shell", "dumpsys", "package", package], timeout=90)
    except WaydroidError:
        return True
    return "RESIZE_MODE_UNRESIZEABLE" not in result.stdout


# Дополнительные контейнеры: по одному Android на окно. Профили этого не
# дают — их показывает только текущий пользователь Android, и соседей у
# основного ровно два. Контейнер же полностью свой: свои данные, свой
# binder, своё окно; предел — только память.
_INSTANCES_SRC = "/app/share/merci/instances"


def _instance_helper() -> str:
    """Кладёт помощника в данные Merci и отдаёт путь, видимый хосту.

    Внутрь песочницы система не заглядывает, поэтому скрипт, который
    запускается через sudo, должен лежать в общем каталоге данных.
    Перезаписываем каждый раз: версия Merci могла смениться.
    """
    target = os.path.join(_data_root(), "instances", "merci-instance.py")
    source = os.path.join(_INSTANCES_SRC, "merci-instance.py")
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.exists(source):
            shutil.copyfile(source, target)
        os.chmod(target, 0o755)
    except OSError as exc:
        raise WaydroidError(
            tr("не удалось подготовить помощника для контейнеров: {error}", error=exc)
        ) from exc
    if not os.path.exists(target):
        raise WaydroidError(tr("помощник для контейнеров не найден"))
    return target


def _instance_call(*argv: str, timeout: int = 240):
    """Зовёт помощника с правами root и разбирает его ответ.

    Пароль спрашивает системное окно, Merci его не видит — тот же путь,
    что и у шагов подготовки.
    """
    helper = _instance_helper()
    askpass = askpass_helper()
    home = os.path.expanduser("~")
    prefix = f"env SUDO_ASKPASS={_quote(askpass)} sudo -A " if askpass else "sudo "
    command = (
        prefix
        + f"python3 {_quote(helper)} --home {_quote(home)} "
        + " ".join(_quote(part) for part in argv)
    )
    result = _run(["sh", "-c", command], timeout=timeout)
    text = (result.stdout or "").strip()
    if result.returncode != 0:
        raise WaydroidError((result.stderr or text).strip() or tr("не удалось"))
    try:
        return json.loads(text.splitlines()[-1]) if text else None
    except (ValueError, IndexError) as exc:
        raise WaydroidError(text or str(exc)) from exc


def _registry_path() -> str:
    return os.path.join(_data_root(), "instances.json")


def _registry() -> dict[int, str]:
    """Заведённые окна: номер → чьи они. Помнит Merci, а не система.

    Спрашивать об этом систему пришлось бы через root, а список нужен на
    каждое открытие карточки — тогда пароль спрашивали бы постоянно.

    Окно принадлежит записи библиотеки, а не приложению вообще: в
    контейнере своя копия данных, и заведено оно было для конкретной
    сборки. Пустой владелец — окна из прежних версий Merci, когда список
    был общим; они достаются первой карточке, которую откроют.
    """
    try:
        with open(_registry_path(), encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    if isinstance(data, list):  # прежний вид: просто номера
        return {int(n): "" for n in data if int(n) > 1}
    if isinstance(data, dict):
        return {int(n): str(owner) for n, owner in data.items() if int(n) > 1}
    return {}


def _registry_set(mapping: dict[int, str]) -> None:
    try:
        os.makedirs(os.path.dirname(_registry_path()), exist_ok=True)
        with open(_registry_path(), "w", encoding="utf-8") as handle:
            json.dump({str(n): owner for n, owner in sorted(mapping.items())}, handle)
    except OSError:
        pass


def adopt_orphan_instances(owner: str) -> list[int]:
    """Отдаёт этой записи окна, заведённые до разделения по приложениям."""
    if not owner:
        return []
    реестр = _registry()
    ничьи = [number for number, чьё in реестр.items() if not чьё]
    if not ничьи:
        return []
    for number in ничьи:
        реестр[number] = owner
    _registry_set(реестр)
    return sorted(ничьи)


def instance_mac(number: int) -> str:
    """Тот же расчёт, что и в помощнике: адрес зависит от номера."""
    return f"00:16:3e:f9:d3:{0x03 + number - 1:02x}"


def instance_ip(number: int) -> str:
    """Адрес контейнера по его MAC — из таблицы соседей моста, без root."""
    if number == 1:
        return container_ip()
    mac = instance_mac(number)
    for address, found in _bridge_neighbours().items():
        if found == mac:
            return address
    return ""


def instance_running(number: int) -> bool:
    """Работает ли контейнер окна — по самому контейнеру, а не по сети.

    Раньше Merci судила по адресу и ответу adb, и контейнер без сети
    считался остановленным. Это не мелочь в показаниях: такой контейнер
    держит монтирование образа, из-за чего у основной сессии не встаёт
    оверлей — а вместе с ним пропадает транслятор ARM у всех окон сразу.
    Merci же не гасила его перед стартом сессии, потому что для неё он был
    выключен.

    Смотрим на процесс: дополнительные окна поднимаются `lxc-start -F`, и
    этот процесс живёт ровно столько, сколько живёт контейнер. Root для
    этого не нужен — чужие процессы видно и так.
    """
    if number == 1:
        return status()[0]
    name = container_name(number)
    result = _run(["ps", "-eo", "args="], timeout=20)
    for line in result.stdout.splitlines():
        words = line.split()
        if not words or "lxc-start" not in words[0]:
            continue
        if "-n" in words and words[words.index("-n") + 1 :][:1] == [name]:
            return True
    return False


def container_name(number: int) -> str:
    return "waydroid" if number == 1 else f"waydroid{number}"


def instances(owner: str = "") -> list[dict]:
    """Контейнеры: номер, состояние, адрес. Первый — основной.

    С непустым ``owner`` показываются только окна этой записи библиотеки:
    у каждого приложения свои клоны, и чужие в его карточке ни к чему.
    Основное окно общее — оно у Waydroid одно на всех.

    Всё делается без root: номера помним сами, состояние берём у процесса
    контейнера, адрес — из таблицы соседей на мосту. Пароль остаётся для
    действий: создать, запустить, погасить, удалить.
    """
    реестр = _registry()
    if owner:
        adopt_orphan_instances(owner)
        реестр = _registry()
        свои = sorted(n for n, чьё in реестр.items() if чьё == owner)
    else:
        свои = sorted(реестр)

    items = []
    for number in [1] + свои:
        ip = instance_ip(number)
        state = "RUNNING" if instance_running(number) else "STOPPED"
        items.append({"number": number, "state": state, "ip": ip})
    return items


def next_instance_number() -> int:
    """Первый свободный номер начиная со второго."""
    taken = {item.get("number") for item in instances()}
    number = 2
    while number in taken:
        number += 1
    return number


def create_instance(number: int, owner: str = "") -> dict:
    result = _instance_call("create", str(number), timeout=300) or {}
    реестр = _registry()
    реестр[number] = owner
    _registry_set(реестр)
    return result


def start_instance(number: int) -> dict:
    """Поднимает окно и приводит его размер к общему.

    У контейнера, которому размер когда-то задавали отдельно, значение
    осталось в его собственном складе свойств и перебивает общее из
    waydroid_base.prop. Пока он выключен, поправить это нечем — свойства
    живут внутри. Поэтому сверяем после запуска и, если разошлось,
    переписываем и поднимаем ещё раз: одна лишняя загрузка, зато дальше
    окно ведёт себя как все.
    """
    # Помощник внутри ждёт сеть и при нужде перезапускает окно — на это
    # уходит до четырёх минут, и обрывать его на середине нельзя.
    result = _instance_call("start", str(number), timeout=420) or {}

    ip = wait_instance_ip(number)
    if not ip or not wait_instance_ready(ip):
        return result

    # Окно должно быть настроено как основное: сессия Waydroid и Merci
    # правят основной контейнер под себя, а дополнительные поднимаются
    # напрямую и об этих правках не знают.
    надо_перезапустить = bool(inherit_main_props(ip))

    wanted = global_size()
    if all(wanted) and display_size(ip) != wanted:
        _apply_size_inside(ip, wanted[0], wanted[1])
        надо_перезапустить = True

    if not надо_перезапустить:
        return result

    # Эти свойства читаются при старте — без перезагрузки они не в счёт.
    return _instance_call("restart", str(number), timeout=420) or result


def restart_instance(number: int) -> dict:
    """Гасит окно и поднимает заново — один заход под root, один пароль."""
    return _instance_call("restart", str(number), timeout=420) or {}


def _host_file(path: str) -> str:
    """Читает файл хоста. Из песочницы /var/lib/waydroid не виден вовсе,
    и обычный open() тихо отвечает «нет такого файла» — а это не то же
    самое, что «настройка выключена»."""
    return _run(["cat", path], timeout=30).stdout


def global_size() -> tuple[int, int]:
    """Общий размер окон из базовых свойств контейнеров."""
    values = {}
    for line in _host_file("/var/lib/waydroid/waydroid_base.prop").splitlines():
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    try:
        return (
            int(values.get("persist.waydroid.width", 0) or 0),
            int(values.get("persist.waydroid.height", 0) or 0),
        )
    except ValueError:
        return 0, 0


def display_size(ip: str) -> tuple[int, int]:
    """Физический размер дисплея контейнера."""
    try:
        text = instance_adb(ip, ["shell", "wm size"], timeout=60).stdout
    except WaydroidError:
        return 0, 0
    match = re.search(r"Physical size:\s*(\d+)x(\d+)", text)
    return (int(match.group(1)), int(match.group(2))) if match else (0, 0)


def stop_instance(number: int) -> dict:
    return _instance_call("stop", str(number), timeout=180) or {}


def remove_instance(number: int) -> dict:
    result = _instance_call("remove", str(number), timeout=300) or {}
    реестр = _registry()
    реестр.pop(number, None)
    _registry_set(реестр)
    return result


def wait_instance_ip(number: int, seconds: int = 120) -> str:
    """Ждёт адрес контейнера: сеть внутри поднимается не мгновенно."""
    deadline = time.time() + seconds
    while True:
        for item in instances():
            if item.get("number") == number and item.get("ip"):
                return item["ip"]
        if time.time() >= deadline:
            return ""
        time.sleep(4)


def instance_adb(ip: str, args: list[str], timeout: int = 120):
    """Команда adb в конкретный контейнер.

    Своего adb_connect на каждый вызов не делаем — только при отказе:
    подключение живёт долго, а лишний connect стоит секунды.
    """
    target = f"{ip}:{_ADB_PORT}"
    result = _run(["adb", "-s", target, *args], timeout=timeout)
    text = (result.stdout + result.stderr).lower()
    if result.returncode != 0 and ("not found" in text or "offline" in text or "unauthorized" in text):
        _run(["adb", "disconnect", target], timeout=30)
        _run(["adb", "connect", target], timeout=60)
        result = _run(["adb", "-s", target, *args], timeout=timeout)
    return result


def host_windows() -> list[dict]:
    """Окна контейнеров на хосте: пакет, размер, в фокусе ли.

    Спрашиваем композитор. Пока умеем только Hyprland — на остальных
    вернётся пусто, и подгонка просто не предложится: обещать то, чего не
    можем узнать, хуже, чем промолчать.
    """
    result = _run(["sh", "-c", "hyprctl clients -j 2>/dev/null"], timeout=30)
    try:
        data = json.loads(result.stdout or "[]")
    except ValueError:
        return []

    windows = []
    for item in data:
        name = str(item.get("class") or "")
        if not name.startswith("waydroid."):
            continue
        size = item.get("size") or [0, 0]
        windows.append(
            {
                "package": name[len("waydroid."):],
                "width": int(size[0]),
                "height": int(size[1]),
                "focused": item.get("focusHistoryID") == 0,
            }
        )
    return windows


def window_for_package(package: str) -> dict | None:
    """Окно этого приложения на хосте. Если их несколько — то, что в фокусе."""
    windows = [w for w in host_windows() if w["package"] == package]
    if not windows:
        return None
    for window in windows:
        if window["focused"]:
            return window
    return windows[0]


def set_global_size(width: int, height: int, stage=None) -> None:
    """Задаёт размер окна сразу всем контейнерам — и будущим тоже.

    Размер живёт в waydroid_base.prop: этот файл читают все контейнеры при
    старте, поэтому запись туда действует и на уже созданные окна, и на те,
    что появятся позже. Отдельно правим ещё и свойства внутри работающих
    контейнеров: у каждого свой склад свойств, и прежнее значение оттуда
    перебило бы общее.
    """
    if width < 320 or height < 240:
        raise WaydroidError(tr("окно слишком мало для подгонки"))

    # Свойства внутри работающих контейнеров правим заранее и без root:
    # у каждого свой склад, и прежнее значение оттуда перебивает общее.
    # Читаются они при старте — значит подействуют на перезапуске ниже.
    if stage is not None:
        stage(tr("Записываем размер для всех окон…"))
    for item in instances():
        if item["state"] == "RUNNING" and item["ip"]:
            _apply_size_inside(item["ip"], width, height)

    # Основной контейнер берёт размер из настроек самого Waydroid.
    _run(["waydroid", "prop", "set", "persist.waydroid.width", str(width)], timeout=90)
    _run(["waydroid", "prop", "set", "persist.waydroid.height", str(height)], timeout=90)

    # Один заход под root: общий размер и остановка окон. Пароль здесь не
    # кешируется, поэтому каждый лишний вызов — ещё одно окно с паролем.
    answer = _instance_call("apply-size", str(width), str(height), timeout=300) or {}
    stopped = [int(number) for number in answer.get("stopped", [])]

    if stage is not None:
        stage(tr("Перезапускаем основное окно…"))
    restart_session()

    if stopped:
        if stage is not None:
            stage(tr("Поднимаем окна заново…"))
        _instance_call("start-many", *(str(n) for n in stopped), timeout=600)
        for number in stopped:
            wait_instance_ip(number)

    # Запоминаем только то, что действительно применилось: сохрани мы
    # выбор заранее, Merci помнила бы размер, которого нет ни в одном
    # окне, и восстанавливала бы его при каждом старте.
    from .settings import Settings

    Settings().window_size = (width, height)


def _apply_size_inside(ip: str, width: int, height: int) -> None:
    """Свойства и сброс «override» внутри одного поднявшегося контейнера."""
    density = max(120, min(320, round(180 * height / 1080)))
    with suppress(WaydroidError):
        instance_adb(ip, ["shell", f"setprop persist.waydroid.width {width}"], timeout=60)
        instance_adb(ip, ["shell", f"setprop persist.waydroid.height {height}"], timeout=60)
        instance_adb(ip, ["shell", f"setprop persist.waydroid.lcd_density {density}"], timeout=60)
        # Прежний wm size переживает перезапуск и спорит с новым размером.
        instance_adb(ip, ["shell", "wm size reset"], timeout=60)
        instance_adb(ip, ["shell", "wm density reset"], timeout=60)


def fit_display(number: int, ip: str, width: int, height: int) -> None:
    """Задаёт окну размер и перезапускает его контейнер.

    Менять размер на лету не выходит, и это проверено: `wm size` меняет
    дисплей, но поверхность, которую Waydroid отдал композитору, остаётся
    прежней — картинка съезжает, обрезается или исчезает вовсе. Размер
    берётся при старте сессии, поэтому единственный честный путь —
    записать его в свойства контейнера и поднять контейнер заново.
    Перезапускается только он один: остальные окна не трогаем.
    """
    if width < 320 or height < 240:
        raise WaydroidError(tr("окно слишком мало для подгонки"))
    density = max(120, min(320, round(180 * height / 1080)))

    instance_adb(ip, ["shell", f"setprop persist.waydroid.width {width}"], timeout=60)
    instance_adb(ip, ["shell", f"setprop persist.waydroid.height {height}"], timeout=60)
    instance_adb(ip, ["shell", f"setprop persist.waydroid.lcd_density {density}"], timeout=60)

    if number == 1:
        # У основного окна свойства живут в настройках самого Waydroid.
        _run(["waydroid", "prop", "set", "persist.waydroid.width", str(width)], timeout=90)
        _run(["waydroid", "prop", "set", "persist.waydroid.height", str(height)], timeout=90)
        restart_session()
        return

    stop_instance(number)
    start_instance(number)
    fresh = wait_instance_ip(number)
    if not fresh:
        return
    wait_instance_ready(fresh)
    # «Override» от прежних попыток переживает перезапуск и спорит с новым
    # размером: физический дисплей один, логический другой — и картинка
    # снова разъезжается. Сбрасываем уже на поднявшемся контейнере: до
    # перезапуска сброс не удерживается.
    instance_adb(fresh, ["shell", "wm size reset"], timeout=60)
    instance_adb(fresh, ["shell", "wm density reset"], timeout=60)


def system_ready(ip: str, timeout: int = 20) -> bool:
    """Отвечает ли Android внутри, а не только загрузился ли он.

    `sys.boot_completed` бывает единицей, когда system_server уже упал:
    сторож Android убивает его при зависании и запускает заново, и в этом
    промежутке любая команда отвечает «Can't find service: package».
    Спрашиваем саму службу пакетов — она и нужна для установки и запуска.
    """
    if not ip:
        return False
    result = instance_adb(ip, ["shell", "cmd package path android"], timeout=timeout)
    return "package:" in result.stdout


def wait_system_ready(ip: str, seconds: int = 90) -> bool:
    """Ждёт, пока Android внутри придёт в себя."""
    deadline = time.time() + seconds
    while True:
        if system_ready(ip):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(5)


def screen_state(package: str = "", ip: str | None = None) -> str:
    """Что сейчас с картинкой Android: "есть", "нет" или "затор".

    Проверка отдельная от служб, и вот почему. Окно Waydroid можно закрыть
    средствами композитора — в Hyprland это Win+Q. Android при этом не
    закрывается: приложение продолжает работать, но поверхности, на
    которой оно рисовало, больше нет. После нескольких таких закрытий
    SurfaceFlinger перестаёт отвечать вовсе, и нового окна не появится уже
    никогда. Снаружи всё выглядит исправным: службы окон и пакетов на
    вопрос «найдены?» отвечают «да», `waydroid app launch` возвращает ноль,
    Merci рапортует об успехе — а экрана нет.

    Поэтому спрашиваем сам SurfaceFlinger: живой отвечает списком слоёв за
    доли секунды, застрявший не отвечает совсем. Заодно по этому списку
    видно, есть ли на экране окно нужного приложения.
    """
    # Пустая строка и «не указано» — разные вещи: у выключенного окна
    # адреса нет, и подставлять вместо него основной контейнер нельзя —
    # так выключенное окно отвечало «картинка есть».
    ip = container_ip() if ip is None else ip
    if not ip:
        return "нет"
    result = instance_adb(
        ip, ["shell", "dumpsys", "-t", "3", "SurfaceFlinger", "--list"], timeout=25
    )
    text = result.stdout + result.stderr
    if "TIMEOUT" in text or not result.stdout.strip():
        return "затор"
    if not package:
        return "есть"
    # Композитор решает, показывать ли окно, по слоям задачи — тем, что
    # названы «TID:<номер>#<пакет>/...». Слои вроде ActivityRecord есть и у
    # приложения, которому окна не досталось: по ним «окно есть» выходило
    # там, где экран оставался пустым.
    return (
        "есть"
        if any(
            line.lstrip().startswith("TID:") and package in line
            for line in result.stdout.splitlines()
        )
        else "нет"
    )


def display_ready(ip: str | None = None) -> bool:
    """Отвечает ли Android за картинку."""
    return screen_state("", ip) == "есть"


def wait_app_on_screen(package: str, seconds: int = 45, ip: str | None = None) -> bool:
    """Ждёт окно приложения, но не дольше, чем есть смысл ждать.

    В заторе ждать нечего: SurfaceFlinger не ответит и через час, поэтому
    возвращаемся сразу, а не выжидаем срок до конца.
    """
    deadline = time.time() + seconds
    while True:
        state = screen_state(package, ip)
        if state == "есть":
            return True
        if state == "затор":
            return False
        if time.time() >= deadline:
            return False
        time.sleep(2)


def package_ready(package: str, ip: str = "") -> bool:
    """Видит ли Android именно это приложение.

    Служба пакетов отвечает раньше, чем дочитывает установленные: сразу
    после перезапуска контейнера `am start` на живой системе отвечает
    «Activity class does not exist», хотя приложение никуда не девалось.
    """
    ip = ip or container_ip()
    if not ip:
        return False
    result = instance_adb(ip, ["shell", "cmd", "package", "path", package], timeout=20)
    return "package:" in result.stdout


def wait_package_ready(package: str, seconds: int = 60, ip: str = "") -> bool:
    deadline = time.time() + seconds
    while True:
        if package_ready(package, ip):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(2)


def instance_ready(ip: str) -> bool:
    if not ip:
        return False
    result = instance_adb(ip, ["shell", "getprop", "sys.boot_completed"], timeout=60)
    return result.stdout.strip() == "1"


def wait_instance_ready(ip: str, seconds: int = 180) -> bool:
    deadline = time.time() + seconds
    while True:
        if instance_ready(ip):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(5)


def instance_has_package(ip: str, package: str) -> bool:
    result = instance_adb(ip, ["shell", "pm", "path", package], timeout=60)
    return "package:" in result.stdout


def install_in_instance(entry: Entry, ip: str) -> None:
    """Ставит APK в отдельный контейнер.

    Здесь без вариантов нужен полноценный install: контейнеры не делят
    установленные приложения, install-existing работает только между
    профилями одного Android.
    """
    if not os.path.exists(entry.apk_path):
        raise WaydroidError(tr("файл APK не найден"))
    result = instance_adb(ip, ["install", "-r", "-g", entry.apk_path], timeout=900)
    text = (result.stdout + result.stderr).strip()
    if "Success" not in text:
        _raise_install_error(text)


# Значение waydroid.active_apps, при котором контейнер спокойно живёт без
# единого окна. Именно так стоят здоровые контейнеры в покое.
#
# Не «Waydroid»: полный интерфейс Android этой сборке композитора показать
# нечем — лаунчер у неё в чёрном списке (waydroid.blacklist_apps), окна не
# будет, а контейнер при этом считает, что экран у него есть. Дальше он
# задыхается без кадровых сигналов: dequeueBuffer отваливается по таймауту,
# виснет WindowManager, сторож убивает system_server по кругу.
IDLE_SCREEN = "none"


def set_active_app(ip: str, value: str) -> None:
    instance_adb(ip, ["shell", "setprop", "waydroid.active_apps", value], timeout=60)


def launch_in_instance(entry: Entry, ip: str, stage=None) -> None:
    """Открывает приложение в контейнере: своё окно, свои данные.

    Порядок здесь важнее, чем кажется. В однооконном режиме композитор
    Waydroid показывает ровно то приложение, которое названо в
    ``waydroid.active_apps``. Если названного приложения на экране нет, он
    не показывает НИЧЕГО — закрывает окно совсем:

        single-window: NO TID layer among 4 layers (window stays closed)

    А без окна композитор хоста не шлёт кадровых сигналов, и Android
    внутри задыхается: `dequeueBuffer failed, error = -110`, следом
    блокируется WindowManager, и сторож убивает system_server по кругу.
    Окно после этого не вылечить ничем, кроме перезапуска контейнера, —
    свойство композитор перечитывает только на кадре, а кадров уже нет.

    Поэтому: сначала поднимаем приложение, и лишь потом отдаём ему экран.
    А если окно так и не появилось — возвращаем контейнеру полный
    интерфейс, чтобы он остался живым и годным для следующей попытки.
    """
    if not entry.activity:
        raise WaydroidError(tr("в APK не нашлось activity для запуска"))
    if not wait_system_ready(ip):
        raise ContainerUnreachable(
            tr("Android внутри не отвечает — помогает полный перезапуск контейнера")
        )

    def say(text: str) -> None:
        if stage is not None:
            stage(text)

    wait_package_ready(entry.package, 60, ip)
    grant_storage_access(entry.package, 0, ip)
    clear_stale_task(entry.package, ip)
    start_activity(entry.package, f"{entry.package}/{entry.activity}", 0, ip)

    # Экран отдаём приложению, когда оно уже рисует, — не раньше.
    say(tr("Ждём первый кадр приложения…"))
    wait_app_drawing(entry.package, 180, ip)
    set_active_app(ip, entry.package)

    # И сразу спрашиваем композитор, будет ли окно: отказ значит, что
    # задача помечена закрытой и нужна новая.
    time.sleep(4)
    if composer_refuses_window(ip) is True:
        instance_adb(ip, ["shell", "am", "force-stop", entry.package], timeout=90)
        time.sleep(2)
        start_activity(entry.package, f"{entry.package}/{entry.activity}", 0, ip)
        wait_app_drawing(entry.package, 180, ip)
        set_active_app(ip, entry.package)

    # У каждого окна свой SurfaceFlinger, и заклинить его может так же:
    # закрыли окно средствами композитора — приложение работает, картинки
    # нет. Запуск при этом отвечает успехом, поэтому проверяем экран.
    if not wait_app_on_screen(entry.package, 60, ip):
        with suppress(WaydroidError):
            set_active_app(ip, IDLE_SCREEN)
        raise PictureStuck(
            tr("приложение запустилось, но картинки в этом окне нет")
        )


def app_frames(package: str, ip: str | None = None) -> int:
    """Сколько кадров приложение уже нарисовало. -1 — не знаем."""
    ip = container_ip() if ip is None else ip
    if not ip:
        return -1
    result = instance_adb(ip, ["shell", "dumpsys", "gfxinfo", package], timeout=25)
    for line in result.stdout.splitlines():
        if "Total frames rendered" in line:
            with suppress(ValueError):
                return int(line.split(":", 1)[1].strip())
    return -1


# Свойства, которыми окно должно быть похоже на основное. Основное
# настраивает под себя сессия Waydroid и сама Merci, а дополнительные
# поднимаются напрямую и берут только общий файл свойств — из-за этого,
# например, частота обновления у них оказывалась 180 Гц против 60.
INHERITED_PROPS = (
    "persist.waydroid.refresh_rate",
    "persist.waydroid.multi_windows",
    "persist.waydroid.adb",
)


def inherit_main_props(ip: str) -> list[str]:
    """Переносит в окно настройки основного контейнера.

    Возвращает список свойств, которые пришлось менять: если он не пуст,
    окно нужно перезапустить — эти свойства читаются при старте.
    """
    main = container_ip()
    if not main or not ip or ip == main:
        return []
    менялись = []
    for name in INHERITED_PROPS:
        нужно = instance_adb(main, ["shell", "getprop", name], timeout=20).stdout.strip()
        если_есть = instance_adb(ip, ["shell", "getprop", name], timeout=20).stdout.strip()
        if not нужно or нужно == если_есть:
            continue
        instance_adb(ip, ["shell", "setprop", name, нужно], timeout=20)
        менялись.append(f"{name}={нужно}")
    return менялись


def app_has_surface(package: str, ip: str | None = None) -> bool:
    """Есть ли у приложения поверхность с кадрами.

    SurfaceFlinger называет такой слой «…(BLAST)» — он появляется, когда у
    приложения заработала очередь буферов, то есть оно и правда рисует.
    Признак виден для любого профиля, в отличие от счётчика кадров:
    `dumpsys gfxinfo` про приложение из второго профиля не знает ничего.
    """
    ip = container_ip() if ip is None else ip
    if not ip:
        return False
    result = instance_adb(
        ip, ["shell", "dumpsys", "-t", "3", "SurfaceFlinger", "--list"], timeout=25
    )
    if "TIMEOUT" in (result.stdout + result.stderr):
        return False
    return any(
        package in line and "BLAST" in line for line in result.stdout.splitlines()
    )


def wait_app_drawing(package: str, seconds: int = 120, ip: str | None = None) -> bool:
    """Ждёт, пока приложение нарисует первый кадр.

    Экран отдавать раньше нельзя. Пока контейнеру велено показывать
    приложение, а показывать нечего, окна нет — а без окна композитор хоста
    не шлёт кадровых сигналов, и Android внутри задыхается: сначала
    `dequeueBuffer failed, error = -110` у строки состояния, потом виснет
    WindowManager, потом сторож убивает system_server по кругу. У быстрых
    приложений это незаметно, а тяжёлая игра при первом запуске готовится
    минуту и больше — и контейнер до неё не доживает.

    Пока ждём, контейнер стоит в покое: без окна, но и без вреда.
    """
    deadline = time.time() + seconds
    while True:
        if app_has_surface(package, ip) or app_frames(package, ip) > 0:
            return True
        if screen_state("", ip) == "затор":
            return False
        if time.time() >= deadline:
            # Приложение может рисовать в обход обычного пути — тогда
            # кадров мы не увидим никогда. Судим по слоям задачи.
            return screen_state(package, ip) == "есть"
        time.sleep(3)


def composer_refuses_window(ip: str | None = None) -> bool | None:
    """Решил ли композитор, что окна не будет.

    Он объявляет это сам, в журнале контейнера:

        single-window: target tid='1000012' aid='com.roblox.client' should_show=1
        single-window: tid 1000009 (com.roblox.client) in ignored_apps -> no window
        single-window: NO TID layer among 4 layers (window stays closed)

    Ответ приходит за секунды — а ждать окна вслепую нельзя дольше минуты:
    ровно столько нужно Android, чтобы задохнуться без кадровых сигналов и
    попасть под сторожа. True — окна не будет, False — будет, None —
    композитор промолчал.
    """
    ip = container_ip() if ip is None else ip
    if not ip:
        return None
    result = instance_adb(ip, ["logcat", "-d", "-t", "150"], timeout=25)
    for line in reversed(result.stdout.splitlines()):
        if "single-window:" not in line:
            continue
        if "should_show=1" in line:
            return False
        if "no window" in line or "window stays closed" in line:
            return True
    return None


def app_running(package: str, ip: str | None = None) -> bool:
    """Работает ли приложение внутри контейнера."""
    ip = container_ip() if ip is None else ip
    if not ip:
        return False
    result = instance_adb(ip, ["shell", "pidof", package], timeout=20)
    return bool(result.stdout.strip())


def grant_storage_access(package: str, user: int = 0, ip: str | None = None) -> None:
    """Выдаёт приложению доступ ко всем файлам заранее.

    Иначе Android при первом запуске поднимает поверх игры свой экран
    «Доступ ко всем файлам» — и всё останавливается: игра стоит за ним и не
    рисует ни кадра, окна нет, а контейнер задыхается без картинки. В
    отдельном окне нажать «Разрешить» некому — окна-то и нет.

    Это не обычное разрешение из манифеста (их выдаёт установка ключом
    `-g`), а особое: у него свой экран и свой список. Поэтому выдаём
    отдельно и заранее.
    """
    ip = container_ip() if ip is None else ip
    if not ip:
        return
    args = ["shell", "appops", "set"]
    if user:
        args += ["--user", str(user)]
    args += ["--uid", package, "MANAGE_EXTERNAL_STORAGE", "allow"]
    with suppress(WaydroidError):
        instance_adb(ip, args, timeout=60)


def start_activity(package: str, component: str, user: int = 0, ip: str | None = None) -> str:
    """Запускает приложение и добивается, чтобы оно и правда поднялось.

    Одного `am start` мало. От прошлой жизни приложения остаётся запись
    задачи, и Android доставляет запуск в неё:

        Warning: Activity not started, intent has been delivered to
        currently running top-most instance.

    Звучит благополучно, а процесса нет: экземпляр, которому доставили
    намерение, давно умер, а запись задачи осталась. Бывает и прямее:

        Warning: Activity not started, its current task has been brought
        to the front

    То есть Android поднял пустую задачу и на этом успокоился. Ни
    повторный запуск, ни `am force-stop` записи не убирают — приложение в
    этом окне не откроется уже никогда. Снаружи это «нажимаю запустить, и
    ничего не происходит».

    Лечится тем, что задачу заводят новую: с флагом «несколько задач»
    Android создаёт её вместо того, чтобы поднимать старую.
    """
    ip = container_ip() if ip is None else ip
    if not ip:
        raise WaydroidError(tr("контейнер не запущен"))

    def запустить(новая_задача: bool):
        args = ["shell", "am", "start"]
        if новая_задача:
            args.append("--activity-multiple-task")
        if user:
            args += ["--user", str(user)]
        args += ["-n", component]
        result = instance_adb(ip, args, timeout=120)
        return (result.returncode, (result.stdout + result.stderr).strip())

    text = ""
    for попытка in range(3):
        code, text = запустить(новая_задача=попытка > 0)
        if code != 0 or "Error" in text:
            if попытка == 2:
                raise WaydroidError(text or tr("запуск не удался"))
            time.sleep(6)
            continue
        for _ in range(6):
            time.sleep(2)
            if app_running(package, ip):
                return text
        instance_adb(ip, ["shell", "am", "force-stop", package], timeout=90)
        time.sleep(2)
    raise WaydroidError(text or tr("запуск не удался"))


def clear_stale_task(package: str, ip: str | None = None) -> bool:
    """Гасит приложение, которое работает, но окна которому уже не дадут.

    Окно Waydroid закрывают средствами композитора — и композитор
    запоминает эту задачу как закрытую:

        single-window: tid 1000009 (com.roblox.client) in ignored_apps -> no window

    Обычно приложение слышит просьбу закрыться и задачу свою убирает; тогда
    следующий запуск заводит новую, и окно появляется. Но занятое
    приложение (игра на загрузке, например) просьбу пропускает, задача
    остаётся — и запуск в неё окна не даст уже никогда, сколько ни
    нажимай. Снаружи это ровно то самое «нажимаю запустить, а ничего не
    открывается».

    Поэтому: работает, а на экране его нет — гасим, чтобы Android завёл
    новую задачу.
    """
    if not app_running(package, ip) or screen_state(package, ip) != "нет":
        return False
    if ip is None or ip == container_ip():
        _adb(["shell", "am", "force-stop", package], timeout=90)
    else:
        instance_adb(ip, ["shell", "am", "force-stop", package], timeout=90)
    time.sleep(2)
    return True


def active_app(ip: str | None = None) -> str:
    """Что контейнеру велено показывать."""
    ip = container_ip() if ip is None else ip
    if not ip:
        return ""
    return instance_adb(
        ip, ["shell", "getprop", "waydroid.active_apps"], timeout=20
    ).stdout.strip()


def guard_idle_screens() -> list[str]:
    """Возвращает в покой окна, которым велено показывать несуществующее.

    Контейнер живёт без окна только пока ему не велено ничего показывать.
    Как только он показывает приложение, а окно с экрана исчезает — его
    закрыли, свернули, увели на другой рабочий стол, — композитор хоста
    перестаёт слать кадровые сигналы, и Android внутри задыхается:
    `dequeueBuffer failed, error = -110`, следом виснет WindowManager, и
    сторож убивает system_server по кругу. Вылечить это потом нечем, кроме
    перезапуска контейнера.

    Поэтому смотрим вовремя: если показывать велено, а слоёв этого
    приложения на экране нет — возвращаем контейнер в покой. Сторож даёт на
    это около минуты, так что заглядывать раз в двадцать секунд достаточно.
    """
    вернули = []
    for item in instances():
        ip = item.get("ip") or ""
        if item.get("state") != "RUNNING" or not ip:
            continue
        try:
            показывает = active_app(ip)
            if not показывает or показывает == IDLE_SCREEN:
                continue
            if screen_state(показывает, ip) != "нет":
                continue  # окно на месте или контейнер уже не отвечает
            if item.get("number") == 1:
                _run(
                    ["waydroid", "prop", "set", "waydroid.active_apps", IDLE_SCREEN],
                    timeout=60,
                )
            else:
                set_active_app(ip, IDLE_SCREEN)
            вернули.append(f"{item['number']}:{показывает}")
        except WaydroidError:
            continue
    return вернули


def stop_main_app(package: str) -> None:
    """Закрывает приложение в основном контейнере и возвращает ему экран.

    Вторая половина обязательна: контейнер, которому велено показывать
    закрытое приложение, остаётся без единого окна — и задыхается без
    кадровых сигналов ровно так же, как дополнительные окна.
    """
    adb_connect()
    _adb(["shell", "am", "force-stop", package], timeout=90)
    with suppress(WaydroidError):
        _run(["waydroid", "prop", "set", "waydroid.active_apps", IDLE_SCREEN], timeout=90)


def stop_in_instance(package: str, ip: str) -> None:
    """Закрывает приложение в окне и возвращает контейнеру полный экран.

    Без второй половины контейнер остаётся с указанием показывать
    закрытое приложение — то есть без единого окна, а это его и убивает.
    """
    instance_adb(ip, ["shell", "am", "force-stop", package], timeout=90)
    with suppress(WaydroidError):
        set_active_app(ip, IDLE_SCREEN)


def get_running_user_instances() -> dict[str, set[int]]:
    """Возвращает {package: {user_id1, user_id2, ...}} для всех запущенных процессов."""
    running: dict[str, set[int]] = {}
    if not adb_available():
        return running
    try:
        adb_connect()
        res = _adb(["shell", "ps", "-A"], timeout=10)
        for line in res.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 9:
                user_str = parts[0]
                pkg = parts[-1]
                if user_str.startswith("u") and "_" in user_str:
                    try:
                        uid_part = user_str[1:].split("_")[0]
                        user_id = int(uid_part)
                        running.setdefault(pkg, set()).add(user_id)
                    except ValueError:
                        pass
    except Exception:
        pass
    return running


def get_android_users() -> list[tuple[int, str, str]]:
    """Возвращает список (user_id, name, raw_line) всех пользователей Android."""
    users: list[tuple[int, str, str]] = []
    if not adb_available():
        return [(0, "Owner", "SYSTEM")]
    try:
        adb_connect()
        res = _adb(["shell", "pm", "list", "users"], timeout=15)
        for line in res.stdout.splitlines():
            line = line.strip()
            if line.startswith("UserInfo{") and "}" in line:
                content = line[len("UserInfo{") : line.find("}")]
                parts = content.split(":")
                if len(parts) >= 2:
                    try:
                        uid = int(parts[0])
                        name = parts[1]
                        users.append((uid, name, line))
                    except ValueError:
                        pass
    except Exception:
        pass
    if not any(u[0] == 0 for u in users):
        users.insert(0, (0, "Owner", "SYSTEM"))
    return users


def android_user_kinds() -> dict[int, str]:
    """Тип каждого пользователя: 'system', 'full', 'clone', 'managed'.

    Разница здесь не косметическая. Профили (clone, managed) принадлежат
    пользователю 0 и показываются ВМЕСТЕ с ним — из них и получается
    несколько окон. Полный пользователь (full) — это отдельный сеанс
    Android: когда контейнер переключается на него, всё остальное уходит с
    экрана. Держать такие в списке окон значит обещать невозможное.
    """
    kinds: dict[int, str] = {}
    try:
        adb_connect()
        result = _adb(["shell", "dumpsys", "user"], timeout=90)
    except WaydroidError:
        return kinds

    pending: int | None = None
    for line in result.stdout.splitlines():
        match = _USER_INFO.search(line)
        if match is not None:
            pending = int(match.group(1))
            kinds[pending] = "system" if pending == 0 else "full"
            continue
        if pending is not None and "Type:" in line:
            if CLONE_USER_TYPE in line:
                kinds[pending] = "clone"
            elif "usertype.profile.MANAGED" in line:
                kinds[pending] = "managed"
            elif "usertype.full.SYSTEM" in line:
                kinds[pending] = "system"
            pending = None
    return kinds


def get_app_instances(entry: Entry) -> list[cloner.InstanceInfo]:
    """Возвращает список всех доступных окон для данного приложения."""
    instances: list[cloner.InstanceInfo] = []
    running_map = get_running_user_instances()
    running_users = running_map.get(entry.package, set())
    all_users = get_android_users()

    # Окно 0 — всегда Основное (User 0, оригинальный пакет)
    instances.append(
        cloner.InstanceInfo(
            index=0,
            user_id=0,
            package=entry.package,
            label=tr("Окно 0 (Основное)"),
            apk_path=entry.apk_path,
            is_main=True,
            is_running=(0 in running_users),
        )
    )

    # Профили пользователя 0 — и только они. Полный пользователь Android
    # показать рядом с основным не может: контейнер переключается на него
    # целиком, и остальные окна пропадают с экрана. Такие профили живут в
    # строке «Профиль Android», а не здесь.
    kinds = android_user_kinds()
    clone_index = 1
    for uid, name, _info in all_users:
        if uid == 0 or kinds.get(uid, "full") not in ("clone", "managed"):
            continue
        has_pkg = False
        try:
            res = _adb(["shell", "pm", "path", "--user", str(uid), entry.package], timeout=10)
            if "package:" in res.stdout:
                has_pkg = True
        except Exception:
            pass

        if has_pkg:
            is_running = uid in running_users
            instances.append(
                cloner.InstanceInfo(
                    index=clone_index,
                    user_id=uid,
                    package=entry.package,
                    label=tr("Окно {index} ({name})", index=clone_index, name=name),
                    apk_path=entry.apk_path,
                    is_main=False,
                    is_running=is_running,
                )
            )
            clone_index += 1

    return instances


def _mirror_storage_access(package: str, user: int) -> None:
    """Выдаёт новому окну то же право на файлы, что есть у основного.

    Права в Android раздаются на каждый профиль отдельно, поэтому при
    первом запуске в новом окне поверх игры открывается экран настроек
    «Доступ ко всем файлам» — по одному на каждое окно. Если у основного
    профиля право уже есть, повторять этот разговор незачем.
    """
    try:
        current = _adb(
            ["shell", "appops", "get", "--user", "0", package,
             "MANAGE_EXTERNAL_STORAGE"],
            timeout=30,
        ).stdout
        if "allow" not in current:
            return
        _adb(
            ["shell", "appops", "set", "--user", str(user), package,
             "MANAGE_EXTERNAL_STORAGE", "allow"],
            timeout=30,
        )
    except WaydroidError:
        # Не вышло — Android просто спросит сам, это не повод падать.
        pass


def create_app_instance(entry: Entry, on_stage=None) -> cloner.InstanceInfo:
    """Заводит ещё одно окно приложения — профиль при основном пользователе.

    Одновременно на экране могут жить только профили пользователя 0, и
    Android даёт по одному профилю каждого вида: клон и рабочий. Значит
    окон больше трёх не бывает: основное, клон и рабочее. Полные
    пользователи сюда не годятся — переключение на них убирает с экрана
    всё остальное, поэтому четвёртым окном мы не притворяемся.
    """
    apply_multiwindow_optimizations()
    adb_connect()
    ensure_user_slots()

    kinds = android_user_kinds()
    has_clone = any(kind == "clone" for kind in kinds.values())
    has_managed = any(kind == "managed" for kind in kinds.values())

    if not has_clone:
        stage_text, argv, label = (
            tr("Создаём клон-профиль…"),
            ["--user-type", CLONE_USER_TYPE, "MerciClone"],
            "MerciClone",
        )
    elif not has_managed:
        stage_text, argv, label = (
            tr("Создаём рабочий профиль…"),
            ["--managed", "MerciWork"],
            "MerciWork",
        )
    else:
        raise WaydroidError(
            tr("Больше окон Android не даёт: у основного профиля может быть "
            "только один клон и один рабочий профиль")
        )

    if on_stage:
        on_stage(stage_text)

    result = _adb(
        ["shell", "pm", "create-user", "--profileOf", "0", *argv], timeout=120
    )
    text = (result.stdout + result.stderr).strip()
    match = re.search(r"id (\d+)", text)
    if match is None:
        raise WaydroidError(text or tr("не удалось создать окно"))
    new_uid = int(match.group(1))

    if on_stage:
        on_stage(tr("Активируем приложение в новом окне…"))

    _adb(["shell", "am", "start-user", str(new_uid)], timeout=120)
    installed = _adb(
        [
            "shell",
            "cmd",
            "package",
            "install-existing",
            "--user",
            str(new_uid),
            entry.package,
        ],
        timeout=180,
    )
    if "installed for user" not in installed.stdout.lower():
        _adb(["shell", "pm", "remove-user", str(new_uid)], timeout=120)
        raise WaydroidError(
            (installed.stdout + installed.stderr).strip()
            or tr("не удалось поставить приложение в новое окно")
        )

    _mirror_storage_access(entry.package, new_uid)

    existing = get_app_instances(entry)
    found = next((i for i in existing if i.user_id == new_uid), None)
    if found is not None:
        return found

    return cloner.InstanceInfo(
        index=len(existing),
        user_id=new_uid,
        package=entry.package,
        label=tr("Окно {index} ({name})", index=len(existing), name=label),
        apk_path=entry.apk_path,
        is_main=False,
        is_running=False,
    )


def remove_app_instance(entry: Entry, instance_index: int) -> None:
    """Удаляет окно приложения."""
    if instance_index <= 0 or not adb_available():
        return
    adb_connect()
    instances = get_app_instances(entry)
    inst = next((i for i in instances if i.index == instance_index), None)
    if inst is None or inst.user_id == 0:
        return
    try:
        _adb(["shell", "am", "force-stop", "--user", str(inst.user_id), entry.package], timeout=30)
        _adb(["shell", "pm", "uninstall", "--user", str(inst.user_id), entry.package], timeout=60)
    except Exception:
        pass


def clear_app_instance_data(entry: Entry, instance_index: int) -> None:
    """Сбрасывает данные аккаунта и кеш окна."""
    adb_connect()
    instances = get_app_instances(entry)
    inst = next((i for i in instances if i.index == instance_index), None)
    if inst is None:
        return
    _adb(["shell", "am", "force-stop", "--user", str(inst.user_id), entry.package], timeout=30)
    _adb(["shell", "pm", "clear", "--user", str(inst.user_id), entry.package], timeout=60)


def launch_app_instance(entry: Entry, instance_index: int) -> None:
    """Запускает конкретное окно приложения в многооконном режиме."""
    apply_multiwindow_optimizations()
    adb_connect()

    instances = get_app_instances(entry)
    inst = next((i for i in instances if i.index == instance_index), None)
    if inst is None:
        if instance_index == 0:
            target_uid = 0
        else:
            raise WaydroidError(tr("Окно не найдено"))
    else:
        target_uid = inst.user_id

    if not entry.activity:
        raise WaydroidError(tr("в APK не нашлось activity для запуска"))

    # Окна принадлежат основному профилю и его профилям. Если контейнер
    # стоит на другом полном пользователе, ни одно из них не покажется:
    # Android держит на экране только текущего пользователя. Именно из-за
    # этого «Запустить все» выглядело как «ничего не происходит».
    if current_android_user() != 0:
        switch_android_user(0)

    if target_uid != 0:
        _adb(["shell", "am", "start-user", str(target_uid)], timeout=30)
        # Право на файлы Android сбрасывает обратно, стоит приложению
        # перезапуститься, поэтому выдаём его перед каждым запуском, а не
        # только при создании окна: иначе поверх игры снова открывается
        # экран «Доступ ко всем файлам».
        _mirror_storage_access(entry.package, target_uid)

    # Waydroid рисует окна только того приложения, которое названо здесь.
    # Без этого задача запускается, живёт — и остаётся невидимой.
    _run(
        ["waydroid", "prop", "set", "waydroid.active_apps", entry.package],
        timeout=90,
    )

    # Запускаем в отдельном окне Wayland с флагами создания новой задачи
    res = _adb(
        [
            "shell",
            "am",
            "start",
            "--user",
            str(target_uid),
            "-n",
            f"{entry.package}/{entry.activity}",
            "-a",
            "android.intent.action.MAIN",
            "-c",
            "android.intent.category.LAUNCHER",
            "-f",
            "0x18080000",
            "--windowingMode",
            "5",
        ],
        timeout=120,
    )
    text = (res.stdout + res.stderr).strip()
    if res.returncode != 0 or "Error" in text:
        raise WaydroidError(text or tr("Запуск окна не удался"))

    policy = "immersive.full=*" if multi_windows_on() else "immersive.status=*"
    _adb(["shell", "settings", "put", "global", "policy_control", policy], timeout=60)


def stop_app_instance(entry: Entry, instance_index: int) -> None:
    """Останавливает процесс выбранного окна."""
    if not adb_available():
        return
    adb_connect()
    instances = get_app_instances(entry)
    inst = next((i for i in instances if i.index == instance_index), None)
    if inst is None:
        return
    _adb(["shell", "am", "force-stop", "--user", str(inst.user_id), entry.package], timeout=30)


def launch_all_instances(entry: Entry, on_stage=None) -> None:
    """Запускает главное окно и все созданные клоны по очереди."""
    instances = get_app_instances(entry)
    failures: list[str] = []
    for inst in instances:
        if on_stage:
            on_stage(tr("Запуск {name}…", name=inst.label))
        try:
            launch_app_instance(entry, inst.index)
        except Exception as failure:  # noqa: BLE001 — причина уходит человеку
            failures.append(f"{inst.label}: {failure}")
        # Окну нужно время встать: запуск следующего сразу за предыдущим
        # приводит к тому, что Android сводит их в одну задачу.
        time.sleep(3)

    # Молчать об отказах нельзя: раньше «Запустить все» при полном провале
    # выглядело точно так же, как при успехе.
    if failures and len(failures) == len(instances):
        raise WaydroidError("; ".join(failures))
    if failures:
        raise WaydroidError(
            tr("Открылись не все окна — {details}", details="; ".join(failures))
        )


def stop_all_instances(entry: Entry) -> None:
    """Останавливает все окна и клоны данного приложения."""
    instances = get_app_instances(entry)
    if adb_available():
        adb_connect()
        for inst in instances:
            try:
                _adb(["shell", "am", "force-stop", "--user", str(inst.user_id), entry.package], timeout=15)
            except Exception:
                pass


def apply_multiwindow_optimizations() -> None:
    """Готовит контейнер к работе рядом с другими окнами.

    Раньше эта функция включала многооконный режим Waydroid — и делала
    хуже: в нём окно подгоняется под размер задачи, приложение оказывается
    в углу, а вокруг прозрачная пустота. Теперь режим не трогаем.
    """
    # Многооконный режим здесь НЕ включаем. Он нужен, чтобы показать
    # несколько приложений из ОДНОГО Android, и ценой тому — окно под
    # размер задачи: приложение сидит в углу, а рядом прозрачная пустота.
    # У нас же на каждое окно свой контейнер, и в обычном режиме Waydroid
    # растягивает картинку фиксированного разрешения на всё окно через
    # wp_viewporter — ровно так, как это делают эмуляторы.
    if adb_available():
        try:
            adb_connect()
            _adb(
                [
                    "shell",
                    "settings",
                    "put",
                    "global",
                    "force_resizable_activities",
                    "1",
                ],
                timeout=15,
            )
            _adb(
                ["shell", "settings", "put", "global", "enable_freeform_support", "1"],
                timeout=15,
            )
            _adb(
                ["shell", "setprop", "persist.sys.debug.multi_window", "1"], timeout=15
            )
        except Exception:
            pass


def apply_eco_mode(enabled: bool) -> None:
    """Режим экономии: отключает тяжелые системные анимации Android."""
    if not adb_available():
        return
    try:
        adb_connect()
        val = "0" if enabled else "1"
        _adb(
            ["shell", "settings", "put", "global", "window_animation_scale", val],
            timeout=15,
        )
        _adb(
            ["shell", "settings", "put", "global", "transition_animation_scale", val],
            timeout=15,
        )
        _adb(
            ["shell", "settings", "put", "global", "animator_duration_scale", val],
            timeout=15,
        )
    except Exception:
        pass


def set_fps_limit(fps: int) -> None:
    """Ограничение частоты кадров (FPS) в Android для экономии ресурсов."""
    if not adb_available():
        return
    try:
        adb_connect()
        if fps > 0:
            _adb(
                ["shell", "settings", "put", "system", "min_refresh_rate", str(fps)],
                timeout=15,
            )
            _adb(
                ["shell", "settings", "put", "system", "peak_refresh_rate", str(fps)],
                timeout=15,
            )
            _adb(["shell", "setprop", "debug.sf.fps", str(fps)], timeout=15)
        else:
            _adb(
                ["shell", "settings", "delete", "system", "min_refresh_rate"],
                timeout=15,
            )
            _adb(
                ["shell", "settings", "delete", "system", "peak_refresh_rate"],
                timeout=15,
            )
            _adb(["shell", "setprop", "debug.sf.fps", "0"], timeout=15)
    except Exception:
        pass


def trim_android_memory() -> None:
    """Освобождает неактивную память Android и кэш."""
    if not adb_available():
        return
    adb_connect()
    _adb(["shell", "am", "kill-all"], timeout=30)
    _adb(["shell", "am", "trim-memory", "0", "COMPLETE"], timeout=30)


def create_clone(entry: Entry) -> int:
    """Совместимость: создаёт новый клон приложения."""
    inst = create_app_instance(entry)
    return inst.index


def remove_clone(package: str) -> None:
    """Совместимость: удаляет клон приложения."""
    if adb_available():
        adb_connect()
        _adb(["shell", "pm", "uninstall", package], timeout=120)


def launch_clone(entry: Entry) -> None:
    """Совместимость: открывает клон."""
    instances = get_app_instances(entry)
    clones = [i for i in instances if not i.is_main]
    if clones:
        launch_app_instance(entry, clones[0].index)
    else:
        create_app_instance(entry)
        launch_app_instance(entry, 1)


def set_browser_role(user: int | None = None) -> None:
    """Делает перехватчик Merci браузером по умолчанию внутри Android.

    Роль BROWSER — штатный способ Android назначить обработчик ссылок; наш
    перехватчик ей подходит, потому что ловит http и https без указания
    хоста, то есть ведёт себя как браузер. Без роли Android спрашивает при
    каждой ссылке, чем открыть.
    """
    if not adb_available():
        return
    try:
        adb_connect()
        targets = [user] if user is not None else list(android_users())
        for number in targets:
            # Роль дают только установленному пакету, а в новом профиле
            # приложений нет вовсе. install-existing включает в профиле уже
            # лежащий в контейнере пакет — заново качать и ставить нечего.
            _adb(
                [
                    "shell",
                    "cmd",
                    "package",
                    "install-existing",
                    "--user",
                    str(number),
                    URLFORWARD_PACKAGE,
                ],
                timeout=120,
            )
            _adb(
                [
                    "shell",
                    "cmd",
                    "role",
                    "add-role-holder",
                    "--user",
                    str(number),
                    "android.app.role.BROWSER",
                    URLFORWARD_PACKAGE,
                ],
                timeout=90,
            )
            _wake_urlforward(number)
    except WaydroidError:
        # Контейнер не отвечает — ссылки просто будут спрашивать, чем
        # открыть; ронять из-за этого ничего не нужно.
        pass


def _wake_urlforward(user: int) -> None:
    """Снимает с перехватчика состояние «остановлен» в профиле.

    Пакет, установленный в профиль, но ни разу там не запущенный, Android
    считает остановленным и не предлагает его неявным намерениям — ссылка
    из игры не находит обработчика вовсе. В Android 13 команды `am unstop`
    ещё нет, а единственный способ снять флаг — один раз запустить пакет.

    Поэтому сначала спрашиваем, чем профиль откроет ссылку, и трогаем
    приложение, только если наш перехватчик в ответе не появился: окно
    проверки связи показывает всплывающую подсказку, и делать это на каждый
    запуск незачем.
    """
    resolved = _adb(
        [
            "shell",
            "cmd",
            "package",
            "resolve-activity",
            "--brief",
            "--user",
            str(user),
            "-a",
            "android.intent.action.VIEW",
            "-d",
            "https://example.com",
        ],
        timeout=60,
    )
    if URLFORWARD_PACKAGE in resolved.stdout:
        return
    _adb(
        [
            "shell",
            "am",
            "start",
            "--user",
            str(user),
            "-n",
            f"{URLFORWARD_PACKAGE}/xyz.hackerstone.merci.urlforward.CheckActivity",
        ],
        timeout=60,
    )


def current_android_user() -> int:
    """Профиль, на котором сейчас контейнер. -1 — узнать не вышло.

    Именно -1, а не 0: на неудачном запросе «считаем, что основной» Merci
    решила бы, что переключаться не нужно, и открыла бы приложение в чужом
    профиле.
    """
    adb_connect()
    result = _adb(["shell", "am", "get-current-user"], timeout=60)
    text = result.stdout.strip()
    return int(text) if text.isdigit() else -1


def packages_for_user(user: int) -> set[str]:
    result = _adb(["shell", "pm", "list", "packages", "--user", str(user)], timeout=120)
    return {
        line.split(":", 1)[1].strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith("package:")
    }


_CONFLICT_MARKERS = (
    "INSTALL_FAILED_UPDATE_INCOMPATIBLE",
    "INSTALL_FAILED_VERSION_DOWNGRADE",
    "signatures do not match",
)


def _raise_install_error(text: str) -> None:
    if any(mark in text for mark in _CONFLICT_MARKERS):
        raise InstallConflict(text)
    raise WaydroidError(text or "установка не удалась")


def installed_build(package: str) -> tuple[str | None, str]:
    """Что за сборка пакета стоит в контейнере: (ключ, версия).

    Ключ — начало sha256 установленного APK, такое же, как хвост ключа
    записи в библиотеке. Три разных ответа, и путать их нельзя:

    * строка — установлена вот эта сборка;
    * ``""`` — пакета в контейнере нет;
    * ``None`` — спросить не удалось (нет adb, контейнер не отвечает).

    Путь берём из ``dumpsys``, а не из ``pm path``: последний отвечает от
    имени того профиля, под которым выполняется команда (нулевого), и на
    приложении, установленном только в другой профиль, молча возвращает
    пустоту. Один и тот же файл при этом лежит в контейнере ровно один —
    профили делят код приложения.
    """
    if not adb_available():
        return None, ""
    try:
        adb_connect()
        result = _adb(["shell", "dumpsys", "package", package], timeout=120)
    except WaydroidError:
        return None, ""
    if result.returncode != 0:
        return None, ""

    dump = result.stdout
    version = ""
    match = re.search(r"versionName=(\S+)", dump)
    if match:
        version = match.group(1)

    match = re.search(r"codePath=(\S+)", dump)
    if not match:
        # dumpsys ответил, но пакета в нём нет — значит его правда нет.
        return ("", "") if dump.strip() else (None, "")

    digest = _adb(["shell", "sha256sum", f"{match.group(1)}/base.apk"], timeout=300)
    text = digest.stdout.strip()
    return (text.split()[0][:12] if text else None), version


def installed_hash(package: str) -> str | None:
    return installed_build(package)[0]


def installed_version(package: str) -> str:
    return installed_build(package)[1]


def install_for_user(entry: Entry, user: int) -> None:
    if not os.path.exists(entry.apk_path):
        raise WaydroidError(tr("файл APK пропал из библиотеки"))
    result = _adb(
        ["install", "-r", "--user", str(user), entry.apk_path], timeout=600
    )
    text = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or "Success" not in text:
        _raise_install_error(text)


def uninstall_for_user(package: str, user: int) -> None:
    adb_connect()
    result = _adb(["uninstall", "--user", str(user), package], timeout=180)
    text = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or "Success" not in text:
        raise WaydroidError(text or "удалить не удалось")


def _free_running_users(keep: int) -> None:
    """Останавливает работающие профили, кроме основного и нужного.

    Профиль в фоне занимает место в лимите одновременно запущенных, а
    пользы от него нет: показать его вместе с текущим Android всё равно
    не может.
    """
    result = _adb(["shell", "pm", "list", "users"], timeout=60)
    for line in result.stdout.splitlines():
        match = re.search(r"UserInfo\{(\d+):[^}]*\}\s+running", line)
        if match is None:
            continue
        number = int(match.group(1))
        if number in (0, keep):
            continue
        _adb(["shell", "am", "stop-user", "-f", str(number)], timeout=120)


def switch_android_user(user: int) -> None:
    """Переводит контейнер на этого пользователя.

    Android показывает на экране только одного пользователя, поэтому копии
    одного пакета живут не одновременно, а по очереди — зато без
    переустановки: у каждой своя установка и свои данные.
    """
    adb_connect()
    if current_android_user() == user:
        return
    result = _adb(["shell", "am", "switch-user", str(user)], timeout=120)
    text = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or "Failed to switch" in text:
        # Android держит одновременно лишь несколько профилей (обычно три,
        # считая основной). Когда мест нет, переключение отвечает «Failed to
        # switch to user N» — и выглядит это как поломка на ровном месте.
        # Освобождаем места, останавливая чужие профили, и пробуем ещё раз.
        _free_running_users(keep=user)
        result = _adb(["shell", "am", "switch-user", str(user)], timeout=120)
        text = (result.stdout + result.stderr).strip()
    if result.returncode != 0 or "Failed to switch" in text:
        raise WaydroidError(text or tr("переключиться не удалось"))
    # Переключение не мгновенное: пока идёт, pm и am отвечают на старого
    # пользователя, и приложение открылось бы не в том профиле.
    for _ in range(30):
        time.sleep(1)
        if current_android_user() == user:
            return
    raise WaydroidError(tr("контейнер не переключился на этого пользователя"))


def launch_for_user(entry: Entry, user: int, stage=None) -> None:
    """Открывает приложение в профиле — так, чтобы окно и правда появилось.

    Мало запустить activity. Waydroid по умолчанию работает в однооконном
    режиме и решает, чьё окно показывать, по свойству
    ``waydroid.active_apps``: его выставляет ``waydroid app launch``, а наш
    запуск через ``am start`` — нет. Из-за этого приложение в профиле честно
    запускалось и работало, но окна не было вовсе:

        ps -A | grep roblox   u10_a131 ... com.roblox.client
        waydroid.active_apps  (пусто)

    Поэтому повторяем то же, что делает сам Waydroid: свойство, запуск и
    режим отображения без строки состояния Android.
    """
    if not entry.activity:
        raise WaydroidError(tr("в APK не нашлось activity для запуска"))

    def say(text: str) -> None:
        if stage is not None:
            stage(text)

    # Android мог загрузиться и тут же остаться без system_server: сторож
    # убивает его при зависании и поднимает заново. В этом промежутке любая
    # команда отвечает «Can't find service: package». А живой system_server
    # ещё не значит, что будет окно: картинку рисует SurfaceFlinger, и его
    # заклинивает от закрытия окна средствами композитора. Проверяем и то,
    # и другое — и, если надо, возвращаем контейнер в чувство.
    ensure_container_alive(stage)
    if not wait_system_ready(container_ip()):
        raise ContainerUnreachable(
            tr("Android внутри не отвечает — помогает полный перезапуск контейнера")
        )


    # Контейнер мог перезапуститься прямо сейчас — проверкой выше — и
    # вернуться в основной профиль. Запуск в непереключённом профиле уходит
    # в фон: приложение работает, окна нет.
    if user and current_android_user() != user:
        say(tr("Переключаем профиль…"))
        switch_android_user(user)

    component = f"{entry.package}/{entry.activity}"

    # Приложение может уже работать, но без окна — с задачей, которую
    # композитор запомнил как закрытую. В неё окна не будет; нужна новая.
    if clear_stale_task(entry.package):
        say(tr("Закрываем прежнее окно приложения…"))

    grant_storage_access(entry.package, user)

    # Приложение должно быть не просто установлено, а уже прочитано службой
    # пакетов — иначе запуск отвечает «Activity class does not exist» о
    # приложении, которое стоит на месте.
    wait_package_ready(entry.package)

    start_activity(entry.package, component, user)

    policy = (
        "immersive.full=*"
        if _prop("persist.waydroid.multi_windows") == "true"
        else "immersive.status=*"
    )
    _adb(["shell", "settings", "put", "global", "policy_control", policy], timeout=60)

    # Экран отдаём приложению, когда оно уже рисует, — и ни секундой раньше.
    # Свойство ставим через waydroid: оно системное, и из adb-оболочки его
    # менять не разрешено — служба контейнера делает это от имени system.
    say(tr("Ждём первый кадр приложения…"))
    wait_app_drawing(entry.package, 180)
    _run(["waydroid", "prop", "set", "waydroid.active_apps", entry.package], timeout=90)

    # Композитор объявляет своё решение сразу. Если он отказался показывать
    # окно — задача помечена закрытой, и ждать нечего: гасим приложение и
    # заводим новую, пока Android не задохнулся без картинки.
    time.sleep(4)
    if composer_refuses_window() is True:
        say(tr("Закрываем прежнее окно приложения…"))
        _adb(["shell", "am", "force-stop", entry.package], timeout=90)
        time.sleep(2)
        start_activity(entry.package, component, user)
        wait_app_drawing(entry.package, 180)
        _run(
            ["waydroid", "prop", "set", "waydroid.active_apps", entry.package],
            timeout=90,
        )

    # Запуск отвечает успехом раньше, чем появляется окно, а иногда окна не
    # появляется вовсе. Ждём его — и, если не дождались, возвращаем картинку
    # и открываем заново.
    if wait_app_on_screen(entry.package):
        return
    say(tr("Окно не появилось — возвращаем картинку Android…"))
    # Контейнер, которому велено показывать не рисующее приложение,
    # остаётся без единого окна и задыхается — возвращаем ему экран до
    # перезапуска, чтобы следующая попытка началась со здорового.
    with suppress(WaydroidError):
        _run(["waydroid", "prop", "set", "waydroid.active_apps", IDLE_SCREEN], timeout=90)
    restart_session(stage)

    # Перезапуск возвращает контейнер в основной профиль, а приложение живёт
    # в своём: запуск в непереключённом профиле уходит в фон, и окна снова
    # не будет — только теперь уже без всякой причины на виду.
    if user:
        say(tr("Переключаем профиль…"))
        switch_android_user(user)
    say(tr("Открываем…"))
    wait_package_ready(entry.package)
    start_activity(entry.package, component, user)
    wait_app_drawing(entry.package, 180)
    _run(["waydroid", "prop", "set", "waydroid.active_apps", entry.package], timeout=90)
    if not wait_app_on_screen(entry.package, 90):
        with suppress(WaydroidError):
            _run(["waydroid", "prop", "set", "waydroid.active_apps", IDLE_SCREEN], timeout=90)
        raise ContainerUnreachable(
            tr("Waydroid запустил приложение, но окна на экране так и нет")
        )


def launch_apk(entry: Entry, stage=None) -> None:
    """Открывает приложение в контейнере — и убеждается, что окно появилось.

    Запуск с повтором, и не для красоты: сессия могла подняться секунду
    назад, а Android внутри неё грузится дольше — первая попытка тогда
    возвращает ошибку, хотя через несколько секунд всё откроется. Один
    такой отказ выглядел как «не запустилось», причём ровно в тот момент,
    когда окно приложения уже появлялось на экране.

    Мало и этого. `waydroid app launch` отвечает успехом, пока жива служба
    приложений, — а рисует картинку другая, SurfaceFlinger. Если её
    заклинило (так бывает после закрытия окна средствами композитора),
    приложение честно запустится и будет работать без единого окна.
    Поэтому дожидаемся окна на экране, а если его нет — возвращаем картинку
    перезапуском сессии и пробуем ещё раз.
    """
    if not entry.package:
        raise WaydroidError(tr("неизвестно имя пакета"))

    # Когда activity известна, открываем своим порядком: `waydroid app
    # launch` отдаёт экран приложению до того, как оно нарисует первый кадр,
    # и всё это время контейнер стоит без единого окна. После перезапуска
    # контейнера приложение стартует холодно и медленно — окна нет долго, и
    # Android успевает задохнуться без кадровых сигналов.
    if entry.activity:
        launch_for_user(entry, 0, stage)
        return

    def say(text: str) -> None:
        if stage is not None:
            stage(text)

    def once() -> None:
        last = ""
        for attempt in range(4):
            if attempt:
                time.sleep(6)
            result = _run(["waydroid", "app", "launch", entry.package], timeout=60)
            if result.returncode == 0:
                return
            last = (result.stderr or result.stdout).strip()
        raise WaydroidError(last or tr("запуск не удался"))

    if screen_state() == "затор":
        say(tr("Картинка Android застряла — возвращаем…"))
        restart_session(stage)

    once()
    if wait_app_on_screen(entry.package):
        return

    say(tr("Окно не появилось — возвращаем картинку Android…"))
    restart_session(stage)
    say(tr("Открываем…"))
    once()
    if not wait_app_on_screen(entry.package, 90):
        raise ContainerUnreachable(
            tr("Waydroid запустил приложение, но окна на экране так и нет")
        )


_TOMBSTONES = "$HOME/.local/share/waydroid/data/tombstones"


def recent_crash_async(package: str, since: float, callback) -> None:
    """Ищет отчёт о падении пакета, появившийся после запуска.

    Так пользователь узнаёт, что приложение не «не запустилось», а упало,
    и сразу видит, где именно.
    """

    def work():
        result = _run(
            [
                "sh",
                "-c",
                f'for f in $(ls -t {_TOMBSTONES}/tombstone_[0-9]* 2>/dev/null '
                "| grep -v '\\.pb$' | head -3); do "
                f'  [ "$(stat -c %Y "$f")" -ge {int(since)} ] || continue; '
                f'  grep -q "Cmdline: {package}" "$f" || continue; '
                '  echo "$f"; '
                '  grep -m1 -E "^signal" "$f"; '
                '  grep -m1 -E "#0[0-9].*\\.so" "$f"; '
                "  break; "
                "done",
            ],
            timeout=20,
        )
        return result.stdout.strip()

    in_thread(work, lambda result, error: callback(result or ""))


def crash_log_async(callback) -> None:
    """Отчёт о последнем падении приложения в контейнере.

    Android складывает их в data/tombstones, и каталог принадлежит
    пользователю — прав root не нужно, в отличие от logcat.
    """

    def work():
        result = _run(
            [
                "sh",
                "-c",
                f'f=$(ls -t {_TOMBSTONES}/tombstone_[0-9]* 2>/dev/null '
                "| grep -v '\\.pb$' | head -1); "
                '[ -n "$f" ] || { echo "записей о падениях нет"; exit 0; }; '
                'echo "файл: $f"; echo; sed -n "1,40p" "$f"',
            ],
            timeout=30,
        )
        return result.stdout or result.stderr

    in_thread(work, lambda result, error: callback(result or str(error)))


def resolution() -> tuple[int, int]:
    """Размер окна контейнера — тот, что на самом деле.

    Свойству верить нельзя: оно может остаться от прежней настройки, а
    дисплей у контейнера будет другой. Показывать в карточке одно, когда
    на экране другое, — худший из вариантов, поэтому спрашиваем сам
    контейнер и только при неудаче смотрим на свойство.
    """
    ip = container_ip()
    if ip:
        actual = display_size(ip)
        if all(actual):
            return actual
    try:
        return (
            int(_prop("persist.waydroid.width") or 0),
            int(_prop("persist.waydroid.height") or 0),
        )
    except ValueError:
        return 0, 0


def recommended_render(monitor: tuple[int, int]) -> tuple[int, int]:
    """Разумное разрешение рендера для этого монитора.

    Контейнер на этой машине рисует процессором, поэтому честные 1080p
    стоят вчетверо дороже 540p. Берём 720p как компромисс: читаемо и
    заметно легче. Если монитор и так небольшой, ничего не меняем.
    """
    width, height = monitor
    if not width or height <= 900:
        return monitor
    if renderer()[1]:  # аппаратный рендер — незачем ужимать
        return monitor
    scale = 720 / height
    return int(width * scale) // 2 * 2, 720


def _pacman_script(packages: str) -> str:
    """Установка пакетов с оглядкой на замок базы pacman.

    Замок означает одно из двух: либо рядом работает другой менеджер
    пакетов — тогда надо просто подождать, либо прошлая операция была
    прервана и замок остался осиротевшим. Второй случай — штатная ситуация,
    и снимается он безопасно: владельца у него нет.
    """
    return (
        'for i in $(seq 1 60); do '
        '  pgrep -x "pacman|pamac|pamac-daemon|yay|paru" >/dev/null 2>&1 '
        '|| pgrep -x pacman >/dev/null || break; '
        '  [ "$i" = 1 ] && echo "ждём другой менеджер пакетов"; sleep 3; '
        "done; "
        'if [ -e /var/lib/pacman/db.lck ] && ! pgrep -x pacman >/dev/null; then '
        '  echo "снимаю осиротевший замок /var/lib/pacman/db.lck"; '
        "  rm -f /var/lib/pacman/db.lck; "
        "fi; "
        # Код возврата печатаем сами: pacman в некоторых случаях выходит
        # молча, и без этой строки причина остаётся неизвестной.
        f"pacman -S --needed --noconfirm {packages}; "
        'code=$?; echo "PACMAN_EXIT:$code"; exit $code'
    )


def gamescope_available() -> bool:
    return _ok(["sh", "-c", "command -v gamescope >/dev/null"])


_gamescope_probe = Cache(ttl=600.0)


def gamescope_works() -> bool:
    """Проверяет, что gamescope вообще запускается на этой машине.

    Одного наличия бинаря мало: с проприетарным драйвером NVIDIA он падает
    ещё до окна («zero modifiers for DRM format», «NVVM compilation
    failed»). Проверяем коротким холостым запуском и запоминаем результат.
    """
    cached = _gamescope_probe.get()
    if cached is not None:
        return cached
    if not gamescope_available():
        _gamescope_probe.set(False)
        return False

    ok = _ok(
        [
            "sh",
            "-c",
            'W=$(ls "$XDG_RUNTIME_DIR" 2>/dev/null | grep -m1 "^wayland-[0-9]*$"); '
            'export WAYLAND_DISPLAY="${W:-$WAYLAND_DISPLAY}"; '
            # Запускаем в фоне и смотрим, жив ли он через пять секунд:
            # при падении на NVIDIA он успевает отработать пару секунд и
            # выйти нулём, поэтому кода возврата тут мало.
            "gamescope -W 640 -H 480 -w 640 -h 480 -- sleep 12 >/dev/null 2>&1 & "
            "sleep 5; "
            "if pgrep -x gamescope >/dev/null; then pkill -x gamescope; exit 0; "
            "else exit 1; fi",
        ],
        timeout=30,
    )
    _gamescope_probe.set(ok)
    return ok


def gamescope_running() -> bool:
    """Идёт ли сессия внутри gamescope. Совпадения свойств мало: они могут
    быть выставлены, а картинка всё равно не растянута."""
    return _ok(["sh", "-c", "pgrep -x gamescope >/dev/null"])


def gamescope_step() -> Step:
    return Step(
        key="gamescope",
        title="Установить gamescope",
        hint="им растягивается картинка контейнера на весь экран",
        argv=["sh", "-c", _pacman_script("gamescope")],
        root=True,
        minutes=2,
    )


# Сессию поднимаем внутри gamescope: он рисует контейнер в его собственном
# разрешении и растягивает на весь монитор. Waydroid сам этого не умеет —
# wm size просто рисует меньший экран в углу большой поверхности.
_GAMESCOPE_SESSION = (
    'W=$(ls "$XDG_RUNTIME_DIR" 2>/dev/null | grep -m1 "^wayland-[0-9]*$"); '
    'export WAYLAND_DISPLAY="${W:-$WAYLAND_DISPLAY}"; '
    "setsid gamescope -W __MW__ -H __MH__ -w __W__ -h __H__ -f "
    "-- waydroid session start >/dev/null 2>&1 &"
)


def set_display_async(width: int, height: int, monitor: tuple[int, int], callback) -> None:
    """Рендер в width×height, показ на весь монитор.

    Waydroid растягивать не умеет: его поверхность равна разрешению
    контейнера, а wm size просто рисует экран поменьше в углу — отсюда
    серые поля. Поэтому контейнер запускается в gamescope, который и
    масштабирует картинку до размера монитора. Немного мылит, зато
    пикселей рисуется меньше, а экран занят целиком.
    """
    monitor_width, monitor_height = monitor

    def work():
        render_width = width or monitor_width
        render_height = height or monitor_height

        if render_width and render_height:
            _run(["waydroid", "prop", "set", "persist.waydroid.width", str(render_width)])
            _run(["waydroid", "prop", "set", "persist.waydroid.height", str(render_height)])

        _run(["waydroid", "session", "stop"], timeout=60)

        stretch = (
            gamescope_available()
            and monitor_width
            and (render_width, render_height) != (monitor_width, monitor_height)
        )
        if stretch:
            script = (
                _GAMESCOPE_SESSION.replace("__MW__", str(monitor_width))
                .replace("__MH__", str(monitor_height))
                .replace("__W__", str(render_width))
                .replace("__H__", str(render_height))
            )
        else:
            script = "setsid waydroid session start >/dev/null 2>&1 &"

        _run(["sh", "-c", script], timeout=30)
        forget_state()
        return stretch

    in_thread(work, lambda result, error: callback(error, bool(result)))


def set_resolution_async(width: int, height: int, callback) -> None:
    """Задаёт разрешение и перезапускает сессию — иначе не применится.

    Прав root не требует: действие prop у waydroid их не спрашивает.
    """

    def work():
        if width and height:
            _run(["waydroid", "prop", "set", "persist.waydroid.width", str(width)])
            _run(["waydroid", "prop", "set", "persist.waydroid.height", str(height)])
        else:
            _run(["waydroid", "prop", "set", "persist.waydroid.width", ""])
            _run(["waydroid", "prop", "set", "persist.waydroid.height", ""])
        _run(["waydroid", "session", "stop"], timeout=60)
        _run(["sh", "-c", "setsid waydroid session start >/dev/null 2>&1 &"], timeout=20)
        forget_state()
        return True

    in_thread(work, lambda _result, error: callback(error))


def renderer() -> tuple[str, bool]:
    """(как контейнер рисует, аппаратно ли).

    Waydroid включает аппаратный путь только когда Mesa умеет видеокарту
    хоста: нужен gralloc=gbm. С проприетарным драйвером NVIDIA Mesa карту
    не видит, и остаётся программный рендер — отсюда низкий FPS.
    """
    gralloc = _prop("ro.hardware.gralloc")
    egl = _prop("ro.hardware.egl")
    vulkan = _prop("ro.hardware.vulkan")

    # gbm бывает под разными именами (minigbm_gbm_mesa у waydroid-nvidia),
    # а virtio в vulkan означает Venus — проксирование в драйвер хоста.
    if "gbm" in gralloc or vulkan == "virtio":
        detail = "через Venus" if vulkan == "virtio" else egl or "mesa"
        return f"аппаратный ({detail})", True

    if gralloc or egl or vulkan:
        return f"программный ({egl or 'swiftshader'}) — рисует процессор", False

    # Свойства недоступны. Прежде чем сказать «программный» — а это прямая
    # неправда, если видеокарта как раз занята контейнером, — смотрим, жив ли
    # сервер Venus: на пути waydroid-nvidia именно он держит карту.
    if _run(["pgrep", "-f", "virgl_render_server"]).returncode == 0:
        return tr("аппаратный (через Venus)"), True
    return tr("определить не удалось — контейнер не отвечает"), True


def current_bridge() -> str:
    """libndk, houdini или пусто, если транслятор не настроен."""
    value = native_bridge()
    if "ndk" in value:
        return "libndk"
    if "houdini" in value:
        return "libhoudini"
    return ""


def nvidia_ready() -> tuple[bool, str]:
    """Годится ли машина для аппаратного ускорения через waydroid-nvidia.

    Проекту нужны открытые модули ядра NVIDIA (у закрытых нет DMA-BUF,
    а здесь каждый выводимый буфер — именно он), драйвер 595.71 и новее
    и видеокарта Turing или свежее.
    """
    version = _run(
        ["sh", "-c", "cat /proc/driver/nvidia/version 2>/dev/null"]
    ).stdout
    if not version:
        return False, tr("драйвер NVIDIA не найден")
    if "Open Kernel Module" not in version:
        return False, tr("нужны открытые модули ядра (nvidia-open), у вас закрытые")

    number = re.search(r"\s(\d+)\.(\d+)", version)
    if number and int(number.group(1)) < 595:
        return False, f"драйвер {number.group(0).strip()} старше требуемого 595.71"
    return True, version.split("Kernel Module for x86_64")[-1].strip()[:24]


def nvidia_step(refresh: int = 60) -> Step:
    """Аппаратное ускорение: Vulkan гостя проксируется в драйвер хоста.

    Waydroid рисует через Mesa, а Mesa не умеет проприетарный NVIDIA —
    отсюда swiftshader и рендер на процессоре. waydroid-nvidia подставляет
    гостю Mesa Venus и проксирует вызовы через unix-сокет в настоящий
    драйвер, так что рисует видеокарта.
    """
    helper = askpass_helper()
    sudo = f"env SUDO_ASKPASS={_quote(helper)} sudo -A " if helper else "sudo "
    # yay должен работать от пользователя, а свой sudo звать с -A, иначе он
    # спросит пароль в терминале, которого здесь нет.
    # Пакет conflicts с waydroid, а на вопрос «удалить waydroid?» --noconfirm
    # отвечает отказом — установка одной командой невозможна в принципе.
    # Поэтому: сначала сборка, и только при её успехе замена пакета. Так
    # система не остаётся без waydroid, если сборка не удалась.
    # Образ Android и данные лежат вне пакета и не затрагиваются.
    askpass_env = f"env SUDO_ASKPASS={_quote(helper)} " if helper else ""
    yay = (
        'PKG=$(makepkg --packagelist | head -1); '
        # Пересобирать уже собранное незачем: makepkg на это отвечает ошибкой,
        # а с set -e она увела бы весь шаг в отказ.
        'if [ -f "$PKG" ]; then echo "пакет уже собран"; else '
        f"  {askpass_env}makepkg -s --noconfirm --needed; "
        "fi; "
        '[ -f "$PKG" ] || { echo "пакет не собрался"; exit 1; }; '
        # Именно точное имя: pacman -Qq waydroid отвечает успехом и тогда,
        # когда waydroid лишь предоставляется другим пакетом (provides), —
        # на повторном запуске это уводило шаг в «не найдена цель».
        'if pacman -Qq | grep -qx waydroid; then '
        '  echo "снимаем пакет waydroid — его заменит waydroid-nvidia-bin"; '
        f"  {sudo}pacman -Rdd --noconfirm waydroid; "
        "fi; "
        f'{sudo}pacman -U --noconfirm "$PKG"'
    )
    # makepkg качает исходники одним запросом и умирает на первом обрыве:
    # на этом канале так и вышло — «bad record type» на релизе с GitHub.
    # Поэтому http-источники кладём сами, curl-ом с докачкой и повторами.
    # Готовый файл с верной суммой makepkg просто использует.
    prefetch = (
        'echo "докачиваем исходники"; '
        "makepkg --printsrcinfo | awk '$1 == \"source\" {print $3}' "
        "| while read -r src; do "
        '  name="${src%%::*}"; url="${src#*::}"; '
        '  case "$src" in *::*) ;; *) url="$src"; name="${src##*/}";; esac; '
        '  case "$url" in http*) ;; *) continue;; esac; '
        '  [ -s "$name" ] && continue; '
        '  echo "качаем $name"; '
        "  curl -fL --progress-bar --retry 30 --retry-all-errors --retry-delay 3 "
        '    -C - -o "$name" "$url"; '
        "done"
    )
    script = (
        "set -e; "
        f"D={_quote(os.path.join(_data_root(), 'waydroid-nvidia-bin'))}; "
        'if [ -d "$D/.git" ]; then '
        '  git -C "$D" pull --ff-only >/dev/null 2>&1 || true; '
        "else "
        '  rm -rf "$D"; '
        "  for try in 1 2 3 4 5; do "
        "    git clone --depth 1 "
        'https://aur.archlinux.org/waydroid-nvidia-bin.git "$D" && break; '
        '    rm -rf "$D"; sleep 5; '
        "  done; "
        "fi; "
        '[ -d "$D/.git" ] || { echo "не удалось получить PKGBUILD из AUR"; exit 1; }; '
        'cd "$D"; '
        f"{prefetch}; "
        'echo "ставим waydroid-nvidia-bin (заменяет пакет waydroid)"; '
        f"{yay}; "
        f'echo "настраиваем гостевой стек, обновление {refresh} Гц"; '
        f"{sudo}waydroid-nvidia-setup --refresh {refresh}; "
        f"{sudo}systemctl enable --now waydroid-container.service; "
        "systemctl --user enable --now wd-venus.service; "
        "waydroid session stop >/dev/null 2>&1 || true; "
        "setsid waydroid session start >/dev/null 2>&1 & "
        'echo "готово: проверьте строку GLES в dumpsys SurfaceFlinger"'
    )
    return Step(
        key="nvidia",
        title="Аппаратное ускорение NVIDIA",
        hint="waydroid-nvidia: Vulkan гостя проксируется в драйвер хоста",
        argv=["sh", "-c", script],
        downloads="waydroid-nvidia-bin из AUR",
        minutes=8,
    )


def magisk_step(remove: bool = False) -> Step:
    """Root внутри контейнера через Magisk Delta — поставить или убрать.

    Обычная возможность своего же Android: доступ к системным разделам,
    отладка, модули. Аттестацию устройства это не проходит — наоборот,
    делает контейнер для проверок ещё более «неродным», и убрать root потом
    так же важно, как поставить.
    """
    action = "remove" if remove else "install"
    return Step(
        key="magisk-remove" if remove else "magisk",
        title="Убрать root (Magisk)" if remove else "Установить root (Magisk)",
        hint=tr("waydroid_script {action} magisk, с перезапуском контейнера", action=action),
        argv=["sh", "-c", _bridge_script("magisk", action)],
        root=True,
        minutes=6,
    )


def container_restart_step() -> Step:
    """Полный перезапуск контейнера — когда перезапуска сессии не хватило."""
    helper = askpass_helper()
    sudo = f"env SUDO_ASKPASS={_quote(helper)} sudo -A " if helper else "sudo "
    # Дополнительные окна держат прежнее монтирование образа: пока они
    # работают, контейнер не поднимется вовсе, а оверлей с транслятором
    # Waydroid отключит насовсем. Поэтому гасим их первым делом — и молча,
    # если их нет.
    stop_extra = "".join(
        f"{sudo}lxc-stop -P /var/lib/waydroid/lxc -n waydroid{number} -k "
        ">/dev/null 2>&1 || true; "
        for number in _registry()
    )
    script = (
        "set -e; "
        'echo "останавливаем сессию"; '
        "waydroid session stop >/dev/null 2>&1 || true; "
        "pkill -9 -f '[w]aydroid session start' || true; "
        + ('echo "гасим дополнительные окна"; ' + stop_extra if stop_extra else "")
        + "sleep 2; "
        'echo "перезапускаем контейнер"; '
        f"{sudo}systemctl restart waydroid-container.service; "
        "sleep 4; "
        # Оверлей мог быть отключён прошлой неудачей — возвращаем его,
        # иначе транслятор ARM не появится и после перезапуска.
        f"{sudo}sed -i 's/^mount_overlays = False/mount_overlays = True/' "
        "/var/lib/waydroid/waydroid.cfg || true; "
        'echo "поднимаем сессию"; '
        # Команду запуска собираем из переменной: если написать её целиком,
        # строка «waydroid session start» окажется в командной строке самого
        # скрипта — и pkill выше убьёт его же, на середине работы.
        'S="session start"; setsid waydroid $S >/dev/null 2>&1 &'
    )
    return Step(
        key="container-restart",
        title="Перезапустить контейнер целиком",
        hint="служба waydroid-container и сессия заново",
        argv=["sh", "-c", script],
        minutes=2,
        background=True,
    )


def multiuser_ready() -> tuple[bool, str]:
    """Готов ли контейнер держать несколько пользователей Android."""
    if not adb_available():
        return False, tr("на хосте нет adb (пакет android-tools)")
    if _prop("fw.max_users") in ("", "0", "1"):
        return False, tr("в контейнере не разрешено больше одного пользователя")
    return True, tr("готово")


def multiuser_step() -> Step:
    """Разрешает контейнеру несколько пользователей Android.

    Три вещи разом, иначе толку нет:

    * fw.max_users — сам предел; по умолчанию Android в этом образе держит
      одного пользователя, и pm create-user отвечает отказом;
    * ro.adb.secure=0 — Merci ходит к pm и am через adbd контейнера, а с
      проверкой ключа первое подключение упирается в окно подтверждения
      внутри Android. Порт слушает только мост контейнера, на машине он и
      так был открыт;
    * android-tools — сам adb на хосте.

    Оба свойства ro.* читает init при загрузке, поэтому пишем их в
    waydroid_base.prop и перезапускаем контейнер.
    """
    helper = askpass_helper()
    sudo = f"env SUDO_ASKPASS={_quote(helper)} sudo -A " if helper else "sudo "
    prop = "/var/lib/waydroid/waydroid_base.prop"
    script = (
        "set -e; "
        'if command -v adb >/dev/null; then echo "adb уже есть"; else '
        '  echo "ставим android-tools"; '
        f"  {sudo}pacman -S --needed --noconfirm android-tools; "
        "fi; "
        'echo "разрешаем нескольких пользователей Android"; '
        # Старые значения убираем, чтобы файл не оброс повторами при
        # каждом включении.
        f"{sudo}sed -i '/^fw\\.max_users=/d;/^ro\\.adb\\.secure=/d' {prop}; "
        f"{sudo}sh -c 'printf \"fw.max_users=4\\nro.adb.secure=0\\n\" >> {prop}'; "
        'echo "перезапускаем контейнер"; '
        f"{sudo}systemctl restart waydroid-container.service; "
        "sleep 3; "
        "setsid waydroid session start >/dev/null 2>&1 &"
    )
    return Step(
        key="multiuser",
        title="Разрешить несколько пользователей Android",
        hint="fw.max_users, доступ к adb контейнера и перезапуск",
        argv=["sh", "-c", script],
        minutes=3,
        background=True,
    )


def switch_bridge_step(target: str) -> Step:
    """Шаг смены транслятора ARM: libndk ↔ libhoudini.

    Имена именно такие, как их принимает waydroid_script: 'libndk' и
    'libhoudini'. На 'houdini' он отвечает invalid choice.

    Они по-разному переживают код, который приложение генерирует на лету:
    там, где один падает, второй иногда работает.
    """
    return Step(
        key="switch-bridge",
        title=tr("Переключить транслятор на {target}", target=target),
        hint="переустановка через waydroid_script",
        argv=["sh", "-c", _bridge_script(target)],
        root=True,
        minutes=6,
    )


def stop_session_async(callback) -> None:
    """Выключает контейнер.

    Одной команды мало: `waydroid session stop` регулярно упирается в
    таймаут D-Bus и оставляет процесс менеджера сессии живым, после чего
    состояние расходится — status показывает RUNNING, а prop отвечает, что
    сессия остановлена. Поэтому после команды добиваем остатки и
    проверяем результат.
    """

    def work():
        _run(["waydroid", "session", "stop"], timeout=40)
        _run(["sh", "-c", "pkill -9 -f '[w]aydroid session start' || true"], timeout=15)
        _run(["sh", "-c", "sleep 3"], timeout=10)
        forget_state()
        return status(use_cache=False)

    def done(result, error):
        if error is not None or result is None:
            callback("не удалось выключить")
            return
        ready, detail = result
        callback("" if not ready else f"контейнер всё ещё работает: {detail}")

    in_thread(work, done)


def show_full_ui() -> None:
    try:
        Gio.Subprocess.new(host_argv(["waydroid", "show-full-ui"]), Gio.SubprocessFlags.NONE)
    except GLib.Error as exc:
        raise WaydroidError(exc.message) from exc
