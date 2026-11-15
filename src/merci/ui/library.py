"""Библиотека: стеклянный список приложений слева.

Именно список с названиями и версиями, а не сетка значков: у пользователя
бывает пять сборок одного приложения с одинаковыми значками, и различить их
можно только по подписи. В узком окне список сворачивается в рейку из
значков — подписи уходят во всплывающие подсказки.
"""

from __future__ import annotations

from typing import Callable, Sequence

from gi.repository import Gio, Gtk, Pango

from ..i18n import language, tr
from .api import LibraryItem
from .widgets import AppIcon, accessible_label, box, button, icon, label

WIDE = 288      # ширина развёрнутой библиотеки
COMPACT = 76    # ширина рейки
MARGIN = 12     # отступ стекла от краёв окна


def plural(n: int, one: str, few: str, many: str) -> str:
    """Форма слова по числу: русские три формы или английские две.

    Сами формы переводятся по отдельности (``tr``), а правило выбора у
    языков своё: в английском всё, кроме единицы, — множественное.
    """
    if language() == "en":
        return tr(one) if n == 1 else tr(many)
    if n % 10 == 1 and n % 100 != 11:
        return tr(one)
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return tr(few)
    return tr(many)


class _Row(Gtk.ListBoxRow):
    """Строка библиотеки: значок, название, версия, метка архитектуры."""

    def __init__(self, item: LibraryItem) -> None:
        super().__init__()
        self.slug = item.slug
        self.item: LibraryItem | None = None
        content = box(False, 12)
        self.icon_view = AppIcon(44)
        overlay = Gtk.Overlay(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        overlay.set_child(self.icon_view)
        # В рейке подписей нет, поэтому «открыто в окнах» — точкой на значке.
        self.dot = label("", "run-dot", xalign=0.5)
        self.dot.set_halign(Gtk.Align.END)
        self.dot.set_valign(Gtk.Align.START)
        self.dot.set_visible(False)
        overlay.add_overlay(self.dot)
        content.append(overlay)

        self.text = box(True, 3, hexpand=True, valign=Gtk.Align.CENTER)
        self.name = label("", "lib-name", ellipsize=Pango.EllipsizeMode.END)
        self.text.append(self.name)
        meta = box(False, 6)
        self.version = label("", "lib-ver", ellipsize=Pango.EllipsizeMode.END)
        self.version.set_hexpand(False)
        meta.append(self.version)
        self.badge = label("", "arch-chip", xalign=0.5)
        self.badge.set_valign(Gtk.Align.CENTER)
        meta.append(self.badge)
        self.text.append(meta)
        content.append(self.text)

        self.running = box(False, 4, "run-badge", valign=Gtk.Align.CENTER)
        self.running.append(icon("media-playback-start-symbolic", 10))
        self.running_count = label("", xalign=0.5)
        self.running.append(self.running_count)
        content.append(self.running)
        self.set_child(content)
        self.update(item)

    def update(self, item: LibraryItem) -> None:
        if item == self.item:
            return
        self.item = item
        self.icon_view.set_path(item.icon)
        self.name.set_label(item.name)
        self.version.set_label(item.version)
        self.badge.set_label(item.badge)
        self.badge.set_tooltip_text(item.badge_hint or None)
        # Метка отличается и формой, а не только цветом: «ARM» — залитая,
        # «родная» архитектура — контурная.
        self.badge.remove_css_class("arm" if item.native else "native")
        self.badge.add_css_class("native" if item.native else "arm")
        running = item.open_windows
        self.running.set_visible(running > 0 and not self.has_css_class("compact"))
        self.running_count.set_label(str(running))
        self.dot.set_label(str(running))
        self.dot.set_visible(running > 0 and self.has_css_class("compact"))
        opened = ""
        if running:
            opened = tr("открытых окон: {n}", n=running)
            self.running.set_tooltip_text(opened)
        lines = [item.name, f"{item.version} · {item.badge}"]
        if opened:
            lines.append(opened)
        self.set_tooltip_text("\n".join(lines))
        accessible_label(self, ", ".join(lines))

    def set_current(self, current: bool) -> None:
        if current == self.has_css_class("current"):
            return
        if current:
            self.add_css_class("current")
        else:
            self.remove_css_class("current")
        # Значение — целым числом: так его ждёт GTK (bool даёт предупреждение).
        self.update_state([Gtk.AccessibleState.SELECTED], [1 if current else 0])

    def set_compact(self, compact: bool) -> None:
        if compact:
            self.add_css_class("compact")
        else:
            self.remove_css_class("compact")
        self.text.set_visible(not compact)
        running = self.item.open_windows if self.item else 0
        self.running.set_visible(running > 0 and not compact)
        self.dot.set_visible(running > 0 and compact)


class Library(Gtk.Box):
    """Стеклянная боковая панель: заголовок с меню, список, «Добавить APK»."""

    def __init__(self, menu: Gio.MenuModel | None, on_select: Callable[[str], None],
                 on_add: Callable[[], None],
                 on_updates: Callable[[], None] | None = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.add_css_class("glass")
        self.add_css_class("glass-sidebar")
        self.set_margin_start(MARGIN)
        self.set_margin_top(MARGIN)
        self.set_margin_bottom(MARGIN)
        self.set_size_request(WIDE, -1)
        # Растяжение строк списка (подписи с hexpand) иначе всплывало бы до
        # библиотеки, и она делила бы свободное место с рабочей областью:
        # ширина гуляла от языка и длины имён, а не держалась WIDE.
        self.set_hexpand(False)
        self._on_select = on_select
        self._quiet = False
        self._compact = False
        self._rows: dict[str, _Row] = {}
        self._order: list[str] = []
        # Ключ показанной записи: её строка отмечена классом «current».
        self._current: str | None = None
        self._empty = False

        # Шапка библиотеки — ещё и ручка окна: за неё окно таскается так же,
        # как за стеклянную шапку справа.
        handle = Gtk.WindowHandle()
        head = box(False, 6, "lib-head")
        self.titles = box(True, 1, hexpand=True, valign=Gtk.Align.CENTER)
        self.titles.append(label(tr("Библиотека"), "lib-title"))
        self.count = label("", "lib-count", ellipsize=Pango.EllipsizeMode.END)
        self.titles.append(self.count)
        head.append(self.titles)
        self.menu = Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text=tr("Меню Merci"),
                                   valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        self.menu.add_css_class("icon-btn")
        self.menu.set_primary(True)
        accessible_label(self.menu, tr("Меню Merci"))
        if menu is not None:
            self.menu.set_menu_model(menu)
        # Точка на меню — «есть обновления»: само окно обновлений логика
        # открывает из меню или по отметке внизу библиотеки.
        menu_slot = Gtk.Overlay(valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
        menu_slot.set_child(self.menu)
        self.menu_dot = _update_dot()
        menu_slot.add_overlay(self.menu_dot)
        head.append(menu_slot)
        self.head = head
        handle.set_child(head)
        self.append(handle)

        # Выбор — по нажатию (щелчок, Enter, пробел), а не по фокусу: в режиме
        # SINGLE GtkListBox выбирает строку, на которую просто пришёл фокус
        # (Tab, стрелки), и человек, проходящий окно клавиатурой, по очереди
        # открывал бы страницу каждого приложения. Показанная запись
        # отмечается своим классом и состоянием «выбрано» для читалки.
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                activate_on_single_click=True)
        self.list.add_css_class("lib-list")
        self.list.set_margin_start(10)
        self.list.set_margin_end(10)
        self.list.set_margin_bottom(6)
        accessible_label(self.list, tr("Библиотека"))
        self.empty = self._skeleton()
        self.list.set_placeholder(self.empty)
        self.list.connect("row-activated", self._on_row_activated)
        scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_child(self.list)
        self.append(scroller)

        # Отметка об обновлениях: спокойная строка-кнопка над «Добавить APK»
        # (это не ошибка — не красная и не мигает), в рейке — значок с точкой.
        on_updates = on_updates or (lambda: None)
        self.updates = Gtk.Button()
        self.updates.add_css_class("updates-row")
        row = box(False, 10)
        row.append(_update_glyph())
        self.updates_text = label("", "updates-text", ellipsize=Pango.EllipsizeMode.END,
                                  hexpand=True)
        row.append(self.updates_text)
        row.append(icon("go-next-symbolic", 14, "updates-chevron"))
        self.updates.set_child(row)
        self.updates.connect("clicked", lambda *_: on_updates())
        self.updates.set_margin_start(14)
        self.updates.set_margin_end(14)
        self.updates.set_margin_top(6)
        self.updates.set_visible(False)
        self.append(self.updates)
        # В рейке — тот же знак «стрелка вверх в круге» и счётчик на нём
        # (как у открытых окон на значках), а не шестерёнка: шестерёнка
        # везде значит «Настройки».
        self.updates_small = button(None, None, "icon-btn", "updates-btn",
                                    tooltip=tr("Обновления"), on_click=on_updates)
        self.updates_small.set_child(_update_glyph())
        self.updates_small.set_size_request(48, 48)
        small_slot = Gtk.Overlay(halign=Gtk.Align.CENTER)
        small_slot.set_child(self.updates_small)
        self.updates_count = label("", "run-dot", "updates-count", xalign=0.5)
        self.updates_count.set_halign(Gtk.Align.END)
        self.updates_count.set_valign(Gtk.Align.START)
        self.updates_count.set_can_target(False)
        small_slot.add_overlay(self.updates_count)
        small_slot.set_margin_top(6)
        small_slot.set_visible(False)
        self.updates_slot = small_slot
        self.append(small_slot)
        self._updates = 0

        self.add_wide = button(tr("Добавить APK"), "list-add-symbolic", "m3", "tonal", "add-apk",
                               on_click=on_add)
        self.add_wide.set_margin_start(14)
        self.add_wide.set_margin_end(14)
        self.add_wide.set_margin_bottom(14)
        self.add_wide.set_margin_top(6)
        self.append(self.add_wide)
        self.add_small = button(None, "list-add-symbolic", "icon-btn", "tonal",
                                tooltip=tr("Добавить APK"), on_click=on_add)
        self.add_small.set_size_request(48, 48)
        self.add_small.set_halign(Gtk.Align.CENTER)
        self.add_small.set_margin_bottom(14)
        self.add_small.set_margin_top(6)
        self.add_small.set_visible(False)
        self.append(self.add_small)

    @staticmethod
    def _skeleton() -> Gtk.Widget:
        """Пустая библиотека: «призрачные» строки и подпись под ними.

        Так видно, что здесь будет список, а сама панель не пустует на всю
        высоту. Действие «Выбрать файл…» — одно, в приёмнике посередине.
        """
        wrap = box(True, 4, "lib-skeleton")
        for fade in ("s1", "s2", "s3"):
            row = box(False, 12, "ghost-row", fade)
            plate = Gtk.Box(valign=Gtk.Align.CENTER)
            plate.add_css_class("ghost-icon")
            row.append(plate)
            bars = box(True, 7, valign=Gtk.Align.CENTER, hexpand=True)
            bar = Gtk.Box(halign=Gtk.Align.START)
            bar.add_css_class("ghost-bar")
            bar.set_size_request(120, 10)
            bars.append(bar)
            bar = Gtk.Box(halign=Gtk.Align.START)
            bar.add_css_class("ghost-bar")
            bar.set_size_request(72, 8)
            bars.append(bar)
            row.append(bars)
            wrap.append(row)
        text = label(tr("Здесь появятся добавленные APK"), "lib-empty", xalign=0.5, wrap=True)
        text.set_justify(Gtk.Justification.CENTER)
        wrap.append(text)
        return wrap

    # -- данные --
    def set_items(self, items: Sequence[LibraryItem], selected: str | None) -> None:
        """Заменяет список, сохраняя строки с прежними ключами.

        Строки не пересоздаются без нужды: иначе каждое обновление счётчика
        окон сбрасывало бы фокус клавиатуры и прокрутку списка.
        """
        self._quiet = True
        try:
            order = [item.slug for item in items]
            if order != self._order:
                clear_list(self.list)
                rows = {}
                for item in items:
                    row = self._rows.get(item.slug)
                    if row is None:
                        row = _Row(item)
                    else:
                        row.update(item)
                    row.set_compact(self._compact)
                    rows[item.slug] = row
                    self.list.append(row)
                self._rows = rows
                self._order = order
            else:
                for item in items:
                    self._rows[item.slug].update(item)
            self.select(selected)
        finally:
            self._quiet = False
        total = len(items)
        running = sum(1 for item in items if item.open_windows > 0)
        text = f"{total} {plural(total, 'приложение', 'приложения', 'приложений')}"
        if running:
            text += " · " + tr("запущено: {n}", n=running)
        self.count.set_label(text if total else tr("пока пусто"))
        self._empty = total == 0
        self._apply_add_buttons()

    def _apply_add_buttons(self) -> None:
        # Пока библиотека пуста, «Добавить» живёт только в приёмнике
        # посередине окна: две кнопки одного действия спорили бы друг с другом.
        self.add_wide.set_visible(not self._compact and not self._empty)
        self.add_small.set_visible(self._compact and not self._empty)
        has = self._updates > 0
        self.updates.set_visible(has and not self._compact)
        self.updates_slot.set_visible(has and self._compact)
        # Без кнопки «Добавить» под ней отметка сама держит нижний отступ.
        self.updates.set_margin_bottom(14 if self._empty else 0)
        self.updates_slot.set_margin_bottom(14 if self._empty else 0)
        self.menu_dot.set_visible(has)

    def set_updates(self, count: int, text: str = "") -> None:
        """Отметка «Обновления: N»; ``count == 0`` — убрать вместе с точкой."""
        self._updates = max(0, int(count))
        caption = tr("Обновления: {n}", n=self._updates)
        self.updates_text.set_label(caption)
        self.updates_count.set_label(str(self._updates) if self._updates < 10 else "9+")
        hint = text or tr("Есть обновления компонентов")
        for widget in (self.updates, self.updates_small):
            widget.set_tooltip_text(f"{caption}\n{hint}")
            accessible_label(widget, f"{caption}. {hint}")
        self._apply_add_buttons()

    def select(self, slug: str | None) -> None:
        """Программный выбор строки: обработчик выбора не зовётся."""
        row = self._rows.get(slug) if slug else None
        self._current = row.slug if row is not None else None
        self._mark_current()

    def _mark_current(self) -> None:
        for slug, row in self._rows.items():
            row.set_current(slug == self._current)

    def selected_row(self) -> "_Row | None":
        """Строка показанной записи (или None)."""
        return self._rows.get(self._current) if self._current else None

    def _on_row_activated(self, _list: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
        slug = getattr(row, "slug", None)
        if slug is None or self._quiet or slug == self._current:
            return
        self._current = slug
        self._mark_current()
        self._on_select(slug)

    # -- узкое окно --
    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self.set_size_request(COMPACT if compact else WIDE, -1)
        self.titles.set_visible(not compact)
        self.head.set_halign(Gtk.Align.CENTER if compact else Gtk.Align.FILL)
        self.list.set_margin_start(8 if compact else 10)
        self.list.set_margin_end(8 if compact else 10)
        self.empty.set_visible(not compact)
        self._apply_add_buttons()
        if compact:
            self.list.add_css_class("compact")
        else:
            self.list.remove_css_class("compact")
        for row in self._rows.values():
            row.set_compact(compact)


def _update_glyph() -> Gtk.Widget:
    """Знак обновлений: стрелка вверх в круге (своего такого значка в теме
    нет, а «software-update» в Adwaita похож на шестерёнку настроек)."""
    ring = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=False)
    ring.add_css_class("updates-glyph")
    arrow = icon("go-up-symbolic", 12)
    arrow.set_hexpand(True)
    arrow.set_halign(Gtk.Align.CENTER)
    ring.append(arrow)
    return ring


def _update_dot() -> Gtk.Widget:
    """Точка «есть обновления» в углу кнопки; нажатия проходят сквозь неё."""
    dot = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.START, can_target=False)
    dot.add_css_class("update-dot")
    dot.set_visible(False)
    return dot


def clear_list(listbox: Gtk.ListBox) -> None:
    """Убирает строки, но не заглушку пустого списка (remove_all убрал бы
    и её)."""
    while (row := listbox.get_row_at_index(0)) is not None:
        listbox.remove(row)
