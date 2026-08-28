"""Точка входа Merci."""

from __future__ import annotations

import os
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from .library import data_root  # noqa: E402
from .i18n import set_language, tr  # noqa: E402
from .settings import Settings  # noqa: E402
from .window import MerciWindow  # noqa: E402

APP_ID = "xyz.hackerstone.Merci"
VERSION = "0.1.2"

_CSS = """
.merci-icon {
  border-radius: 12px;
}
.merci-log {
  font-size: 0.82em;
  background: transparent;
}
.merci-badge {
  font-size: 0.75em;
  font-weight: bold;
  padding: 2px 8px;
  border-radius: 999px;
  background: alpha(@warning_color, 0.18);
  color: @warning_color;
}
/* объявлено после .merci-badge: иначе общее правило перекрывает цвет */
.merci-badge-native {
  background: alpha(@success_color, 0.16);
  color: @success_color;
}
.merci-percent {
  font-size: 2.6em;
  font-weight: 300;
  font-feature-settings: "tnum";
}
.merci-progress trough,
.merci-progress progress {
  min-height: 10px;
  border-radius: 999px;
}
.merci-step {
  transition: opacity 200ms ease;
}
.merci-row:selected .merci-badge {
  background: alpha(currentColor, 0.2);
  color: inherit;
}
"""


class MerciApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_OPEN,
        )
        self.window: MerciWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)

        provider = Gtk.CssProvider()
        provider.load_from_string(_CSS)
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        for name, callback in (
            ("about", self._on_about),
            ("open-library", self._on_open_library),
            ("quit", lambda *_: self.quit()),
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
        about.add_credit_section(tr("Работает поверх"), ["Waydroid", "waydroid_script"])
        about.present(self.window)

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
