"""Мелкие строительные детали вида: подписи, кнопки, значки, пропорция экрана.

Всё здесь — без знания о Merci: тексты приходят готовыми, цвета живут в CSS
(style.py). Подписи создаются только через ``set_label``/``label=`` и никогда
через разметку Pango: в данных бывают «<» и «&» из ответов adb, и разметка
либо сломала бы текст, либо молча выбросила бы кусок строки.
"""

from __future__ import annotations

import math
from typing import Callable, Sequence

import gi

gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango  # noqa: E402

from ..i18n import tr  # noqa: E402


# -- подписи и значки -------------------------------------------------------

def label(text: str = "", *classes: str, xalign: float = 0.0, wrap: bool = False,
          lines: int = 0, ellipsize: Pango.EllipsizeMode | None = None,
          max_chars: int = -1, hexpand: bool = False) -> Gtk.Label:
    """Подпись без разметки.

    ``wrap`` переносит по словам, а слишком длинное слово (имя пакета,
    activity) — по символам: иначе одно длинное слово раздвигает колонку.
    """
    widget = Gtk.Label(label=text, xalign=xalign, hexpand=hexpand)
    widget.set_use_markup(False)
    for cls in classes:
        widget.add_css_class(cls)
    if wrap:
        widget.set_wrap(True)
        widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        # Естественная ширина — строка без переноса (до max_chars): иначе
        # GTK берёт за естественную ширину самое длинное слово, и подпись
        # в свободной коробке ломается на каждом пробеле.
        widget.set_natural_wrap_mode(Gtk.NaturalWrapMode.NONE)
    if lines:
        widget.set_lines(lines)
    if ellipsize is not None:
        widget.set_ellipsize(ellipsize)
    if max_chars > 0:
        widget.set_max_width_chars(max_chars)
    return widget


def icon(name: str, size: int = 16, *classes: str) -> Gtk.Image:
    image = Gtk.Image(icon_name=name, pixel_size=size,
                      accessible_role=Gtk.AccessibleRole.PRESENTATION)
    for cls in classes:
        image.add_css_class(cls)
    return image


def box(vertical: bool = False, spacing: int = 0, *classes: str, **props) -> Gtk.Box:
    orientation = Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL
    widget = Gtk.Box(orientation=orientation, spacing=spacing, **props)
    for cls in classes:
        widget.add_css_class(cls)
    return widget


def clear(container: Gtk.Widget) -> None:
    """Убирает всех детей коробки (Gtk.Box и родственных)."""
    while (child := container.get_first_child()) is not None:
        container.remove(child)


def set_classes(widget: Gtk.Widget, prefix: str, active: str | None) -> None:
    """Оставляет у виджета ровно один класс с данным префиксом.

    Состояния (``st-open``, ``tone-error``…) меняются на лету, и прошлый
    класс должен уйти вместе со своим цветом.
    """
    for cls in widget.get_css_classes():
        if cls.startswith(prefix) and cls != active:
            widget.remove_css_class(cls)
    if active and not widget.has_css_class(active):
        widget.add_css_class(active)


def accessible_label(widget: Gtk.Widget, text: str) -> None:
    widget.update_property([Gtk.AccessibleProperty.LABEL], [text])


# -- кнопки -----------------------------------------------------------------

class ButtonContent(Gtk.Box):
    """Значок и подпись в кнопке; подпись можно менять без пересборки."""

    def __init__(self, text: str, icon_name: str | None, size: int = 18) -> None:
        super().__init__(spacing=8, halign=Gtk.Align.CENTER)
        self.image = icon(icon_name or "", size)
        self.image.set_visible(bool(icon_name))
        self.append(self.image)
        self.text = label(text, xalign=0.5)
        self.append(self.text)

    def set_label(self, text: str) -> None:
        self.text.set_label(text)

    def set_icon(self, name: str | None) -> None:
        self.image.set_from_icon_name(name or "")
        self.image.set_visible(bool(name))


def button(text: str | None = None, icon_name: str | None = None, *classes: str,
           tooltip: str | None = None, on_click: Callable[[], None] | None = None) -> Gtk.Button:
    """Кнопка M3. Кнопке из одного значка обязательно нужна ``tooltip``:
    она же становится доступным именем для читалки экрана."""
    widget = Gtk.Button()
    if text and icon_name:
        widget.set_child(ButtonContent(text, icon_name))
    elif icon_name:
        widget.set_icon_name(icon_name)
    else:
        widget.set_label(text or "")
    for cls in classes:
        widget.add_css_class(cls)
    if tooltip:
        widget.set_tooltip_text(tooltip)
        if not text:
            accessible_label(widget, tooltip)
    if on_click is not None:
        widget.connect("clicked", lambda *_: on_click())
    return widget


def menu_button(icon_name: str, tooltip: str, *classes: str,
                model=None, popover: Gtk.Popover | None = None) -> Gtk.MenuButton:
    widget = Gtk.MenuButton(icon_name=icon_name, tooltip_text=tooltip)
    accessible_label(widget, tooltip)
    if model is not None:
        widget.set_menu_model(model)
    if popover is not None:
        widget.set_popover(popover)
    for cls in classes:
        widget.add_css_class(cls)
    return widget


# -- значки приложений ------------------------------------------------------
#
# Значки читаются асинхронно: каталог данных бывает на медленном диске или в
# сетевом домашнем каталоге, а set_library с десятком записей не должна
# подвешивать окно. Прочитанное запоминается вместе со временем изменения
# файла; каждый set_path заново (тоже асинхронно) сверяет это время, поэтому
# переустановленный APK с новым значком по тому же пути подхватывается сразу.

class _IconEntry:
    __slots__ = ("mtime", "texture", "light_glyph")

    def __init__(self, mtime: int | None, texture: Gdk.Texture | None, light_glyph: bool) -> None:
        self.mtime = mtime
        self.texture = texture
        self.light_glyph = light_glyph


_TEXTURES: dict[str, _IconEntry] = {}
# Кто ждёт значок. Ссылки сильные, а не слабые: значок на «экране» плитки
# никто, кроме родителя-GTK, в Python не держит, и его обёртка умирала
# раньше, чем приходил ответ, — плитка навсегда оставалась с заглушкой.
# Ожидание короткое (одно чтение файла), так что удержание ничему не мешает.
_PENDING: dict[str, list["AppIcon"]] = {}


def _light_glyph(texture: Gdk.Texture) -> bool:
    """Светлый знак на прозрачном фоне (белый «R$», белый лист APK)?

    Таким значкам нужна тёмная тональная подложка, иначе в светлой теме они
    пропадают. Цветным значкам — наоборот, светлая. Смотрим сетку 24×24
    точек: этого хватает, и это быстро даже в Python.
    """
    try:
        downloader = Gdk.TextureDownloader.new(texture)
        downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
        data, stride = downloader.download_bytes()
        buf = data.get_data()
    except (GLib.Error, AttributeError, TypeError):
        return False
    width, height = texture.get_width(), texture.get_height()
    if width <= 0 or height <= 0 or not buf:
        return False
    grid = 24
    alpha_sum = 0
    lum_sum = 0.0
    solid = 0
    for j in range(grid):
        y = min(height - 1, int((j + 0.5) * height / grid))
        for i in range(grid):
            x = min(width - 1, int((i + 0.5) * width / grid))
            o = y * stride + x * 4
            r, g, b, a = buf[o], buf[o + 1], buf[o + 2], buf[o + 3]
            alpha_sum += a
            if a > 160:
                solid += 1
                lum_sum += 0.2126 * r + 0.7152 * g + 0.0722 * b
    coverage = alpha_sum / (255.0 * grid * grid)
    return solid > 0 and coverage < 0.8 and lum_sum / solid / 255.0 > 0.78


def _deliver(path: str, entry: _IconEntry) -> None:
    for widget in _PENDING.pop(path, []):
        widget._on_loaded(path, entry)


def _request_icon(path: str, widget: "AppIcon") -> None:
    waiting = _PENDING.get(path)
    if waiting is not None:
        if widget not in waiting:
            waiting.append(widget)
        return
    _PENDING[path] = [widget]
    gfile = Gio.File.new_for_path(path)

    def on_bytes(source: Gio.File, result: Gio.AsyncResult, mtime: int) -> None:
        try:
            data, _etag = source.load_bytes_finish(result)
            texture = Gdk.Texture.new_from_bytes(data)
        except GLib.Error:
            texture = None
        entry = _IconEntry(mtime, texture, _light_glyph(texture) if texture is not None else False)
        _TEXTURES[path] = entry
        _deliver(path, entry)

    def on_info(source: Gio.File, result: Gio.AsyncResult) -> None:
        try:
            info = source.query_info_finish(result)
        except GLib.Error:
            # Файла нет или он недоступен — заглушка, а не исключение.
            entry = _IconEntry(None, None, False)
            _TEXTURES[path] = entry
            _deliver(path, entry)
            return
        mtime = (info.get_attribute_uint64("time::modified") * 1_000_000
                 + info.get_attribute_uint32("time::modified-usec"))
        cached = _TEXTURES.get(path)
        if cached is not None and cached.mtime == mtime:
            _deliver(path, cached)
            return
        source.load_bytes_async(None, on_bytes, mtime)

    gfile.query_info_async("time::modified,time::modified-usec", Gio.FileQueryInfoFlags.NONE,
                           GLib.PRIORITY_LOW, None, on_info)


class AppIcon(Gtk.Box):
    """Значок приложения на подложке со скруглением.

    Подложка светлая и тональная — значок лежит на ней, как на стекле. Белым
    знакам на прозрачном фоне (Delta, APK без значка) нужна тёмная подложка:
    их узнаём по самой картинке (класс ``light-glyph``).
    """

    def __init__(self, size: int, *classes: str) -> None:
        # Растягиваться значку незачем; явный запрет ещё и не даёт растяжению
        # картинки внутри «всплыть» к родителю и раздвинуть строку списка.
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                         hexpand=False, vexpand=False)
        self.add_css_class("app-icon")
        for cls in classes:
            self.add_css_class(cls)
        self.set_overflow(Gtk.Overflow.HIDDEN)
        self.set_size_request(size, size)
        self._size = size
        self._path: str | None = None
        self._shown: Gdk.Texture | None = None
        self.image = Gtk.Image(pixel_size=size, hexpand=True, vexpand=True,
                               accessible_role=Gtk.AccessibleRole.PRESENTATION)
        self.append(self.image)
        self._show(None)

    def set_path(self, path: str) -> None:
        changed = path != self._path
        self._path = path
        if not path:
            self._show(None)
            return
        cached = _TEXTURES.get(path)
        if changed:
            # Уже прочитанное показываем сразу (без мигания заглушкой), а
            # свежесть файла проверяем в фоне.
            self._show(cached)
        _request_icon(path, self)

    def _on_loaded(self, path: str, entry: _IconEntry) -> None:
        if path == self._path:
            self._show(entry)

    def _show(self, entry: _IconEntry | None) -> None:
        texture = entry.texture if entry is not None else None
        if texture is not None and texture is self._shown:
            return
        self._shown = texture
        if texture is not None:
            self.remove_css_class("placeholder")
            set_flag(self, "light-glyph", entry.light_glyph)
            self.image.set_from_paintable(texture)
            self.image.set_pixel_size(self._size)
        else:
            # Заглушка: знак пакета на тональной подложке, в размер значка.
            self.add_css_class("placeholder")
            self.remove_css_class("light-glyph")
            self.image.set_from_icon_name("application-x-addon-symbolic")
            self.image.set_pixel_size(max(16, self._size // 2))


def set_flag(widget: Gtk.Widget, cls: str, on: bool) -> None:
    if on:
        widget.add_css_class(cls)
    else:
        widget.remove_css_class(cls)


# -- края прокрутки и всплывающая панель ------------------------------------

def _stop(offset: float, alpha: float) -> Gsk.ColorStop:
    stop = Gsk.ColorStop()
    stop.offset = max(0.0, min(1.0, offset))
    color = Gdk.RGBA()
    color.red = color.green = color.blue = 0.0
    color.alpha = alpha
    stop.color = color
    return stop


_RAMP = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


def _smooth(k: float) -> float:
    return k * k * (3 - 2 * k)


class FadeBin(Gtk.Widget):
    """Растворяет верх и низ содержимого в фоне окна.

    Страница прокручивается под стеклянной шапкой и доком. Без затухания
    она обрывалась бы о край окна над шапкой и шумела бы текстом под доком;
    с ним содержимое уходит в атмосферу, а стекло лежит на чистом фоне.
    Маска альфа-канала — один проход отрисовки, без размытия.
    """

    __gtype_name__ = "MerciFadeBin"

    def __init__(self, child: Gtk.Widget, top: tuple[int, int] = (0, 0),
                 bottom: tuple[int, int] = (0, 0)) -> None:
        super().__init__()
        self._child: Gtk.Widget | None = child
        child.set_parent(self)
        # (где начинается видимость, где она полная) — от края, в пикселях.
        self.top = top
        self.bottom = bottom

    def set_edges(self, top: tuple[int, int], bottom: tuple[int, int]) -> None:
        if (top, bottom) != (self.top, self.bottom):
            self.top, self.bottom = top, bottom
            self.queue_draw()

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        if self._child is None:
            return 0, 0, -1, -1
        return self._child.measure(orientation, for_size)

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        if self._child is not None:
            self._child.allocate(width, height, baseline, None)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if self._child is None:
            return
        width, height = self.get_width(), self.get_height()
        (t0, t1), (b0, b1) = self.top, self.bottom
        if height <= t1 + b1 or (t1 == 0 and b1 == 0):
            self.snapshot_child(self._child, snapshot)
            return
        # Спад — по плавной S-кривой, а не по прямой: у края стекла содержимое
        # уже почти невидимо (не просвечивает наполовину), а у границы зоны
        # затухания проявляется без заметного «шва».
        stops = []
        if t1:
            stops.append(_stop(0.0, 0.0))
            stops += [_stop((t0 + (t1 - t0) * k) / height, _smooth(k)) for k in _RAMP]
        else:
            stops.append(_stop(0.0, 1.0))
        if b1:
            stops += [_stop(1 - (b0 + (b1 - b0) * k) / height, _smooth(k)) for k in reversed(_RAMP)]
            stops.append(_stop(1.0, 0.0))
        else:
            stops.append(_stop(1.0, 1.0))
        rect = Graphene.Rect().init(0, 0, width, height)
        snapshot.push_mask(Gsk.MaskMode.ALPHA)
        snapshot.append_linear_gradient(rect, Graphene.Point().init(0, 0),
                                        Graphene.Point().init(0, height), stops)
        snapshot.pop()
        self.snapshot_child(self._child, snapshot)
        snapshot.pop()

    def do_dispose(self) -> None:
        if self._child is not None:
            self._child.unparent()
            self._child = None


class SheetBin(Gtk.Widget):
    """Всплывающая панель, растущая вниз от «язычка».

    Высота — по содержимому, но не больше места до низа окна: в низком окне
    панель листается внутри, а не уходит за край. Сверху рисуется язычок,
    указывающий на остров в шапке, — панель «вырастает» из него. Цвет
    язычка — CSS ``color`` этого виджета (он совпадает с заливкой панели).
    """

    __gtype_name__ = "MerciSheetBin"
    NOTCH = 10

    def __init__(self, child: Gtk.Widget) -> None:
        super().__init__(valign=Gtk.Align.FILL)
        self.add_css_class("sheet-notch")
        self._child: Gtk.Widget | None = child
        child.set_parent(self)

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        if self._child is None:
            return 0, 0, -1, -1
        low, nat, _a, _b = self._child.measure(orientation, for_size)
        if orientation == Gtk.Orientation.VERTICAL:
            return self.NOTCH + low, self.NOTCH + nat, -1, -1
        return low, nat, -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        if self._child is None:
            return
        low, nat, _a, _b = self._child.measure(Gtk.Orientation.VERTICAL, width)
        room = max(low, height - self.NOTCH)
        transform = Gsk.Transform.new().translate(Graphene.Point().init(0, self.NOTCH))
        self._child.allocate(width, min(nat, room), -1, transform)

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        if self._child is None:
            return
        self.snapshot_child(self._child, snapshot)
        width = self.get_width()
        if width < 40:
            return
        # Язычок перекрывает верхнюю кромку панели на пиксель — шов не виден.
        cx, n = width / 2, float(self.NOTCH)
        path = Gsk.PathBuilder.new()
        path.move_to(cx - 15, n + 1.5)
        path.cubic_to(cx - 8, n + 1.5, cx - 5, 1.5, cx, 1.0)
        path.cubic_to(cx + 5, 1.5, cx + 8, n + 1.5, cx + 15, n + 1.5)
        path.close()
        snapshot.append_fill(path.to_path(), Gsk.FillRule.WINDING, self.get_color())

    def do_dispose(self) -> None:
        if self._child is not None:
            self._child.unparent()
            self._child = None


class Collapse(Gtk.Revealer):
    """Ревилер, который в закрытом виде не занимает места вовсе.

    Отступы вокруг содержимого живут внутри ребёнка (margin), а не в
    spacing родительской коробки: тогда закрытая плашка не оставляет
    двойного зазора, а открытие и закрытие идут плавно, без скачка в конце.
    """

    def __init__(self, child: Gtk.Widget, gap_after: int = 0, duration: int = 220) -> None:
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                         transition_duration=duration)
        child.set_margin_bottom(gap_after)
        self.set_child(child)


# -- пропорция экрана -------------------------------------------------------

class AspectBin(Gtk.Widget):
    """Держит у ребёнка пропорцию экрана: высота считается от ширины.

    Плитка окна похожа на экран того размера, что задан для окон в
    контейнере, — поэтому пропорция меняется на лету (``set_ratio``).
    """

    __gtype_name__ = "MerciAspectBin"

    def __init__(self, child: Gtk.Widget, ratio: float = 16 / 9,
                 min_width: int = 132, natural_width: int = 176) -> None:
        super().__init__()
        self._ratio = ratio
        self._min = min_width
        self._natural = natural_width
        self._child: Gtk.Widget | None = child
        child.set_parent(self)
        self.set_overflow(Gtk.Overflow.HIDDEN)

    def set_ratio(self, ratio: float) -> None:
        if abs(ratio - self._ratio) > 1e-3:
            self._ratio = ratio
            self.queue_resize()

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_measure(self, orientation: Gtk.Orientation, for_size: int) -> tuple[int, int, int, int]:
        if self._child is None:
            return 0, 0, -1, -1
        if orientation == Gtk.Orientation.HORIZONTAL:
            child_min = self._child.measure(orientation, -1)[0]
            low = max(child_min, self._min)
            return low, max(low, self._natural), -1, -1
        width = for_size if for_size > 0 else self._natural
        height = max(int(round(width / self._ratio)), self._child.measure(orientation, width)[0])
        return height, height, -1, -1

    def do_size_allocate(self, width: int, height: int, baseline: int) -> None:
        if self._child is not None:
            self._child.allocate(width, height, baseline, None)

    def do_dispose(self) -> None:
        if self._child is not None:
            self._child.unparent()
            self._child = None


# -- волнистый индикатор хода -----------------------------------------------

def _animations_enabled() -> bool:
    settings = Gtk.Settings.get_default()
    return settings is None or settings.get_property("gtk-enable-animations")


class WavyIndicator(Gtk.Widget):
    """Неопределённый ход операции в духе M3 Expressive: бегущая волна.

    Сколько осталось, логика не знает («до трёх минут»), поэтому это не
    полоса заполнения, а знак «работа идёт». Цвет — CSS ``color``. Тикает
    только пока виден, а при выключенных анимациях системы стоит на месте.
    """

    __gtype_name__ = "MerciWavyIndicator"

    def __init__(self) -> None:
        super().__init__(hexpand=True, accessible_role=Gtk.AccessibleRole.PROGRESS_BAR)
        self.add_css_class("wavy")
        self.set_size_request(-1, 12)
        self._phase = 0.0
        self._tick_id = 0
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)

    def _on_map(self, *_args) -> None:
        if not self._tick_id and _animations_enabled():
            self._tick_id = self.add_tick_callback(self._tick)

    def _on_unmap(self, *_args) -> None:
        if self._tick_id:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0

    def _tick(self, _widget, clock) -> bool:
        # Волна лежит на стекле, и каждый её кадр заново считает размытие
        # под ней. Двадцати кадров в секунду глазу хватает, а слабой
        # видеокарте (и программному GL) — втрое меньше работы.
        if clock.get_frame_counter() % 3 == 0:
            self._phase = (clock.get_frame_time() / 1e6 * 5.0) % (2 * math.pi)
            self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        if width < 8:
            return
        color = self.get_color()
        track = color.copy()
        track.alpha = color.alpha * 0.22
        cy = height / 2
        stroke = Gsk.Stroke.new(3.0)
        stroke.set_line_cap(Gsk.LineCap.ROUND)
        # Ровная дорожка под волной — граница «полосы» видна и без движения.
        line = Gsk.PathBuilder.new()
        line.move_to(2.0, cy)
        line.line_to(width - 2.0, cy)
        snapshot.append_stroke(line.to_path(), stroke, track)
        # Волна бежит слева направо и затухает к краям.
        wave = Gsk.PathBuilder.new()
        amp, length = 3.0, 20.0
        x = 2.0
        first = True
        while x <= width - 2.0:
            edge = min(1.0, x / 40.0, (width - x) / 40.0)
            y = cy + amp * edge * math.sin(2 * math.pi * x / length - self._phase)
            if first:
                wave.move_to(x, y)
                first = False
            else:
                wave.line_to(x, y)
            x += 1.5
        snapshot.append_stroke(wave.to_path(), stroke, color)


# -- переключатель-сегменты -------------------------------------------------

class Segmented(Gtk.Box):
    """Ряд взаимоисключающих кнопок (сегменты M3).

    Выбранный сегмент отличается не только заливкой, но и галочкой перед
    подписью. ``set_value`` — программное обновление: обработчик не
    зовётся. Значение не из списка снимает выбор со всех сегментов —
    так любой из них остаётся нажимаемым. Нажатие человека зовёт
    ``on_change(value)``.
    """

    def __init__(self, choices: Sequence[tuple[object, str]],
                 on_change: Callable[[object], None], name: str = "", *classes: str) -> None:
        super().__init__(spacing=2, accessible_role=Gtk.AccessibleRole.GROUP)
        self.add_css_class("segmented")
        for cls in classes:
            self.add_css_class(cls)
        if name:
            accessible_label(self, name)
        self._on_change = on_change
        self._quiet = False
        self._buttons: dict[object, Gtk.ToggleButton] = {}
        self._checks: dict[object, Gtk.Image] = {}
        group: Gtk.ToggleButton | None = None
        for value, text in choices:
            toggle = Gtk.ToggleButton()
            content = box(False, 4, halign=Gtk.Align.CENTER)
            check = icon("object-select-symbolic", 14)
            check.set_visible(False)
            content.append(check)
            content.append(label(text, xalign=0.5))
            toggle.set_child(content)
            accessible_label(toggle, f"{name}: {text}" if name else text)
            if group is not None:
                toggle.set_group(group)
            else:
                group = toggle
            toggle.connect("toggled", self._on_toggled, value)
            self._buttons[value] = toggle
            self._checks[value] = check
            self.append(toggle)

    def _on_toggled(self, toggle: Gtk.ToggleButton, value: object) -> None:
        self._checks[value].set_visible(toggle.get_active())
        if toggle.get_active() and not self._quiet:
            self._on_change(value)

    def set_value(self, value: object) -> None:
        target = self._buttons.get(value)
        if target is not None and target.get_active():
            return
        self._quiet = True
        try:
            if target is not None:
                target.set_active(True)
            else:
                for toggle in self._buttons.values():
                    if toggle.get_active():
                        toggle.set_active(False)
        finally:
            self._quiet = False


def fps_label(value: int) -> str:
    return tr("Без ограничений") if value == 0 else str(value)


def spinner(size: int = 16) -> Adw.Spinner:
    widget = Adw.Spinner()
    widget.set_size_request(size, size)
    return widget
