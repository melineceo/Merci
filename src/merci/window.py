"""Окно Merci: логика.

Как окно выглядит, знает пакет ``ui`` — здесь только то, что оно делает:
библиотека, запуск, окна-контейнеры, состояние Waydroid, диалоги. С видом
окно говорит по договору из ``ui/api.py``: отдаёт данные и получает нажатия.
"""

from __future__ import annotations

import os
import time
from contextlib import suppress

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import apk, hostexec, theme, updates, waydroid  # noqa: E402
from .settings import Settings  # noqa: E402
from .i18n import LANGUAGES, set_language, tr  # noqa: E402
from .tray import MenuItem, TrayIcon  # noqa: E402
from .installer import InstallerDialog, StepsDialog  # noqa: E402
from .library import AlreadyInLibrary, Entry, Library, host_abi  # noqa: E402
from .ui import api  # noqa: E402
from .ui.shell import Shell  # noqa: E402
from .updates_dialog import UpdatesDialog  # noqa: E402

# То же имя, что у флатпака: по нему трей ищет значок среди установленных
# иконок, а панель — среди экспортированных флатпаком.
APP_ID = "xyz.hackerstone.Merci"

_ABI_LABEL = {
    "arm64-v8a": "ARM64",
    "armeabi-v7a": "ARM32",
    "x86_64": "x86-64",
    "x86": "x86",
}

# Пульс карточки: как часто сверяем показанное с тем, что на самом деле
# происходит в контейнерах. Опрос стоит четверть секунды работы хоста.
_TICK_SECONDS = 6
# Присмотр за окнами без картинки — на каждый третий удар: сторож Android
# даёт около минуты, так что восемнадцати секунд хватает с запасом.
_GUARD_EVERY = 3


def apply_color_scheme(name: str) -> None:
    """Светлая, тёмная или как в системе.

    «Как в системе» работает там, где рабочий стол сообщает свой выбор
    (GNOME, KDE, порталы). Где не сообщает — голый оконный менеджер под X11,
    например, — тему можно выбрать самому.
    """
    Adw.StyleManager.get_default().set_color_scheme(
        {
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "dark": Adw.ColorScheme.FORCE_DARK,
        }.get(name, Adw.ColorScheme.DEFAULT)
    )


def _brief(name: str, limit: int = 44) -> str:
    """Имя записи для фразы в диалоге. Имена берутся из имён файлов и
    бывают в сотню знаков — в предложении такое не читается."""
    return name if len(name) <= limit else name[: limit - 1].rstrip() + "…"


def _human_size(count: int) -> str:
    size = float(count)
    for unit in (tr("Б"), tr("КБ"), tr("МБ")):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == tr("Б") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} {tr('ГБ')}"


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


class MerciWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application) -> None:
        super().__init__(application=application, title="Merci")
        self.set_default_size(1180, 820)
        self.set_size_request(640, 520)
        self.library = Library()
        self.settings = Settings()
        self.entries: list[Entry] = []
        self.selected: Entry | None = None

        # Идёт установка или запуск в основном окне: второе нажатие не нужно.
        # Чей это запуск и что он сейчас делает — помним по записи: иначе,
        # переключившись на другую карточку, человек видел бы на её кнопке ход
        # чужого запуска и получал вопросы про запись, которую не трогал.
        self._busy = False
        self._busy_slug = ""
        self._busy_label = ""

        # Окно может быть заменено или закрыто, пока ответы фоновых запросов
        # ещё в пути. Трогать виджеты уничтоженного окна нельзя.
        self._closing = False

        # Пульс: раз в несколько секунд показанное сверяется с контейнерами.
        # Тем же пульсом присматриваем за окнами: контейнер, которому велено
        # показывать исчезнувшее с экрана приложение, задыхается за минуту с
        # небольшим — и лечится потом только перезапуском.
        self._tick_busy = False
        self._tick_count = 0
        self._last_states: list[dict] | None = None
        self._states_error = ""
        self._last_status: waydroid.ContainerStatus | None = None
        self._last_session: tuple[bool, str] | None = None
        # Номера запросов: ответы приходят из потоков не в том порядке, в
        # каком их спрашивали, и старый не должен затирать свежий.
        self._instances_serial = 0
        self._state_serial = 0
        # Окна, с которыми прямо сейчас идёт операция (0 — со всеми сразу):
        # второе нажатие запустило бы рядом вторую такую же.
        self._window_ops: set[int] = set()
        self._stage_text = ""
        self._state_retries = 0
        self._state_retry_pending = False
        # Профили Android в выпадающем списке: номера по порядку строк.
        self._profile_values: list[int] = []
        # Цвет оформления по значку записи: считается один раз.
        self._seeds: dict[str, str] = {}
        self._size_busy = False
        self._window_size_text = ""
        # Сколько окон открыто у каждой записи — для отметок в библиотеке.
        self._open_counts: dict[str, int] = {}
        # Что из библиотеки уже отдано виду: одинаковое повторно не шлём.
        self._library_sent: tuple | None = None
        self._settings_dialog: Adw.PreferencesDialog | None = None
        # Обновления: итог последней проверки и окно, где он показан.
        self._updates: updates.Report | None = None
        self._updates_checking = False
        self._updates_dialog: UpdatesDialog | None = None
        self._menu_app: Gio.Menu | None = None
        # Идёт обновление Waydroid (или возврат образа): его мастер работает
        # минутами, под root, с выключенным контейнером. Всё это время окно
        # держит занятыми все окна — иначе запуск из трея поднял бы сессию
        # на образе, который в эту секунду переписывается.
        self._updating = False
        # О сброшенном исправлении мерцания говорим раз за запуск.
        self._flicker_warned = False
        # Ключ записи, про которую точно известно: в основном окне её
        # сборки нет — и снимать там нечего (см. _render_manage).
        self._build_absent = ""
        # Идёт доведение настроек до поднявшихся окон; и просьба повторить
        # его, пришедшая за это время (см. _sync_containers).
        self._syncing = False
        self._sync_pending = False
        # Идёт обновление самой Merci: второе рядом ни к чему.
        self._merci_updating = False
        # Мастер подготовки исполняет шаги и держит замок окна.
        self._wizard_working = False
        # Переключатель в настройках возвращается на место самой Merci, а
        # не человеком: на такое «нажатие» отвечать не надо.
        self._switch_quiet = False
        # Что писать, пока замок держится: у обновления и у разового шага
        # из меню слова разные (см. _begin_update).
        self._lock_summary = ""
        self._lock_notice = ""
        # Контейнер поднимается, гаснет или перезапускается: (что, текст).
        self._container_work: tuple[str, str] | None = None

        self.tray = self._build_tray()
        # Со значком в трее крестик логично прячет окно, а не закрывает
        # программу: выйти можно из того же меню, где «Открыть Merci».
        self.connect("close-request", self._on_close_request)

        self._install_actions()
        self.shell = Shell(self, self._app_menu())
        self.shell.set_reduced_glass(self.settings.reduced_glass)
        self.set_content(self.shell)

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self._on_drop)
        self.add_controller(drop)

        GLib.timeout_add_seconds(_TICK_SECONDS, self._tick)

        # Что за система на хосте, выясняем сразу и в потоке: от этого
        # зависят команды установки и способ спросить пароль.
        hostexec.in_thread(waydroid.host, lambda *_: self._after_host_known())
        # Служба приёма ссылок на хосте обновляется вместе с Merci.
        hostexec.in_thread(waydroid.refresh_url_listener)

        # Вернулись к окну с другого рабочего стола — показываем свежее
        # сразу, не дожидаясь очередного удара пульса.
        with suppress(TypeError):
            self.connect("notify::suspended", lambda *_: self._pulse())

        self.refresh()

        # Обновления: отметка — сразу, по памяти о прошлой проверке; сама
        # проверка — чуть погодя, чтобы не спорить за хост с запуском окна,
        # и дальше раз в несколько часов (сработает, когда минут сутки).
        self._render_updates()
        GLib.timeout_add_seconds(8, self._check_flicker_fix)
        GLib.timeout_add_seconds(25, self._check_updates_if_due)
        GLib.timeout_add_seconds(3 * 3600, lambda: self._check_updates_if_due() or True)

    def _after_host_known(self) -> None:
        if not self._closing and self.selected is not None:
            self._refresh_runtime_state(self.selected, quiet=True)
        self._sync_containers()

    def _sync_containers(self) -> None:
        """Доводит до работающих окон то, что Merci держит внутри Android.

        Спрятанный обновлятор: он опасен всегда — по нажатию на его
        уведомление образ переписывается под работающими окнами. Режим
        экономии и предел кадров: включённые при выключенном контейнере, они
        до него не доходили, а предел ещё и терялся при каждом перезапуске.

        Зовётся после всякой операции, которая могла поднять контейнер.
        Окно, которому всё уже послано, повторно не спрашивается. Во время
        работы под root не идёт вовсе: опрос состояния будил бы службу
        контейнера, которую помощник как раз остановил.
        """
        if self._closing or self._updating:
            return
        if self._syncing:
            self._sync_pending = True
            return
        self._syncing = True

        def done(_result, _error) -> None:
            self._syncing = False
            if self._sync_pending:
                self._sync_pending = False
                self._sync_containers()

        hostexec.in_thread(waydroid.sync_android_settings, done)

    # -- меню и трей -------------------------------------------------------

    def _install_actions(self) -> None:
        for name, callback in (
            ("waydroid-setup", lambda *_: self.show_installer()),
            ("waydroid-ui", lambda *_: self._show_android_ui()),
            ("waydroid-stop", lambda *_: self._stop_waydroid()),
            ("waydroid-crash", lambda *_: self._show_crash_log()),
            ("waydroid-bridge", lambda *_: self._switch_bridge()),
            ("waydroid-magisk", lambda *_: self._install_magisk()),
            ("waydroid-magisk-remove", lambda *_: self._remove_magisk()),
            ("settings", lambda *_: self._show_settings()),
            ("updates", lambda *_: self._show_updates()),
            ("waydroid-nvidia", lambda *_: self._install_nvidia()),
            ("waydroid-flicker", lambda *_: self._fix_flicker()),
            ("waydroid-urls", lambda *_: self._install_urlforward()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

    def _app_menu(self) -> Gio.Menu:
        menu = Gio.Menu()
        setup = Gio.Menu()
        setup.append(tr("Подготовить Waydroid"), "win.waydroid-setup")
        setup.append(tr("Журнал сбоев Android"), "win.waydroid-crash")
        menu.append_section(None, setup)
        tools = Gio.Menu()
        tools.append(tr("Сменить транслятор ARM"), "win.waydroid-bridge")
        tools.append(tr("Ссылки в браузере хоста"), "win.waydroid-urls")
        tools.append(tr("Аппаратное ускорение NVIDIA"), "win.waydroid-nvidia")
        tools.append(tr("Исправить мерцание картинки"), "win.waydroid-flicker")
        tools.append(tr("Установить root (Magisk)"), "win.waydroid-magisk")
        tools.append(tr("Убрать root (Magisk)"), "win.waydroid-magisk-remove")
        menu.append_section(None, tools)
        app = Gio.Menu()
        app.append(tr("Обновления"), "win.updates")
        app.append(tr("Настройки"), "win.settings")
        app.append(tr("Папка библиотеки"), "app.open-library")
        app.append(tr("О программе"), "app.about")
        menu.append_section(None, app)
        self._menu_app = app
        return menu

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
            MenuItem(7, tr("Перезапустить Waydroid"), self._restart_from_tray),
            MenuItem(8, tr("Выключить Waydroid"), self._stop_waydroid),
            MenuItem(9, "", separator=True),
            MenuItem(10, tr("Выйти из Merci"), self._quit_app),
        ]

    def _build_tray(self) -> TrayIcon:
        """Значок в трее: левое нажатие открывает Merci, правое — это меню."""
        tray = TrayIcon(
            APP_ID, "Merci", self._tray_items(), self.present_window,
            on_lost=self._on_tray_lost,
        )
        tray.start()
        return tray

    def _on_tray_lost(self) -> None:
        """Панель со значками исчезла: её закрыли или перезапускают.

        Спрятанное окно вернуть тогда нечем. Перезапуск панели — дело
        нескольких секунд, поэтому сразу окно не дёргаем: ждём, и если
        значок за это время не вернулся, показываем окно сами.
        """
        GLib.timeout_add_seconds(5, self._show_if_tray_gone)

    def _show_if_tray_gone(self) -> bool:
        if not self._closing and not self.tray.registered and not self.get_visible():
            self.present_window()
        return False

    def _on_close_request(self, *_args) -> bool:
        if self.tray.registered:
            self.set_visible(False)
            return True  # окно не закрываем
        if self._updating:
            # Значка в трее нет, и закрытие окна — это выход из программы.
            self._toast(self._lock_notice)
            return True
        return False

    def present_window(self) -> None:
        """Показать окно и поднять его поверх — из трея и после сворачивания."""
        self.set_visible(True)
        self.present()
        self._pulse()

    def hide_to_tray(self) -> None:
        """Спрятать окно. Если значка в трее нет, прятать нельзя — вернуть
        окно будет нечем, поэтому просто сворачиваем."""
        if self.tray.registered:
            self.set_visible(False)
        else:
            self.minimize()

    def _locked_out(self) -> bool:
        """Идёт работа под root — и говорит об этом, если так.

        Пункты трея доступны всегда, а многие из них начинают с вопроса
        хосту. Спрашивать во время обновления нельзя: `waydroid status`
        будит службу контейнера, которую помощник как раз остановил.
        """
        if self._updating:
            self._toast(self._lock_notice)
            return True
        return False

    def _raise_running_app(self) -> None:
        """Поднять запущенную игру — ту, что и правда запущена.

        Раньше пункт запускал последнее, что когда-либо запускали: при
        выключенном Waydroid он молча поднимал контейнер и ставил приложение
        заново, хотя назван «открыть запущенную».
        """
        if self._locked_out():
            return
        if self._busy:
            self._toast(tr("Дождитесь конца текущего запуска"))
            return
        catalog = self._catalog()

        def found(counts) -> None:
            running = [
                entry
                for entry in sorted(self.entries, key=lambda e: e.last_run, reverse=True)
                if (counts or {}).get(entry.slug)
            ]
            if not running:
                self._toast(tr("Сейчас ничего не запущено"))
                return
            # Запуск уже работающего приложения его и поднимает — в том
            # окне, где оно живёт.
            self._launch_waydroid(running[0])

        self._ask_host(lambda: waydroid.open_entries(catalog), found)

    def _start_waydroid(self) -> None:
        # Как и всё остальное, что трогает основной контейнер: не поверх
        # идущего выключения, перезапуска или обновления.
        if not self._begin_window_op(1):
            return
        self._container_work = ("start", tr("запускается…"))
        self._render_container()

        def work() -> bool:
            # Уже работает — так и скажем, а не «запущен», будто это мы.
            if waydroid.status(use_cache=False)[0]:
                return False
            GLib.idle_add(lambda: (self._toast(tr("Поднимаем контейнер…")), False)[1])
            waydroid.ensure_session()
            return True

        def done(started, error) -> None:
            self._window_ops.discard(1)
            self._container_work = None
            waydroid.forget_state()
            if error is not None:
                self._toast(tr("Не вышло: {error}", error=error))
            elif started:
                self._toast(tr("Контейнер запущен"))
            else:
                self._toast(tr("Waydroid уже работает"))
            self._after_op()

        hostexec.in_thread(work, done)

    def _restart_from_tray(self) -> None:
        """«Перезапустить Waydroid» из меню трея — с вопросом.

        Пункты там стоят вплотную, окна Merci человек в эту минуту не
        видит, а перезапуск закрывает всё запущенное. «Выключить» рядом
        спрашивал всегда, «Перезапустить» — нет.
        """
        if self._locked_out():
            return

        def decide(off: bool) -> None:
            if off:
                # Перезапускать нечего: просто включаем.
                self._start_waydroid()
                return
            self._choose(
                tr("Перезапустить Waydroid?"),
                tr("Всё запущенное в Android закроется.")
                + "\n\n"
                + tr("Дополнительные окна, если они работают, выключатся тоже — "
                     "для этого понадобится пароль."),
                (
                    ("cancel", tr("Отмена"), ""),
                    ("restart", tr("Перезапустить"), "destructive"),
                ),
                lambda _answer: self._restart_waydroid(),
            )

        self._ask_host(waydroid.certainly_off, decide)

    def request_quit(self) -> None:
        """Выход по Ctrl+Q — тем же путём, что «Выйти» в трее."""
        self._quit_app()

    def _quit_app(self) -> None:
        if self._updating:
            # Работу под root выход уже не остановит, но ход её станет не
            # виден, а итог — не проверен.
            self.present_window()
            self._toast(self._lock_notice)
            return
        self.tray.stop()
        application = self.get_application()
        if application is not None:
            application.quit()

    # -- нажатия из вида (ui.api.Handlers) ---------------------------------

    def select_entry(self, slug: str) -> None:
        entry = next((e for e in self.entries if e.slug == slug), None)
        if entry is None or (self.selected is not None and self.selected.slug == slug):
            return
        self.selected = entry
        # Строку выбрал человек — вид уже показывает её выбранной.
        if self._library_sent is not None:
            self._library_sent = (self._library_sent[0], slug)
        self._show_detail(entry)

    def add_apk(self) -> None:
        self.choose_apk()

    def play(self) -> None:
        if self.selected is not None:
            self._launch_waydroid(self.selected)

    def notice_action(self) -> None:
        self.show_installer()

    def window_action(self, action: str, number: int) -> None:
        handler = {
            "open": self._launch_in_window,
            "close": self._stop_in_window,
            "restart": self._restart_window,
            "power_off": self._power_off_window,
            "remove": self._confirm_remove_window,
            "resize": self._choose_size,
        }.get(action)
        if handler is not None:
            handler(number)

    def windows_action(self, action: str) -> None:
        handler = {
            "add": self._create_new_instance,
            "launch_all": self._launch_all_instances,
            "stop_all": self._stop_all_instances,
            "power_off_all": self._power_off_all,
            "retry": self._retry_windows,
        }.get(action)
        if handler is not None:
            handler()

    def container_action(self, action: str, value: object = None) -> None:
        if action == "toggle_eco":
            self._set_eco(bool(value))
        elif action == "set_fps":
            self._set_fps(int(value or 0))
        elif action == "apply_size":
            self._apply_size_text(str(value or ""))
        else:
            handler = {
                "fill_monitor_size": self._fill_monitor_size,
                "open_android": self._show_android_ui,
                "restart": self._restart_waydroid,
                "stop": self._stop_waydroid,
                "prepare": self.show_installer,
                "switch_bridge": self._switch_bridge,
                "apply_freeform": self._apply_freeform_opt,
                "trim_memory": self._trim_ram,
            }.get(action)
            if handler is not None:
                handler()

    def profile_chosen(self, index: int) -> None:
        self._on_profile_chosen(index)

    def manage_action(self, action: str) -> None:
        if action == "rename":
            self._rename_entry()
        elif action == "uninstall":
            self._confirm_uninstall()
        elif action == "remove":
            self._confirm_remove()

    def _retry_windows(self) -> None:
        """«Повторить» под сообщением «окна прочитать не удалось»."""
        if self.selected is not None:
            self._state_retries = 0
            self._refresh_runtime_state(self.selected, quiet=True)

    def _rename_entry(self) -> None:
        """Своё имя для записи. Имена берутся из имён файлов — у пяти сборок
        одного приложения они длинные и почти одинаковые."""
        entry = self.selected
        if entry is None:
            return
        field = Adw.EntryRow(title=tr("Название"))
        field.set_text(entry.name)
        field.set_activates_default(True)
        group = Adw.PreferencesGroup()
        group.add(field)
        dialog = Adw.AlertDialog(
            heading=tr("Переименовать запись"),
            body=tr("Имя видно только в Merci: на само приложение оно не влияет."),
        )
        dialog.set_extra_child(group)
        dialog.add_response("cancel", tr("Отмена"))
        dialog.add_response("save", tr("Сохранить"))
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        def answer(_dialog, response: str) -> None:
            name = " ".join(field.get_text().split())[:120]
            if response != "save" or not name or name == entry.name:
                return
            entry.name = name
            try:
                self.library.save(entry)
            except OSError as exc:
                self._error(tr("Не удалось сохранить имя"), str(exc))
                return
            self._send_library()
            if self.selected is not None and self.selected.slug == entry.slug:
                self.shell.show_page(self._page(entry))
                self._render_action()

        dialog.connect("response", answer)
        self._ask(dialog)

    # -- библиотека и страница ---------------------------------------------

    def _library_items(self) -> list[api.LibraryItem]:
        items = []
        for entry in self.entries:
            native = not entry.needs_native_bridge()
            items.append(
                api.LibraryItem(
                    slug=entry.slug,
                    name=entry.name,
                    version=entry.version or entry.package,
                    icon=entry.icon_path if os.path.exists(entry.icon_path) else "",
                    badge=_ABI_LABEL.get(host_abi(), host_abi()) if native else "ARM",
                    native=native,
                    badge_hint=tr("Есть код под этот процессор: пойдёт напрямую, без трансляции")
                    if native
                    else tr("Только ARM-код: пойдёт через трансляцию в контейнере"),
                    open_windows=self._open_counts.get(entry.slug, 0),
                )
            )
        return items

    def _send_library(self) -> None:
        """Отдаёт виду список библиотеки — если в нём что-то изменилось."""
        items = self._library_items()
        selected = self.selected.slug if self.selected is not None else None
        sent = (tuple(items), selected)
        if sent != self._library_sent:
            self._library_sent = sent
            self.shell.set_library(items, selected)

    def _catalog(self) -> list[tuple[str, str, str]]:
        """Записи библиотеки в том виде, в каком их понимает ядро."""
        return [(e.slug, e.package, e.file_hash) for e in self.entries]

    def _apply_open_counts(self, counts: dict[str, int] | None) -> None:
        if counts is None or counts == self._open_counts or self._closing:
            return
        self._open_counts = counts
        self._send_library()

    def refresh(self, select_slug: str | None = None) -> None:
        self.entries = self.library.load()
        # Окна без хозяина (запись удалили мимо Merci или в прежней версии)
        # иначе остались бы невидимыми ни в одной карточке.
        # «Без хозяина» — это когда каталога записи нет вовсе. Запись, которая
        # просто не прочиталась (недописанный файл сведений), окна сохраняет:
        # иначе их забрала бы первая открытая карточка — вместе с данными.
        known = {entry.slug for entry in self.entries}
        with suppress(OSError):
            known.update(os.listdir(self.library.apps_dir))
        waydroid.release_lost_windows(known)
        if not self.entries:
            self.selected = None
            self._send_library()
            self.shell.set_seed(None)
            self.shell.show_welcome()
            return

        target = select_slug or (self.selected.slug if self.selected else None)
        entry = next((e for e in self.entries if e.slug == target), self.entries[0])
        self.selected = entry
        self._send_library()
        self._show_detail(entry)

    def _page(self, entry: Entry) -> api.AppPage:
        native = not entry.needs_native_bridge()
        size = _human_size(entry.size_bytes())
        abis = ", ".join(_ABI_LABEL.get(a, a) for a in entry.abis) or tr("без нативного кода")
        return api.AppPage(
            slug=entry.slug,
            name=entry.name,
            icon=entry.icon_path if os.path.exists(entry.icon_path) else "",
            overline=tr("ANDROID · {arch} НАПРЯМУЮ", arch=host_abi())
            if native
            else tr("ANDROID · ЧЕРЕЗ ТРАНСЛЯЦИЮ ARM"),
            version=tr("версия {version}", version=entry.version) if entry.version else "",
            package=entry.package,
            chips=(abis, size),
            facts=(
                ("package", tr("Пакет"), entry.package or "—"),
                ("activity", "Activity", entry.activity or tr("по умолчанию")),
                ("abi", tr("Архитектура"), abis),
                ("size", tr("Занимает места"), size),
                ("last", tr("Последний запуск"), _human_time(entry.last_run)),
            ),
        )

    def _show_detail(self, entry: Entry) -> None:
        # Новое приложение — новая серия попыток.
        self._state_retries = 0
        self._state_retry_pending = False
        self._last_states = None
        self._states_error = ""
        self.shell.show_page(self._page(entry))
        self._apply_seed(entry)
        # Что уже известно о контейнере, показываем сразу: оно общее для
        # всех приложений, и ждать ради него нового ответа незачем.
        self._render_container()
        self._render_notice()
        self._render_action()
        self._refresh_resolution()
        self._refresh_runtime_state(entry)

    def _apply_seed(self, entry: Entry) -> None:
        """Цвет оформления — из значка приложения, как в Android."""
        slug = entry.slug
        if slug in self._seeds:
            self.shell.set_seed(self._seeds[slug] or None)
            return
        icon = entry.icon_path
        if not os.path.exists(icon):
            self._seeds[slug] = ""
            self.shell.set_seed(None)
            return

        def done(seed, _error) -> None:
            self._seeds[slug] = seed or ""
            if not self._closing and self.selected is not None and self.selected.slug == slug:
                self.shell.set_seed(seed or None)

        # Разбор картинки — десятки миллисекунд; выбор строки ждать не должен.
        hostexec.in_thread(lambda: theme.seed_from_png(icon), done)

    def _refresh_resolution(self) -> None:
        """Разрешение у контейнера одно на все приложения — спрашиваем хост."""

        def apply(size, _error) -> None:
            if self._closing:
                return
            width, height = size or (0, 0)
            self._window_size_text = f"{width}x{height}" if width else ""
            self.shell.set_window_size(self._window_size_text, self._size_busy)

        hostexec.in_thread(waydroid.resolution, apply)

    # -- состояние: опрос ---------------------------------------------------

    def _refresh_runtime_state(self, entry: Entry, quiet: bool = False) -> None:
        """Перечитывает всё, что карточка показывает о контейнере. Хост
        опрашивается в потоке — обработчик выбора обязан вернуться мгновенно.

        ``quiet`` — обновление по ходу дела (пульс, конец операции): прежние
        ответы остаются на месте, пока не пришли новые. Иначе карточка
        каждый раз мигала бы строками «проверяем…».
        """
        slug = entry.slug
        self._state_serial += 1
        serial = self._state_serial

        def fresh() -> bool:
            # Пока ответ шёл, карточку успели спросить заново — он устарел.
            return serial == self._state_serial and not self._closing

        if not quiet:
            self.shell.set_build(tr("спрашиваем контейнер…"), None)
            self.shell.set_renderer(tr("проверяем…"))
            self.shell.set_profiles(api.Profiles(visible=False))
            hostexec.in_thread(
                waydroid.renderer,
                lambda result, _error: self._show_renderer(result),
            )

        if self.settings.multiuser:
            hostexec.in_thread(
                waydroid.android_users,
                lambda result, error: fresh() and self._show_profiles(slug, result, error),
            )

        hostexec.in_thread(
            # Заодно — работает ли сессия: «не ответил» у выключенного
            # контейнера значит совсем другое, чем у работающего.
            lambda: (waydroid.installed_build(entry.package), waydroid.session_running()),
            lambda result, error: fresh() and self._show_build(slug, result, error),
        )

        self._refresh_instances(entry)

        waydroid.state_async(
            entry.needs_native_bridge(),
            lambda status: fresh() and self._apply_state(slug, status),
            multiuser=self.settings.multiuser,
        )

    def _refresh_instances(self, entry: Entry) -> None:
        """Окна — это отдельные контейнеры Android, по одному на окно."""
        slug = entry.slug
        package = entry.package
        user = max(0, entry.profile)
        build = entry.file_hash
        catalog = self._catalog()
        self._instances_serial += 1
        serial = self._instances_serial

        def done(result, error) -> None:
            if serial != self._instances_serial:
                return
            states, counts = result if result is not None else (None, None)
            self._show_instances(slug, states, error)
            self._apply_open_counts(counts)

        hostexec.in_thread(
            lambda: waydroid.card_snapshot(package, slug, user, build, catalog), done
        )

    def _retry_state_later(self, slug: str, error) -> bool:
        """Контейнер ещё поднимается — перечитаем состояние через пару секунд.

        Повторов конечное число: если контейнер и правда мёртв, карточка не
        должна опрашивать его вечно.
        """
        if not isinstance(error, waydroid.ContainerUnreachable):
            return False
        if self._state_retries >= 8 or self._state_retry_pending:
            return False
        self._state_retries += 1
        self._state_retry_pending = True

        def again() -> bool:
            self._state_retry_pending = False
            entry = self.selected
            if not self._closing and entry is not None and entry.slug == slug:
                self._refresh_runtime_state(entry, quiet=True)
            return False

        GLib.timeout_add_seconds(4, again)
        return True

    # -- состояние: показ ---------------------------------------------------

    def _show_build(self, slug: str, result, error) -> None:
        """Какая сборка этого пакета сейчас стоит в основном окне.

        Имени пакета мало: у обычного и модифицированного клиента оно одно.
        Сверяем сам файл — начало sha256 у нас в ключе записи, и ровно его
        же можно получить от контейнера.
        """
        entry = self.selected
        if entry is None or entry.slug != slug:
            return
        # Что в основном окне этой сборки нет, знаем только из ответа ниже;
        # всё остальное — «есть» или «не знаем», и снять установку тогда
        # разрешено (удаление проверит само).
        absent = bool(error is None and result and result[0][0] == "")
        self._build_absent = slug if absent else ""
        self._render_manage()
        if error is not None or not result:
            self.shell.set_build(
                str(error) if error is not None else tr("контейнер не ответил"), None
            )
            self._retry_state_later(slug, error)
            return
        (digest, version), running = result
        if digest is None:
            # Неполученный ответ — не «не установлена»: путать эти два
            # состояния значит показывать уверенную неправду.
            self.shell.set_build(
                tr("спросить не удалось — контейнер не ответил")
                if running
                else tr("контейнер выключен — проверим при запуске"),
                None,
            )
            return
        if not digest:
            self.shell.set_build(tr("не установлена — поставится при запуске"), None)
            return
        if entry.file_hash and digest == entry.file_hash:
            self.shell.set_build(tr("эта — можно запускать"), True)
            return
        who = self._build_name(digest, version)
        home = self._home_window(entry)
        self.shell.set_build(
            tr("в основном окне стоит {who} — запуск откроет окно {number}",
               who=who, number=home)
            if home
            else tr("в основном окне стоит {who} — при запуске Merci спросит, куда поставить эту",
                    who=who),
            False,
        )

    def _show_profiles(self, slug: str, users, error) -> None:
        """Ответ контейнера о профилях."""
        entry = self.selected
        if entry is None or entry.slug != slug:
            return
        if error is not None or users is None:
            # Пока контейнер поднимается, он сам объясняет причину.
            self.shell.set_profiles(
                api.Profiles(
                    visible=True,
                    labels=(tr("основной"),),
                    selected=0,
                    enabled=False,
                    hint=str(error)
                    if isinstance(error, waydroid.ContainerUnreachable)
                    else tr("контейнер не ответил: {error}", error=error),
                )
            )
            self._retry_state_later(slug, error)
            return

        values = [0] + sorted(n for n in users if n > 0)
        labels = [
            tr("основной") if n == 0 else tr("№{n} — {name}", n=n, name=users.get(n, tr("профиль")))
            for n in values
        ]
        # Отрицательное значение — «завести новый»: номер выдаёт Android.
        values.append(-1)
        labels.append(tr("Новый профиль…"))

        current = entry.profile if entry.profile in values else 0
        self._profile_values = values
        self.shell.set_profiles(
            api.Profiles(
                visible=True,
                labels=tuple(labels),
                selected=values.index(current),
                enabled=True,
                hint=tr("свои данные приложения: вход, кеш, настройки")
                if current
                else tr("общие данные приложения"),
            )
        )

    def _on_profile_chosen(self, index: int) -> None:
        entry = self.selected
        if entry is None or not 0 <= index < len(self._profile_values):
            return
        choice = self._profile_values[index]
        if choice == entry.profile:
            return

        if choice >= 0:
            entry.android_user = choice
            self.library.save(entry)
            self._refresh_runtime_state(entry, quiet=True)
            return

        # Новый профиль заводит сам Android, и это разговор с контейнером.
        slug = entry.slug
        self.shell.set_profiles(
            api.Profiles(visible=True, labels=(tr("заводим профиль…"),), enabled=False)
        )

        def done(number, error) -> None:
            fresh = self.selected
            if self._closing or fresh is None or fresh.slug != slug:
                return
            if error is not None:
                self._error(tr("Профиль не завёлся"), str(error))
            else:
                fresh.android_user = number
                self.library.save(fresh)
                self._toast(tr("Готов профиль №{n}", n=number))
            self._refresh_runtime_state(fresh, quiet=True)

        hostexec.in_thread(lambda: waydroid.create_android_user(entry.name), done)

    def _show_renderer(self, result) -> None:
        if self._closing:
            return
        text, hardware = result or (tr("состояние неизвестно"), False)
        self.shell.set_renderer(
            text if hardware else tr("{text}. Меньше разрешение — выше частота кадров", text=text)
        )

    def _apply_state(self, slug: str, status: waydroid.ContainerStatus) -> None:
        """Ответ хоста пришёл: запоминаем и перерисовываем то, что от него зависит."""
        entry = self.selected
        if entry is None or entry.slug != slug:
            return
        self._last_status = status
        self._last_session = (status.ready, status.detail)
        self._render_container()
        self._render_notice()
        self._render_action()

    def _blocked(self) -> bool:
        """Нельзя ли запускать выбранное приложение прямо сейчас."""
        entry, status = self.selected, self._last_status
        if entry is None or status is None:
            return True
        # Выключенная сессия — не поломка: запуск поднимет её сам.
        unprepared = not status.ready and not status.asleep
        return bool(
            unprepared
            or not status.has_adb
            or status.nested_missing
            or (entry.needs_native_bridge() and not status.bridge)
        )

    def _render_container(self) -> None:
        """«Остров» контейнера и его панель."""
        entry, status = self.selected, self._last_status
        self.shell.set_quick(self.settings.eco_mode, self.settings.fps_limit)
        if status is None:
            if self._container_work is not None:
                self.shell.set_container(api.CONTAINER_BUSY, self._container_work[1])
            else:
                self.shell.set_container(api.CONTAINER_UNKNOWN, tr("проверяем…"))
            self.shell.set_container_controls(True, tr("проверяем состояние…"), True)
            return

        if status.ready:
            kind, text = api.CONTAINER_READY, tr("сессия запущена")
        elif status.asleep:
            kind, text = api.CONTAINER_ASLEEP, tr("сессия остановлена")
        elif status.detail in ("не установлен", "образ Android не загружен"):
            kind = api.CONTAINER_UNPREPARED
            text = tr("{detail} — нажмите, чтобы подготовить", detail=tr(status.detail))
        else:
            kind = api.CONTAINER_UNKNOWN
            text = tr(status.detail)
        off = status.asleep or kind == api.CONTAINER_UNPREPARED
        work = self._container_work
        if work is not None and work[0] == "start" and status.ready:
            # Сессия поднялась — дальше идёт уже запуск приложения, а не её.
            work = self._container_work = None
        if work is not None:
            kind, text = api.CONTAINER_BUSY, work[1]
        self.shell.set_container(kind, text)

        # Выключение гасим, только когда точно знаем, что выключать нечего.
        # Раньше оно гасло на любом «не готов» — в том числе когда контейнер
        # завис и не ответил, то есть ровно тогда, когда нужнее всего.
        if status.ready:
            hint = tr("контейнер работает — остановить его и все приложения в нём")
        elif off:
            hint = tr("контейнер не запущен")
        else:
            hint = tr("состояние неясно — выключить можно и так")
        idle = work is None and not ({0, 1} & self._window_ops)
        self.shell.set_container_controls(not off and work is None, hint, idle)

        needs_bridge = entry is not None and entry.needs_native_bridge()
        if status.expired:
            bridge = tr("{bridge} — срок действия сборки истёк, нужно обновить",
                        bridge=status.bridge or "libhoudini")
        else:
            bridge = status.bridge or tr("не настроена — arm64-APK Waydroid не примет")
        self.shell.set_bridge(
            bridge,
            alert=bool(status.expired or (needs_bridge and not status.bridge)),
            visible=bool(needs_bridge or status.bridge),
        )

    def _render_notice(self) -> None:
        """Плашка: самое важное, что мешает или грозит, — одно за раз."""
        entry, status = self.selected, self._last_status
        if entry is None or status is None:
            self.shell.set_notice(None)
            return
        prepare = tr("Подготовить")
        needs_bridge = entry.needs_native_bridge()
        if not status.ready and not status.asleep:
            notice = api.Notice(
                tr("Waydroid не готов: {detail}", detail=tr(status.detail)), prepare, "error"
            )
        elif not status.has_adb:
            notice = api.Notice(
                tr("Нет adb: без него Merci не может управлять Android"), prepare, "error"
            )
        elif status.nested_missing:
            notice = api.Notice(
                tr("Сеанс X11: для Android нужен вложенный композитор Wayland"),
                prepare, "error",
            )
        elif needs_bridge and not status.bridge:
            notice = api.Notice(tr("Нужна трансляция ARM64 → x86_64"), prepare, "error")
        elif needs_bridge and status.expired:
            # Самый коварный случай: всё выглядит готовым, а приложение
            # после запуска молча зависает внутри транслятора.
            notice = api.Notice(
                tr("Транслятор ARM устарел: приложения зависают при запуске"), prepare
            )
        elif not status.network:
            notice = api.Notice(
                tr("Контейнер без доступа в интернет: мешает сетевой экран ({name})",
                   name=status.firewall),
                prepare,
            )
        elif status.adb_open:
            # Работе не мешает, но оставлять так нельзя: к контейнеру без
            # пароля подключится любое приложение внутри него и сосед по сети.
            notice = api.Notice(
                tr("adb контейнера открыт без пароля — стоит закрыть его ключом"), prepare
            )
        elif status.profiles_broken:
            notice = api.Notice(
                tr("Профили Android включены, но контейнер их не держит: "
                   "новый профиль не создать"),
                prepare,
            )
        else:
            notice = None
        self.shell.set_notice(notice)

    def _render_action(self) -> None:
        """Кнопка «Запустить» и строка рядом с ней."""
        entry = self.selected
        if entry is None:
            return
        if self._updating:
            self.shell.set_action(tr("Запустить"), busy=False, enabled=False)
            self.shell.set_summary(self._lock_summary)
            self._render_manage()
            return
        if self._busy and self._busy_slug == entry.slug:
            self.shell.set_action(self._busy_label or tr("Готовим…"), busy=True, enabled=False)
            self.shell.set_summary(tr("не закрывайте Merci"))
            self._render_manage()
            return
        if self._busy:
            # Идёт запуск другой записи: ход показываем словами, а не на
            # чужой кнопке.
            other = next((e for e in self.entries if e.slug == self._busy_slug), None)
            self.shell.set_action(tr("Запустить"), busy=False, enabled=False)
            self.shell.set_summary(
                tr("идёт запуск «{name}»", name=_brief(other.name)) if other else tr("идёт запуск")
            )
            self._render_manage()
            return
        known = self._last_status is not None
        self.shell.set_action(tr("Запустить"), busy=False, enabled=known and not self._blocked())
        self.shell.set_summary(self._summary(entry))
        self._render_manage()

    def _render_manage(self) -> None:
        """Снять установку можно только у работающего контейнера — и только
        когда есть что снимать. Раньше кнопка была доступна и под надписью
        «не установлена», а нажатие кончалось просьбой подтвердить удаление
        в окне Android — приложения, которого там нет."""
        status = self._last_status
        entry = self.selected
        absent = entry is not None and self._build_absent == entry.slug
        self.shell.set_can_uninstall(
            bool(status is not None and status.ready and status.has_adb
                 and not self._busy and not self._updating and not absent)
        )

    def _summary(self, entry: Entry) -> str:
        states = self._last_states
        status = self._last_status
        if status is not None and status.asleep:
            return tr("Waydroid выключен — поднимется при запуске")
        if not states:
            return ""
        own = [s for s in states if not s.get("foreign")]
        opened = sum(
            1 for s in own if s["app"] and s["window"] and s["showing"] == entry.package
        )
        if opened:
            return tr("открыто окон: {open} из {total}", open=opened, total=len(states))
        return tr("окон: {total} · приложение не открыто", total=len(states))

    # -- окна: показ --------------------------------------------------------

    def _show_instances(self, slug: str, items, error) -> None:
        if self._closing:
            return
        entry = self.selected
        if entry is None or entry.slug != slug:
            return
        if error is not None or items is None:
            self._last_states = None
            self._states_error = str(error) if error is not None else tr("контейнер не ответил")
        else:
            self._last_states = items
            self._states_error = ""
        self._render_windows()
        self._render_action()

    def _render_windows(self) -> None:
        entry = self.selected
        if entry is None:
            return
        if self._last_states is None:
            if self._states_error:
                self.shell.set_windows_error(
                    tr("Не удалось прочитать список окон"), self._states_error
                )
            return
        ops = self._window_ops
        tiles = [self._tile(entry, item) for item in self._last_states]
        running_clones = [
            s for s in self._last_states if s["running"] and s["number"] != 1
        ]
        opened = sum(1 for tile in tiles if tile.state == api.WINDOW_OPEN)
        total = len(tiles)
        self.shell.set_windows(
            tiles,
            api.WindowsHeader(
                summary=tr("окон: {total} · открыто: {open} · у каждого свой Android",
                           total=total, open=opened)
                if total > 1
                else tr("одно окно · добавьте ещё — у каждого будет свой Android"),
                can_add=not ops,
                can_launch_all=not ops and not self._busy,
                can_stop_all=not ops,
                can_power_off_all=bool(running_clones) and not ops,
                busy=0 in ops,
            ),
        )

    def _build_name(self, build: str, version: str) -> str:
        """Как назвать сборку, стоящую в контейнере: по записи библиотеки,
        если такая есть, иначе по версии."""
        holder = next((e for e in self.entries if e.file_hash and e.file_hash == build), None)
        if holder is not None:
            return f"«{_brief(holder.name)}»"
        return tr("версия {version}", version=version) if version else tr("другая сборка")

    def _tile(self, entry: Entry, item: dict) -> api.WindowTile:
        """Плитка окна: состояние одной фразой и то, что с окном можно сделать.

        «Не знаем» здесь отдельный ответ. Контейнер под нагрузкой молчит по
        нескольку секунд, и раньше такое молчание превращалось в уверенное
        «не отвечает» или «приложения здесь нет».
        """
        number = item["number"]
        main = number == 1
        running = item["running"]
        reachable = running and bool(item["ip"])

        if 0 in self._window_ops or number in self._window_ops:
            state, text = api.WINDOW_BUSY, tr("идёт операция…")
        elif item.get("foreign"):
            # В окне стоит другая сборка того же пакета. Раньше карточка
            # говорила про неё «приложение открыто» — про чужую игру.
            who = self._build_name(item.get("other_build", ""), item.get("other", ""))
            state = api.WINDOW_FOREIGN
            if item["app"] and item["window"]:
                text = tr("занято другой сборкой: {who} — она открыта", who=who)
            elif item["app"]:
                text = tr("занято другой сборкой: {who} — она работает в фоне", who=who)
            else:
                text = tr("занято другой сборкой: {who}", who=who)
        elif not running:
            state, text = api.WINDOW_OFF, tr("выключено")
        elif not item["ip"]:
            if main:
                state, text = api.WINDOW_BOOTING, tr("Android запускается — сети ещё нет")
            else:
                state, text = api.WINDOW_DEAD, tr("запущено, но сети нет — перезапустите окно")
        elif item["alive"] is None:
            state, text = api.WINDOW_UNKNOWN, tr("Android пока не отвечает")
        elif item["alive"] is False:
            state, text = api.WINDOW_DEAD, tr("Android не отвечает — перезапустите окно")
        elif item["app"] and item["window"] and item["showing"] == entry.package:
            state, text = api.WINDOW_OPEN, tr("приложение открыто")
        elif item["app"]:
            state = api.WINDOW_BACKGROUND
            text = tr("приложение работает в фоне — окна на экране нет")
        elif item["installed"] is False:
            state, text = api.WINDOW_ABSENT, tr("Android работает · приложения здесь ещё нет")
        else:
            state, text = api.WINDOW_IDLE, tr("Android работает · приложение закрыто")

        actions: list[str] = []
        if state != api.WINDOW_BUSY:
            actions.append("open")
            if reachable and state != api.WINDOW_FOREIGN:
                # Закрыть можно в любом работающем окне. Раньше кнопка
                # появлялась, только когда удавалось убедиться, что приложение
                # установлено, — и пропадала ровно тогда, когда контейнер
                # отвечал с задержкой, то есть когда была нужнее всего.
                # Кроме окна с чужой сборкой: имя пакета у неё то же, и
                # «закрыть» отсюда закрыло бы чужую игру.
                actions.append("close")
            if running:
                actions += ["resize", "restart"]
            if running and not main:
                actions.append("power_off")
            if not main:
                actions.append("remove")
        return api.WindowTile(
            number=number,
            title=tr("Окно {number}", number=number),
            role=tr("основное") if main else tr("свой Android"),
            state=state,
            text=text,
            main=main,
            actions=tuple(actions),
        )

    def _instance_stage(self, text: str) -> None:
        """Ход длинной операции с окнами — в полосе над плитками."""
        self._stage_text = text
        if not self._closing:
            self.shell.set_stage(text)

    def _stage_from_thread(self, text: str) -> None:
        GLib.idle_add(lambda: (self._instance_stage(text), False)[1])

    # -- пульс --------------------------------------------------------------

    def _watched(self) -> Entry | None:
        """Запись, на карточку которой сейчас кто-то смотрит."""
        if self._closing or self.selected is None or not self.get_visible():
            return None
        # Окно на другом рабочем столе или свёрнуто — рисовать не для кого.
        suspended = getattr(self, "is_suspended", None)
        if suspended is not None and suspended():
            return None
        return self.selected

    def _tick(self) -> bool:
        """Удар пульса. Отдаёт True, чтобы таймер жил дальше."""
        if self._closing:
            return False
        self._tick_count += 1
        self._pulse(guard=self._tick_count % _GUARD_EVERY == 0)
        return True

    def _pulse(self, guard: bool = False) -> None:
        """Сверяет карточку с контейнерами.

        ``guard`` — заодно присмотреть за окнами без картинки; это делается
        и при спрятанном окне Merci, остальное — только когда на карточку
        кто-то смотрит.
        """
        entry = self._watched()
        if self._tick_busy or self._updating or (entry is None and not guard):
            return
        self._tick_busy = True
        slug = entry.slug if entry else ""
        package = entry.package if entry else ""
        user = max(0, entry.profile) if entry else 0
        build = entry.file_hash if entry else ""
        catalog = self._catalog()
        serial = self._instances_serial

        def work():
            returned = waydroid.guard_idle_screens() if guard else []
            snapshot = None
            if slug:
                session = waydroid.status(use_cache=False)
                states, counts = waydroid.card_snapshot(package, slug, user, build, catalog)
                snapshot = (session, states, counts)
            return returned, snapshot

        def done(result, _error) -> None:
            self._tick_busy = False
            if self._closing or result is None:
                return
            returned, snapshot = result
            for item in returned:
                print(f"[merci] окно {item}: картинки нет, вернули в покой", flush=True)
            # Пока снимок готовился, строки могли обновить по другому поводу.
            if snapshot is not None and serial == self._instances_serial:
                self._apply_snapshot(slug, *snapshot)

        hostexec.in_thread(work, done)

    def _apply_snapshot(self, slug: str, session, states, counts) -> None:
        """Снимок пришёл: перерисовываем только то, что изменилось."""
        self._apply_open_counts(counts)
        entry = self.selected
        if entry is None or entry.slug != slug:
            return
        # Заняты все окна разом (создание, выключение, смена размера):
        # плитки перерисует тот, кто это начал. Отдельное занятое окно
        # пульсу не мешает — его плитка и так показывает «идёт операция».
        if 0 in self._window_ops:
            return
        previous, self._last_session = self._last_session, session
        if previous is not None and previous != session:
            # Сессию выключили или подняли мимо Merci — меняется всё разом.
            waydroid.forget_state()
            self._refresh_runtime_state(entry, quiet=True)
            if session[0]:
                self._sync_containers()
        elif states != self._last_states:
            self._show_instances(slug, states, None)

    # -- операции с окнами: занятость ---------------------------------------

    def _begin_window_op(self, number: int) -> bool:
        """Занимает окно под операцию; 0 — все окна сразу.

        False — с окном уже что-то делают. Второе нажатие запустило бы
        рядом вторую такую же операцию: два запуска в один контейнер, запуск
        поверх перезапуска — и они мешали бы друг другу.
        """
        ops = self._window_ops
        if number in ops or 0 in ops or (number == 0 and ops):
            self._toast(
                self._lock_notice
                if self._updating
                else tr("Дождитесь конца текущей операции с окном")
            )
            return False
        ops.add(number)
        self._render_windows()
        return True

    def _end_window_op(self, number: int, slug: str) -> None:
        """Освобождает окно и показывает, чем операция кончилась."""
        self._window_ops.discard(number)
        if self._closing:
            return
        # Полоса хода одна на все окна: стираем её, только когда кончилась
        # последняя операция, иначе она пропадала бы у ещё идущей.
        if not self._window_ops:
            self._instance_stage("")
        entry = self.selected
        if entry is not None and entry.slug == slug:
            self._refresh_instances(entry)
        else:
            self._render_windows()
        self._render_container()
        self._sync_containers()

    # -- запуск -------------------------------------------------------------

    def _begin_launch(self, entry: Entry, label: str) -> None:
        """Занимает главное действие под эту запись."""
        self._busy = True
        self._busy_slug = entry.slug
        self._busy_label = label
        self._render_action()

    def _launch_stage(self, slug: str, text: str) -> None:
        """Ход запуска — на кнопке той записи, которую запускают."""
        if self._busy and self._busy_slug == slug:
            self._busy_label = text
            if not self._closing:
                self._render_action()

    def _end_launch(self) -> None:
        self._busy = False
        self._busy_slug = ""
        self._busy_label = ""

    def _after_op(self) -> None:
        """Операция кончилась: показываем, чем, — какая бы карточка ни была
        открыта. Запуск одной записи меняет и чужие карточки: в основном
        окне теперь стоит другая сборка, сессия поднялась, окно занято."""
        if self._closing:
            return
        if not self._window_ops:
            self._instance_stage("")
        if self.selected is not None:
            self._refresh_runtime_state(self.selected, quiet=True)
        self._render_windows()
        self._render_container()
        self._render_action()
        self._sync_containers()

    def _hide_after_launch(self) -> None:
        """Убирает Merci с глаз после удачного запуска — если так настроено."""
        if self.settings.minimize_on_launch:
            GLib.timeout_add_seconds(2, lambda: (self.hide_to_tray(), False)[1])

    def _launch_waydroid(self, entry: Entry) -> None:
        """«Запустить»: ядро само решает, в каком окне живёт эта сборка."""
        if self._busy:
            # Второе нажатие запустило бы параллельную установку.
            self._toast(tr("Дождитесь конца текущего запуска"))
            return
        if not self._begin_window_op(1):
            return
        if self._last_status is not None and self._last_status.asleep:
            self._container_work = ("start", tr("запускается…"))
            self._render_container()
        self._begin_launch(entry, tr("Готовим…"))
        slug = entry.slug
        started = time.time()
        # Окно, в котором идёт запуск. Начинаем с основного; если ядро
        # выберет своё окно записи, занятость переезжает туда.
        where = {"number": 1}

        def routed(number: int) -> None:
            self._window_ops.discard(where["number"])
            where["number"] = number
            self._window_ops.add(number)
            if not self._closing:
                self._render_windows()

        def done(error, number: int) -> None:
            self._end_launch()
            self._container_work = None
            self._window_ops.discard(where["number"])
            if self._closing:
                return
            self._finish_launch(entry, error, started, number or where["number"])
            self._after_op()

        waydroid.play_async(
            entry,
            done,
            on_stage=lambda text: self._launch_stage(slug, text),
            multiuser=self.settings.multiuser,
            on_routed=routed,
        )

    def _finish_launch(self, entry: Entry, error, started: float, number: int = 1) -> None:
        """Общий конец запуска: вопросы, ошибки, отметка времени.

        ``started`` — когда нажали кнопку. Отчёт о падении ищем с этого
        момента: раньше отсчёт шёл от конца ожидания окна, и приложение,
        упавшее за эти минуты, считалось упавшим «до запуска».
        """
        if isinstance(error, waydroid.InstallConflict):
            self._offer_replace(entry, error.build, error.version, error.window or number)
            return
        if isinstance(error, waydroid.ContainerUnreachable):
            self._offer_container_restart(str(error))
            return
        if error is not None:
            self._launch_failed(str(error))
            return
        self._mark_launched(entry)
        self._toast(
            tr("«{name}» открыто в окне {number}", name=_brief(entry.name), number=number)
            if number > 1
            else tr("«{name}» открыто", name=_brief(entry.name))
        )
        # Библиотека нужна была до нажатия; поверх игры она лишняя.
        self._hide_after_launch()
        # Приложение может умереть через несколько секунд: без этого
        # выглядит как «не запустилось», и человек жмёт ещё раз.
        GLib.timeout_add_seconds(
            12,
            lambda: (
                waydroid.recent_crash_async(
                    entry.package, started, self._report_crash, number
                ),
                False,
            )[1],
        )

    def _mark_launched(self, entry: Entry) -> None:
        entry.last_run = time.time()
        self.library.save(entry)
        if self.selected is not None and self.selected.slug == entry.slug:
            self.shell.set_fact("last", _human_time(entry.last_run))

    def _offer_replace(self, entry: Entry, build: str = "", version: str = "",
                       window: int = 1) -> None:
        """Окно занято другой сборкой того же пакета.

        Android держит имя пакета как одну установку на контейнер: обычный
        клиент и модифицированный рядом не стоят ни в каком профиле.
        Безболезненный выход — дать этой сборке своё окно: у неё будет свой
        Android, а прежняя останется как есть. Второй — поставить её сюда:
        сначала обновлением на месте, с сохранением данных; стирать что-то
        Merci станет только после отдельного вопроса.
        """
        other = self._build_name(build, version)
        if window > 1:
            self._choose(
                tr("В окне {number} стоит другая сборка", number=window),
                tr("В окне {number} стоит {other}. Поставить вместо неё «{name}»?",
                   number=window, other=other, name=_brief(entry.name))
                + "\n\n"
                + tr(
                    "Merci попробует обновить сборку на месте, не трогая данные. "
                    "Если Android не позволит — другая подпись или версия ниже, — "
                    "Merci спросит ещё раз, прежде чем что-то стирать."
                ),
                (
                    ("cancel", tr("Отмена"), ""),
                    ("replace", tr("Поставить в это окно"), "suggested"),
                ),
                lambda _answer: self._replace_build(entry, window),
            )
            return

        home = self._home_window(entry)

        def answer(response: str) -> None:
            if response == "window" and home:
                self._launch_in_window(home)
            elif response == "window":
                self._open_in_new_window(entry)
            else:
                self._replace_build(entry, 1)

        if home:
            # У записи уже есть своё окно: заводить ещё одно незачем.
            self._choose(
                tr("Основное окно занято другой сборкой"),
                tr(
                    "В основном окне стоит {other}. Android держит одно имя пакета "
                    "({package}) как одну установку на контейнер, поэтому две "
                    "сборки в одном окне не живут.",
                    other=other, package=entry.package,
                )
                + "\n\n"
                + tr("У «{name}» есть своё окно {number} — можно открыть её там.",
                     name=_brief(entry.name), number=home)
                + "\n\n"
                + tr(
                    "В основное окно: Merci попробует обновить сборку на месте, не "
                    "трогая данные. Если Android не позволит — другая подпись или "
                    "версия ниже, — Merci спросит ещё раз, прежде чем что-то стирать."
                ),
                (
                    ("cancel", tr("Отмена"), ""),
                    ("replace", tr("В основное окно"), ""),
                    ("window", tr("Открыть в окне {number}", number=home), "suggested"),
                ),
                answer,
                default="window",
            )
            return

        self._choose(
            tr("Основное окно занято другой сборкой"),
            tr(
                "В основном окне стоит {other}. Android держит одно имя пакета "
                "({package}) как одну установку на контейнер, поэтому две "
                "сборки в одном окне не живут.",
                other=other, package=entry.package,
            )
            + "\n\n"
            + tr(
                "Отдельное окно: у «{name}» будет свой Android и свои данные, "
                "а прежняя сборка останется как есть. Понадобится пароль и "
                "около гигабайта памяти, пока окно работает.",
                name=_brief(entry.name),
            )
            + "\n\n"
            + tr(
                "В основное окно: Merci попробует обновить сборку на месте, не "
                "трогая данные. Если Android не позволит — другая подпись или "
                "версия ниже, — Merci спросит ещё раз, прежде чем что-то стирать."
            ),
            (
                ("cancel", tr("Отмена"), ""),
                ("replace", tr("В основное окно"), ""),
                ("window", tr("Отдельное окно"), "suggested"),
            ),
            answer,
            default="window",
        )

    def _home_window(self, entry: Entry) -> int:
        """Своё окно записи, куда уйдёт запуск, если основное занято другой
        сборкой; 0 — своих окон нет. Как и ядро (where_to_launch), берём
        сначала работающее: ему не нужен ни пароль, ни загрузка Android."""
        own = waydroid.own_windows(entry)
        if not own:
            return 0
        running = {
            state["number"] for state in self._last_states or [] if state.get("running")
        }
        return next((number for number in own if number in running), own[0])

    def _replace_build(self, entry: Entry, window: int = 1, wipe: bool = False) -> None:
        """Ставит эту сборку вместо стоящей в окне и запускает её.

        Без ``wipe`` — обновлением на месте. Если Android откажет, данные
        остаются нетронутыми, а человек получает второй вопрос — уже со
        списком того, что сотрётся.
        """
        if self._busy:
            self._toast(tr("Дождитесь конца текущего запуска"))
            return
        if not self._begin_window_op(window):
            return
        self._begin_launch(
            entry, tr("Заменяем сборку…") if wipe else tr("Обновляем сборку на месте…")
        )
        slug = entry.slug
        started = time.time()

        def done(error) -> None:
            self._end_launch()
            self._window_ops.discard(window)
            if self._closing:
                return
            if isinstance(error, waydroid.InstallConflict) and error.needs_wipe and not wipe:
                self._after_op()
                self._confirm_wipe(entry, error, window)
                return
            self._finish_launch(entry, error, started, window)
            self._after_op()

        waydroid.replace_install_async(
            entry,
            done,
            on_stage=lambda text: self._launch_stage(slug, text),
            multiuser=self.settings.multiuser,
            wipe=wipe,
            window=window,
        )

    def _confirm_wipe(self, entry: Entry, error: waydroid.InstallConflict, window: int) -> None:
        """Обновить на месте нельзя — остаётся снять прежнюю сборку вместе с
        данными. На это нужно отдельное согласие, и человек должен видеть,
        что именно пропадёт."""
        where = tr("основное окно") if window == 1 else tr("окно {number}", number=window)
        lines = [
            tr(
                "Android отказался обновить сборку на месте: у «{name}» другая "
                "подпись или версия ниже. Чтобы поставить её в {where}, прежнюю "
                "сборку придётся снять — вместе с её данными: входом в аккаунт, "
                "сохранениями, настройками.",
                name=_brief(entry.name), where=where,
            )
        ]
        users = [
            tr("основной") if number == 0 else tr("№{n} — {name}", n=number, name=name)
            for number, name in error.users
        ]
        if users:
            lines.append(tr("Данные сотрутся в профилях Android: {users}.",
                            users=", ".join(users)))
        owners = [
            f"«{_brief(other.name)}»"
            for other in self.entries
            if other.slug != entry.slug
            and other.package == entry.package
            and other.file_hash
            and other.file_hash == error.build
            and (window == 1 or window in waydroid.own_windows(other))
        ]
        if owners:
            lines.append(tr("Этой установкой пользуются записи: {names}.",
                            names=", ".join(owners)))
        if window == 1:
            lines.append(tr("Отдельное окно ничего не стирает."))

        answers = [("cancel", tr("Отмена"), "")]
        if window == 1:
            answers.append(("window", tr("Отдельное окно"), "suggested"))
        answers.append(("wipe", tr("Стереть и поставить"), "destructive"))

        def answer(response: str) -> None:
            if response == "window":
                self._open_in_new_window(entry)
            else:
                self._replace_build(entry, window, wipe=True)

        self._choose(tr("Обновить на месте нельзя"), "\n\n".join(lines), answers, answer)

    def _open_in_new_window(self, entry: Entry) -> None:
        """Заводит записи своё окно и открывает её там."""
        if self._busy:
            self._toast(tr("Дождитесь конца текущего запуска"))
            return
        # Номер окна выберет ядро — уже в потоке и уже подняв основное окно.
        # До тех пор занимаем все окна разом: раньше номер спрашивали у хоста
        # прямо из обработчика нажатия, и окно замирало на этом вопросе.
        if not self._begin_window_op(0):
            return
        self._begin_launch(entry, tr("Создаём окно… (до трёх минут)"))
        slug = entry.slug
        started = time.time()
        multiuser = self.settings.multiuser
        where = {"number": 0}

        def stage(text: str) -> None:
            GLib.idle_add(lambda: (self._launch_stage(slug, text), False)[1])

        def created(number: int) -> None:
            def move() -> bool:
                if where["number"] in self._window_ops:
                    self._window_ops.discard(where["number"])
                    where["number"] = number
                    self._window_ops.add(number)
                    fresh = self.selected
                    if not self._closing and fresh is not None:
                        self._refresh_instances(fresh)
                return False

            GLib.idle_add(move)

        def done(number, error) -> None:
            self._end_launch()
            self._window_ops.discard(where["number"])
            if self._closing:
                return
            self._finish_launch(entry, error, started, number or where["number"] or 1)
            self._after_op()

        hostexec.in_thread(
            lambda: waydroid.open_in_new_window(entry, stage, multiuser, created), done
        )

    def _launch_failed(self, message: str) -> None:
        self._choose(
            tr("Waydroid не запустил APK"),
            tr("{message}\n\nОткрыть проверку готовности?", message=message),
            (
                ("close", tr("Закрыть"), ""),
                ("setup", tr("Подготовить"), "suggested"),
            ),
            lambda _answer: self.show_installer(),
            default="setup",
        )

    def _report_crash(self, summary: str, report: str = "") -> None:
        """Сообщает, что приложение упало, и объясняет чем это лечится.

        ``report`` — сам отчёт: его и показывают «Подробности». Раньше они
        читали последний отчёт вообще, а он мог быть от другого приложения.
        """
        if not summary or self._closing:
            return
        translator = "libndk" in summary or "houdini" in summary
        title = (
            tr("Приложение упало внутри транслятора")
            if translator
            else tr("Приложение упало после запуска")
        )
        if not self.get_visible():
            # Окно к этому времени обычно уже спрятано в трей — строку
            # внутри него никто бы не увидел.
            self._notify(title, tr("Подробности — в окне Merci."))
            return
        self.shell.toast(
            title, tr("Подробности"),
            (lambda: self._present_crash_log(report)) if report else self._show_crash_log,
            8,
        )

    def _pending_nvidia_update(self) -> bool:
        """Ждёт ли установки обновление waydroid-nvidia.

        Его композитор до версии 0.2.0 иногда намертво встаёт на создании
        окна — тот самый «контейнер не отвечает» при запуске. Если новая
        версия уже вышла, честнее сказать об этом, чем в который раз
        предлагать перезапуск.
        """
        found = (
            [(item.key, item.title) for item in self._updates.updates]
            if self._updates is not None
            else [(str(i.get("key")), str(i.get("title"))) for i in self.settings.updates_found]
        )
        return any(key == "package" and "waydroid-nvidia" in title for key, title in found)

    def _offer_container_restart(self, message: str) -> None:
        body = tr(
            "{message}.\n\nAndroid внутри переживает перезапуск сессии, "
            "поэтому его мало: нужен перезапуск самого контейнера. Всё "
            "запущенное в нём закроется, потребуется пароль.",
            message=message,
        )
        answers = [("cancel", tr("Отмена"), "")]
        if self._pending_nvidia_update():
            body += "\n\n" + tr(
                "Похоже на известную ошибку waydroid-nvidia: его композитор "
                "иногда зависает, создавая окно. В новой версии она исправлена — "
                "обновление ждёт в окне обновлений."
            )
            answers.append(("updates", tr("Обновления"), ""))
        answers.append(("restart", tr("Перезапустить контейнер"), "suggested"))

        def answer(response: str) -> None:
            if response == "updates":
                self._show_updates()
            else:
                self._run_step_later(waydroid.container_restart_step)

        self._choose(tr("Контейнер не отвечает"), body, answers, answer)

    # -- окна: действия -----------------------------------------------------

    def _state_of(self, number: int) -> dict | None:
        return next((s for s in self._last_states or [] if s["number"] == number), None)

    def _create_new_instance(self) -> None:
        """Заводит ещё один контейнер: свой Android под ещё одно окно."""
        entry = self.selected
        if entry is None or not self._begin_window_op(0):
            return
        slug = entry.slug
        self._instance_stage(tr("Создаём окно… (до трёх минут)"))

        def done(number, error) -> None:
            if not self._closing:
                if error is not None:
                    self._error(tr("Не удалось создать окно"), str(error))
                else:
                    self._toast(tr("Окно {number} готово", number=number))
            self._end_window_op(0, slug)

        # Номер, основное окно, создание и первый запуск — всё в потоке и
        # одним заходом под root.
        hostexec.in_thread(
            lambda: waydroid.create_window(slug, self._stage_from_thread), done
        )

    def _launch_in_window(self, number: int) -> None:
        """Открывает приложение в выбранном окне, подняв контейнер и
        поставив APK, если этого ещё не сделано."""
        entry = self.selected
        if entry is None:
            return
        state = self._state_of(number)
        if state is not None and state.get("foreign"):
            # Человек показал именно на это окно, а оно занято другой
            # сборкой. Большая кнопка в таком случае сама уходит в окно
            # записи; здесь — спрашиваем.
            self._offer_replace(
                entry, state.get("other_build", ""), state.get("other", ""), number
            )
            return
        if number == 1:
            # Основное окно — тот же путь, что у большой кнопки: с
            # установкой, профилями и выбором окна.
            self._launch_waydroid(entry)
            return
        if not self._begin_window_op(number):
            return
        slug = entry.slug
        multiuser = self.settings.multiuser

        def work():
            waydroid.open_in_window(entry, number, self._stage_from_thread, multiuser)
            return number

        def done(_result, error) -> None:
            if not self._closing:
                if isinstance(error, waydroid.InstallConflict):
                    self._offer_replace(entry, error.build, error.version, number)
                elif error is not None:
                    self._error(tr("Окно не открылось"), str(error))
                else:
                    self._mark_launched(entry)
                    self._toast(tr("Открыто окно {number}", number=number))
                    self._hide_after_launch()
            self._end_window_op(number, slug)

        hostexec.in_thread(work, done)

    def _stop_in_window(self, number: int) -> None:
        """Закрывает приложение в окне — и говорит, закрылось ли оно."""
        entry = self.selected
        if entry is None or not self._begin_window_op(number):
            return
        slug, package = entry.slug, entry.package

        def work():
            if number == 1:
                waydroid.stop_main_app(package)
                return number
            item = next(
                (i for i in waydroid.instances() if i.get("number") == number), None
            )
            if item is None or item.get("state") != "RUNNING" or not item.get("ip"):
                raise waydroid.WaydroidError(tr("окно не запущено"))
            waydroid.stop_in_instance(package, item["ip"])
            return number

        def done(_result, error) -> None:
            if not self._closing:
                if error is not None:
                    self._error(tr("Не удалось закрыть приложение"), str(error))
                else:
                    self._toast(tr("Приложение закрыто в окне {number}", number=number))
            self._end_window_op(number, slug)

        hostexec.in_thread(work, done)

    def _restart_window(self, number: int) -> None:
        """Перезапускает контейнер окна: так лечится зависший Android и
        застрявшая картинка."""
        if number == 1:
            self._restart_waydroid()
            return
        entry = self.selected
        if entry is None or not self._begin_window_op(number):
            return
        slug = entry.slug
        self._instance_stage(
            tr("Перезапускаем окно {number}… (до трёх минут)", number=number)
        )

        def done(_result, error) -> None:
            if not self._closing:
                if error is not None:
                    self._error(tr("Окно не перезапустилось"), str(error))
                else:
                    self._toast(tr("Окно {number} перезапущено", number=number))
            self._end_window_op(number, slug)

        hostexec.in_thread(lambda: waydroid.restart_window(number), done)

    def _power_off_window(self, number: int) -> None:
        """Выключает контейнер окна: память возвращается системе, данные
        остаются. Следующий запуск поднимет его заново."""
        entry = self.selected
        if entry is None or number == 1 or not self._begin_window_op(number):
            return
        slug = entry.slug
        self._instance_stage(tr("Выключаем окно {number}…", number=number))

        def done(_result, error) -> None:
            if not self._closing:
                if error is not None:
                    self._error(tr("Окно не выключилось"), str(error))
                else:
                    self._toast(tr("Окно {number} выключено", number=number))
            self._end_window_op(number, slug)

        hostexec.in_thread(lambda: waydroid.stop_instance(number), done)

    def _power_off_all(self) -> None:
        """Выключает все дополнительные окна этого приложения одним заходом."""
        entry = self.selected
        if entry is None or not self._begin_window_op(0):
            return
        slug = entry.slug
        self._instance_stage(tr("Выключаем дополнительные окна…"))

        def work():
            numbers = [
                item["number"]
                for item in waydroid.instances(slug)
                if item["number"] != 1 and item["state"] == "RUNNING"
            ]
            waydroid.stop_instances(numbers)
            return len(numbers)

        def done(count, error) -> None:
            if not self._closing:
                if error is not None:
                    self._error(tr("Окна не выключились"), str(error))
                else:
                    self._toast(tr("Выключено окон: {count}", count=count or 0))
            self._end_window_op(0, slug)

        hostexec.in_thread(work, done)

    def _confirm_remove_window(self, number: int) -> None:
        if number == 1:
            return
        self._choose(
            tr("Удалить окно {number}?", number=number),
            tr("Контейнер и все его данные будут стёрты: установленные "
               "приложения, входы, кеш. Другие окна это не затронет."),
            (
                ("cancel", tr("Отмена"), ""),
                ("remove", tr("Удалить"), "destructive"),
            ),
            lambda _answer: self._remove_window(number),
        )

    def _remove_window(self, number: int) -> None:
        if number == 1 or not self._begin_window_op(number):
            return
        slug = self.selected.slug if self.selected is not None else ""

        def done(_result, error) -> None:
            if not self._closing:
                if error is not None:
                    self._error(tr("Не удалось удалить окно"), str(error))
                else:
                    self._toast(tr("Окно {number} удалено", number=number))
            self._end_window_op(number, slug)

        self._instance_stage(tr("Удаляем окно {number}…", number=number))
        hostexec.in_thread(lambda: waydroid.remove_instance(number), done)

    def _launch_all_instances(self) -> None:
        """Открывает приложение во всех существующих окнах, по очереди."""
        entry = self.selected
        if entry is None:
            return
        if self._busy:
            self._toast(tr("Дождитесь конца текущего запуска"))
            return
        if not self._begin_window_op(0):
            return
        slug = entry.slug
        multiuser = self.settings.multiuser

        def work():
            numbers = [item["number"] for item in waydroid.instances(slug)]
            return waydroid.open_in_windows(
                entry, numbers, self._stage_from_thread, multiuser
            )

        def done(result, error) -> None:
            if not self._closing:
                opened, failures = result if result is not None else ([], [])
                if isinstance(error, waydroid.InstallConflict):
                    # Открывать негде: основное занято другой сборкой, а
                    # своих окон у записи нет.
                    self._offer_replace(entry, error.build, error.version, 1)
                elif error is not None:
                    self._error(tr("Окна не открылись"), str(error))
                elif failures and not opened:
                    self._error(tr("Окна не открылись"), "\n".join(failures))
                elif failures:
                    self._error(
                        tr("Открылись не все окна"),
                        tr("Открыто окон: {count}.", count=len(opened))
                        + "\n\n"
                        + "\n".join(failures),
                    )
                else:
                    self._toast(tr("Открыто окон: {count}", count=len(opened)))
                    self._hide_after_launch()
                if opened:
                    self._mark_launched(entry)
            self._end_window_op(0, slug)

        hostexec.in_thread(work, done)

    def _stop_all_instances(self) -> None:
        """Закрывает приложение во всех окнах — и честно говорит, где не вышло."""
        entry = self.selected
        if entry is None or not self._begin_window_op(0):
            return
        slug, package, build = entry.slug, entry.package, entry.file_hash

        def done(result, error) -> None:
            if not self._closing:
                closed, failures = result if result is not None else (0, [])
                if error is not None:
                    self._error(tr("Не удалось закрыть приложение"), str(error))
                elif failures:
                    self._error(tr("Закрылось не везде"), "\n".join(failures))
                elif closed:
                    self._toast(tr("Приложение закрыто во всех окнах"))
                else:
                    self._toast(tr("Закрывать нечего: в окнах этой записи оно не запущено"))
            self._end_window_op(0, slug)

        hostexec.in_thread(lambda: waydroid.close_in_windows(package, slug, build), done)

    # -- размер окон --------------------------------------------------------

    def _monitor(self):
        display = self.get_display()
        surface = self.get_surface()
        if display is None:
            return None
        if surface is not None:
            found = display.get_monitor_at_surface(surface)
            if found is not None:
                return found
        monitors = display.get_monitors()
        return monitors.get_item(0) if monitors and monitors.get_n_items() else None

    def _monitor_size(self) -> tuple[int, int]:
        """Размер монитора берём у самого дисплея — без обращений к хосту."""
        monitor = self._monitor()
        if monitor is None:
            return 0, 0
        geometry = monitor.get_geometry()
        # Дробный масштаб (125 %, 150 %) есть только у новых GTK; целый
        # давал бы на таких мониторах размер больше настоящего.
        scale = getattr(monitor, "get_scale", monitor.get_scale_factor)() or 1
        return round(geometry.width * scale), round(geometry.height * scale)

    def _monitor_refresh(self) -> int:
        """Частота монитора в герцах — её ждёт waydroid-nvidia-setup."""
        monitor = self._monitor()
        if monitor is None:
            return 60
        return max(30, round((monitor.get_refresh_rate() or 60000) / 1000))

    def _choose_size(self, _number: int = 0) -> None:
        """Спрашивает размер окон и применяет его.

        Поверхность Waydroid берёт размер из свойств контейнера при старте
        и на просьбы композитора не отзывается, поэтому размер меняется
        только с перезапуском контейнера. Раз уж так, предлагаем готовые
        варианты, чтобы не набирать руками.
        """
        entry = self.selected
        if entry is None:
            return
        sizes = {"hd": (1280, 720), "hdplus": (1600, 900), "fhd": (1920, 1080)}
        answers = [("cancel", tr("Отмена"), "")]
        answers += [(key, f"{w}×{h}", "") for key, (w, h) in sizes.items()]
        monitor = self._monitor_size()
        if monitor[0]:
            sizes["monitor"] = monitor
            answers.append(
                ("monitor", tr("Как монитор — {w}×{h}", w=monitor[0], h=monitor[1]), "")
            )
        # Размер окна на экране знает только композитор, и спросить его
        # умеем не у всякого: где не умеем — не предлагаем.
        if waydroid.can_measure_windows():
            answers.append(("current", tr("Под окно на экране"), ""))

        def answer(response: str) -> None:
            width, height = sizes.get(response, (0, 0))
            self._apply_size(width, height)

        self._choose(
            tr("Размер окон"),
            tr("Размер применится ко всем окнам сразу — и к тем, что "
               "появятся позже. Контейнеры для этого перезапускаются, "
               "по сорок секунд на каждый работающий."),
            answers,
            answer,
        )

    def _apply_size(self, width: int, height: int) -> None:
        """Применяет размер ко всем окнам.

        Размер пишется в общие свойства контейнеров, поэтому его получают и
        существующие окна, и те, что будут созданы позже. Нулевой размер
        значит «взять с окна на экране».
        """
        if not self._begin_window_op(0):
            self.shell.set_window_size(self._window_size_text, self._size_busy)
            return
        entry = self.selected
        slug = entry.slug if entry is not None else ""
        package = entry.package if entry is not None else ""
        self._size_busy = True
        self.shell.set_window_size(self._window_size_text, True)

        def work():
            size = (width, height)
            if not all(size):
                window = waydroid.window_for_package(package) if package else None
                if window is None:
                    raise waydroid.WaydroidError(
                        tr("окно этого приложения на экране не найдено")
                    )
                size = (window["width"], window["height"])
            waydroid.set_global_size(size[0], size[1], stage=self._stage_from_thread)
            return size

        def done(result, error) -> None:
            self._size_busy = False
            if not self._closing:
                if error is not None:
                    self._error(tr("Не удалось применить размер"), str(error))
                elif result is not None:
                    self._toast(
                        tr("Все окна теперь {width}×{height}",
                           width=result[0], height=result[1])
                    )
                self._refresh_resolution()
            self._end_window_op(0, slug)

        hostexec.in_thread(work, done)

    def _apply_size_text(self, text: str) -> None:
        """Размер, набранный руками в панели контейнера."""
        # Кириллическая «х» — не перевод, а то, что реально приходит с русской
        # раскладки: «1600х900». Заменяем её всегда, независимо от языка окна.
        text = text.strip().lower().replace("х", "x").replace("*", "x").replace("×", "x")
        try:
            width, height = (int(part) for part in text.split("x"))
        except ValueError:
            self._toast(tr("Размер задаётся как 1600x900"))
            self.shell.set_window_size(self._window_size_text, self._size_busy)
            return
        if not (320 <= width <= 7680 and 240 <= height <= 4320):
            self._toast(tr("Слишком странный размер"))
            self.shell.set_window_size(self._window_size_text, self._size_busy)
            return
        self._apply_size(width, height)

    def _fill_monitor_size(self) -> None:
        """Ставит размер монитора и сразу применяет его."""
        width, height = self._monitor_size()
        if not width:
            self._toast(tr("Не удалось определить размер монитора"))
            return
        self._apply_size(width, height)

    # -- контейнер: быстрые действия ----------------------------------------

    def _set_eco(self, active: bool) -> None:
        if active == self.settings.eco_mode:
            return
        waydroid.note_quick_settings()
        self.settings.eco_mode = active
        self.shell.set_quick(active, self.settings.fps_limit)

        def done(result, _error) -> None:
            # Что сказать, решает ответ Android. Раньше «включён» писалось
            # сразу и в любом случае — в том числе при выключенном
            # контейнере, до которого настройка не доходила вовсе.
            reached, waiting = result or (False, True)
            if active:
                text = (
                    tr("Режим экономии включён (анимации отключены)")
                    if reached
                    else tr("Режим экономии включится при запуске Android")
                )
            elif reached or not waiting:
                text = tr("Режим экономии выключен")
            else:
                text = tr("Режим экономии выключен: анимации вернутся при запуске Android")
            self._toast(text)

        hostexec.in_thread(lambda: waydroid.apply_eco_mode(active), done)

    def _set_fps(self, fps: int) -> None:
        if fps not in api.FPS_CHOICES or fps == self.settings.fps_limit:
            return
        waydroid.note_quick_settings()
        self.settings.fps_limit = fps
        self.shell.set_quick(self.settings.eco_mode, fps)

        def done(result, _error) -> None:
            reached, waiting = result or (False, True)
            if fps:
                text = (
                    tr("Установлен лимит {fps} FPS", fps=fps)
                    if reached
                    else tr("Лимит {fps} FPS применится при запуске Android", fps=fps)
                )
            elif reached or not waiting:
                text = tr("Лимит FPS снят")
            else:
                text = tr("Лимит FPS снимется при запуске Android")
            self._toast(text)

        hostexec.in_thread(lambda: waydroid.set_fps_limit(fps), done)

    def _apply_freeform_opt(self) -> None:
        self._toast(tr("Применяем настройки многооконности…"))

        def done(reached, err) -> None:
            if err:
                self._toast(tr("Не удалось применить: {err}", err=err))
            elif reached:
                self._toast(tr("Свободный режим многооконности активен!"))
            else:
                # Без работающего Android применять некуда: об успехе тут
                # раньше сообщалось и при выключенном контейнере.
                self._toast(tr("Не применилось: Android не запущен или не отвечает"))

        hostexec.in_thread(waydroid.apply_multiwindow_optimizations, done)

    def _trim_ram(self) -> None:
        self._toast(tr("Очищаем память Android…"))

        def done(reached, err) -> None:
            if err:
                self._toast(tr("Ошибка очистки памяти: {err}", err=err))
            elif reached:
                self._toast(tr("Память Android успешно очищена!"))
            else:
                self._toast(tr("Память не очищена: Android не запущен или не отвечает"))

        hostexec.in_thread(waydroid.trim_android_memory, done)

    def _show_android_ui(self) -> None:
        if self._locked_out():
            # Окно Android само подняло бы сессию — посреди работы под root.
            return
        if 0 in self._window_ops or self._container_work is not None:
            # И посреди выключения или перезапуска: `waydroid show-full-ui`
            # у остановленной сессии запускает её сам — выключение кончалось
            # бы словами «контейнер всё ещё работает», а то и «выключен» при
            # поднятом заново Android.
            self._toast(tr("Дождитесь конца текущей операции с окном"))
            return
        try:
            waydroid.show_full_ui()
        except waydroid.WaydroidError as exc:
            self._error(tr("Не удалось открыть окно Android"), str(exc))

    def _restart_waydroid(self) -> None:
        """Перезапуск контейнера: всё запущенное в нём закроется."""
        if not self._begin_window_op(1):
            return
        self._container_work = ("restart", tr("перезапускается…"))
        self._render_container()
        self._instance_stage(tr("Перезапускаем контейнер…"))

        def done(error) -> None:
            self._window_ops.discard(1)
            self._container_work = None
            waydroid.forget_state()
            if self._closing:
                return
            if isinstance(error, waydroid.ContainerUnreachable):
                # Android внутри переживает перезапуск сессии, поэтому в
                # зависшем состоянии сессии мало — нужен сам контейнер.
                self._offer_container_restart(str(error))
            elif error is not None:
                self._error(tr("Перезапустить не вышло"), str(error))
            else:
                self._toast(tr("Контейнер перезапущен"))
            self._after_op()

        waydroid.restart_session_async(done, on_stage=self._instance_stage)

    def _stop_waydroid(self) -> None:
        """Выключение контейнера: все запущенные в нём приложения закроются.

        Сначала смотрим, есть ли что выключать: из меню трея пункт доступен
        всегда, и при выключенном Waydroid он спрашивал «Выключить?», гасил
        уже погашенное и отвечал «Waydroid выключен». Неясное состояние —
        зависший контейнер — выключенным не считается: тогда выключить его
        нужнее всего.
        """
        if self._locked_out():
            return

        def decide(off: bool) -> None:
            if off:
                self._toast(tr("Waydroid уже выключен"))
                return
            self._choose(
                tr("Выключить Waydroid?"),
                tr("Контейнер остановится, и всё запущенное в нём закроется. "
                   "Следующий запуск приложения поднимет его заново — это займёт "
                   "около полуминуты.")
                + "\n\n"
                + tr("Дополнительные окна, если они работают, выключатся тоже — "
                     "для этого понадобится пароль."),
                (
                    ("cancel", tr("Отмена"), ""),
                    ("stop", tr("Выключить"), "destructive"),
                ),
                lambda _answer: self._do_stop_waydroid(),
            )

        self._ask_host(waydroid.certainly_off, decide)

    def _do_stop_waydroid(self) -> None:
        if not self._begin_window_op(0):
            return
        self._container_work = ("stop", tr("выключается…"))
        self._render_container()
        self._instance_stage(tr("Выключаем контейнер…"))
        self._render_action()

        def done(problem: str) -> None:
            self._window_ops.discard(0)
            self._container_work = None
            waydroid.forget_state()
            if self._closing:
                return
            if problem:
                self._error(tr("Не выключилось"), problem)
            else:
                self._toast(tr("Waydroid выключен"))
            self._after_op()

        waydroid.stop_session_async(done)

    def show_installer(self) -> None:
        """Мастер «Подготовить Waydroid».

        Пока мастер исполняет шаги, окно занято — так же, как при разовом
        шаге из меню (см. _run_step_later): шаги те же, под root, с
        остановкой контейнера. Раньше замка не было вовсе: пульс будил
        службу контейнера, которую помощник только что остановил, а трей мог
        поднять сессию посреди работы. Замок берётся со стартом шагов и
        снимается с их концом, чем бы они ни кончились, — тогда же карточка
        перечитывает состояние (раньше после неудачи или отмены она
        показывала прежнее). Пока мастер просто открыт — показывает план
        или итог, — окно свободно.
        """
        if self._updating:
            # План мастер строит, опрашивая хост, а во время работы под root
            # это будит службу контейнера.
            self._toast(self._lock_notice)
            return
        entry = self.selected
        needs_bridge = bool(entry and entry.needs_native_bridge())
        dialog = InstallerDialog(
            self,
            needs_bridge,
            on_finished=self._on_installer_done,
            monitor=self._monitor_size(),
            on_work=self._on_installer_work,
        )
        self._ask(dialog)

    def _on_installer_work(self, working: bool) -> bool:
        """Мастер подготовки начал исполнять шаги — или кончил.

        Отдаёт False, если начинать нельзя: идёт запуск, операция с окном
        или обновление (о чём человеку уже сказано).
        """
        if working:
            if not self._begin_update(tr("Идёт настройка Waydroid…"), setup=True):
                return False
            self._wizard_working = True
        elif self._wizard_working:
            self._wizard_working = False
            self._end_update()
        return True

    def _on_installer_done(self) -> None:
        waydroid.forget_state()
        if self._closing:
            return
        # Мастер мог поменять что угодно: сессию, транслятор, adb, профили.
        self._last_status = None
        self.refresh()

    def _run_step_later(self, builder, done_title: str = "", on_result=None) -> None:
        """Строит шаг в потоке и открывает для него мастер.

        Шаги читают версию Android и настройки контейнера с хоста; на
        зависшем контейнере — а в эти меню идут как раз тогда — такой вопрос
        ждёт до таймаута, и окно замирало бы вместе с ним.

        Зовётся только после вопроса, на который человек ответил «да»: шаг
        в мастере стартует сразу. ``done_title`` — что написать при удаче
        (иначе «Waydroid готов»), ``on_result(получилось, ключ шага)`` —
        кому сообщить итог.

        Пока шаг готовится и идёт, окно занято так же, как при обновлении:
        запуск из трея поднял бы сессию посреди работы под root, второй
        выбор того же пункта меню запустил бы второй шаг поверх первого, а
        опрос состояния будил бы службу контейнера, которую помощник как раз
        остановил (она поднимается по первому же обращению к ней).
        """
        if not self._begin_update(tr("Идёт настройка Waydroid…"), setup=True):
            return

        def built(step, error) -> None:
            if self._closing:
                return
            if error is not None or step is None:
                self._end_update()
                self._error(tr("Не удалось подготовить шаг"), str(error))
                return
            self._run_single_step(step, done_title, on_result)

        hostexec.in_thread(builder, built)

    def _run_single_step(self, step, done_title: str = "", on_result=None) -> None:
        """Открывает мастер для шага. Замок (см. _run_step_later) снимается,
        когда мастер закрыт: во время шага закрыть его нельзя."""
        from .installer import SingleStepDialog

        dialog = SingleStepDialog(
            self, step, on_finished=self._on_installer_done,
            on_result=on_result, done_title=done_title,
        )
        dialog.connect("closed", lambda *_: self._end_update())
        self._ask(dialog)

    # -- библиотека: добавление и удаление ----------------------------------

    def choose_apk(self) -> None:
        dialog = Gtk.FileDialog(title=tr("Выберите APK"))
        apk_filter = Gtk.FileFilter()
        apk_filter.set_name(tr("Android-приложения (*.apk)"))
        apk_filter.add_pattern("*.apk")
        apk_filter.add_pattern("*.APK")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(apk_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(apk_filter)
        if not self.get_visible():
            self.present_window()
        dialog.open_multiple(self, None, self._on_files_chosen)

    def _on_files_chosen(self, dialog, result) -> None:
        try:
            files = dialog.open_multiple_finish(result)
        except GLib.Error:
            return  # человек передумал
        for index in range(files.get_n_items() if files is not None else 0):
            path = files.get_item(index).get_path()
            if path:
                self.import_apk(path)

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
        self._ask(progress)

        def done(entry, error) -> None:
            progress.force_close()
            if self._closing:
                return
            if isinstance(error, AlreadyInLibrary):
                # Запись не трогаем — показываем её.
                self.refresh(select_slug=error.entry.slug)
                self._toast(
                    tr("«{name}» уже в библиотеке", name=_brief(error.entry.name))
                )
                return
            if isinstance(error, apk.ApkError):
                self._error(tr("Не получилось добавить APK"), str(error))
                return
            if error is not None or entry is None:
                self._error(tr("Ошибка файловой системы"), str(error))
                return
            twins = self.library.package_conflicts(entry.package, exclude=entry.slug)
            self.refresh(select_slug=entry.slug)
            if twins:
                # Обещать «обе сразу в одном окне» нельзя: Android держит имя
                # пакета как одну установку на контейнер. Но у сборки может
                # быть своё окно — и тогда они работают рядом.
                self._toast(
                    tr("{name} добавлено. Пакет {package} уже есть в библиотеке — "
                       "при запуске Merci предложит этой сборке своё окно",
                       name=_brief(entry.name), package=entry.package),
                    timeout=8,
                )
            else:
                self._toast(tr("{name} добавлено", name=_brief(entry.name)))

        # Копирование и разбор — в потоке: APK на сотни мегабайт читается
        # секундами, и всё это время окно не отзывалось бы ни на что.
        hostexec.in_thread(lambda: self.library.add(path), done)

    def _confirm_uninstall(self) -> None:
        """Снять установку в основном окне, не трогая запись в библиотеке."""
        entry = self.selected
        if entry is None:
            return
        if self._busy:
            self._toast(tr("Дождитесь конца текущего запуска"))
            return
        if self._updating or {0, 1} & self._window_ops:
            # Спросить и отказать уже после ответа «Удалить» — хуже, чем не
            # спрашивать: основное окно сейчас занято.
            self._toast(
                self._lock_notice if self._updating
                else tr("Дождитесь конца текущей операции с окном")
            )
            return
        where = (
            tr("из профиля Android №{n}", n=entry.profile)
            if entry.profile
            else tr("из основного окна")
        )
        self._choose(
            tr("Удалить из контейнера?"),
            tr(
                "«{name}» будет убрано {where} вместе со своими данными "
                "внутри Android — учётной записью в игре, кешем, настройками.\n\n"
                "APK останется в библиотеке, и запустить его можно будет снова. "
                "Окна этой записи и то, что стоит в них, это не затронет.",
                name=_brief(entry.name), where=where,
            ),
            (
                ("cancel", tr("Отмена"), ""),
                ("remove", tr("Удалить"), "destructive"),
            ),
            lambda _answer: self._uninstall(entry),
        )

    def _uninstall(self, entry: Entry) -> None:
        if self._busy or not self._begin_window_op(1):
            return
        # Пока идёт удаление, запускать это же приложение бессмысленно.
        self._begin_launch(entry, tr("Убираем из контейнера…"))
        shared = bool(self.library.package_conflicts(entry.package, exclude=entry.slug))

        def done(error) -> None:
            self._end_launch()
            self._window_ops.discard(1)
            if self._closing:
                return
            if isinstance(error, waydroid.NeedsConfirmation):
                self._ask_android_window(str(error))
            elif error is not None:
                self._error(tr("Удалить из контейнера не вышло"), str(error))
            else:
                self._toast(tr("{name} убрано из контейнера", name=_brief(entry.name)))
            # Строка «Сборка в контейнере» после удаления обязана
            # перечитаться, иначе карточка врёт о состоянии.
            self._after_op()

        waydroid.uninstall_async(entry, done, shared=shared)

    def _ask_android_window(self, message: str) -> None:
        """Просит подтвердить действие в окне контейнера и открывает его."""
        self._choose(
            tr("Нужно подтвердить в Android"),
            message,
            (
                ("close", tr("Понятно"), ""),
                ("open", tr("Открыть окно Android"), "suggested"),
            ),
            lambda _answer: self._show_android_ui(),
            default="open",
        )

    def _confirm_remove(self) -> None:
        entry = self.selected
        if entry is None:
            return
        if self._busy and self._busy_slug == entry.slug:
            self._toast(tr("Дождитесь конца текущего запуска"))
            return
        if self._updating or self._window_ops:
            # Удаление записи занимает все окна разом; пока хоть одно занято,
            # оно не начнётся — и спрашивать об этом незачем.
            self._toast(
                self._lock_notice if self._updating
                else tr("Дождитесь конца текущей операции с окном")
            )
            return
        windows = waydroid.own_windows(entry)
        awake = self._last_status is not None and self._last_status.ready
        lines = [
            tr("«{name}» будет убрано из библиотеки: сам APK и его установка "
               "в основном окне вместе с данными внутри Android.", name=_brief(entry.name))
            if awake
            # Снять установку можно только у работающего контейнера. Обещать
            # это при выключенном значило оставить её без хозяина молча.
            else tr("«{name}» будет убрано из библиотеки. Waydroid сейчас выключен, "
                    "поэтому установка в основном окне, если она там есть, останется: "
                    "чтобы убрать и её, сначала включите Waydroid.",
                    name=_brief(entry.name))
        ]
        if windows:
            lines.append(
                tr("Окна этой записи ({numbers}) — её собственные контейнеры — "
                   "будут стёрты целиком, со всем, что в них стоит. Для этого "
                   "понадобится пароль.",
                   numbers=", ".join(str(n) for n in windows))
            )
        if entry.profile:
            lines.append(
                tr("Профиль Android №{n} останется: в нём может жить что-то ещё.",
                   n=entry.profile)
            )
        self._choose(
            tr("Удалить из библиотеки?"),
            "\n\n".join(lines),
            (
                ("cancel", tr("Отмена"), ""),
                ("remove", tr("Удалить"), "destructive"),
            ),
            lambda _answer: self._remove_entry(entry, windows),
        )

    def _remove_entry(self, entry: Entry, windows: list[int]) -> None:
        """Удаляет запись: сначала её следы в контейнерах, потом её саму.

        Именно в таком порядке. Раньше запись исчезала первой, а окна
        стирались «следом и молча»: если пароль не ввели, контейнеры
        оставались — работающие, занимающие память и уже не видимые ни в
        одной карточке.
        """
        if not self._begin_window_op(0):
            return
        self._instance_stage(tr("Удаляем «{name}»…", name=_brief(entry.name)))
        shared = bool(self.library.package_conflicts(entry.package, exclude=entry.slug))

        def done(blocked: str, leftover: str) -> None:
            self._window_ops.discard(0)
            if self._closing:
                return
            self._instance_stage("")
            if blocked:
                self._error(
                    tr("Удаление отменено"),
                    tr("Окна записи стереть не удалось, поэтому запись осталась "
                       "на месте: {error}", error=blocked),
                )
                self._after_op()
                return
            try:
                self.library.remove(entry)
            except (OSError, ValueError) as exc:
                self._error(tr("Удаление отменено"), str(exc))
                self._after_op()
                return
            self._seeds.pop(entry.slug, None)
            if self.selected is not None and self.selected.slug == entry.slug:
                self.selected = None
            self.refresh()
            if leftover:
                self._toast(
                    tr("{name} удалено, но установка в контейнере осталась: {error}",
                       name=_brief(entry.name), error=leftover),
                    timeout=8,
                )
            else:
                self._toast(tr("{name} удалено", name=_brief(entry.name)))

        waydroid.forget_entry_async(entry, shared, windows, done)

    # -- меню: разовые шаги -------------------------------------------------

    def _ask_host(self, question, then) -> None:
        """Спрашивает хост в потоке и продолжает в главном цикле.

        Пункты меню раньше ходили к хосту прямо из обработчика. На зависшем
        контейнере — а в это меню идут как раз тогда — окно замирало целиком
        до таймаута: ни нажатий, ни перерисовки.
        """

        def done(result, error) -> None:
            if self._closing:
                return
            if error is not None:
                self._error(tr("Не удалось спросить контейнер"), str(error))
                return
            then(result)

        hostexec.in_thread(question, done)

    def _confirm_step(self, heading: str, body: str, label: str, step,
                      destructive: bool = False, done_title: str = "",
                      on_result=None) -> None:
        """Вопрос перед шагом, который меняет систему и просит пароль."""
        self._choose(
            heading,
            body,
            (
                ("cancel", tr("Отмена"), ""),
                ("go", label, "destructive" if destructive else "suggested"),
            ),
            lambda _answer: self._run_step_later(step, done_title, on_result),
        )

    def _switch_bridge(self) -> None:
        """Другой транслятор ARM: они по-разному переживают JIT приложений."""
        self._ask_host(waydroid.current_bridge, self._offer_bridge_switch)

    def _offer_bridge_switch(self, current: str) -> None:
        if not current:
            # Менять нечего: транслятора нет вовсе. Раньше здесь предлагалось
            # «переключить на libndk» — не тот, что Merci ставит по умолчанию
            # и на котором работает большинство приложений.
            target = waydroid.DEFAULT_BRIDGE
            self._confirm_step(
                tr("Поставить транслятор ARM?"),
                tr("Транслятора ARM в контейнере сейчас нет: приложения, собранные "
                   "только под ARM, не запустятся. Merci поставит {target}. "
                   "Контейнер перезапустится; потребуется пароль.", target=target),
                tr("Поставить"),
                lambda: waydroid.switch_bridge_step(target),
                done_title=tr("Транслятор установлен"),
            )
            return
        target = "libhoudini" if current == "libndk" else "libndk"
        self._confirm_step(
            tr("Переключить на {target}?", target=target),
            tr(
                "Сейчас стоит {current}. Если приложение падает внутри "
                "транслятора, второй вариант иногда справляется. Контейнер "
                "перезапустится, всё запущенное в нём закроется; потребуется пароль.",
                current=current,
            ),
            tr("Переключить"),
            lambda: waydroid.switch_bridge_step(target),
            done_title=tr("Транслятор переключён"),
        )

    def _install_urlforward(self) -> None:
        """Ссылки из Android — в браузер хоста."""
        self._ask_host(waydroid.urlforward_installed, self._offer_urlforward)

    def _offer_urlforward(self, installed: bool) -> None:
        if installed:
            self._toast(tr("Перехватчик ссылок уже установлен"))
            return
        self._confirm_step(
            tr("Открывать ссылки на компьютере?"),
            tr("Сейчас ссылка из приложения открывается браузером самого "
               "Android: передачи ссылок из контейнера на хост в Waydroid нет — "
               "это открытая заявка waydroid#210.\n\n"
               "Merci соберёт маленькое Android-приложение, которое ловит ссылку "
               "и отдаёт её службе на хосте, а та открывает её вашим браузером. "
               "Для сборки нужны JDK и инструменты Android SDK — Merci поставит "
               "их сама. Займёт несколько минут и потребует пароль."),
            tr("Собрать и установить"),
            waydroid.urlforward_step,
        )

    def _fix_flicker(self) -> None:
        """Мерцание из-за синхронных подповерхностей Wayland."""
        self._ask_host(waydroid.subsurface_flicker, self._offer_flicker_fix)

    def _offer_flicker_fix(self, flicker: bool) -> None:
        if not flicker:
            self._toast(tr("Подповерхности уже выключены — эта причина исключена"))
            return
        self._confirm_step(
            tr("Исправить мерцание?"),
            tr("Android-слои сейчас рисуются в подповерхностях Wayland. "
               "Синхронная подповерхность показывается только когда коммитит "
               "родительская поверхность, поэтому кадр замирает и обновляется "
               "лишь на события: нажатие клавиши, вход курсора в окно, "
               "появление экранной клавиатуры.\n\n"
               "Merci выключит этот режим в обоих файлах настроек и перезапустит "
               "контейнер: всё запущенное в Android закроется, дополнительные "
               "окна выключатся. Потребуется пароль."),
            tr("Исправить"),
            waydroid.subsurface_step,
            done_title=tr("Мерцание исправлено"),
            on_result=self._flicker_fixed,
        )

    def _flicker_fixed(self, ok: bool, _key: str) -> None:
        """Исправили — напоминать о сброшенном исправлении уже незачем. Сам
        выбор запоминает шаг (Step.after): он же идёт и в мастере."""
        if ok:
            self._flicker_warned = True

    def _check_flicker_fix(self) -> bool:
        """Исправление мерцания, которое человек когда-то включил, могло быть
        сброшено настройкой ускорения мимо Merci. Говорим об этом сами — раз
        за запуск, — а не ждём, пока картинка снова начнёт замирать."""

        def answered(lost, error) -> None:
            if self._closing or error is not None or not lost or self._flicker_warned:
                return
            self._flicker_warned = True
            self._toast(
                tr("Исправление мерцания сброшено настройкой ускорения"),
                tr("Вернуть"),
                lambda: self._offer_flicker_fix(True),
                timeout=30,
            )

        if not self._flicker_warned and not self._updating:
            hostexec.in_thread(waydroid.flicker_fix_lost, answered)
        return False

    def _install_nvidia(self) -> None:
        """Аппаратное ускорение контейнера на видеокартах NVIDIA."""
        self._ask_host(waydroid.nvidia_state, self._offer_nvidia)

    def _offer_nvidia(self, state: dict) -> None:
        if state["installed"]:
            self._nvidia_installed(state)
            return
        if not state["suitable"]:
            self._error(tr("Не подходит для этой машины"), state["driver"])
            return
        if not state["installable"]:
            self._error(
                tr("Merci не умеет ставить ускорение на этой системе"),
                tr("waydroid-nvidia Merci собирает из AUR — это есть только в Arch и "
                   "производных. На других системах его ставят по описанию самого "
                   "проекта; уже установленное Merci узнает и будет поддерживать."),
            )
            return
        refresh = self._monitor_refresh()
        self._confirm_step(
            tr("Включить аппаратное ускорение?"),
            tr(
                "Сейчас контейнер рисует процессором: Waydroid ходит в Mesa, "
                "а Mesa не умеет проприетарный драйвер NVIDIA.\n\n"
                "waydroid-nvidia ставит в контейнер драйвер, который рисует на "
                "видеокарте NVIDIA (у вас драйвер {detail}). Перед установкой он "
                "сам испытывает его на этой машине и, если не подходит, называет "
                "причину.\n\n"
                "Пакет waydroid будет заменён на waydroid-nvidia-bin — это тот же "
                "Waydroid с патчами, образ Android и данные остаются на месте. "
                "Частота обновления возьмётся из монитора: {refresh} Гц.",
                detail=state["driver"], refresh=refresh,
            ),
            tr("Включить"),
            lambda: waydroid.nvidia_step(refresh),
            done_title=tr("Ускорение включено"),
        )

    def _nvidia_installed(self, state: dict) -> None:
        """Ускорение уже стоит: говорим, как оно работает, и не предлагаем
        ставить заново. Раньше пункт меню предлагал «включить» всегда —
        пакет переустанавливался, а настройка сбрасывала исправление
        мерцания."""
        lines = [
            tr("Ускорение уже включено: waydroid-nvidia {version}, режим {mode}.",
               version=state["version"] or tr("без пакета"), mode=state["mode"])
        ]
        broken = False
        if not state["suitable"]:
            broken = True
            lines.append(tr("Но драйвер NVIDIA сейчас не найден — без него контейнер "
                            "не стартует."))
        elif state["needs_setup"]:
            broken = True
            lines.append(tr("После обновления драйвера или пакета ему нужна повторная "
                            "настройка: без неё контейнер не стартует."))
        elif state["hardware"] is False:
            broken = True
            lines.append(tr("Но контейнер сейчас рисует так: {renderer}. Обычно это "
                            "исправляет повторная настройка.", renderer=state["renderer"]))
        elif state["renderer"]:
            lines.append(tr("Контейнер сейчас рисует так: {renderer}.",
                            renderer=state["renderer"]))
        else:
            lines.append(tr("Контейнер выключен — как он рисует, будет видно после "
                            "запуска."))
        lines.append(tr("Ставить заново ничего не нужно. Повторная настройка сохраняет "
                        "режим ускорения и настройки Merci; контейнер при ней "
                        "перезапускается, потребуется пароль."))
        self._choose(
            tr("Аппаратное ускорение уже включено"),
            "\n\n".join(lines),
            (
                ("close", tr("Понятно"), ""),
                ("setup", tr("Перенастроить"), "suggested" if broken else ""),
            ),
            lambda _answer: self._run_step_later(
                waydroid.nvidia_setup_step, tr("Ускорение настроено")
            ),
        )

    def _install_magisk(self) -> None:
        """Root в контейнере: обычная возможность своего же Android."""
        self._ask_host(waydroid.magisk_state, self._offer_magisk)

    def _offer_magisk(self, state: dict) -> None:
        have_file = state["file"]
        if state["installed"]:
            # Стоит — так и говорим: раньше пункт предлагал «установить»
            # всегда, как будто root нет.
            body = tr("Magisk Delta уже стоит в системе Android и запускается "
                      "вместе с ней. Ставить его заново нужно, только если root "
                      "перестал работать.")
            if have_file:
                body += "\n\n" + tr(
                    "Переустановка вернёт файлы Magisk на место; выданные "
                    "приложениям права и модули останутся. Контейнер "
                    "перезапустится, всё запущенное в нём закроется; потребуется "
                    "пароль."
                )
                self._confirm_step(
                    tr("Root уже установлен"), body, tr("Переустановить"),
                    waydroid.magisk_step, done_title=tr("Root установлен"),
                )
            else:
                self._choose(
                    tr("Root уже установлен"), body, (("close", tr("Понятно"), ""),),
                    lambda _answer: None,
                )
            return
        body = tr("Magisk Delta даст root внутри Waydroid — это ваш собственный "
                  "Android, так что доступ к системным разделам и модулям тут "
                  "нормальная вещь.\n\nПроверку устройства играми это не проходит: "
                  "для них контейнер с root выглядит наоборот подозрительнее.")
        if have_file:
            body += "\n\n" + tr(
                "Merci ставит только сборку Magisk Delta с известной контрольной "
                "суммой. Контейнер перезапустится, всё запущенное в нём закроется; "
                "потребуется пароль."
            )
        else:
            # Обещать установку, которой не из чего состояться, нечестно.
            body += "\n\n" + tr(
                "Merci ставит только сборку Magisk Delta с известной контрольной "
                "суммой, а этого файла на компьютере нет: место, откуда он "
                "раздавался, удалено с GitHub. Merci поищет файл в «Загрузках» и "
                "попробует скачать; не найдёт — откажется до запроса пароля, "
                "ничего не изменив."
            )
        self._confirm_step(
            tr("Установить root в контейнере?"),
            body,
            tr("Установить") if have_file else tr("Попробовать"),
            waydroid.magisk_step,
            done_title=tr("Root установлен"),
        )

    def _remove_magisk(self) -> None:
        self._ask_host(waydroid.magisk_state, self._offer_unroot)

    def _offer_unroot(self, state: dict) -> None:
        if not state["installed"] and not state["traces"]:
            self._toast(tr("Root не установлен — убирать нечего"))
            return
        if state["installed"]:
            heading = tr("Убрать root из контейнера?")
            body = tr("Magisk Delta будет снят вместе со своим приложением. Модули, "
                      "которые вы через него ставили, перестанут работать.\n\n"
                      "Контейнер перезапустится, всё запущенное в нём закроется; "
                      "потребуется пароль.")
            label = tr("Убрать")
        else:
            heading = tr("Убрать следы root?")
            body = tr("Сам root уже снят, но от него остались следы: служебные "
                      "файлы в системе Android или приложение Magisk. Merci уберёт "
                      "их.\n\nКонтейнер перезапустится, всё запущенное в нём "
                      "закроется; потребуется пароль.")
            label = tr("Убрать следы")
        self._confirm_step(
            heading,
            body,
            label,
            lambda: waydroid.magisk_step(remove=True),
            destructive=True,
            done_title=tr("Root убран"),
            on_result=self._magisk_removed,
        )

    def _magisk_removed(self, ok: bool, _key: str) -> None:
        """После удаления root снимаем и приложение Magisk: без него оно
        бесполезно, а выглядит так, будто root остался. Молча и без пароля —
        не вышло, значит, приложение просто останется в списке."""
        if ok and not self._closing:
            hostexec.in_thread(waydroid.forget_magisk_app)

    def _show_crash_log(self) -> None:
        """Отчёт о падении из контейнера: почему приложение закрылось."""
        self._toast(tr("Читаем отчёт о падении"))

        def shown(path: str, body: str) -> None:
            if path:
                body = tr("Файл: {path}", path=path) + "\n\n" + body
            self._present_crash_log(body)

        waydroid.crash_log_async(shown)

    def _present_crash_log(self, text: str) -> None:
        if self._closing:
            return
        view = Gtk.TextView(editable=False, monospace=True)
        view.get_buffer().set_text(text or tr("Записей о сбоях нет."))
        view.set_left_margin(12)
        view.set_right_margin(12)
        view.set_top_margin(8)
        view.set_bottom_margin(8)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.add_css_class("merci-log")
        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(view)

        dialog = Adw.Dialog(title=tr("Журнал сбоев Android"))
        dialog.set_content_width(820)
        dialog.set_content_height(560)
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        copy = Gtk.Button(icon_name="edit-copy-symbolic")
        copy.set_tooltip_text(tr("Скопировать"))
        copy.connect("clicked", lambda *_: self.get_clipboard().set(text or ""))
        header.pack_end(copy)
        toolbar.add_top_bar(header)
        toolbar.set_content(scroller)
        dialog.set_child(toolbar)
        self._ask(dialog)

    # -- обновления ---------------------------------------------------------

    def show_updates(self) -> None:
        self._show_updates()

    @staticmethod
    def _version() -> str:
        from .main import VERSION

        return VERSION

    def _check_updates_if_due(self) -> bool:
        """Проверка по расписанию: не чаще раза в сутки и только если её
        не выключили. Отдаёт False — для одноразового таймера."""
        due = time.time() - self.settings.updates_checked_at > 20 * 3600
        if not self._closing and not self._updating and self.settings.auto_update_check and due:
            self._check_updates(announce=True)
        return False

    def _check_updates(self, announce: bool = False) -> None:
        """Смотрит, что вышло нового. Ничего не ставит.

        ``announce`` — проверка шла сама, по расписанию: о находке надо
        сказать. При проверке по нажатию человек и так смотрит на список.
        """
        if self._updates_checking or self._updating:
            return
        self._updates_checking = True
        if self._updates_dialog is not None:
            self._updates_dialog.set_checking(True)
        hide = self.settings.hide_android_updater
        version = self._version()

        def work():
            report = waydroid.check_updates(version)
            hidden = 0
            if hide:
                with suppress(Exception):
                    hidden = waydroid.hide_android_updater()
            return report, hidden

        def done(result, error) -> None:
            self._updates_checking = False
            if self._closing:
                return
            if error is not None or result is None:
                if self._updates_dialog is not None:
                    self._updates_dialog.set_checking(False)
                    self._updates_dialog.set_report(self._updates)
                if not announce:
                    self._toast(tr("Проверить обновления не удалось: {error}", error=error))
                return
            report, hidden = result
            remembered = self.settings.updates_found
            known = {(item.get("key"), item.get("available")) for item in remembered}
            self._updates = report
            found = [
                {"key": item.key, "title": item.title, "available": item.available}
                for item in report.updates
            ]
            if report.notes:
                # Часть проверок не удалась (нет сети, молчит сервер): о чём
                # не узнали — помним прежнее, и срок следующей проверки не
                # отодвигаем на сутки. Иначе отметка об обновлении пропадала
                # бы от первого же обрыва связи.
                seen = {item["key"] for item in found}
                found += [item for item in remembered if item.get("key") not in seen]
            else:
                self.settings.updates_checked_at = report.checked_at
            self.settings.updates_found = found
            self._render_updates()
            fresh = [item for item in report.updates if (item.key, item.available) not in known]
            # Саму Merci — если это не требует пароля — ставим сразу: ради
            # этого проверка и идёт сама. Остальное просит пароль, и решает
            # человек.
            own = report.find("merci")
            if (
                announce
                and own is not None
                and own.kind == updates.FLATPAK
                and not own.root
                and self.settings.auto_update_merci
            ):
                fresh = [item for item in fresh if item.key != "merci"]
                self._update_merci(own, quiet=True)
            if announce and fresh:
                self._toast(
                    tr("Доступны обновления: {what}",
                       what=", ".join(item.title for item in fresh)),
                    tr("Показать"), self._show_updates, 10, action="app.show-updates",
                )
            elif hidden:
                self._toast(
                    tr("Встроенный обновлятор Android скрыт: обновления ставит Merci"),
                    timeout=7,
                )

        hostexec.in_thread(work, done)

    def _render_updates(self) -> None:
        """Отметка об обновлениях в библиотеке и в меню, список в окне."""
        report = self._updates
        if report is not None and not report.notes:
            count, text = len(report.updates), updates.summary(report)
        else:
            # Проверки в этом запуске ещё не было или она удалась не вся —
            # показываем, что помним.
            found = self.settings.updates_found
            count = len(found)
            text = ", ".join(str(item.get("title", "")) for item in found)
        self.shell.set_updates(count, text)
        if self._menu_app is not None:
            self._menu_app.remove(0)
            self._menu_app.insert(
                0,
                tr("Обновления: {count}", count=count) if count else tr("Обновления"),
                "win.updates",
            )
        if self._updates_dialog is not None:
            self._updates_dialog.set_checking(self._updates_checking)
            if not self._updates_checking:
                self._updates_dialog.set_report(report)

    def _show_updates(self) -> None:
        if self._updates_dialog is not None:
            return
        if self._updating:
            # Уведомление «Доступны обновления» могло привести сюда посреди
            # работы под root — поверх её мастера. Список оттуда всё равно
            # ничего бы не запустил, а «Удалить копию образа» — запустило бы.
            self._toast(self._lock_notice)
            return
        dialog = UpdatesDialog(
            on_check=self._check_updates,
            on_apply=self._apply_update,
            on_apply_all=self._apply_all_updates,
            on_restore=self._restore_images,
            on_drop_backup=self._drop_image_backup,
            on_auto=self._set_auto_updates,
            on_hide_updater=self._set_hide_updater,
            auto=self.settings.auto_update_check,
            hide_updater=self.settings.hide_android_updater,
        )
        dialog.connect("closed", self._on_updates_closed)
        self._updates_dialog = dialog
        dialog.set_report(self._updates)
        self._ask(dialog)
        if self._updates_checking:
            dialog.set_checking(True)
        elif self._updates is None or time.time() - self._updates.checked_at > 600:
            self._check_updates()

    def _on_updates_closed(self, dialog) -> None:
        if self._updates_dialog is dialog:
            self._updates_dialog = None

    def _set_auto_updates(self, active: bool) -> None:
        self.settings.auto_update_check = active

    def _set_hide_updater(self, active: bool) -> None:
        self.settings.hide_android_updater = active

        def done(reached, error) -> None:
            if self._closing or error is not None:
                return
            # Скрыть и вернуть можно только в работающем окне. До остальных
            # выбор дойдёт при их запуске (_sync_containers) — так и говорим:
            # раньше «возвращён» писалось и тогда, когда не было возвращено
            # ни в одном.
            if active:
                text = (
                    tr("Встроенный обновлятор Android скрыт")
                    if reached
                    else tr("Встроенный обновлятор Android скроется при запуске Android")
                )
            else:
                text = (
                    tr("Встроенный обновлятор Android возвращён")
                    if reached
                    else tr("Встроенный обновлятор Android вернётся при запуске Android")
                )
            self._toast(text)

        hostexec.in_thread(lambda: waydroid.apply_updater_choice(active), done)

    def _apply_update(self, update: updates.Update) -> None:
        """Нажали «Обновить» у одной строки."""
        if update.kind == updates.MANUAL:
            self._explain_update(update)
            return
        if update.kind == updates.FLATPAK:
            self._choose(
                tr("Обновить Merci?"),
                update.detail,
                (("cancel", tr("Отмена"), ""), ("go", tr("Обновить"), "suggested")),
                lambda _answer: self._update_merci(update),
            )
            return
        self._confirm_updates([update])

    def _apply_all_updates(self) -> None:
        if self._updates is None:
            return
        items = [item for item in self._updates.updates if item.kind == updates.STEP]
        if items:
            self._confirm_updates(items)

    def _confirm_updates(self, items: list[updates.Update]) -> None:
        if self._busy or self._window_ops or self._updating:
            self._toast(tr("Дождитесь конца текущей операции"))
            return
        lines = []
        for item in items:
            head = f"{item.title}: {item.current} → {item.available}" if len(items) > 1 else ""
            lines.append("\n".join(part for part in (head, item.detail) if part))
        size = sum(item.size_mb for item in items)
        tail = [tr("Всё запущенное в Android закроется. Понадобится пароль.")]
        if size:
            tail.append(tr("Загрузка — около {size} МБ.", size=size))
        self._choose(
            tr("Обновить «{title}»?", title=items[0].title)
            if len(items) == 1
            else tr("Установить обновления: {count}?", count=len(items)),
            "\n\n".join(lines + [" ".join(tail)]),
            (("cancel", tr("Отмена"), ""), ("go", tr("Обновить"), "suggested")),
            lambda _answer: self._run_updates(items),
        )

    def _run_updates(self, items: list[updates.Update]) -> None:
        """Готовит шаги (они читают настройки контейнера — это в потоке) и
        открывает мастер."""
        if not self._begin_update(tr("Идёт обновление Waydroid…")):
            return
        keys = [item.key for item in items]

        def work():
            before = waydroid.system_snapshot()
            steps = []
            for item in items:
                steps += waydroid.update_steps(item)
            return before, steps

        def done(result, error) -> None:
            if self._closing:
                return
            if error is not None or result is None:
                self._end_update()
                self._error(tr("Не удалось подготовить обновление"), str(error))
                return
            before, steps = result
            if self._updates_dialog is not None:
                self._updates_dialog.close()
            self._ask(
                StepsDialog(
                    self,
                    steps,
                    on_result=lambda ok, key: self._updates_finished(ok, key, keys, before),
                    title=tr("Обновление"),
                    done_title=tr("Обновление установлено"),
                    autostart=True,
                    cancellable=False,
                )
            )

        hostexec.in_thread(work, done)

    def _begin_update(self, text: str, setup: bool = False) -> bool:
        """Занимает всё под обновление Waydroid — до самого конца мастера.

        ``setup`` — то же для разового шага из меню (root, транслятор,
        перезапуск контейнера): замок тот же, слова другие.
        """
        if self._busy or self._window_ops or self._updating:
            self._toast(tr("Дождитесь конца текущей операции"))
            return False
        self._updating = True
        self._lock_summary = (
            tr("идёт настройка Waydroid") if setup else tr("идёт обновление Waydroid")
        )
        self._lock_notice = (
            tr("Идёт настройка Waydroid — дождитесь её конца") if setup
            else tr("Идёт обновление Waydroid — дождитесь его конца")
        )
        self._window_ops.add(0)
        self._container_work = (
            "update", tr("настраивается…") if setup else tr("обновляется…")
        )
        self._instance_stage(text)
        self._render_windows()
        self._render_container()
        self._render_action()
        return True

    def _end_update(self) -> None:
        self._updating = False
        self._window_ops.discard(0)
        self._container_work = None
        waydroid.forget_state()
        waydroid.forget_builds()
        if self._closing:
            return
        self._last_status = None
        self._after_op()

    def _updates_finished(self, ok: bool, key: str, keys: list[str], before: dict) -> None:
        """Мастер закончил: сверяем, что после обновления всё работает."""
        self._end_update()
        if self._closing:
            return
        if not ok:
            # Что именно не вышло, показывает сам мастер. Здесь — только то,
            # чего он не знает: если образ уже сменился, а сессия с ним не
            # поднялась, есть куда вернуться. Если образ прежний (не ввели
            # пароль, оборвалась загрузка) — возвращать нечего.
            def failed(changed, _error) -> None:
                if self._closing:
                    return
                if changed:
                    self._offer_image_restore(
                        tr("Новый образ Android установлен, но Waydroid с ним не поднялся.")
                    )
                elif key in ("update-package", "nvidia-setup"):
                    # Пакет мог встать, а настройка ускорения — нет: тогда
                    # контейнер не стартует, пока её не повторят. Мастер
                    # подготовки это видит и предложит.
                    self._choose(
                        tr("Обновление не завершилось"),
                        tr("Что пошло не так — в журнале мастера. Если пакет уже "
                           "обновился, а настройка ускорения не прошла, Waydroid не "
                           "запустится, пока её не повторить: это сделает "
                           "«Подготовить Waydroid»."),
                        (("close", tr("Закрыть"), ""), ("setup", tr("Подготовить"), "suggested")),
                        lambda _answer: self.show_installer(),
                        default="setup",
                    )
                self._check_updates()

            hostexec.in_thread(lambda: waydroid.images_changed(before), failed)
            return

        def done(result, error) -> None:
            if self._closing:
                return
            self._check_updates()
            if self.selected is not None:
                self._refresh_runtime_state(self.selected, quiet=True)
            problems, changed = result if result is not None else ([str(error)], False)
            if not problems:
                self._toast(
                    tr("Обновление установлено: Android отвечает, настройки на месте"),
                    timeout=8,
                )
                return
            text = tr("После обновления не всё в порядке:") + "\n" + "\n".join(
                f"• {problem}" for problem in problems
            )
            if changed:
                self._offer_image_restore(text)
            else:
                self._choose(
                    tr("Обновление установлено, но…"),
                    text,
                    (("close", tr("Закрыть"), ""), ("setup", tr("Подготовить"), "suggested")),
                    lambda _answer: self.show_installer(),
                    default="setup",
                )

        hostexec.in_thread(
            lambda: (waydroid.verify_after_update(before), waydroid.images_changed(before)),
            done,
        )

    def _offer_image_restore(self, reason: str) -> None:
        """Новый образ не прижился — если прежний сохранён, предлагаем его."""

        def done(report, _error) -> None:
            if self._closing:
                return
            if report is None or not report.backup:
                self._choose(
                    tr("Обновление не прижилось"),
                    reason,
                    (("close", tr("Закрыть"), ""), ("setup", tr("Подготовить"), "suggested")),
                    lambda _answer: self.show_installer(),
                    default="setup",
                )
                return
            self._updates = report
            self._render_updates()

            def answer(response: str) -> None:
                if response == "restore":
                    self._run_restore()
                else:
                    self.show_installer()

            self._choose(
                tr("Обновление не прижилось"),
                reason + "\n\n" + tr(
                    "Прежний образ Android сохранён: его можно вернуть — приложения "
                    "и данные останутся как есть."
                ),
                (
                    ("close", tr("Закрыть"), ""),
                    ("setup", tr("Подготовить"), ""),
                    ("restore", tr("Вернуть прежний образ"), "suggested"),
                ),
                answer,
                default="restore",
            )

        version = self._version()
        hostexec.in_thread(lambda: waydroid.check_updates(version), done)

    def _restore_images(self) -> None:
        if self._busy or self._window_ops or self._updating:
            # Спросить и отказать уже после ответа было бы издёвкой.
            self._toast(tr("Дождитесь конца текущей операции"))
            return
        self._choose(
            tr("Вернуть прежний образ Android?"),
            tr("Нынешний образ заменится копией, сделанной перед обновлением. "
               "Приложения и их данные останутся. Всё запущенное в Android "
               "закроется, понадобится пароль."),
            (("cancel", tr("Отмена"), ""), ("restore", tr("Вернуть"), "suggested")),
            lambda _answer: self._run_restore(),
        )

    def _run_restore(self) -> None:
        if not self._begin_update(tr("Возвращаем прежний образ Android…")):
            return
        if self._updates_dialog is not None:
            self._updates_dialog.close()

        def finished(_ok: bool, _key: str) -> None:
            self._end_update()
            if not self._closing:
                self._check_updates()

        def prepared(step, error) -> None:
            if self._closing:
                return
            if error is not None or step is None:
                self._end_update()
                self._error(tr("Не удалось подготовить возврат"), str(error))
                return
            self._ask(
                StepsDialog(
                    self, [step], on_result=finished,
                    title=tr("Возврат образа"),
                    done_title=tr("Прежний образ возвращён"),
                    autostart=True,
                    cancellable=False,
                )
            )

        hostexec.in_thread(waydroid.images_restore_step, prepared)

    def _drop_image_backup(self) -> None:
        def busy() -> bool:
            # Копию нельзя стирать, пока обновление её делает или возврат ею
            # пользуется. Проверяем и до вопроса, и после ответа на него.
            if self._busy or self._window_ops or self._updating:
                self._toast(tr("Дождитесь конца текущей операции"))
                return True
            return False

        def drop() -> None:
            if busy():
                return
            self._toast(tr("Удаляем копию прежнего образа…"))

            def done(_result, error) -> None:
                if self._closing:
                    return
                if error is not None:
                    self._error(tr("Не удалось удалить копию"), str(error))
                else:
                    self._toast(tr("Копия прежнего образа удалена"))
                self._check_updates()

            hostexec.in_thread(waydroid.drop_image_backup, done)

        if busy():
            return
        self._choose(
            tr("Удалить копию прежнего образа?"),
            tr("Вернуться к прежнему образу Android после этого будет нельзя. "
               "Понадобится пароль."),
            (("cancel", tr("Отмена"), ""), ("drop", tr("Удалить"), "destructive")),
            lambda _answer: drop(),
        )

    def _explain_update(self, update: updates.Update) -> None:
        """Обновление, которое ставит человек: говорим чем."""
        web = update.command.startswith(("https://", "http://"))

        def act(_answer: str) -> None:
            if web:
                Gtk.UriLauncher.new(update.command).launch(self, None, None)
            else:
                self.get_clipboard().set(update.command)
                self._toast(tr("Команда скопирована"))

        body = update.detail
        if update.command and not web:
            body += "\n\n" + update.command
        self._choose(
            tr("{title}: {version}", title=update.title, version=update.available),
            body,
            (
                ("close", tr("Закрыть"), ""),
                ("act", tr("Открыть страницу") if web else tr("Скопировать команду"),
                 "suggested"),
            ),
            act,
            default="act",
        )

    def _update_merci(self, update: updates.Update, quiet: bool = False) -> None:
        """Обновляет саму Merci. ``quiet`` — обновление шло само, по
        расписанию: о неудаче тогда молчим (повторится завтра), а не
        выскакиваем с диалогом посреди чужих дел."""
        if self._merci_updating:
            # Проверка по расписанию и кнопка «Обновить» могли сойтись: два
            # `flatpak update` рядом только мешали бы друг другу.
            if not quiet:
                self._toast(tr("Merci уже обновляется"))
            return
        self._merci_updating = True
        if not quiet:
            self._toast(tr("Обновляем Merci…"), timeout=8)

        def done(_result, error) -> None:
            self._merci_updating = False
            if self._closing:
                return
            if error is not None:
                if not quiet:
                    self._error(tr("Merci не обновилась"), str(error))
                return
            self._check_updates()
            self._toast(
                tr("Merci обновлена — новая версия заработает после перезапуска"),
                tr("Перезапустить"), self._restart_app, 12,
            )

        hostexec.in_thread(lambda: waydroid.update_merci(update), done)

    def _restart_app(self) -> None:
        """Перезапускает Merci: новую копию поднимает хост — уже после того,
        как эта вышла (приложение одно на сеанс, вторая копия не стартует)."""
        if self._busy or self._window_ops or self._updating:
            # Выход оборвал бы идущую работу на середине.
            self._toast(tr("Дождитесь конца текущей операции"))
            return
        # Новую копию поднимаем, когда эта уже вышла: иначе запуск просто
        # активировал бы её же и на этом закончился.
        relaunch = (
            "for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do "
            f"flatpak ps --columns=application 2>/dev/null | grep -qx {updates.APP_ID} || break; "
            "sleep 0.5; done; "
            f"flatpak run {updates.APP_ID} >/dev/null 2>&1 &"
        )
        with suppress(GLib.Error):
            Gio.Subprocess.new(
                hostexec.host_argv(["sh", "-c", relaunch]), Gio.SubprocessFlags.NONE
            )
        self._quit_app()

    # -- настройки ----------------------------------------------------------

    def _show_settings(self) -> None:
        page = Adw.PreferencesPage()

        look = Adw.PreferencesGroup(title=tr("Оформление"))
        scheme_row = Adw.ComboRow(
            title=tr("Тема"),
            subtitle=tr("цвета берутся из значка выбранного приложения"),
        )
        schemes = ("system", "light", "dark")
        scheme_row.set_model(
            Gtk.StringList.new([tr("Как в системе"), tr("Светлая"), tr("Тёмная")])
        )
        current_scheme = self.settings.color_scheme
        scheme_row.set_selected(
            schemes.index(current_scheme) if current_scheme in schemes else 0
        )

        def scheme_changed(row, _param) -> None:
            index = row.get_selected()
            if 0 <= index < len(schemes) and schemes[index] != self.settings.color_scheme:
                self.settings.color_scheme = schemes[index]
                apply_color_scheme(schemes[index])

        scheme_row.connect("notify::selected", scheme_changed)
        look.add(scheme_row)

        glass_row = Adw.SwitchRow(
            title=tr("Меньше прозрачности"),
            subtitle=tr("плотные поверхности вместо стекла: легче для слабой "
                        "видеокарты и для глаз"),
        )
        glass_row.set_active(self.settings.reduced_glass)

        def glass_changed(row, _param) -> None:
            self.settings.reduced_glass = row.get_active()
            self.shell.set_reduced_glass(row.get_active())

        glass_row.connect("notify::active", glass_changed)
        look.add(glass_row)

        # Язык: текст у виджетов задаётся при постройке, поэтому вид
        # пересобирается сразу.
        language_row = Adw.ComboRow(
            title=tr("Язык интерфейса"),
            subtitle=tr("сохраняется; окно перерисуется сразу"),
        )
        codes = list(LANGUAGES)
        language_row.set_model(Gtk.StringList.new([LANGUAGES[code] for code in codes]))
        current = self.settings.language
        language_row.set_selected(codes.index(current) if current in codes else 0)
        language_row.connect(
            "notify::selected",
            lambda row, _p: self._on_language_chosen(
                codes[row.get_selected()] if row.get_selected() < len(codes) else ""
            ),
        )
        look.add(language_row)
        page.add(look)

        window_group = Adw.PreferencesGroup(title=tr("Окно"))
        minimize_row = Adw.SwitchRow(
            title=tr("Сворачивать Merci при запуске приложения"),
            subtitle=tr("окно прячется в трей через пару секунд после запуска; "
                        "вернуть — нажатием на значок"),
        )
        minimize_row.set_active(self.settings.minimize_on_launch)
        minimize_row.connect(
            "notify::active",
            lambda row, _p: setattr(self.settings, "minimize_on_launch", row.get_active()),
        )
        window_group.add(minimize_row)
        page.add(window_group)

        group = Adw.PreferencesGroup(
            title=tr("Профили Android"),
            description=tr("Профили Android дают одному приложению отдельные "
                           "данные: свой вход, свой кеш, свои настройки. Так запускают "
                           "второй аккаунт, не выходя из первого.\n\n"
                           "Двух разных сборок одного пакета это не даёт и дать не может: "
                           "имя пакета для Android — одна установка на всё устройство, "
                           "профили делят между собой код приложения. Разным сборкам "
                           "нужны разные окна."),
        )
        multiuser_row = Adw.SwitchRow(
            title=tr("Использовать MultiUser"),
            subtitle=tr("в карточке появится выбор профиля; контейнер "
                        "переключается на нужный при запуске"),
        )
        multiuser_row.set_active(self.settings.multiuser)
        multiuser_row.connect("notify::active", self._on_multiuser_toggled)
        group.add(multiuser_row)

        # Приложения в разных профилях работают одновременно, но показать
        # оба окна контейнер может только в многооконном режиме — иначе
        # рисует что-то одно.
        multiwindow_row = Adw.SwitchRow(
            title=tr("Отдельные окна для приложений"),
            subtitle=tr("копия и оригинал становятся видны одновременно; "
                        "полноэкранные игры это не берут — Android показывает их по "
                        "очереди. Контейнер перезапустится"),
        )
        multiwindow_row.set_sensitive(False)
        group.add(multiwindow_row)
        page.add(group)

        fresh = Adw.PreferencesGroup(
            title=tr("Обновления"),
            description=tr("Образ Android, пакет Waydroid, транслятор ARM и сама "
                           "Merci. Проверка ничего не ставит — ставите вы, из окна "
                           "обновлений."),
        )
        auto_row = Adw.SwitchRow(
            title=tr("Проверять обновления автоматически"),
            subtitle=tr("при запуске и раз в сутки; ставятся они по вашему нажатию"),
        )
        auto_row.set_active(self.settings.auto_update_check)
        auto_row.connect(
            "notify::active", lambda row, _p: self._set_auto_updates(row.get_active())
        )
        fresh.add(auto_row)
        own_row = Adw.SwitchRow(
            title=tr("Обновлять саму Merci без вопросов"),
            subtitle=tr("новая версия ставится в фоне и включается после "
                        "перезапуска; остальное Merci только предлагает"),
        )
        own_row.set_active(self.settings.auto_update_merci)
        own_row.connect(
            "notify::active",
            lambda row, _p: setattr(self.settings, "auto_update_merci", row.get_active()),
        )
        fresh.add(own_row)
        updater_row = Adw.SwitchRow(
            title=tr("Скрывать встроенный обновлятор Android"),
            subtitle=tr("он ставит образ по нажатию на своё уведомление — под "
                        "работающими окнами; обновления ставит Merci"),
        )
        updater_row.set_active(self.settings.hide_android_updater)
        updater_row.connect(
            "notify::active", lambda row, _p: self._set_hide_updater(row.get_active())
        )
        fresh.add(updater_row)
        check_row = Adw.ActionRow(
            title=tr("Проверить обновления"),
            subtitle=tr("откроет окно обновлений"),
            activatable=True,
        )
        check_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))

        def open_updates(*_args) -> None:
            if self._settings_dialog is not None:
                self._settings_dialog.close()
                self._settings_dialog = None
            self._show_updates()
            self._check_updates()

        check_row.connect("activated", open_updates)
        fresh.add(check_row)
        page.add(fresh)

        def show_multi_windows(result, _error) -> None:
            """Состояние берём у контейнера, а не из своих настроек: свойство
            могли поменять и мимо Merci."""
            if self._closing:
                return
            multiwindow_row.set_active(bool(result))
            multiwindow_row.set_sensitive(True)
            # Обработчик вешаем после подстановки: иначе она сама выглядела
            # бы как нажатие и перезапускала контейнер.
            multiwindow_row.connect("notify::active", self._on_multi_windows_toggled)

        hostexec.in_thread(waydroid.multi_windows_on, show_multi_windows)

        dialog = Adw.PreferencesDialog()
        dialog.add(page)
        # Ссылку держим: при смене языка вид пересобирается, и открытый
        # диалог остался бы на прежнем языке.
        self._settings_dialog = dialog
        dialog.connect("closed", self._on_settings_closed)
        self._ask(dialog)

    def _on_settings_closed(self, dialog) -> None:
        if self._settings_dialog is dialog:
            self._settings_dialog = None

    def _on_language_chosen(self, code: str) -> None:
        if not code or code == self.settings.language:
            return
        self.settings.language = code
        set_language(code)
        dialog = self._settings_dialog
        if dialog is not None:
            dialog.close()
            self._settings_dialog = None
        # Через idle_add: обработчик пришёл из сигнала строки выбора.
        GLib.idle_add(self.apply_language)

    def apply_language(self) -> bool:
        """Перерисовывает окно на новом языке.

        Вид собирается заново прямо в этом окне: текст у виджетов задаётся
        при постройке, а уничтожать само окно ради этого нельзя — у него
        трей, таймеры и ответы хоста в пути.
        """
        if self._closing:
            return GLib.SOURCE_REMOVE
        selected = self.selected.slug if self.selected else None
        self.shell = Shell(self, self._app_menu())
        self.shell.set_reduced_glass(self.settings.reduced_glass)
        self.set_content(self.shell)
        self.tray.set_items(self._tray_items())
        self._library_sent = None
        self.selected = None
        self.refresh(selected)
        self._render_updates()
        if self._stage_text:
            # Операция с окнами ещё идёт — её ход должен быть виден и в
            # пересобранном виде.
            self.shell.set_stage(self._stage_text)
        return GLib.SOURCE_REMOVE

    def _on_multi_windows_toggled(self, row, _param) -> None:
        if self._switch_quiet:
            return
        enabled = row.get_active()

        def revert() -> None:
            # Режим не переключился — и переключатель возвращается на место:
            # оставшись щёлкнутым, он показывал бы то, чего нет.
            self._switch_quiet = True
            row.set_active(not enabled)
            self._switch_quiet = False

        # Смена режима перезапускает основное окно и гасит дополнительные —
        # под тем же замком, что и любая операция со всеми окнами. Раньше
        # замка не было, и перезапуск шёл поверх запуска или обновления.
        if not self._begin_window_op(0):
            revert()
            return
        row.set_sensitive(False)
        self._container_work = ("restart", tr("перезапускается…"))
        self._render_container()
        self._instance_stage(tr("Перезапускаем контейнер…"))

        def done(_result, error) -> None:
            self._window_ops.discard(0)
            self._container_work = None
            waydroid.forget_state()
            if self._closing:
                return
            row.set_sensitive(True)
            if error is not None:
                revert()
                self._error(tr("Не удалось переключить режим окон"), str(error))
            else:
                self._toast(
                    tr("Каждое приложение теперь в своём окне")
                    if enabled
                    else tr("Контейнер снова показывает одно окно")
                )
            self._after_op()

        hostexec.in_thread(lambda: waydroid.set_multi_windows(enabled), done)

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
        self._choose(
            tr("Контейнер к этому не готов"),
            tr(
                "{detail}.\n\nMerci может подготовить его: разрешит Android "
                "нескольких пользователей, откроет себе доступ к контейнеру через "
                "adb и поставит android-tools. Контейнер перезапустится, "
                "потребуется пароль.",
                detail=detail,
            ),
            (
                ("later", tr("Потом"), ""),
                ("prepare", tr("Подготовить"), "suggested"),
            ),
            lambda _answer: self._run_step_later(waydroid.multiuser_step),
        )

    # -- мелочи ------------------------------------------------------------

    def _toast(self, message: str, button: str = "", on_button=None, timeout: int = 4,
               action: str = "") -> None:
        """Короткое сообщение. ``action`` — действие приложения, которое
        выполнит нажатие на системное уведомление, если окно спрятано."""
        if self._closing:
            return
        if not self.get_visible():
            # Окно спрятано в трей (действие пришло из его меню): строку
            # внутри окна никто не увидит.
            self._notify(message, action=action)
            return
        self.shell.toast(message, button, on_button, timeout)

    def _notify(self, title: str, body: str = "", action: str = "") -> None:
        """Системное уведомление. Номер у него один: новое заменяет прежнее,
        и ход длинного действия не копится стопкой."""
        application = self.get_application()
        if application is None:
            return
        note = Gio.Notification.new(title)
        if body:
            note.set_body(body)
        if action:
            note.set_default_action(action)
        application.send_notification("merci-status", note)

    def _ask(self, dialog: Adw.Dialog) -> None:
        """Показывает диалог, подняв окно, если оно спрятано в трей.

        Диалог привязан к окну, и у спрятанного окна он не виден: вопрос
        «Выключить Waydroid?» из меню трея уходил в никуда.
        """
        if self._closing:
            return
        if not self.get_visible():
            self.present_window()
        # Панель контейнера — не диалог, а слой окна: под вопросом она
        # осталась бы открытой и перехватывала бы Esc.
        self.shell.close_container_panel()
        dialog.present(self)

    def _choose(self, heading: str, body: str, answers, then, default: str = "") -> None:
        """Вопрос с вариантами ответа.

        ``answers`` — тройки (ключ, подпись, вид): вид — ``suggested``,
        ``destructive`` или пусто. Первый ответ — всегда отказ: им же
        закрывается диалог по Esc, и ``then`` на него не зовётся.
        """
        dialog = Adw.AlertDialog(heading=heading)
        self._set_dialog_text(dialog, body)
        looks = {
            "suggested": Adw.ResponseAppearance.SUGGESTED,
            "destructive": Adw.ResponseAppearance.DESTRUCTIVE,
        }
        for key, label, look in answers:
            dialog.add_response(key, label)
            if look in looks:
                dialog.set_response_appearance(key, looks[look])
        refuse = answers[0][0]
        dialog.set_close_response(refuse)
        dialog.set_default_response(default or refuse)
        dialog.connect(
            "response", lambda _dialog, response: response != refuse and then(response)
        )
        self._ask(dialog)

    @staticmethod
    def _set_dialog_text(dialog: Adw.AlertDialog, body: str) -> None:
        """Текст вопроса. Короткий — как обычно, по центру; длинный, в
        несколько абзацев, — по левому краю и в диалоге пошире: колонку
        текста, выровненную по центру, читать тяжело."""
        if "\n" not in body and len(body) < 140:
            dialog.set_body(body)
            return
        text = Gtk.Label(label=body, wrap=True, xalign=0, selectable=True)
        text.set_max_width_chars(56)
        text.add_css_class("merci-dialog-text")
        dialog.set_extra_child(text)
        dialog.set_prefer_wide_layout(True)

    def _error(self, heading: str, body: str) -> None:
        if self._closing:
            return
        dialog = Adw.AlertDialog(heading=heading)
        self._set_dialog_text(dialog, body)
        dialog.add_response("ok", tr("Понятно"))
        self._ask(dialog)
