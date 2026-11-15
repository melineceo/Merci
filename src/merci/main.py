"""Точка входа Merci."""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk  # noqa: E402

from .library import data_root  # noqa: E402
from .i18n import set_language, tr  # noqa: E402
from .settings import Settings  # noqa: E402
from .window import MerciWindow, apply_color_scheme  # noqa: E402

APP_ID = "xyz.hackerstone.Merci"
VERSION = "0.2.0"

class MerciApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
        )
        self.window: MerciWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)

        # Оформление целиком живёт в пакете ui: стиль подключает вид, когда
        # строится. Здесь — только выбор светлой или тёмной темы.
        apply_color_scheme(Settings().color_scheme)

        for name, callback in (
            ("about", self._on_about),
            ("open-library", self._on_open_library),
            ("show-updates", self._on_show_updates),
            ("quit", self._on_quit),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def do_activate(self) -> None:
        if self.window is None:
            self.window = MerciWindow(self)
        self.window.present()

    def do_open(self, files, _n_files, _hint) -> None:
        self.do_activate()
        for gfile in files:
            path = gfile.get_path()
            if path and self.window is not None:
                self.window.import_apk(path)

    def _on_quit(self, *_args) -> None:
        """Ctrl+Q. Выходим через окно: во время работы под root оно в выходе
        отказывает (ход работы стал бы не виден, а итог — не проверен).
        Раньше сочетание клавиш закрывало программу мимо этого отказа —
        в том числе поверх мастера, посреди шага."""
        if self.window is not None:
            self.window.request_quit()
        else:
            self.quit()

    def _on_about(self, *_args) -> None:
        about = Adw.AboutDialog(
            application_name="Merci",
            application_icon=APP_ID,
            version=VERSION,
            developer_name="hackerstone",
            license_type=Gtk.License.MIT_X11,
            comments=(
                tr("Библиотека APK с запуском через Waydroid: перетащите файл — "
                "Merci разберёт манифест, подготовит контейнер и отдаст "
                "приложение ему.")
            ),
        )
        about.add_credit_section(tr("Работает поверх"), ["Waydroid"])
        # Сам waydroid_script Merci больше не исполняет, но трансляторы ARM
        # ставит из тех же архивов и тем же порядком, что и он.
        about.add_credit_section(
            tr("Трансляторы ARM и порядок их установки"), ["waydroid_script (casualsnek)"]
        )
        about.present(self.window)

    def _on_show_updates(self, *_args) -> None:
        """Нажали на уведомление об обновлениях: окно могло быть в трее."""
        self.do_activate()
        if self.window is not None:
            self.window.present_window()
            self.window.show_updates()

    def _on_open_library(self, *_args) -> None:
        Gtk.FileLauncher.new(Gio.File.new_for_path(data_root())).launch(
            self.window, None, None
        )


def main() -> int:
    os.makedirs(data_root(), exist_ok=True)
    # Язык выбираем до постройки окон: текст виджетам задаётся один раз.
    set_language(Settings().language)
    return MerciApplication().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
