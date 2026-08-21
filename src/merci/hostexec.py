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


def host_argv(argv: list[str]) -> list[str]:
    """Команда для хоста. Вне флатпака — как есть."""
    if shutil.which("flatpak-spawn"):
        return ["flatpak-spawn", "--host", *argv]
    return argv


def run(argv: list[str], timeout: int = 15) -> subprocess.CompletedProcess:
    """Синхронный вызов. Только вне главного потока."""
    try:
        return subprocess.run(
            host_argv(argv), capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
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
