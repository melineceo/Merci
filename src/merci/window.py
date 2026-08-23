"""Окно Merci: библиотека APK слева, карточка приложения справа."""

from __future__ import annotations

import os
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import apk, hostexec, waydroid  # noqa: E402
from .settings import Settings  # noqa: E402
from .i18n import LANGUAGES, set_language, tr  # noqa: E402
from .tray import MenuItem, TrayIcon  # noqa: E402
from .installer import InstallerDialog  # noqa: E402
from .library import Entry, Library, host_abi  # noqa: E402

# То же имя, что у флатпака: по нему трей ищет значок среди установленных
# иконок, а панель — среди экспортированных флатпаком.
APP_ID = "xyz.hackerstone.Merci"

_ABI_LABEL = {
    "arm64-v8a": "ARM64",
    "armeabi-v7a": "ARM32",
    "x86_64": "x86-64",
    "x86": "x86",
}


def _human_size(count: int) -> str:
    value = float(count)
    for unit in (tr("Б"), tr("КБ"), tr("МБ"), tr("ГБ")):
        if value < 1024 or unit == tr("ГБ"):
            return f"{value:.0f} {unit}" if unit in (tr("Б"), tr("КБ")) else f"{value:.1f} {unit}"
        value /= 1024
    return tr("{value} ГБ", value=f"{value:.1f}")


def _human_time(stamp: float) -> str:
    if not stamp:
        return tr("ещё не запускалось")
    delta = time.time() - stamp
    if delta < 90:
        return tr("только что")
    if delta < 3600:
        return tr("{n} мин назад", n=int(delta // 60))
    if delta < 86400:
        return tr("{n} ч назад", n=int(delta // 3600))
    return time.strftime("%d.%m.%Y", time.localtime(stamp))


_TEXTURES: dict[str, Gdk.Texture | None] = {}


def _texture(path: str) -> Gdk.Texture | None:
    """Иконки живут в кеше: список перестраивается часто, а файлы те же."""
    if path not in _TEXTURES:
        try:
            _TEXTURES[path] = Gdk.Texture.new_from_filename(path)
        except GLib.Error:
            _TEXTURES[path] = None
    return _TEXTURES[path]


def _icon_widget(entry: Entry, size: int) -> Gtk.Widget:
    texture = _texture(entry.icon_path) if os.path.exists(entry.icon_path) else None
    if texture is not None:
        picture = Gtk.Picture.new_for_paintable(texture)
        picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        picture.set_size_request(size, size)
        picture.add_css_class("merci-icon")
        picture.set_valign(Gtk.Align.CENTER)
        return picture

    placeholder = Adw.Avatar(size=size, text=entry.name, show_initials=True)
    placeholder.set_valign(Gtk.Align.CENTER)
    return placeholder


class AppRow(Gtk.ListBoxRow):
    def __init__(self, entry: Entry) -> None:
        super().__init__()
        self.entry = entry
        self.add_css_class("merci-row")

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.append(_icon_widget(entry, 40))

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text.set_valign(Gtk.Align.CENTER)
        title = Gtk.Label(label=entry.name, xalign=0)
        title.add_css_class("heading")
        title.set_ellipsize(Pango.EllipsizeMode.END)
        subtitle = Gtk.Label(label=entry.version or entry.package, xalign=0)
        subtitle.add_css_class("dim-label")
        subtitle.add_css_class("caption")
        subtitle.set_ellipsize(Pango.EllipsizeMode.END)
        text.append(title)
        text.append(subtitle)
        text.set_hexpand(True)
        box.append(text)

        # Значок говорит не «чем запустить» — запуск теперь один, — а нужна
        # ли APK трансляция: от этого зависит и скорость, и настройки.
        native = not entry.needs_native_bridge()
        badge = Gtk.Label(label=host_abi() if native else "ARM")
        badge.add_css_class("merci-badge")
        if native:
            badge.add_css_class("merci-badge-native")
        badge.set_valign(Gtk.Align.CENTER)
        badge.set_tooltip_text(
            tr("Есть код под этот процессор: пойдёт напрямую, без трансляции")
            if native
            else tr("Только ARM-код: пойдёт через трансляцию в контейнере")
        )
        box.append(badge)

        self.set_child(box)


class MerciWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title="Merci")
        self.set_default_size(980, 660)
        self.library = Library()
        self.settings = Settings()
        self.entries: list[Entry] = []
        self.selected: Entry | None = None
        self._busy = False  # идёт установка или запуск: второе нажатие не нужно
        # Окно может быть заменено (смена языка) или закрыто, пока ответы
        # фоновых запросов ещё в пути. Трогать виджеты уничтоженного окна
        # нельзя — GTK на этом ругается, а то и падает.
        self._closing = False

        self.tray = self._build_tray()
        # Со значком в трее крестик логично прячет окно, а не закрывает
        # программу: выйти можно из того же меню, где «Открыть Merci».
        self.connect("close-request", self._on_close_request)

        self.toasts = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView()
        self.split.set_min_sidebar_width(280)
        self.split.set_max_sidebar_width(340)
        self.split.set_sidebar(self._build_sidebar())
        self.split.set_content(self._build_content())
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)


        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

        self.refresh()

    # -- построение интерфейса -------------------------------------------

    def _tray_items(self) -> list[MenuItem]:
        """Пункты меню трея. Отдельно от значка: при смене языка их надо
        пересобрать, а сам значок и его регистрация остаются."""
        return [
            MenuItem(1, tr("Открыть Merci"), self.present_window),
            MenuItem(2, "", separator=True),
            MenuItem(3, tr("Открыть окно Android"), self._show_android_ui),
            MenuItem(4, tr("Открыть запущенную игру"), self._raise_running_app),
            MenuItem(5, "", separator=True),
            MenuItem(6, tr("Включить Waydroid"), self._start_waydroid),
            MenuItem(7, tr("Перезапустить Waydroid"), self._restart_waydroid),
            MenuItem(8, tr("Выключить Waydroid"), self._stop_waydroid),
            MenuItem(9, "", separator=True),
            MenuItem(10, tr("Выйти из Merci"), self._quit_app),
        ]

    def _build_tray(self) -> TrayIcon:
        """Значок в трее: левое нажатие открывает Merci, правое — это меню."""
        tray = TrayIcon(APP_ID, "Merci", self._tray_items(), self.present_window)
        tray.start()
        return tray

    def _on_close_request(self, *_args) -> bool:
        if self.tray.registered:
            self.set_visible(False)
            return True  # окно не закрываем
        return False

    def present_window(self) -> None:
        """Показать окно и поднять его поверх — из трея и после сворачивания."""
        self.set_visible(True)
        self.present()

    def hide_to_tray(self) -> None:
        """Спрятать окно. Если значка в трее нет, прятать нельзя — вернуть
        окно будет нечем, поэтому просто сворачиваем."""
        if self.tray.registered:
            self.set_visible(False)
        else:
            self.minimize()

    def _raise_running_app(self) -> None:
        """Поднять запущенную игру.

        Отдельного «показать окно» у контейнера нет: Waydroid показывает то
        приложение, которое названо в его свойстве, и обычный запуск как раз
        это свойство и ставит. Поэтому просто запускаем последнее, что
        запускали, — если оно уже работает, Android его и поднимет.
        """
        entries = sorted(self.entries, key=lambda e: e.last_run, reverse=True)
        recent = next((e for e in entries if e.last_run), None)
        if recent is None:
            self._toast(tr("Нечего открывать: ещё ничего не запускали"))
            return
        self._launch_waydroid(recent)

    def _start_waydroid(self) -> None:
        self._toast(tr("Поднимаем контейнер…"))
        hostexec.in_thread(
            waydroid.ensure_session,
            lambda _result, error: self._toast(
                tr("Не вышло: {error}", error=error) if error else tr("Контейнер запущен")
            ),
        )

    def _quit_app(self) -> None:
        self.tray.stop()
        application = self.get_application()
        if application is not None:
            application.quit()

    def _build_sidebar(self) -> Adw.NavigationPage:
        header = Adw.HeaderBar()
        add_button = Gtk.Button(icon_name="list-add-symbolic")
        add_button.set_tooltip_text(tr("Добавить APK"))
        add_button.connect("clicked", lambda *_: self.choose_apk())
        header.pack_start(add_button)

        menu = Gio.Menu()
        menu.append(tr("Подготовить Waydroid"), "win.waydroid-setup")
        menu.append(tr("Окно Android"), "win.waydroid-ui")
        menu.append(tr("Выключить Waydroid"), "win.waydroid-stop")
        menu.append(tr("Журнал сбоев Android"), "win.waydroid-crash")
        menu.append(tr("Сменить транслятор ARM"), "win.waydroid-bridge")
        menu.append(tr("Установить root (Magisk)"), "win.waydroid-magisk")
        menu.append(tr("Убрать root (Magisk)"), "win.waydroid-magisk-remove")
        menu.append(tr("Аппаратное ускорение NVIDIA"), "win.waydroid-nvidia")
        menu.append(tr("Исправить мерцание картинки"), "win.waydroid-flicker")
        menu.append(tr("Ссылки в браузере хоста"), "win.waydroid-urls")
        menu.append(tr("Настройки"), "win.settings")
        menu.append(tr("Папка библиотеки"), "app.open-library")
        menu.append(tr("О программе"), "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu))

        for name, callback in (
            ("waydroid-setup", lambda *_: self.show_installer()),
            ("waydroid-ui", lambda *_: self._show_android_ui()),
            ("waydroid-stop", lambda *_: self._stop_waydroid()),
            ("waydroid-crash", lambda *_: self._show_crash_log()),
            ("waydroid-bridge", lambda *_: self._switch_bridge()),
            ("waydroid-magisk", lambda *_: self._install_magisk()),
            ("waydroid-magisk-remove", lambda *_: self._remove_magisk()),
            ("settings", lambda *_: self._show_settings()),
            ("waydroid-nvidia", lambda *_: self._install_nvidia()),
            ("waydroid-flicker", lambda *_: self._fix_flicker()),
            ("waydroid-urls", lambda *_: self._install_urlforward()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        self.list_box = Gtk.ListBox()
        self.list_box.add_css_class("navigation-sidebar")
        self.list_box.connect("row-selected", self._on_row_selected)

        scroller = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scroller.set_child(self.list_box)

        empty = Adw.StatusPage(
            icon_name="folder-download-symbolic",
            title=tr("Пусто"),
            description=tr("Перетащите APK в окно"),
        )
        empty.add_css_class("compact")

        self.sidebar_stack = Gtk.Stack()
        self.sidebar_stack.add_named(scroller, "list")
        self.sidebar_stack.add_named(empty, "empty")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(self.sidebar_stack)
        return Adw.NavigationPage(child=box, title=tr("Библиотека"))

    def _build_content(self) -> Adw.NavigationPage:
        self.content_header = Adw.HeaderBar()
        self.content_title = Adw.WindowTitle(title="Merci", subtitle="")
        self.content_header.set_title_widget(self.content_title)

        self.welcome = Adw.StatusPage(
            icon_name="application-x-executable-symbolic",
            title=tr("Перетащите APK сюда"),
            description=(
                tr("Merci разберёт манифест и сам выберет, как запускать: "
                "APK с кодом под этот процессор идут внутри Merci, "
                "APK только под ARM — через Waydroid.")
            ),
        )
        button = Gtk.Button(label=tr("Выбрать файл…"))
        button.add_css_class("suggested-action")
        button.add_css_class("pill")
        button.set_halign(Gtk.Align.CENTER)
        button.connect("clicked", lambda *_: self.choose_apk())
        self.welcome.set_child(button)

        self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.content_stack.add_named(self.welcome, "welcome")
        self.content_stack.add_named(self._build_detail(), "detail")
        self.content_stack.set_vexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self.content_header)
        box.append(self.content_stack)
        return Adw.NavigationPage(child=box, title="Merci")

    def _build_detail(self) -> Gtk.Widget:
        self.detail_icon_slot = Gtk.Box(halign=Gtk.Align.CENTER)
        self.detail_title = Gtk.Label(xalign=0.5)
        self.detail_title.add_css_class("title-1")
        self.detail_subtitle = Gtk.Label(xalign=0.5)
        self.detail_subtitle.add_css_class("dim-label")

        self.play_button = Gtk.Button(label=tr("Запустить"))
        self.play_button.add_css_class("suggested-action")
        self.play_button.add_css_class("pill")
        self.play_button.set_halign(Gtk.Align.CENTER)
        self.play_button.connect("clicked", lambda *_: self._on_play_clicked())

        # Выключение стоит рядом с запуском: оно нужно сразу после игры, и
        # прокручивать за ним карточку неудобно.
        self.stop_button = Gtk.Button(icon_name="system-shutdown-symbolic")
        self.stop_button.set_tooltip_text(tr("Выключить Waydroid"))
        self.stop_button.add_css_class("circular")
        self.stop_button.add_css_class("flat")
        self.stop_button.set_valign(Gtk.Align.CENTER)
        self.stop_button.connect("clicked", lambda *_: self._stop_waydroid())

        # Перезапуск рядом с выключением: им лечится состояние «сессия
        # запущена, а сети у контейнера нет» — обычное дело сразу после
        # включения машины.
        self.restart_button = Gtk.Button(icon_name="view-refresh-symbolic")
        self.restart_button.set_tooltip_text(tr("Перезапустить Waydroid"))
        self.restart_button.add_css_class("circular")
        self.restart_button.add_css_class("flat")
        self.restart_button.set_valign(Gtk.Align.CENTER)
        self.restart_button.connect("clicked", lambda *_: self._restart_waydroid())

        buttons = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        buttons.append(self.play_button)
        buttons.append(self.restart_button)
        buttons.append(self.stop_button)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        hero.set_margin_top(28)
        hero.set_margin_bottom(8)
        hero.append(self.detail_icon_slot)
        hero.append(self.detail_title)
        hero.append(self.detail_subtitle)
        hero.append(buttons)

        self.banner = Adw.Banner(revealed=False)
        self.banner.set_button_label(tr("Подготовить"))
        self.banner.connect("button-clicked", lambda *_: self.show_installer())

        self.info_group = Adw.PreferencesGroup(title=tr("Сведения"))
        self.row_package = Adw.ActionRow(title=tr("Пакет"))
        self.row_activity = Adw.ActionRow(title="Activity")
        self.row_abi = Adw.ActionRow(title=tr("Архитектура"))
        self.row_size = Adw.ActionRow(title=tr("Занимает места"))
        self.row_last = Adw.ActionRow(title=tr("Последний запуск"))
        for row in (
            self.row_package,
            self.row_activity,
            self.row_abi,
            self.row_size,
            self.row_last,
        ):
            row.add_css_class("property")
            self.info_group.add(row)

        runtime = Adw.PreferencesGroup(title=tr("Запуск"))

        # Профиль — это отдельные данные одного и того же приложения: свой
        # вход, свой кеш. Так запускают второй аккаунт, не выходя из первого.
        self.profile_row = Adw.ComboRow(title=tr("Профиль Android"))
        self.profile_row.add_prefix(
            Gtk.Image.new_from_icon_name("system-users-symbolic")
        )
        self.profile_row.connect("notify::selected", self._on_profile_changed)
        self._profile_values: list[int] = []
        self._syncing_profile = False
        runtime.add(self.profile_row)

        self.build_row = Adw.ActionRow(title=tr("Сборка в контейнере"))
        self.build_row.add_prefix(Gtk.Image.new_from_icon_name("package-x-generic-symbolic"))
        runtime.add(self.build_row)

        self.state_row = Adw.ActionRow(title="Waydroid")
        self.state_row.add_prefix(Gtk.Image.new_from_icon_name("system-run-symbolic"))
        self.state_row.set_activatable(True)
        self.state_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        self.state_row.connect("activated", lambda *_: self.show_installer())
        runtime.add(self.state_row)

        self.bridge_row = Adw.ActionRow(title=tr("Трансляция ARM64 → x86_64"))
        self.bridge_row.add_prefix(Gtk.Image.new_from_icon_name("system-switch-user-symbolic"))
        runtime.add(self.bridge_row)

        self.resolution_row = Adw.EntryRow(title=tr("Разрешение окна (напр. 1600x900)"))
        self.resolution_row.set_show_apply_button(True)
        self.resolution_row.connect("apply", self._on_resolution_applied)
        fit = Gtk.Button(icon_name="video-display-symbolic")
        fit.set_tooltip_text(tr("Подставить размер монитора"))
        fit.set_valign(Gtk.Align.CENTER)
        fit.add_css_class("flat")
        fit.connect("clicked", lambda *_: self._fill_monitor_size())
        self.resolution_row.add_suffix(fit)
        runtime.add(self.resolution_row)

        self.renderer_row = Adw.ActionRow(title=tr("Рендер контейнера"))
        self.renderer_row.add_prefix(Gtk.Image.new_from_icon_name("video-display-symbolic"))
        runtime.add(self.renderer_row)

        ui_row = Adw.ActionRow(
            title=tr("Открыть окно Android"), subtitle=tr("Полный рабочий стол контейнера")
        )
        ui_row.set_activatable(True)
        ui_row.add_prefix(Gtk.Image.new_from_icon_name("video-display-symbolic"))
        ui_row.connect("activated", lambda *_: self._show_android_ui())
        runtime.add(ui_row)

        # Выключение — прямо в карточке: чаще всего оно нужно сразу после
        # игры, и лезть за ним в меню неудобно.
        self.restart_row = Adw.ActionRow(
            title=tr("Перезапустить Waydroid"),
            subtitle=tr("если контейнер запущен, а сети у него нет"),
        )
        self.restart_row.set_activatable(True)
        self.restart_row.add_prefix(Gtk.Image.new_from_icon_name("view-refresh-symbolic"))
        self.restart_row.connect("activated", lambda *_: self._restart_waydroid())
        runtime.add(self.restart_row)

        self.stop_row = Adw.ActionRow(title=tr("Выключить Waydroid"))
        self.stop_row.set_activatable(True)
        self.stop_row.add_css_class("error")
        self.stop_row.add_prefix(
            Gtk.Image.new_from_icon_name("system-shutdown-symbolic")
        )
        self.stop_row.connect("activated", lambda *_: self._stop_waydroid())
        runtime.add(self.stop_row)

        actions = Adw.PreferencesGroup(title=tr("Управление"))
        remove_row = Adw.ActionRow(
            title=tr("Удалить из библиотеки"), subtitle=tr("APK и все его данные")
        )
        remove_row.set_activatable(True)
        remove_row.add_css_class("error")
        remove_row.add_prefix(Gtk.Image.new_from_icon_name("edit-delete-symbolic"))
        remove_row.connect("activated", lambda *_: self._confirm_remove())

        self.uninstall_row = Adw.ActionRow(
            title=tr("Удалить из контейнера"),
            subtitle=tr("снять установку в Waydroid, запись в библиотеке останется"),
        )
        self.uninstall_row.set_activatable(True)
        self.uninstall_row.add_prefix(
            Gtk.Image.new_from_icon_name("user-trash-symbolic")
        )
        self.uninstall_row.connect("activated", lambda *_: self._confirm_uninstall())
        actions.add(self.uninstall_row)
        actions.add(remove_row)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        column.set_margin_start(18)
        column.set_margin_end(18)
        column.set_margin_bottom(24)
        column.append(hero)
        column.append(self.banner)
        column.append(self.info_group)
        column.append(runtime)
        column.append(actions)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(Adw.Clamp(maximum_size=620, child=column))
        return scroller

    # -- данные ------------------------------------------------------------

    def refresh(self, select_slug: str | None = None) -> None:
        self.entries = self.library.load()

        self.list_box.remove_all()
        for entry in self.entries:
            self.list_box.append(AppRow(entry))

        self.sidebar_stack.set_visible_child_name("list" if self.entries else "empty")

        if not self.entries:
            self.selected = None
            self.content_stack.set_visible_child_name("welcome")
            self.content_title.set_title("Merci")
            self.content_title.set_subtitle("")
            return

        target = select_slug or (self.selected.slug if self.selected else None)
        index = next((i for i, e in enumerate(self.entries) if e.slug == target), 0)
        row = self.list_box.get_row_at_index(index)
        if row is not None:
            self.list_box.select_row(row)

    def _on_row_selected(self, _list_box, row) -> None:
        if row is None:
            return
        self.selected = row.entry
        self._show_detail(row.entry)

    def _show_detail(self, entry: Entry) -> None:
        self.content_stack.set_visible_child_name("detail")
        self.content_title.set_title(entry.name)
        self.content_title.set_subtitle(entry.package)

        child = self.detail_icon_slot.get_first_child()
        if child is not None:
            self.detail_icon_slot.remove(child)
        self.detail_icon_slot.append(_icon_widget(entry, 96))

        self.detail_title.set_text(entry.name)
        self.detail_subtitle.set_text(
            tr("версия {version}", version=entry.version) if entry.version else entry.package
        )

        self.row_package.set_subtitle(entry.package or "—")
        self.row_activity.set_subtitle(entry.activity or tr("по умолчанию"))
        self.row_abi.set_subtitle(
            ", ".join(_ABI_LABEL.get(a, a) for a in entry.abis)
            if entry.abis
            else tr("без нативного кода")
        )
        self.row_size.set_subtitle(_human_size(entry.size_bytes()))
        self.row_last.set_subtitle(_human_time(entry.last_run))

        self._refresh_resolution(entry)
        self._refresh_runtime_state(entry)

    def _refresh_resolution(self, entry: Entry) -> None:
        """Разрешение у контейнера одно на все приложения — спрашиваем хост."""
        def apply(size, _error):
            width, height = size or (0, 0)
            self.resolution_row.set_text(f"{width}x{height}" if width else "")

        hostexec.in_thread(waydroid.resolution, apply)

    def _monitor_size(self) -> tuple[int, int]:
        """Размер монитора берём у самого дисплея — без обращений к хосту."""
        display = self.get_display()
        monitors = display.get_monitors() if display is not None else None
        monitor = monitors.get_item(0) if monitors and monitors.get_n_items() else None
        surface = self.get_surface()
        if surface is not None and display is not None:
            found = display.get_monitor_at_surface(surface)
            monitor = found or monitor
        if monitor is None:
            return 0, 0

        geometry = monitor.get_geometry()
        scale = monitor.get_scale_factor() or 1
        return geometry.width * scale, geometry.height * scale

    def _fill_monitor_size(self) -> None:
        width, height = self._monitor_size()
        if not width:
            self._toast(tr("Не удалось определить размер монитора"))
            return
        self.resolution_row.set_text(f"{width}x{height}")
        self._toast(tr("Подставил {w}x{h} — нажмите галочку, чтобы применить",
                       w=width, h=height))

    def _on_resolution_applied(self, row: Adw.EntryRow) -> None:
        entry = self.selected
        if entry is None:
            return

        text = row.get_text().strip().lower().replace(tr("х"), "x").replace("*", "x")
        width = height = 0
        if text:
            try:
                width, height = (int(part) for part in text.split("x"))
            except ValueError:
                self._toast(tr("Разрешение задаётся как 1600x900"))
                self._refresh_resolution(entry)
                return
            if not (320 <= width <= 7680 and 240 <= height <= 4320):
                self._toast(tr("Слишком странное разрешение"))
                self._refresh_resolution(entry)
                return

        # Окно контейнера растягиваем на монитор, а внутренний размер
        # дисплея Android задаём тем, что ввёл пользователь: картинка
        # занимает весь экран, но рисуется в меньшем разрешении.
        monitor = self._monitor_size()
        needs_stretch = bool(width) and (width, height) != monitor
        if needs_stretch and not waydroid.gamescope_available():
            self._offer_gamescope(width, height)
            return

        self._toast(tr("Перезапускаем контейнер…"))
        row.set_sensitive(False)

        def done(error, stretched):
            row.set_sensitive(True)
            if error is not None:
                self._error(tr("Не удалось настроить дисплей"), str(error))
                return
            if not width:
                self._toast(tr("Готово: размер сброшен"))
            elif stretched:
                self._toast(tr("Готово: рендер {w}x{h} растянут на экран",
                               w=width, h=height))
            else:
                self._toast(tr("Готово: контейнер в {w}x{h}", w=width, h=height))
            self._refresh_runtime_state(entry)

        waydroid.set_display_async(width, height, monitor, done)

    def _offer_gamescope(self, width: int, height: int) -> None:
        """Без gamescope растянуть нечем: Waydroid своё окно не масштабирует."""
        dialog = Adw.AlertDialog(
            heading=tr("Нужен gamescope"),
            body=tr(
                "Чтобы рисовать в {w}x{h} и занимать весь экран, "
                "контейнер запускается внутри gamescope — он и растягивает "
                "картинку. Waydroid сам этого не умеет: его окно всегда равно "
                "разрешению контейнера.\n\nПоставить gamescope из репозитория?",
                w=width, h=height,
            ),
        )
        dialog.add_response("cancel", tr("Отмена"))
        dialog.add_response("install", tr("Установить"))
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _d, r: r == "install"
            and self._run_single_step(waydroid.gamescope_step()),
        )
        dialog.present(self)

    def _refresh_runtime_state(self, entry: Entry) -> None:
        """Обновляет строки состояния. Хост опрашивается в потоке —
        обработчик выбора приложения обязан возвращаться мгновенно."""
        self.resolution_row.set_title(
            tr("Разрешение рендера, растянется на монитор (напр. 1600x900)")
        )
        # Трансляция важна только для ARM-кода: у APK с x86 в контейнере
        # она не участвует, и строка сбивала бы с толку.
        self.bridge_row.set_visible(entry.needs_native_bridge())

        # Профиль показываем только в многопользовательском режиме: иначе он
        # всегда основной, и строка была бы шумом.
        self.profile_row.set_visible(self.settings.multiuser)
        if self.settings.multiuser:
            self.profile_row.set_subtitle(tr("спрашиваем контейнер…"))
            slug = entry.slug
            hostexec.in_thread(
                waydroid.android_users,
                lambda result, error: self._show_profiles(slug, result, error),
            )
        self.stop_row.set_subtitle(tr("проверяем состояние…"))
        self.state_row.set_subtitle(tr("проверяем…"))

        self.build_row.set_subtitle(tr("спрашиваем контейнер…"))
        slug = entry.slug
        hostexec.in_thread(
            lambda: waydroid.installed_build(entry.package),
            lambda result, error: self._show_build(slug, result, error),
        )
        self.bridge_row.set_subtitle(tr("проверяем…"))
        self.play_button.set_sensitive(False)

        self.renderer_row.set_visible(True)
        self.renderer_row.set_subtitle(tr("проверяем…"))
        hostexec.in_thread(
            waydroid.renderer,
            lambda result, _error: self._show_renderer(result),
        )

        slug = entry.slug
        waydroid.state_async(True, lambda *args: self._apply_state(slug, *args))

    def _show_build(self, slug: str, result, error) -> None:
        """Какая сборка этого пакета сейчас стоит в контейнере.

        Имени пакета мало: у обычного и модифицированного клиента оно одно.
        Сверяем сам файл — начало sha256 у нас в ключе записи, и ровно его
        же можно получить от контейнера.
        """
        if self._closing:
            return
        entry = self.selected
        if entry is None or entry.slug != slug:
            return
        if error is not None or not result:
            self.build_row.set_subtitle(tr("контейнер не ответил"))
            return
        digest, version = result
        if digest is None:
            # Неполученный ответ — не «не установлена»: путать эти два
            # состояния значит показывать уверенную неправду.
            self.build_row.set_subtitle(
                tr("спросить не удалось — контейнер не ответил")
                if waydroid.adb_available()
                else tr("проверить нечем: adb появится вместе с MultiUser")
            )
            return
        if not digest:
            self.build_row.set_subtitle(tr("не установлена — поставится при запуске"))
            return
        if entry.file_hash and digest == entry.file_hash:
            self.build_row.set_subtitle(tr("эта — можно запускать"))
            return
        self.build_row.set_subtitle(
            tr("другая{version} — при запуске Merci предложит заменить",
               version=tr(" (версия {v})", v=version) if version else "")
        )

    def _show_profiles(self, slug: str, users, error) -> None:
        """Ответ контейнера о профилях. Карточка могла смениться — проверяем."""
        if self._closing:
            return
        entry = self.selected
        if entry is None or entry.slug != slug:
            return
        if error is not None or users is None:
            self.profile_row.set_subtitle(tr("контейнер не ответил: {error}", error=error))
            return

        values = [0] + sorted(n for n in users if n > 0)
        labels = [
            tr("основной") if n == 0 else tr("№{n} — {name}", n=n, name=users.get(n, tr("профиль")))
            for n in values
        ]
        # Отрицательное значение — «завести новый»: номер выдаёт Android,
        # заранее его не угадать.
        values.append(-1)
        labels.append(tr("Новый профиль…"))

        current = entry.profile if entry.profile in values else 0
        self._syncing_profile = True
        try:
            self._profile_values = values
            self.profile_row.set_model(Gtk.StringList.new(labels))
            self.profile_row.set_selected(values.index(current))
        finally:
            self._syncing_profile = False
        self.profile_row.set_subtitle(
            tr("свои данные приложения: вход, кеш, настройки")
            if current
            else tr("общие данные приложения")
        )

    def _on_profile_changed(self, *_args) -> None:
        if self._syncing_profile or self.selected is None:
            return
        index = self.profile_row.get_selected()
        if index >= len(self._profile_values):
            return
        entry = self.selected
        choice = self._profile_values[index]
        if choice == entry.profile:
            return

        if choice >= 0:
            entry.android_user = choice
            self.library.save(entry)
            self._refresh_runtime_state(entry)
            return

        # Новый профиль заводит сам Android, и это разговор с контейнером.
        self.profile_row.set_sensitive(False)
        self.profile_row.set_subtitle(tr("заводим профиль…"))
        slug = entry.slug

        def done(number, error) -> None:
            self.profile_row.set_sensitive(True)
            fresh = self.selected
            if fresh is None or fresh.slug != slug:
                return
            if error is not None:
                self._error(tr("Профиль не завёлся"), str(error))
                self._refresh_runtime_state(fresh)
                return
            fresh.android_user = number
            self.library.save(fresh)
            self._toast(tr("Готов профиль №{n}", n=number))
            self._refresh_runtime_state(fresh)

        hostexec.in_thread(lambda: waydroid.create_android_user(entry.name), done)

    def _show_renderer(self, result) -> None:
        if self._closing:
            return
        text, hardware = result or (tr("состояние неизвестно"), False)
        self.renderer_row.set_subtitle(
            text
            if hardware
            else tr("{text}. Меньше разрешение — выше частота кадров", text=text)
        )

    def _apply_state(
        self, slug: str, ready: bool, detail: str, bridge: str, network: bool = True
    ) -> None:
        """Ответ хоста пришёл. Если пользователь успел выбрать другое
        приложение — или окно вовсе закрылось, — ответ уже неинтересен."""
        if self._closing:
            return
        entry = self.selected
        if entry is None or entry.slug != slug:
            return

        self.state_row.set_subtitle(
            tr("сессия запущена") if ready else tr("{detail} — нажмите, чтобы подготовить", detail=detail)
        )
        # Выключать нечего, если контейнер и так не работает.
        self.stop_row.set_sensitive(ready)
        self.stop_button.set_sensitive(ready)
        self.stop_row.set_subtitle(
            tr("контейнер работает — остановить его и все приложения в нём")
            if ready
            else tr("контейнер не запущен")
        )
        self.bridge_row.set_subtitle(
            bridge if bridge else tr("не настроена — arm64-APK Waydroid не примет")
        )

        # Без трансляции нельзя запустить только ARM-код. APK с x86 внутри
        # контейнеру подходит как есть, и требовать от него libhoudini значит
        # запретить запуск на ровном месте.
        needs_bridge = entry.needs_native_bridge()
        blocked = not ready or (needs_bridge and not bridge)
        if blocked:
            self.banner.set_title(
                tr("Waydroid не готов: {detail}", detail=detail)
                if not ready
                else tr("Нужна трансляция ARM64 → x86_64 (libndk)")
            )
        elif not network:
            # Запускать можно, но приложение останется без сети.
            self.banner.set_title(tr("Контейнер без доступа в интернет: мешает ufw"))
        self.banner.set_revealed(blocked or not network)
        if not self._busy:
            self.play_button.set_sensitive(not blocked)

    # -- действия ----------------------------------------------------------

    def choose_apk(self) -> None:
        dialog = Gtk.FileDialog(title=tr("Выберите APK"))
        apk_filter = Gtk.FileFilter()
        apk_filter.set_name(tr("Android-приложения (*.apk)"))
        apk_filter.add_pattern("*.apk")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(apk_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(apk_filter)
        dialog.open(self, None, self._on_file_chosen)

    def _on_file_chosen(self, dialog, result) -> None:
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return  # пользователь передумал
        if gfile is not None and gfile.get_path():
            self.import_apk(gfile.get_path())

    def _on_drop(self, _target, value, _x, _y) -> bool:
        paths = [f.get_path() for f in value.get_files() if f.get_path()]
        if not paths:
            self._toast(tr("Такой источник перетащить нельзя — выберите файл вручную"))
            return False
        for path in paths:
            self.import_apk(path)
        return True

    def import_apk(self, path: str) -> None:
        if not path.lower().endswith(".apk"):
            self._toast(tr("{file}: это не APK", file=os.path.basename(path)))
            return

        progress = Adw.AlertDialog(
            heading=tr("Добавляем APK"),
            body=tr("{file} копируется в библиотеку…", file=os.path.basename(path)),
        )
        progress.present(self)

        def work() -> bool:
            try:
                entry = self.library.add(path)
            except apk.ApkError as exc:
                progress.close()
                self._error(tr("Не получилось добавить APK"), str(exc))
                return False
            except OSError as exc:
                progress.close()
                self._error(tr("Ошибка файловой системы"), str(exc))
                return False
            progress.close()
            # Пакет уже есть в библиотеке — значит это вторая сборка того же
            # приложения (обычный и модифицированный клиент, например).
            # Android держит по одной копии пакета на пользователя, поэтому
            # в многопользовательском режиме отдаём новой записи свой
            # профиль. Отрицательное число значит «профиль нужен, но номер
            # ещё не выдан»: контейнер может быть и выключен.
            twins = self.library.package_conflicts(entry.package, exclude=entry.slug)
            self.refresh(select_slug=entry.slug)
            if twins:
                # Обещать «обе сразу» нельзя: Android держит имя пакета как
                # одну установку на всё устройство. При запуске Merci
                # предложит заменить — это единственное, что тут возможно.
                self._toast(
                    tr("{name}: пакет {package} уже занят — "
                       "при запуске Merci предложит заменить установку",
                       name=entry.name, package=entry.package)
                )
            else:
                self._toast(tr("{name} добавлено", name=entry.name))
            return False

        # Копирование блокирующее, но сначала нужно показать диалог.
        GLib.idle_add(work)

    def show_installer(self) -> None:
        entry = self.selected
        needs_bridge = bool(entry and entry.needs_native_bridge())
        InstallerDialog(
            self,
            needs_bridge,
            on_finished=self._on_installer_done,
            monitor=self._monitor_size(),
        ).present(self)

    def _on_installer_done(self) -> None:
        waydroid.forget_state()
        if self.selected is not None:
            self._refresh_runtime_state(self.selected)
        self.refresh()

    def _update_play_button(self) -> None:
        self.play_button.set_label(tr("Запустить"))
        self.play_button.remove_css_class("destructive-action")
        self.play_button.add_css_class("suggested-action")

    def _on_play_clicked(self) -> None:
        if self.selected is not None:
            self._launch_waydroid(self.selected)

    def _launch_waydroid(self, entry: Entry) -> None:
        if self._busy:
            return  # второе нажатие запустило бы параллельную установку
        self._busy = True
        self.play_button.set_sensitive(False)
        self.play_button.set_label(tr("Готовим…"))
        self._toast(tr("{name} уходит в Waydroid", name=entry.name))

        def done(error) -> None:
            self._busy = False
            self.play_button.set_label(tr("Запустить"))
            self.play_button.set_sensitive(True)
            if isinstance(error, waydroid.ContainerUnreachable):
                self._offer_container_restart(str(error))
                return
            if isinstance(error, waydroid.InstallConflict):
                self._offer_replace(entry, str(error))
                return
            if error is not None:
                self._launch_failed(str(error))
                return
            entry.last_run = time.time()
            self.library.save(entry)
            self.row_last.set_subtitle(_human_time(entry.last_run))
            self._toast(tr("Окно откроет Waydroid"))
            if self.settings.minimize_on_launch:
                # Библиотека нужна была до нажатия; поверх игры она лишняя.
                GLib.timeout_add_seconds(2, lambda: (self.hide_to_tray(), False)[1])
            # Приложение может умереть через несколько секунд: без этого
            # выглядит как «не запустилось», и пользователь жмёт ещё раз.
            started = time.time()
            GLib.timeout_add_seconds(
                12,
                lambda: (
                    waydroid.recent_crash_async(
                        entry.package, started, self._report_crash
                    ),
                    False,
                )[1],
            )

        waydroid.install_and_launch_async(
            entry,
            done,
            on_stage=self.play_button.set_label,
            multiuser=self.settings.multiuser,
        )

    def _offer_replace(self, entry: Entry, message: str) -> None:
        """В контейнере другая сборка того же пакета.

        Android держит имя пакета как одну установку на всё устройство:
        профили делят код и различаются только данными. Обычный клиент и
        модифицированный подписаны по-разному, поэтому рядом стоять не
        могут — ни в каком профиле. Остаётся заменить.
        """
        twins = self.library.package_conflicts(entry.package, exclude=entry.slug)
        other = f"«{twins[0].name}»" if twins else tr("другая сборка")
        dialog = Adw.AlertDialog(
            heading=tr("В контейнере другая сборка этого приложения"),
            body=tr(
                "Пакет {package} уже занят: там стоит {other}. "
                "Android держит одно имя пакета как одну установку на всё "
                "устройство — профили делят между собой код приложения и "
                "различаются только данными, поэтому две разные сборки рядом "
                "не живут.\n\nЗаменить установку на «{name}»? Данные прежней "
                "сборки внутри Android будут стёрты — так требует Android при "
                "смене подписи.",
                package=entry.package, other=other, name=entry.name,
            ),
        )
        dialog.add_response("cancel", tr("Отмена"))
        dialog.add_response("replace", tr("Заменить"))
        dialog.set_response_appearance("replace", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", self._on_replace_response, entry)
        dialog.present(self)

    def _on_replace_response(self, _dialog, response: str, entry: Entry) -> None:
        if response != "replace":
            return
        self._busy = True
        self.play_button.set_sensitive(False)

        def done(error) -> None:
            self._busy = False
            self.play_button.set_label(tr("Запустить"))
            self.play_button.set_sensitive(True)
            if error is not None:
                self._launch_failed(str(error))
                return
            entry.last_run = time.time()
            self.library.save(entry)
            self.row_last.set_subtitle(_human_time(entry.last_run))
            self._toast(tr("{name}: установка заменена, открываем", name=entry.name))
            if self.settings.minimize_on_launch:
                GLib.timeout_add_seconds(2, lambda: (self.hide_to_tray(), False)[1])

        waydroid.replace_install_async(
            entry,
            done,
            on_stage=self.play_button.set_label,
            multiuser=self.settings.multiuser,
        )

    def _launch_failed(self, message: str) -> None:
        dialog = Adw.AlertDialog(
            heading=tr("Waydroid не запустил APK"),
            body=tr("{message}\n\nОткрыть проверку готовности?", message=message),
        )
        dialog.add_response("close", tr("Закрыть"))
        dialog.add_response("setup", tr("Подготовить"))
        dialog.set_response_appearance("setup", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("setup")
        dialog.connect("response", lambda _d, r: r == "setup" and self.show_installer())
        dialog.present(self)
        if self.selected is not None:
            self._refresh_runtime_state(self.selected)

    def _report_crash(self, summary: str) -> None:
        """Сообщает, что приложение упало, и объясняет чем это лечится."""
        if not summary:
            return
        translator = "libndk" in summary or "houdini" in summary
        toast = Adw.Toast(
            title=tr("Приложение упало внутри транслятора")
            if translator
            else tr("Приложение упало после запуска"),
            button_label=tr("Подробности"),
            timeout=8,
        )
        toast.connect("button-clicked", lambda *_: self._show_crash_log())
        self.toasts.add_toast(toast)

    def _switch_bridge(self) -> None:
        """Другой транслятор ARM: они по-разному переживают JIT приложений."""
        current = waydroid.current_bridge()
        target = "libhoudini" if current == "libndk" else "libndk"

        dialog = Adw.AlertDialog(
            heading=tr("Переключить на {target}?", target=target),
            body=tr(
                "Сейчас стоит {current}. Если приложение падает внутри "
                "транслятора, второй вариант иногда справляется. Займёт несколько "
                "минут и потребует пароль.",
                current=current or tr("ничего"),
            ),
        )
        dialog.add_response("cancel", tr("Отмена"))
        dialog.add_response("switch", tr("Переключить"))
        dialog.set_response_appearance("switch", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _d, r: r == "switch" and self._run_single_step(
                waydroid.switch_bridge_step(target)
            ),
        )
        dialog.present(self)

    def _install_urlforward(self) -> None:
        """Ссылки из Android — в браузер хоста."""
        if waydroid.urlforward_installed():
            self._toast(tr("Перехватчик ссылок уже установлен"))
            return

        dialog = Adw.AlertDialog(
            heading=tr("Открывать ссылки на компьютере?"),
            body=tr("Сейчас ссылка из приложения открывается браузером самого "
            "Android: передачи ссылок из контейнера на хост в Waydroid нет — "
            "это открытая заявка waydroid#210.\n\n"
            "Merci соберёт маленькое Android-приложение, которое ловит ссылку "
            "и отдаёт её службе на хосте, а та открывает её вашим браузером. "
            "Для сборки нужны JDK и инструменты Android SDK — Merci поставит "
            "их сама. Займёт несколько минут и потребует пароль."),
        )
        dialog.add_response("cancel", tr("Отмена"))
        dialog.add_response("install", tr("Собрать и установить"))
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _d, r: r == "install"
            and self._run_single_step(waydroid.urlforward_step()),
        )
        dialog.present(self)

    def _fix_flicker(self) -> None:
        """Мерцание из-за синхронных подповерхностей Wayland."""
        if not waydroid.subsurface_flicker():
            self._toast(tr("Подповерхности уже выключены — эта причина исключена"))
            return

        dialog = Adw.AlertDialog(
            heading=tr("Исправить мерцание?"),
            body=tr("Android-слои сейчас рисуются в подповерхностях Wayland. "
            "Синхронная подповерхность показывается только когда коммитит "
            "родительская поверхность, поэтому кадр замирает и обновляется "
            "лишь на события: нажатие клавиши, вход курсора в окно, "
            "появление экранной клавиатуры.\n\n"
            "Merci выключит этот режим в обоих файлах настроек и перезапустит "
            "контейнер. Потребуется пароль."),
        )
        dialog.add_response("cancel", tr("Отмена"))
        dialog.add_response("fix", tr("Исправить"))
        dialog.set_response_appearance("fix", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _d, r: r == "fix"
            and self._run_single_step(waydroid.subsurface_step()),
        )
        dialog.present(self)

    def _install_nvidia(self) -> None:
        """Аппаратное ускорение контейнера на видеокартах NVIDIA."""
        ready, detail = waydroid.nvidia_ready()
        if not ready:
            self._error(tr("Не подходит для этой машины"), detail)
            return

        refresh = self._monitor_refresh()
        dialog = Adw.AlertDialog(
            heading=tr("Включить аппаратное ускорение?"),
            body=tr(
                "Сейчас контейнер рисует процессором: Waydroid ходит в Mesa, "
                "а Mesa не умеет проприетарный драйвер NVIDIA.\n\n"
                "waydroid-nvidia подставляет гостю Mesa Venus и проксирует Vulkan "
                "в настоящий драйвер ({detail}), так что рисует видеокарта.\n\n"
                "Пакет waydroid будет заменён на waydroid-nvidia-bin — это тот же "
                "Waydroid с патчами, образ Android и данные остаются на месте. "
                "Частота обновления возьмётся из монитора: {refresh} Гц.",
                detail=detail, refresh=refresh,
            ),
        )
        dialog.add_response("cancel", tr("Отмена"))
        dialog.add_response("install", tr("Включить"))
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _d, r: r == "install"
            and self._run_single_step(waydroid.nvidia_step(refresh)),
        )
        dialog.present(self)

    def _monitor_refresh(self) -> int:
        """Частота монитора в герцах — её ждёт waydroid-nvidia-setup."""
        display = self.get_display()
        surface = self.get_surface()
        monitor = None
        if display is not None and surface is not None:
            monitor = display.get_monitor_at_surface(surface)
        if monitor is None:
            monitors = display.get_monitors() if display is not None else None
            monitor = monitors.get_item(0) if monitors and monitors.get_n_items() else None
        if monitor is None:
            return 60
        return max(30, round((monitor.get_refresh_rate() or 60000) / 1000))

    def _install_magisk(self) -> None:
        """Root в контейнере: обычная возможность своего же Android."""
        dialog = Adw.AlertDialog(
            heading=tr("Установить root в контейнере?"),
            body=tr("Magisk Delta даст root внутри Waydroid — это ваш собственный "
            "Android, так что доступ к системным разделам и модулям тут "
            "нормальная вещь.\n\nПроверку устройства играми это не проходит: "
            "для них контейнер с root выглядит наоборот подозрительнее. "
            "Займёт несколько минут и потребует пароль."),
        )
        dialog.add_response("cancel", tr("Отмена"))
        dialog.add_response("install", tr("Установить"))
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _d, r: r == "install" and self._run_single_step(waydroid.magisk_step()),
        )
        dialog.present(self)

    def _remove_magisk(self) -> None:
        dialog = Adw.AlertDialog(
            heading=tr("Убрать root из контейнера?"),
            body=tr("Magisk Delta будет снят, контейнер перезапустится. Модули, "
            "которые вы через него ставили, перестанут работать.\n\n"
            "Займёт несколько минут и потребует пароль."),
        )
        dialog.add_response("cancel", tr("Отмена"))
        dialog.add_response("remove", tr("Убрать"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect(
            "response",
            lambda _d, r: r == "remove"
            and self._run_single_step(waydroid.magisk_step(remove=True)),
        )
        dialog.present(self)

    # -- настройки ----------------------------------------------------------

    def _show_settings(self) -> None:
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title=tr("Профили Android"),
            description=tr("Профили Android дают одному приложению отдельные "
            "данные: свой вход, свой кеш, свои настройки. Так запускают "
            "второй аккаунт, не выходя из первого.\n\n"
            "Двух разных сборок одного пакета это не даёт и дать не может: "
            "имя пакета для Android — одна установка на всё устройство, "
            "профили делят между собой код приложения."),
        )

        self.multiuser_row = Adw.SwitchRow(
            title=tr("Использовать MultiUser"),
            subtitle=tr("в карточке появится выбор профиля; контейнер "
            "переключается на нужный при запуске"),
        )
        self.multiuser_row.set_active(self.settings.multiuser)
        self.multiuser_row.connect("notify::active", self._on_multiuser_toggled)
        group.add(self.multiuser_row)
        page.add(group)

        window_group = Adw.PreferencesGroup(title=tr("Окно"))
        self.minimize_row = Adw.SwitchRow(
            title=tr("Сворачивать Merci при запуске приложения"),
            subtitle=tr("окно прячется в трей через пару секунд после запуска; "
            "вернуть — нажатием на значок"),
        )
        self.minimize_row.set_active(self.settings.minimize_on_launch)
        self.minimize_row.connect(
            "notify::active",
            lambda row, _p: setattr(self.settings, "minimize_on_launch", row.get_active()),
        )
        window_group.add(self.minimize_row)

        # Язык первым в списке было бы логичнее, но группа «Окно» уже есть,
        # а заводить ради одной строки третью — лишний шум.
        self.language_row = Adw.ComboRow(
            title=tr("Язык интерфейса"),
            subtitle=tr("сохраняется; окно перерисуется сразу"),
        )
        self._language_codes = list(LANGUAGES)
        self.language_row.set_model(
            Gtk.StringList.new([LANGUAGES[code] for code in self._language_codes])
        )
        current = self.settings.language
        self.language_row.set_selected(
            self._language_codes.index(current) if current in self._language_codes else 0
        )
        self.language_row.connect("notify::selected", self._on_language_changed)
        window_group.add(self.language_row)

        page.add(window_group)

        dialog = Adw.PreferencesDialog()
        dialog.add(page)
        # Ссылку держим: при смене языка окно пересобирается, и открытый
        # диалог остался бы висеть без родителя — GTK на это ругается.
        self._settings_dialog = dialog
        dialog.present(self)

    def _on_language_changed(self, row, _param) -> None:
        """Смена языка. Текст у виджетов задан при постройке, поэтому окно
        собирается заново — иначе половина надписей осталась бы на старом
        языке до перезапуска."""
        index = row.get_selected()
        if index >= len(self._language_codes):
            return
        code = self._language_codes[index]
        if code == self.settings.language:
            return
        self.settings.language = code
        set_language(code)

        dialog = getattr(self, "_settings_dialog", None)
        if dialog is not None:
            dialog.close()
            self._settings_dialog = None

        # Через idle_add: обработчик пришёл из сигнала строки выбора, а она
        # живёт в том самом содержимом, которое сейчас будет заменено.
        GLib.idle_add(self.apply_language)

    def apply_language(self) -> bool:
        """Перерисовывает окно на новом языке.

        Содержимое пересобирается прямо в этом окне, а не в новом: текст у
        виджетов задаётся при постройке, но уничтожать окно ради этого
        нельзя — GTK при разборе окна с открытыми диалогами и висящими
        ответами хоста роняет весь процесс.
        """
        selected = self.selected.slug if self.selected else None
        self.split.set_sidebar(self._build_sidebar())
        self.split.set_content(self._build_content())
        self.tray.set_items(self._tray_items())
        self.refresh(selected)
        return GLib.SOURCE_REMOVE

    def _on_multiuser_toggled(self, row, _param) -> None:
        active = row.get_active()
        self.settings.multiuser = active
        if self.selected is not None:
            self._refresh_runtime_state(self.selected)
        if not active:
            self._toast(tr("MultiUser выключен: запуск идёт в основном профиле"))
            return

        # Готовность спрашиваем у хоста, а значит в потоке.
        hostexec.in_thread(waydroid.multiuser_ready, self._on_multiuser_ready)

    def _on_multiuser_ready(self, result, _error) -> None:
        if self._closing:
            return
        ready, detail = result or (False, tr("не удалось спросить хост"))
        if ready:
            self._toast(tr("MultiUser включён"))
            return
        dialog = Adw.AlertDialog(
            heading=tr("Контейнер к этому не готов"),
            body=tr(
                "{detail}.\n\nMerci может подготовить его: разрешит Android "
                "нескольких пользователей, откроет себе доступ к контейнеру через "
                "adb и поставит android-tools. Контейнер перезапустится, "
                "потребуется пароль.",
                detail=detail,
            ),
        )
        dialog.add_response("later", tr("Потом"))
        dialog.add_response("prepare", tr("Подготовить"))
        dialog.set_response_appearance("prepare", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _d, r: r == "prepare"
            and self._run_single_step(waydroid.multiuser_step()),
        )
        dialog.present(self)

    def _run_single_step(self, step) -> None:
        from .installer import SingleStepDialog

        SingleStepDialog(self, step, on_finished=self._on_installer_done).present(self)

    def _show_crash_log(self) -> None:
        """Отчёт о падении из контейнера: почему приложение закрылось."""
        self._toast(tr("Читаем отчёт о падении"))
        waydroid.crash_log_async(self._present_crash_log)

    def _present_crash_log(self, text: str) -> None:
        view = Gtk.TextView(editable=False, monospace=True)
        view.get_buffer().set_text(text or tr("Записей о сбоях нет."))
        view.set_left_margin(10)
        view.set_right_margin(10)
        view.set_wrap_mode(Pango.WrapMode.WORD_CHAR if False else Gtk.WrapMode.WORD_CHAR)
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(view)
        scroller.set_size_request(760, 480)

        dialog = Adw.Dialog(title=tr("Журнал сбоев Android"))
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        copy = Gtk.Button(icon_name="edit-copy-symbolic")
        copy.set_tooltip_text(tr("Скопировать"))
        copy.connect("clicked", lambda *_: self.get_clipboard().set(text))
        header.pack_end(copy)
        toolbar.add_top_bar(header)
        toolbar.set_content(scroller)
        dialog.set_child(toolbar)
        dialog.present(self)

    def _restart_waydroid(self) -> None:
        """Перезапуск контейнера: всё запущенное в нём закроется."""
        self.restart_button.set_sensitive(False)
        self.restart_row.set_sensitive(False)
        self._toast(tr("Перезапускаем контейнер…"))

        def done(error) -> None:
            self.restart_button.set_sensitive(True)
            self.restart_row.set_sensitive(True)
            waydroid.forget_state()
            if isinstance(error, waydroid.ContainerUnreachable):
                # Android внутри переживает перезапуск сессии, поэтому в
                # зависшем состоянии сессии мало — нужен сам контейнер.
                self._offer_container_restart(str(error))
            elif error is not None:
                self._error(tr("Перезапустить не вышло"), str(error))
            else:
                self._toast(tr("Контейнер перезапущен"))
            if self.selected is not None:
                self._refresh_runtime_state(self.selected)

        waydroid.restart_session_async(done, on_stage=self._toast)

    def _offer_container_restart(self, message: str) -> None:
        dialog = Adw.AlertDialog(
            heading=tr("Контейнер не отвечает"),
            body=tr(
                "{message}.\n\nAndroid внутри переживает перезапуск сессии, "
                "поэтому его мало: нужен перезапуск самого контейнера. Всё "
                "запущенное в нём закроется, потребуется пароль.",
                message=message,
            ),
        )
        dialog.add_response("cancel", tr("Отмена"))
        dialog.add_response("restart", tr("Перезапустить контейнер"))
        dialog.set_response_appearance("restart", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect(
            "response",
            lambda _d, r: r == "restart"
            and self._run_single_step(waydroid.container_restart_step()),
        )
        dialog.present(self)

    def _stop_waydroid(self) -> None:
        """Выключение контейнера: все запущенные в нём приложения закроются."""
        dialog = Adw.AlertDialog(
            heading=tr("Выключить Waydroid?"),
            body=tr("Контейнер остановится, и всё запущенное в нём закроется. "
            "Следующий запуск приложения поднимет его заново — это займёт "
            "около полуминуты."),
        )
        dialog.add_response("cancel", tr("Отмена"))
        dialog.add_response("stop", tr("Выключить"))
        dialog.set_response_appearance("stop", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_stop_response)
        dialog.present(self)

    def _on_stop_response(self, _dialog, response: str) -> None:
        if response != "stop":
            return

        self._toast(tr("Выключаем контейнер…"))
        self.play_button.set_sensitive(False)

        def done(problem: str) -> None:
            waydroid.forget_state()
            if problem:
                self._error(tr("Не выключилось"), problem)
            else:
                self._toast(tr("Waydroid выключен"))
            if self.selected is not None:
                self._refresh_runtime_state(self.selected)

        waydroid.stop_session_async(done)

    def _show_android_ui(self) -> None:
        try:
            waydroid.show_full_ui()
        except waydroid.WaydroidError as exc:
            self._error(tr("Не удалось открыть окно Android"), str(exc))

    def _confirm_uninstall(self) -> None:
        """Снять установку в контейнере, не трогая запись в библиотеке."""
        entry = self.selected
        if entry is None:
            return
        where = (
            tr("из профиля Android №{n}", n=entry.profile)
            if entry.profile
            else tr("из контейнера")
        )
        dialog = Adw.AlertDialog(
            heading=tr("Удалить из контейнера?"),
            body=tr(
                "«{name}» будет убрано {where} вместе со своими данными "
                "внутри Android — учётной записью в игре, кешем, настройками.\n\n"
                "APK останется в библиотеке, и запустить его можно будет снова.",
                name=entry.name, where=where,
            ),
        )
        dialog.add_response("cancel", tr("Отмена"))
        dialog.add_response("remove", tr("Удалить"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_uninstall_response, entry)
        dialog.present(self)

    def _on_uninstall_response(self, _dialog, response: str, entry: Entry) -> None:
        if response != "remove":
            return
        # Пока идёт удаление, запускать это же приложение бессмысленно.
        self._busy = True
        self.uninstall_row.set_sensitive(False)
        self.play_button.set_sensitive(False)
        self._toast(tr("{name}: убираем из контейнера…", name=entry.name))

        def done(error) -> None:
            self._busy = False
            self.uninstall_row.set_sensitive(True)
            self.play_button.set_sensitive(True)
            # Строка «Сборка в контейнере» после удаления обязана
            # перечитаться, иначе карточка врёт о состоянии.
            if self.selected is not None and self.selected.slug == entry.slug:
                self._refresh_runtime_state(self.selected)
            if isinstance(error, waydroid.NeedsConfirmation):
                self._ask_android_window(str(error))
                return
            if error is not None:
                self._error(tr("Удалить из контейнера не вышло"), str(error))
                return
            self._toast(tr("{name} убрано из контейнера", name=entry.name))

        waydroid.uninstall_async(entry, done)

    def _ask_android_window(self, message: str) -> None:
        """Просит подтвердить действие в окне контейнера и открывает его."""
        dialog = Adw.AlertDialog(heading=tr("Нужно подтвердить в Android"), body=message)
        dialog.add_response("close", tr("Понятно"))
        dialog.add_response("open", tr("Открыть окно Android"))
        dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("open")
        dialog.connect(
            "response", lambda _d, r: r == "open" and self._show_android_ui()
        )
        dialog.present(self)

    def _confirm_remove(self) -> None:
        entry = self.selected
        if entry is None:
            return
        dialog = Adw.AlertDialog(
            heading=tr("Удалить из библиотеки?"),
            body=tr(
                "«{name}» и все его данные будут стёрты безвозвратно — "
                "и APK здесь, и установка в контейнере вместе с её данными "
                "внутри Android.",
                name=entry.name,
            ),
        )
        dialog.add_response("cancel", tr("Отмена"))
        dialog.add_response("remove", tr("Удалить"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_remove_response, entry)
        dialog.present(self)

    def _on_remove_response(self, _dialog, response: str, entry: Entry) -> None:
        if response != "remove":
            return
        try:
            self.library.remove(entry)
        except ValueError as exc:
            self._error(tr("Удаление отменено"), str(exc))
            return
        self.selected = None
        self.refresh()
        self._toast(tr("{name} удалено", name=entry.name))

        # Из контейнера убираем следом и молча: запись из библиотеки уже
        # исчезла, и держать пользователя ради ответа контейнера незачем.
        # Но только если тем же пакетом не пользуется другая запись —
        # иначе мы снесли бы установку соседа.
        if entry.profile:
            waydroid.in_thread(
                lambda: waydroid.remove_android_user(entry.profile),
                lambda *_: None,
            )
            return
        if self.library.package_conflicts(entry.package, exclude=entry.slug):
            return
        waydroid.uninstall_async(entry, lambda *_: None)

    # -- мелочи ------------------------------------------------------------

    def _toast(self, message: str) -> None:
        if self._closing:
            return
        self.toasts.add_toast(Adw.Toast(title=message, timeout=4))

    def _error(self, heading: str, body: str) -> None:
        if self._closing:
            return
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("ok", tr("Понятно"))
        dialog.present(self)
