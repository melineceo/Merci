"""Страница приложения: герой, плашка, карточки «Сведения» и «Запуск»;
и приёмник пустой библиотеки.

Карточки — тональные поверхности M3 без размытия: стекла в окне и так
достаточно, а содержимое должно читаться ровно, что бы ни лежало под ним.
"""

from __future__ import annotations

from typing import Callable

from gi.repository import Adw, Gtk, Pango

from ..i18n import tr
from .api import AppPage, Notice, Profiles
from .widgets import (AppIcon, accessible_label, box, button, clear, icon, label, set_classes,
                      spinner)


class Hero(Gtk.Box):
    """Значок со свечением, надзаголовок, крупное название, плашки.

    Рядом с названием — карандаш «Переименовать»: имя записи берётся из
    имени файла и бывает длинным и одинаковым у нескольких сборок, а
    исправляют его там же, где видят. Диалог ввода показывает логика.
    """

    def __init__(self, on_rename: Callable[[], None] | None = None) -> None:
        super().__init__(spacing=26)
        self.set_margin_top(6)
        self.icon = AppIcon(112, "hero")
        self.icon.set_valign(Gtk.Align.CENTER)
        self.append(self.icon)
        col = box(True, 4, hexpand=True, valign=Gtk.Align.CENTER)
        self.overline = label("", "hero-over", wrap=True)
        col.append(self.overline)
        self.title = label("", "hero-title", wrap=True, lines=2,
                           ellipsize=Pango.EllipsizeMode.END)
        self.title.set_hexpand(False)
        title_row = box(False, 6)
        title_row.append(self.title)
        self.rename = button(None, "document-edit-symbolic", "icon-btn", "rename-btn",
                             tooltip=tr("Переименовать запись"),
                             on_click=on_rename or (lambda: None))
        self.rename.set_valign(Gtk.Align.CENTER)
        title_row.append(self.rename)
        col.append(title_row)
        self.version = label("", "hero-sub", wrap=True)
        col.append(self.version)
        self.chips = Adw.WrapBox(child_spacing=8, line_spacing=8)
        self.chips.set_margin_top(10)
        self.chips.set_valign(Gtk.Align.START)
        col.append(self.chips)
        self.append(col)

    def show(self, page: AppPage) -> None:
        self.icon.set_path(page.icon)
        self.overline.set_label(page.overline)
        self.overline.set_visible(bool(page.overline))
        self.title.set_label(page.name)
        self.title.set_tooltip_text(page.name)
        accessible_label(self.rename, tr("Переименовать «{name}»", name=page.name))
        self.version.set_label(page.version)
        self.version.set_visible(bool(page.version))
        clear(self.chips)
        if page.package:
            self.chips.append(self._chip(page.package, "application-x-addon-symbolic", "mono"))
        for index, text in enumerate(page.chips):
            # Первая плашка обычно — способ запуска (ARM/x86): её выделяем
            # тоном secondary и значком. Tertiary в этой гамме не берём: им
            # здесь ничего не помечено, чтобы метки не сливались в одну группу
            # с предупреждениями.
            if index == 0:
                self.chips.append(self._chip(text, "system-run-symbolic", "accent"))
            else:
                self.chips.append(self._chip(text, None, ""))

    @staticmethod
    def _chip(text: str, glyph: str | None, extra: str) -> Gtk.Widget:
        chip = box(False, 6, "chip", valign=Gtk.Align.CENTER)
        if extra:
            chip.add_css_class(extra)
        if glyph:
            chip.append(icon(glyph, 14))
        chip.append(label(text, ellipsize=Pango.EllipsizeMode.MIDDLE, max_chars=40))
        return chip


_NOTICE_ICONS = {"warning": "dialog-warning-symbolic", "error": "dialog-warning-symbolic",
                 "info": "dialog-information-symbolic"}


class NoticeBar(Gtk.Revealer):
    """Плашка-предупреждение с необязательной кнопкой.

    Подкрашенное стекло, а не сплошная заливка: предупреждение — лёгкий
    оттенок ошибки с рамкой, ошибка — тот же компонент на ступень плотнее,
    сведения — тональная secondary. Значок — треугольник: «кирпич» читался
    как «запрещено». Отступ до следующего блока — внутри (``gap_after``):
    закрытая плашка не оставляет двойного зазора.
    """

    def __init__(self, on_action: Callable[[], None], gap_after: int = 0) -> None:
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                         transition_duration=220)
        self.bar = box(False, 14, "notice")
        self.bar.set_margin_bottom(gap_after)
        self.glyph = icon("dialog-warning-symbolic", 22, "notice-glyph")
        self.glyph.set_valign(Gtk.Align.CENTER)
        self.bar.append(self.glyph)
        self.text = label("", "notice-text", wrap=True, hexpand=True)
        self.text.set_valign(Gtk.Align.CENTER)
        self.bar.append(self.text)
        self.action = button("", None, "m3", on_click=on_action)
        self.action.set_valign(Gtk.Align.CENTER)
        self.bar.append(self.action)
        self.set_child(self.bar)

    def set_notice(self, notice: Notice | None) -> None:
        if notice is None:
            self.set_reveal_child(False)
            return
        tone = notice.tone if notice.tone in _NOTICE_ICONS else "warning"
        set_classes(self.bar, "tone-", f"tone-{tone}")
        self.glyph.set_from_icon_name(_NOTICE_ICONS[tone])
        self.text.set_label(notice.text)
        self.action.set_label(notice.action)
        self.action.set_visible(bool(notice.action))
        self.set_reveal_child(True)


class FactsCard(Gtk.Box):
    """«Сведения»: пары подпись — значение. Значения обновляются по ключу."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.add_css_class("m3-card")
        self.append(label(tr("Сведения"), "card-title"))
        self.rows = box(True, 0)
        self.rows.set_margin_top(6)
        self.append(self.rows)
        self._values: dict[str, Gtk.Label] = {}
        self._facts: tuple[tuple[str, str, str], ...] = ()

    def show(self, facts: tuple[tuple[str, str, str], ...], overrides: dict[str, str]) -> None:
        if facts != self._facts:
            self._facts = facts
            clear(self.rows)
            self._values = {}
            for index, (key, caption, _value) in enumerate(facts):
                if index:
                    sep = Gtk.Box()
                    sep.add_css_class("kv-sep")
                    self.rows.append(sep)
                row = box(True, 1)
                row.set_margin_top(8)
                row.set_margin_bottom(8)
                row.append(label(caption, "kv-key"))
                value = label("", "kv-val", ellipsize=Pango.EllipsizeMode.MIDDLE)
                value.set_selectable(True)
                value.set_can_focus(False)
                row.append(value)
                self.rows.append(row)
                self._values[key] = value
        for key, _caption, value in facts:
            self.set_fact(key, overrides.get(key, value))

    def set_fact(self, key: str, value: str) -> bool:
        target = self._values.get(key)
        if target is None:
            return False
        target.set_label(value)
        target.set_tooltip_text(value or None)
        return True


class LaunchCard(Gtk.Box):
    """«Запуск»: сборка в контейнере, профиль Android, управление записью."""

    def __init__(self, on_profile: Callable[[int], None],
                 on_manage: Callable[[str], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=14, hexpand=True)
        self.add_css_class("m3-card")
        self.append(label(tr("Запуск"), "card-title"))
        self._on_profile = on_profile
        self._quiet = False
        self._labels: tuple[str, ...] = ()

        # Строка состояния, а не плашка-кнопка: спокойный тональный фон,
        # смысл несёт значок в кружке (галочка — эта сборка, обновление —
        # другая, вопрос — не знаем).
        self.build = box(False, 12, "build-box")
        self.build_slot = box(False, 0, "build-mark", valign=Gtk.Align.CENTER)
        self.build.append(self.build_slot)
        col = box(True, 1, hexpand=True)
        col.append(label(tr("Сборка в контейнере"), "kv-key"))
        self.build_text = label("", "kv-val", wrap=True)
        col.append(self.build_text)
        self.build.append(col)
        self.append(self.build)

        self.profile = box(False, 12)
        col = box(True, 1, hexpand=True, valign=Gtk.Align.CENTER)
        col.append(label(tr("Профиль Android"), "set-title"))
        self.profile_hint = label("", "set-sub", wrap=True)
        col.append(self.profile_hint)
        self.profile.append(icon("system-users-symbolic", 20, "set-icon"))
        self.profile.append(col)
        self.dropdown = Gtk.DropDown.new_from_strings([])
        self.dropdown.add_css_class("field")
        self.dropdown.set_valign(Gtk.Align.CENTER)
        self.dropdown.set_tooltip_text(tr("Профиль Android"))
        accessible_label(self.dropdown, tr("Профиль Android"))
        self.dropdown.connect("notify::selected", self._on_selected)
        self.profile.append(self.dropdown)
        self.profile.set_visible(False)
        self.append(self.profile)

        spacer = Gtk.Box(vexpand=True)
        self.append(spacer)
        sep = Gtk.Box()
        sep.add_css_class("kv-sep")
        self.append(sep)
        self.append(label(tr("Управление записью"), "kv-key"))
        manage = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True,
                             min_children_per_line=1, max_children_per_line=2,
                             column_spacing=8, row_spacing=8)
        self.btn_uninstall = button(tr("Удалить из контейнера"), "edit-delete-symbolic", "m3",
                                    "outlined", "small", on_click=lambda: on_manage("uninstall"))
        self.btn_remove = button(tr("Удалить из библиотеки"), "user-trash-symbolic", "m3", "danger",
                                 "small", on_click=lambda: on_manage("remove"))
        for widget in (self.btn_uninstall, self.btn_remove):
            child = Gtk.FlowBoxChild(focusable=False)
            child.set_child(widget)
            manage.append(child)
        self.append(manage)
        self.set_build(tr("проверяем…"), None)

    def set_build(self, text: str, ok: bool | None) -> None:
        kind = "unknown" if ok is None else ("ok" if ok else "other")
        for cls in ("ok", "other", "unknown"):
            self.build.remove_css_class(cls)
        self.build.add_css_class(kind)
        clear(self.build_slot)
        if ok is None:
            self.build_slot.append(spinner(18) if text.endswith("…") else
                                   icon("dialog-question-symbolic", 16))
        else:
            self.build_slot.append(icon("object-select-symbolic" if ok else
                                        "software-update-available-symbolic", 16))
        self.build_text.set_label(text)

    def set_profiles(self, profiles: Profiles) -> None:
        self.profile.set_visible(profiles.visible)
        self.profile_hint.set_label(profiles.hint)
        self.profile_hint.set_visible(bool(profiles.hint))
        self._quiet = True
        try:
            if tuple(profiles.labels) != self._labels:
                self._labels = tuple(profiles.labels)
                self.dropdown.set_model(Gtk.StringList.new(list(self._labels)))
            if 0 <= profiles.selected < len(self._labels):
                self.dropdown.set_selected(profiles.selected)
            self.dropdown.set_sensitive(profiles.enabled)
        finally:
            self._quiet = False

    def _on_selected(self, dropdown: Gtk.DropDown, _pspec) -> None:
        index = dropdown.get_selected()
        if not self._quiet and index != Gtk.INVALID_LIST_POSITION:
            self._on_profile(int(index))

    def set_can_uninstall(self, enabled: bool) -> None:
        self.btn_uninstall.set_sensitive(enabled)


class Welcome(Gtk.Box):
    """Пустая библиотека: стеклянный приёмник «Перетащите APK сюда»."""

    def __init__(self, on_add: Callable[[], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER,
                         valign=Gtk.Align.CENTER)
        self.set_margin_start(24)
        self.set_margin_end(24)
        zone = box(True, 14, "glass", "glass-panel", "dropzone")
        ring = box(False, 0, "dropzone-ring", halign=Gtk.Align.CENTER)
        glyph = icon("folder-download-symbolic", 44)
        glyph.set_hexpand(True)
        ring.append(glyph)
        zone.append(ring)
        title = label(tr("Перетащите APK сюда"), "welcome-title", xalign=0.5, wrap=True)
        title.set_margin_top(6)
        title.set_justify(Gtk.Justification.CENTER)
        zone.append(title)
        sub = label(tr("Игры и приложения Android запустятся в своих окнах — по одному "
                       "Android на окно"), "welcome-sub", xalign=0.5, wrap=True, max_chars=44)
        sub.set_justify(Gtk.Justification.CENTER)
        zone.append(sub)
        pick = button(tr("Выбрать файл…"), "document-open-symbolic", "m3", "filled",
                      on_click=on_add)
        pick.set_halign(Gtk.Align.CENTER)
        pick.set_margin_top(8)
        zone.append(pick)
        self.pick = pick
        self.append(zone)
