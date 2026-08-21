#!/usr/bin/env python3
"""Открывает на хосте ссылки, присланные из Waydroid.

Слушает только адрес моста контейнера (192.168.240.1) — снаружи машины порт
недоступен, а из самого контейнера доступен без настройки. Принимает один
запрос вида /open?url=… и передаёт адрес xdg-open.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

BRIDGE = "192.168.240.1"
PORT = 7749
# Значение из <linux/in.h>: в socket его нет, а число стабильное.
_IP_FREEBIND = 15
# Открываем только веб-ссылки: schemes вроде file:// сюда попадать не должны.
ALLOWED = ("http://", "https://")


def _session_env() -> dict[str, str]:
    """Окружение графической сессии для xdg-open.

    Служба живёт под systemd пользователя, и там может не быть ни
    WAYLAND_DISPLAY, ни DBUS_SESSION_BUS_ADDRESS — тогда xdg-open просто
    молча ничего не делает. Недостающее восстанавливаем по XDG_RUNTIME_DIR:
    сокет Wayland и шина лежат именно там.
    """
    env = dict(os.environ)
    runtime = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    env.setdefault("XDG_RUNTIME_DIR", runtime)

    if "WAYLAND_DISPLAY" not in env:
        try:
            sockets = sorted(
                name
                for name in os.listdir(runtime)
                if name.startswith("wayland-") and not name.endswith(".lock")
            )
        except OSError:
            sockets = []
        if sockets:
            env["WAYLAND_DISPLAY"] = sockets[0]

    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
    return env


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — имя задано базовым классом
        request = urlparse(self.path)
        if request.path == "/ping":
            # Проверка связи из приложения: ничего не открываем, просто
            # подтверждаем, что запрос дошёл.
            self.send_response(204)
            self.end_headers()
            return

        if request.path != "/open":
            self.send_error(404)
            return

        values = parse_qs(request.query).get("url", [])
        url = values[0] if values else ""
        if not url.startswith(ALLOWED):
            self.send_error(400, "only http/https")
            return

        self.send_response(204)
        self.end_headers()
        # Аргументом, а не через оболочку: ссылка приходит извне, и никакой
        # разбор её содержимого нам не нужен.
        result = subprocess.run(
            ["xdg-open", url],
            env=_session_env(),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if result.returncode != 0:
            # Раньше вывод уходил в никуда, и отказ выглядел как «ничего не
            # произошло» — самая дорогая для отладки ситуация.
            print(
                f"xdg-open вернул {result.returncode}: "
                f"{(result.stderr or result.stdout).strip()[:200]}",
                flush=True,
            )

    def log_message(self, fmt: str, *args) -> None:
        """Пишем в журнал только суть: кто пришёл и что открыли."""
        print(fmt % args, flush=True)


class FreebindServer(HTTPServer):
    """Позволяет занять адрес моста до того, как он появится.

    Мост waydroid0 создаётся при старте контейнера, а служба поднимается
    вместе с сеансом — и раньше падала с «Cannot assign requested address»,
    уходя в бесконечный перезапуск. IP_FREEBIND снимает это условие: адрес
    можно занять заранее.
    """

    def server_bind(self) -> None:
        self.socket.setsockopt(socket.SOL_IP, _IP_FREEBIND, 1)
        super().server_bind()


def main() -> int:
    if shutil.which("xdg-open") is None:
        print("xdg-open не найден", file=sys.stderr)
        return 1

    try:
        server = FreebindServer((BRIDGE, PORT), Handler)
    except OSError as exc:
        # Ядро может не знать IP_FREEBIND — тогда ждём появления моста.
        print(f"привязка не удалась ({exc}), ждём мост", flush=True)
        while True:
            time.sleep(5)
            try:
                server = HTTPServer((BRIDGE, PORT), Handler)
                break
            except OSError:
                continue

    print(f"слушаю {BRIDGE}:{PORT}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
