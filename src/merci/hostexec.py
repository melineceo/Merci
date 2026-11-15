"""Обращения к хосту без подвисаний интерфейса.

Любой вызов waydroid идёт через портал flatpak-spawn и стоит от десятков
миллисекунд до секунд. Раньше это делалось прямо в обработчиках сигналов, и
окно замирало на каждом щелчке по приложению. Здесь всё уходит в отдельный
поток, а результат возвращается в главный цикл через GLib.idle_add.
"""

from __future__ import annotations

import shutil
import subprocess
import threading
import time

from gi.repository import GLib


# Код возврата «команда не уложилась в срок» — тот же, что у timeout из
# coreutils. Отдельный от 127 («команду не удалось запустить») намеренно:
# «не ответила» и «не существует» требуют разных решений, а раньше оба
# случая выглядели одинаково, и молчание занятого контейнера читалось как
# «контейнер остановлен».
TIMED_OUT = 124


def host_argv(argv: list[str], watch: bool = False) -> list[str]:
    """Команда для хоста. Вне флатпака — как есть.

    ``watch`` привязывает команду к посреднику: стоит flatpak-spawn
    исчезнуть, портал гасит и её. Без этого таймаут убивал только
    посредника, а сама команда оставалась на хосте — зависшие `adb shell`
    и `waydroid prop` копились там с каждым опросом подвисшего контейнера.
    Для того, что должно пережить Merci (окно Android, шаги в терминале),
    привязка не нужна — поэтому она по запросу.
    """
    if shutil.which("flatpak-spawn"):
        return ["flatpak-spawn", "--host", *(["--watch-bus"] if watch else []), *argv]
    return argv


def run(argv: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    """Синхронный вызов. Только вне главного потока."""
    try:
        return subprocess.run(
            host_argv(argv, watch=True),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, returncode=TIMED_OUT, stdout="", stderr="")
    except OSError:
        return subprocess.CompletedProcess(argv, returncode=127, stdout="", stderr="")


def run_detached(argv: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    """То же, что run, но команда на хосте переживает и таймаут, и нас.

    Для того, что нельзя обрывать на середине, — помощника под root.
    """
    try:
        return subprocess.run(
            host_argv(argv), capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, returncode=TIMED_OUT, stdout="", stderr="")
    except OSError:
        return subprocess.CompletedProcess(argv, returncode=127, stdout="", stderr="")


def succeeds(argv: list[str], timeout: int = 15) -> bool:
    return run(argv, timeout).returncode == 0


def in_thread(work, callback=None) -> None:
    """Выполняет work() в потоке и отдаёт результат в главный цикл.

    В work() нельзя трогать виджеты — только считать и опрашивать хост.
    """

    def target() -> None:
        try:
            result = work()
            error: Exception | None = None
        except Exception as exc:  # поток не должен уносить с собой приложение
            result, error = None, exc
        if callback is not None:
            GLib.idle_add(lambda: (callback(result, error), False)[1])

    threading.Thread(target=target, daemon=True).start()


class Cache:
    """Значение с временем жизни: одно и то же состояние не переспрашиваем."""

    def __init__(self, ttl: float = 4.0) -> None:
        self._ttl = ttl
        self._value = None
        self._stamp = 0.0

    def get(self):
        if self._value is None or time.monotonic() - self._stamp > self._ttl:
            return None
        return self._value

    def set(self, value) -> None:
        self._value = value
        self._stamp = time.monotonic()

    def clear(self) -> None:
        self._value = None
