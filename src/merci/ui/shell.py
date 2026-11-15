"""Оболочка окна Merci — реализация ``ShellProtocol`` из ``api.py``.

Раскладка (снизу вверх):

* два слоя атмосферы (кроссфейд палитры);
* тело: стеклянная библиотека слева и рабочая область справа. В рабочей
  области страница прокручивается под стеклянной шапкой и плавающим доком
  главного действия; у краёв она растворяется в атмосфере (маска, а не
  обрезка), поэтому над шапкой и под доком не торчат обрывки текста;
* затемнение и стеклянная панель контейнера поверх всего — панель
  «вырастает» язычком из острова в шапке.

Вид ничего не решает: нажатия уходят в ``Handlers``, а всё, что приходит
от логики, только показывается. Программные обновления (выбор строки,
быстрые переключатели, профиль, размер окон) обработчики не зовут.
Порядок вызовов любой: виджеты существуют с самого начала, и ответ,
пришедший «раньше времени», просто ложится на своё место.
"""

from __future__ import annotations

from typing import Callable, Sequence

import os

from gi.repository import Adw, Gdk, Gio, GLib, Gsk, Gtk, Pango

from ..i18n import tr
from .api import AppPage, Handlers, LibraryItem, Notice, Profiles, WindowsHeader, WindowTile
from .container import ContainerPanel, Island
from .library import COMPACT, MARGIN, WIDE, Library
from .page import FactsCard, Hero, LaunchCard, NoticeBar, Welcome
from .style import StyleSheet
from .widgets import (AppIcon, FadeBin, SheetBin, accessible_label, box, icon, label, set_flag,
                      spinner)
from .windows import WindowsSection, ratio_from_size

# Ширина, после которой окно считается узким: библиотека сворачивается в
# рейку, карточки встают столбцом, второстепенные действия уходят в «⋮».
_NARROW = "max-width: 940sp"
_HEADER_H = 60        # высота стеклянной шапки
_GAP = 24             # ритм страницы: зазор между блоками в любом состоянии
_DOCK_H = 70          # высота дока: кнопка 56 + поля 6 + кромка
_DOCK_BOTTOM = 16     # отступ дока от низа окна
# Затухание страницы у краёв: (где содержимое ещё невидимо, где уже целиком)
# от края, в пикселях. Сверху: под капсулой шапки страницы нет вовсе, а
# ниже её кромки она проявляется за 38 px — шапка парит над страницей, а не
# режет строки. Снизу: к середине дока содержимое исчезает полностью, а
# гаснуть начинает примерно на 40 px выше его кромки.
_FADE_TOP = (MARGIN + _HEADER_H + 2, MARGIN + _HEADER_H + 40)
_FADE_BOTTOM = (_DOCK_BOTTOM + _DOCK_H // 2, _DOCK_BOTTOM + _DOCK_H + 74)
# Поля страницы: в покое и в конце прокрутки содержимое стоит там, где оно
# уже видно целиком (а не в зоне затухания).
_HEADER_SPACE = _FADE_TOP[1] - 2
_DOCK_SPACE = _FADE_BOTTOM[1] - 12


def _software_gl() -> bool:
    """Программный OpenGL (llvmpipe/softpipe) по переменным окружения.

    Строку GL_RENDERER GTK наружу не отдаёт, а спрашивать хост вид не
    должен; эти переменные ставят именно тогда, когда видеокарты нет.
    """
    if os.environ.get("LIBGL_ALWAYS_SOFTWARE", "") not in ("", "0", "false"):
        return True
    return os.environ.get("GALLIUM_DRIVER", "") in ("llvmpipe", "softpipe", "swr")


class Shell(Adw.BreakpointBin):
    """Внешний вид главного окна. Кладётся содержимым в ``Adw.ApplicationWindow``."""

    __gtype_name__ = "MerciShell"

    def __init__(self, handlers: Handlers, menu: Gio.MenuModel | None = None) -> None:
        super().__init__()
        # Брейкпоинтам нужен известный наименьший размер: без него BreakpointBin
        # не знает, до какой ширины ему разрешено сжимать содержимое.
        self.set_size_request(560, 480)
        self._h = handlers
        self._seed: str | None = None
        self._dark = Adw.StyleManager.get_default().get_dark()
        self._slots: dict[str, tuple[str | None, bool]] = {}
        self._b_shown = False
        self._reduced_user = False
        self._reduced_auto = False
        self._compact = False
        self._page_slug: str | None = None
        self._fact_overrides: dict[str, str] = {}
        self._quick = (False, 0)
        self._panel_open = False
        self._toast_source = 0
        self._toast_callback: Callable[[], None] | None = None
        self._dark_handler = 0

        display = Gdk.Display.get_default()
        self._sheet = StyleSheet.for_display(display) if display is not None else None
        self._apply_palette(animate=False)

        root = Gtk.Overlay()
        root.add_css_class("merci-shell")
        self.root = root
        self.atmo_a = Gtk.Box(hexpand=True, vexpand=True, can_target=False)
        self.atmo_a.add_css_class("merci-atmo")
        self.atmo_a.add_css_class("slot-a")
        root.set_child(self.atmo_a)
        self.atmo_b = Gtk.Box(can_target=False)
        for cls in ("merci-atmo", "slot-b", "faded"):
            self.atmo_b.add_css_class(cls)
        root.add_overlay(self.atmo_b)

        self.body = box(False, 0)
        root.add_overlay(self.body)
        root.set_measure_overlay(self.body, True)

        self.library = Library(menu, self._h.select_entry, self._h.add_apk,
                               lambda: self._h.show_updates())
        self.body.append(self.library)
        self.body.append(self._build_main())

        self._build_panel(root)
        self.set_child(root)

        keys = Gtk.ShortcutController()
        keys.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("Escape"),
                                           Gtk.CallbackAction.new(self._on_escape)))
        self.add_controller(keys)

        narrow = Adw.Breakpoint.new(Adw.BreakpointCondition.parse(_NARROW))
        narrow.connect("apply", lambda *_: self._set_compact(True))
        narrow.connect("unapply", lambda *_: self._set_compact(False))
        self.add_breakpoint(narrow)

        self.connect("realize", self._on_realize)
        self.connect("unrealize", self._on_unrealize)

    # ================================================================ сборка
    def _build_main(self) -> Gtk.Widget:
        main = Gtk.Overlay(hexpand=True, vexpand=True)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=200)
        self.stack.add_named(Gtk.Box(), "blank")

        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        clamp = Adw.Clamp(maximum_size=1000, tightening_threshold=720)
        # Зазоры между блоками — отступами снизу у каждого блока, а не
        # spacing коробки: закрытая плашка тогда не оставляет двойной щели.
        page = box(True, 0)
        page.set_margin_top(_HEADER_SPACE)
        page.set_margin_bottom(_DOCK_SPACE)
        page.set_margin_start(24)
        page.set_margin_end(24)
        clamp.set_child(page)
        self.scroller.set_child(clamp)

        self.hero = Hero(lambda: self._h.manage_action("rename"))
        self.hero.set_margin_bottom(_GAP)
        page.append(self.hero)
        self.notice = NoticeBar(self._h.notice_action, gap_after=_GAP)
        page.append(self.notice)
        self.windows = WindowsSection(self._h.window_action, self._h.windows_action)
        self.windows.set_margin_bottom(_GAP)
        page.append(self.windows)
        self.cards = Gtk.Box(spacing=16, homogeneous=True)
        self.facts = FactsCard()
        self.launch = LaunchCard(self._h.profile_chosen, self._h.manage_action)
        self.cards.append(self.facts)
        self.cards.append(self.launch)
        page.append(self.cards)
        self.stack.add_named(self.scroller, "page")

        self.welcome = Welcome(self._h.add_apk)
        self.stack.add_named(self.welcome, "welcome")
        self.stack.set_visible_child_name("blank")
        self.fade = FadeBin(self.stack, _FADE_TOP, _FADE_BOTTOM)
        main.set_child(self.fade)

        main.add_overlay(self._build_header())
        main.add_overlay(self._build_dock())
        main.add_overlay(self._build_snackbar())
        return main

    def _build_header(self) -> Gtk.Widget:
        handle = Gtk.WindowHandle(valign=Gtk.Align.START)
        bar = box(False, 0, "glass", "glass-header")
        bar.set_size_request(-1, _HEADER_H)
        bar.set_margin_top(MARGIN)
        bar.set_margin_start(MARGIN)
        bar.set_margin_end(MARGIN)
        handle.set_child(bar)
        center = Gtk.CenterBox(hexpand=True)
        bar.append(center)

        # Кнопки окна с обеих сторон: раскладка бывает и слева (macOS-стиль,
        # часть тем KDE), а GtkWindowControls сам читает gtk-decoration-layout.
        # Слева — всегда значок и название показанного приложения (на
        # приёмнике пустой библиотеки — «Merci»): шапка — заголовок окна, а
        # не пустая полоса вокруг острова.
        start = box(False, 8, valign=Gtk.Align.CENTER)
        start.set_margin_start(8)
        start.append(self._window_controls(Gtk.PackType.START))
        self.crumbs = box(False, 10, "crumbs", valign=Gtk.Align.CENTER)
        self.crumbs.set_margin_start(6)
        self.crumb_icon = AppIcon(28, "small")
        self.crumb_icon.set_visible(False)
        self.crumbs.append(self.crumb_icon)
        self.crumb_name = label("Merci", "crumb-name", ellipsize=Pango.EllipsizeMode.END,
                                max_chars=16)
        self.crumbs.append(self.crumb_name)
        start.append(self.crumbs)
        center.set_start_widget(start)

        self.island = Island(self._on_container, self.open_container_panel)
        self.island.set_margin_start(8)
        self.island.set_margin_end(8)
        self.island.expand.connect("toggled", self._on_expand_toggled)
        center.set_center_widget(self.island)

        end = box(False, 8, valign=Gtk.Align.CENTER)
        end.set_margin_end(8)
        end.append(self._window_controls(Gtk.PackType.END))
        center.set_end_widget(end)
        return handle

    @staticmethod
    def _window_controls(side: Gtk.PackType) -> Gtk.WindowControls:
        controls = Gtk.WindowControls(side=side, valign=Gtk.Align.CENTER)
        # Пустая сторона не должна занимать место отступами.
        controls.set_visible(not controls.get_empty())
        controls.connect("notify::empty", lambda c, _p: c.set_visible(not c.get_empty()))
        return controls

    def _build_dock(self) -> Gtk.Widget:
        dock = box(False, 14, "glass", "glass-dock", halign=Gtk.Align.CENTER,
                   valign=Gtk.Align.END)
        dock.set_margin_bottom(_DOCK_BOTTOM)
        dock.set_margin_start(16)
        dock.set_margin_end(16)
        self.dock = dock
        info = box(False, 12, valign=Gtk.Align.CENTER)
        info.set_margin_start(6)
        self.dock_icon = AppIcon(42, "small")
        info.append(self.dock_icon)
        col = box(True, 1, valign=Gtk.Align.CENTER)
        self.dock_title = label("", "dock-title", ellipsize=Pango.EllipsizeMode.END, max_chars=22)
        col.append(self.dock_title)
        self.dock_sub = label("", "dock-sub", wrap=True, max_chars=34)
        col.append(self.dock_sub)
        info.append(col)
        # В узком окне подписи уходят, а значок остаётся: док — всё та же
        # стеклянная капсула, а не одинокая кнопка с кольцом.
        self.dock_text = col
        self.dock_info = info
        dock.append(info)

        self.fab = Gtk.Button()
        self.fab.add_css_class("fab")
        content = box(False, 10, halign=Gtk.Align.CENTER)
        self.fab_icon = icon("media-playback-start-symbolic", 20)
        content.append(self.fab_icon)
        self.fab_spin = spinner(20)
        self.fab_spin.set_visible(False)
        content.append(self.fab_spin)
        self.fab_label = label(tr("Запустить"), xalign=0.5, wrap=True, max_chars=30)
        self.fab_label.set_justify(Gtk.Justification.CENTER)
        content.append(self.fab_label)
        self.fab.set_child(content)
        self.fab.connect("clicked", lambda *_: self._h.play())
        dock.append(self.fab)
        self.dock_rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                                     transition_duration=220, halign=Gtk.Align.CENTER,
                                     valign=Gtk.Align.END)
        self.dock_rev.set_child(dock)
        self.dock_rev.set_reveal_child(False)
        self.dock_rev.set_can_target(False)
        return self.dock_rev

    def _build_snackbar(self) -> Gtk.Widget:
        self.snack = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                                  transition_duration=200, halign=Gtk.Align.CENTER,
                                  valign=Gtk.Align.END)
        self.snack.set_margin_bottom(100)
        self.snack.set_margin_start(24)
        self.snack.set_margin_end(24)
        bar = box(False, 8, "snackbar")
        self.snack_text = label("", wrap=True, max_chars=60, hexpand=True)
        self.snack_text.set_valign(Gtk.Align.CENTER)
        bar.append(self.snack_text)
        self.snack_button = Gtk.Button()
        self.snack_button.set_valign(Gtk.Align.CENTER)
        self.snack_button.connect("clicked", self._on_snack_button)
        bar.append(self.snack_button)
        self.snack.set_child(bar)
        self.snack.set_can_target(False)
        return self.snack

    def _build_panel(self, root: Gtk.Overlay) -> None:
        self.scrim = Gtk.Box()
        self.scrim.add_css_class("panel-scrim")
        click = Gtk.GestureClick()
        click.connect("released", lambda *_: self.close_container_panel())
        self.scrim.add_controller(click)
        self.scrim_rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                                      transition_duration=200, hexpand=True, vexpand=True)
        self.scrim_rev.set_child(self.scrim)
        self.scrim_rev.set_can_target(False)
        root.add_overlay(self.scrim_rev)

        self.panel = ContainerPanel(self._on_container, self.close_container_panel)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    propagate_natural_height=True)
        scroll.set_child(self.panel)
        self.panel_scroll = scroll
        # Когда панель листается, её край растворяется: видно, что ниже есть
        # ещё, и обрыв не выглядит поломкой.
        self.panel_fade = FadeBin(scroll)
        vadj = scroll.get_vadjustment()
        for signal in ("value-changed", "changed"):
            vadj.connect(signal, self._on_panel_scroll)
        sheet = box(True, 0, "glass", "strong", "glass-sheet")
        sheet.set_overflow(Gtk.Overflow.HIDDEN)
        sheet.append(self.panel_fade)
        self.panel_sheet = sheet
        # Высота панели — по содержимому, но не ниже края окна: в низком
        # окне она листается внутри, а не уходит за край.
        clamp = Adw.Clamp(maximum_size=780, tightening_threshold=780, hexpand=True,
                          valign=Gtk.Align.FILL)
        clamp.set_margin_start(MARGIN)
        clamp.set_margin_end(MARGIN)
        clamp.set_child(SheetBin(sheet))
        row = box(False, 0)
        # Панель встаёт по центру рабочей области — ровно под островом, а не
        # по центру окна: слева её отодвигает распорка шириной с библиотеку.
        self.panel_spacer = Gtk.Box()
        self.panel_spacer.set_size_request(MARGIN + WIDE, -1)
        row.append(self.panel_spacer)
        row.append(clamp)
        self.panel_rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.CROSSFADE,
                                      transition_duration=200, valign=Gtk.Align.FILL,
                                      halign=Gtk.Align.FILL)
        self.panel_rev.set_margin_top(MARGIN + _HEADER_H - 2)
        self.panel_rev.set_margin_bottom(MARGIN)
        self.panel_rev.set_child(row)
        self.panel_rev.set_can_target(False)
        self.panel_rev.connect("notify::child-revealed", self._on_panel_revealed)
        # Слой панели во всю высоту, поэтому щелчок «мимо» ловится и здесь:
        # всё, что не попало в саму панель, закрывает её, как и затемнение.
        outside = Gtk.GestureClick()
        outside.connect("released", self._on_panel_layer_click)
        self.panel_rev.add_controller(outside)
        root.add_overlay(self.panel_rev)

    def _on_panel_layer_click(self, _gesture, _n, x: float, y: float) -> None:
        target = self.panel_rev.pick(x, y, Gtk.PickFlags.DEFAULT)
        if target is None or not (target is self.panel_sheet
                                  or target.is_ancestor(self.panel_sheet)):
            self.close_container_panel()

    def _on_panel_scroll(self, adj: Gtk.Adjustment) -> None:
        value, page, upper = adj.get_value(), adj.get_page_size(), adj.get_upper()
        top = (0, 28) if value > 1 else (0, 0)
        bottom = (0, 40) if value + page < upper - 1 else (0, 0)
        self.panel_fade.set_edges(top, bottom)

    # ============================================================== палитра
    def _apply_palette(self, animate: bool) -> None:
        current = (self._seed, self._dark)
        if not self._slots or not animate:
            self._slots = {"slot-a": current, "slot-b": current}
            if self._sheet is not None:
                self._sheet.load(self._seed, self._dark, current, current)
            return
        # Новую палитру пишем в невидимый слой и меняем видимость верхнего:
        # прежняя атмосфера плавно уходит, новая проявляется.
        if self._b_shown:
            self._slots["slot-a"] = current
        else:
            self._slots["slot-b"] = current
        if self._sheet is not None:
            self._sheet.load(self._seed, self._dark, self._slots["slot-a"], self._slots["slot-b"])
        self._b_shown = not self._b_shown
        if self._b_shown:
            self.atmo_b.remove_css_class("faded")
        else:
            self.atmo_b.add_css_class("faded")

    def _on_dark(self, manager: Adw.StyleManager, _pspec) -> None:
        dark = manager.get_dark()
        if dark != self._dark:
            self._dark = dark
            self._apply_palette(animate=self.get_mapped())

    def _on_realize(self, *_args) -> None:
        manager = Adw.StyleManager.get_default()
        if not self._dark_handler:
            self._dark_handler = manager.connect("notify::dark", self._on_dark)
        self._on_dark(manager, None)
        # Программный отрисовщик (Cairo: старые X11, виртуальные машины без
        # GL) размывает фон каждый кадр на процессоре — включаем плотные
        # поверхности сами, не дожидаясь настройки.
        native = self.get_native()
        renderer = native.get_renderer() if native is not None else None
        self._reduced_auto = isinstance(renderer, Gsk.CairoRenderer) or _software_gl()
        self._update_reduced()

    def _on_unrealize(self, *_args) -> None:
        if self._dark_handler:
            Adw.StyleManager.get_default().disconnect(self._dark_handler)
            self._dark_handler = 0
        # Таймер сообщения не должен пережить окно: он трогал бы снятый
        # виджет и держал бы замыкание кнопки.
        if self._toast_source:
            GLib.source_remove(self._toast_source)
            self._toast_source = 0
        self._toast_callback = None

    def _update_reduced(self) -> None:
        set_flag(self.root, "reduced", self._reduced_user or self._reduced_auto)

    # ============================================================== общее
    def set_seed(self, seed: str | None) -> None:
        if seed == self._seed:
            return
        self._seed = seed
        self._apply_palette(animate=self.get_mapped())

    def set_reduced_glass(self, reduced: bool) -> None:
        self._reduced_user = bool(reduced)
        self._update_reduced()

    def toast(self, text: str, button: str = "", on_button=None, timeout: int = 4) -> None:
        self.snack_text.set_label(text)
        self.snack_button.set_label(button)
        self.snack_button.set_visible(bool(button))
        self._toast_callback = on_button
        self.snack.set_can_target(True)
        self.snack.set_reveal_child(True)
        if hasattr(self, "announce"):
            self.announce(text, Gtk.AccessibleAnnouncementPriority.MEDIUM)
        if self._toast_source:
            GLib.source_remove(self._toast_source)
        # С кнопкой сообщение держится дольше: человеку нужно успеть нажать.
        seconds = max(1, int(timeout)) + (3 if button else 0)
        self._toast_source = GLib.timeout_add_seconds(seconds, self._hide_toast)

    def _hide_toast(self) -> bool:
        self._toast_source = 0
        self.snack.set_reveal_child(False)
        self.snack.set_can_target(False)
        self._toast_callback = None
        return GLib.SOURCE_REMOVE

    def _on_snack_button(self, *_args) -> None:
        callback = self._toast_callback
        if self._toast_source:
            GLib.source_remove(self._toast_source)
        self._hide_toast()
        if callback is not None:
            callback()

    def set_updates(self, count: int, text: str = "") -> None:
        self.library.set_updates(count, text)

    # ============================================================ библиотека
    def set_library(self, items: Sequence[LibraryItem], selected: str | None) -> None:
        self.library.set_items(items, selected)

    # ============================================================== страница
    def _reset_answers(self) -> None:
        """Ответы прежнего приложения к новому не относятся."""
        self.notice.set_notice(None)
        self.windows.reset()
        self.launch.set_build(tr("проверяем…"), None)
        self._fact_overrides.clear()

    def show_welcome(self) -> None:
        self.close_container_panel()
        # Ответы, пришедшие до этого (и до первой страницы вообще), ни к
        # какой странице не относятся: следующая show_page начнёт с
        # «Проверяем окна…», а не с чужих плиток.
        self._reset_answers()
        self._page_slug = None
        self.dock_rev.set_can_target(False)
        self.dock_rev.set_reveal_child(False)
        self.crumb_icon.set_visible(False)
        self.crumb_name.set_label("Merci")
        self.stack.set_visible_child_name("welcome")

    def show_page(self, page: AppPage) -> None:
        switched = self._page_slug is not None and page.slug != self._page_slug
        if switched:
            self._reset_answers()
        if page.slug != self._page_slug:
            self.scroller.get_vadjustment().set_value(0)
        self._page_slug = page.slug
        self.hero.show(page)
        self.facts.show(page.facts, self._fact_overrides)
        self.windows.set_art(page.icon)
        self.dock_icon.set_path(page.icon)
        self.dock_title.set_label(page.name)
        self.crumb_icon.set_path(page.icon)
        self.crumb_icon.set_visible(True)
        self.crumb_name.set_label(page.name)
        self.dock_rev.set_can_target(True)
        self.dock_rev.set_reveal_child(True)
        self.stack.set_visible_child_name("page")

    def set_fact(self, key: str, value: str) -> None:
        self._fact_overrides[key] = value
        self.facts.set_fact(key, value)

    def set_build(self, text: str, ok: bool | None = None) -> None:
        self.launch.set_build(text, ok)

    def set_profiles(self, profiles: Profiles) -> None:
        self.launch.set_profiles(profiles)

    def set_notice(self, notice: Notice | None) -> None:
        self.notice.set_notice(notice)

    def set_can_uninstall(self, enabled: bool) -> None:
        self.launch.set_can_uninstall(enabled)

    # ======================================================== главное действие
    def set_action(self, label: str, busy: bool = False, enabled: bool = True) -> None:
        self.fab_label.set_label(label)
        self.fab_icon.set_visible(not busy)
        self.fab_spin.set_visible(busy)
        if busy:
            self.fab.add_css_class("busy")
        else:
            self.fab.remove_css_class("busy")
        self.fab.set_sensitive(enabled)
        accessible_label(self.fab, label)

    def set_summary(self, text: str) -> None:
        self.dock_sub.set_label(text)
        self.dock_sub.set_visible(bool(text))

    # ================================================================== окна
    def set_windows(self, tiles: Sequence[WindowTile], header: WindowsHeader) -> None:
        self.windows.set_windows(tiles, header)

    def set_windows_error(self, title: str, text: str) -> None:
        self.windows.show_error(title, text)

    def set_stage(self, text: str) -> None:
        self.windows.set_stage(text)

    # ============================================================ контейнер
    def set_container(self, kind: str, text: str) -> None:
        self.island.set_state(kind, text)
        self.panel.set_state(kind, text)

    def set_container_controls(self, can_stop: bool, stop_hint: str, can_restart: bool) -> None:
        self.panel.set_controls(can_stop, stop_hint, can_restart)

    def set_bridge(self, text: str, alert: bool = False, visible: bool = True) -> None:
        self.panel.set_bridge(text, alert, visible)

    def set_renderer(self, text: str) -> None:
        self.panel.set_renderer(text)

    def set_window_size(self, text: str, busy: bool = False) -> None:
        self.panel.set_window_size(text, busy)
        self.windows.set_ratio(ratio_from_size(text))

    def set_quick(self, eco: bool, fps: int) -> None:
        self._quick = (bool(eco), int(fps))
        self.island.set_quick(*self._quick)
        self.panel.set_quick(*self._quick)

    def _on_container(self, action: str, value: object = None) -> None:
        # Быстрые переключатели есть и на острове, и в панели: вторая копия
        # догоняет первую молча, а логике уходит одно нажатие.
        if action == "toggle_eco":
            self._quick = (bool(value), self._quick[1])
            self.island.set_quick(*self._quick)
            self.panel.set_quick(*self._quick)
        elif action == "set_fps":
            self._quick = (self._quick[0], int(value))
            self.island.set_quick(*self._quick)
            self.panel.set_quick(*self._quick)
        self._h.container_action(action, value)

    # -- панель --
    def open_container_panel(self) -> None:
        if not self.island.expand.get_active():
            self.island.expand.set_active(True)

    def close_container_panel(self) -> None:
        if self.island.expand.get_active():
            self.island.expand.set_active(False)

    def _on_expand_toggled(self, toggle: Gtk.ToggleButton) -> None:
        opened = toggle.get_active()
        if opened == self._panel_open:
            return
        self._panel_open = opened
        toggle.set_icon_name("pan-up-symbolic" if opened else "pan-down-symbolic")
        self.scrim_rev.set_can_target(opened)
        self.scrim_rev.set_reveal_child(opened)
        if not opened:
            self.panel.discard_size_edit()
        self.panel_rev.set_can_target(opened)
        # Пока панель открыта, остальное окно не берёт фокус: Tab ходит
        # только по панели, как в модальном диалоге.
        self.body.set_can_focus(not opened)
        self.panel_rev.set_reveal_child(opened)
        if opened:
            self.panel.focus_first()
        else:
            self.island.expand.grab_focus()

    def _on_panel_revealed(self, revealer: Gtk.Revealer, _pspec) -> None:
        if revealer.get_child_revealed():
            self.panel.focus_first()

    def _on_escape(self, *_args) -> bool:
        if self._panel_open:
            self.close_container_panel()
            return True
        return False

    # ============================================================ раскладка
    def _set_compact(self, compact: bool) -> None:
        self._compact = compact
        self.library.set_compact(compact)
        self.windows.set_compact(compact)
        self.panel.set_compact(compact)
        self.island.set_compact(compact)
        self.cards.set_orientation(Gtk.Orientation.VERTICAL if compact else
                                   Gtk.Orientation.HORIZONTAL)
        self.dock_text.set_visible(not compact)
        self.panel_spacer.set_size_request(MARGIN + (COMPACT if compact else WIDE), -1)
