"""Окно обновлений: что устарело и чем это обновить.

Только показ и нажатия. Что устарело, знает ``updates``; как это ставится
и что спросить перед установкой — логика окна (``window.py``): сюда она
отдаёт отчёт проверки и получает нажатия через переданные функции.
"""

from __future__ import annotations

import time
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gtk  # noqa: E402

from . import updates  # noqa: E402
from .i18n import tr  # noqa: E402


def _ago(stamp: float) -> str:
    """Когда проверяли — словами."""
    if not stamp:
        return tr("ещё не проверялось")
    delta = time.time() - stamp
    if delta < 90:
        return tr("проверено только что")
    if delta < 3600:
        return tr("проверено {n} мин назад", n=int(delta // 60))
    if delta < 86400:
        return tr("проверено {n} ч назад", n=int(delta // 3600))
    return tr("проверено {date}", date=time.strftime("%d.%m.%Y", time.localtime(stamp)))


def _chip(text: str, *classes: str) -> Gtk.Label:
    label = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
    label.add_css_class("merci-chip")
    for name in classes:
        label.add_css_class(name)
    return label


class UpdatesDialog(Adw.Dialog):
    """Список обновлений с кнопками. Данные — через set_report."""

    def __init__(
        self,
        on_check: Callable[[], None],
        on_apply: Callable[[updates.Update], None],
        on_apply_all: Callable[[], None],
        on_restore: Callable[[], None],
        on_drop_backup: Callable[[], None],
        on_auto: Callable[[bool], None],
        on_hide_updater: Callable[[bool], None],
        auto: bool,
        hide_updater: bool,
    ) -> None:
        super().__init__(title=tr("Обновления"), content_width=620, content_height=640)
        self._on_apply = on_apply
        self._rows: list[Gtk.Widget] = []
        self._backup_rows: list[Gtk.Widget] = []
        self._quiet = False

        self.icon = Gtk.Image(pixel_size=56)
        self.icon.add_css_class("merci-update-icon")
        self.spinner = Adw.Spinner()
        self.spinner.set_size_request(56, 56)
        self.spinner.set_visible(False)
        self.headline = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.headline.add_css_class("title-2")
        self.subline = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.subline.add_css_class("dim-label")

        status = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        status.set_margin_top(10)
        status.set_halign(Gtk.Align.CENTER)
        status.append(self.icon)
        status.append(self.spinner)
        status.append(self.headline)
        status.append(self.subline)

        self.found = Adw.PreferencesGroup(title=tr("Доступно"))
        self.backup = Adw.PreferencesGroup(
            title=tr("Прежний образ Android"),
            description=tr("Копия, сделанная перед обновлением. Если новый образ "
                           "работает, её можно удалить."),
        )

        behaviour = Adw.PreferencesGroup(title=tr("Как обновляться"))
        self.auto_row = Adw.SwitchRow(
            title=tr("Проверять обновления автоматически"),
            subtitle=tr("при запуске и раз в сутки; ставятся они по вашему нажатию"),
        )
        self.auto_row.set_active(auto)
        self.auto_row.connect(
            "notify::active", lambda row, _p: self._quiet or on_auto(row.get_active())
        )
        behaviour.add(self.auto_row)
        self.updater_row = Adw.SwitchRow(
            title=tr("Скрывать встроенный обновлятор Android"),
            subtitle=tr("он ставит образ по нажатию на своё уведомление — под "
                        "работающими окнами; обновления ставит Merci"),
        )
        self.updater_row.set_active(hide_updater)
        self.updater_row.connect(
            "notify::active", lambda row, _p: self._quiet or on_hide_updater(row.get_active())
        )
        behaviour.add(self.updater_row)

        self.notes = Gtk.Label(wrap=True, xalign=0)
        self.notes.add_css_class("dim-label")
        self.notes.add_css_class("caption")
        self.notes.set_visible(False)

        self.all_button = Gtk.Button(label=tr("Обновить всё"))
        self.all_button.add_css_class("suggested-action")
        self.all_button.add_css_class("pill")
        self.all_button.set_halign(Gtk.Align.CENTER)
        self.all_button.connect("clicked", lambda *_: on_apply_all())
        self.all_button.set_visible(False)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        column.set_margin_start(16)
        column.set_margin_end(16)
        column.set_margin_bottom(20)
        column.append(status)
        column.append(self.all_button)
        column.append(self.found)
        column.append(self.backup)
        column.append(behaviour)
        column.append(self.notes)

        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(Adw.Clamp(maximum_size=580, child=column))

        header = Adw.HeaderBar()
        self.check_button = Gtk.Button(icon_name="view-refresh-symbolic")
        self.check_button.set_tooltip_text(tr("Проверить снова"))
        self.check_button.connect("clicked", lambda *_: on_check())
        header.pack_end(self.check_button)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(scroller)
        self.set_child(toolbar)
        self.add_css_class("merci-updates")

        self._on_restore = on_restore
        self._on_drop_backup = on_drop_backup
        self.set_report(None)

    # -- данные -----------------------------------------------------------

    def set_checking(self, checking: bool) -> None:
        """Идёт проверка: вместо значка — индикатор, кнопки недоступны."""
        self.spinner.set_visible(checking)
        self.icon.set_visible(not checking)
        self.check_button.set_sensitive(not checking)
        self.found.set_sensitive(not checking)
        self.all_button.set_sensitive(not checking)
        if checking:
            self.headline.set_text(tr("Проверяем обновления…"))
            self.subline.set_text(tr("образ Android, пакет Waydroid, транслятор, сама Merci"))

    def set_switches(self, auto: bool, hide_updater: bool) -> None:
        """Положение переключателей — без вызова обработчиков."""
        self._quiet = True
        self.auto_row.set_active(auto)
        self.updater_row.set_active(hide_updater)
        self._quiet = False

    def set_report(self, report: updates.Report | None) -> None:
        """Показывает итог проверки; None — проверки ещё не было."""
        for row in self._rows:
            self.found.remove(row)
        self._rows.clear()
        for row in self._backup_rows:
            self.backup.remove(row)
        self._backup_rows.clear()

        found = report.updates if report is not None else ()
        self.found.set_visible(bool(found))
        if report is None:
            self.icon.set_from_icon_name("software-update-available-symbolic")
            self.headline.set_text(tr("Обновления ещё не проверялись"))
            self.subline.set_text(tr("нажмите «Проверить снова» вверху"))
        elif found:
            self.icon.set_from_icon_name("software-update-available-symbolic")
            self.headline.set_text(tr("Доступно обновлений: {count}", count=len(found)))
            self.subline.set_text(_ago(report.checked_at))
        else:
            self.icon.set_from_icon_name("object-select-symbolic")
            self.headline.set_text(tr("Всё актуально"))
            self.subline.set_text(_ago(report.checked_at))

        for update in found:
            row = self._row(update)
            self.found.add(row)
            self._rows.append(row)

        steps = [update for update in found if update.kind == updates.STEP]
        self.all_button.set_visible(len(steps) > 1)

        backup = report.backup if report is not None else {}
        self.backup.set_visible(bool(backup))
        if backup:
            row = self._backup_row(backup)
            self.backup.add(row)
            self._backup_rows.append(row)

        notes = report.notes if report is not None else ()
        self.notes.set_visible(bool(notes))
        self.notes.set_text(
            tr("Не удалось проверить: {what}", what="; ".join(notes)) if notes else ""
        )

    # -- строки -----------------------------------------------------------

    def _row(self, update: updates.Update) -> Adw.ActionRow:
        change = (
            f"{update.current} → {update.available}"
            if update.current and update.available
            else update.available or update.current
        )
        row = Adw.ActionRow(title=update.title, subtitle=change)
        # Версии и имена пакетов — чужой текст: показываем как есть.
        row.set_use_markup(False)
        row.set_subtitle_lines(0)
        row.set_tooltip_text(update.detail)
        row.add_css_class("merci-update-row")

        suffix = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        if update.risky:
            suffix.append(_chip(tr("крупное"), "warn"))
        if update.size_mb:
            suffix.append(_chip(tr("≈ {size} МБ", size=update.size_mb)))
        button = Gtk.Button(
            label=tr("Как обновить") if update.kind == updates.MANUAL else tr("Обновить")
        )
        button.set_valign(Gtk.Align.CENTER)
        button.add_css_class("pill")
        if update.kind != updates.MANUAL:
            button.add_css_class("suggested-action")
        button.connect("clicked", lambda *_: self._on_apply(update))
        suffix.append(button)
        row.add_suffix(suffix)
        return row

    def _backup_row(self, backup: dict) -> Adw.ActionRow:
        try:
            system = int(backup.get("system_datetime", 0))
            saved = float(backup.get("saved_at", 0))
        except (TypeError, ValueError):
            system, saved = 0, 0.0
        size = int(backup.get("size", 0) or 0) // updates.MEGABYTE
        row = Adw.ActionRow(
            title=tr("Сборка от {date}", date=updates.build_date(system)),
            subtitle=tr("сохранена {date} · до {size} МБ на диске",
                        date=time.strftime("%d.%m.%Y", time.localtime(saved)), size=size),
        )
        row.set_use_markup(False)
        row.set_subtitle_lines(0)
        suffix = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        restore = Gtk.Button(label=tr("Вернуть"))
        restore.add_css_class("pill")
        restore.set_tooltip_text(tr("Вернуть прежний образ Android вместо нынешнего"))
        restore.connect("clicked", lambda *_: self._on_restore())
        drop = Gtk.Button(label=tr("Удалить копию"))
        drop.add_css_class("pill")
        drop.add_css_class("flat")
        drop.connect("clicked", lambda *_: self._on_drop_backup())
        suffix.append(restore)
        suffix.append(drop)
        row.add_suffix(suffix)
        return row
