"""Демонстрация вида: учебные данные и снимки состояний.

``Shell`` строится и наполняется **только через договор** (``api.py``) —
так же, как это делает логика окна. Снимки рисуются за кадром обвязкой
``.dev/design/shotlib.py``:

    .dev/design/shot.sh src/merci/ui/demo.py .dev/design/final/shots          # среда Merci
    .dev/design/shot.sh src/merci/ui/demo.py .dev/design/final/shots host     # GTK хоста
"""

from __future__ import annotations

import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from merci import i18n, theme
from merci.ui import api
from merci.ui.api import (AppPage, LibraryItem, Notice, Profiles, WindowsHeader, WindowTile)

HERE = os.path.dirname(os.path.abspath(__file__))
ICONS = os.path.normpath(os.path.join(HERE, "..", "..", "..", ".dev", "design", "icons"))


def icon_path(key: str) -> str:
    return os.path.join(ICONS, f"{key}.png")


class DemoHandlers:
    """Обработчики-заглушки: печатают нажатия (видно, что программные
    обновления их не зовут)."""

    def __getattr__(self, name: str):
        def handler(*args):
            print(f"нажатие: {name}{args}", flush=True)
        return handler


# -- учебные данные ---------------------------------------------------------

APPS = [
    ("roblox", "Roblox", "2.745.1142", False, "com.roblox.client", "ARM64", "159.8\u00a0МБ", 2),
    ("delta", "Delta 735", "2.735.1138", False, "com.roblox.client", "ARM64", "158.1\u00a0МБ", 0),
    ("ember", "Ember Quest", "1.4.2", True, "com.emberquest.game", "x86_64", "412.6\u00a0МБ", 1),
    ("mint", "Mint Notes", "3.0.1", False, "app.mintnotes", "ARM64", "24.3\u00a0МБ", 0),
    ("orchid", "Orchid Player", "12.8", False, "tv.orchid.player", "ARM64", "61.0\u00a0МБ", 0),
    ("sun", "Sunny Farm", "0.9.7", True, "com.sunnyfarm.game", "x86_64", "288.4\u00a0МБ", 0),
    ("apk", "com roblox client 2 734 916 apkmirror", "2.734.916", True, "com.roblox.client",
     "x86_64", "151.2\u00a0МБ", 0),
]


def library(selected: str | None, running: dict[str, int] | None = None) -> list[LibraryItem]:
    items = []
    for key, name, version, native, _pkg, _abi, _size, opened in APPS:
        items.append(LibraryItem(
            slug=key, name=name, version=version, icon=icon_path(key),
            badge="x86_64" if native else "ARM", native=native,
            badge_hint="код под этот процессор" if native else "нужна трансляция ARM",
            open_windows=(running or {}).get(key, opened)))
    return items


def page(key: str, en: bool = False) -> AppPage:
    _k, name, version, native, pkg, abi, size, _o = next(a for a in APPS if a[0] == key)
    if en:
        return AppPage(
            slug=key, name=name, icon=icon_path(key),
            overline="ANDROID APP",
            version=f"version {version}", package=pkg,
            chips=("x86_64 · native" if native else "ARM · through translation",
                   size.replace("МБ", "MB")),
            facts=(("package", "Package", pkg),
                   ("activity", "Activity", f"{pkg}.startup.LauncherAliasMain"),
                   ("abi", "Architecture", abi),
                   ("size", "Takes up", size.replace("МБ", "MB")),
                   ("last", "Last launched", "a moment ago")))
    return AppPage(
        slug=key, name=name, icon=icon_path(key),
        # Надзаголовок не повторяет архитектуру: она — одним чипом ниже.
        overline="ANDROID-ПРИЛОЖЕНИЕ",
        version=f"версия {version}", package=pkg,
        chips=("x86_64 · напрямую" if native else "ARM · через трансляцию", size),
        facts=(("package", "Пакет", pkg),
               ("activity", "Activity", f"{pkg}.startup.LauncherAliasMain"),
               ("abi", "Архитектура", abi),
               ("size", "Занимает места", size),
               ("last", "Последний запуск", "только что")))


ALL = ("open", "close", "restart", "power_off", "remove", "resize")


def tile(n: int, state: str, text: str, actions: tuple[str, ...], en: bool = False) -> WindowTile:
    if en:
        return WindowTile(number=n, title=f"Window {n}", role="main" if n == 1 else "own Android",
                          state=state, text=text, main=n == 1, actions=actions)
    return WindowTile(number=n, title=f"Окно {n}", role="основное" if n == 1 else "свой Android",
                      state=state, text=text, main=n == 1, actions=actions)


ROBLOX_TILES = [
    tile(1, api.WINDOW_OPEN, "приложение открыто", ("close", "resize", "restart", "power_off")),
    tile(2, api.WINDOW_BACKGROUND, "приложение работает в фоне — окна на экране нет",
         ("open", "close", "resize", "restart", "power_off", "remove")),
    tile(3, api.WINDOW_OFF, "выключено", ("open", "resize", "remove")),
    tile(4, api.WINDOW_DEAD, "Android не отвечает — перезапустите окно",
         ("restart", "power_off", "remove")),
]


def seed(key: str | None) -> str | None:
    return theme.seed_from_png(icon_path(key)) if key else None


# -- сценарий как у логики ---------------------------------------------------

def fill_container(shell, en: bool = False) -> None:
    if en:
        shell.set_container(api.CONTAINER_READY, "session running")
        shell.set_container_controls(True, "Stop the extra windows first — they keep the "
                                           "container busy", True)
        shell.set_bridge("libhoudini.so · ARM64 → x86_64 translation", alert=True)
        shell.set_renderer("hardware (through Venus)")
    else:
        shell.set_container(api.CONTAINER_READY, "сессия запущена")
        shell.set_container_controls(True, "", True)
        shell.set_bridge("libhoudini.so · ARM64 → x86_64")
        shell.set_renderer("аппаратный (через Venus)")
    shell.set_window_size("1920x1080")
    shell.set_quick(True, 60)


def show_roblox(shell) -> None:
    shell.set_seed(seed("roblox"))
    shell.set_library(library("roblox"), "roblox")
    shell.show_page(page("roblox"))
    shell.set_notice(Notice("adb контейнера открыт без пароля — стоит закрыть его ключом",
                            "Подготовить", "warning"))
    shell.set_build("эта — можно запускать", True)
    shell.set_profiles(Profiles(visible=True, labels=("основной", "второй", "чистый"),
                                selected=0, hint="общие данные приложения"))
    shell.set_can_uninstall(True)
    shell.set_action("Запустить")
    shell.set_summary("2 из 4 окон открыто · сборка совпадает")
    shell.set_stage("")
    shell.set_windows(ROBLOX_TILES, WindowsHeader(summary="4 окна · открыто 2 · каждое — свой Android",
                                                  can_power_off_all=True))
    shell.set_updates(2, "образ Android 20.0.3 · waydroid-nvidia 1.6.1")


STATE = {}


def build(app: Adw.Application, ctx) -> Gtk.Window:
    from merci.ui.shell import Shell

    win = Adw.ApplicationWindow(application=app, title="Merci")
    menu = Gio.Menu()
    for text in ("Подготовить Waydroid", "Журнал сбоев Android", "Настройки", "О приложении"):
        menu.append(text, "app.noop")
    shell = Shell(DemoHandlers(), menu)
    # Порядок нарочно «неудобный»: сборка и контейнер раньше страницы.
    shell.set_build("эта — можно запускать", True)
    fill_container(shell)
    win.set_content(shell)
    show_roblox(shell)
    STATE.update(win=win, shell=shell, menu=menu)
    return win


def main_dark(win, ctx) -> None:
    ctx.set_dark(True)


def main_light(win, ctx) -> None:
    ctx.set_dark(False)


def alt_dark(win, ctx) -> None:
    ctx.set_dark(True)
    shell = STATE["shell"]
    shell.set_seed(seed("ember"))
    shell.set_library(library("ember", {"roblox": 2, "ember": 1}), "ember")
    shell.show_page(page("ember"))
    shell.set_build("другая (версия 1.4.0) — при запуске Merci предложит заменить", False)
    shell.set_action("Устанавливаем в контейнер…", busy=True, enabled=False)
    shell.set_summary("окно 3 · не закрывайте Merci")
    shell.set_stage("Запускаем окно 3… (до трёх минут)")
    shell.set_container(api.CONTAINER_BUSY, "поднимается…")
    shell.set_windows([
        tile(1, api.WINDOW_OPEN, "приложение открыто", ("close", "resize", "restart", "power_off")),
        tile(2, api.WINDOW_IDLE, "Android работает · приложение закрыто",
             ("open", "resize", "restart", "power_off", "remove")),
        tile(3, api.WINDOW_BOOTING, "Android запускается — сети ещё нет", ("power_off",)),
        tile(4, api.WINDOW_BUSY, "идёт операция…", ALL),
    ], WindowsHeader(summary="4 окна · открыто 1 · идёт запуск", busy=True))


def states_dark(win, ctx) -> None:
    ctx.set_dark(True)
    shell = STATE["shell"]
    shell.set_container(api.CONTAINER_READY, "сессия запущена")
    shell.set_action("Запустить")
    shell.set_seed(seed("delta"))
    shell.set_library(library("delta"), "delta")
    shell.show_page(page("delta"))
    shell.set_notice(Notice("Транслятор ARM устарел: приложения зависают при запуске",
                            "Сменить транслятор", "error"))
    shell.set_build("не установлена — поставится при запуске", None)
    shell.set_summary("окна не открыты")
    shell.set_windows([
        tile(1, api.WINDOW_IDLE, "Android работает · приложение закрыто",
             ("open", "resize", "restart", "power_off")),
        tile(2, api.WINDOW_ABSENT, "Android работает · приложения здесь ещё нет",
             ("open", "resize", "restart", "power_off", "remove")),
        tile(3, api.WINDOW_UNKNOWN, "Android пока не отвечает — ждём", ("power_off", "remove")),
        tile(4, api.WINDOW_FOREIGN, "здесь стоит другая сборка (2.734.916) — при открытии заменим",
             ("open", "resize", "restart", "power_off", "remove")),
    ], WindowsHeader(summary="4 окна · открытых нет", can_power_off_all=True))
    STATE["shell"].scroller.get_vadjustment().set_value(150)


def windows_error_dark(win, ctx) -> None:
    ctx.set_dark(True)
    shell = STATE["shell"]
    shell.scroller.get_vadjustment().set_value(0)
    shell.set_notice(None)
    shell.set_windows_error("Не удалось прочитать окна",
                            "adb: device offline <emulator-5554> & ещё 2 устройства — "
                            "проверьте контейнер и попробуйте снова")


def container_dark(win, ctx) -> None:
    ctx.set_dark(True)
    shell = STATE["shell"]
    show_roblox(shell)
    shell.open_container_panel()


def container_light(win, ctx) -> None:
    ctx.set_dark(False)


def narrow_dark(win, ctx) -> None:
    ctx.set_dark(True)
    shell = STATE["shell"]
    shell.close_container_panel()
    win.set_default_size(760, 900)


def bottom_dark(win, ctx) -> None:
    """Низ страницы: карточки «Сведения» и «Запуск» уходят под стеклянный док."""
    ctx.set_dark(True)
    shell = STATE["shell"]
    adj = shell.scroller.get_vadjustment()
    adj.set_value(adj.get_upper())


def container_narrow_dark(win, ctx) -> None:
    ctx.set_dark(True)
    shell = STATE["shell"]
    shell.scroller.get_vadjustment().set_value(0)
    shell.set_container(api.CONTAINER_BUSY, "перезапускается…")
    shell.set_container_controls(False, "Выключить можно, когда перезапуск закончится", False)
    shell.set_window_size("1920x1080", busy=True)
    shell.open_container_panel()


def welcome_dark(win, ctx) -> None:
    ctx.set_dark(True)
    win.set_default_size(1180, 820)
    shell = STATE["shell"]
    shell.close_container_panel()
    fill_container(shell)
    shell.set_seed(None)
    shell.set_library([], None)
    shell.show_welcome()


def reduced_dark(win, ctx) -> None:
    """«Меньше прозрачности», прокручено: под шапкой и доком — плотные
    поверхности, страница сквозь них не просвечивает."""
    ctx.set_dark(True)
    shell = STATE["shell"]
    show_roblox(shell)
    shell.set_reduced_glass(True)
    shell.scroller.get_vadjustment().set_value(140)


def long_en_dark(win, ctx) -> None:
    """Английский интерфейс с длинными подписями — в новом Shell: GTK
    забирает текст у виджета при создании, язык меняется пересборкой."""
    from merci.ui.lang_en_ui import TABLE
    from merci.ui.shell import Shell

    i18n.set_language("en")
    i18n._table = {**i18n._table, **TABLE}  # демонстрация: так подключит ведущий
    ctx.set_dark(True)
    shell = Shell(DemoHandlers(), STATE["menu"])
    win.set_content(shell)
    STATE["shell"] = shell
    fill_container(shell, en=True)
    shell.set_seed(seed("roblox"))
    shell.set_library(library("roblox"), "roblox")
    shell.show_page(page("roblox", en=True))
    shell.set_notice(Notice("The container's adb is open without a password — it is worth "
                            "closing it with a key before you play online", "Prepare", "warning"))
    shell.set_build("this one — ready to launch", True)
    shell.set_profiles(Profiles(visible=True, labels=("main", "second"), selected=0,
                                hint="shared application data"))
    shell.set_action("Installing into the container…", busy=True)
    shell.set_summary("2 of 4 windows open · build matches")
    shell.set_stage("Launching window 3… (this can take up to three minutes)")
    shell.set_windows([
        tile(1, api.WINDOW_OPEN, "the app is open", ("close", "resize", "restart", "power_off"),
             en=True),
        tile(2, api.WINDOW_BACKGROUND, "the app runs in the background — no window on screen",
             ("open", "close", "resize", "restart", "power_off", "remove"), en=True),
        tile(3, api.WINDOW_ABSENT, "Android is running · the app is not installed here yet",
             ("open", "resize", "remove"), en=True),
        tile(4, api.WINDOW_DEAD, "Android is not responding — restart the window",
             ("restart", "power_off", "remove"), en=True),
    ], WindowsHeader(summary="4 windows · 2 open · each one is its own Android",
                     can_power_off_all=True))


# -- обычные диалоги логики поверх окна ----------------------------------------
#
# Логика показывает стандартные диалоги libadwaita; вид красит их только CSS.
# Здесь они собраны прямо в демонстрации теми же классами, что у логики
# (настоящий мастер installer.py ходит к хосту и здесь не запускается).

def _close_dialog() -> None:
    dialog = STATE.pop("dialog", None)
    if dialog is not None:
        dialog.force_close()


def _present(dialog: Adw.Dialog, win) -> None:
    _close_dialog()
    STATE["dialog"] = dialog
    dialog.present(win)


def _back_to_roblox(ctx, dark: bool) -> None:
    ctx.set_dark(dark)
    shell = STATE["shell"]
    shell.close_container_panel()
    show_roblox(shell)
    fill_container(shell)


def dialog_short_dark(win, ctx) -> None:
    """Снова русский интерфейс (новый Shell) и короткий вопрос поверх окна."""
    from merci.ui.shell import Shell

    i18n.set_language("ru")
    shell = Shell(DemoHandlers(), STATE["menu"])
    win.set_content(shell)
    STATE["shell"] = shell
    _back_to_roblox(ctx, True)
    dialog = Adw.AlertDialog(heading="Удалить окно 3?",
                             body="Его Android и данные приложений в нём будут стёрты.")
    dialog.add_response("cancel", "Отмена")
    dialog.add_response("remove", "Удалить")
    dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_default_response("cancel")
    _present(dialog, win)


def dialog_long_dark(win, ctx) -> None:
    dialog = Adw.AlertDialog(heading="Сменить транслятор ARM?")
    text = Gtk.Label(label=(
        "Нынешняя сборка libhoudini просрочена: ARM-приложения зависают на заставке.\n\n"
        "Merci скачает свежую сборку (около 90\u00a0МБ), перезапустит контейнер и вернёт "
        "ваши настройки. Окна закроются на минуту — несохранённое в играх пропадёт.\n\n"
        "Можно и остаться на прежней: тогда запускайте приложения в сборке x86_64."),
        wrap=True, xalign=0, selectable=True)
    text.set_max_width_chars(56)
    text.add_css_class("merci-dialog-text")
    dialog.set_extra_child(text)
    dialog.set_prefer_wide_layout(True)
    dialog.add_response("cancel", "Не сейчас")
    dialog.add_response("x86", "Удалить ARM-сборки")
    dialog.add_response("switch", "Сменить транслятор")
    dialog.set_response_appearance("x86", Adw.ResponseAppearance.DESTRUCTIVE)
    dialog.set_response_appearance("switch", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("switch")
    _present(dialog, win)
    # Выделяемая подпись, получив фокус первой, выделяет весь текст; как и
    # советуем логике — снимаем выделение после показа (см. «Что важно знать»).
    GLib.idle_add(lambda: text.select_region(0, 0) or False)


def dialog_long_light(win, ctx) -> None:
    ctx.set_dark(False)


def _settings_dialog() -> Adw.PreferencesDialog:
    dialog = Adw.PreferencesDialog(title="Настройки")
    page = Adw.PreferencesPage(title="Общие", icon_name="preferences-system-symbolic")
    look = Adw.PreferencesGroup(title="Вид", description="Как выглядит окно Merci")
    look.add(Adw.SwitchRow(title="Меньше прозрачности",
                           subtitle="плотные поверхности без размытия", active=False))
    lang = Adw.ComboRow(title="Язык", subtitle="интерфейс Merci",
                        model=Gtk.StringList.new(["Как в системе", "Русский", "English"]))
    look.add(lang)
    page.add(look)
    upd = Adw.PreferencesGroup(title="Обновления")
    upd.add(Adw.SwitchRow(title="Проверять обновления автоматически",
                          subtitle="при запуске и раз в сутки; ставятся по вашему нажатию",
                          active=True))
    check = Adw.ActionRow(title="Проверить сейчас", subtitle="проверено 12 мин назад",
                          activatable=True)
    check.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
    upd.add(check)
    page.add(upd)
    adv = Adw.PreferencesGroup(title="Контейнер")
    exp = Adw.ExpanderRow(title="Дополнительно", subtitle="для тех, кто знает, зачем", expanded=True)
    exp.add_row(Adw.EntryRow(title="Размер окон по умолчанию", text="1920x1080"))
    adv.add(exp)
    page.add(adv)
    dialog.add(page)
    return dialog


def dialog_settings_dark(win, ctx) -> None:
    ctx.set_dark(True)
    _present(_settings_dialog(), win)


def dialog_settings_light(win, ctx) -> None:
    ctx.set_dark(False)


def _chip(text: str, *classes: str) -> Gtk.Label:
    chip = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
    chip.add_css_class("merci-chip")
    for cls in classes:
        chip.add_css_class(cls)
    return chip


def _wizard_dialog() -> Adw.Dialog:
    """Как мастер подготовки (installer.py) и окно обновлений: проценты,
    полоса, шаги, журнал, мелкие метки."""
    dialog = Adw.Dialog(title="Подготовка Waydroid", content_width=640, content_height=700)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
    for side in ("top", "bottom", "start", "end"):
        getattr(body, f"set_margin_{side}")(22 if side in ("start", "end") else 12)
    percent = Gtk.Label(label="62 %", xalign=0.5)
    percent.add_css_class("merci-percent")
    body.append(percent)
    phase = Gtk.Label(label="Скачиваем образ Android", xalign=0.5, wrap=True)
    phase.add_css_class("title-2")
    body.append(phase)
    detail = Gtk.Label(label="system.img · 531 из 847\u00a0МБ", xalign=0.5)
    detail.add_css_class("dim-label")
    body.append(detail)
    bar = Gtk.ProgressBar(fraction=0.62, hexpand=True)
    bar.add_css_class("merci-progress")
    body.append(bar)

    steps = Adw.PreferencesGroup(title="Шаги")
    for title, sub, state in (
            ("Пакет Waydroid", "уже установлен", "done"),
            ("Образ Android", "скачает 847\u00a0МБ", "run"),
            ("Транслятор ARM", "libhoudini для ARM-приложений", "wait")):
        row = Adw.ActionRow(title=title, subtitle=sub)
        row.add_css_class("merci-step")
        if state == "run":
            spin = Adw.Spinner()
            spin.set_size_request(18, 18)
            row.add_prefix(spin)
            row.add_suffix(_chip("≈\u00a0847\u00a0МБ"))
        else:
            image = Gtk.Image.new_from_icon_name(
                "object-select-symbolic" if state == "done" else "radio-symbolic")
            image.add_css_class("success" if state == "done" else "dim-label")
            row.add_prefix(image)
            if state == "wait":
                row.set_opacity(0.65)
                row.add_suffix(_chip("нужен пароль", "warn"))
            else:
                row.add_suffix(_chip("готово", "accent"))
        steps.add(row)
    body.append(steps)

    log = Gtk.TextView(editable=False, monospace=True, cursor_visible=False)
    log.add_css_class("merci-log")
    for side in ("left", "right"):
        getattr(log, f"set_{side}_margin")(10)
    log.set_top_margin(6)
    log.set_bottom_margin(6)
    log.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    log.get_buffer().set_text(
        "$ waydroid init -s GAPPS\n[23:41:07] Downloading system.img\n"
        "[23:41:52] 62% 531.4 MiB / 847.0 MiB  11.8 MiB/s\n")
    scroll = Gtk.ScrolledWindow()
    scroll.set_child(log)
    scroll.set_size_request(-1, 110)
    expander = Adw.ExpanderRow(title="Журнал", subtitle="Полный вывод команд на хосте",
                               expanded=True)
    expander.add_row(Adw.ActionRow(child=scroll))
    group = Adw.PreferencesGroup()
    group.add(expander)
    body.append(group)

    cancel = Gtk.Button(label="Отмена")
    cancel.add_css_class("pill")
    cancel.set_halign(Gtk.Align.CENTER)
    body.append(cancel)

    scroller = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
    clamp = Adw.Clamp(maximum_size=560)
    clamp.set_child(body)
    scroller.set_child(clamp)
    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    toolbar.set_content(scroller)
    dialog.set_child(toolbar)
    return dialog


def dialog_wizard_dark(win, ctx) -> None:
    ctx.set_dark(True)
    _present(_wizard_dialog(), win)


def dialog_wizard_light(win, ctx) -> None:
    ctx.set_dark(False)


if __name__ == "__main__":
    import shotlib  # обвязка снимков: .dev/design/shotlib.py (shot.sh кладёт её в путь)

    shotlib.run(build, [
        ("main-dark.png", main_dark),
        ("main-light.png", main_light),
        ("alt-dark.png", alt_dark),
        ("states-dark.png", states_dark),
        ("windows-error-dark.png", windows_error_dark),
        ("container-dark.png", container_dark),
        ("container-light.png", container_light),
        ("narrow-dark.png", narrow_dark),
        ("bottom-dark.png", bottom_dark),
        ("container-narrow-dark.png", container_narrow_dark),
        ("welcome-dark.png", welcome_dark),
        ("reduced-dark.png", reduced_dark),
        ("long-en-dark.png", long_en_dark),
        ("dialog-short-dark.png", dialog_short_dark),
        ("dialog-long-dark.png", dialog_long_dark),
        ("dialog-long-light.png", dialog_long_light),
        ("dialog-settings-dark.png", dialog_settings_dark),
        ("dialog-settings-light.png", dialog_settings_light),
        ("dialog-wizard-dark.png", dialog_wizard_dark),
        ("dialog-wizard-light.png", dialog_wizard_light),
    ], width=1180, height=820)
