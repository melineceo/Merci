"""Разбор APK без внешних зависимостей.

Нам нужно ровно четыре вещи: имя пакета, версия, activity для запуска
и список ABI. Всё это лежит в AndroidManifest.xml, а он в APK хранится
в бинарном формате AXML — поэтому здесь свой маленький парсер.
"""

from __future__ import annotations

import re
import struct
import zipfile
from dataclasses import dataclass, field

# Типы чанков AXML
_RES_STRING_POOL = 0x0001
_RES_XML_RESOURCE_MAP = 0x0180
_RES_XML_START_ELEMENT = 0x0102
_RES_XML_END_ELEMENT = 0x0103

_UTF8_FLAG = 1 << 8

# Значения атрибутов
_TYPE_REFERENCE = 0x01
_TYPE_STRING = 0x03
_TYPE_INT_DEC = 0x10
_TYPE_INT_HEX = 0x11
_TYPE_INT_BOOLEAN = 0x12

# Публичные id атрибутов android:*. AAPT2 иногда не кладёт имена в пул
# строк, и тогда единственный способ понять, что это за атрибут, —
# посмотреть его id в resource map.
_ATTR_IDS = {
    0x01010001: "label",
    0x01010002: "icon",
    0x01010003: "name",
    0x0101000E: "enabled",
    0x01010202: "targetActivity",
    0x0101021B: "versionCode",
    0x0101021C: "versionName",
    0x0101020C: "targetSdkVersion",
    0x0101020D: "minSdkVersion",
    0x0101052C: "roundIcon",
}

_LAUNCHER_CATEGORIES = {
    "android.intent.category.LAUNCHER",
    "android.intent.category.LEANBACK_LAUNCHER",
}


# Сколько байт манифеста и значка мы согласны распаковать. Сжатие deflate
# даёт тысячекратный выигрыш на однообразных данных: APK в двадцать
# мегабайт может нести «манифест» на двадцать гигабайт, и чтение его
# целиком клало бы по памяти не только Merci, но и весь сеанс. Настоящий
# манифест — десятки килобайт, значок — сотни.
_MANIFEST_LIMIT = 8 << 20
_ICON_LIMIT = 4 << 20

# Имя пакета, каким его принимает сам Android: части из латинских букв, цифр
# и подчёркиваний, каждая с буквы, через точку и не меньше двух. Это не
# придирка: имя уходит в команды adb, а adb склеивает аргументы в одну
# строку для оболочки на той стороне. Манифест пишет автор APK, и «пакет»
# вида `a.b; rm -rf /sdcard` был бы исполнен как команда.
_PACKAGE = re.compile(r"[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+")
# Имя класса activity: то же плюс `$` у вложенных классов.
_CLASS = re.compile(r"[A-Za-z0-9_.$]+")


def valid_package(name: str) -> bool:
    return bool(_PACKAGE.fullmatch(name or "")) and len(name) <= 255


def valid_activity(name: str) -> bool:
    return bool(_CLASS.fullmatch(name or "")) and len(name) <= 255


def full_activity(package: str, name: str) -> str:
    """Полное имя класса activity — как его записал бы сам Android.

    В манифесте имя бывает сокращённым: «.Main» или просто «Main» значат
    «в пакете приложения». `am start` понимает только первое сокращение.
    """
    if not name:
        return ""
    if name.startswith("."):
        return package + name
    if "." not in name:
        return f"{package}.{name}"
    return name


class ApkError(Exception):
    """APK нечитаем или это вообще не APK."""


def _read_limited(archive: zipfile.ZipFile, name: str, limit: int) -> bytes:
    """Читает файл из архива, но не больше ``limit`` байт.

    Размеру из заголовка верить нельзя — его пишет тот же, кто собрал
    архив, — поэтому читаем сами и останавливаемся на пределе.
    """
    if archive.getinfo(name).file_size > limit:
        raise ApkError(f"{name} неправдоподобно велик — файл повреждён или подделан")
    with archive.open(name) as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ApkError(f"{name} неправдоподобно велик — файл повреждён или подделан")
    return data


class _StringPool:
    def __init__(self, blob: bytes) -> None:
        count, _styles, flags, strings_start, _styles_start = struct.unpack_from(
            "<IIIII", blob, 8
        )
        self._utf8 = bool(flags & _UTF8_FLAG)
        self._blob = blob
        self._strings_start = strings_start
        self._offsets = struct.unpack_from(f"<{count}I", blob, 28)
        self._cache: dict[int, str] = {}

    def __len__(self) -> int:
        return len(self._offsets)

    def get(self, index: int) -> str:
        if index < 0 or index >= len(self._offsets):
            return ""
        if index not in self._cache:
            self._cache[index] = self._decode(self._strings_start + self._offsets[index])
        return self._cache[index]

    def _decode(self, pos: int) -> str:
        blob = self._blob
        if self._utf8:
            # Две длины подряд: в символах и в байтах. Каждая — один байт,
            # либо два со старшим битом-признаком в первом.
            pos = self._skip_utf8_len(pos)
            length, pos = self._read_utf8_len(pos)
            return blob[pos : pos + length].decode("utf-8", "replace")
        length = struct.unpack_from("<H", blob, pos)[0]
        pos += 2
        if length & 0x8000:
            length = ((length & 0x7FFF) << 16) | struct.unpack_from("<H", blob, pos)[0]
            pos += 2
        return blob[pos : pos + length * 2].decode("utf-16-le", "replace")

    def _read_utf8_len(self, pos: int) -> tuple[int, int]:
        value = self._blob[pos]
        pos += 1
        if value & 0x80:
            value = ((value & 0x7F) << 8) | self._blob[pos]
            pos += 1
        return value, pos

    def _skip_utf8_len(self, pos: int) -> int:
        return self._read_utf8_len(pos)[1]


@dataclass
class _Element:
    name: str
    attrs: dict[str, object]


@dataclass
class ApkInfo:
    """То, что удалось вытащить из APK."""

    package: str = ""
    label: str = ""
    version_name: str = ""
    version_code: str = ""
    launch_activity: str = ""
    abis: list[str] = field(default_factory=list)
    icon_entry: str = ""
    min_sdk: str = ""
    target_sdk: str = ""

    @property
    def is_native_only(self) -> bool:
        """APK без нативных библиотек запустится на любой архитектуре."""
        return not self.abis


def _parse_axml(data: bytes) -> list[_Element]:
    """Возвращает плоский список открывающих тегов в порядке появления,
    с прицепленным путём предков — этого достаточно для наших запросов."""
    if len(data) < 8 or struct.unpack_from("<I", data, 0)[0] != 0x00080003:
        raise ApkError("AndroidManifest.xml не похож на бинарный AXML")

    pool: _StringPool | None = None
    resource_ids: list[int] = []
    elements: list[_Element] = []
    stack: list[str] = []

    pos = struct.unpack_from("<H", data, 2)[0]  # headerSize
    end = min(struct.unpack_from("<I", data, 4)[0] or len(data), len(data))

    while pos + 8 <= end:
        chunk_type, _header_size, chunk_size = struct.unpack_from("<HHI", data, pos)
        if chunk_size < 8 or pos + chunk_size > end:
            break
        chunk = data[pos : pos + chunk_size]

        if chunk_type == _RES_STRING_POOL:
            pool = _StringPool(chunk)
        elif chunk_type == _RES_XML_RESOURCE_MAP:
            resource_ids = list(struct.unpack_from(f"<{(chunk_size - 8) // 4}I", chunk, 8))
        elif chunk_type == _RES_XML_START_ELEMENT and pool is not None:
            element = _read_element(chunk, pool, resource_ids)
            stack.append(element.name)
            element.attrs["__path__"] = "/".join(stack)
            elements.append(element)
        elif chunk_type == _RES_XML_END_ELEMENT:
            if stack:
                stack.pop()

        pos += chunk_size

    if pool is None:
        raise ApkError("в AndroidManifest.xml нет пула строк")
    return elements


def _read_element(chunk: bytes, pool: _StringPool, resource_ids: list[int]) -> _Element:
    name = pool.get(struct.unpack_from("<i", chunk, 20)[0])
    attr_start, attr_size, attr_count = struct.unpack_from("<HHH", chunk, 24)
    attrs: dict[str, object] = {}

    for i in range(attr_count):
        # attributeStart отсчитывается от начала attrExt, а он идёт
        # сразу за заголовком узла (8 байт чанка + lineNumber + comment)
        base = 16 + attr_start + i * attr_size
        if base + 20 > len(chunk):
            break
        _ns, name_idx, raw_idx = struct.unpack_from("<iii", chunk, base)
        _size, _res0, data_type, value = struct.unpack_from("<HBBI", chunk, base + 12)

        key = pool.get(name_idx)
        if not key and 0 <= name_idx < len(resource_ids):
            key = _ATTR_IDS.get(resource_ids[name_idx], "")
        if not key:
            continue

        if data_type == _TYPE_STRING:
            attrs[key] = pool.get(raw_idx if raw_idx >= 0 else value)
        elif data_type == _TYPE_INT_BOOLEAN:
            attrs[key] = value != 0
        elif data_type in (_TYPE_INT_DEC, _TYPE_INT_HEX):
            attrs[key] = value
        elif data_type == _TYPE_REFERENCE:
            attrs[key] = f"@0x{value:08x}"
        else:
            attrs[key] = value

    return _Element(name=name, attrs=attrs)


def _find_launch_activity(elements: list[_Element]) -> str:
    """Ищем activity, у которой в intent-filter есть MAIN + LAUNCHER."""
    current_component = ""
    current_enabled = True
    current_target = ""
    in_filter = False
    has_main = False
    has_launcher = False
    fallback = ""
    disabled = ""

    for element in elements:
        path = str(element.attrs.get("__path__", ""))
        name = str(element.attrs.get("name", ""))

        if element.name in ("activity", "activity-alias"):
            current_component = name
            # enabled="false" — обычное дело у псевдонимов: приложение держит
            # несколько значков (обычный, праздничный) и включает один.
            current_enabled = element.attrs.get("enabled", True) is not False
            current_target = (
                str(element.attrs.get("targetActivity", ""))
                if element.name == "activity-alias"
                else ""
            )
            fallback = fallback or name
            in_filter = False
        elif element.name == "intent-filter" and current_component:
            in_filter = True
            has_main = has_launcher = False
        elif element.name == "action" and in_filter:
            has_main = has_main or name == "android.intent.action.MAIN"
        elif element.name == "category" and in_filter:
            has_launcher = has_launcher or name in _LAUNCHER_CATEGORIES
        elif element.name == "application" and "manifest/application" == path:
            continue

        if in_filter and has_main and has_launcher and current_component:
            if current_enabled:
                return current_component
            # Выключенный компонент запустить нельзя: `am start` ответит, что
            # такого класса нет. Запоминаем то, на что он указывает, и ищем
            # дальше включённый.
            disabled = disabled or current_target or current_component
            in_filter = False

    return disabled or fallback


def inspect(apk_path: str) -> ApkInfo:
    """Читает APK и возвращает всё, что нужно для запуска и карточки."""
    try:
        with zipfile.ZipFile(apk_path) as archive:
            names = archive.namelist()
            if "AndroidManifest.xml" not in names:
                raise ApkError("в архиве нет AndroidManifest.xml — это не APK")
            manifest = _read_limited(archive, "AndroidManifest.xml", _MANIFEST_LIMIT)
            icon_entry = _pick_icon(names)
    except zipfile.BadZipFile as exc:
        raise ApkError("файл не открывается как zip-архив") from exc
    except (OSError, KeyError) as exc:
        raise ApkError(f"не удалось прочитать файл: {exc}") from exc

    try:
        elements = _parse_axml(manifest)
    except (struct.error, IndexError, ValueError, OverflowError, MemoryError) as exc:
        # Битый пул строк или смещения за границей файла: без этого
        # добавление APK падало с трассировкой вместо понятного отказа.
        raise ApkError("AndroidManifest.xml повреждён и не читается") from exc
    info = ApkInfo(icon_entry=icon_entry)

    abis: list[str] = []
    for entry in names:
        if entry.startswith("lib/") and entry.endswith(".so"):
            abi = entry.split("/")[1]
            if abi not in abis:
                abis.append(abi)
    info.abis = sorted(abis)

    for element in elements:
        if element.name == "manifest":
            # Только корневой тег: Android читает именно его. Вложенный
            # <manifest> — способ показать Merci одно имя пакета, а
            # установить другое.
            if element.attrs.get("__path__") != "manifest" or info.package:
                continue
            info.package = str(element.attrs.get("package", ""))
            info.version_name = str(element.attrs.get("versionName", ""))
            info.version_code = str(element.attrs.get("versionCode", ""))
        elif element.name == "uses-sdk":
            info.min_sdk = str(element.attrs.get("minSdkVersion", ""))
            info.target_sdk = str(element.attrs.get("targetSdkVersion", ""))
        elif element.name == "application":
            label = str(element.attrs.get("label", ""))
            # label часто ссылка на строковый ресурс — такие нам не нужны
            if label and not label.startswith("@0x"):
                info.label = label

    if not info.package:
        raise ApkError("в манифесте нет имени пакета")
    if not valid_package(info.package):
        raise ApkError("в манифесте недопустимое имя пакета — такой APK не установится")

    activity = full_activity(info.package, _find_launch_activity(elements))
    # Странное имя класса — не повод отказывать: без activity Merci запустит
    # приложение по имени пакета, как это делает сам Waydroid.
    info.launch_activity = activity if valid_activity(activity) else ""
    # Остальное только показывается человеку, но и тут длина ни к чему.
    info.label = info.label[:120]
    info.version_name = info.version_name[:60]
    info.version_code = info.version_code[:30]
    return info


def _pick_icon(names: list[str]) -> str:
    """Иконку берём эвристикой: разрешать ссылки через resources.arsc —
    отдельный парсер ради картинки, оно того не стоит."""
    candidates = [
        n
        for n in names
        if n.startswith("res/")
        and n.endswith(".png")
        and ("mipmap" in n or "drawable" in n)
        and any(k in n.lower() for k in ("ic_launcher", "icon", "app_icon", "logo"))
    ]
    if not candidates:
        candidates = [
            n for n in names if n.startswith("res/mipmap") and n.endswith(".png")
        ]
    if not candidates:
        return ""

    def rank(entry: str) -> int:
        # слои adaptive-иконки по отдельности выглядят плохо: у foreground
        # прозрачный фон, background — просто заливка
        layer_penalty = -10 if any(
            k in entry for k in ("_foreground", "_background", "_monochrome")
        ) else 0
        for dpi, weight in (
            ("xxxhdpi", 5),
            ("xxhdpi", 4),
            ("xhdpi", 3),
            ("hdpi", 2),
            ("mdpi", 1),
        ):
            if dpi in entry:
                return weight + layer_penalty
        return layer_penalty

    return max(candidates, key=rank)


def extract_icon(apk_path: str, entry: str, destination: str) -> bool:
    """Достаёт иконку из APK. Ошибка здесь некритична — карточка
    просто останется с заглушкой."""
    if not entry:
        return False
    try:
        with zipfile.ZipFile(apk_path) as archive:
            payload = _read_limited(archive, entry, _ICON_LIMIT)
        if not payload.startswith(b"\x89PNG"):
            return False
        with open(destination, "wb") as target:
            target.write(payload)
        return True
    except (OSError, KeyError, zipfile.BadZipFile, ApkError):
        return False
