"""Раздел «Окна»: ряд «экранов», полоса долгой операции, общие действия.

Каждое окно — отдельный Android, и плитка рисует его как маленький экран
той же пропорции, что задана окнам в контейнере. Состояние видно четырежды:
метка со значком и словом на экране, рисунок экрана (значок приложения,
приглушённый, с бейджем, в кольце хода, под штриховкой), полный текст
под экраном и рамка.

``set_windows`` приходит каждые несколько секунд, поэтому плитки живут
долго: обновляются по номеру окна на месте и пересоздаются только при смене
набора окон. Так не мигает экран и не закрывается открытое меню «⋮».
"""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Sequence

from gi.repository import Gio, GLib, Gtk, Pango

from ..i18n import tr
from . import api
from .api import WindowsHeader, WindowTile
from .widgets import (AppIcon, AspectBin, Collapse, WavyIndicator, accessible_label, box, button,
                      clear, icon, label, menu_button, set_classes, set_flag, spinner)

# Как выглядит состояние: значок и короткое слово на «экране».
_LOOK = {
    api.WINDOW_OPEN: ("media-playback-start-symbolic", "Открыто"),
    api.WINDOW_BACKGROUND: ("view-conceal-symbolic", "В фоне"),
    api.WINDOW_IDLE: ("view-app-grid-symbolic", "Закрыто"),
    api.WINDOW_ABSENT: ("folder-download-symbolic", "Не установлено"),
    api.WINDOW_OFF: ("system-shutdown-symbolic", "Выключено"),
    api.WINDOW_BOOTING: ("network-wireless-acquiring-symbolic", "Загрузка"),
    api.WINDOW_UNKNOWN: ("dialog-question-symbolic", "Ждём ответа"),
    api.WINDOW_DEAD: ("computer-fail-symbolic", "Не отвечает"),
    api.WINDOW_FOREIGN: ("software-update-available-symbolic", "Другая сборка"),
    api.WINDOW_BUSY: ("emblem-system-symbolic", "Операция"),
}

# Подача значка приложения на экране плитки: (вид, глиф бейджа в углу).
#   plain — как есть; dim — без цвета и полупрозрачный (окно закрыто или
#   выключено); faint — чуть приглушён (ответа ещё нет); badge — с бейджем
#   (приложения здесь нет или стоит другая сборка); ring — кольцо хода;
#   hatch — под штриховкой ошибки.
_ART_LOOK = {
    api.WINDOW_OPEN: ("plain", ""),
    api.WINDOW_BACKGROUND: ("plain", ""),
    api.WINDOW_IDLE: ("dim", ""),
    api.WINDOW_OFF: ("dim", ""),
    api.WINDOW_ABSENT: ("badge", "go-down-symbolic"),
    api.WINDOW_FOREIGN: ("badge", "view-refresh-symbolic"),
    api.WINDOW_BOOTING: ("ring", ""),
    api.WINDOW_BUSY: ("ring", ""),
    api.WINDOW_UNKNOWN: ("faint", "dialog-question-symbolic"),
    api.WINDOW_DEAD: ("hatch", "dialog-error-symbolic"),
}
_ART = 48    # значок приложения на экране плитки
_SLOT = 56   # место под него вместе с кольцом хода и бейджем

# Состояния, в которых метка на экране говорит всё сама: строка «приложение
# открыто» под «Открыто» лишь повторяет её.
_SELF_EXPLAINED = (api.WINDOW_OPEN, api.WINDOW_OFF)

# Пункты меню «⋮» плитки — в порядке показа; удаление отдельной группой.
_MENU = (
    ("resize", "Размер окон…"),
    ("restart", "Перезапустить окно"),
    ("power_off", "Выключить окно"),
)
_MENU_DANGER = (("remove", "Удалить окно…"),)


def ratio_from_size(text: str) -> float:
    """Пропорция «экрана» плитки из размера окон («1920x1080»).

    Ограничена разумными пределами: портретный 1080x1920 дал бы плитку
    выше соседних карточек, а сверхширокий — полоску, на которой не
    помещаются метка и кнопки.
    """
    try:
        width, height = (int(part) for part in text.lower().replace("×", "x").split("x", 1))
        ratio = width / height
    except (ValueError, ZeroDivisionError):
        return 16 / 9
    return min(2.2, max(1.0, ratio))


class _TileView(Gtk.FlowBoxChild):
    """Плитка одного окна. Обновляется на месте по новым данным."""

    def __init__(self, number: int, on_action: Callable[[str, int], None]) -> None:
        super().__init__()
        # Фокус получают кнопки внутри, сама ячейка — нет: иначе Tab
        # останавливался бы на каждой плитке без всякого действия.
        self.set_focusable(False)
        self.number = number
        self.tile: WindowTile | None = None
        self._on_action = on_action
        self._center_kind = ""
        self._art_path = ""
        self.art: AppIcon | None = None
        self._menu_items: tuple[str, ...] | None = None

        self.card = box(True, 8, "screen-tile", accessible_role=Gtk.AccessibleRole.GROUP)
        self.set_child(self.card)

        self.screen = Gtk.Overlay()
        self.screen.add_css_class("screen")
        # Рисунок экрана центрируется в части под меткой состояния: на узкой
        # плитке длинная метка («Не отвечает») иначе наезжала бы на него.
        self.center = box(False, 0, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.center.set_margin_top(30)
        self.center.set_margin_bottom(2)
        self.screen.set_child(self.center)

        self.badge = box(False, 5, "screen-badge", halign=Gtk.Align.START, valign=Gtk.Align.START)
        self.badge.set_margin_top(8)
        self.badge.set_margin_start(8)
        self.badge_icon = icon("", 14)
        self.badge.append(self.badge_icon)
        self.badge_text = label("", xalign=0.0)
        self.badge.append(self.badge_text)
        self.screen.add_overlay(self.badge)

        # Главное действие — в правом нижнем углу экрана, «закрыть» — в левом:
        # разнесённые по углам кнопки не наезжают на значок посередине даже
        # на узком экране.
        self.btn_restart = button(None, "view-refresh-symbolic", "screen-btn", "danger",
                                  tooltip=tr("Перезапустить окно"),
                                  on_click=lambda: self._fire("restart"))
        self.btn_close = button(None, "media-playback-stop-symbolic", "screen-btn",
                                tooltip=tr("Закрыть приложение"),
                                on_click=lambda: self._fire("close"))
        self.btn_open = button(None, "media-playback-start-symbolic", "screen-btn", "primary",
                               tooltip=tr("Открыть приложение"),
                               on_click=lambda: self._fire("open"))
        for widget, align in ((self.btn_close, Gtk.Align.START), (self.btn_open, Gtk.Align.END),
                              (self.btn_restart, Gtk.Align.END)):
            widget.set_halign(align)
            widget.set_valign(Gtk.Align.END)
            widget.set_margin_start(8)
            widget.set_margin_end(8)
            widget.set_margin_bottom(8)
            self.screen.add_overlay(widget)
        self.aspect = AspectBin(self.screen, min_width=128, natural_width=150)
        self.card.append(self.aspect)

        # Подпись под экраном: название и «⋮» строкой, ниже роль и полный
        # текст состояния. Роль не в одной строке с названием нарочно: так
        # плитка остаётся узкой и в ряд встают четыре экрана.
        foot = box(True, 4)
        foot.set_margin_start(6)
        title_row = box(False, 4)
        self.title = label("", "tile-title", ellipsize=Pango.EllipsizeMode.END, hexpand=True)
        self.title.set_valign(Gtk.Align.CENTER)
        title_row.append(self.title)
        self.menu = menu_button("view-more-symbolic", tr("Окно: действия"), "icon-btn", "small")
        self.menu.set_valign(Gtk.Align.CENTER)
        title_row.append(self.menu)
        foot.append(title_row)
        # Роль — короткая метка в одну строку, без переноса и многоточия.
        # Показывается только у основного окна: «свой Android» на каждой
        # плитке ничего не сообщает (это сказано в подзаголовке раздела).
        self.role = label("", "tile-role", xalign=0.5)
        self.role.set_halign(Gtk.Align.START)
        foot.append(self.role)
        # Полный текст состояния: обрезанная многоточием фраза («окна на
        # эк…») теряла как раз смысл. Плитки в ряду равной высоты, поэтому
        # предел всё же есть — четыре строки; целиком текст ещё и во
        # всплывающей подсказке и у читалки экрана.
        self.state_text = label("", "tile-state", wrap=True, max_chars=14, lines=4,
                                ellipsize=Pango.EllipsizeMode.END)
        self.state_text.set_margin_end(6)
        foot.append(self.state_text)
        self.card.append(foot)

    def _fire(self, action: str) -> None:
        self._on_action(action, self.number)

    def set_ratio(self, ratio: float) -> None:
        self.aspect.set_ratio(ratio)

    def set_art(self, path: str) -> None:
        self._art_path = path
        if self.art is not None:
            self.art.set_path(path)

    def _set_center(self, look: str, mark: str = "") -> None:
        """Значок приложения посреди экрана и подача состояния вокруг него.

        Значок есть всегда: плитка — «экран этого приложения» в любом
        состоянии. Само состояние названо в метке сверху, а здесь оно видно
        подачей: приглушённый значок, бейдж в углу, кольцо хода, штриховка.
        Пересобирается только при смене подачи.
        """
        key = f"{look}:{mark}"
        if key == self._center_kind:
            return
        self._center_kind = key
        clear(self.center)
        ring = look == "ring"
        # Значок держим полем: обёртку Python, которую держит только GTK,
        # сборщик мог бы отпустить, и с ней — её состояние.
        self.art = AppIcon(40 if ring else _ART, "screen-art")
        if look in ("dim", "faint"):
            self.art.add_css_class(look)
        self.art.set_path(self._art_path)
        frame = Gtk.Overlay(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        slot = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        slot.set_size_request(_SLOT, _SLOT)
        frame.set_child(slot)
        art = self.art
        art.set_halign(Gtk.Align.CENTER)
        art.set_valign(Gtk.Align.CENTER)
        if ring:
            # Кольцо хода вокруг значка — «Загрузка» и «Операция».
            ring_spin = spinner(_SLOT)
            ring_spin.add_css_class("art-ring")
            frame.add_overlay(ring_spin)
        frame.add_overlay(art)
        if look == "hatch":
            # «Не отвечает»: значок под штриховкой ошибки.
            hatch = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, can_target=False)
            hatch.add_css_class("art-hatch")
            hatch.set_size_request(_ART, _ART)
            frame.add_overlay(hatch)
        if mark:
            badge = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.END, can_target=False,
                            hexpand=False)
            badge.add_css_class("art-badge")
            badge.add_css_class(look)
            glyph = icon(mark, 12)
            glyph.set_hexpand(True)
            glyph.set_halign(Gtk.Align.CENTER)
            badge.append(glyph)
            frame.add_overlay(badge)
        self.center.append(frame)

    def update(self, tile: WindowTile) -> None:
        if tile == self.tile:
            return
        self.tile = tile
        state = tile.state if tile.state in _LOOK else api.WINDOW_UNKNOWN
        set_classes(self.card, "st-", f"st-{state}")
        glyph, short = _LOOK[state]
        self.badge_icon.set_from_icon_name(glyph)
        self.badge_text.set_label(tr(short))
        self._set_center(*_ART_LOOK.get(state, ("faint", "dialog-question-symbolic")))

        busy = state == api.WINDOW_BUSY
        actions = () if busy else tuple(tile.actions)
        self.btn_open.set_visible("open" in actions)
        self.btn_close.set_visible("close" in actions)
        # Перезапуск выносится на экран только там, где он — главный выход.
        self.btn_restart.set_visible("restart" in actions and state == api.WINDOW_DEAD)
        # Доступное имя — вместе с названием окна: у четырёх плиток четыре
        # кнопки «Открыть», и читалке нужно сказать, какая из них какая.
        tips = (
            (self.btn_open, tr("Показать приложение") if state == api.WINDOW_BACKGROUND
             else tr("Открыть приложение")),
            (self.btn_close, tr("Закрыть приложение")),
            (self.btn_restart, tr("Перезапустить окно")),
        )
        for widget, tip in tips:
            widget.set_tooltip_text(tip)
            accessible_label(widget, f"{tile.title}: {tip}")

        self.title.set_label(tile.title)
        self.role.set_label(tile.role)
        self.role.set_visible(bool(tile.role) and tile.main)
        set_flag(self.role, "main", tile.main)
        self.state_text.set_label(tile.text)
        self.state_text.set_tooltip_text(tile.text or None)
        redundant = (state in _SELF_EXPLAINED and
                     tile.text.strip(" .…").casefold().endswith(tr(short).casefold()))
        self.state_text.set_visible(bool(tile.text) and not redundant)
        self.menu.set_tooltip_text(tr("{title}: действия", title=tile.title))
        accessible_label(self.menu, tr("{title}: действия", title=tile.title))
        role = f", {tile.role}" if tile.role else ""
        accessible_label(self.card, f"{tile.title}{role}: {tile.text}")

        menu_items = tuple(a for a, _ in _MENU + _MENU_DANGER if a in actions)
        if menu_items != self._menu_items:
            # Модель меню меняется только вместе с набором действий: замена
            # модели закрыла бы меню, открытое человеком прямо сейчас.
            self._menu_items = menu_items
            self.menu.set_menu_model(self._menu_model(menu_items))
        self.menu.set_sensitive(bool(menu_items))

    def _menu_model(self, items: Sequence[str]) -> Gio.Menu:
        menu = Gio.Menu()
        main = Gio.Menu()
        danger = Gio.Menu()
        for action, text in _MENU:
            if action in items:
                main.append_item(self._item(text, action))
        for action, text in _MENU_DANGER:
            if action in items:
                danger.append_item(self._item(text, action))
        menu.append_section(None, main)
        menu.append_section(None, danger)
        return menu

    def _item(self, text: str, action: str) -> Gio.MenuItem:
        item = Gio.MenuItem.new(tr(text), None)
        item.set_action_and_target_value("tiles.act", GLib.Variant("(si)", (action, self.number)))
        return item


class _GhostTile(Gtk.FlowBoxChild):
    """Пунктирная плитка «Ещё окно» в конце ряда."""

    def __init__(self, on_add: Callable[[], None]) -> None:
        super().__init__()
        self.set_focusable(False)
        add = Gtk.Button(tooltip_text=tr("Добавить окно — ещё один Android"))
        add.add_css_class("ghost-tile")
        add.connect("clicked", lambda *_: on_add())
        content = box(True, 6, valign=Gtk.Align.CENTER)
        content.append(icon("list-add-symbolic", 28))
        content.append(label(tr("Ещё окно"), "ghost-title", xalign=0.5))
        sub = label(tr("отдельный Android со своим аккаунтом"), "ghost-sub", xalign=0.5, wrap=True,
                    max_chars=16)
        sub.set_justify(Gtk.Justification.CENTER)
        content.append(sub)
        add.set_child(content)
        self.set_child(add)
        self.button = add


class _Flow(Gtk.FlowBox):
    """Ряд плиток, который сообщает, сколько их встало в строку на деле.

    FlowBox решает это сам по ширине и естественному размеру плиток; число
    нужно, чтобы пунктирная «Ещё окно» дописывала неполный ряд, а не
    начинала новый в одиночку.
    """

    __gtype_name__ = "MerciTileFlow"

    def __init__(self, on_layout: Callable[[int], None], **props) -> None:
        super().__init__(**props)
        self._on_layout = on_layout

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        Gtk.FlowBox.do_size_allocate(self, width, height, baseline)
        self._on_layout(width)


class WindowsSection(Gtk.Box):
    """Стеклянная панель «Окна»."""

    def __init__(self, on_window: Callable[[str, int], None],
                 on_section: Callable[[str], None]) -> None:
        # Зазоры — отступами детей, а не spacing коробки: закрытая полоса
        # хода иначе оставляла бы двойной зазор между шапкой и плитками.
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("glass")
        self.add_css_class("glass-panel")
        self.add_css_class("windows-panel")
        self._on_window = on_window
        self._on_section = on_section
        self._tiles: dict[int, _TileView] = {}
        self._order: list[int] = []
        self._header = WindowsHeader(summary="", can_add=False, can_launch_all=False,
                                     can_stop_all=False, can_power_off_all=False)
        self._error = False
        self._compact = False
        self._ratio = 16 / 9
        self._art = ""
        self._menu_key: tuple[bool, bool] | None = None
        self._per_line = 4
        self._ghost_idle = 0

        # Действия раздела и плиток — через группу действий: так пункты
        # меню «⋮» получают состояние «недоступно» тем же механизмом GTK,
        # что и кнопки, и работают с клавиатуры как обычное меню.
        group = Gio.SimpleActionGroup()
        act = Gio.SimpleAction.new("act", GLib.VariantType.new("(si)"))
        act.connect("activate", self._on_tile_action)
        group.add_action(act)
        self._section_actions: dict[str, Gio.SimpleAction] = {}
        for name in api.SECTION_ACTIONS:
            action = Gio.SimpleAction.new(name.replace("_", "-"), None)
            action.connect("activate", lambda _a, _p, n=name: self._on_section(n))
            group.add_action(action)
            self._section_actions[name] = action
        self.insert_action_group("tiles", group)

        # Сводка — отдельной строкой во всю ширину под заголовком и
        # действиями: рядом с тремя кнопками ей не хватало места, и она
        # ломалась на две строки даже в широком окне.
        head = box(True, 2)
        head.set_margin_bottom(16)
        top = box(False, 8)
        title = label(tr("Окна"), "section-title", hexpand=True)
        title.set_valign(Gtk.Align.CENTER)
        top.append(title)
        head.append(top)
        self.summary = label("", "section-sub", wrap=True)
        head.append(self.summary)
        acts = box(False, 4, valign=Gtk.Align.CENTER)
        self.btn_launch = button(tr("Запустить все"), "media-playback-start-symbolic", "m3", "text",
                                 "small", on_click=lambda: self._on_section("launch_all"))
        self.btn_stop = button(tr("Остановить все"), "media-playback-stop-symbolic", "m3", "text",
                               "small", on_click=lambda: self._on_section("stop_all"))
        self.btn_add = button(tr("Окно"), "list-add-symbolic", "m3", "tonal", "small",
                              tooltip=tr("Добавить окно — ещё один Android"),
                              on_click=lambda: self._on_section("add"))
        self.more = menu_button("view-more-symbolic", tr("Действия с окнами"), "icon-btn")
        for widget in (self.btn_launch, self.btn_stop, self.btn_add, self.more):
            acts.append(widget)
        top.append(acts)
        self.append(head)

        # Полоса долгой операции.
        strip = box(True, 6, "op-strip")
        top = box(False, 10)
        top.append(icon("emblem-system-symbolic", 16))
        self.stage_text = label("", "op-text", wrap=True, hexpand=True)
        top.append(self.stage_text)
        strip.append(top)
        self.wavy = WavyIndicator()
        strip.append(self.wavy)
        self.stage = Collapse(strip, gap_after=14)
        self.append(self.stage)

        # Плитки либо сообщение («проверяем…», ошибка чтения).
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE,
                               transition_duration=180, vhomogeneous=False, interpolate_size=True)
        self.flow = _Flow(self._on_flow_layout, homogeneous=True, min_children_per_line=2,
                          max_children_per_line=4, column_spacing=12, row_spacing=12,
                          selection_mode=Gtk.SelectionMode.NONE, valign=Gtk.Align.START)
        self.flow.add_css_class("tiles")
        self.flow.set_activate_on_single_click(False)
        self.stack.add_named(self.flow, "tiles")
        self.status = box(True, 8, "windows-status", halign=Gtk.Align.CENTER)
        self.status_icon_slot = box(False, 0, halign=Gtk.Align.CENTER)
        self.status.append(self.status_icon_slot)
        self.status_title = label("", "status-title", xalign=0.5, wrap=True)
        self.status_title.set_justify(Gtk.Justification.CENTER)
        self.status.append(self.status_title)
        self.status_text = label("", "status-text", xalign=0.5, wrap=True, max_chars=60)
        self.status_text.set_justify(Gtk.Justification.CENTER)
        self.status.append(self.status_text)
        # «Повторить» — главный выход из ошибки чтения: текст и так просит
        # попробовать снова, без кнопки это пришлось бы делать наугад.
        self.btn_retry = button(tr("Повторить"), "view-refresh-symbolic", "m3", "tonal", "small",
                                on_click=lambda: self._on_section("retry"))
        self.btn_retry.set_halign(Gtk.Align.CENTER)
        self.btn_retry.set_margin_top(6)
        self.btn_retry.set_visible(False)
        self.status.append(self.btn_retry)
        # Ошибка чтения: техническая строка (ответ adb) — свёрнутой, мелким
        # моноширинным шрифтом; сам заголовок говорит, что случилось.
        self.details = Gtk.Expander(label=tr("Подробности"), halign=Gtk.Align.CENTER)
        self.details.add_css_class("status-details")
        self.details_text = label("", "details-text", wrap=True, max_chars=64)
        self.details_text.set_selectable(True)
        self.details_text.set_margin_top(6)
        self.details.set_child(self.details_text)
        self.details.set_visible(False)
        self.status.append(self.details)
        self.stack.add_named(self.status, "status")
        self.append(self.stack)
        self.ghost = _GhostTile(lambda: self._on_section("add"))
        # До первого ответа логики — «Проверяем окна…», а не пустая панель.
        self.show_checking()

    # -- состояние «ещё не знаем» и ошибка --
    def show_checking(self) -> None:
        self._set_status("spin", tr("Проверяем окна…"), "")
        self.set_header(WindowsHeader(summary="", can_add=False, can_launch_all=False,
                                      can_stop_all=False, can_power_off_all=False))

    def show_error(self, title: str, text: str) -> None:
        self._set_status("dialog-warning-symbolic", title, "")
        self.details_text.set_label(text)
        self.details.set_expanded(False)
        self.details.set_visible(bool(text))
        self.btn_retry.set_visible(True)
        self.status.add_css_class("error")
        # Окон не прочитали — действия над ними выглядели бы доступными зря.
        self._error = True
        self._apply_header()

    def _set_status(self, glyph: str, title: str, text: str) -> None:
        self.status.remove_css_class("error")
        self.details.set_visible(False)
        self.btn_retry.set_visible(False)
        clear(self.status_icon_slot)
        if glyph == "spin":
            self.status_icon_slot.append(spinner(32))
        else:
            self.status_icon_slot.append(icon(glyph, 32, "status-glyph"))
        self.status_title.set_label(title)
        self.status_text.set_label(text)
        self.status_text.set_visible(bool(text))
        self.stack.set_visible_child_name("status")

    # -- данные --
    def set_windows(self, tiles: Sequence[WindowTile], header: WindowsHeader) -> None:
        self._error = False
        order = [tile.number for tile in tiles]
        if order != self._order:
            # Набор окон сменился — пересобираем ряд, но существующие
            # плитки переиспользуем: их рисунок не мигает.
            for child in list(self._iter_children()):
                self.flow.remove(child)
            fresh: dict[int, _TileView] = {}
            for tile in tiles:
                view = self._tiles.get(tile.number) or _TileView(tile.number, self._on_window)
                view.set_ratio(self._ratio)
                view.set_art(self._art)
                fresh[tile.number] = view
                self.flow.append(view)
            self._tiles = fresh
            self._order = order
            self.flow.append(self.ghost)
        for tile in tiles:
            self._tiles[tile.number].update(tile)
        self.stack.set_visible_child_name("tiles" if tiles else "status")
        if not tiles:
            self._set_status("view-app-grid-symbolic", tr("Окон пока нет"), "")
        self.set_header(header)

    def _iter_children(self):
        child = self.flow.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            yield child
            child = following

    def set_header(self, header: WindowsHeader) -> None:
        self._header = header
        self._apply_header()

    def _apply_header(self) -> None:
        h = self._header
        if self._error:
            h = replace(h, can_add=False, can_launch_all=False, can_stop_all=False,
                        can_power_off_all=False)
        self.summary.set_label(h.summary)
        self.summary.set_visible(bool(h.summary))
        enabled = {
            "add": h.can_add and not h.busy,
            "launch_all": h.can_launch_all and not h.busy,
            "stop_all": h.can_stop_all and not h.busy,
            "power_off_all": h.can_power_off_all and not h.busy,
            # «Повторить» нужен ровно тогда, когда окна прочитать не удалось.
            "retry": self._error,
        }
        for name, action in self._section_actions.items():
            # Действие, о котором вид ещё не знает (договор дополнили), —
            # недоступно, а не ошибка раскладки.
            action.set_enabled(enabled.get(name, False))
        self.btn_launch.set_sensitive(enabled["launch_all"])
        self.btn_stop.set_sensitive(enabled["stop_all"])
        self.btn_add.set_sensitive(enabled["add"])
        self._update_ghost()
        self.ghost.button.set_sensitive(enabled["add"])
        self.btn_launch.set_visible(not self._compact)
        self.btn_stop.set_visible(not self._compact)

        menu = Gio.Menu()
        if self._compact:
            menu.append(tr("Запустить все"), "tiles.launch-all")
            menu.append(tr("Остановить все"), "tiles.stop-all")
        if h.can_power_off_all:
            menu.append(tr("Выключить дополнительные окна"), "tiles.power-off-all")
        has_items = menu.get_n_items() > 0
        # Меню пересобирается только при смене состава, иначе раз в
        # несколько секунд закрывалось бы у человека под рукой.
        key = (self._compact, h.can_power_off_all)
        if self._menu_key != key:
            self._menu_key = key
            self.more.set_menu_model(menu if has_items else None)
        self.more.set_visible(has_items)

    # -- «Ещё окно» --
    def _update_ghost(self) -> None:
        # «Ещё окно» дописывает неполный ряд; полный ряд не начинаем
        # одинокой пунктирной плиткой — для этого есть «+ Окно» в шапке.
        count = len(self._order)
        show = (self._header.can_add and not self._error and count > 0
                and count % max(1, self._per_line) != 0)
        if self.ghost.get_visible() != show:
            self.ghost.set_visible(show)

    def _on_flow_layout(self, width: int) -> None:
        first = self._tiles.get(self._order[0]) if self._order else None
        if first is None or not first.get_mapped():
            return
        tile_width = first.get_width()
        if tile_width <= 0:
            return
        spacing = self.flow.get_column_spacing()
        per_line = max(1, int((width + spacing + tile_width // 2) // (tile_width + spacing)))
        if per_line != self._per_line:
            self._per_line = per_line
            # Видимость меняем вне раскладки: GTK не любит перестройку
            # посреди size_allocate.
            if not self._ghost_idle:
                self._ghost_idle = GLib.idle_add(self._ghost_after_layout)

    def _ghost_after_layout(self) -> bool:
        self._ghost_idle = 0
        self._update_ghost()
        return GLib.SOURCE_REMOVE

    def _on_tile_action(self, _action, param: GLib.Variant) -> None:
        name, number = param.unpack()
        self._on_window(name, number)

    def set_stage(self, text: str) -> None:
        if text:
            self.stage_text.set_label(text)
        self.stage.set_reveal_child(bool(text))

    def set_ratio(self, ratio: float) -> None:
        self._ratio = ratio
        for view in self._tiles.values():
            view.set_ratio(ratio)

    def set_art(self, path: str) -> None:
        self._art = path
        for view in self._tiles.values():
            view.set_art(path)

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        self.flow.set_max_children_per_line(2 if compact else 4)
        self._apply_header()

    def reset(self) -> None:
        """Новое приложение: прежние плитки к нему не относятся."""
        for child in list(self._iter_children()):
            self.flow.remove(child)
        self._tiles.clear()
        self._order = []
        self._error = False
        self.set_stage("")
        self.show_checking()

    def do_unrealize(self) -> None:
        if self._ghost_idle:
            GLib.source_remove(self._ghost_idle)
            self._ghost_idle = 0
        Gtk.Box.do_unrealize(self)
