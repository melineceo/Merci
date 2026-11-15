"""Контейнер Waydroid: «остров» в шапке и его стеклянная панель.

Контейнер общий для всех приложений, поэтому он не повторяется на каждой
странице, а живёт в шапке: точка и текст состояния, быстрые Eco и FPS,
стрелка. Стрелка (или щелчок по состоянию) раскрывает панель со всем
остальным — системой, оптимизацией, перезапуском и выключением.

Быстрые переключатели продублированы в панели. Обе копии держатся в
согласии сами, а логике уходит ровно один вызов на одно нажатие человека;
``set_quick`` от логики не зовёт обработчики вовсе.
"""

from __future__ import annotations

from typing import Callable

from gi.repository import Gtk, Pango

from ..i18n import tr
from . import api
from .widgets import (ButtonContent, Collapse, Segmented, WavyIndicator, accessible_label, box,
                      button, fps_label, icon, label, set_flag, spinner)

Action = Callable[[str, object], None]

# Классы точки состояния. Всё, чего вид не знает (опечатка, состояние из
# будущей версии договора), рисуется как «неясно»: незнакомое не должно
# выглядеть как «всё готово».
_DOT_CLASSES = ("ready", "asleep", "unprepared", "unknown", "busy")

# Короткий статус для узкого окна: слово «Waydroid» остаётся подлежащим.
_SHORT = {
    api.CONTAINER_READY: "Waydroid: работает",
    api.CONTAINER_ASLEEP: "Waydroid: остановлен",
    api.CONTAINER_UNPREPARED: "Waydroid: не готов",
    api.CONTAINER_UNKNOWN: "Waydroid: нет ответа",
    api.CONTAINER_BUSY: "Waydroid: подождите…",
}


def _dot_class(kind: str) -> str:
    return kind if kind in _DOT_CLASSES else api.CONTAINER_UNKNOWN


def _needs_session(kind: str) -> bool:
    """Действиям, которым нужен подготовленный и не занятый контейнер (окно
    Android, многооконный режим, очистка памяти), сейчас не сработать: они
    недоступны, а не «нажимаются впустую». Сохраняемые настройки (Eco, FPS,
    размер окон) ждут только конца хода — их логика запоминает и без сессии."""
    return kind in (api.CONTAINER_BUSY, api.CONTAINER_UNPREPARED)


def _nowrap_arrow(text: str) -> str:
    """«ARM64 → x86_64» не рвётся переносом перед стрелкой или после неё."""
    return text.replace(" → ", "\u00a0→\u00a0")


class StatusDot(Gtk.Box):
    """Точка состояния контейнера, а пока он поднимается, гаснет или
    перезапускается, — индикатор хода на её месте.

    Место под знак одно и того же размера: остров не прыгает, когда точка
    сменяется индикатором и обратно.
    """

    def __init__(self) -> None:
        super().__init__(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER, hexpand=False)
        self.set_size_request(16, 16)
        # Точка центрируется в своём месте 16×16, не растягиваясь: растяжение
        # ребёнка всплыло бы к острову и раздвинуло бы его.
        self.dot = Gtk.Box(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.dot.set_margin_start(3)
        self.dot.set_margin_end(3)
        self.dot.add_css_class("status-dot")
        self.dot.add_css_class("unknown")
        self.append(self.dot)
        self.spin = spinner(16)
        self.spin.add_css_class("status-spin")
        self.spin.set_visible(False)
        self.append(self.spin)

    def set_kind(self, kind: str) -> None:
        active = _dot_class(kind)
        for cls in _DOT_CLASSES:
            set_flag(self.dot, cls, cls == active)
        busy = active == api.CONTAINER_BUSY
        self.dot.set_visible(not busy)
        self.spin.set_visible(busy)


class Island(Gtk.Box):
    """Пилюля в центре шапки.

    Своей подложки у неё нет — она лежит прямо на стекле шапки (стекло в
    стекле давало мутную двойную кромку). Части разделены тонкими линиями,
    а нажимаемость показывают слои наведения.
    """

    def __init__(self, on_action: Action, on_open: Callable[[], None]) -> None:
        super().__init__(spacing=2, valign=Gtk.Align.CENTER)
        self.add_css_class("island")
        self._on_action = on_action
        self._quiet = False
        self._kind = api.CONTAINER_UNKNOWN
        self._compact = False

        self.status = Gtk.Button(tooltip_text=tr("Контейнер Waydroid — общие настройки"))
        self.status.add_css_class("island-status")
        inner = box(False, 9)
        self.dot = StatusDot()
        inner.append(self.dot)
        self.title = label("Waydroid", "island-title")
        inner.append(self.title)
        self.sub = label("", "island-sub", ellipsize=Pango.EllipsizeMode.END, max_chars=28)
        inner.append(self.sub)
        self.short = label("", "island-title")
        self.short.set_visible(False)
        inner.append(self.short)
        self.status.set_child(inner)
        self.status.connect("clicked", lambda *_: on_open())
        self.append(self.status)

        self.div = Gtk.Box()
        self.div.add_css_class("island-div")
        self.append(self.div)

        self.eco = Gtk.ToggleButton(tooltip_text=tr("Режим экономии (Eco Mode)"))
        self.eco.add_css_class("island-chip")
        eco_box = box(False, 6)
        eco_box.append(icon("power-profile-power-saver-symbolic", 16))
        eco_box.append(label("Eco"))
        self.eco.set_child(eco_box)
        accessible_label(self.eco, tr("Режим экономии (Eco Mode)"))
        self.eco.connect("toggled", self._on_eco)
        self.append(self.eco)

        self.fps = Gtk.MenuButton(tooltip_text=tr("Ограничение FPS"))
        self.fps.add_css_class("island-chip")
        fps_box = box(False, 6)
        fps_box.append(icon("power-profile-performance-symbolic", 16))
        self.fps_text = label("")
        fps_box.append(self.fps_text)
        self.fps.set_child(fps_box)
        accessible_label(self.fps, tr("Ограничение FPS"))
        popover = Gtk.Popover()
        choices = box(True, 2, "fps-menu")
        choices.append(label(tr("Ограничение FPS"), "group-cap"))
        self._fps_checks: dict[int, Gtk.CheckButton] = {}
        group = None
        for value in api.FPS_CHOICES:
            check = Gtk.CheckButton(label=fps_label(value))
            if group is not None:
                check.set_group(group)
            else:
                group = check
            check.connect("toggled", self._on_fps_check, value)
            self._fps_checks[value] = check
            choices.append(check)
        popover.set_child(choices)
        self.fps.set_popover(popover)
        self.append(self.fps)

        self.div2 = Gtk.Box()
        self.div2.add_css_class("island-div")
        self.append(self.div2)

        self.expand = Gtk.ToggleButton(icon_name="pan-down-symbolic",
                                       tooltip_text=tr("Настройки контейнера"))
        self.expand.add_css_class("island-expand")
        accessible_label(self.expand, tr("Настройки контейнера"))
        self.append(self.expand)
        self.set_quick(False, 0)
        self.set_state(api.CONTAINER_UNKNOWN, "")

    def set_state(self, kind: str, text: str) -> None:
        self._kind = kind
        self.dot.set_kind(kind)
        for widget in (self.eco, self.fps):
            widget.set_sensitive(_dot_class(kind) != api.CONTAINER_BUSY)
        self.sub.set_label(text)
        self.sub.set_visible(bool(text) and not self._compact)
        self.short.set_label(tr(_SHORT.get(_dot_class(kind), _SHORT[api.CONTAINER_UNKNOWN])))
        self.status.set_tooltip_text(f"Waydroid: {text}\n" + tr("Контейнер Waydroid — общие настройки"))
        accessible_label(self.status, f"Waydroid: {text}")

    def _on_eco(self, toggle: Gtk.ToggleButton) -> None:
        if not self._quiet:
            self._on_action("toggle_eco", toggle.get_active())

    def _on_fps_check(self, check: Gtk.CheckButton, value: int) -> None:
        if check.get_active() and not self._quiet:
            self.fps_text.set_label(self._fps_short(value))
            self.fps.popdown()
            self._on_action("set_fps", value)

    @staticmethod
    def _fps_short(value: int) -> str:
        return "∞ FPS" if value == 0 else f"{value} FPS"

    def set_quick(self, eco: bool, fps: int) -> None:
        self._quiet = True
        try:
            self.eco.set_active(eco)
            check = self._fps_checks.get(fps)
            if check is not None:
                check.set_active(True)
            else:
                # Значение не из списка: ни одна галочка не врёт, и любую
                # можно нажать, чтобы вернуть привычное ограничение.
                for other in self._fps_checks.values():
                    if other.get_active():
                        other.set_active(False)
            self.fps_text.set_label(self._fps_short(fps))
        finally:
            self._quiet = False

    def set_compact(self, compact: bool) -> None:
        # В узком окне остров показывает короткий статус с подлежащим
        # («Waydroid: работает»), а Eco и FPS уходят в панель — обрезанный
        # многоточием статус хуже, чем на одно нажатие дальше переключатель.
        self._compact = compact
        self.title.set_visible(not compact)
        self.sub.set_visible(not compact and bool(self.sub.get_label()))
        self.short.set_visible(compact)
        for widget in (self.div, self.eco, self.fps):
            widget.set_visible(not compact)


class ContainerPanel(Gtk.Box):
    """Содержимое панели контейнера (раскрывается из острова)."""

    def __init__(self, on_action: Action, on_close: Callable[[], None]) -> None:
        # Стекло — у рамки вокруг прокрутки (её строит Shell): так тень и
        # скругление не обрезаются, а длинная панель в низком окне листается.
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                         accessible_role=Gtk.AccessibleRole.GROUP)
        self._on_action = on_action
        self._quiet = False
        self._size_dirty = False
        self._logic_size = ""
        self._compact = False
        self._kind = api.CONTAINER_UNKNOWN
        self._text = ""
        self._size_busy = False
        accessible_label(self, tr("Контейнер Waydroid"))

        inner = box(True, 16)
        inner.set_margin_top(20)
        inner.set_margin_bottom(22)
        inner.set_margin_start(22)
        inner.set_margin_end(22)
        self.append(inner)

        head = box(False, 14)
        self.dot = StatusDot()
        self.dot.set_valign(Gtk.Align.START)
        self.dot.set_margin_top(9)
        head.append(self.dot)
        titles = box(True, 2, hexpand=True)
        titles.append(label(tr("Контейнер Waydroid"), "panel-title", wrap=True))
        self.sub = label("", "panel-sub", wrap=True)
        titles.append(self.sub)
        head.append(titles)
        # Свернуть панель можно и стрелкой на острове; здесь — «×», как у
        # любой всплывающей панели, а не вторая такая же стрелка.
        self.close = button(None, "window-close-symbolic", "icon-btn", tooltip=tr("Закрыть"),
                            on_click=on_close)
        self.close.set_valign(Gtk.Align.START)
        head.append(self.close)
        inner.append(head)

        # Контейнер в пути или идёт применение размера — строка хода под
        # заголовком (как «Запускаем окно…» в разделе «Окна»): видно, что
        # именно занято и почему часть действий сейчас недоступна.
        strip = box(True, 6, "op-strip")
        strip_top = box(False, 10)
        strip_top.append(icon("emblem-system-symbolic", 16))
        self.busy_text = label("", "op-text", wrap=True, hexpand=True)
        strip_top.append(self.busy_text)
        strip.append(strip_top)
        strip.append(WavyIndicator())
        self.busy = Collapse(strip)
        self.busy.set_visible(False)
        inner.append(self.busy)

        # «Не подготовлен» — главный выход отсюда, он и стоит первым. Тот же
        # компонент, что и плашка ошибки на странице.
        self.prepare = box(False, 14, "notice", "tone-error")
        glyph = icon("dialog-warning-symbolic", 22, "notice-glyph")
        glyph.set_valign(Gtk.Align.CENTER)
        self.prepare.append(glyph)
        self.prepare_text = label(tr("Waydroid не готов к запуску приложений"), "notice-text",
                                  wrap=True, hexpand=True)
        self.prepare_text.set_valign(Gtk.Align.CENTER)
        self.prepare.append(self.prepare_text)
        prep = button(tr("Подготовить"), None, "m3", on_click=lambda: self._fire("prepare"))
        prep.set_valign(Gtk.Align.CENTER)
        self.prepare.append(prep)
        self.prepare.set_visible(False)
        inner.append(self.prepare)

        # Три действия — равные по ширине, в один ряд при любой ширине окна:
        # одинокая кнопка, перенесённая на вторую строку, выглядит случайно.
        acts_wrap = box(True, 6)
        acts = Gtk.Box(spacing=8, homogeneous=True)
        self.btn_android = button(tr("Открыть окно Android"), "video-display-symbolic", "m3",
                                  "filled", tooltip=tr("Открыть окно Android"),
                                  on_click=lambda: self._fire("open_android"))
        self.btn_restart = button(tr("Перезапустить"), "view-refresh-symbolic", "m3", "tonal",
                                  tooltip=tr("Перезапустить Waydroid"),
                                  on_click=lambda: self._fire("restart"))
        self.btn_stop = button(tr("Выключить"), "system-shutdown-symbolic", "m3", "tonal",
                               tooltip=tr("Выключить Waydroid"),
                               on_click=lambda: self._fire("stop"))
        for widget in (self.btn_android, self.btn_restart, self.btn_stop):
            acts.append(widget)
        acts_wrap.append(acts)
        # Пояснение к «Выключить» — под этой кнопкой, у правого края ряда, со
        # значком сведений; целиком оно ещё и во всплывающей подсказке кнопки.
        self.stop_hint = box(False, 6, "stop-hint", halign=Gtk.Align.END)
        self.stop_hint.append(icon("dialog-information-symbolic", 14))
        self.stop_hint_text = label("", wrap=True, xalign=1.0)
        self.stop_hint.append(self.stop_hint_text)
        self.stop_hint.set_visible(False)
        acts_wrap.append(self.stop_hint)
        inner.append(acts_wrap)

        # Колонки 5 : 7 — «Оптимизации» строк больше, и равные колонки
        # оставляли слева пустоту, а справа ломали подписи в столбик.
        self.columns = Gtk.Grid(column_spacing=14, row_spacing=16, column_homogeneous=True)

        # -- система --
        self.row_bridge, self.bridge_sub = self._row(
            "system-run-symbolic", tr("Трансляция ARM"), "",
            button(tr("Сменить"), None, "m3", "tonal", "small",
                   tooltip=tr("Сменить транслятор ARM"),
                   on_click=lambda: self._fire("switch_bridge")))
        self.row_renderer, self.renderer_sub = self._row(
            "applications-graphics-symbolic", tr("Рендер контейнера"), "")
        size_box = box(False, 8)
        self.size_entry = Gtk.Entry(width_chars=9, max_width_chars=11,
                                    placeholder_text="1920x1080")
        self.size_entry.add_css_class("field")
        self.size_entry.set_input_purpose(Gtk.InputPurpose.FREE_FORM)
        accessible_label(self.size_entry, tr("Размер всех окон"))
        self.size_entry.connect("changed", self._on_size_changed)
        self.size_entry.connect("activate", lambda *_: self._apply_size())
        size_box.append(self.size_entry)
        self.btn_monitor = button(None, "video-display-symbolic", "icon-btn", "tonal", "field-btn",
                                  tooltip=tr("Подставить размер монитора"),
                                  on_click=lambda: self._fire("fill_monitor_size"))
        size_box.append(self.btn_monitor)
        self.btn_size = button(None, "object-select-symbolic", "icon-btn", "tonal", "field-btn",
                               tooltip=tr("Применить размер"), on_click=self._apply_size)
        size_box.append(self.btn_size)
        self.row_size, _ = self._row("view-fullscreen-symbolic", tr("Размер всех окон"),
                                     tr("ширина × высота, Enter — применить"), size_box,
                                     stacked=True)
        self.group_system = self._group(tr("Система"),
                                        [self.row_bridge, self.row_renderer, self.row_size])

        # -- оптимизация --
        self.eco = Gtk.Switch(valign=Gtk.Align.CENTER)
        accessible_label(self.eco, tr("Режим экономии (Eco Mode)"))
        self.eco.connect("notify::active", self._on_eco)
        self.row_eco, _ = self._row("power-profile-power-saver-symbolic",
                                    tr("Режим экономии (Eco Mode)"),
                                    tr("меньше анимаций, ниже нагрузка"), self.eco)
        self.fps = Segmented([(v, fps_label(v)) for v in api.FPS_CHOICES],
                             lambda v: self._fire("set_fps", v), tr("Ограничение FPS"))
        self.fps.set_halign(Gtk.Align.START)
        row_fps, _ = self._row("power-profile-performance-symbolic", tr("Ограничение FPS"), "",
                               self.fps, stacked=True)
        self.btn_freeform = button(tr("Применить"), None, "m3", "tonal", "small",
                                   tooltip=tr("Применить свободный многооконный режим"),
                                   on_click=lambda: self._fire("apply_freeform"))
        row_free, _ = self._row(
            "video-joined-displays-symbolic", tr("Многооконный режим"),
            tr("игры и окна открываются рядом"), self.btn_freeform)
        self.btn_trim = button(tr("Очистить"), None, "m3", "tonal", "small",
                               tooltip=tr("Очистить память Android (RAM Trim)"),
                               on_click=lambda: self._fire("trim_memory"))
        row_trim, _ = self._row(
            "edit-clear-all-symbolic", tr("Очистить память Android"),
            tr("RAM Trim: кэш и неактивная память"), self.btn_trim)
        self.group_opt = self._group(tr("Оптимизация"), [self.row_eco, row_fps, row_free, row_trim])
        self._place_columns()
        inner.append(self.columns)

    # -- построение --
    @staticmethod
    def _group(title: str, rows: list[Gtk.Widget]) -> Gtk.Widget:
        wrap = box(True, 8)
        wrap.append(label(title.upper(), "group-cap"))
        body = box(True, 0, "set-group", vexpand=True)
        for row in rows:
            body.append(row)
        wrap.append(body)
        return wrap

    def _place_columns(self) -> None:
        for widget in (self.group_system, self.group_opt):
            if widget.get_parent() is self.columns:
                self.columns.remove(widget)
        if self._compact:
            self.columns.attach(self.group_system, 0, 0, 12, 1)
            self.columns.attach(self.group_opt, 0, 1, 12, 1)
        else:
            self.columns.attach(self.group_system, 0, 0, 5, 1)
            self.columns.attach(self.group_opt, 5, 0, 7, 1)

    @staticmethod
    def _row(glyph: str, title: str, sub: str, end: Gtk.Widget | None = None,
             stacked: bool = False) -> tuple[Gtk.Widget, Gtk.Label]:
        """Строка настройки: значок, заголовок, пояснение и элемент справа
        (или под текстом — для широких элементов вроде сегментов FPS)."""
        # Строка растягивается по высоте, а её содержимое стоит посередине:
        # у более короткой из двух колонок лишняя высота расходится поровну
        # между строками, а не пустеет внизу карточки.
        row = box(True, 0, "set-row", vexpand=True)
        inner = box(True, 10, valign=Gtk.Align.CENTER, vexpand=True)
        row.append(inner)
        top = box(False, 12)
        top.append(icon(glyph, 18, "set-icon"))
        col = box(True, 1, hexpand=True, valign=Gtk.Align.CENTER)
        col.append(label(title, "set-title", wrap=True))
        sub_label = label(sub, "set-sub", wrap=True)
        sub_label.set_visible(bool(sub))
        col.append(sub_label)
        top.append(col)
        inner.append(top)
        if end is not None:
            end.set_valign(Gtk.Align.CENTER)
            if stacked:
                end.set_margin_start(30)
                end.set_halign(Gtk.Align.START)
                inner.append(end)
            else:
                top.append(end)
        return row, sub_label

    # -- нажатия --
    def _fire(self, action: str, value: object = None) -> None:
        if not self._quiet:
            self._on_action(action, value)

    def _on_eco(self, switch: Gtk.Switch, _pspec) -> None:
        self._fire("toggle_eco", switch.get_active())

    def _on_size_changed(self, _entry) -> None:
        if not self._quiet:
            self._size_dirty = self.size_entry.get_text().strip() != self._logic_size
            # Изменённое значение ждёт применения — галочка зовёт залитым
            # цветом, а не остаётся незаметным глифом.
            set_flag(self.btn_size, "armed", self._size_dirty)

    def _apply_size(self) -> None:
        self._size_dirty = False
        self.btn_size.remove_css_class("armed")
        self._on_action("apply_size", self.size_entry.get_text().strip())

    def discard_size_edit(self) -> None:
        """Панель закрыта без Enter: недописанный размер не должен навсегда
        заслонить значения, которые присылает логика."""
        if self._size_dirty:
            self._size_dirty = False
            self.btn_size.remove_css_class("armed")
            self._quiet = True
            try:
                self.size_entry.set_text(self._logic_size)
            finally:
                self._quiet = False

    # -- данные --
    def set_state(self, kind: str, text: str) -> None:
        self._kind = _dot_class(kind)
        self._text = text
        self.dot.set_kind(kind)
        self.sub.set_label(f"{text} · {tr('общий для всех приложений')}" if text else
                           tr("общий для всех приложений"))
        self.prepare.set_visible(kind == api.CONTAINER_UNPREPARED)
        self._apply_lock()

    def _apply_lock(self) -> None:
        """Что сейчас можно трогать: всё, чему нужна живая сессия, ждёт
        конца хода (или подготовки); строка хода говорит, чего ждём."""
        busy = self._kind == api.CONTAINER_BUSY
        if busy:
            # Текст логики («перезапускается…») — с подлежащим.
            self.busy_text.set_label(tr("Контейнер: {state}", state=self._text) if self._text
                                     else tr("Контейнер готовится…"))
        elif self._size_busy:
            self.busy_text.set_label(tr("Применяем размер окон…"))
        # Скрытый ревилер всё равно занимал бы зазор коробки — убираем совсем.
        self.busy.set_visible(busy or self._size_busy)
        self.busy.set_reveal_child(busy or self._size_busy)
        for widget in (self.btn_freeform, self.btn_trim, self.btn_android):
            widget.set_sensitive(not _needs_session(self._kind))
        for widget in (self.eco, self.fps):
            widget.set_sensitive(not busy)
        for widget in (self.size_entry, self.btn_monitor, self.btn_size):
            widget.set_sensitive(not busy and not self._size_busy)

    def set_controls(self, can_stop: bool, stop_hint: str, can_restart: bool) -> None:
        self.btn_stop.set_sensitive(can_stop)
        self.btn_restart.set_sensitive(can_restart)
        self.stop_hint_text.set_label(stop_hint)
        self.stop_hint.set_visible(bool(stop_hint))
        self.btn_stop.set_tooltip_text(stop_hint or tr("Выключить Waydroid"))

    def set_bridge(self, text: str, alert: bool, visible: bool) -> None:
        text = _nowrap_arrow(text)
        self.bridge_sub.set_label(text)
        self.bridge_sub.set_visible(bool(text))
        self.row_bridge.set_visible(visible)
        set_flag(self.row_bridge, "alert", alert)

    def set_renderer(self, text: str) -> None:
        self.renderer_sub.set_label(text)
        self.renderer_sub.set_visible(bool(text))

    def set_window_size(self, text: str, busy: bool) -> None:
        # Пока человек правит поле, присланное логикой значение его не
        # перетирает; после применения (или закрытия панели) поле снова
        # следует за логикой.
        self._logic_size = text
        if not self._size_dirty or busy:
            self._quiet = True
            try:
                self.size_entry.set_text(text)
            finally:
                self._quiet = False
            self._size_dirty = False
            self.btn_size.remove_css_class("armed")
        self._size_busy = bool(busy)
        self._apply_lock()

    def set_quick(self, eco: bool, fps: int) -> None:
        self._quiet = True
        try:
            self.eco.set_active(eco)
            self.fps.set_value(fps)
        finally:
            self._quiet = False

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        self._place_columns()
        # В узком окне подписи кнопок короче: три равные кнопки в ряд.
        content = self.btn_android.get_child()
        if isinstance(content, ButtonContent):
            content.set_label(tr("Окно Android") if compact else tr("Открыть окно Android"))

    def focus_first(self) -> None:
        for widget in (self.btn_android, self.close):
            if widget.get_sensitive() and widget.grab_focus():
                return
