"""Значок Merci в системном трее.

Трей на Wayland — это не окно, а служба на шине: панель держит
`org.kde.StatusNotifierWatcher`, приложение регистрирует в нём свой объект и
дальше отвечает на вопросы о значке и меню. GTK4 своего API для этого не
имеет (GtkStatusIcon убран, libappindicator остался в GTK3), поэтому обе
стороны разговора — StatusNotifierItem и com.canonical.dbusmenu — сделаны
здесь напрямую через Gio.

Регистрируемся по уникальному имени шины (`:1.42/StatusNotifierItem`), а не
под именем вида `org.kde.StatusNotifierItem-pid-1`: так делают почти все
современные приложения, и из песочницы это не требует права владеть чужим
именем — достаточно разговора с самим watcher.
"""

from __future__ import annotations

from gi.repository import GdkPixbuf, Gio, GLib

# Значок внутри флатпака. Отдаём панели саму картинку, а не имя из темы:
# по имени панель ищет файл у себя и держит найденное в памяти, поэтому
# новый значок она показала бы только после своего перезапуска.
ICON_FILE = "/app/share/icons/hicolor/256x256/apps/xyz.hackerstone.Merci.png"

WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"

_ITEM_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <method name="Activate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <signal name="NewIcon"/>
    <signal name="NewTitle"/>
    <signal name="NewStatus">
      <arg name="status" type="s"/>
    </signal>
  </interface>
</node>
"""

_MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg name="parentId" type="i" direction="in"/>
      <arg name="recursionDepth" type="i" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="revision" type="u" direction="out"/>
      <arg name="layout" type="(ia{sv}av)" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg name="ids" type="ai" direction="in"/>
      <arg name="propertyNames" type="as" direction="in"/>
      <arg name="properties" type="a(ia{sv})" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg name="id" type="i" direction="in"/>
      <arg name="name" type="s" direction="in"/>
      <arg name="value" type="v" direction="out"/>
    </method>
    <method name="Event">
      <arg name="id" type="i" direction="in"/>
      <arg name="eventId" type="s" direction="in"/>
      <arg name="data" type="v" direction="in"/>
      <arg name="timestamp" type="u" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg name="events" type="a(isvu)" direction="in"/>
      <arg name="idErrors" type="ai" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg name="id" type="i" direction="in"/>
      <arg name="needUpdate" type="b" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg name="updatedProps" type="a(ia{sv})"/>
      <arg name="removedProps" type="a(ias)"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg name="revision" type="u"/>
      <arg name="parent" type="i"/>
    </signal>
  </interface>
</node>
"""


class MenuItem:
    """Строка меню трея. ``action`` пустой у разделителя."""

    def __init__(self, item_id: int, label: str, action=None, separator: bool = False):
        self.id = item_id
        self.label = label
        self.action = action
        self.separator = separator
        self.enabled = True

    def properties(self) -> dict[str, GLib.Variant]:
        if self.separator:
            return {"type": GLib.Variant("s", "separator")}
        return {
            "label": GLib.Variant("s", self.label),
            "enabled": GLib.Variant("b", self.enabled),
            "visible": GLib.Variant("b", True),
        }


class TrayIcon:
    """Значок и меню. Все действия уходят в главный поток через idle_add."""

    def __init__(self, app_id: str, title: str, items: list[MenuItem], on_activate):
        self.app_id = app_id
        self.title = title
        self.items = items
        self.on_activate = on_activate
        self.registered = False
        self._connection: Gio.DBusConnection | None = None
        self._ids: list[int] = []
        self._revision = 1

    # -- запуск ------------------------------------------------------------

    def start(self) -> None:
        """Пытается встать в трей. Неудача не должна ломать приложение:
        панель может не поддерживать значки вовсе."""
        try:
            connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return
        self._connection = connection

        item_info = Gio.DBusNodeInfo.new_for_xml(_ITEM_XML).interfaces[0]
        menu_info = Gio.DBusNodeInfo.new_for_xml(_MENU_XML).interfaces[0]
        try:
            self._ids.append(
                connection.register_object(
                    ITEM_PATH, item_info, self._item_call, self._item_get, None
                )
            )
            self._ids.append(
                connection.register_object(
                    MENU_PATH, menu_info, self._menu_call, self._menu_get, None
                )
            )
        except GLib.Error:
            return

        connection.call(
            WATCHER_NAME,
            WATCHER_PATH,
            WATCHER_NAME,
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (ITEM_PATH,)),
            None,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
            self._on_registered,
        )

    def _on_registered(self, connection, result) -> None:
        try:
            connection.call_finish(result)
        except GLib.Error:
            # Панель без поддержки трея — просто живём без значка.
            self.registered = False
            return
        self.registered = True

    def stop(self) -> None:
        if self._connection is None:
            return
        for registration in self._ids:
            self._connection.unregister_object(registration)
        self._ids.clear()
        self.registered = False

    # -- сам значок --------------------------------------------------------

    def _pixmaps(self) -> GLib.Variant:
        """Значок в том виде, в каком его ждёт трей: ARGB, старший байт первым.

        Готовим сразу несколько размеров — панель возьмёт подходящий сама.
        """
        images = []
        for size in (22, 32, 48):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(ICON_FILE, size, size)
            except GLib.Error:
                continue
            if not pixbuf.get_has_alpha():
                pixbuf = pixbuf.add_alpha(False, 0, 0, 0)
            pixels = pixbuf.get_pixels()
            width, height = pixbuf.get_width(), pixbuf.get_height()
            stride, channels = pixbuf.get_rowstride(), pixbuf.get_n_channels()
            argb = bytearray(width * height * 4)
            out = 0
            for y in range(height):
                row = y * stride
                for x in range(width):
                    i = row + x * channels
                    argb[out] = pixels[i + 3]
                    argb[out + 1] = pixels[i]
                    argb[out + 2] = pixels[i + 1]
                    argb[out + 3] = pixels[i + 2]
                    out += 4
            images.append((width, height, bytes(argb)))
        return GLib.Variant("a(iiay)", images)

    def _item_get(self, _conn, _sender, _path, _iface, name):
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", self.app_id),
            "Title": GLib.Variant("s", self.title),
            "Status": GLib.Variant("s", "Active"),
            # Пусто намеренно: с непустым именем панель предпочтёт поиск по
            # теме и снова покажет то, что у неё закешировано.
            "IconName": GLib.Variant("s", ""),
            "IconPixmap": self._pixmaps(),
            "IconThemePath": GLib.Variant("s", ""),
            "Menu": GLib.Variant("o", MENU_PATH),
            # false — левое нажатие идёт в Activate, а не открывает меню.
            "ItemIsMenu": GLib.Variant("b", False),
        }
        return values.get(name)

    def _item_call(self, _conn, _sender, _path, _iface, method, _params, invocation):
        try:
            if method in ("Activate", "SecondaryActivate"):
                GLib.idle_add(self._run, self.on_activate)
            invocation.return_value(None)
        except Exception as exc:  # noqa: BLE001
            invocation.return_error_literal(
                Gio.io_error_quark(), Gio.IOErrorEnum.FAILED, str(exc)
            )

    # -- меню --------------------------------------------------------------

    def _menu_get(self, _conn, _sender, _path, _iface, name):
        values = {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }
        return values.get(name)

    def _layout(self) -> tuple:
        """Дерево меню как обычные значения Python.

        Внутрь конструктора GLib.Variant готовые Variant кладут только в
        места типа ``v``; в остальных нужны обычные кортежи и списки. Дети
        здесь — как раз ``av``, поэтому каждый ребёнок оборачивается, а сам
        корень остаётся кортежем. Перепутать это значит получить исключение
        в обработчике — панель не дождётся ответа и повиснет на таймауте.
        """
        children = [
            GLib.Variant("(ia{sv}av)", (item.id, item.properties(), []))
            for item in self.items
        ]
        root = {"children-display": GLib.Variant("s", "submenu")}
        return (0, root, children)

    def _menu_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        try:
            self._menu_dispatch(method, params, invocation)
        except Exception as exc:  # noqa: BLE001
            # Молчание здесь дороже ошибки: панель ждала бы ответа до
            # таймаута на каждом открытии меню.
            invocation.return_error_literal(
                Gio.io_error_quark(), Gio.IOErrorEnum.FAILED, str(exc)
            )

    def _menu_dispatch(self, method, params, invocation):
        if method == "GetLayout":
            invocation.return_value(
                GLib.Variant("(u(ia{sv}av))", (self._revision, self._layout()))
            )
            return

        if method == "GetGroupProperties":
            ids = params.unpack()[0]
            wanted = [i for i in self.items if not ids or i.id in ids]
            invocation.return_value(
                GLib.Variant(
                    "(a(ia{sv}))", ([(i.id, i.properties()) for i in wanted],)
                )
            )
            return

        if method == "GetProperty":
            item_id, name = params.unpack()[:2]
            for item in self.items:
                if item.id == item_id:
                    value = item.properties().get(name)
                    if value is not None:
                        invocation.return_value(GLib.Variant("(v)", (value,)))
                        return
            invocation.return_value(GLib.Variant("(v)", (GLib.Variant("s", ""),)))
            return

        if method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
            return

        if method == "Event":
            item_id, event = params.unpack()[:2]
            if event == "clicked":
                for item in self.items:
                    if item.id == item_id and item.action is not None:
                        GLib.idle_add(self._run, item.action)
                        break
            invocation.return_value(None)
            return

        if method == "EventGroup":
            for item_id, event, _data, _time in params.unpack()[0]:
                if event != "clicked":
                    continue
                for item in self.items:
                    if item.id == item_id and item.action is not None:
                        GLib.idle_add(self._run, item.action)
                        break
            invocation.return_value(GLib.Variant("(ai)", ([],)))
            return

        invocation.return_value(None)

    @staticmethod
    def _run(action) -> bool:
        """Действие меню в главном потоке. Исключение отсюда убило бы весь
        цикл событий, поэтому ловим и живём дальше."""
        try:
            action()
        except Exception as exc:  # noqa: BLE001 — трей не повод падать
            print(f"меню трея: {exc}", flush=True)
        return GLib.SOURCE_REMOVE
