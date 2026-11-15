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

import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import threading
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field

from gi.repository import Gio, GLib

from . import hostinfo, updates
from .hostexec import (
    TIMED_OUT, Cache, host_argv, in_thread, run as _run, run_detached as _run_detached,
    succeeds as _ok,
)
from .i18n import tr
from .library import Entry


class WaydroidError(Exception):
    pass


# Состояние меняется редко, а спрашивают его на каждый показ карточки.
_state_cache = Cache(ttl=4.0)
_bridge_cache = Cache(ttl=10.0)

# Контейнеры, с которыми прямо сейчас идёт операция (запуск приложения).
# Нужны сторожу окон: пока запуск не закончил, вмешиваться нельзя.
_operating: set[str] = set()
_operating_guard = threading.Lock()


@contextmanager
def operating(ip: str):
    """Помечает контейнер занятым на время операции."""
    with _operating_guard:
        _operating.add(ip)
    try:
        yield
    finally:
        with _operating_guard:
            _operating.discard(ip)


def is_operating(ip: str) -> bool:
    with _operating_guard:
        return ip in _operating

_bridge_expired_cache = Cache(ttl=60.0)
_image_sdk_cache = Cache(ttl=120.0)
_status_text_cache = Cache(ttl=1.5)

# Что за система на хосте: дистрибутив, менеджер пакетов, тип сеанса, какие
# нужные программы есть. Merci писалась на одной машине (Arch, Hyprland,
# Wayland) и долго считала, что других не бывает: `pacman`, `hyprctl`,
# `kdialog` были зашиты в команды. Теперь команды выбираются по этим сведениям.
_host: hostinfo.Host | None = None
_host_guard = threading.Lock()


def host(refresh: bool = False) -> hostinfo.Host:
    """Сведения о хосте. Первый вызов опрашивает хост — звать из потока."""
    global _host
    with _host_guard:
        if _host is None or refresh:
            def ask(argv: list[str]) -> tuple[int, str]:
                result = _run(argv, timeout=30)
                return result.returncode, result.stdout

            _host = hostinfo.detect(ask)
        return _host


def host_known() -> hostinfo.Host:
    """То же, но без обращения к хосту: что уже узнали, иначе «неизвестно».

    Для мест, которые исполняются в главном потоке и ждать не могут. Опрос
    запускается при старте приложения, так что к первому нажатию ответ есть.
    """
    return _host if _host is not None else hostinfo.Host()


def _sudo(askpass: str | None = None) -> str:
    """Приставка, с которой команда исполняется под root (с пробелом в конце).

    Там, где есть sudo и окно для пароля, — `sudo -A`: пароль спрашивает
    системное окно и отдаёт его прямо sudo. Иначе — то, что на этой системе
    есть: pkexec или run0 (при живом агенте polkit), doas, просто sudo.
    """
    helper = askpass_helper() if askpass is None else askpass
    tools = host_known().tools
    if not tools:
        # Хост ещё не опрошен — ведём себя как раньше.
        return f"env SUDO_ASKPASS={_quote(helper)} sudo -A " if helper else "sudo "
    prefix = hostinfo.root_prefix(tools, helper)
    if not prefix:
        return "sudo "
    if prefix[:1] == ["env"]:
        return f"env SUDO_ASKPASS={_quote(helper)} sudo -A "
    return " ".join(prefix) + " "


def root_needs_terminal() -> bool:
    """Правда ли, что пароль на этой системе можно ввести только в терминале."""
    helper = askpass_helper()
    tools = host_known().tools
    if not tools:
        return not helper
    return hostinfo.root_needs_terminal(tools, helper)


def _status_text(fresh: bool = False):
    """Ответ `waydroid status`.

    Им пользуются и проверка готовности, и поиск адреса контейнера, а стоит
    он как запуск интерпретатора: waydroid написан на Python. С пульсом
    карточки это выходило по нескольку запусков на каждый удар — поэтому
    ответ живёт полторы секунды и делится между всеми, кто спросил.
    """
    cached = None if fresh else _status_text_cache.get()
    if cached is not None:
        return cached
    result = _run(["waydroid", "status"], timeout=40)
    _status_text_cache.set(result)
    return result


# -- состояние -----------------------------------------------------------


def status(use_cache: bool = True) -> tuple[bool, str]:
    """(готов ли Waydroid к запуску приложений, что показать пользователю).

    Блокирующая: вызывать только из потока, см. status_async.
    """
    cached = _state_cache.get() if use_cache else None
    if cached is not None:
        return cached

    result = _probe()
    # «Не знаем» не запоминаем: следующий вопрос должен дойти до хоста, а
    # не получить то же «не знаем» из памяти.
    if result[1] != _UNKNOWN:
        _state_cache.set(result)
    return result


# Ответ, когда `waydroid status` промолчал или сказал непонятное.
_UNKNOWN = "состояние неясно"
_waydroid_found = False


def _waydroid_installed() -> bool:
    """Есть ли на хосте команда waydroid. «Да» запоминаем: поставленное не
    исчезает, а спрашивать об этом на каждом ударе пульса незачем."""
    global _waydroid_found
    if not _waydroid_found:
        _waydroid_found = _ok(["sh", "-c", "command -v waydroid"])
    return _waydroid_found


def _probe() -> tuple[bool, str]:
    # Описание возвращаем русским исходником, без перевода: по нему plan()
    # решает, нужен ли шаг загрузки образа. Переведи мы здесь — сравнение
    # перестало бы совпадать при английском языке, и шаг молча пропал бы.
    # Переводит тот, кто показывает.
    if not _waydroid_installed():
        return False, "не установлен"
    result = _status_text(fresh=True)
    if result.returncode == TIMED_OUT:
        # Команда не ответила в срок — под нагрузкой или с занятым D-Bus
        # это обычное дело. Это не «остановлен» и не «недоступен».
        return False, _UNKNOWN
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
    return False, _UNKNOWN


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


def _setting(name: str) -> str:
    """Свойство контейнера — и у выключенного тоже.

    У работающего спрашиваем его самого. А выключенный отвечает на
    `waydroid prop get` пустотой, и принять её за «нет» значит уверенно
    сказать неправду: «подповерхности выключены», «больше одного
    пользователя не разрешено» — при том, что в настройках всё на месте.
    Поэтому у молчащего контейнера читаем файлы, с которыми он загрузится:
    waydroid_base.prop, а затем раздел [properties] настроек Waydroid.
    """
    value = _prop(name)
    if value:
        return value
    for line in _host_file("/var/lib/waydroid/waydroid_base.prop").splitlines():
        key, _, found = line.partition("=")
        if key.strip() == name:
            return found.strip()
    return waydroid_cfg().get("properties", {}).get(name, "")


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
            "/var/lib/waydroid/waydroid.cfg 2>/dev/null",
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


def bridge_expired() -> bool:
    """Стоит ли в контейнере libhoudini с истёкшим сроком годности.

    Библиотеку читаем с хоста, из оверлея Waydroid: так ответ есть и при
    выключенном контейнере, и без единой команды внутри Android.
    """
    cached = _bridge_expired_cache.get()
    if cached is not None:
        return cached
    expired = False
    if "houdini" in native_bridge():
        result = _run(["md5sum", _HOUDINI_ON_HOST], timeout=30)
        digest = result.stdout.split()[0] if result.returncode == 0 and result.stdout.split() else ""
        if digest in _HOUDINI_BUILDS:
            until = _HOUDINI_BUILDS[digest]
            expired = until is None or time.strftime("%Y-%m-%d") >= until
    _bridge_expired_cache.set(expired)
    return expired


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
    """Возвращает оверлей на место и перезапускает сессию.

    Делает помощник (prepare): гасит дополнительные окна и правит настройку
    одним заходом. Окна надо гасить обязательно — пока они работают, старт
    основной сессии кончается тем, что Waydroid выключает оверлей снова.
    Прежний шаг этого не делал, запускал сессию от root (весь сценарий шёл
    под sudo) и при отменённом пароле рапортовал об успехе.
    """
    return Step(
        key="overlay",
        title=tr("Вернуть транслятор ARM64"),
        hint=tr("Waydroid отключил оверлей, а в нём libhoudini — контейнер перезапустится"),
        argv=["sh", "-c", _update_script(_helper_command("prepare", "0", "0"))],
        minutes=2,
        asks_root=True,
        then_session=True,
    )


def forget_state() -> None:
    """Сбросить кеш — после установки или запуска сессии."""
    _state_cache.clear()
    _bridge_cache.clear()
    _bridge_expired_cache.clear()
    _status_text_cache.clear()
    # И память о том, что уже послано в окна: после перезапуска контейнера
    # предел кадров в нём пропал, а обновлятор в пересозданном окне не
    # спрятан. Обычно это видно по номеру процесса контейнера; сброс здесь —
    # страховка на случай, когда номер узнать не удалось.
    _quick_sent.clear()
    _updater_hidden.clear()


@dataclass(frozen=True)
class ContainerStatus:
    """Что известно о контейнере — всё, от чего зависит карточка приложения."""

    ready: bool = False            # сессия запущена
    detail: str = _UNKNOWN         # исходная русская строка от status()
    bridge: str = ""               # транслятор ARM или пусто
    network: bool = True           # сетевой экран не мешает
    firewall: str = "none"         # какой экран включён (ufw, firewalld…)
    expired: bool = False          # у транслятора истёк срок
    adb_open: bool = False         # adbd без проверки ключа
    has_adb: bool = True           # adb есть на хосте
    profiles_broken: bool = False  # профили включены, а контейнер их не держит
    nested_missing: bool = False   # сеанс X11, а вложенного композитора нет

    @property
    def asleep(self) -> bool:
        """Сессия просто выключена — запуск поднимет её сам."""
        return not self.ready and self.detail == "сессия остановлена"


def container_status(needs_bridge: bool, multiuser: bool = False) -> ContainerStatus:
    """Опрашивает хост и контейнер. Блокирующая: звать из потока."""
    ready, detail = status()
    system = host()
    asleep = not ready and detail == "сессия остановлена"
    # Транслятор виден и при выключенной сессии — он записан в настройках
    # контейнера. Спрашивать его только у работающей значило после каждой
    # перезагрузки показывать «нужна трансляция» там, где всё на месте.
    # Спрашиваем его всегда, а не только для ARM-приложений: ответ один на
    # контейнер, и карточка другого приложения не должна ждать его заново.
    del needs_bridge
    bridge = native_bridge() if (ready or asleep) else ""
    has_adb = adb_available()
    return ContainerStatus(
        ready=ready,
        detail=detail,
        bridge=bridge,
        network=not firewall_blocks_container() if ready else True,
        firewall=_firewall_kind(),
        expired=bridge_expired() if bridge else False,
        adb_open=adb_open(),
        has_adb=has_adb,
        # Проверяем на работающем контейнере: иначе «свойства нет» значило
        # бы всего лишь «спросить не у кого».
        profiles_broken=bool(multiuser and ready and has_adb and not multiuser_ready()[0]),
        nested_missing=bool(
            hostinfo.needs_nested_compositor(system)
            and not hostinfo.nested_compositor(system, system.tools)
        ),
    )


def state_async(needs_bridge: bool, callback, multiuser: bool = False) -> None:
    """Опрашивает Waydroid в потоке и отдаёт ContainerStatus."""

    def done(result, error):
        callback(result if error is None and result is not None else ContainerStatus())

    in_thread(lambda: container_status(needs_bridge, multiuser), done)


def plan_async(needs_bridge: bool, callback, monitor: tuple[int, int] = (0, 0)) -> None:
    """Строит план установки в потоке: там десяток обращений к хосту."""
    def done(result, error):
        # Сорвавшийся план — не пустой план: пустой мастер читает как «всё
        # готово», и человек с неустановленным Waydroid видел «Waydroid готов».
        if error is not None or result is None:
            callback([
                Step(
                    key="plan-failed",
                    title="Проверка готовности не удалась",
                    hint=tr("Merci не смогла выяснить, что уже установлено: {error}",
                            error=error if error is not None else tr("хост не ответил")),
                    manual=True,
                )
            ])
            return
        callback(result)

    in_thread(lambda: plan(needs_bridge, monitor), done)


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


def install_and_launch(entry: Entry, stage=None, multiuser: bool = False,
                       replace: str = "") -> bool:
    """То же, но в текущем потоке: от выключенной сессии до окна на экране.

    ``replace`` — что делать, если в основном контейнере стоит другая сборка
    того же пакета (см. put_build): по умолчанию — ничего, наружу уходит
    InstallConflict.
    """
    if stage is None:
        stage = lambda _text: None  # noqa: E731

    # Выключенную сессию поднимаем сами. Раньше обычный путь на этом
    # месте шёл дальше, упирался в молчащий контейнер и отвечал
    # «Контейнер не отвечает» с предложением перезапуска под root — на
    # контейнер, который просто никто не включил.
    ensure_session(stage)
    require_adb()
    ensure_container_alive(stage)
    if multiuser and entry.profile:
        stage(tr("Готовим профиль Android…"))
        user = ensure_android_user(entry)
        put_build(entry, None, user, replace, 1, stage)
        stage(tr("Переключаем профиль…"))
        switch_android_user(user)
        # Роль браузера у каждого профиля своя: без этого ссылки из игры
        # открывались внутренним браузером Android, а не на хосте.
        if URLFORWARD_PACKAGE in packages_for_user(0):
            set_browser_role(user)
        stage(tr("Открываем…"))
        launch_for_user(entry, user, stage)
        return True

    # Сборку сверяем (и ставим) до любых действий с профилем. Раньше
    # сначала переключался профиль — а это закрывает всё, что в нём
    # открыто, — и только потом выяснялось, что в контейнере стоит другая
    # сборка: человек получал вопрос «заменить?» уже с убитой игрой, даже
    # если отвечал «нет».
    put_build(entry, None, 0, replace, 1, stage)

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

    stage(tr("Открываем…"))
    launch_apk(entry, stage)
    return True


def require_adb() -> None:
    """Без adb на хосте Merci не может ни поставить приложение, ни открыть его.

    Раньше отсутствие adb всплывало минутами позже и в чужом обличье:
    «контейнер не отвечает», перезапуски, запрос пароля на перезапуск
    службы — на контейнер, с которым всё было в порядке.
    """
    if not adb_available():
        raise WaydroidError(
            tr("на компьютере нет adb — без него Merci не может управлять "
               "Android. Откройте «Подготовить Waydroid»: она поставит его")
        )


def package_users(package: str, ip: str | None = None) -> tuple[tuple[int, str], ...]:
    """В каких профилях Android пакет установлен: ((номер, имя профиля), …)."""
    ip = ip or _require_ip()
    safe_package(package)
    listing = instance_adb(ip, ["shell", "pm", "list", "users"], timeout=60).stdout
    found = []
    for match in re.finditer(r"UserInfo\{(\d+):([^:]*):", listing):
        number, name = int(match.group(1)), match.group(2)
        answer = instance_adb(
            ip, ["shell", "pm", "list", "packages", "--user", str(number), package],
            timeout=60,
        ).stdout
        # Фильтр у pm — по вхождению: «com.x» находит и «com.x.m1». Нужно точное имя.
        if any(line.strip() == f"package:{package}" for line in answer.splitlines()):
            found.append((number, name))
    return tuple(found)


def _known_build(package: str, ip: str) -> tuple[str, str]:
    """Сборка в контейнере — достоверно: (начало sha256 или пусто, версия).

    «Не удалось спросить» раньше молча считалось «конфликта нет», и вместо
    выбранной сборки запускалась та, что стояла, — с чужими данными, либо
    запуск падал на точке входа, которой в ней нет. Переспрашиваем, и если
    Android так и не ответил, останавливаемся с понятным отказом.
    """
    for attempt in range(3):
        if attempt:
            time.sleep(3)
        current, version = installed_build(package, ip)
        if current is not None:
            return current, version
    raise ContainerUnreachable(
        tr("не удалось узнать, какая сборка стоит в контейнере: Android не ответил")
    )


def put_build(entry: Entry, ip: str | None = None, user: int = 0, mode: str = "",
              window: int = 1, stage=None) -> str:
    """Добивается, чтобы в контейнере стояла именно сборка этой записи.

    Отдаёт, что пришлось сделать: ``kept`` (она и стояла), ``installed``
    (пакета не было), ``updated`` (обновили на месте, данные целы),
    ``replaced`` (сняли прежнюю и поставили эту — данные пакета стёрты).

    Если стоит другая сборка, всё решает ``mode``:

    * ``""`` — ничего не трогаем, наружу уходит InstallConflict: что делать,
      решает человек;
    * ``"update"`` — пробуем обновить на месте. Android позволяет это, когда
      подпись та же и версия не ниже: данные (вход в аккаунт, сохранения)
      при этом остаются. Раньше Merci при любом расхождении сразу снимала
      пакет — и стирала данные там, где этого можно было избежать. Отказ
      Android уходит наружу как InstallConflict с ``needs_wipe``;
    * ``"wipe"`` — снимаем пакет и ставим заново. Данные пакета в этом
      контейнере стираются во всех профилях.
    """
    say = stage if stage is not None else (lambda _text: None)
    if not os.path.exists(entry.apk_path):
        raise WaydroidError(tr("файл APK пропал из библиотеки"))
    if ip is None:
        adb_connect()
        ip = _require_ip()
    package = entry.package
    who = ["--user", str(int(user))] if user else []
    current, version = _known_build(package, ip)

    def install() -> str:
        result = instance_adb(ip, ["install", "-r", *who, entry.apk_path], timeout=900)
        _installs_changed()
        return (result.stdout + result.stderr).strip()

    if current and entry.file_hash and current == entry.file_hash:
        # Сборка та самая, но в нужном профиле она может быть не включена:
        # код у пакета один на контейнер, а установлен он по профилям.
        if not package_ready(package, ip, user):
            say(tr("Устанавливаем в профиль…"))
            answer = install()
            if "Success" not in answer:
                _raise_install_error(answer)
        return "kept"

    if not current:
        say(tr("Устанавливаем в контейнер…") if window == 1
            else tr("Ставим приложение в окно {number}…", number=window))
        answer = install()
        if "Success" not in answer:
            _raise_install_error(answer)
        return "installed"

    if not mode:
        raise InstallConflict(
            f"в контейнере стоит другая сборка {package}"
            + (f" (версия {version})" if version else ""),
            build=current, version=version, window=window,
        )

    if mode == "update":
        say(tr("Обновляем сборку на месте…"))
        answer = install()
        if "Success" in answer:
            return "updated"
        if any(mark in answer for mark in _CONFLICT_MARKERS):
            raise InstallConflict(
                answer, build=current, version=version, window=window,
                needs_wipe=True, users=package_users(package, ip),
            )
        _raise_install_error(answer)

    say(tr("Убираем прежнюю сборку…"))
    removed = instance_adb(ip, ["uninstall", package], timeout=300)
    _installs_changed()
    if "Success" not in (removed.stdout + removed.stderr):
        raise WaydroidError(
            (removed.stdout + removed.stderr).strip() or tr("удалить не удалось")
        )
    say(tr("Устанавливаем в контейнер…") if window == 1
        else tr("Ставим приложение в окно {number}…", number=window))
    answer = install()
    if "Success" not in answer:
        _raise_install_error(answer)
    return "replaced"


def own_windows(entry: Entry) -> list[int]:
    """Номера дополнительных окон, закреплённых за этой записью."""
    return sorted(number for number, owner in _registry().items() if owner == entry.slug)


def where_to_launch(entry: Entry) -> dict:
    """Куда пойдёт «Запустить» для этой записи. Ничего не меняет.

    Android держит одно имя пакета как одну установку на контейнер, а в
    библиотеке спокойно лежат несколько сборок одного пакета — обычный
    клиент и модифицированный, разные версии. Раньше «Запустить» всегда шёл
    в основное окно, и вторая сборка упиралась в вопрос «заменить? данные
    сотрутся» — даже когда у неё было своё окно, где она уже стоит.

    Теперь у сборки есть дом:

    * основное окно — если там стоит она же или этого пакета там нет;
    * своё дополнительное окно — если основное занято другой сборкой;
    * если своего окна нет — решает человек (``window`` равно нулю):
      завести окно или заменить сборку в основном.

    Отдаёт словарь: ``window`` (номер окна, 0 — надо спросить),
    ``conflict`` (основное занято другой сборкой), ``other`` (её версия),
    ``own`` (свои окна).
    """
    own = own_windows(entry)
    current, version = installed_build(entry.package)
    conflict = bool(current) and bool(entry.file_hash) and current != entry.file_hash
    target = {
        "window": 1,
        "conflict": conflict,
        "other": version if conflict else "",
        "build": current if conflict else "",
        "own": own,
    }
    if not conflict:
        return target
    if not own:
        target["window"] = 0
        return target
    # Из своих окон берём то, что уже работает: ему не нужен ни пароль, ни
    # полминуты на загрузку Android.
    states = {item["number"]: item["state"] for item in instances(entry.slug)}
    running = [number for number in own if states.get(number) == "RUNNING"]
    target["window"] = (running or own)[0]
    return target


def play(entry: Entry, stage=None, multiuser: bool = False, routed=None) -> int:
    """«Запустить»: поднимает что нужно и открывает запись в её окне.

    Отдаёт номер окна, в котором приложение открылось. ``routed(номер)``
    зовётся, когда выбрано не основное окно, — чтобы интерфейс показал, где
    идёт работа. InstallConflict уходит наружу, только если основное окно
    занято другой сборкой, а своего окна у записи нет: дальше решает человек.
    """
    ensure_session(stage)
    ensure_container_alive(stage)
    target = where_to_launch(entry)
    if target["window"] == 1:
        install_and_launch(entry, stage, multiuser)
        return 1
    if target["window"] > 1:
        if routed is not None:
            routed(target["window"])
        open_in_window(entry, target["window"], stage, multiuser)
        return target["window"]
    raise InstallConflict(
        f"в контейнере стоит другая сборка {entry.package}"
        + (f" (версия {target['other']})" if target["other"] else ""),
        build=target["build"],
        version=target["other"],
    )


def play_async(entry: Entry, callback, on_stage=None, multiuser: bool = False,
               on_routed=None) -> None:
    """То же в потоке. callback(ошибка, номер окна)."""

    def stage(text):
        if on_stage is not None:
            GLib.idle_add(lambda: (on_stage(text), False)[1])

    def routed(number):
        if on_routed is not None:
            GLib.idle_add(lambda: (on_routed(number), False)[1])

    in_thread(
        lambda: play(entry, stage, multiuser, routed),
        lambda result, error: callback(error, result or 0),
    )


def open_in_new_window(entry: Entry, stage=None, multiuser: bool = False, created=None) -> int:
    """Заводит записи своё окно и открывает её там. Отдаёт номер окна.

    Это ответ «отдельное окно» на вопрос о занятом основном: прежняя
    сборка остаётся нетронутой, у этой появляется свой Android.
    """
    number = create_window(entry.slug, stage)
    if created is not None:
        created(number)
    open_in_window(entry, number, stage, multiuser, fresh=True)
    return number


def replace_install_async(entry: Entry, callback, on_stage=None, multiuser: bool = False,
                          wipe: bool = False, window: int = 1) -> None:
    """Ставит эту сборку вместо стоящей в окне и запускает её.

    Без ``wipe`` — обновлением на месте, с сохранением данных; если Android
    не позволит (другая подпись, версия ниже), в callback придёт
    InstallConflict с ``needs_wipe``. С ``wipe`` прежняя сборка снимается
    вместе с данными — на это нужно отдельное согласие человека.
    """

    def stage(text):
        if on_stage is not None:
            GLib.idle_add(lambda: (on_stage(text), False)[1])

    mode = "wipe" if wipe else "update"

    def work():
        if window > 1:
            open_in_window(entry, window, stage, multiuser, replace=mode)
        else:
            install_and_launch(entry, stage, multiuser, replace=mode)
        return True

    in_thread(work, lambda _result, error: callback(error))


def uninstall_async(entry: Entry, callback, shared: bool = False) -> None:
    """Убирает приложение из основного контейнера.

    ``shared`` — тем же пакетом пользуются другие записи библиотеки. Тогда
    сначала убеждаемся, что в окне стоит сборка именно этой записи: чужую
    установку не трогаем.
    """

    def work():
        _require_own_build(entry, shared)
        # Пакета в контейнере нет — так и говорим. Раньше удаление всё равно
        # шло по всей цепочке: adb отказывал, служба Waydroid отказывала, и
        # кончалось это просьбой «подтвердите удаление в окне Android» — для
        # приложения, которого там нет.
        if installed_build(entry.package)[0] == "":
            raise WaydroidError(tr("в основном окне это приложение не установлено"))
        # Сюда доходит только своя установка: её и снимаем. В профиле —
        # только из него: код пакета мог понадобиться человеку и в другом.
        if entry.profile:
            uninstall_for_user(entry.package, entry.profile)
        else:
            uninstall_apk(entry.package)
        return True

    in_thread(work, lambda _result, error: callback(error))


def _require_own_build(entry: Entry, shared: bool) -> None:
    """Убеждается, что в основном окне стоит именно сборка этой записи.

    Имя пакета у нескольких записей бывает одно, и команда «удалить пакет»
    сняла бы то, что стоит, — чьё бы оно ни было. Раньше «Удалить из
    контейнера» в карточке одной сборки молча сносило установку и данные
    другой. Если пакет в библиотеке только у этой записи, проверять нечего:
    что бы там ни стояло, убрать это — её право.
    """
    if not shared:
        return
    current, _version = installed_build(entry.package)
    if current is None:
        raise ContainerUnreachable(
            tr("не удалось узнать, какая сборка стоит в контейнере: Android не ответил")
        )
    if not current:
        raise WaydroidError(tr("в основном окне это приложение не установлено"))
    if not entry.file_hash or current != entry.file_hash:
        raise WaydroidError(
            tr("в основном окне стоит другая сборка этого пакета — её Merci не трогает")
        )


def forget_entry_async(entry: Entry, shared: bool, windows: list[int], callback) -> None:
    """Убирает следы записи перед её удалением: её окна и её установку.

    callback(помеха, остаток). «Помеха» — окна записи стереть не удалось:
    запись тогда удалять нельзя, иначе её контейнеры остались бы без
    хозяина — работающие и невидимые. «Остаток» — установка в основном
    окне не снялась: это не страшно, о нём достаточно сказать.

    Установку в основном окне трогаем, только когда там стоит сборка
    именно этой записи. Профиль Android не удаляем никогда: в нём могут
    жить другие записи и то, что человек ставил сам, — раньше удаление
    одной записи сносило весь профиль вместе с соседями. Окна записи — её
    собственные контейнеры — стираются целиком.
    """

    def work():
        if windows:
            try:
                remove_instances(windows)
            except Exception as failure:  # noqa: BLE001 — причина уйдёт человеку
                return str(failure) or tr("не удалось"), ""
        leftover = ""
        if session_running() and adb_available():
            try:
                current, _version = installed_build(entry.package)
                ours = bool(current) and (
                    current == entry.file_hash if entry.file_hash else not shared
                )
                if ours and entry.profile:
                    uninstall_for_user(entry.package, entry.profile)
                elif ours:
                    uninstall_apk(entry.package)
            except WaydroidError as failure:
                leftover = str(failure)
        return "", leftover

    def done(result, error):
        blocked, leftover = result if result is not None else (str(error or ""), "")
        callback(blocked, leftover)

    in_thread(work, done)


_SUDO = "sudo "  # вынесено, чтобы тесты могли пройти путь без прав

# Гасит процесс сессии Waydroid — и только его. Шаблон привязан к началу
# командной строки (сессия — это «python3 /usr/bin/waydroid session start»):
# те же слова встречаются в тексте самих шагов — они ведь потом сессию и
# поднимают, — и поиск по всей строке находил оболочку, которая шаг
# исполняет, и убивал её на середине работы.
_KILL_SESSION = (
    "pkill -9 -f '^[^ ]*python[^ ]* [^ ]*waydroid[^ ]* session start' "
    "2>/dev/null || true"
)
# Поднимает сессию отдельным процессом. Слова команды разнесены по
# переменной по той же причине — чтобы текст шага не походил на сессию.
_START_SESSION = 'S="session start"; (setsid waydroid $S >/dev/null 2>&1 &)'

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


def askpass_helper() -> str:
    """Путь к помощнику. Пустая строка, если спросить пароль окном нечем."""
    tools = host_known().tools
    if tools:
        found = hostinfo.can_ask_password_in_window(tools)
    else:
        found = _ok(["sh", "-c", " || ".join(
            f"command -v {name} >/dev/null" for name in hostinfo.ASKPASS_PROGRAMS
        )])
    if not found:
        return ""
    path = os.path.join(_data_root(), "askpass.sh")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(hostinfo.ASKPASS_SCRIPT)
        os.chmod(path, 0o755)
    except OSError:
        return ""
    return path


def host_terminal() -> list[str] | None:
    """Первый найденный терминал на хосте, в котором можно ввести пароль."""
    for name, argv in _TERMINALS:
        if _ok(["sh", "-c", f"command -v {name} >/dev/null"]):
            return argv
    # Остальные — те, что знает слой переносимости (терминалы XFCE, LXDE,
    # общий x-terminal-emulator в Debian).
    known = {name for name, _argv in _TERMINALS}
    for name, argv in hostinfo.TERMINALS:
        if name not in known and _ok(["sh", "-c", f"command -v {name} >/dev/null"]):
            return list(argv)
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
            lines = [_safe_url(line.strip()) for line in handle if line.strip()]
    except OSError:
        return "", ""
    return (lines[0] if lines else ""), (lines[1] if len(lines) > 1 else "")


# Адрес, который можно без опаски вставить в команду: без кавычек, пробелов,
# точек с запятой и прочего, что оболочка поняла бы по-своему.
_URL = re.compile(r"https?://[A-Za-z0-9._~:/?&=%+@-]+")


def _safe_url(text: str) -> str:
    """Адрес из файла настроек — или пустая строка, если это не адрес.

    Файлы ota.conf и mirror.conf лежат в данных Merci, куда пишет любая
    программа пользователя, а их содержимое попадает в команды шагов
    подготовки, в том числе выполняемых с правами root. Строка вида
    `https://x/"; что-нибудь; "` стала бы командой.
    """
    return text if _URL.fullmatch(text) and len(text) <= 300 else ""


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
    if _firewall_kind() == "firewalld":
        # У firewalld контейнер без сети, пока его интерфейс не в доверенной
        # зоне. Спрашиваем зону интерфейса: это разрешено без прав root.
        zone = _run(
            ["firewall-cmd", "--get-zone-of-interface=waydroid0"], timeout=10
        )
        if zone.returncode not in (0, 2):
            return False  # не ответил — не знаем, зря не пугаем
        return zone.stdout.strip() != "trusted"
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


def _firewall_kind() -> str:
    """Какой сетевой экран включён на хосте: ufw, firewalld, nftables или none."""
    system = host_known()
    return hostinfo.firewall(system) if system.tools else "none"


def _firewall_step() -> Step:
    if _firewall_kind() == "firewalld":
        return Step(
            key="firewall",
            title="Доступ в интернет для контейнера",
            hint=hostinfo.firewall_hint("firewalld"),
            argv=[
                "sh",
                "-c",
                (hostinfo.firewall_fix_script("firewalld") or "true")
                # Адрес контейнер получает только при старте сессии.
                + "; waydroid session stop || true",
            ],
            root=True,
            minutes=1,
        )
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
    # Через test, а не `ls … | head`: у конвейера код возврата — от head, то
    # есть всегда ноль, и шаг «Модуль ядра binder» не предлагался никогда.
    if _ok(["sh", "-c", "test -e /dev/binderfs || test -e /dev/binder "
            "|| grep -qw binder /proc/filesystems"]):
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
    # Шаг, который Merci выполнить не может или не вправе: на этой системе
    # она не знает нужной команды либо та меняет систему целиком (слой
    # образа с перезагрузкой). Мастер показывает подсказку и ждёт человека.
    manual: bool = False
    # Команда шага сама поднимает права внутри себя (зовёт помощника под
    # root). Если окна для пароля на системе нет, такой шаг надо исполнять в
    # терминале — иначе sudo без терминала просто откажет.
    asks_root: bool = False
    # Команда шага под конец сама поднимает сессию отдельным процессом. Шаг
    # считается выполненным, когда команда вышла без ошибки И контейнер
    # действительно запущен. Раньше такие шаги запускались «в фон» и
    # рапортовали об успехе, увидев ещё прежнюю, не перезапущенную сессию, —
    # до того, как человек успевал ввести пароль.
    then_session: bool = False
    # У шага с остановкой контейнера (then_session) есть начало, которое
    # можно прервать, — загрузка или сборка до всякой работы под root.
    # Отменить такой шаг можно, пока его сценарий не напечатал метку
    # NO_CANCEL_MARK; без этого признака шаг с then_session не отменяется с
    # самого начала.
    prelude: bool = False
    # Что Merci должна запомнить, когда шаг удался: для какой версии пакета
    # настроено ускорение, что мерцание исправлено. Зовётся в потоке, один
    # раз, перед тем как шаг объявят выполненным. Раньше это помнило окно —
    # и только у тех шагов, что запускались из меню: тот же шаг в мастере
    # подготовки или в обновлении проходил бесследно, и Merci потом
    # предлагала уже сделанное заново.
    after: object = None

    def command_line(self) -> str:
        """Команда так, как её и правда выполняют."""
        prefix = "sudo " if self.root else ""
        # shlex.join: у шагов-сценариев команда — `sh -c '<сценарий>'`, и
        # склеенная пробелами она исполняла бы первое слово сценария.
        return prefix + shlex.join(self.argv)


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


def refresh_url_listener() -> bool:
    """Обновляет службу приёма ссылок на хосте, если она старее встроенной.

    Служба ставится один раз, шагом установки, и дальше живёт сама по себе:
    исправления в ней — в том числе закрывающие дыры — до хоста не доходили
    бы никогда. Поэтому при старте сверяем файл на хосте со своим и, если
    они разошлись, заменяем и перезапускаем службу. Прав root для этого не
    нужно: и файл, и служба принадлежат пользователю. Если служба не
    установлена вовсе, ничего не делаем. Отдаёт True, если обновила.
    """
    source = os.path.join(_URLFORWARD_SRC, "merci-url-listener.py")
    if not os.path.exists(source):
        return False
    # Из песочницы на хост файл попадает через общий каталог данных.
    staged = os.path.join(_data_root(), "urlforward", "merci-url-listener.py")
    try:
        os.makedirs(os.path.dirname(staged), exist_ok=True)
        shutil.copyfile(source, staged)
    except OSError:
        return False
    script = (
        'dst="$HOME/.local/share/merci/urlforward/merci-url-listener.py"; '
        '[ -f "$dst" ] || exit 3; '
        'cmp -s "$1" "$dst" && exit 4; '
        'install -m 755 "$1" "$dst" || exit 5; '
        "if systemctl --user cat merci-url-listener.service >/dev/null 2>&1; then "
        "  systemctl --user restart merci-url-listener.service; "
        "else "
        f"  {_KILL_LISTENER}; "
        '  (setsid "$dst" >/dev/null 2>&1 &); '
        "fi"
    )
    return _run(["sh", "-c", script, "merci", staged], timeout=30).returncode == 0


def urlforward_step() -> Step:
    """Ссылки из Android открываются браузером хоста.

    В Waydroid такой возможности нет вовсе: работает только направление
    хост → контейнер (waydroid app intent), а обратного нет — открытая
    заявка waydroid/waydroid#210. Поэтому здесь собирается маленькое
    Android-приложение, которое ловит VIEW-интент и отдаёт адрес службе на
    хосте, а та вызывает xdg-open.
    """
    helper = askpass_helper()
    sudo = _sudo(helper)
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
        + _listener_service_snippet() +
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
        asks_root=True,
    )


def subsurface_flicker() -> bool:
    """Включён ли режим подповерхностей — источник мерцания картинки.

    Android-слои в этом режиме живут в wl_subsurface, а синхронная
    подповерхность показывается только когда коммитит родительская
    поверхность. Родитель молчит — кадр замирает, и картинка обновляется
    лишь на события: нажатие любой клавиши, вход курсора в окно, появление
    экранной клавиатуры. waydroid-nvidia включает этот режим по умолчанию.
    """
    return _setting("persist.waydroid.use_subsurface") == "true"


def subsurface_step() -> Step:
    """Выключает подповерхности и перезапускает контейнер.

    Пишет помощник — в оба файла свойств: waydroid_base.prop читает init
    при загрузке контейнера, а из waydroid.cfg Waydroid собирает его заново
    при обновлении образа. Раньше шаг правил оба файла сам, тремя командами
    sudo подряд (три запроса пароля), и при отказе на любом из них оставлял
    Waydroid выключенным.
    """
    return Step(
        key="subsurface",
        title="Исправить мерцание картинки",
        hint="выключает подповерхности: из-за них кадр обновляется только на ввод",
        argv=["sh", "-c", _update_script(_helper_command("subsurface", "false"))],
        minutes=1,
        asks_root=True,
        then_session=True,
        after=remember_flicker_fix,
    )


def remember_flicker_fix() -> None:
    """Запоминает, что человек включил исправление мерцания: настройка
    waydroid-nvidia включает подповерхности заново, и по этой памяти Merci
    возвращает их в «выключено» (см. _kept_settings)."""
    from .settings import Settings

    Settings().set("subsurface_off", True)


def flicker_fix_lost() -> bool:
    """Сброшено ли исправление мерцания, которое человек когда-то включил.

    Так бывает, когда ускорение настраивали мимо Merci (или прежней её
    версией): настройка waydroid-nvidia всегда включает подповерхности.
    Смотрим в настройки Waydroid, а не в работающий контейнер: ответ нужен
    и при выключенном. Блокирующая: звать из потока.
    """
    from .settings import Settings

    if not Settings().get("subsurface_off", False):
        return False
    properties = waydroid_cfg().get("properties", {})
    return properties.get("persist.waydroid.use_subsurface") == "true"


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
    running = session_running()
    if not stretch and not running:
        # Выключенный контейнер о своём размере не скажет (resolution()
        # отдаёт нули), а нули — не «размер не тот». Раньше шаг из-за этого
        # предлагался при каждом открытии мастера с выключенным Waydroid и
        # каждый раз перезапускал его впустую: `waydroid prop set` до
        # выключенного контейнера не доходит, так что и после шага всё
        # оставалось как было.
        return None
    if running and any(
        item["number"] != 1 and item["state"] == "RUNNING" for item in instances()
    ):
        # Шаг перезапускает основную сессию, а под работающими
        # дополнительными окнами это выключает оверлей — и транслятор ARM
        # пропадает во всех окнах разом. Размер при открытых окнах меняет
        # панель контейнера: она гасит их и поднимает заново.
        return None
    if resolution() == render and (not stretch or gamescope_running()):
        return None

    width, height = render
    monitor_width, monitor_height = monitor

    helper = askpass_helper()
    sudo = _sudo(helper)
    start = (
        _GAMESCOPE_SESSION.replace("__MW__", str(monitor_width))
        .replace("__MH__", str(monitor_height))
        .replace("__W__", str(width))
        .replace("__H__", str(height))
        if stretch
        else _session_snippet()
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
        asks_root=True,
        then_session=True,
    )


def plan(needs_bridge: bool, monitor: tuple[int, int] = (0, 0)) -> list[Step]:
    """Чего не хватает — в порядке выполнения. Готовое не попадает.

    Проверки идут через adb, и каждая ждёт своего таймаута. Пока Android
    внутри не отвечает, это складывается в минуты молчаливого ожидания —
    мастер выглядит зависшим. Поэтому сначала короткий вопрос: жива ли
    система вообще.
    """
    steps: list[Step] = []
    # Систему хоста опрашиваем заново: за время работы мастера на ней могли
    # появиться пакеты, которых не было минуту назад.
    system = host(refresh=True)
    have_adb = adb_available()
    # Жив ли Android, спрашиваем через adb — без него вопрос задать нечем, и
    # молчание значило бы не «контейнер завис», а «спросить не смогли».
    if have_adb and session_running() and not system_ready(container_ip(), timeout=10):
        return [container_restart_step()]

    if not kernel_has_binder():
        if system.family == "arch" and _ok(["sh", "-c", "command -v yay >/dev/null"]):
            steps.append(
                Step(
                    key="binder",
                    title="Модуль ядра binder",
                    hint="ядро без поддержки binder — нужен внешний модуль из AUR",
                    argv=["yay", "-S", "--needed", "binder_linux-dkms"],
                    minutes=5,
                )
            )
        else:
            # На других системах модуль из AUR не поставить, и общего рецепта
            # нет: где-то binder в ядре есть, где-то нужен другой пакет ядра.
            steps.append(
                Step(
                    key="binder",
                    title="Модуль ядра binder",
                    hint=hostinfo.binder_hint(system),
                    manual=True,
                )
            )

    installed = _ok(["sh", "-c", "command -v waydroid"])
    if not installed:
        steps.append(_package_step(system, "waydroid", "package", "Пакет waydroid"))
    if not have_adb:
        # Без adb Merci не может ни поставить приложение, ни открыть его:
        # весь запуск идёт через него. Раньше его ставил только шаг MultiUser.
        steps.append(_package_step(system, "adb", "adb", "adb — связь с Android"))
    if hostinfo.needs_nested_compositor(system) and not hostinfo.nested_compositor(
        system, system.tools
    ):
        # Сеанс X11: Waydroid рисует только в композитор Wayland, поэтому
        # ему нужен вложенный — окно, внутри которого живёт Android.
        steps.append(
            _package_step(system, "cage", "nested", "Композитор Wayland для сеанса X11")
        )

    ready, detail = status(use_cache=False) if installed else (False, "не установлен")
    if not ready and detail in ("образ Android не загружен", "не установлен"):
        # Имена — не system и vendor: system выше — описание хоста, и
        # затёртое строкой, оно роняло план на следующем же шаге.
        ota_system, ota_vendor = custom_channels()
        if not ota_system:
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
        if ota_system:
            init_argv += ["-c", ota_system]
        if ota_vendor:
            init_argv += ["-v", ota_vendor]
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

    if not ready and installed and nvidia_setup_needed():
        # После обновления драйвера NVIDIA или пакета waydroid-nvidia
        # контейнер не стартует, пока ускорение не настроят заново. Просто
        # «запустить сессию» тут бесполезно: шаг сам поднимет её в конце.
        steps.append(nvidia_setup_step())
    elif not ready:
        steps.append(
            Step(
                key="session",
                title="Сессия Waydroid",
                hint="запуск контейнера, прав root не требует",
                argv=_session_argv(system),
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
                argv=_session_argv(system),
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
            _bridge_helper_step(
                "Трансляция ARM64 → x86_64",
                "libhoudini из проверенного архива",
                DEFAULT_BRIDGE,
            )
        )
    elif needs_bridge and bridge_expired():
        # Транслятор стоит, но его срок вышел: ставим свежую сборку поверх.
        steps.append(_ndk_step("libhoudini"))
        steps.append(
            _bridge_helper_step(
                "Обновить транслятор ARM",
                "у прежней сборки libhoudini истёк срок: приложения зависают при запуске",
            )
        )

    if adb_open():
        steps.append(adb_lock_step())
    else:
        # Профили включены, но предел пользователей в настройках контейнера
        # потерялся (Waydroid пересобирает их при обновлении образа): новые
        # профили не создаются. Шаг возвращает предел на место. При открытом
        # adb это сделает шаг выше — у них общий перезапуск.
        from .settings import Settings

        if Settings().multiuser and ready and have_adb and not multiuser_ready()[0]:
            steps.append(multiuser_step())
    display = display_step(monitor) if monitor[0] else None
    if display is not None:
        steps.append(display)

    # Мерцание чинится последним: шаг перезапускает контейнер, и делать это
    # раньше остальных настроек незачем.
    if subsurface_flicker():
        steps.append(subsurface_step())

    return steps


# Архивы трансляторов ARM: адрес и сумма MD5 для каждой версии Android. Это
# те же сборки, что ставит waydroid_script (stuff/ndk.py, stuff/houdini.py);
# сам он в Merci больше не участвует. MD5 здесь — проверка целости загрузки:
# что именно попадёт в систему, решает помощник по своему списку сумм
# (BRIDGE_ARCHIVES в data/instances/merci-instance.py).
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
        # Сборки libhoudini несут в себе срок годности (см. bridge_expired).
        # Эта — из образа HPE UKW1.260829.001, действует до 1 июля 2028.
        "13": (
            "https://github.com/supremegamers/vendor_intel_proprietary_houdini"
            "/archive/2f8f088671182e17e67321e098e8411a3972a628.zip",
            "37fe0899f1e4da7f9a724cca1c1ab1ea",
        ),
    },
}

# libhoudini — закрытая сборка Intel, и в неё зашита дата, после которой она
# перестаёт работать. Никакой ошибки при этом нет: приложение стартует, а
# через пару секунд после загрузки его потоки уходят в бесконечный
# sched_yield внутри транслятора — десяток ядер занят на сто процентов, ни
# одного кадра не нарисовано, через двенадцать минут Android объявляет
# «приложение не отвечает». Снаружи это «нажимаю запустить, и ничего».
# Так в ноябре 2026 встала сборка из GooglePlayGames UKW1.251130.001,
# которую ставила Merci 0.1.x; waydroid_script заменил её 6 сентября
# («extended expiry date»).
#
# Сумма lib64/libhoudini.so → дата, до которой сборка работает
# (None — срок уже вышел). Неизвестную сборку считаем годной: гадать о
# чужих библиотеках незачем, а ложная тревога хуже молчания.
_HOUDINI_BUILDS: dict[str, str | None] = {
    "84050a496451463fd0848ee0a88d4086": None,  # GooglePlayGames UKW1.251130.001
    "d6722e86269af92d0ec5b1a8a3326970": "2028-07-01",  # HPE UKW1.260829.001
}
_HOUDINI_ON_HOST = "/var/lib/waydroid/overlay/system/lib64/libhoudini.so"


# Транслятор по умолчанию. libndk падает на коде, который приложение
# генерирует на лету (у Roblox так работает JIT Luau): проверено, стабильный
# SIGSEGV в ndk_translation_HandleNoExec. libhoudini тот же код переваривает.
DEFAULT_BRIDGE = "libhoudini"


def archive_for(target: str) -> tuple[str, str, str]:
    """(адрес, md5, путь в кеше) для транслятора."""
    entry = _ARCHIVES[target]
    url, md5 = entry[_android_version()]
    mirror = github_mirror()
    if mirror:
        url = mirror.rstrip("/") + "/" + url
    path = os.path.join(
        _script_cache(), "waydroid-script", "downloads", entry["file"]
    )
    return url, md5, path


# Каталог загрузок Merci. Подкаталог внутри называется waydroid-script — так
# его завели, когда архивы ещё ставил waydroid_script; имя оставлено, чтобы
# уже скачанное не пришлось качать заново.
def _script_cache() -> str:
    return os.path.join(_data_root(), "cache")


_NDK_SIZE_MB = 18.0  # размер архива libndk: нужен, чтобы показать проценты
_HOUDINI_SIZE_MB = 72.0


def _archive_megabytes() -> tuple[float, float]:
    """Сколько мегабайт архива транслятора уже скачано и сколько всего.

    Качается во временный файл рядом с целевым; смотрим на оба, какой есть.
    """
    best, total = 0.0, _NDK_SIZE_MB
    for target, size in (("libndk", _NDK_SIZE_MB), ("libhoudini", _HOUDINI_SIZE_MB)):
        try:
            local = archive_for(target)[2]
        except (KeyError, WaydroidError):
            continue
        for candidate in (local + ".part", local):
            with suppress(OSError):
                done = os.path.getsize(candidate) / 1_048_576
                if done > best:
                    best, total = done, size
    return best, total


def github_mirror() -> str:
    """Префикс зеркала GitHub из mirror.conf, если пользователь его задал.

    Например https://ghproxy.example/ — тогда адрес архива станет
    https://ghproxy.example/https://github.com/... Пусто — качаем напрямую.
    """
    try:
        with open(os.path.join(_data_root(), "mirror.conf"), encoding="utf-8") as handle:
            return _safe_url(handle.read().strip())
    except OSError:
        return ""


def _android_version() -> str:
    """11 или 13 — от этого зависит, какой архив транслятора нужен."""
    # Образ — раньше памяти: запомненная версия устаревает, когда образ
    # сменили при выключенном контейнере (`waydroid init -f` с другим
    # каналом), и на новый Android лёг бы транслятор от старого.
    sdk = _prop("ro.build.version.sdk") or _sdk_from_image() or _remembered_sdk()
    if sdk:
        _remember_sdk(sdk)
    return "11" if sdk == "30" else "13"


def _sdk_from_image() -> str:
    """Версия SDK прямо из образа Android — без сессии и без прав.

    Образ читается как файл (debugfs из e2fsprogs, ничего не подключая).
    Нужна, когда контейнер выключен, а Merci его версию ещё не видела:
    раньше в этом случае версию определял waydroid_script, уже под root.
    Не вышло — пустая строка, и берётся Android 13: его и ставит нынешний
    `waydroid init`.
    """
    cached = _image_sdk_cache.get()
    if cached is not None:
        return cached
    images = waydroid_cfg().get("waydroid", {}).get("images_path", "") or "/var/lib/waydroid/images"
    if not re.fullmatch(r"/[\w./ -]{1,200}", images):
        return ""
    result = _run(
        ["sh", "-c",
         "command -v debugfs >/dev/null || exit 0; "
         f"debugfs -c -R 'cat /system/build.prop' {_quote(images + '/system.img')} 2>/dev/null "
         "| grep -m1 '^ro.build.version.sdk=' || true"],
        timeout=30,
    )
    value = result.stdout.strip().rpartition("=")[2]
    value = value if value.isdigit() else ""
    # Версию спрашивают помногу раз за шаг (и каждые две секунды, пока идёт
    # загрузка архива), а образ за это время не меняется.
    _image_sdk_cache.set(value)
    return value


def _sdk_note() -> str:
    return os.path.join(_data_root(), "android-sdk")


def _remembered_sdk() -> str:
    """Версия SDK образа, увиденная при работавшей сессии.

    Спросить её можно только у работающего контейнера, а транслятор
    выбирается и при выключенном. Раньше «не ответил» молча значило
    «Android 13» — и на образе Android 11 поставился бы чужой транслятор.
    """
    try:
        with open(_sdk_note(), encoding="ascii") as handle:
            value = handle.read().strip()
    except OSError:
        return ""
    return value if value.isdigit() else ""


def _remember_sdk(sdk: str) -> None:
    if not sdk.isdigit() or sdk == _remembered_sdk():
        return
    with suppress(OSError):
        os.makedirs(_data_root(), exist_ok=True)
        with open(_sdk_note(), "w", encoding="ascii") as handle:
            handle.write(sdk)


def _bridge_fetch(target: str) -> str:
    """Сценарий: добыть архив транслятора с верной суммой.

    Три источника по порядку: уже скачанное нами, файл из каталога загрузок
    (браузер справляется там, где рвётся прямая загрузка), и только потом
    сеть — с повторами после обрывов.

    Написан функцией оболочки, чтобы годиться и отдельным шагом, и началом
    шага смены транслятора. Именно функцией, а не в скобках: подоболочка
    пережила бы отмену шага и продолжала качать — до тридцати попыток.
    """
    url, md5, local = archive_for(target)
    part = local + ".part"
    limit = 256 * 1024 * 1024
    return (
        "fetch_archive() { "
        f'mkdir -p "$(dirname {_quote(local)})"; '
        f'check() {{ [ -f "$1" ] && echo "{md5}  $1" | md5sum -c - >/dev/null 2>&1; }}; '
        f'if check {_quote(local)}; then echo "архив уже скачан"; return 0; fi; '
        'for d in "$HOME/Загрузки" "$HOME/Downloads" "$HOME/загрузки"; do '
        '  for f in "$d"/*.zip; do '
        # Суммы всех архивов из загрузок подряд не считаем — только похожих
        # по имени: так их называет GitHub, когда архив качают браузером.
        '    case "$f" in *ndk_translation*|*houdini*) ;; *) continue;; esac; '
        f'    if check "$f"; then echo "берём готовый файл: $f"; '
        f'      cp "$f" {_quote(local)}; return 0; fi; '
        "  done; "
        "done; "
        'command -v curl >/dev/null 2>&1 '
        '|| { echo "Качать нечем: на этой системе нет curl."; return 1; }; '
        'echo "качаем архив"; '
        # Под этим именем может лежать архив прошлой сборки транслятора:
        # сумма у него чужая, а докачивать к нему нечего. Сервер архивов
        # GitHub частями не отдаёт вовсе, поэтому каждая попытка — с начала,
        # во временный файл; на место переносим только проверенное.
        f'rm -f {_quote(local)} {_quote(part)}; TRY=0; '
        # Повторяем обрывы связи, но не отказ сервера: `--retry-all-errors`
        # тридцать раз подряд спрашивал бы адрес, по которому архива уже нет.
        # Связь, вставшую на минуту, считаем оборванной — иначе шаг ждал бы
        # её вечно; больше разумного не качаем.
        'while [ "$TRY" -lt 30 ]; do TRY=$((TRY + 1)); CODE=0; '
        f"  curl -fL --progress-bar --connect-timeout 20 --speed-limit 1024 --speed-time 60 "
        f'--max-filesize {limit} -o {_quote(part)} "{url}" || CODE=$?; '
        '  [ "$CODE" = 0 ] && break; '
        '  if [ "$CODE" = 22 ]; then '
        '    echo "Сервер ответил отказом: архива по этому адресу больше нет."; return 1; fi; '
        '  if [ "$CODE" = 63 ]; then '
        '    echo "По этому адресу лежит другой файл — больше, чем архив транслятора."; '
        "    return 1; fi; "
        '  echo "связь оборвалась (curl: $CODE), попытка $TRY из 30"; sleep 3; '
        "done; "
        f'check {_quote(part)} || {{ rm -f {_quote(part)}; '
        'echo "Архив транслятора не скачался целиком: связь с GitHub рвётся."; '
        'echo "Обычно помогает другая локация VPN. Архив можно скачать и браузером — '
        'положите его в «Загрузки», Merci возьмёт его оттуда."; return 1; }; '
        f'mv -f {_quote(part)} {_quote(local)}; '
        "}; fetch_archive; "
    )


def _ndk_step(target: str = DEFAULT_BRIDGE) -> Step:
    _url, _md5, local = archive_for(target)
    return Step(
        key="ndk",
        title=tr("Архив транслятора ({target})", target=target),
        hint="берём из загрузок или качаем с докачкой",
        argv=["sh", "-c", "set -e; " + _bridge_fetch(target)],
        downloads=os.path.basename(local),
        minutes=3,
    )


def _bridge_helper_step(title: str, hint: str, target: str = "libhoudini",
                        fetch: bool = False) -> Step:
    """Установка, обновление или смена транслятора ARM помощником Merci.

    Раньше этим занимался waydroid_script: Merci клонировала его с GitHub и
    запускала от root — то есть исполняла с полными правами код, который
    на тот момент лежал в чужой ветке, причём из каталога, куда пишет любая
    программа пользователя. Помощник делает то же сам и кладёт в оверлей
    только файлы транслятора из архива с известной суммой (список сумм — на
    стороне root, в самом помощнике).

    ``fetch`` — добыть архив в этом же шаге (для разовой смены транслятора
    из меню): до запроса пароля и до остановки Waydroid. В мастере
    подготовки архив качает отдельный шаг перед этим.
    """
    _url, _md5, archive = archive_for(target)
    script = _update_script(
        'echo "ставим транслятор из проверенного архива"; '
        + _helper_command("bridge-install", archive),
        before=_bridge_fetch(target) if fetch else "",
    )
    return Step(
        key="bridge",
        title=title,
        hint=hint,
        argv=["sh", "-c", script],
        downloads=os.path.basename(archive) if fetch else "",
        minutes=4 if fetch else 2,
        asks_root=True,
        then_session=True,
        # Пока качается архив, шаг можно отменить; дальше — нет.
        prelude=fetch,
    )


def _package_step(system: hostinfo.Host, what: str, key: str, title: str) -> Step:
    """Шаг установки пакета — той командой, что годится для этой системы.

    Где Merci команду знает, шаг исполняется сам. Где не знает или не вправе
    (неизменяемые системы: пакет там ставится слоем образа с перезагрузкой) —
    шаг показывает, что сделать, и ждёт человека.
    """
    script = hostinfo.install_script(system, what)
    if script is None:
        return Step(key=key, title=title, hint=hostinfo.install_hint(system, what), manual=True)
    names = hostinfo.packages(system, what) or [what]
    return Step(
        key=key,
        title=title,
        hint=tr("ставится из репозитория: {packages}", packages=" ".join(names)),
        argv=["sh", "-c", script],
        root=True,
        minutes=1,
    )


def _session_argv(system: hostinfo.Host) -> list[str]:
    """Команда, поднимающая сессию Waydroid, — в сеансе X11 внутри
    вложенного композитора."""
    nested = hostinfo.nested_compositor(system, system.tools) or []
    return [*nested, "waydroid", "session", "start"]


def _session_snippet() -> str:
    """Фрагмент оболочки для шагов: поднять сессию отдельным процессом."""
    system = host_known()
    return hostinfo.session_start_script(system, system.tools) or _START_SESSION


def _container_service(action: str = "restart") -> str:
    """Команда службы контейнера Waydroid для этой системы (без приставки
    root): systemd, OpenRC или runit. Пока хост не опрошен — как в systemd."""
    return (
        hostinfo.container_service_command(host_known(), action)
        or f"systemctl {action} waydroid-container.service"
    )


# Останавливает службу приёма ссылок, запущенную без systemd. Шаблон привязан
# к началу командной строки по той же причине, что и _KILL_SESSION: имя файла
# встречается и в тексте самого шага.
_KILL_LISTENER = (
    "pkill -f '^[^ ]*python[^ ]* [^ ]*merci-url-listener[.]py' 2>/dev/null || true"
)


def _listener_service_snippet() -> str:
    """Фрагмент оболочки: поставить службу приёма ссылок на автозапуск и
    поднять её. Ждёт путь к каталогу с файлами службы в переменной ``W``.

    Со systemd — пользовательская служба. Без него (OpenRC, runit) службы
    пользователя нет вовсе, поэтому берём то, что есть на любом рабочем
    столе: автозапуск XDG — и поднимаем службу сразу, не дожидаясь входа.
    """
    install = (
        'install -Dm755 "$W/merci-url-listener.py" '
        '"$HOME/.local/share/merci/urlforward/merci-url-listener.py"; '
    )
    if hostinfo.supports_user_services(host_known()) or not host_known().tools:
        return (
            install
            + 'install -Dm644 "$W/merci-url-listener.service" '
            '"$HOME/.config/systemd/user/merci-url-listener.service"; '
            "systemctl --user daemon-reload; "
            "systemctl --user enable --now merci-url-listener.service; "
        )
    return (
        install
        + 'L="$HOME/.local/share/merci/urlforward/merci-url-listener.py"; '
        'mkdir -p "$HOME/.config/autostart"; '
        "printf '[Desktop Entry]\\nType=Application\\nName=Merci URL listener\\n"
        "Exec=%s\\nNoDisplay=true\\n' \"$L\" "
        '> "$HOME/.config/autostart/merci-url-listener.desktop"; '
        f"{_KILL_LISTENER}; "
        '(setsid "$L" >/dev/null 2>&1 &); '
    )


def adb_open() -> bool:
    """Открыт ли adbd контейнера без проверки ключа (ro.adb.secure=0).

    Так его настраивали прежние версии Merci. См. adb_lock_step.
    """
    text = _host_file("/var/lib/waydroid/waydroid_base.prop")
    return any(
        line.replace(" ", "") == "ro.adb.secure=0" for line in text.splitlines()
    )


def _secure_adb_args() -> list[str]:
    """Аргументы помощника: закрыть adb, а при включённых профилях заодно
    вернуть предел пользователей Android — перезапуск у них общий."""
    from .settings import Settings

    return ["secure-adb", "--users", "4"] if Settings().multiuser else ["secure-adb"]


def adb_lock_step() -> Step:
    """Возвращает adbd контейнера проверку ключа.

    Без неё к порту 5555 подключается любое приложение внутри Android и
    любая машина в локальной сети — и получает оболочку без пароля. Ключ
    хоста при этом кладётся в доверенные, так что сама Merci продолжает
    подключаться молча. Подробности — в помощнике, cmd_secure_adb.
    """
    script = _update_script(
        'echo "возвращаем adb проверку ключа"; ' + _helper_command(*_secure_adb_args()),
        # Ключ adb появляется при первом запуске сервера. Без него закрывать
        # нельзя: Merci сама осталась бы за дверью — помощник это проверит.
        before="adb start-server >/dev/null 2>&1 || true; ",
    )
    return Step(
        key="adb-lock",
        title="Закрыть adb контейнера ключом",
        hint="сейчас к нему без пароля подключится любое приложение Android "
        "и сосед по сети",
        argv=["sh", "-c", script],
        minutes=2,
        asks_root=True,
        then_session=True,
    )


def _quote(argument: str) -> str:
    """Простое экранирование для передачи команды в sh -c."""
    return "'" + argument.replace("'", "'\\''") + "'"


# С этого места сценарий шага не слушает просьб завершиться и говорит об этом
# мастеру особой строкой — тот прячет «Отмену». Работу под root она всё
# равно не остановила бы: сигнал доходит только до оболочки шага, а sudo и
# помощник дорабатывают. Окно же писало бы «отменено», а «Повторить»
# запускало бы вторую копию поверх первой.
NO_CANCEL_MARK = "MERCI_NO_CANCEL"
# HUP и PIPE — туда же: закрытое окно терминала или закрытая Merci не должны
# обрывать работу под root на середине. До метки сценарий слушает всё, как
# обычная программа: там ещё ничего не тронуто, и отмена должна отменять.
#
# Ловушка выхода — для запуска в терминале (StepRunner._start_in_terminal):
# закрой человек окно терминала, вывод сценария оборвётся, и о конце работы
# Merci узнает только из файла, названного в MERCI_CODE_FILE.
_CODE_TO_FILE = '[ -n "${MERCI_CODE_FILE:-}" ] && echo "$CODE" > "$MERCI_CODE_FILE"; '
_NO_CANCEL = (
    "trap '' TERM INT HUP PIPE; "
    "trap 'CODE=$?; " + _CODE_TO_FILE + "exit $CODE' EXIT; "
    f"echo {NO_CANCEL_MARK}; "
)


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

    def __init__(self, step: Step, on_line, on_done, on_progress=None, on_locked=None) -> None:
        self.step = step
        # Шаг уже нельзя отменить: см. NO_CANCEL_MARK и Step.prelude.
        self.locked = step.then_session and not step.prelude
        self._on_locked = on_locked or (lambda: None)
        # Сценарий сам сказал, что дошёл до работы под root (а не «заперт
        # по признаку шага»): такой доведёт её до конца и без нашего окна.
        self._marked = False
        self._code_path = ""
        self._code_waited = 0
        self._on_line = on_line
        self._report = on_done
        self._on_done = self._finish_step
        self._waiting_session = False
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

        if self.step.asks_root and root_needs_terminal():
            # Шаг сам зовёт sudo, а окна для пароля нет: sudo без терминала
            # отвечает «a terminal is required» — уводим шаг в терминал.
            self._start_in_terminal()
            return

        if not self.step.root:
            self._start_directly()
            return

        helper = askpass_helper()
        if helper:
            self._start_directly(askpass=helper)
        else:
            self._start_in_terminal()

    def _finish_step(self, ok: bool) -> None:
        """Конец команды шага — ещё не обязательно конец шага."""
        if ok and self.step.then_session and not self._waiting_session:
            self._waiting_session = True
            self._done = False
            self._waited = 0
            self._on_progress(None, tr("Поднимаем контейнер Android"))
            GLib.timeout_add_seconds(3, self._await_ready)
            return
        after = self.step.after
        if ok and callable(after):
            # Сбой памяти — не сбой шага: работа сделана, и сказать об этом
            # надо в любом случае.
            in_thread(after, lambda _result, _error: self._report(True))
            return
        self._report(ok)

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
        # Сюда сценарий шага сам запишет код возврата (см. _NO_CANCEL): по
        # нему узнаём о конце работы, если окно терминала закрыли раньше.
        self._code_path = os.path.join(_log_dir(), f"step-{self.step.key}.code")
        with suppress(OSError):
            os.unlink(self._code_path)

        self._terminal_name = terminal[0]
        self._on_line(
            f"открыто окно {terminal[0]}: если спросит пароль, введите его там"
        )
        self._on_progress(None, tr("Подтвердите пароль в окне {app}", app=terminal[0]))
        command = " ".join(_quote(a) for a in self.step.argv)
        log = _quote(self._tail_path)
        # Шаг, который поднимает права сам, запускаем как есть: sudo внутри
        # него спросит пароль прямо в этом терминале.
        prefix = _SUDO if self.step.root else ""
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
            f"export MERCI_CODE_FILE={_quote(self._code_path)}; "
            f'{{ timeout --foreground 3600 {prefix}{_UNBUFFERED}{command}; echo "MERCI_EXIT:$?"; }} 2>&1 | tee -a {log}; '
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

        if text == NO_CANCEL_MARK:
            self._marked = True
            if not self.locked:
                self.locked = True
                self._on_locked()
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
        elif self.step.key in ("ndk", "switch-bridge"):
            # Полоса curl в конвейере процентов не печатает, поэтому смотрим
            # на растущий файл: размер архива известен заранее.
            GLib.timeout_add_seconds(2, self._watch_archive)

    def _watch_archive(self) -> bool:
        if self._done:
            return False
        in_thread(_archive_megabytes, self._show_archive)
        return True

    def _show_archive(self, answer, _error) -> None:
        if self._done or not answer or not answer[0]:
            return
        megabytes, total = answer
        self._on_progress(
            min(megabytes / total, 1.0),
            tr("{done} из {total} МБ", done=f"{megabytes:.1f}", total=f"{total:.0f}"),
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
        if self._done or self._waiting_session:
            # Команда кончилась, идёт подъём контейнера: «вывода пока нет»
            # тут говорить не о чем.
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
        if self.locked:
            return  # работа под root идёт до конца: рвать можно только оболочку
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
            if self._marked and self._code_path:
                # Работу под root закрытое окно не остановило — сценарий на
                # это время сигналов не слушает. Объяви мы сейчас «не
                # удалось», мастер закрылся бы, окно сняло бы свой замок, а
                # помощник ещё переписывал бы образ или оверлей.
                self._on_line(
                    "окно терминала закрыто, но работа под root идёт до конца — ждём её"
                )
                self._code_waited = 0
                GLib.timeout_add_seconds(2, self._await_code)
                return False
            self._on_line("окно терминала закрыто, команда не завершилась")
            self._done = True
            self._on_done(False)
        return False

    def _await_code(self) -> bool:
        """Ждёт, пока сценарий шага сам запишет код возврата в файл."""
        if self._done:
            return False
        self._code_waited += 2
        try:
            with open(self._code_path, encoding="ascii") as handle:
                text = handle.read().strip()
        except (OSError, ValueError):
            text = ""
        if text.isdigit():
            self._exit_code = int(text)
            self._on_line(f"код возврата: {self._exit_code}")
            self._done = True
            self._on_done(self._exit_code == 0)
            return False
        if self._code_waited > 3600:
            self._on_line("работа под root так и не сообщила о своём конце")
            self._done = True
            self._on_done(False)
            return False
        return True

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
        if not self._done and not self._waiting_session:
            self._eof = True
            self._maybe_finish()
        return False

    def _maybe_finish(self) -> None:
        """Шаг закончен, когда процесс вышел и весь вывод прочитан.

        Порядок важен: раньше о завершении сообщалось сразу по выходу
        процесса, и хвост вывода — то есть текст ошибки — терялся.

        Команда кончается один раз. Шаг с подъёмом сессии после неё ещё
        ждёт контейнер и на это время снова считается идущим (_done снят) —
        а страховочный таймер _force_finish принимал это за «команда так и
        не кончилась», проводил её конец второй раз и объявлял шаг удавшимся
        через три секунды, поднялся контейнер или нет. Ожидание контейнера
        не выполнялось никогда.
        """
        if self._done or self._waiting_session or not (self._exited and self._eof):
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


class AppNotDrawing(WaydroidError):
    """Контейнер здоров, но само приложение после запуска не рисует."""


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

    Выход один: убрать ту установку и поставить эту — либо дать этой
    сборке своё окно, то есть свой контейнер.
    """

    def __init__(
        self,
        message: str = "",
        build: str = "",
        version: str = "",
        window: int = 1,
        needs_wipe: bool = False,
        users: tuple[tuple[int, str], ...] = (),
    ) -> None:
        super().__init__(message)
        # Что именно стоит в контейнере: начало sha256 файла и версия — по
        # ним интерфейс называет мешающую сборку по имени.
        self.build = build
        self.version = version
        # В каком окне (контейнере) дело.
        self.window = window
        # Android отказался обновить сборку на месте — другая подпись или
        # версия ниже установленной: остаётся только снять пакет, а это
        # стирает его данные.
        self.needs_wipe = needs_wipe
        # Профили Android, в которых пакет установлен, — чьи данные сотрутся.
        self.users = users


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
            _installs_changed()
            if "Success" in (result.stdout + result.stderr):
                return
        except WaydroidError:
            pass  # adb есть, но не подключился — идём дальше

    result = _run(["waydroid", "app", "remove", package], timeout=180)
    _installs_changed()
    text = (result.stdout + result.stderr).strip()
    if result.returncode == 0 and "ailed" not in text:
        return

    intent = _run(
        ["waydroid", "app", "intent", "android.intent.action.DELETE", f"package:{package}"],
        timeout=120,
    )
    if intent.returncode != 0:
        raise WaydroidError(text or tr("удалить не удалось"))
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
    # Молчание команды — не «остановлена». Раньше таймаут здесь читался как
    # «сессии нет», и Merci запускала сессию поверх живой или считала
    # лёгшей ту, что просто долго отвечала. Переспрашиваем.
    for _ in range(3):
        state = session_state()
        if state is not None:
            return state
        time.sleep(2)
    return False


def session_state() -> bool | None:
    """Запущена ли сессия: да, нет или None — узнать не удалось."""
    result = _status_text(fresh=True)
    for line in result.stdout.splitlines():
        if line.strip().startswith("Session:"):
            return "RUNNING" in line
    return None if result.returncode in (TIMED_OUT, 127) else False


def _session_log() -> str:
    return os.path.join(_log_dir(), "session.log")


_VENUS_SOCKET = "/run/waydroid-venus/venus.sock"


def ensure_venus() -> None:
    """Поднимает сервер Venus, если контейнер рисует через него.

    waydroid-nvidia отдаёт рисование гостя серверу на хосте и при старте
    сессии требует его сокет. Сервер — пользовательская служба, и
    перезапускается она только после падения с ошибкой: вышел с нулём —
    остался лежать. Сессия тогда завершается сразу, а человек через две
    минуты видит «контейнер не поднялся» без единого намёка на причину.
    """
    cfg = waydroid_cfg()
    if nvidia_mode(cfg) != "venus":
        return
    # До версии 0.2 путь к сокету лежал в настройках; новые версии этот ключ
    # убрали (режим называется явно), а сокет у сервера всегда один.
    socket = cfg.get("waydroid", {}).get("nvidia_venus_socket", "") or _VENUS_SOCKET
    if not re.fullmatch(r"/[A-Za-z0-9._/-]{1,200}", socket):
        return
    if _ok(["sh", "-c", f"test -S {_quote(socket)} "
            "&& systemctl --user is-active --quiet wd-venus.service"]):
        return
    _run(["systemctl", "--user", "restart", "wd-venus.service"], timeout=30)
    for _ in range(10):
        if _ok(["test", "-S", socket]):
            return
        time.sleep(1)


def _start_session() -> None:
    """Запускает сессию Waydroid отдельным процессом.

    Что она сказала при старте, ложится в журнал: раньше вывод уходил в
    /dev/null, и от несостоявшегося запуска не оставалось ничего.
    """
    ensure_venus()
    log = _session_log()
    with suppress(OSError):
        os.makedirs(os.path.dirname(log), exist_ok=True)
    system = host_known()
    nested: list[str] = []
    if hostinfo.needs_nested_compositor(system):
        # Сеанс X11: сессия живёт внутри окна-композитора Wayland.
        nested = hostinfo.nested_compositor(system, system.tools) or []
        if not nested:
            raise WaydroidError(
                tr("в сеансе X11 для Waydroid нужен композитор Wayland — "
                   "откройте «Подготовить Waydroid»: она поставит cage")
            )
    prefix = "".join(_quote(part) + " " for part in nested)
    _run(
        ["sh", "-c", f"setsid {prefix}waydroid session start >{_quote(log)} 2>&1 &"],
        timeout=30,
    )


def _session_failure() -> str:
    """Последняя строка журнала запуска сессии — в хвост сообщения об ошибке."""
    try:
        with open(_session_log(), encoding="utf-8", errors="replace") as handle:
            lines = [line.strip() for line in handle if line.strip()]
    except OSError:
        return ""
    return f": {lines[-1][:300]}" if lines else ""


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
        _start_session()
        for _ in range(60):
            time.sleep(2)
            if session_running():
                forget_state()
                break
        else:
            raise WaydroidError(tr("контейнер не поднялся") + _session_failure())

    # Android внутри поднимается ещё некоторое время после сессии, и adb до
    # этого момента отвечает отказом.
    if wait_for_ip(45):
        return
    restart_session(stage)


def ensure_container_alive(stage=None) -> None:
    """Приводит контейнер в состояние, в котором можно ставить и запускать.

    Контейнер бывает наполовину жив: system_server убит сторожем, или
    SurfaceFlinger заклинило после закрытия окна — и тогда каждая следующая
    команда молча выжидает свой таймаут adb.

    Но «не отвечает» бывает и здоровым: первую минуту после старта сессии
    Android просто грузится. Раньше нажатие «Запустить» в эту минуту
    перезапускало контейнер, который как раз поднимался, — и так по кругу.
    Поэтому сначала даём ему загрузиться и только потом считаем мёртвым.
    """
    if not session_running():
        return  # выключенный контейнер поднимет ensure_session
    ip = container_ip()
    if not ip and wait_for_ip(45):
        ip = container_ip()
    if ip and wait_system_ready(ip, 75) and screen_state(ip=ip) != "затор":
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
    for line in _status_text().stdout.splitlines():
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
            if adb_reachable(address):
                _run(["adb", "connect", f"{address}:{_ADB_PORT}"], timeout=20)
            state = _run(["adb", "-s", f"{address}:{_ADB_PORT}", "get-state"], timeout=20)
            if state.stdout.strip() == "device":
                return address
        if time.time() >= deadline:
            return fallback
        time.sleep(2)


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
    _run(["sh", "-c", _KILL_SESSION], timeout=15)

    # Убеждаемся, что сессия правда легла: `session stop` регулярно упирается
    # в таймаут D-Bus и молча оставляет всё как было. Запустить поверх живой
    # сессии — значит не перезапустить ничего, а отрапортовать об успехе.
    for _ in range(15):
        forget_state()
        if session_state() is False:  # именно «нет», а не «не знаем»
            break
        time.sleep(2)
    else:
        raise WaydroidError(tr("сессия не остановилась"))

    time.sleep(3)
    _start_session()
    forget_state()
    for _ in range(60):
        time.sleep(2)
        if session_running():
            break
    else:
        raise WaydroidError(tr("контейнер не поднялся") + _session_failure())
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
        _start_session()
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
            _run(["adb", "disconnect", target], timeout=10)
            time.sleep(3)
        if not adb_reachable(ip):
            # adbd ещё не слушает (или контейнер лёг): `adb connect` в
            # пустоту висит минуту и держит сервер adb занятым.
            text = tr("контейнер не отвечает по сети")
            continue
        _run(["adb", "connect", target], timeout=20)
        state = _run(["adb", "-s", target, "get-state"], timeout=30)
        text = (state.stdout + state.stderr).strip()
        last = text.splitlines()[-1].strip() if text else ""
        if state.returncode == 0 and last == "device":
            return
        if "unauthorized" in text.lower() and attempt == 0:
            # Ключ положили — следующая попытка покажет, принял ли его
            # контейнер. Раньше ошибка поднималась тут же, по ответу,
            # снятому ещё до выдачи ключа, и выдача не помогала никогда.
            with suppress(WaydroidError):
                _instance_call("adb-key", "1", timeout=180)
            time.sleep(3)
            continue
        if "unauthorized" in text.lower():
            raise WaydroidError(
                tr("контейнер просит разрешить отладку: откройте окно Android и "
                "нажмите «Разрешить» в появившемся вопросе")
            )
    raise WaydroidError(text or "adb не подключился к контейнеру")


def android_users() -> dict[int, str]:
    """Пользователи Android внутри контейнера: {номер: имя}."""
    adb_connect()
    # Основной пользователь есть всегда. Пустой список — это не «профилей
    # нет», а «контейнер не ответил»: system_server ещё поднимается или
    # команда упёрлась в таймаут. Раньше такой ответ принимался за чистую
    # монету, и Merci заводила приложению новый профиль — человек видел
    # игру «с нуля», а старые данные оставались в прежнем профиле.
    for attempt in range(4):
        if attempt:
            time.sleep(4)
        result = _adb(["shell", "pm", "list", "users"], timeout=60)
        users: dict[int, str] = {}
        for match in re.finditer(r"UserInfo\{(\d+):([^:]*):", result.stdout):
            users[int(match.group(1))] = match.group(2)
        if users:
            return users
    raise ContainerUnreachable(tr("Android не ответил на вопрос о профилях"))


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


def multi_windows_on() -> bool:
    """Показывает ли Waydroid каждое приложение отдельным окном.

    От этого зависит, увидит ли человек копию и оригинал одновременно:
    в однооконном режиме контейнер рисует что-то одно.

    Свойство живёт в складе самого Android, и у выключенного контейнера его
    не спросить: тогда отдаём то, что видели в последний раз. Раньше
    выключенный контейнер читался как «выключено» — и переключатель в
    настройках показывал неправду.
    """
    from .settings import Settings

    value = _prop("persist.waydroid.multi_windows")
    if value:
        Settings().set("multi_windows", value == "true")
        return value == "true"
    return bool(Settings().get("multi_windows", False))


def set_multi_windows(enabled: bool) -> None:
    """Переключает многооконный режим и перезапускает сессию.

    Свойство persist.* читается при старте сессии, поэтому без перезапуска
    переключатель бы ничего не изменил, а выглядел бы рабочим.
    """
    from .settings import Settings

    wanted = "true" if enabled else "false"
    # У выключенного контейнера `waydroid prop set` пишет в журнал «session
    # is stopped» и выходит с нулём: переключатель щёлкал, окно сообщало об
    # успехе, а не менялось ничего. Поэтому сначала поднимаем контейнер.
    ensure_session()
    _run(["waydroid", "prop", "set", "persist.waydroid.multi_windows", wanted], timeout=90)
    if _prop("persist.waydroid.multi_windows") != wanted:
        raise WaydroidError(tr("контейнер не принял настройку — попробуйте ещё раз"))
    Settings().set("multi_windows", enabled)
    restart_session()


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


def _helper_digest() -> str:
    """SHA-256 помощника — с того экземпляра, которому можно верить.

    Внутри флатпака это файл из /app: он только для чтения, и подменить
    его программа пользователя не может. При запуске из исходников
    (разработка) экземпляр один — тот, что лежит в данных.
    """
    source = os.path.join(_INSTANCES_SRC, "merci-instance.py")
    if not os.path.exists(source):
        source = os.path.join(_data_root(), "instances", "merci-instance.py")
    try:
        with open(source, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError as exc:
        raise WaydroidError(
            tr("помощник для контейнеров не найден")
        ) from exc


class _Raw(str):
    """Кусок сценария, который идёт в команду помощника как есть, без
    кавычек: например ``"$1"`` — значение, известное только при исполнении.
    Только наши собственные постоянные строки, ничего пришедшего извне."""


def _helper_command(*argv: str, tail: str = "") -> str:
    """Команда оболочки: помощник с правами root и такими аргументами.

    Помощник лежит в данных Merci — иначе хост его не увидит, — а значит в
    каталоге, куда пишет любая программа пользователя. Запускай sudo файл
    прямо оттуда, подменивший его получал бы root в ту секунду, когда
    человек вводит пароль для Merci.

    Поэтому root сначала забирает файл к себе — в каталог, куда кроме него
    никто не пишет, — сверяет sha256 с суммой, которую Merci сняла со
    своего экземпляра, и исполняет уже свою копию. Подмена после проверки
    ничего не даёт: исполняется не тот файл, до которого можно дотянуться.

    Пароль спрашивает системное окно, Merci его не видит.

    ``tail`` — позиционные аргументы для сценария root (``$1``, ``$2``…):
    так в него попадает значение, вычисленное уже при исполнении шага, —
    и попадает в командную строку sudo, где его после запуска не подменить.
    """
    command = f"{_sudo(askpass_helper())}sh -c {_quote(_helper_inner(*argv))}"
    return f"{command} merci {tail}" if tail else command


def _helper_inner(*argv: str) -> str:
    """То же без подъёма прав: сценарий, который должен исполнить root.

    Нужен, когда под одним запросом пароля делается ещё что-то — например,
    сначала менеджер пакетов, потом помощник: два запроса подряд на одно
    действие человек не поймёт, а пропустив второй, останется с
    обновлённым пакетом и несобранными настройками.
    """
    helper = _instance_helper()
    digest = _helper_digest()
    home = os.path.expanduser("~")
    inner = (
        # Маску прав не трогаем: её унаследовал бы помощник, а за ним и
        # контейнер. Каталог mktemp и так закрыт для всех, кроме root.
        "set -e; "
        "d=$(mktemp -d /run/merci-helper.XXXXXX); "
        "trap 'rm -rf \"$d\"' EXIT; "
        f"cat {_quote(helper)} > \"$d/helper.py\"; "
        f"echo \"{digest}  $d/helper.py\" | sha256sum -c - >/dev/null 2>&1 || "
        "{ echo 'помощник Merci изменён посторонней программой — запуск отменён' >&2; "
        "exit 97; }; "
        # -I: без PYTHON*-переменных, пользовательских site-packages и без
        # каталога скрипта в sys.path — иначе под root подхватился бы модуль,
        # подложенный рядом с помощником.
        f"python3 -I \"$d/helper.py\" --home {_quote(home)} "
        + " ".join(part if isinstance(part, _Raw) else _quote(part) for part in argv)
    )
    return inner


def _instance_call(*argv: str, timeout: int = 240):
    """Зовёт помощника с правами root и разбирает его ответ.

    Помощника к посреднику не привязываем (см. hostexec.host_argv): он
    пересобирает конфиги и удаляет каталоги, и оборванный на середине
    оставил бы окно наполовину собранным. Пусть лучше доработает до конца,
    даже если мы перестали ждать.
    """
    result = _run_detached(["sh", "-c", _helper_command(*argv)], timeout=timeout)
    text = (result.stdout or "").strip()
    if result.returncode == TIMED_OUT:
        raise WaydroidError(
            tr("операция под root не уложилась в {seconds} с — возможно, окно "
               "пароля осталось без ответа", seconds=timeout)
        )
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
    # Через временный файл: реестр — единственное место, где записано,
    # какие окна есть и чьи они. Оборвись запись на середине, Merci
    # прочла бы пустоту и «забыла» все окна вместе с их данными.
    target = _registry_path()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target + ".new", "w", encoding="utf-8") as handle:
            json.dump({str(n): owner for n, owner in sorted(mapping.items())}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(target + ".new", target)
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


def release_lost_windows(known: set[str]) -> list[int]:
    """Освобождает окна, чьей записи в библиотеке больше нет.

    Такое окно не показала бы ни одна карточка: контейнер работал бы,
    занимал память и место, а выключить или стереть его было бы нечем.
    Освобождённое окно достаётся первой открытой карточке (см.
    adopt_orphan_instances) — там его видно и там же его можно удалить.
    """
    реестр = _registry()
    потерянные = sorted(n for n, чьё in реестр.items() if чьё and чьё not in known)
    if not потерянные:
        return []
    for number in потерянные:
        реестр[number] = ""
    _registry_set(реестр)
    return потерянные


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


def _container_pids() -> dict[str, int]:
    """Имя контейнера → номер процесса его lxc-start, одним вызовом ps.

    Номер процесса меняется при каждом старте контейнера: по нему видно,
    что под тем же адресом уже другая загрузка Android — а значит, всё, что
    Merci держит в нём до перезапуска, надо послать заново.
    """
    pids: dict[str, int] = {}
    result = _run(["ps", "-eo", "pid=,args="], timeout=20)
    for line in result.stdout.splitlines():
        words = line.split()
        if len(words) < 2 or not words[0].isdigit() or "lxc-start" not in words[1]:
            continue
        if "-n" in words:
            for name in words[words.index("-n") + 1 :][:1]:
                pids[name] = int(words[0])
    return pids


def _running_containers() -> set[str]:
    """Имена контейнеров, у которых жив lxc-start."""
    return set(_container_pids())


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

    # Соседей на мосту и процессы контейнеров снимаем один раз на всех:
    # список окон спрашивают каждые несколько секунд, и по два обращения к
    # хосту на каждое окно — это заметная доля всей работы Merci.
    neighbours = _bridge_neighbours() if свои else {}
    alive = _running_containers() if свои else set()

    items = [
        {
            "number": 1,
            "state": "RUNNING" if status()[0] else "STOPPED",
            "ip": container_ip(),
        }
    ]
    for number in свои:
        mac = instance_mac(number)
        ip = next((address for address, found in neighbours.items() if found == mac), "")
        state = "RUNNING" if container_name(number) in alive else "STOPPED"
        items.append({"number": number, "state": state, "ip": ip})
    return items


# Больше окон помощник не заведёт: десяток Android на одной машине — это
# уже не про окна, а про ферму (то же число записано у него самого).
MAX_WINDOWS = 12


def next_instance_number() -> int:
    """Первый свободный номер начиная со второго. Звать из потока.

    Свободный — значит не занятый ни в нашем списке, ни на диске. Список
    мог потеряться, а окна могла заводить прежняя версия: под таким номером
    остались бы чужие настройки и чужие данные.
    """
    taken = set(_registry())
    listing = _run(
        ["sh", "-c", 'ls -d /var/lib/waydroid/lxc/waydroid[0-9]* '
                     '"$HOME"/.local/share/waydroid[0-9]* 2>/dev/null'],
        timeout=15,
    ).stdout
    for line in listing.splitlines():
        match = re.search(r"waydroid(\d+)$", line.strip())
        if match:
            taken.add(int(match.group(1)))
    number = 2
    while number in taken:
        number += 1
    return number


def create_window(owner: str, stage=None) -> int:
    """Заводит новое окно записи и поднимает его. Отдаёт номер. Из потока.

    Основное окно готовит общее для всех (образ, мост, сокет), поэтому
    сначала поднимаем его: раньше «+ Окно» при выключенном Waydroid
    спрашивало пароль — и только потом сообщало, что основной контейнер не
    запущен.
    """
    say = stage if stage is not None else (lambda _text: None)
    say(tr("Поднимаем основное окно…"))
    ensure_session(stage)
    number = next_instance_number()
    say(tr("Создаём окно {number}… (до трёх минут)", number=number))
    create_instance(number, owner, start=True)
    say(tr("Ждём загрузки Android в окне {number}…", number=number))
    ip = wait_instance_ip(number)
    if not ip or not wait_instance_ready(ip):
        raise WaydroidError(
            tr("окно создано, но Android в нём не загрузился — "
               "перезапустите окно из его меню")
        )
    return number


def remove_instances(numbers: list[int]) -> None:
    """Стирает несколько окон одним заходом под root."""
    numbers = sorted({int(n) for n in numbers if int(n) != 1})
    if not numbers:
        return
    addresses = [instance_ip(number) for number in numbers]
    _instance_call("remove-many", *(str(n) for n in numbers), timeout=600)
    adb_forget(*addresses)
    known = _registry()
    for number in numbers:
        known.pop(number, None)
    _registry_set(known)


def create_instance(number: int, owner: str = "", start: bool = False) -> dict:
    """Заводит окно; со ``start`` — сразу поднимает его тем же заходом
    под root, чтобы пароль спросили один раз, а не дважды."""
    if number > MAX_WINDOWS:
        raise WaydroidError(
            tr("окон слишком много: больше {limit} не заводим", limit=MAX_WINDOWS)
        )
    # Проверяем память до создания, а не после: иначе окно заводится,
    # занимает место на диске — и не может запуститься.
    require_memory_for_window()
    argv = ["create", str(number)] + (["--start"] if start else [])
    result = _instance_call(*argv, timeout=600 if start else 300) or {}
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
    require_memory_for_window()
    # Помощник внутри ждёт сеть и при нужде перезапускает окно — на это
    # уходит до четырёх минут, и обрывать его на середине нельзя.
    result = _instance_call("start", str(number), timeout=420) or {}
    if not _settle_instance(number):
        return result
    # Эти свойства читаются при старте — без перезагрузки они не в счёт.
    return _instance_call("restart", str(number), timeout=420) or result


def _settle_instance(number: int) -> bool:
    """Приводит настройки поднявшегося окна к общим.

    Окно должно быть настроено как основное: сессия Waydroid и Merci
    правят основной контейнер под себя, а дополнительные поднимаются
    напрямую и об этих правках не знают. Отдаёт True, если что-то пришлось
    поменять: тогда окну нужен ещё один перезапуск.
    """
    ip = wait_instance_ip(number)
    if not ip or not wait_instance_ready(ip):
        return False
    changed = bool(inherit_main_props(ip))
    wanted = global_size()
    if all(wanted) and display_size(ip) != wanted:
        _apply_size_inside(ip, wanted[0], wanted[1])
        changed = True
    return changed


def start_instances(numbers: list[int]) -> dict:
    """Поднимает несколько окон одним заходом под root — один пароль.

    По одному это стоило бы запроса пароля на каждое окно: sudo его здесь
    не запоминает.
    """
    numbers = sorted({int(n) for n in numbers if int(n) != 1})
    if not numbers:
        return {}
    require_memory_for_window(len(numbers))
    result = _instance_call("start-many", *(str(n) for n in numbers), timeout=900) or {}
    changed = [number for number in numbers if _settle_instance(number)]
    if changed:
        _instance_call("restart-many", *(str(n) for n in changed), timeout=900)
    return result


def restart_instance(number: int) -> dict:
    """Гасит окно и поднимает заново — один заход под root, один пароль."""
    adb_forget(instance_ip(number))
    return _instance_call("restart", str(number), timeout=420) or {}


def stop_instances(numbers: list[int]) -> dict:
    """Выключает несколько окон одним заходом под root."""
    numbers = sorted({int(n) for n in numbers if int(n) != 1})
    if not numbers:
        return {}
    addresses = [instance_ip(number) for number in numbers]
    result = _instance_call("stop-many", *(str(n) for n in numbers), timeout=300) or {}
    adb_forget(*addresses)
    return result


# Сколько памяти нужно, чтобы поднять ещё один Android: сам он занимает
# около гигабайта, и ещё столько же попросит приложение внутри.
_WINDOW_MEMORY_MB = 1500


def free_memory_mb() -> int:
    """Свободная память хоста вместе с подкачкой, в мегабайтах. 0 — не знаем."""
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="ascii") as handle:
            for line in handle:
                name, _, rest = line.partition(":")
                parts = rest.split()
                if parts and parts[0].isdigit():
                    values[name] = int(parts[0])
    except (OSError, ValueError):
        return 0
    if "MemAvailable" not in values:
        return 0
    return (values["MemAvailable"] + values.get("SwapFree", 0)) // 1024


def require_memory_for_window(count: int = 1) -> None:
    """Не даёт поднять окно, на которое заведомо не хватит памяти.

    Каждый Android — отдельная система. Когда память кончается, плохо
    становится не окну, а всей машине: она уходит в подкачку и перестаёт
    отвечать, и закрыть лишнее уже нечем. Отказ с объяснением лучше.
    """
    free = free_memory_mb()
    need = _WINDOW_MEMORY_MB * max(1, count)
    if free and free < need:
        raise WaydroidError(
            tr(
                "не хватает памяти: нужно около {need} МБ, свободно {free} МБ — "
                "закройте что-нибудь или выключите другие окна",
                need=need,
                free=free,
            )
        )


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
    address = instance_ip(number)
    result = _instance_call("stop", str(number), timeout=180) or {}
    adb_forget(address)
    return result


def remove_instance(number: int) -> dict:
    address = instance_ip(number)
    result = _instance_call("remove", str(number), timeout=300) or {}
    adb_forget(address)
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


def adb_reachable(ip: str, seconds: float = 1.5) -> bool:
    """Слушает ли кто-нибудь порт adb по этому адресу.

    Спрашиваем сами, обычным подключением с коротким сроком, — и только
    потом зовём `adb connect`. Выключенное окно ещё долго числится в
    таблице соседей моста, и подключение к нему adb пытается установить
    целую минуту; всё это время его сервер занят, и запросы к живым
    контейнерам тоже ждут. Так после «Выключить окно» карточка на минуту
    теряла связь с основным контейнером.
    """
    if not ip:
        return False
    try:
        with socket.create_connection((ip, int(_ADB_PORT)), timeout=seconds):
            return True
    except (OSError, ValueError):
        return False


def adb_forget(*addresses: str) -> None:
    """Просит adb забыть контейнеры, которые мы выключаем.

    Иначе запись о подключении остаётся висеть в сервере adb, и он время от
    времени пытается её оживить.
    """
    for ip in addresses:
        if ip:
            _run(["adb", "disconnect", f"{ip}:{_ADB_PORT}"], timeout=10)


def instance_adb(ip: str, args: list[str], timeout: int = 120):
    """Команда adb в конкретный контейнер.

    Своего adb_connect на каждый вызов не делаем — только при отказе:
    подключение живёт долго, а лишний connect стоит секунды.
    """
    target = f"{ip}:{_ADB_PORT}"
    result = _run(["adb", "-s", target, *args], timeout=timeout)
    text = (result.stdout + result.stderr).lower()
    if result.returncode != 0 and ("not found" in text or "offline" in text or "unauthorized" in text):
        _run(["adb", "disconnect", target], timeout=10)
        if not adb_reachable(ip):
            return result  # там никого нет: переподключаться не к кому
        _run(["adb", "connect", target], timeout=20)
        result = _run(["adb", "-s", target, *args], timeout=timeout)
    return result


def can_measure_windows() -> bool:
    """Умеем ли спросить у композитора размер окна приложения.

    Без обращения к хосту: смотрит на то, что уже известно о системе.
    """
    system = host_known()
    return hostinfo.window_argv(hostinfo.window_query(system, system.tools or None)) is not None


def host_windows() -> list[dict]:
    """Окна контейнеров на хосте: пакет, размер, в фокусе ли.

    Спрашиваем композитор — тот, что на этой системе: Hyprland, Sway.
    Остальные (GNOME, сеанс X11) посторонним программам список окон не
    дают: вернётся пусто, и подгонка под окно просто не предложится —
    обещать то, чего не можем узнать, хуже, чем промолчать.
    """
    system = host()
    kind = hostinfo.window_query(system, system.tools or None)
    argv = hostinfo.window_argv(kind)
    if argv is None:
        return []
    result = _run(argv, timeout=30)
    return hostinfo.parse_windows(kind, result.stdout)


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

    # Выбор запоминаем сразу после записи в общие свойства и до перезапуска.
    # Перезапуск сессии сверяет общие свойства с тем, что помнит Merci, и
    # «чинит» расхождение: пока здесь лежал прежний размер, он возвращал его
    # обратно — новый размер не применялся никогда, зато спрашивался второй
    # пароль. До записи в общие свойства запоминать нельзя: сорвись она,
    # Merci помнила бы размер, которого нет ни в одном окне.
    from .settings import Settings

    Settings().window_size = (width, height)

    if stage is not None:
        stage(tr("Перезапускаем основное окно…"))
    restart_session()

    # Основной контейнер мог быть выключен, когда размер меняли: тогда его
    # собственный склад свойств прежний размер и держит — `waydroid prop set`
    # до выключенного не доходит, а запись в складе перебивает общую. Смотрим,
    # с каким размером он поднялся на самом деле, и если с прежним — правим
    # склад изнутри и перезапускаем ещё раз: читается он только при старте.
    ip = container_ip()
    actual = display_size(ip) if ip else (0, 0)
    if all(actual) and actual != (width, height):
        if stage is not None:
            stage(tr("Основное окно держит прежний размер — исправляем…"))
        _apply_size_inside(ip, width, height)
        restart_session()

    if stopped:
        if stage is not None:
            stage(tr("Поднимаем окна заново…"))
        start_instances(stopped)


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


def _surface_layers(ip: str) -> str | None:
    """Список слоёв SurfaceFlinger или None, если он не отвечает.

    Один пропущенный срок — ещё не затор. Под полной загрузкой процессора
    (тяжёлая игра на старте) SurfaceFlinger отвечает дольше трёх секунд, и
    Merci принимала это за мёртвую картинку: перезапускала здоровый
    контейнер прямо посреди запуска. Спрашиваем дважды, второй раз с запасом.
    """
    for patience in (3, 8):
        result = instance_adb(
            ip,
            ["shell", "dumpsys", "-t", str(patience), "SurfaceFlinger", "--list"],
            timeout=patience + 20,
        )
        if "TIMEOUT" not in (result.stdout + result.stderr) and result.stdout.strip():
            return result.stdout
        time.sleep(2)
    return None


def screen_state(package: str = "", ip: str | None = None) -> str:
    """Что сейчас с картинкой Android: "есть", "нет" или "затор".

    Проверка отдельная от служб, и вот почему. Окно Waydroid можно закрыть
    средствами композитора — в Hyprland это Win+Q. Android при этом не
    закрывается: приложение продолжает работать, но поверхности, на
    которой оно рисовало, больше нет. После нескольких таких закрытий
    SurfaceFlinger перестаёт отвечать вовсе, и нового окна не появится уже
    никогда. Снаружи всё выглядит исправным: службы окон и пакетов на
    вопрос «найдены?» отвечают «да», `waydroid app launch` возвращает ноль.

    Поэтому спрашиваем сам SurfaceFlinger: живой отвечает списком слоёв за
    доли секунды, застрявший не отвечает совсем. Заодно по этому списку
    видно, есть ли у приложения слои задачи.
    """
    # Пустая строка и «не указано» — разные вещи: у выключенного окна
    # адреса нет, и подставлять вместо него основной контейнер нельзя.
    ip = container_ip() if ip is None else ip
    if not ip:
        return "нет"
    layers = _surface_layers(ip)
    if layers is None:
        return "затор"
    if not package:
        return "есть"
    # Композитор решает, показывать ли окно, по слоям задачи — тем, что
    # названы «TID:<номер>#<пакет>/...».
    return (
        "есть"
        if any(
            line.lstrip().startswith("TID:") and package in line
            for line in layers.splitlines()
        )
        else "нет"
    )


def open_windows(ip: str | None = None) -> int:
    """Сколько окон контейнер держит на экране хоста. -1 — не знаем.

    Считает сам композитор Waydroid и сообщает свойством: единица, пока
    окно приложения открыто, ноль — сразу после того, как его закрыли.
    Приложение при этом может спокойно работать дальше в фоне.
    """
    ip = container_ip() if ip is None else ip
    if not ip:
        return -1
    value = instance_adb(
        ip, ["shell", "getprop", "waydroid.open_windows"], timeout=20
    ).stdout.strip()
    return int(value) if value.isdigit() else -1


def wait_window(ip: str, seconds: int = 20, package: str = "") -> bool:
    """Ждёт, пока композитор откроет окно.

    Счётчик окон публикует композитор этой сборки Waydroid; у других сборок
    и у старых образов такого свойства нет вовсе. Раньше «не знаем»
    читалось как «окна нет»: игра рисовала кадр, двадцать секунд ожидания —
    и Merci прятала её обратно с ошибкой «окно не открылось». Теперь без
    свойства судим по слоям задачи приложения: по ним композитор и решает,
    показывать ли окно.
    """
    deadline = time.time() + seconds
    while True:
        count = open_windows(ip)
        if count > 0:
            return True
        if count < 0 and package and screen_state(package, ip) == "есть":
            return True
        if time.time() >= deadline:
            return False
        time.sleep(1.5)


def package_ready(package: str, ip: str = "", user: int = 0) -> bool:
    """Видит ли Android именно это приложение.

    Служба пакетов отвечает раньше, чем дочитывает установленные: сразу
    после перезапуска контейнера `am start` на живой системе отвечает
    «Activity class does not exist», хотя приложение никуда не девалось.
    """
    ip = ip or container_ip()
    if not ip:
        return False
    # Спрашиваем в том профиле, где приложение стоит. Без этого вопрос
    # уходил от имени основного профиля, а приложение из профиля 10 он «не
    # видит» вовсе: ожидание выбирало весь срок, и каждый запуск записи с
    # профилем начинался с минуты молчания на «Открываем…».
    args = ["shell", "cmd", "package", "path"]
    if user:
        args += ["--user", str(int(user))]
    result = instance_adb(ip, [*args, package], timeout=20)
    return "package:" in result.stdout


def wait_package_ready(package: str, seconds: int = 60, ip: str = "", user: int = 0) -> bool:
    deadline = time.time() + seconds
    while True:
        if package_ready(package, ip, user):
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
    """Открывает приложение в дополнительном окне: свой контейнер, свои данные.

    Если встала картинка этого окна, наружу уходит PictureStuck: лечится
    это перезапуском контейнера, а он под root — решать вызывающему.
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

    _launch_in_container(entry, ip, 0, say)


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


def wait_app_drawing(package: str, seconds: int = 90, ip: str | None = None) -> bool:
    """Ждёт, пока приложение нарисует первый кадр.

    Экран отдавать раньше нельзя. Пока контейнеру велено показывать
    приложение, а показывать нечего, окна нет — а без окна композитор хоста
    не шлёт кадровых сигналов, и Android внутри задыхается.

    Пока ждём, контейнер стоит в покое: без окна, но и без вреда. Если срок
    вышел, а кадров нет, — так и отвечаем: «не рисует». Раньше здесь была
    лазейка «у приложения есть слои задачи — значит, сойдёт», и через неё
    Merci рапортовала об успехе, когда игра намертво висела без единого кадра.
    """
    deadline = time.time() + seconds
    while True:
        if app_has_surface(package, ip) or app_frames(package, ip) > 0:
            return True
        if time.time() >= deadline:
            return False
        time.sleep(3)


def container_clock(ip: str) -> str:
    """Текущее время контейнера в виде, который понимает logcat -t."""
    return instance_adb(
        ip, ["shell", "date '+%m-%d %H:%M:%S.000'"], timeout=20
    ).stdout.strip()


def composer_refuses_window(ip: str | None = None, since: str = "") -> bool | None:
    """Решил ли композитор, что окна не будет.

    Он объявляет это сам, в журнале контейнера:

        single-window: target tid='1200012' aid='com.roblox.client' should_show=1
        single-window: tid 1000009 (com.roblox.client) in ignored_apps -> no window
        single-window: NO TID layer among 4 layers (window stays closed)

    True — окна не будет, False — будет, None — композитор промолчал.
    ``since`` — отметка container_clock: без неё в выборку попадают решения
    прошлого запуска, и свежий старт гасился из-за старого отказа.
    """
    ip = container_ip() if ip is None else ip
    if not ip:
        return None
    window = f"-t '{since}'" if since else "-t 150"
    result = instance_adb(ip, ["shell", f"logcat -d {window}"], timeout=25)
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
            args += ["--user", str(int(user))]
        # adb склеивает аргументы в одну строку для оболочки на той стороне,
        # поэтому имя берём в кавычки сами: `$` у вложенного класса иначе
        # развернулся бы как переменная, а что похуже — исполнилось бы.
        args += ["-n", shlex.quote(component)]
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


def launch_component(package: str, user: int = 0, ip: str | None = None) -> str:
    """Что запускать — «пакет/класс», как это видит сам Android. Пусто — не знаем.

    Имя точки входа, записанное при добавлении APK, годится не всегда.
    Приложения держат несколько значков-псевдонимов (обычный, праздничный)
    и сами включают и выключают их; запуск выключенного Android отвергает
    словами «Activity class does not exist» — и запись, работавшая вчера,
    сегодня «не запускается». Поэтому перед запуском спрашиваем у Android,
    что он сам считает точкой входа прямо сейчас.

    Флаги запроса разрешают ответ и для профиля, который ещё не разблокирован
    (не запускался с загрузки): без них Android говорит «No activity found»
    про приложение, которое спокойно стоит на месте.
    """
    ip = container_ip() if ip is None else ip
    if not ip:
        return ""
    safe_package(package)
    args = ["shell", "cmd", "package", "resolve-activity", "--brief", "--query-flags", "786432"]
    if user:
        args += ["--user", str(int(user))]
    args += [
        "-a", "android.intent.action.MAIN",
        "-c", "android.intent.category.LAUNCHER",
        "-p", package,
    ]
    result = instance_adb(ip, args, timeout=20)
    for line in reversed(result.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith(package + "/") and re.fullmatch(r"[A-Za-z0-9_.]+/[A-Za-z0-9_.$]+", line):
            return line
    return ""


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


def _start_reason(answer: dict, number: int) -> str:
    """Что помощник записал о несостоявшемся запуске окна."""
    log = (answer or {}).get("log") or ""
    if isinstance(log, dict):
        log = log.get(str(number), "")
    return f": {log}" if log else ""


def restart_window(number: int) -> str:
    """Перезапускает окно и ждёт, пока Android в нём ответит. Отдаёт адрес."""
    ensure_session()
    answer = restart_instance(number)
    ip = wait_instance_ip(number)
    if not ip:
        raise WaydroidError(
            tr("окно не получило адрес в сети") + _start_reason(answer, number)
        )
    if not wait_instance_ready(ip) or not wait_system_ready(ip, 90):
        raise WaydroidError(tr("Android в этом окне не загрузился"))
    return ip


def _raise_window(number: int, say, fresh: bool = False) -> str:
    """Адрес окна, в котором Android отвечает, — поднимая и перезапуская
    контейнер, если без этого никак.

    Случаев три, а раньше различался только первый:

    * окно выключено — запускаем;
    * окно числится запущенным, но адреса у него нет или Android внутри
      молчит — оно зависло на старте и само не пройдёт. Раньше на это
      отвечали «окно не получило адрес в сети», и сделать с таким окном из
      Merci было нечего. Теперь перезапускаем;
    * окно работает — берём как есть.

    ``fresh`` — окно только что подняли (групповой запуск): ему даём
    догрузиться полный срок и за зависшее не принимаем.
    """
    item = next((i for i in instances() if i.get("number") == number), None)
    if item is None:
        raise WaydroidError(tr("окно не найдено"))

    answer: dict = {}
    if item.get("state") == "RUNNING":
        ip = item.get("ip", "") or wait_instance_ip(number, 120 if fresh else 20)
        # Здоровое окно отвечает сразу; срок с запасом — на случай, когда
        # оно грузится, потому что его подняли минуту назад.
        if ip and wait_system_ready(ip, 180 if fresh else 45):
            return ip
        say(tr("Окно {number} не отвечает — перезапускаем…", number=number))
        answer = restart_instance(number)
    else:
        say(tr("Запускаем окно {number}… (до трёх минут)", number=number))
        answer = start_instance(number)

    ip = wait_instance_ip(number)
    if not ip:
        raise WaydroidError(
            tr("окно не получило адрес в сети") + _start_reason(answer, number)
        )
    say(tr("Ждём загрузки Android…"))
    if not wait_instance_ready(ip) or not wait_system_ready(ip, 90):
        raise WaydroidError(tr("Android в этом окне не загрузился"))
    return ip


def open_in_window(
    entry: Entry, number: int, stage=None, multiuser: bool = False, fresh: bool = False,
    replace: str = "",
) -> None:
    """Открывает приложение в окне с этим номером — от выключенного
    контейнера до картинки на экране. Блокирующая: зовётся из потока.

    Основное окно идёт обычным путём (установка, профиль, запуск). Для
    дополнительного — поднимаем его контейнер, ставим приложение, если его
    там ещё нет, и запускаем; застрявшую картинку лечим одним перезапуском
    этого окна. Если в окне стоит другая сборка того же пакета, решает
    ``replace`` (см. put_build); по умолчанию её не трогаем.
    """
    say = stage if stage is not None else (lambda _text: None)
    if number == 1:
        install_and_launch(entry, stage, multiuser, replace)
        return

    # Основное окно готовит общее для всех: образ, мост, сокет. Пока оно
    # не работает, остальные подняться не могут — Android в них стартует
    # без сети и остаётся без адреса.
    say(tr("Поднимаем основное окно…"))
    ensure_session(stage)

    ip = _raise_window(number, say, fresh)
    # Контейнеры не делят установленные приложения: каждому своё. И стоять
    # в окне должна именно эта сборка: раньше хватало того, что пакет с
    # таким именем там есть, — и запускалось что стояло.
    put_build(entry, ip, 0, replace, number, stage)

    say(tr("Открываем…"))
    try:
        launch_in_instance(entry, ip, stage)
    except (PictureStuck, ContainerUnreachable):
        # Либо приложение работает без окна, либо Android в окне лёг
        # совсем. И то и другое лечится перезапуском этого окна.
        say(tr("Картинка застряла — перезапускаем окно {number}…", number=number))
        restart_instance(number)
        ip = wait_instance_ip(number)
        if not ip or not wait_instance_ready(ip) or not wait_system_ready(ip, 90):
            raise
        say(tr("Открываем…"))
        launch_in_instance(entry, ip, stage)


def open_in_windows(
    entry: Entry, numbers: list[int], stage=None, multiuser: bool = False
) -> tuple[list[int], list[str]]:
    """Открывает приложение в нескольких окнах по очереди.

    Отдаёт (номера открывшихся, описания неудач). Выключенные окна
    поднимаются разом, одним заходом под root: по одному это был бы запрос
    пароля на каждое.
    """
    say = stage if stage is not None else (lambda _text: None)
    numbers = sorted(set(numbers))
    say(tr("Поднимаем основное окно…"))
    ensure_session(stage)
    if 1 in numbers and where_to_launch(entry)["conflict"]:
        # Основное окно занято другой сборкой — это не окно этой записи.
        # Молча менять в нём сборку нельзя, а ругаться на каждое «Запустить
        # все» незачем: просто открываем там, где запись живёт.
        numbers = [n for n in numbers if n != 1]
        if not numbers:
            current, version = installed_build(entry.package)
            raise InstallConflict(
                tr("основное окно занято другой сборкой, а своих окон у записи нет"),
                build=current or "", version=version,
            )
    clones = [n for n in numbers if n != 1]
    fresh: set[int] = set()
    not_started: dict[int, str] = {}
    if clones:
        running = {i["number"] for i in instances() if i.get("state") == "RUNNING"}
        stopped = [n for n in clones if n not in running]
        if stopped:
            say(tr("Запускаем окна: {numbers}… (до трёх минут)",
                   numbers=", ".join(str(n) for n in stopped)))
            # Не вышло поднять разом (не хватило памяти, отменили пароль) —
            # это не повод не открывать основное окно и те, что уже работают.
            # Сами выключенные повторно не пробуем: это был бы ещё запрос
            # пароля на каждое, а человек только что от него отказался.
            try:
                start_instances(stopped)
                fresh = set(stopped)
            except WaydroidError as failure:
                not_started = {number: str(failure) for number in stopped}

    opened: list[int] = []
    failures: list[str] = []
    for number in numbers:
        if number in not_started:
            failures.append(
                tr("окно {number}: {error}", number=number, error=not_started[number])
            )
            continue
        say(tr("Открываем окно {number}…", number=number))
        try:
            open_in_window(entry, number, stage, multiuser, fresh=number in fresh)
            opened.append(number)
        except Exception as failure:  # noqa: BLE001 — причина уйдёт человеку
            failures.append(tr("окно {number}: {error}", number=number, error=failure))
    return opened, failures


def close_in_windows(package: str, owner: str = "", build: str = "") -> tuple[int, list[str]]:
    """Закрывает приложение во всех окнах записи.

    Отдаёт (в скольких окнах закрыто, описания неудач). Раньше ошибки
    здесь глотались целиком, и «закрыто во всех окнах» сообщалось даже
    тогда, когда не закрылось ни в одном.

    С непустым ``build`` окно, в котором стоит другая сборка того же
    пакета, пропускается: имя пакета у неё то же самое, и «остановить все»
    из карточки одной сборки закрывало бы чужую игру в основном окне.
    """
    closed = 0
    failures: list[str] = []
    for item in instances(owner):
        number = item.get("number", 0)
        ip = item.get("ip") or ""
        if item.get("state") != "RUNNING" or not ip:
            continue  # выключенное окно закрывать незачем
        try:
            if build:
                current, _version = _build_seen(package, ip)
                if current and current != build:
                    continue
            stop_app(package, ip)
            closed += 1
        except Exception as failure:  # noqa: BLE001 — причина уйдёт человеку
            failures.append(tr("окно {number}: {error}", number=number, error=failure))
    return closed, failures


def guard_idle_screens() -> list[str]:
    """Возвращает в покой окна, которым велено показывать закрытое.

    Контейнер живёт без окна только пока ему не велено ничего показывать.
    Стоит закрыть окно приложения — средствами композитора, тем же Win+Q, —
    и контейнер остаётся с указанием показывать то, чего на экране нет:
    композитор хоста перестаёт слать кадровые сигналы, Android внутри
    задыхается, и через минуту сторож начинает убивать system_server по
    кругу. Вылечить это потом нечем, кроме перезапуска контейнера.

    Композитор сам считает свои окна (waydroid.open_windows): ноль при
    названном приложении и есть тот самый случай. Сторож даёт около минуты,
    так что заглядывать раз в двадцать секунд достаточно. Контейнеры, с
    которыми прямо сейчас идёт операция, не трогаем: запуск сам знает, что
    делает, и окно у него появится через секунду.
    """
    returned = []
    for item in instances():
        ip = item.get("ip") or ""
        if item.get("state") != "RUNNING" or not ip or is_operating(ip):
            continue
        try:
            showing = active_app(ip)
            if not showing or showing == IDLE_SCREEN:
                continue
            if open_windows(ip) != 0 or is_operating(ip):
                continue  # окно на месте, либо контейнер не ответил
            set_active_app(ip, IDLE_SCREEN)
            returned.append(f"{item['number']}:{showing}")
        except WaydroidError:
            continue
    return returned


def safe_package(package: str) -> str:
    """Имя пакета, пригодное для подстановки в команду оболочки.

    Имя приходит из APK, а APK приносит пользователь: манифест может
    назвать пакет хоть «a; rm -rf /». adb склеивает аргументы в одну строку
    для оболочки на той стороне, поэтому всё, что туда подставляется,
    обязано быть обычным именем пакета — буквы, цифры, подчёркивания, точки.
    """
    if not re.fullmatch(r"[A-Za-z0-9_]+(\.[A-Za-z0-9_]+)+", package or ""):
        raise WaydroidError(tr("недопустимое имя пакета"))
    return package


def probe_container(ip: str, package: str, user: int = 0) -> dict:
    """Что происходит в контейнере — одним быстрым запросом.

    Отдаёт: alive (отвечает ли Android; None — не знаем), app (работает ли
    приложение), window (открыто ли его окно на экране хоста), showing (что
    контейнеру велено показывать), installed (стоит ли приложение).

    «Не знаем» — отдельный ответ, и это важно. Под нагрузкой или в момент
    перезапуска system_server контейнер молчит несколько секунд; раньше
    такое молчание записывалось как «не отвечает» или «приложения нет», и
    карточка прятала кнопку остановки ровно тогда, когда она нужна.
    """
    safe_package(package)
    who = f"--user {int(user)} " if user else ""
    script = (
        'echo "SYS=$(timeout 4 cmd package path android 2>/dev/null | head -c 8)"; '
        f'echo "PID=$(pidof {package})"; '
        'echo "WIN=$(getprop waydroid.open_windows)"; '
        'echo "ACT=$(getprop waydroid.active_apps)"; '
        f'echo "PKG=$(timeout 4 pm path {who}{package} 2>/dev/null | head -c 8)"'
    )
    result = instance_adb(ip, ["shell", script], timeout=15)
    fields = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    state = {"alive": None, "app": False, "window": False, "showing": "", "installed": None}
    if result.returncode != 0 or "SYS" not in fields:
        return state  # adb не ответил: ни «жив», ни «мёртв»
    state["alive"] = fields["SYS"].startswith("package:")
    state["app"] = bool(fields.get("PID", "").strip())
    win = fields.get("WIN", "").strip()
    state["window"] = win.isdigit() and int(win) > 0
    state["showing"] = fields.get("ACT", "").strip()
    if state["alive"]:
        state["installed"] = fields.get("PKG", "").startswith("package:")
    return state


def window_states(package: str, owner: str = "", user: int = 0, build: str = "") -> list[dict]:
    """Снимок окон приложения для карточки: номер, состояние контейнера,
    адрес и то, что в нём происходит (см. probe_container).

    С непустым ``build`` (начало sha256 файла записи) в снимок попадает ещё
    и то, чья сборка стоит в окне: ``foreign`` — там другая сборка того же
    пакета, ``other`` — её версия. Без этого карточка говорила «приложение
    открыто» про чужую сборку и «приложения здесь нет» про пакет, который
    стоит в другом профиле.
    """
    states = []
    for item in instances(owner):
        number = item.get("number", 0)
        ip = item.get("ip") or ""
        running = item.get("state") == "RUNNING"
        state = {
            "number": number, "running": running, "ip": ip,
            "alive": None, "app": False, "window": False, "showing": "", "installed": None,
            "foreign": False, "other": "", "other_build": "",
        }
        if running and ip:
            state.update(probe_container(ip, package, user if number == 1 else 0))
            if build and state["alive"]:
                current, version = _build_seen(package, ip)
                if current and current != build:
                    state["foreign"], state["other"], state["other_build"] = True, version, current
        states.append(state)
    return states


def _showing(ip: str) -> tuple[str, bool] | None:
    """Что контейнеру велено показывать и есть ли у него окно на экране.

    None — контейнер не ответил.
    """
    script = (
        'echo "ACT=$(getprop waydroid.active_apps)"; '
        'echo "WIN=$(getprop waydroid.open_windows)"'
    )
    result = instance_adb(ip, ["shell", script], timeout=10)
    fields = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line
    )
    if result.returncode != 0 or "ACT" not in fields:
        return None
    win = fields.get("WIN", "").strip()
    return fields["ACT"].strip(), win.isdigit() and int(win) > 0


def card_snapshot(
    package: str, owner: str = "", user: int = 0, build: str = "",
    catalog: list[tuple[str, str, str]] | tuple = (),
) -> tuple[list[dict], dict[str, int]]:
    """Всё, что карточка показывает об окнах, — одним проходом по хосту.

    Отдаёт (окна этой записи — как window_states, {ключ записи: сколько её
    окон сейчас открыто}). Второе нужно библиотеке: по отметке у строки
    видно, какая из сборок работает, не открывая её карточку.

    ``catalog`` — записи библиотеки как (ключ, пакет, начало sha256).
    Контейнеры этой записи опрашиваются полностью, чужие — одним коротким
    вопросом «что показываешь».
    """
    if owner:
        adopt_orphan_instances(owner)
    registry = _registry()
    package_of = {slug: pkg for slug, pkg, _digest in catalog}
    states: list[dict] = []
    counts: dict[str, int] = {}
    for item in instances():
        number = item.get("number", 0)
        ip = item.get("ip") or ""
        running = item.get("state") == "RUNNING"
        mine = number == 1 or registry.get(number) == owner
        showing, window = "", False
        if mine:
            state = {
                "number": number, "running": running, "ip": ip,
                "alive": None, "app": False, "window": False, "showing": "",
                "installed": None, "foreign": False, "other": "", "other_build": "",
            }
            if running and ip:
                state.update(probe_container(ip, package, user if number == 1 else 0))
                if build and state["alive"]:
                    current, version = _build_seen(package, ip)
                    if current and current != build:
                        state["foreign"], state["other"] = True, version
                        state["other_build"] = current
                showing, window = state["showing"], state["window"]
            states.append(state)
        elif running and ip and catalog:
            answer = _showing(ip)
            if answer is not None:
                showing, window = answer
        if not window or not showing or showing == IDLE_SCREEN:
            continue
        holder = _window_holder(number, ip, showing, catalog, registry, package_of)
        if holder:
            counts[holder] = counts.get(holder, 0) + 1
    return states, counts


def _window_holder(number: int, ip: str, showing: str, catalog, registry: dict,
                   package_of: dict) -> str:
    """Ключ записи библиотеки, чьё приложение показывает окно, — или пусто."""
    if number == 1:
        # Основное окно общее: чьё в нём приложение, видно по сборке.
        holders = [slug for slug, pkg, _digest in catalog if pkg == showing]
        if len(holders) > 1:
            current, _version = _build_seen(showing, ip)
            holders = [
                slug for slug, pkg, digest in catalog
                if pkg == showing and digest and digest == current
            ]
        return holders[0] if holders else ""
    holder = registry.get(number, "")
    return holder if package_of.get(holder) == showing else ""


def open_entries(catalog: list[tuple[str, str, str]] | tuple) -> dict[str, int]:
    """Какие записи библиотеки сейчас открыты: {ключ записи: число окон}.

    То же, что считает card_snapshot, но без карточки: нужно меню трея —
    «Открыть запущенную игру» должна знать, запущено ли что-нибудь, а пульс
    при спрятанном окне Merci этого не обновляет. Блокирующая.
    """
    registry = _registry()
    package_of = {slug: pkg for slug, pkg, _digest in catalog}
    counts: dict[str, int] = {}
    for item in instances():
        number, ip = item.get("number", 0), item.get("ip") or ""
        if item.get("state") != "RUNNING" or not ip:
            continue
        answer = _showing(ip)
        if answer is None:
            continue
        showing, window = answer
        if not window or not showing or showing == IDLE_SCREEN:
            continue
        holder = _window_holder(number, ip, showing, catalog, registry, package_of)
        if holder:
            counts[holder] = counts.get(holder, 0) + 1
    return counts


def certainly_off() -> bool:
    """Точно ли выключать нечего.

    Да — когда сессия остановлена (или Waydroid ещё не подготовлен) и ни
    одно дополнительное окно не работает. Неясное состояние — контейнер
    завис и не отвечает — выключенным не считается: тогда выключить его
    нужнее всего. Блокирующая.
    """
    ready, detail = status(use_cache=False)
    if ready or detail not in ("сессия остановлена", "не установлен",
                               "образ Android не загружен"):
        return False
    return not any(
        item["state"] == "RUNNING" for item in instances() if item["number"] != 1
    )


# Что за сборка стоит в контейнере — с коротким сроком годности: карточка
# спрашивает это каждые несколько секунд, а меняется оно только при
# установке. Установка и удаление сбрасывают память сами (forget_builds).
_builds_seen: dict[tuple[str, str], tuple[float, str | None, str]] = {}


def _build_seen(package: str, ip: str) -> tuple[str | None, str]:
    key = (ip, package)
    seen = _builds_seen.get(key)
    if seen is not None and time.monotonic() - seen[0] < 20:
        return seen[1], seen[2]
    current, version = installed_build(package, ip)
    _builds_seen[key] = (time.monotonic(), current, version)
    return current, version


def forget_builds() -> None:
    """Сбрасывает память о сборках в контейнерах — после установки и удаления."""
    _builds_seen.clear()


def _installs_changed() -> None:
    """Что-то поставили или сняли: всё, что мы помним об установках, устарело."""
    _installed_cache.clear()
    _builds_seen.clear()


def stop_app(package: str, ip: str) -> None:
    """Закрывает приложение в контейнере — и убеждается, что оно закрылось.

    Раньше остановка не проверяла ничего: adb мог ответить unauthorized,
    offline или не ответить вовсе, а Merci рапортовала «закрыто». Теперь
    отказ команды — ошибка, и живой после неё процесс — тоже ошибка.
    Контейнер после остановки возвращается в покой: оставшись с указанием
    показывать закрытое приложение, он задыхается без окна.
    """
    safe_package(package)
    result = instance_adb(ip, ["shell", "am", "force-stop", package], timeout=60)
    text = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        raise ContainerUnreachable(text or tr("контейнер не ответил на команду"))
    for _ in range(6):
        if not app_running(package, ip):
            break
        time.sleep(1)
    else:
        raise WaydroidError(tr("приложение не закрылось"))
    set_active_app(ip, IDLE_SCREEN)


def stop_main_app(package: str) -> None:
    """Закрывает приложение в основном контейнере."""
    adb_connect()
    stop_app(package, _require_ip())


def stop_in_instance(package: str, ip: str) -> None:
    """Закрывает приложение в дополнительном окне."""
    stop_app(package, ip)


def apply_multiwindow_optimizations() -> bool:
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
    return _in_running_windows([
        ["shell", "settings", "put", "global", "force_resizable_activities", "1"],
        ["shell", "settings", "put", "global", "enable_freeform_support", "1"],
        ["shell", "setprop", "persist.sys.debug.multi_window", "1"],
    ])


def _in_running_windows(commands: list[list[str]], timeout: int = 15) -> bool:
    """Выполняет команды adb во всех работающих окнах — основном и
    дополнительных. Отдаёт, дошло ли хоть до одного.

    Через instance_adb, а не adb_connect: тот при отказе в доступе зовёт
    помощника, то есть спросил бы пароль из-за щелчка по переключателю.
    Раньше эти настройки уходили только в основное окно, ошибки молча
    глотались, а окно Merci в любом случае сообщало об успехе — в том числе
    при выключенном контейнере.
    """
    if not adb_available():
        return False
    reached = False
    for item in instances():
        if item["state"] != "RUNNING" or not item["ip"]:
            continue
        fine = True
        for command in commands:
            fine = instance_adb(item["ip"], command, timeout=timeout).returncode == 0 and fine
        reached = reached or fine
    return reached


# Что из быстрых настроек уже послано какой загрузке Android: (адрес, номер
# процесса контейнера) → (экономия, предел кадров). Только чтобы не слать
# одно и то же при каждом запуске приложения.
_quick_sent: dict[tuple[str, int], tuple[bool, int]] = {}
# Переключатели щёлкают быстро, а каждое нажатие — свой поток: без замка
# «включить» и «выключить» перемешивались бы на полпути.
_quick_lock = threading.RLock()

_ANIMATION_SCALES = (
    "window_animation_scale", "transition_animation_scale", "animator_duration_scale"
)


def _quick_script(eco: bool | None, fps: int | None) -> str:
    """Команды Android для быстрых настроек — одной строкой оболочки.

    ``None`` — эту настройку не трогать. Экономия: True выключает системные
    анимации, False возвращает их. Предел кадров: число ставит его, ноль
    снимает.
    """
    parts: list[str] = []
    if eco is not None:
        scale = "0" if eco else "1"
        parts += [f"settings put global {name} {scale}" for name in _ANIMATION_SCALES]
    # Удалась ли настройка, судим по settings: свойство debug.sf.fps —
    # довесок, и его отказ не должен объявлять неудачей всё остальное (иначе
    # команды слались бы заново после каждой операции).
    if fps:
        parts += [
            f"settings put system min_refresh_rate {int(fps)}",
            f"settings put system peak_refresh_rate {int(fps)}",
            f"{{ setprop debug.sf.fps {int(fps)} || true; }}",
        ]
    elif fps is not None:
        parts += [
            "settings delete system min_refresh_rate",
            "settings delete system peak_refresh_rate",
            "{ setprop debug.sf.fps 0 || true; }",
        ]
    return " && ".join(parts)


def _numbers(values) -> set[int]:
    """Номера окон из сохранённого списка — что бы в нём ни лежало."""
    found: set[int] = set()
    for value in values if isinstance(values, (list, tuple)) else ():
        with suppress(TypeError, ValueError):
            found.add(int(value))
    return found


def _quick_applied(settings) -> dict[str, set[int]]:
    """Окна, в которых сейчас лежат значения Merci: {настройка: номера}.

    По этой памяти выключенное возвращается ровно там, где было включено:
    чужие настройки анимаций в остальных окнах Merci не трогает.
    """
    saved = settings.get("quick_applied")
    if not isinstance(saved, dict):
        # Память завела эта версия. Прежняя слала настройки только в основное
        # окно — значит, включённое сейчас лежит именно там.
        return {
            "eco": {1} if settings.eco_mode else set(),
            "fps": {1} if settings.fps_limit else set(),
        }
    return {"eco": _numbers(saved.get("eco")), "fps": _numbers(saved.get("fps"))}


def note_quick_settings() -> None:
    """Заводит память о том, где лежат быстрые настройки, если её ещё нет.

    Зовётся перед сменой переключателя — пока в настройках прежние
    значения: по ним и видно, что оставила в основном окне прежняя версия.
    Заведи мы память после смены, только что включённое (и ещё никуда не
    посланное) считалось бы уже лежащим в Android. Только файл настроек,
    хост не трогает.
    """
    from .settings import Settings

    settings = Settings()
    if not isinstance(settings.get("quick_applied"), dict):
        applied = _quick_applied(settings)
        settings.set("quick_applied", {kind: sorted(numbers)
                                       for kind, numbers in applied.items()})


def sync_quick_settings() -> tuple[bool, bool]:
    """Доводит режим экономии и предел кадров до работающих окон.

    Отдаёт пару: дошло ли хоть до одного окна сейчас — и остались ли окна,
    до которых дойдёт позже, когда они поднимутся.

    Раньше настройки уходили только в основное окно и только в миг
    переключения: включённые при выключенном контейнере, они до него не
    доходили вовсе, предел кадров терялся при каждом перезапуске (он —
    свойство, живущее до выключения), а переключатель в Merci при этом
    оставался включённым. Теперь включённое шлётся каждой новой загрузке
    Android, а выключенное возвращается там, где Merci его включала: такие
    окна она помнит (``quick_applied``). Через instance_adb, а не
    adb_connect: тот при отказе в доступе зовёт помощника, то есть спросил
    бы пароль из-за щелчка по переключателю. Блокирующая: звать из потока.
    """
    with _quick_lock:
        return _sync_quick_settings()


def _sync_quick_settings() -> tuple[bool, bool]:
    from .settings import Settings

    note_quick_settings()
    settings = Settings()
    eco, fps = bool(settings.eco_mode), int(settings.fps_limit or 0)
    applied = _quick_applied(settings)
    before = {kind: sorted(numbers) for kind, numbers in applied.items()}
    if not (eco or fps or applied["eco"] or applied["fps"]):
        return False, False
    if not adb_available():
        return False, True

    items = instances()
    boots = _container_pids()
    wanted = (eco, fps)
    live: set[tuple[str, int]] = set()
    reached = False
    waiting = False
    for item in items:
        number, ip = int(item["number"]), item.get("ip") or ""
        undo_eco = not eco and number in applied["eco"]
        undo_fps = not fps and number in applied["fps"]
        if item.get("state") != "RUNNING" or not ip:
            waiting = waiting or eco or bool(fps) or undo_eco or undo_fps
            continue
        key = (ip, boots.get(container_name(number), 0))
        live.add(key)
        script = _quick_script(
            True if eco else (False if undo_eco else None),
            fps if fps else (0 if undo_fps else None),
        )
        if not script:
            continue
        if _quick_sent.get(key) == wanted and not (undo_eco or undo_fps):
            reached = True
            continue
        try:
            fine = instance_adb(ip, ["shell", script], timeout=30).returncode == 0
        except WaydroidError:
            fine = False
        if not fine:
            waiting = True
            continue
        _quick_sent[key] = wanted
        (applied["eco"].add if eco else applied["eco"].discard)(number)
        (applied["fps"].add if fps else applied["fps"].discard)(number)
        reached = True
    for key in [key for key in _quick_sent if key not in live]:
        del _quick_sent[key]

    # Удалённые окна из памяти убираем: возвращать в них уже нечего.
    existing = {int(item["number"]) for item in items}
    after = {kind: sorted(numbers & existing) for kind, numbers in applied.items()}
    if after != before:
        settings.set("quick_applied", after)
    return reached, waiting


def apply_eco_mode(enabled: bool) -> tuple[bool, bool]:
    """Режим экономии: отключает тяжёлые системные анимации Android.

    Зовётся, когда переключатель уже записан в настройки (``enabled`` — что
    в них записано). Отдаёт то же, что sync_quick_settings: применилось ли
    сейчас и ждёт ли где-то запуска.
    """
    return sync_quick_settings()


def set_fps_limit(fps: int) -> tuple[bool, bool]:
    """Ограничение частоты кадров в Android; ноль снимает его. Зовётся,
    когда выбор уже записан в настройки. Отдаёт то же, что
    sync_quick_settings."""
    return sync_quick_settings()


def trim_android_memory() -> bool:
    """Освобождает неактивную память Android и кеш — во всех работающих
    окнах. Отдаёт, было ли где."""
    return _in_running_windows([
        ["shell", "am", "kill-all"],
        ["shell", "am", "trim-memory", "0", "COMPLETE"],
    ], timeout=30)


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


def installed_build(package: str, ip: str | None = None) -> tuple[str | None, str]:
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
    safe_package(package)
    try:
        if ip is None:
            adb_connect()
            ip = _require_ip()
        result = instance_adb(ip, ["shell", "dumpsys", "package", package], timeout=120)
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

    # Сумму файла считаем один раз на установку: каталог у каждой установки
    # свой, со случайным именем, так что он и служит ключом. Иначе каждый
    # вопрос «что там стоит» заново читал бы полтораста мегабайт.
    code_path = match.group(1)
    key = (ip, code_path)
    if key not in _build_digests:
        digest = instance_adb(
            ip, ["shell", "sha256sum", f"{code_path}/base.apk"], timeout=300
        )
        text = digest.stdout.strip()
        if not text:
            return None, version
        if len(_build_digests) > 64:
            _build_digests.clear()
        _build_digests[key] = text.split()[0][:12]
    return _build_digests[key], version


# (адрес контейнера, каталог установки) → начало sha256 установленного APK.
_build_digests: dict[tuple[str, str], str] = {}


def uninstall_for_user(package: str, user: int) -> None:
    adb_connect()
    safe_package(package)
    result = _adb(["uninstall", "--user", str(int(user)), package], timeout=180)
    text = (result.stdout + result.stderr).strip()
    _installs_changed()
    if "not installed for" in text:
        # Ответ adb «Failure [not installed for 12]» человеку ничего не скажет.
        raise WaydroidError(tr("в этом профиле приложение не установлено"))
    if result.returncode != 0 or "Success" not in text:
        raise WaydroidError(text or tr("удалить не удалось"))


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


# Сколько ждём первый кадр. Тяжёлая игра под трансляцией ARM после
# перезапуска контейнера поднимается до минуты; полторы — с запасом.
DRAW_SECONDS = 90


def _not_drawing_message(entry: Entry) -> str:
    if entry.needs_native_bridge() and bridge_expired():
        return tr(
            "«{name}» запустилось и зависло: у транслятора ARM истёк срок "
            "действия. Обновите его — кнопка «Подготовить»",
            name=entry.name,
        )
    return tr(
        "«{name}» запустилось, но за {seconds} с не нарисовало ни кадра — "
        "приложение зависает при старте",
        name=entry.name, seconds=DRAW_SECONDS,
    )


def _launch_in_container(entry: Entry, ip: str, user: int, say, immersive: str = "") -> None:
    """Открывает приложение в контейнере и убеждается, что окно появилось.

    Порядок здесь важнее, чем кажется. В однооконном режиме композитор
    Waydroid показывает ровно то приложение, которое названо в
    ``waydroid.active_apps``; если названного на экране нет, он не
    показывает НИЧЕГО, и Android без окна задыхается: `dequeueBuffer
    failed, error = -110`, виснет WindowManager, сторож убивает
    system_server по кругу. Поэтому: поднимаем приложение, ждём первый кадр
    и лишь потом отдаём ему экран. Что бы ни пошло не так — контейнер
    возвращается в покой, а не остаётся с пустым экраном.

    Отказы различаются, потому что лечатся по-разному:
    PictureStuck — встала картинка контейнера, нужен его перезапуск;
    AppNotDrawing — контейнер здоров, не рисует само приложение.
    """
    package = entry.package
    with operating(ip):
        grant_storage_access(package, user, ip)
        if not wait_package_ready(package, 30, ip, user):
            raise WaydroidError(
                tr("Android не видит это приложение — похоже, оно не установилось")
            )
        # Точку входа называет Android; записанное при импорте имя — запасное.
        component = launch_component(package, user, ip) or (
            f"{package}/{entry.activity}" if entry.activity else ""
        )
        if not component:
            raise WaydroidError(tr("в APK не нашлось activity для запуска"))
        if clear_stale_task(package, ip):
            say(tr("Закрываем прежнее окно приложения…"))
        mark = container_clock(ip)
        start_activity(package, component, user, ip)
        if immersive:
            instance_adb(
                ip, ["shell", "settings", "put", "global", "policy_control", immersive],
                timeout=60,
            )

        say(tr("Ждём первый кадр приложения…"))
        if not wait_app_drawing(package, DRAW_SECONDS, ip):
            if screen_state("", ip) == "затор":
                raise PictureStuck(tr("картинка контейнера не отвечает"))
            # Зависшее приложение нередко ещё и молотит все ядра: гасим.
            instance_adb(ip, ["shell", "am", "force-stop", package], timeout=90)
            raise AppNotDrawing(_not_drawing_message(entry))

        set_active_app(ip, package)
        if wait_window(ip, 8, package):
            return
        # Окна нет уже восемь секунд — а композитор открывает его за одну-две.
        # Если при этом молчит SurfaceFlinger, это затор: композитор
        # waydroid-nvidia до версии 0.2.0 иногда намертво встаёт прямо на
        # создании окна (взаимная блокировка с его же потоком Wayland).
        # Ждать дальше бесполезно — раньше на это уходило ещё полминуты.
        if screen_state("", ip) == "затор":
            with suppress(WaydroidError):
                set_active_app(ip, IDLE_SCREEN)
            raise PictureStuck(tr("картинка контейнера не отвечает"))
        if wait_window(ip, 12, package):
            return

        # Окна нет, хотя приложение рисует. Композитор мог пометить его
        # задачу закрытой — тогда нужна новая.
        if composer_refuses_window(ip, mark) is True:
            say(tr("Закрываем прежнее окно приложения…"))
            set_active_app(ip, IDLE_SCREEN)
            instance_adb(ip, ["shell", "am", "force-stop", package], timeout=90)
            time.sleep(2)
            start_activity(package, component, user, ip)
            if wait_app_drawing(package, DRAW_SECONDS, ip):
                set_active_app(ip, package)
                if wait_window(ip, 20, package):
                    return

        with suppress(WaydroidError):
            set_active_app(ip, IDLE_SCREEN)
        if screen_state("", ip) == "затор":
            raise PictureStuck(tr("картинка контейнера не отвечает"))
        raise WaydroidError(tr("приложение работает, но окно на экране не открылось"))


def launch_for_user(entry: Entry, user: int, stage=None) -> None:
    """Открывает приложение в основном контейнере, в нужном профиле.

    Поднимает сессию, если она выключена, ждёт загрузки Android, а если
    картинка контейнера встала — перезапускает его и пробует снова, до двух
    раз. Затор на создании окна — гонка внутри композитора (см.
    _launch_in_container): повтор её обычно проходит, а обнаруживается она
    теперь быстро, так что третья попытка стоит полминуты, а не две.
    """
    def say(text: str) -> None:
        if stage is not None:
            stage(text)

    ensure_session(stage)
    ensure_container_alive(stage)

    immersive = (
        "immersive.full=*"
        if _prop("persist.waydroid.multi_windows") == "true"
        else "immersive.status=*"
    )
    for attempt in (1, 2, 3):
        ip = container_ip()
        if not ip or not wait_system_ready(ip):
            raise ContainerUnreachable(
                tr("Android внутри не отвечает — помогает полный перезапуск контейнера")
            )
        # Перезапуск возвращает контейнер в основной профиль, а приложение
        # живёт в своём: запуск в непереключённом профиле уходит в фон.
        if user and current_android_user() != user:
            say(tr("Переключаем профиль…"))
            switch_android_user(user)
        try:
            _launch_in_container(entry, ip, user, say, immersive)
            return
        except PictureStuck:
            if attempt == 3:
                raise ContainerUnreachable(
                    tr("Waydroid запустил приложение, но окна на экране так и нет")
                ) from None
            say(tr("Картинка Android застряла — возвращаем…"))
            restart_session(stage)
            say(tr("Открываем…"))


def launch_apk(entry: Entry, stage=None) -> None:
    """Открывает приложение в основном контейнере.

    Когда activity известна, идём своим порядком (launch_for_user): сперва
    запуск, потом экран. `waydroid app launch` отдаёт экран приложению до
    первого кадра, и пока оно поднимается, контейнер стоит без окна.
    Запасной путь через саму команду Waydroid остался для APK, из которых
    не удалось достать activity.
    """
    if not entry.package:
        raise WaydroidError(tr("неизвестно имя пакета"))
    if entry.activity or adb_available():
        launch_for_user(entry, 0, stage)
        return

    ensure_session(stage)
    ensure_container_alive(stage)
    last = ""
    for attempt in range(4):
        if attempt:
            time.sleep(6)
        result = _run(["waydroid", "app", "launch", entry.package], timeout=60)
        if result.returncode == 0:
            break
        last = (result.stderr or result.stdout).strip()
    else:
        raise WaydroidError(last or tr("запуск не удался"))
    ip = container_ip()
    if not ip or not wait_window(ip, 60, entry.package):
        with suppress(WaydroidError):
            set_active_app(ip, IDLE_SCREEN)
        raise WaydroidError(tr("приложение работает, но окно на экране не открылось"))


def _tombstones(number: int = 1) -> str:
    """Каталог отчётов о падениях окна — в виде, понятном оболочке хоста.

    Android складывает их в data/tombstones, и каталог принадлежит
    пользователю: прав root не нужно, в отличие от logcat.
    """
    suffix = "" if int(number) == 1 else str(int(number))
    return f'"$HOME"/.local/share/waydroid{suffix}/data/tombstones'


def recent_crash_async(package: str, since: float, callback, number: int = 1) -> None:
    """Ищет отчёт о падении пакета, появившийся после запуска.

    Так пользователь узнаёт, что приложение не «не запустилось», а упало,
    и сразу видит, где именно. ``number`` — окно, в котором запускали.

    Отдаёт ``callback(сводка, отчёт)``: по сводке окно решает, что сказать,
    а отчёт показывает по кнопке «Подробности». Оба пусты, если падения не
    было. Раньше «Подробности» читали последний отчёт вообще — он мог быть
    от другого приложения или другого запуска, а после падения в Java (в
    каталоге отчётов оно следа не оставляет) показывали «записей нет».
    """

    def work():
        safe_package(package)
        # Имя пакета и время уходят в оболочку хоста отдельными параметрами,
        # а не подстановкой в текст команды: имя приходит из APK, и вставленное
        # в строку оно исполнялось бы на хосте — уже вне песочницы.
        result = _run(
            [
                "sh",
                "-c",
                f'for f in $(ls -t {_tombstones(number)}/tombstone_[0-9]* 2>/dev/null '
                "| grep -v '\\.pb$' | head -3); do "
                '  [ "$(stat -c %Y "$f")" -ge "$2" ] || continue; '
                '  grep -qF "Cmdline: $1" "$f" || continue; '
                '  echo "$f"; '
                '  grep -m1 -E "^signal" "$f"; '
                '  grep -m1 -E "#0[0-9].*\\.so" "$f"; '
                '  echo "MERCI_REPORT"; sed -n "1,60p" "$f"; '
                "  break; "
                "done",
                "merci",
                package,
                str(int(since)),
            ],
            timeout=20,
        )
        summary, mark, report = result.stdout.partition("MERCI_REPORT\n")
        summary = summary.strip()
        if summary:
            return summary, (summary.splitlines()[0] + "\n\n" + report.strip()) if mark else summary
        # Падение в Java следа в tombstones не оставляет вовсе — а именно так
        # выглядит, например, отказ загрузить библиотеку без транслятора.
        # Такие лежат в отдельном журнале Android.
        if int(number) == 1:
            ip = container_ip()
        else:
            ip = next(
                (item["ip"] for item in instances() if int(item["number"]) == int(number)), ""
            )
        if not ip or not adb_available():
            return "", ""
        log = instance_adb(
            ip, ["logcat", "-b", "crash", "-d", "-t", f"{int(since)}.000"], timeout=20
        ).stdout
        lines = log.splitlines()
        for index, line in enumerate(lines):
            if f"Process: {package}" in line:
                reason = next(
                    (
                        text.split(": ", 1)[-1].strip()
                        for text in lines[index + 1 : index + 6]
                        if "Exception" in text or "Error" in text
                    ),
                    "",
                )
                report = "\n".join(lines[max(0, index - 1) : index + 40])
                return (f"logcat\n{reason}" if reason else "logcat"), report
        return "", ""

    in_thread(work, lambda result, error: callback(*(result or ("", ""))))


def crash_log_async(callback) -> None:
    """Отчёт о последнем падении приложения — в любом из окон.

    Отдаёт ``callback(путь, текст)``: файл отчёта и его начало. Оба пусты,
    если отчётов нет; при ошибке путь пуст, а в тексте — она сама. Слова
    вокруг («файл», «записей нет») говорит окно, на своём языке: раньше их
    печатал сценарий оболочки, и в английском интерфейсе они оставались
    русскими.
    """

    def work():
        result = _run(
            [
                "sh",
                "-c",
                'f=$(ls -t "$HOME"/.local/share/waydroid*/data/tombstones/tombstone_[0-9]* '
                "2>/dev/null | grep -v '\\.pb$' | head -1); "
                '[ -n "$f" ] || exit 0; '
                'echo "$f"; sed -n "1,40p" "$f"',
            ],
            timeout=30,
        )
        path, _, body = result.stdout.partition("\n")
        return path.strip(), body.rstrip()

    in_thread(
        work,
        lambda result, error: callback(*(result or ("", str(error) if error else ""))),
    )


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
        detail = tr("через Venus") if vulkan == "virtio" else egl or "mesa"
        return tr("аппаратный ({detail})", detail=detail), True

    # У waydroid-nvidia с версии 0.2 есть режимы, где драйвер NVIDIA работает
    # прямо в контейнере: свойства там другие, а рисует всё равно видеокарта.
    mode = nvidia_mode()
    if mode in ("nvidia-prolapse", "glibc-shim") and (gralloc or egl or vulkan):
        return tr("аппаратный ({detail})", detail=f"NVIDIA, {mode}"), True

    if gralloc or egl or vulkan:
        return tr("программный ({detail}) — рисует процессор",
                  detail=egl or "swiftshader"), False

    # Свойства недоступны. Прежде чем сказать «программный» — а это прямая
    # неправда, если видеокарта как раз занята контейнером, — смотрим, жив ли
    # сервер Venus: на пути waydroid-nvidia именно он держит карту.
    if _run(["pgrep", "-f", "virgl_render_server"]).returncode == 0:
        return tr("аппаратный (через Venus)"), True
    if mode in ("nvidia-prolapse", "glibc-shim"):
        return tr("аппаратный ({detail})", detail=f"NVIDIA, {mode}"), True
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

    Нужен загруженный драйвер NVIDIA. Остальное — модель видеокарты, версию
    драйвера, открытые или закрытые модули ядра — проверяет сама настройка
    waydroid-nvidia: она испытывает драйвер на этой машине до того, как
    что-либо поменять, и называет причину отказа. Прежняя проверка здесь
    (только открытые модули, только драйвер 595.71+) была верна для версии
    0.1 и отсекала машины, которые новая версия поддерживает.
    """
    version = _run(
        ["sh", "-c", "cat /proc/driver/nvidia/version 2>/dev/null"]
    ).stdout
    if not version:
        return False, tr("драйвер NVIDIA не найден")
    number = re.search(r"\s(\d+\.\d+(?:\.\d+)?)\s", version)
    kind = tr("открытые модули") if "Open Kernel Module" in version else tr("закрытые модули")
    return True, f"{number.group(1) if number else '?'}, {kind}"


def nvidia_state() -> dict:
    """Что с аппаратным ускорением на этой машине — для пункта меню.

    Раньше пункт спрашивал только, годится ли машина, и предлагал «включить»
    всегда. Человек с уже работающим ускорением соглашался — пакет заново
    собирался и ставился, а настройка ускорения сбрасывала то, что Merci
    держит сама (исправление мерцания). Блокирующая: звать из потока.
    """
    suitable, driver = nvidia_ready()
    cfg = waydroid_cfg()
    version = _nvidia_package_version()
    mode = nvidia_mode(cfg)
    tool = _ok(["sh", "-c", "command -v waydroid-nvidia-setup >/dev/null"])
    # Стоит и настроено: средство на месте (пакет pacman или установка из
    # исходников) и настройка уже записала свой режим в настройки Waydroid.
    installed = bool(mode) and (bool(version) or tool)
    picture, hardware = "", None
    if installed and session_running():
        picture, hardware = renderer()
    return {
        "suitable": suitable,
        "driver": driver,
        "installable": host().family == "arch",
        "installed": installed,
        "version": version,
        "mode": mode,
        "needs_setup": installed and nvidia_setup_needed(),
        "renderer": picture,
        "hardware": hardware,
    }


def nvidia_step(refresh: int = 60) -> Step:
    """Аппаратное ускорение: Vulkan гостя проксируется в драйвер хоста.

    Waydroid рисует через Mesa, а Mesa не умеет проприетарный NVIDIA —
    отсюда swiftshader и рендер на процессоре. waydroid-nvidia даёт гостю
    путь к настоящему драйверу, так что рисует видеокарта.

    Первое включение идёт тем же путём, что обновление пакета (см.
    _nvidia_package_step): сборка из AUR от пользователя, затем один заход
    помощника под root — замена пакета waydroid одной операцией, настройка,
    возврат настроек Merci. Раньше здесь был свой сценарий: три-четыре
    запроса пароля подряд, снятие пакета отдельной командой до установки
    нового и настройка мимо помощника — она сбрасывала исправление мерцания.
    Образ Android и данные лежат вне пакета и не затрагиваются.
    """
    return _nvidia_package_step(
        "waydroid-nvidia-bin",
        key="nvidia",
        title="Аппаратное ускорение NVIDIA",
        hint="waydroid-nvidia: сборка из AUR, установка и настройка одним запросом пароля",
        refresh=refresh,
        minutes=8,
    )


# Сборки Magisk, которые Merci согласна ставить: сумма SHA-256 → размер
# файла. Решает не этот список, а такой же на стороне root (MAGISK_APKS в
# помощнике); здесь он нужен, чтобы отказать сразу — до запроса пароля и до
# остановки Waydroid.
_MAGISK_BUILDS = {
    "a3c37c4172b6ce7fdfbcccb2a608ef84b0a267d7aaef7c183047adceb8494c80": 12714968,
}
# Откуда этот файл брал waydroid_script. В ноябре 2026 репозиторий исчез
# (404), а проверенной копии в сети не нашлось. Адрес оставлен на случай,
# если файл вернётся: что бы по нему ни лежало, без известной суммы оно не
# пройдёт ни здесь, ни в помощнике.
_MAGISK_SOURCES = (
    "https://github.com/mistrmochov/magiskdeltaorig/raw/main/app-release.apk",
)
# Приложение Magisk внутри Android — то, что видно в списке программ.
MAGISK_PACKAGE = "io.github.huskydg.magisk"


def magisk_state() -> dict:
    """Стоит ли root и остались ли от него следы.

    Видно с хоста и без прав: Magisk лежит в оверлее Waydroid, его
    приложение — в данных Android. Пункты меню смотрят сюда, чтобы не
    предлагать поставить уже стоящее и убрать отсутствующее. Блокирующая:
    звать из потока.
    """
    init = "/var/lib/waydroid/overlay/system/etc/init"
    script = (
        f"I={init}; "
        '[ -e "$I/magisk/magisk64" ] && grep -q magisk "$I/bootanim.rc" 2>/dev/null '
        "&& echo installed; "
        '{ [ -e "$I/magisk" ] || [ -e "$I/bootanim.rc.gz" ] '
        "|| [ -d /var/lib/waydroid/overlay/sbin ] "
        f'|| [ -d "$HOME/.local/share/waydroid/data/data/{MAGISK_PACKAGE}" ]; }} '
        "&& echo traces; true"
    )
    found = _run(["sh", "-c", script], timeout=20).stdout.split()
    return {
        "installed": "installed" in found,
        "traces": "traces" in found,
        "file": magisk_file_ready(),
    }


def _magisk_apk() -> str:
    """Где лежит файл Magisk. Там же, куда его клали прежние версии Merci: у
    кого он уже скачан, тому качать нечего — и неоткуда."""
    return os.path.join(_script_cache(), "waydroid-script", "downloads", "magisk.apk")


def _magisk_ready(path: str) -> bool:
    """Лежит ли по этому пути сборка Magisk с известной суммой."""
    try:
        if os.path.getsize(path) not in _MAGISK_BUILDS.values():
            return False
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest() in _MAGISK_BUILDS
    except OSError:
        return False


def magisk_file_ready() -> bool:
    """Есть ли на машине файл Magisk, который Merci согласна ставить, — в
    своём кеше. Окно спрашивает это до вопроса «установить root?», чтобы не
    обещать установку, которой не из чего состояться."""
    return _magisk_ready(_magisk_apk())


def _magisk_fetch(apk: str) -> str:
    """Фрагмент шага: добыть файл Magisk с известной суммой.

    Идёт до запроса пароля и до остановки Waydroid: не нашёлся файл —
    ничего ещё не тронуто. Три источника по порядку: уже скачанное, файл из
    каталога загрузок (его мог принести сам человек), сеть. Ошибку сервера
    (404) не повторяем — повторять нечего; обрыв связи повторяем с докачкой.
    """
    mirror = github_mirror()
    urls = " ".join(
        _quote(mirror.rstrip("/") + "/" + url if mirror else url) for url in _MAGISK_SOURCES
    )
    sums = "|".join(_MAGISK_BUILDS)
    sizes = "|".join(sorted({str(size) for size in _MAGISK_BUILDS.values()}))
    largest = max(_MAGISK_BUILDS.values())
    return (
        f"A={_quote(apk)}; P={_quote(apk + '.part')}; "
        'known() { [ -f "$1" ] || return 1; '
        f'case "$(sha256sum "$1" | cut -d" " -f1)" in {sums}) return 0;; esac; return 1; }}; '
        'mkdir -p "$(dirname "$A")"; '
        'if known "$A"; then echo "файл Magisk уже есть, контрольная сумма сходится"; else '
        "  GOT=0; "
        '  for d in "$HOME/Загрузки" "$HOME/Downloads" "$HOME/загрузки"; do '
        '    for f in "$d"/*.apk; do '
        '      [ -f "$f" ] || continue; '
        f'      case "$(stat -c %s "$f" 2>/dev/null)" in {sizes}) ;; *) continue;; esac; '
        '      if known "$f"; then echo "берём готовый файл: $f"; cp -f "$f" "$A"; '
        "        GOT=1; break 2; fi; "
        "    done; "
        "  done; "
        '  if [ "$GOT" = 0 ]; then '
        '    [ -f "$A" ] && echo "в кеше лежит другая сборка Magisk — её Merci не ставит"; '
        '    command -v curl >/dev/null 2>&1 || echo "качать нечем: на этой системе нет curl"; '
        f"    for url in {urls}; do "
        '      command -v curl >/dev/null 2>&1 || break; '
        '      echo "качаем Magisk Delta: $url"; rm -f "$P"; TRY=0; '
        '      while [ "$TRY" -lt 10 ]; do TRY=$((TRY + 1)); CODE=0; '
        # Больше известного размера не качаем: по освободившемуся адресу
        # может лежать что угодно и сколько угодно. Связь, вставшую на
        # 45 секунд, считаем оборванной — иначе шаг ждал бы её вечно.
        f"        curl -fL --progress-bar --connect-timeout 20 --max-filesize {largest} "
        '--speed-limit 1024 --speed-time 45 -C - -o "$P" "$url" || CODE=$?; '
        '        [ "$CODE" = 0 ] && break; '
        '        if [ "$CODE" = 22 ]; then '
        '          echo "сервер ответил отказом: файла по этому адресу больше нет"; break; fi; '
        '        if [ "$CODE" = 63 ]; then '
        '          echo "по этому адресу лежит другой файл — больше, чем нужный"; break; fi; '
        # Сервер не умеет отдавать с середины — начинаем файл заново.
        '        [ "$CODE" = 33 ] && rm -f "$P"; '
        '        echo "связь оборвалась (curl: $CODE), попытка $TRY из 10"; sleep 3; '
        "      done; "
        '      if known "$P"; then mv -f "$P" "$A"; GOT=1; break; fi; '
        '      [ -s "$P" ] && echo "скачалось не то: сумма не совпала с известной, файл отброшен"; '
        '      rm -f "$P"; '
        "    done; "
        "  fi; "
        '  if [ "$GOT" = 0 ]; then '
        # Эти три строки мастер покажет как причину неудачи (и переведёт):
        # сумма — отдельной строкой, чтобы в переводимый текст не попадала.
        '    echo "Проверенного файла Magisk Delta взять негде: место, откуда он '
        'раздавался, удалено с GitHub, а ставить под root файл без известной '
        'контрольной суммы Merci не станет."; '
        '    echo "Если такой файл у вас сохранился, положите его в «Загрузки» и '
        'повторите. Его сумма SHA-256:"; '
        f'    echo "{next(iter(_MAGISK_BUILDS))}"; '
        "    exit 1; "
        "  fi; "
        "fi; "
    )


def magisk_step(remove: bool = False) -> Step:
    """Root внутри контейнера через Magisk Delta — поставить или убрать.

    Обычная возможность своего же Android: доступ к системным разделам,
    отладка, модули. Аттестацию устройства это не проходит — наоборот,
    делает контейнер для проверок ещё более «неродным», и убрать root потом
    так же важно, как поставить.

    Делает это помощник Merci (magisk-install, magisk-remove). Раньше шаг
    клонировал waydroid_script и исполнял его под root; удаление при этом
    зачем-то качало сам Magisk, а после обновления waydroid-nvidia до 0.2.0
    ещё и кончалось ошибкой, уже убрав root, — и оставляло Waydroid
    выключенным. Теперь, чем бы шаг ни кончился, сессия возвращается.
    """
    if remove:
        return Step(
            key="magisk-remove",
            title="Убрать root (Magisk)",
            hint="файлы Magisk убираются из системы и данных Android, контейнер перезапускается",
            argv=["sh", "-c", _update_script(_helper_command("magisk-remove"))],
            minutes=1,
            asks_root=True,
            then_session=True,
        )
    apk = _magisk_apk()
    script = _update_script(_helper_command("magisk-install", apk), before=_magisk_fetch(apk))
    return Step(
        key="magisk",
        title="Установить root (Magisk)",
        hint="Magisk Delta из файла с известной контрольной суммой, контейнер перезапускается",
        argv=["sh", "-c", script],
        # Обещать «скачает» нечестно: источник удалён, и без готового файла
        # шаг почти наверняка кончится отказом (см. magisk_file_ready).
        downloads="" if _magisk_ready(apk) else tr("Magisk Delta, если его источник ответит"),
        minutes=2,
        asks_root=True,
        then_session=True,
        # Поиск и загрузку файла отменить можно; работу под root — нет.
        prelude=True,
    )


def forget_magisk_app() -> bool:
    """Снимает приложение Magisk, оставшееся в Android после удаления root.

    Без самого Magisk оно бесполезно, а выглядит так, будто root никуда не
    делся. Зовётся после шага удаления, когда контейнер уже поднят; Android
    в эту минуту может ещё грузиться, поэтому службу пакетов ждём.
    Блокирующая: звать из потока. Отдаёт, было ли что снимать.
    """
    if not adb_available():
        return False
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        # instance_adb, а не adb_connect: тот при отказе в доступе зовёт
        # помощника — то есть спросил бы пароль ради уборки, о которой
        # человек не просил отдельно.
        ip = container_ip()
        if not ip or not adb_reachable(ip):
            time.sleep(5)
            continue
        listed = instance_adb(ip, ["shell", "pm", "list", "packages", MAGISK_PACKAGE], timeout=30)
        text = listed.stdout + listed.stderr
        if "unauthorized" in text.lower():
            return False
        if listed.returncode != 0 or "Can't find service" in text:
            time.sleep(5)  # служба пакетов ещё не поднялась
            continue
        if f"package:{MAGISK_PACKAGE}" not in listed.stdout.split():
            return False
        result = instance_adb(ip, ["uninstall", MAGISK_PACKAGE], timeout=120)
        _installs_changed()
        return "Success" in result.stdout + result.stderr
    return False


def container_restart_step() -> Step:
    """Полный перезапуск контейнера — когда перезапуска сессии не хватило.

    Всё под root делает помощник одним заходом (restart-container): гасит
    дополнительные окна, возвращает оверлей, перезапускает службу. Раньше
    шаг звал sudo сам — по разу на каждое окно, на службу и на оверлей, то
    есть несколько запросов пароля подряд, — а отменённый запрос оставлял
    Waydroid выключенным.
    """
    return Step(
        key="container-restart",
        title="Перезапустить контейнер целиком",
        hint="служба waydroid-container и сессия заново",
        argv=["sh", "-c", _update_script(_helper_command("restart-container"))],
        minutes=2,
        asks_root=True,
        then_session=True,
    )


def multiuser_ready() -> tuple[bool, str]:
    """Готов ли контейнер держать несколько пользователей Android."""
    if not adb_available():
        return False, tr("на хосте нет adb (пакет android-tools)")
    # И у выключенного контейнера: предел записан в его настройках, а
    # спрошенный только у работающего, он при выключенном читался как
    # «не разрешено» — и человеку предлагали заново готовить готовое.
    if _setting("fw.max_users") in ("", "0", "1"):
        return False, tr("в контейнере не разрешено больше одного пользователя")
    return True, tr("готово")


def multiuser_step() -> Step:
    """Разрешает контейнеру несколько пользователей Android.

    Три вещи разом, иначе толку нет:

    * fw.max_users — сам предел; по умолчанию Android в этом образе держит
      одного пользователя, и pm create-user отвечает отказом;
    * ключ adb хоста в доверенных контейнера — Merci ходит к pm и am через
      adbd, а без ключа первое подключение упирается в окно подтверждения
      внутри Android. Прежние версии вместо этого выключали проверку ключа
      целиком (ro.adb.secure=0), и adbd открывался всем подряд;
    * сам adb на хосте.

    Свойства пишет помощник — в waydroid_base.prop и в конфиг Waydroid:
    записанное только в первый файл пропадало при обновлении образа, и
    новые профили после этого переставали создаваться. Контейнер гасится и
    поднимается самим шагом (каркас _update_script): раньше шаг рассчитывал
    на перезапуск службы в помощнике, а тот знал только systemd — на OpenRC
    и runit предел молча не вступал в силу.
    """
    system = host_known()
    install = hostinfo.install_script(system, "adb")
    if install is None:
        # Здесь Merci не знает команды (или не вправе: неизменяемые системы).
        need = f'echo {_quote(hostinfo.install_hint(system, "adb"))}; exit 1; '
    else:
        need = (
            'echo "ставим adb"; '
            f"{_sudo(askpass_helper())}sh -c {_quote(install)}; "
        )
    before = (
        'if command -v adb >/dev/null; then echo "adb уже есть"; else '
        + need
        + "fi; "
        "adb start-server >/dev/null 2>&1 || true; "
    )
    script = _update_script(
        'echo "разрешаем нескольких пользователей Android"; '
        + _helper_command("secure-adb", "--users", "4"),
        before=before,
    )
    return Step(
        key="multiuser",
        title="Разрешить несколько пользователей Android",
        hint="fw.max_users, доступ к adb контейнера и перезапуск",
        argv=["sh", "-c", script],
        minutes=3,
        asks_root=True,
        then_session=True,
        # Установку adb отменить можно; дальше — нет.
        prelude=True,
    )


def switch_bridge_step(target: str) -> Step:
    """Шаг смены транслятора ARM: libndk ↔ libhoudini.

    Они по-разному переживают код, который приложение генерирует на лету:
    там, где один падает, второй иногда работает.

    Один шаг на всё: архив добывается до запроса пароля, ставит его
    помощник, Waydroid возвращается при любом исходе. Новый транслятор
    ложится поверх прежнего, как это всегда и делалось: общие каталоги
    ARM-библиотек переписываются, а включает его свойство native.bridge.
    """
    if target not in _ARCHIVES:
        raise WaydroidError(tr("неизвестный транслятор"))
    step = _bridge_helper_step(
        tr("Переключить транслятор на {target}", target=target),
        "из архива с известной контрольной суммой, контейнер перезапускается",
        target,
        fetch=True,
    )
    step.key = "switch-bridge"
    return step


def stop_session_async(callback) -> None:
    """Выключает контейнер.

    Одной команды мало: `waydroid session stop` регулярно упирается в
    таймаут D-Bus и оставляет процесс менеджера сессии живым, после чего
    состояние расходится — status показывает RUNNING, а prop отвечает, что
    сессия остановлена. Поэтому после команды добиваем остатки и
    проверяем результат.
    """

    def work():
        problems = []
        # Дополнительные окна — отдельные контейнеры, и команда Waydroid о
        # них не знает: после «выключить» они продолжали работать и держать
        # по гигабайту памяти каждое. Гасим их первыми, одним заходом.
        clones = [
            item["number"]
            for item in instances()
            if item["number"] != 1 and item["state"] == "RUNNING"
        ]
        if clones:
            try:
                stop_instances(clones)
            except WaydroidError as failure:
                problems.append(
                    tr("дополнительные окна остались включены: {error}", error=failure)
                )
        main = container_ip()
        _run(["waydroid", "session", "stop"], timeout=40)
        _run(["sh", "-c", _KILL_SESSION], timeout=15)
        adb_forget(main)
        time.sleep(3)
        forget_state()
        ready, detail = status(use_cache=False)
        if ready:
            problems.append(
                tr("контейнер всё ещё работает: {detail}", detail=tr(detail))
            )
        return "\n".join(problems)

    def done(result, error):
        if error is not None or result is None:
            callback(tr("не удалось выключить"))
            return
        callback(result)

    in_thread(work, done)


def show_full_ui() -> None:
    try:
        Gio.Subprocess.new(host_argv(["waydroid", "show-full-ui"]), Gio.SubprocessFlags.NONE)
    except GLib.Error as exc:
        raise WaydroidError(exc.message) from exc


# -- обновления -----------------------------------------------------------
#
# Что устарело, выясняет модуль updates (он ничего не меняет). Здесь — чем
# это ставится: шаги мастера, каждый с одним запросом пароля, и то, без
# чего обновление ломало бы Merci, — погасить окна до смены образа,
# сохранить прежний, вернуть свои настройки после.

WAYDROID_CFG = "/var/lib/waydroid/waydroid.cfg"
# Встроенный обновлятор Android. Он ставит новый образ сам, по нажатию на
# своё уведомление: останавливает основной контейнер и переписывает файл
# образа — не зная о дополнительных окнах, которые держат этот файл
# подключённым. Поэтому, когда обновлениями занимается Merci, его прячем.
UPDATER_PACKAGE = "org.lineageos.waydroidupdater"


def waydroid_cfg() -> dict[str, dict[str, str]]:
    """waydroid.cfg с хоста: {раздел: {ключ: значение}}. Из потока."""
    return updates.parse_cfg(_host_file(WAYDROID_CFG))


def nvidia_mode(cfg: dict | None = None) -> str:
    """Чем рисует waydroid-nvidia (см. updates.nvidia_mode). Без ``cfg`` —
    читает настройки с хоста: тогда только из потока."""
    return updates.nvidia_mode(waydroid_cfg() if cfg is None else cfg)


def _nvidia_package_version() -> str:
    """Версия установленного пакета waydroid-nvidia; пусто — такого пакета
    нет или менеджер пакетов здесь другой."""
    out = _run(
        ["sh", "-c", "LC_ALL=C pacman -Q waydroid-nvidia-bin waydroid-nvidia "
                     "waydroid-nvidia-git 2>/dev/null"],
        timeout=20,
    ).stdout
    for line in out.splitlines():
        words = line.split()
        if len(words) == 2 and words[0].startswith("waydroid-nvidia"):
            return words[1]
    return ""


def remember_nvidia_setup() -> None:
    """Запоминает, для какой версии пакета ускорение настроено и работает.

    Зовётся после удачной настройки, установки или обновления — и заодно
    убирает прежний журнал запуска сессии: строка об отказе, оставшаяся в
    нём от неудачи до настройки, заставляла бы предлагать её снова.
    """
    from .settings import Settings

    version = _nvidia_package_version()
    if version:
        Settings().set("nvidia_configured_for", version)
    with suppress(OSError):
        with open(_session_log(), "w", encoding="utf-8"):
            pass


def _session_newer_than_package() -> bool:
    """Поднята ли сессия уже после установки нынешнего пакета waydroid-nvidia.

    Работающая сессия сама по себе ничего не доказывает: пакет могли
    обновить под ней (pacman -Syu без перезагрузки), и она ещё дорабатывает
    на прежнем. Тогда после перезагрузки контейнер не стартует, пока
    настройку не повторят, — а Merci, запомнив новую версию как «настроено»,
    этого бы уже не заметила.
    """
    script = (
        # Время установки пакета — по записи в базе pacman.
        'P=$(ls -d /var/lib/pacman/local/waydroid-nvidia*-[0-9]* 2>/dev/null | head -1); '
        '[ -n "$P" ] || exit 1; '
        "S=$(pgrep -o -f '^[^ ]*python[^ ]* [^ ]*waydroid[^ ]* session start'); "
        '[ -n "$S" ] || exit 1; '
        'AGE=$(ps -o etimes= -p "$S" | tr -d " "); '
        '[ -n "$AGE" ] || exit 1; '
        '[ $(( $(date +%s) - AGE )) -gt "$(stat -c %Y "$P")" ]'
    )
    return _ok(["sh", "-c", script])


def nvidia_setup_needed() -> bool:
    """Ждёт ли waydroid-nvidia повторной настройки.

    После обновления его пакета или драйвера NVIDIA контейнер отказывается
    стартовать, пока настройку не запустят снова. Узнаём это двумя путями:
    по своей памяти — версия пакета сменилась с тех пор, как ускорение
    работало (пакет обновили мимо Merci, либо настройка после обновления
    упала), — и по сообщению самого контейнера: отказываясь стартовать, он
    называет это средство в состоянии службы и в журнале сессии.
    """
    if not _ok(["sh", "-c", "command -v waydroid-nvidia-setup >/dev/null"]):
        return False
    from .settings import Settings

    recorded = str(Settings().get("nvidia_configured_for", "") or "")
    version = _nvidia_package_version()
    if recorded and version and recorded != version:
        return True
    text = _run(
        ["sh", "-c",
         "LC_ALL=C systemctl status waydroid-container.service --no-pager -n 40 2>&1; "
         f"tail -n 40 {_quote(_session_log())} 2>/dev/null"],
        timeout=20,
    ).stdout
    return "waydroid-nvidia-setup" in text


def _kept_settings(cfg: dict | None = None) -> list[str]:
    """Настройки Merci, которые помощник вернёт на место после обновления.

    Называем их явно, по своей памяти, а не «как было в файле перед
    обновлением»: файл к этому моменту уже мог быть испорчен прошлой
    неудачной попыткой.
    """
    from .settings import Settings

    settings = Settings()
    args: list[str] = []
    width, height = settings.window_size
    if width and height:
        args += ["--width", str(width), "--height", str(height)]
    properties = (waydroid_cfg() if cfg is None else cfg).get("properties", {})
    if properties.get("persist.waydroid.use_subsurface") == "false":
        settings.set("subsurface_off", True)
    if settings.get("subsurface_off", False):
        # Исправление мерцания: настройка waydroid-nvidia включает
        # подповерхности заново при каждом запуске.
        args += ["--subsurface", "false"]
    if settings.multiuser:
        # Предел пользователей Android: без него профили есть, а новые не
        # создаются. Обновление образа стирало его из базовых свойств.
        args += ["--max-users", "4"]
    return args


def _update_script(body: str, mode: str = "", fresh_venus: bool = False,
                   before: str = "") -> str:
    """Сценарий шага обновления вокруг работы помощника.

    ``before`` — то, что делается до остановки Waydroid (сборка пакета):
    сорвись оно, ничего ещё не тронуто. ``body`` — работа под root.

    Чем бы ни кончилось, сценарий доводит машину до понятного состояния:
    если обновление прошло или если Waydroid работал до него, сессия
    поднимается снова. Раньше отмена окна пароля оставляла Waydroid
    выключенным: сессия гасилась до запроса, а поднималась только после
    удачи.

    С остановки Waydroid и до конца работы под root сценарий не слушает
    просьб завершиться. Оболочка, которую просят выйти, пока она ждёт
    помощника, выходит сразу — с кодом «всё хорошо» — и тут же поднимала бы
    сессию рядом с ещё работающим помощником: тот как раз переписывает то,
    что сессия подключает. Теперь она дожидается его и решает по настоящему
    коду возврата, а мастеру об этом сообщает метка NO_CANCEL_MARK — он
    прячет «Отмену». До остановки (пока идёт ``before``) прервать можно как
    обычно: там ещё ничего не тронуто (см. Step.prelude).
    """
    venus = ""
    if mode in ("venus", "auto"):
        # Сервер рисования Venus — служба пользователя; без неё сессия в
        # этом режиме не стартует. После обновления пакета её надо именно
        # перезапустить: `enable --now` работающую службу не трогает, и
        # новый гость говорил бы со старым сервером на хосте.
        action = "restart" if fresh_venus else "enable --now"
        venus = (
            "systemctl --user daemon-reload >/dev/null 2>&1; "
            # При первом включении службу надо ещё и поставить на автозапуск.
            + ("systemctl --user enable wd-venus.service >/dev/null 2>&1; " if mode == "auto" else "")
            + f"systemctl --user {action} wd-venus.service >/dev/null 2>&1; "
            f"for i in 1 2 3 4 5 6 7 8 9 10; do [ -S {_VENUS_SOCKET} ] && break; sleep 1; done; "
        )
        if mode == "auto":
            # Режим выбирает сама настройка, и узнаём мы его уже после неё —
            # из настроек Waydroid, куда она его записала.
            venus = (
                f"if grep -qE '^nvidia_driver_mode *= *venus' {WAYDROID_CFG} 2>/dev/null; "
                f"then {venus}fi; "
            )
    return (
        "set -e; "
        + before
        + 'WAS=0; waydroid status 2>/dev/null | grep -q "^Session:.*RUNNING" && WAS=1; '
        # Сигналы возвращаем до старта сессии: иначе она унаследовала бы
        # «не слушать TERM» и её нельзя было бы остановить по-хорошему.
        # PIPE остаётся: вывод шага читает Merci, и закрой её человек — запись
        # в оборванный канал убила бы сценарий, не дав поднять сессию.
        "finish() { CODE=$?; set +e; trap - EXIT TERM INT HUP; "
        'if [ "$CODE" = 0 ] || [ "$WAS" = 1 ]; then '
        + venus
        + 'echo "поднимаем Waydroid"; sleep 2; '
        f"{_session_snippet()}; sleep 3; "
        "fi; " + _CODE_TO_FILE + "exit $CODE; }; "
        # Сначала перестаём слушать сигналы, потом ставим ловушку выхода:
        # в обратном порядке между ними оставался бы миг, когда сигнал
        # приводил к выходу «с успехом».
        + _NO_CANCEL
        + "trap finish EXIT; "
        'echo "останавливаем Waydroid"; '
        "waydroid session stop >/dev/null 2>&1 || true; "
        f"{_KILL_SESSION}; "
        "sleep 2; "
        + body
    )


def images_update_step(update: updates.Update) -> Step:
    """Новый образ Android — штатным `waydroid upgrade`, но бережно.

    Сам по себе он не знает ни о дополнительных окнах (они держат прежний
    образ подключённым — перезапись файла под ними портит им файловую
    систему), ни о том, что прежний образ стоит сохранить. Это делает
    помощник, одним заходом под root: см. cmd_update_images.
    """
    megabytes = int(update.data.get("download_mb", 0) or 0)
    script = _update_script(
        f"{_helper_command('update-images', '--download-mb', str(megabytes), *_kept_settings())}"
    )
    return Step(
        key="update-images",
        title="Обновить образ Android",
        hint="окна выключаются, прежний образ сохраняется, настройки возвращаются",
        argv=["sh", "-c", script],
        downloads=tr("образ Android (~{size} МБ)", size=megabytes) if megabytes else "",
        minutes=12,
        asks_root=True,
        then_session=True,
    )


def images_restore_step() -> Step:
    """Прежний образ Android — из копии, сделанной перед обновлением."""
    script = _update_script(f"{_helper_command('restore-images', *_kept_settings())}")
    return Step(
        key="restore-images",
        title="Вернуть прежний образ Android",
        hint="из копии, сделанной перед обновлением; приложения и данные остаются",
        argv=["sh", "-c", script],
        minutes=3,
        asks_root=True,
        then_session=True,
    )


def drop_image_backup() -> None:
    """Удаляет копию прежнего образа. Из потока; спросит пароль."""
    _instance_call("drop-image-backup", timeout=180)


def _nvidia_arguments(cfg: dict) -> tuple[list[str], str]:
    """Аргументы настройки ускорения, сохраняющие то, что выбрано сейчас.

    Режим называем явно: без него новая версия настройки выбрала бы свой
    режим по умолчанию — то есть обновление молча сменило бы способ
    рисования. Частоту передаём, только если её когда-то задавали.
    """
    mode = nvidia_mode(cfg)
    args = ["--mode", mode] if mode else []
    refresh = cfg.get("properties", {}).get("persist.waydroid.refresh_rate", "")
    if refresh.isdigit() and 1 <= int(refresh) <= 1000:
        args += ["--refresh", refresh]
    return args, mode


def nvidia_setup_step() -> Step:
    """Повторная настройка waydroid-nvidia — после обновления драйвера или
    пакета, когда контейнер отказывается стартовать без неё."""
    cfg = waydroid_cfg()
    arguments, mode = _nvidia_arguments(cfg)
    script = _update_script(
        f"{_helper_command('nvidia-setup', *arguments, *_kept_settings(cfg))}",
        mode=mode, fresh_venus=True,
    )
    return Step(
        key="nvidia-setup",
        title="Перенастроить ускорение NVIDIA",
        hint="после обновления драйвера или пакета контейнер без этого не стартует",
        argv=["sh", "-c", script],
        minutes=4,
        asks_root=True,
        then_session=True,
        after=remember_nvidia_setup,
    )


def nvidia_update_step(update: updates.Update) -> Step:
    """Новая версия waydroid-nvidia: собрать из AUR, поставить, настроить."""
    name = str(update.data.get("name", ""))
    if not re.fullmatch(r"waydroid-nvidia[a-z-]{0,12}", name):
        raise WaydroidError(tr("неизвестный пакет"))
    return _nvidia_package_step(
        name,
        key="update-package",
        title="Обновить waydroid-nvidia",
        hint="сборка из AUR, установка и настройка ускорения в прежнем режиме",
    )


def _nvidia_package_step(name: str, *, key: str, title: str, hint: str,
                         refresh: int = 0, minutes: int = 10) -> Step:
    """Собрать waydroid-nvidia из AUR, поставить и настроить.

    Сборка идёт от пользователя и ничего не меняет в системе; всё, что
    меняет, — установка пакета, настройка ускорения, возврат настроек Merci
    — делает помощник одним заходом под root (cmd_nvidia_update).

    ``refresh`` — частота обновления для первой настройки: потом она
    берётся из настроек контейнера, куда её записала сама настройка.
    """
    workdir = os.path.join(_data_root(), name)
    package = os.path.join(workdir, "merci-update.pkg.tar.zst")
    helper = askpass_helper()
    askpass_env = f"env SUDO_ASKPASS={_quote(helper)} " if helper else ""
    cfg = waydroid_cfg()
    arguments, mode = _nvidia_arguments(cfg)
    if refresh and "--refresh" not in arguments:
        arguments += ["--refresh", str(refresh)]
    build = (
        f"D={_quote(workdir)}; "
        'echo "берём описание пакета из AUR"; '
        # Каталог наш, чужих правок в нём нет: приводим его ровно к тому, что
        # лежит в AUR. Простой pull молча оставлял прежнее описание, и «обновление»
        # заново ставило ту же версию.
        'if [ -d "$D/.git" ]; then '
        '  git -C "$D" fetch --depth 1 origin && git -C "$D" reset -q --hard FETCH_HEAD '
        '    || { echo "AUR не ответил"; exit 1; }; '
        "else "
        '  rm -rf "$D"; '
        "  for try in 1 2 3 4 5; do "
        f'    git clone --depth 1 https://aur.archlinux.org/{name}.git "$D" && break; '
        '    rm -rf "$D"; sleep 5; '
        "  done; "
        "fi; "
        '[ -f "$D/PKGBUILD" ] || { echo "не удалось получить PKGBUILD из AUR"; exit 1; }; '
        'cd "$D"; '
        f"{_AUR_PREFETCH}; "
        'PKG=$(makepkg --packagelist | head -1); '
        'if [ -f "$PKG" ]; then echo "пакет уже собран"; else '
        '  echo "собираем пакет"; '
        f"  {askpass_env}makepkg -s --noconfirm --needed; "
        "fi; "
        '[ -f "$PKG" ] || { echo "пакет не собрался"; exit 1; }; '
        f'cp -f "$PKG" {_quote(package)}; '
        # Сумму снимаем сейчас, до запроса пароля: помощник сверит её с тем,
        # что ему дали поставить.
        f'SUM=$(sha256sum {_quote(package)} | cut -d" " -f1); '
    )
    command = _helper_command(
        "nvidia-update", "--package", package, "--sha256", _Raw('"$1"'),
        *arguments, *_kept_settings(cfg), tail='"$SUM"',
    )
    script = _update_script(
        f"{command}; "
        f"rm -f {_quote(package)}; "
        # Исходники и собранный пакет этой версии больше не нужны: каждая
        # занимает сотни мегабайт, и без уборки они копились бы вечно.
        'git -C "$D" clean -fdxq >/dev/null 2>&1 || true',
        # Режим ещё не выбран (первое включение) — какой выберет настройка,
        # станет известно только после неё.
        mode=mode or "auto", fresh_venus=True, before=build,
    )
    return Step(
        key=key,
        title=title,
        hint=hint,
        argv=["sh", "-c", script],
        downloads=tr("{name} из AUR", name=name),
        minutes=minutes,
        asks_root=True,
        then_session=True,
        # Сборку отменить можно; замену пакета и настройку — уже нет.
        prelude=True,
        after=remember_nvidia_setup,
    )


# makepkg качает исходники одним запросом и умирает на первом обрыве. Поэтому
# http-источники кладём сами, curl-ом с докачкой и повторами: готовый файл с
# верной суммой makepkg просто использует.
_AUR_PREFETCH = (
    'echo "докачиваем исходники"; '
    "fetch_sources() { "
    "makepkg --printsrcinfo | awk '$1 == \"source\" {print $3}' "
    "| while read -r src; do "
    '  name="${src%%::*}"; url="${src#*::}"; '
    '  case "$src" in *::*) ;; *) url="$src"; name="${src##*/}";; esac; '
    '  case "$url" in http*) ;; *) continue;; esac; '
    '  [ "$1" = again ] && rm -f "$name"; '
    '  [ -s "$name" ] && continue; '
    '  echo "качаем $name"; TRY=0; '
    # Повторяем обрывы связи, но не отказ сервера: `--retry-all-errors`
    # тридцать раз подряд спрашивал бы адрес, по которому файла уже нет.
    '  while [ "$TRY" -lt 30 ]; do TRY=$((TRY + 1)); CODE=0; '
    '    curl -fL --progress-bar --connect-timeout 20 -C - -o "$name" "$url" </dev/null '
    "|| CODE=$?; "
    '    [ "$CODE" = 0 ] && break; '
    '    if [ "$CODE" = 22 ]; then echo "сервер ответил отказом: $url"; exit 1; fi; '
    # Сервер не умеет отдавать с середины — начинаем файл заново.
    '    [ "$CODE" = 33 ] && rm -f "$name"; '
    "    sleep 3; "
    "  done; "
    '  [ "$CODE" = 0 ] || exit 1; '
    "done; }; "
    "fetch_sources first; "
    # Файл, оставшийся от оборванной загрузки, выглядит как готовый — и
    # сборка падала на его контрольной сумме при каждой следующей попытке.
    # Суммы сверяет сам makepkg; не сошлись — качаем всё заново.
    "if ! makepkg --verifysource --noconfirm >/dev/null 2>&1; then "
    '  echo "исходники с прошлого раза повреждены — качаем заново"; '
    "  fetch_sources again; "
    '  makepkg --verifysource --noconfirm >/dev/null 2>&1 '
    '    || { echo "исходники не прошли проверку сумм"; exit 1; }; '
    "fi"
)


def package_upgrade_step(update: updates.Update) -> Step:
    """Обновление пакета waydroid менеджером пакетов системы.

    Сразу за ним — пересборка настроек контейнера новой версией и возврат
    настроек Merci; всё под одним запросом пароля.
    """
    command = {
        "apt": "export DEBIAN_FRONTEND=noninteractive; "
               "apt-get install -y --only-upgrade -o DPkg::Lock::Timeout=300 waydroid",
        "dnf": "dnf upgrade -y waydroid",
    }.get(str(update.data.get("source", "")))
    if command is None:
        raise WaydroidError(tr("для этой системы Merci не знает, как обновить пакет"))
    root = "set -e; " + command + "; " + _helper_inner("refresh-config", *_kept_settings())
    script = _update_script(f"{_sudo(askpass_helper())}sh -c {_quote(root)}")
    return Step(
        key="update-package",
        title="Обновить пакет Waydroid",
        hint="менеджером пакетов системы; затем настройки контейнера пересобираются",
        argv=["sh", "-c", script],
        minutes=5,
        asks_root=True,
        then_session=True,
    )


def bridge_update() -> updates.Update | None:
    """Обновление транслятора ARM — если у стоящей сборки вышел срок."""
    if not bridge_expired():
        return None
    return updates.Update(
        key="bridge",
        title=tr("Транслятор ARM"),
        current=tr("libhoudini, срок действия сборки истёк"),
        available=tr("свежая сборка libhoudini"),
        detail=tr("У прежней сборки истёк срок: ARM-приложения зависают при "
                  "запуске. Контейнер перезапустится."),
        root=True,
    )


def update_steps(update: updates.Update) -> list[Step]:
    """Шаги мастера, которыми ставится это обновление. Из потока: шаги
    читают настройки контейнера с хоста."""
    if update.key == "images":
        return [images_update_step(update)]
    if update.key == "bridge":
        return [
            _ndk_step("libhoudini"),
            _bridge_helper_step(
                "Обновить транслятор ARM",
                "у прежней сборки libhoudini истёк срок: приложения зависают при запуске",
            ),
        ]
    if update.key == "package":
        if update.data.get("source") == "aur" and update.data.get("nvidia"):
            return [nvidia_update_step(update)]
        return [package_upgrade_step(update)]
    raise WaydroidError(tr("это обновление ставится не мастером"))


def check_updates(version: str) -> updates.Report:
    """Что на машине устарело. Блокирующая: звать из потока."""

    def ask(argv: list, timeout: int) -> tuple[int, str]:
        result = _run(argv, timeout=timeout)
        return result.returncode, result.stdout

    bridge = None
    with suppress(WaydroidError):
        bridge = bridge_update()
    # Сессия работает и поднята уже после установки нынешнего пакета —
    # значит, ускорение настроено под него: запоминаем версию, чтобы
    # заметить, когда пакет сменится.
    with suppress(Exception):
        if session_running() and _session_newer_than_package():
            remember_nvidia_setup()
    return updates.check(ask, host(), version, bridge=bridge)


def update_merci(update: updates.Update) -> None:
    """Обновляет саму Merci как флатпак. Из потока.

    Команду к Merci не привязываем: выйди человек из программы посреди
    обновления, флатпак остался бы установленным наполовину.
    """
    scope = "--system" if update.data.get("scope") == "--system" else "--user"
    result = _run_detached(
        ["flatpak", "update", "-y", "--noninteractive", scope, updates.APP_ID], timeout=900
    )
    if result.returncode != 0:
        raise WaydroidError(
            (result.stderr or result.stdout).strip()[-400:] or tr("flatpak не обновил Merci")
        )


def system_snapshot() -> dict:
    """Что работает сейчас — чтобы после обновления сверить, не пропало ли."""
    snapshot = {"bridge": "", "hardware": False, "images": image_stamps()}
    with suppress(Exception):
        snapshot["bridge"] = native_bridge()
        snapshot["hardware"] = bool(session_running() and renderer()[1])
    return snapshot


def image_stamps() -> tuple[str, str]:
    """Отметки о сборках установленного образа: (система, драйверы)."""
    section = waydroid_cfg().get("waydroid", {})
    return section.get("system_datetime", ""), section.get("vendor_datetime", "")


def images_changed(before: dict | None) -> bool:
    """Сменился ли образ Android с момента снимка system_snapshot.

    От этого зависит, что предлагать после неудачи: если образ прежний —
    возвращать нечего, обновление просто не состоялось.
    """
    stamps = (before or {}).get("images")
    return bool(stamps) and tuple(stamps) != image_stamps()


def verify_after_update(before: dict | None = None) -> list[str]:
    """Проверяет контейнер после обновления: то, от чего зависит Merci.

    Отдаёт список того, что не в порядке (пусто — всё на месте). Сверяем
    не «обновление прошло», а «после него всё работает»: сессия поднялась,
    Android отвечает, картинка жива, транслятор и ускорение не пропали.
    """
    before = before or {}
    forget_state()
    for _ in range(45):
        if session_running():
            break
        time.sleep(2)
    else:
        return [tr("сессия Waydroid не поднялась") + _session_failure()]
    ip = wait_for_ip(90)
    if not ip:
        return [tr("контейнер не получил адрес в сети")]
    with suppress(WaydroidError):
        adb_connect()
    if not wait_system_ready(ip, 150):
        return [tr("Android внутри не отвечает")]
    problems = []
    if screen_state(ip=ip) == "затор":
        problems.append(tr("картинка Android не отвечает"))
    forget_state()
    if before.get("bridge") and not native_bridge():
        problems.append(tr("пропал транслятор ARM"))
    if before.get("hardware") and not renderer()[1]:
        problems.append(tr("контейнер рисует процессором: ускорение не включилось"))
    if not problems:
        with suppress(Exception):
            remember_nvidia_setup()
        with suppress(Exception):
            from .settings import Settings

            if Settings().hide_android_updater:
                hide_android_updater()
    return problems


# Где обновлятор уже спрятан: (адрес, номер процесса контейнера). Номер
# процесса нужен, чтобы не принять за прежнее окно, удалённое и созданное
# заново под тем же адресом: Android в нём уже другой.
_updater_hidden: set[tuple[str, int]] = set()
# Скрытие идёт после каждой операции, а возврат — по щелчку переключателя,
# каждый в своём потоке. Без замка скрытие, начатое до щелчка, заканчивалось
# после возврата и прятало только что возвращённое — уже насовсем: Merci
# считала дело сделанным.
_updater_lock = threading.RLock()


def hide_android_updater(only: str = "") -> int:
    """Прячет встроенный обновлятор Android в работающих контейнерах.

    Отдаёт, у скольких пользователей Android он был включён и выключен.
    Его уведомления («System update available») — по одному на каждого
    пользователя Android после каждой загрузки; а установка по нажатию на
    них перезаписывает образ под работающими окнами. Отключение обратимо
    (show_android_updater) и хранится в данных контейнера.

    Прячет, только пока так велят настройки: спрашивает их заново, уже под
    замком, — человек мог передумать, пока очередь доходила до этого вызова.
    """
    from .settings import Settings

    script = (
        'for u in $(pm list users | grep -o "UserInfo{[0-9]*" | grep -o "[0-9]*"); do '
        f"  pm list packages -e --user $u {UPDATER_PACKAGE} 2>/dev/null | grep -q {UPDATER_PACKAGE} "
        f"    && pm disable-user --user $u {UPDATER_PACKAGE} >/dev/null 2>&1 && echo HID; "
        # Код цикла — код последней проверки, а она «не удаётся» как раз там,
        # где обновлятор уже выключен: окно, в котором всё в порядке,
        # выглядело отказавшим и переспрашивалось после каждой операции.
        "done; true"
    )
    with _updater_lock:
        if not Settings().hide_android_updater:
            return 0
        hidden = 0
        boots: dict[str, int] | None = None
        live: set[tuple[str, int]] = set()
        for item in instances():
            ip = item.get("ip") or ""
            if item.get("state") != "RUNNING" or not ip:
                continue
            if boots is None:
                boots = _container_pids()
            key = (ip, boots.get(container_name(int(item["number"])), 0))
            live.add(key)
            if key in _updater_hidden or (only and ip != only):
                continue
            result = instance_adb(ip, ["shell", script], timeout=40)
            if result.returncode == 0:
                _updater_hidden.add(key)
                hidden += result.stdout.count("HID")
        _updater_hidden.intersection_update(live)
        return hidden


def show_android_updater(everywhere: bool = True) -> tuple[bool, bool]:
    """Возвращает встроенный обновлятор Android — если человек так решил.

    Отдаёт пару: вернулся ли он хоть в одном окне сейчас — и остались ли
    окна, где он вернётся позже. Вернуть можно только в работающем окне;
    раньше выключенные так и оставались без обновлятора, а Merci сообщала
    «возвращён». Теперь такие окна запоминаются (``updater_restore``) и
    получают его при первом же запуске. ``everywhere=False`` — именно этот
    второй заход: только по запомненным окнам.
    """
    from .settings import Settings

    script = (
        'for u in $(pm list users | grep -o "UserInfo{[0-9]*" | grep -o "[0-9]*"); do '
        f"  pm enable --user $u {UPDATER_PACKAGE} >/dev/null 2>&1; "
        "done; true"
    )
    with _updater_lock:
        settings = Settings()
        if settings.hide_android_updater:
            # Пока дошла очередь, скрывать велели снова — возвращать нечего.
            if settings.get("updater_restore"):
                settings.set("updater_restore", [])
            return False, False
        _updater_hidden.clear()
        items = instances()
        existing = {int(item["number"]) for item in items}
        saved = _numbers(settings.get("updater_restore"))
        # Спрятан он мог быть в любом окне: пока настройка была включена,
        # Merci прятала его в каждом, что поднималось.
        pending = set(existing) if everywhere else saved & existing
        reached = False
        for item in items:
            number, ip = int(item["number"]), item.get("ip") or ""
            if number not in pending or item.get("state") != "RUNNING" or not ip:
                continue
            if instance_adb(ip, ["shell", script], timeout=40).returncode == 0:
                pending.discard(number)
                reached = True
        if pending != saved:
            settings.set("updater_restore", sorted(pending))
        return reached, bool(pending)


def apply_updater_choice(hide: bool) -> bool:
    """Применяет выбор «скрывать встроенный обновлятор» к работающим окнам.

    Зовётся, когда выбор уже записан в настройки (``hide`` — что в них
    записано). Отдаёт, дошло ли хоть до одного окна; до остальных дойдёт
    при их запуске (sync_android_settings). Блокирующая: звать из потока.
    """
    from .settings import Settings

    with _updater_lock:
        settings = Settings()
        if not settings.hide_android_updater:
            return show_android_updater()[0]
        if settings.get("updater_restore"):
            settings.set("updater_restore", [])
        hide_android_updater()
        return bool(_updater_hidden)


def sync_android_settings() -> None:
    """Доводит до поднявшихся окон то, что Merci держит внутри Android:
    спрятанный (или возвращаемый) обновлятор, режим экономии и предел
    кадров. Зовётся после всякого запуска контейнера. Блокирующая."""
    from .settings import Settings

    with suppress(WaydroidError), _updater_lock:
        settings = Settings()
        if settings.hide_android_updater:
            if settings.get("updater_restore"):
                # Скрывать включили снова — возвращать уже нечего.
                settings.set("updater_restore", [])
            hide_android_updater()
        elif settings.get("updater_restore"):
            show_android_updater(everywhere=False)
    with suppress(WaydroidError):
        sync_quick_settings()
