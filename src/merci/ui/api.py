"""Договор между логикой окна и его внешним видом.

Окно Merci разделено надвое. Логика (``window.py``) знает про Waydroid,
контейнеры, операции и их порядок, но не знает, как что нарисовано. Вид
(пакет ``ui``) рисует и сообщает о нажатиях, но ничего не решает и ни к
чему на хосте не обращается. Между ними — только то, что описано здесь:

* данные, которые логика отдаёт виду (неизменяемые записи ниже);
* ``Handlers`` — что вид сообщает логике;
* ``ShellProtocol`` — что логика может попросить у вида.

Всё вызывается только из главного потока GTK. Вид обязан переносить любой
порядок вызовов: ответы контейнеров приходят из потоков когда придётся, и
строка «сборка в контейнере» может обновиться раньше, чем показана страница.
Тексты приходят уже переведёнными — вид их только показывает (как есть,
без разметки Pango: в них бывают «<», «&» из ответов adb).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

# -- состояния окна -------------------------------------------------------
#
# Окно — это контейнер Android. Его состояние вид обязан показывать не одним
# только цветом: значком, формой и текстом тоже.

WINDOW_OPEN = "open"              # приложение открыто, его окно на экране
WINDOW_BACKGROUND = "background"  # приложение работает, но окна на экране нет
WINDOW_IDLE = "idle"              # Android работает, приложение закрыто
WINDOW_ABSENT = "absent"          # Android работает, приложение сюда не поставлено
WINDOW_OFF = "off"                # контейнер выключен
WINDOW_BOOTING = "booting"        # контейнер поднимается, сети ещё нет
WINDOW_UNKNOWN = "unknown"        # Android пока не отвечает (ни «жив», ни «мёртв»)
WINDOW_DEAD = "dead"              # Android не отвечает — нужен перезапуск окна
WINDOW_FOREIGN = "foreign"        # в окне стоит другая сборка того же пакета
WINDOW_BUSY = "busy"              # с окном идёт операция, действия недоступны

WINDOW_STATES = (
    WINDOW_OPEN, WINDOW_BACKGROUND, WINDOW_IDLE, WINDOW_ABSENT, WINDOW_OFF,
    WINDOW_BOOTING, WINDOW_UNKNOWN, WINDOW_DEAD, WINDOW_FOREIGN, WINDOW_BUSY,
)

# -- состояния контейнера (точка на «острове») ----------------------------

CONTAINER_READY = "ready"            # сессия запущена
CONTAINER_ASLEEP = "asleep"          # сессия остановлена — поднимется вместе с приложением
CONTAINER_UNPREPARED = "unprepared"  # Waydroid не установлен или образ не загружен
CONTAINER_UNKNOWN = "unknown"        # состояние неясно (не ответил в срок)
CONTAINER_BUSY = "busy"              # контейнер поднимается, гаснет или перезапускается

CONTAINER_STATES = (
    CONTAINER_READY, CONTAINER_ASLEEP, CONTAINER_UNPREPARED, CONTAINER_UNKNOWN,
    CONTAINER_BUSY,
)

# -- действия -------------------------------------------------------------

# Handlers.window_action(action, number)
WINDOW_ACTIONS = ("open", "close", "restart", "power_off", "remove", "resize")
# Handlers.windows_action(action)
#   retry — «Повторить» под сообщением «окна прочитать не удалось»
SECTION_ACTIONS = ("add", "launch_all", "stop_all", "power_off_all", "retry")
# Handlers.container_action(action, value)
#   toggle_eco      — value: bool
#   set_fps         — value: int (0 — без ограничений, 15, 30, 45, 60)
#   apply_size      — value: str, как ввёл человек («1600x900»)
#   fill_monitor_size, open_android, restart, stop, prepare (мастер
#   подготовки), switch_bridge, apply_freeform, trim_memory — value нет
CONTAINER_ACTIONS = (
    "toggle_eco", "set_fps", "apply_size", "fill_monitor_size", "open_android",
    "restart", "stop", "prepare", "switch_bridge", "apply_freeform", "trim_memory",
)
# Handlers.manage_action(action)
#   rename    — дать записи своё имя (имена берутся из имён файлов и бывают
#               длинными и одинаковыми)
#   uninstall — снять установку в основном окне, запись остаётся
#   remove    — убрать запись из библиотеки
MANAGE_ACTIONS = ("rename", "uninstall", "remove")

FPS_CHOICES = (0, 15, 30, 45, 60)


@dataclass(frozen=True)
class LibraryItem:
    """Строка библиотеки."""

    slug: str            # ключ записи; приходит обратно в select_entry
    name: str
    version: str         # «2.745.1142» или имя пакета, если версии нет
    icon: str            # путь к PNG; пусто — нарисовать заглушку
    badge: str           # «ARM» либо архитектура хоста («x86_64»)
    native: bool         # True — код под этот процессор, трансляция не нужна
    badge_hint: str      # подсказка к метке
    open_windows: int = 0  # в скольких окнах приложение сейчас открыто (0 — не показывать)


@dataclass(frozen=True)
class AppPage:
    """Страница выбранного приложения — то, что не меняется, пока оно выбрано."""

    slug: str
    name: str
    icon: str
    overline: str        # надзаголовок: «ANDROID · ЧЕРЕЗ ТРАНСЛЯЦИЮ ARM»
    version: str         # «версия 2.745.1142»; может быть пустой
    package: str         # «com.roblox.client»
    chips: tuple[str, ...] = ()            # короткие плашки под названием
    # Карточка «Сведения»: (ключ, подпись, значение). Ключ нужен set_fact.
    facts: tuple[tuple[str, str, str], ...] = ()


@dataclass(frozen=True)
class WindowTile:
    """Одно окно приложения."""

    number: int
    title: str           # «Окно 1»
    role: str            # «основное» / «свой Android»
    state: str           # одно из WINDOW_STATES
    text: str            # полный текст состояния, одной фразой
    main: bool = False
    # Что с окном можно сделать сейчас. Кнопку, которой нет в списке, вид не
    # показывает; при state == WINDOW_BUSY все действия недоступны.
    actions: tuple[str, ...] = ()   # подмножество WINDOW_ACTIONS


@dataclass(frozen=True)
class WindowsHeader:
    """Раздел «Окна» целиком: сводка и доступность общих действий."""

    summary: str = ""            # «4 окна · открыто 2»
    can_add: bool = True
    can_launch_all: bool = True
    can_stop_all: bool = True
    can_power_off_all: bool = False   # False — действие скрыть (выключать нечего)
    busy: bool = False                # идёт операция сразу со всеми окнами


@dataclass(frozen=True)
class Notice:
    """Плашка-предупреждение на странице приложения."""

    text: str
    action: str = ""             # подпись кнопки; пусто — без кнопки
    tone: str = "warning"        # warning | error | info


@dataclass(frozen=True)
class Profiles:
    """Выбор профиля Android (только в многопользовательском режиме)."""

    visible: bool = False
    labels: tuple[str, ...] = ()
    selected: int = 0
    enabled: bool = True
    hint: str = ""               # подпись под названием: «общие данные приложения»


class Handlers(Protocol):
    """Что вид сообщает логике. Каждый вызов — нажатие человека."""

    def select_entry(self, slug: str) -> None: ...
    def add_apk(self) -> None: ...
    def play(self) -> None: ...
    def notice_action(self) -> None: ...
    def window_action(self, action: str, number: int) -> None: ...
    def windows_action(self, action: str) -> None: ...
    def container_action(self, action: str, value: object = None) -> None: ...
    def profile_chosen(self, index: int) -> None: ...
    def manage_action(self, action: str) -> None: ...
    def show_updates(self) -> None: ...   # нажата отметка «Обновления» в библиотеке


class ShellProtocol(Protocol):
    """Что логика может попросить у вида.

    Настоящий класс — ``ui.shell.Shell`` (виджет GTK: его кладут содержимым
    в ``Adw.ApplicationWindow``). Конструктор: ``Shell(handlers, menu)``, где
    ``menu`` — ``Gio.MenuModel`` меню приложения (его рисует кнопка-меню).
    """

    # -- общее --
    def set_seed(self, seed: str | None) -> None:
        """Исходный цвет оформления (#rrggbb) — обычно из значка выбранного
        приложения. None — цвет по умолчанию. Светлую и тёмную тему вид
        переключает сам, следя за Adw.StyleManager."""

    def set_reduced_glass(self, reduced: bool) -> None:
        """«Меньше прозрачности»: без размытия, плотные поверхности."""

    def toast(self, text: str, button: str = "", on_button=None, timeout: int = 4) -> None:
        """Короткое сообщение внизу окна; с ``button`` — с кнопкой."""

    def set_updates(self, count: int, text: str = "") -> None:
        """Отметка об обновлениях в библиотеке: спокойная строка-кнопка
        («Обновления: 3») и точка на кнопке меню. ``count == 0`` — убрать.
        ``text`` — подсказка: что именно обновилось. Нажатие —
        ``Handlers.show_updates``."""

    # -- библиотека --
    def set_library(self, items: Sequence[LibraryItem], selected: str | None) -> None:
        """Заменяет список. Выбор строки ``selected`` не должен вызывать
        select_entry — это не нажатие человека."""

    # -- страница приложения --
    def show_welcome(self) -> None:
        """Библиотека пуста: приёмник «Перетащите APK сюда» и «Выбрать файл…»."""

    def show_page(self, page: AppPage) -> None:
        """Показывает страницу приложения. Прежние ответы (сборка, окна,
        плашка) относятся к другому приложению — их вид сбрасывает сам:
        плашку прячет, окна показывает как «проверяем…»."""

    def set_fact(self, key: str, value: str) -> None:
        """Обновляет одно значение в «Сведениях» (например «последний запуск»)."""

    def set_build(self, text: str, ok: bool | None = None) -> None:
        """«Сборка в контейнере»: текст и оттенок (True — эта, False —
        другая/не та, None — не знаем или проверяем)."""

    def set_profiles(self, profiles: Profiles) -> None: ...

    def set_notice(self, notice: Notice | None) -> None:
        """Плашка на странице; None — убрать."""

    def set_can_uninstall(self, enabled: bool) -> None:
        """Доступно ли «Удалить из контейнера»."""

    # -- главное действие --
    def set_action(self, label: str, busy: bool = False, enabled: bool = True) -> None:
        """Кнопка «Запустить»: подпись (во время работы — текст стадии),
        признак «идёт работа» (индикатор вместо значка) и доступность."""

    def set_summary(self, text: str) -> None:
        """Строка рядом с кнопкой: «2 из 4 окон открыто · сборка совпадает»."""

    # -- окна --
    def set_windows(self, tiles: Sequence[WindowTile], header: WindowsHeader) -> None:
        """Заменяет плитки окон. Вызывается каждые несколько секунд, только
        когда что-то изменилось; открытое меню «⋮» плитки по возможности
        не закрывать, если её данные не поменялись."""

    def set_windows_error(self, title: str, text: str) -> None:
        """Список окон прочитать не удалось."""

    def set_stage(self, text: str) -> None:
        """Ход долгой операции с окнами («Запускаем окно 3… (до трёх
        минут)»); пустая строка — убрать полосу."""

    # -- контейнер («остров» и его панель) --
    def set_container(self, kind: str, text: str) -> None:
        """Состояние сессии: одно из CONTAINER_STATES и текст («сессия
        запущена»). CONTAINER_BUSY — идёт работа: на «острове» индикатор
        хода вместо точки. Незнакомое значение — как CONTAINER_UNKNOWN."""

    def set_container_controls(self, can_stop: bool, stop_hint: str, can_restart: bool) -> None:
        """Доступность «Выключить» (с пояснением под кнопкой) и «Перезапустить»."""

    def set_bridge(self, text: str, alert: bool = False, visible: bool = True) -> None:
        """Трансляция ARM: текст; ``alert`` — не настроена или просрочена."""

    def set_renderer(self, text: str) -> None: ...

    def set_window_size(self, text: str, busy: bool = False) -> None:
        """Общий размер окон («1920x1080») в поле ввода; ``busy`` — идёт
        применение, поле недоступно."""

    def set_quick(self, eco: bool, fps: int) -> None:
        """Быстрые переключатели: режим экономии и ограничение FPS (не
        должны вызывать toggle_eco/set_fps — это не нажатие)."""

    def close_container_panel(self) -> None:
        """Закрыть панель контейнера (перед показом диалога, например)."""
