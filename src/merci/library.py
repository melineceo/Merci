"""Библиотека установленных APK.

Каждый APK живёт в своей папке со своими данными Android — приложения
не видят файлы друг друга, и снести одно можно, не задев остальные.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import threading
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, field

from . import apk

_ABI_FOR_MACHINE = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "arm64-v8a",
    "arm64": "arm64-v8a",
    "i686": "x86",
    "armv7l": "armeabi-v7a",
}

# ABI, которые ещё можно загрузить, если основной не совпал
_ABI_FALLBACK = {
    "x86_64": ["x86"],
    "arm64-v8a": ["armeabi-v7a", "armeabi"],
}


def host_abi() -> str:
    return _ABI_FOR_MACHINE.get(platform.machine(), platform.machine())


def data_root() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(base, "merci")


@dataclass
class Entry:
    """Один APK в библиотеке."""

    slug: str
    name: str
    package: str
    version: str = ""
    activity: str = ""
    abis: list[str] = field(default_factory=list)
    added: float = 0.0
    last_run: float = 0.0
    # Разрешение рендера контейнера, заданное для этого APK.
    width: int = 0
    height: int = 0
    # Профиль Android, в котором запускать. 0 — основной. Профиль даёт
    # приложению отдельные данные (вход, кеш, настройки) — так работает
    # второй аккаунт. Код приложения профили делят, поэтому другой сборке
    # профиль не поможет.
    android_user: int = 0

    @property
    def directory(self) -> str:
        return os.path.join(data_root(), "apps", self.slug)

    @property
    def apk_path(self) -> str:
        return os.path.join(self.directory, "app.apk")

    @property
    def android_data(self) -> str:
        return os.path.join(self.directory, "android")

    @property
    def icon_path(self) -> str:
        return os.path.join(self.directory, "icon.png")

    @property
    def file_hash(self) -> str:
        """Начало sha256 самого файла APK — оно же хвост ключа записи.

        Этим Merci отличает сборки друг от друга: имя пакета у обычного и
        модифицированного клиента одно, а файл разный. Ровно то же число
        можно получить от контейнера — `sha256sum` над установленным
        base.apk, — и сравнить.
        """
        tail = self.slug.rsplit("-", 1)[-1]
        return tail if len(tail) == 12 and all(c in "0123456789abcdef" for c in tail) else ""

    @property
    def profile(self) -> int:
        """Профиль Android, в котором запускается эта запись.

        Отрицательные значения остались от первой схемы, где профиль
        выдавался сам собой второму APK с тем же пакетом. Схема оказалась
        негодной — Android держит имя пакета как одну установку на всё
        устройство, и второй сборке профиль не помогает, — так что теперь
        такое значение читается как «основной».
        """
        return self.android_user if self.android_user > 0 else 0

    def needs_native_bridge(self) -> bool:
        """True, если в APK нет кода под архитектуру машины.

        Такой пакет Waydroid примет только с native bridge (libhoudini или
        libndk_translation), который транслирует ARM64 в x86_64 прямо внутри
        контейнера. APK с кодом под этот процессор идёт в контейнере
        нативно — трансляция не участвует.
        """
        if not self.abis:
            return False
        host = host_abi()
        return not (
            host in self.abis
            or any(a in self.abis for a in _ABI_FALLBACK.get(host, []))
        )

    def size_bytes(self) -> int:
        total = 0
        for root, _dirs, files in os.walk(self.directory):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        return total


# Добавления идут по одному. Файлы, выбранные разом, копируются каждый в
# своём потоке, и две копии одного APK (тот же ключ записи) делили бы один
# временный каталог: вторая стирала бы его из-под первой.
_ADD_LOCK = threading.Lock()


class AlreadyInLibrary(Exception):
    """Этот самый файл APK в библиотеке уже есть. ``entry`` — его запись."""

    def __init__(self, entry: Entry) -> None:
        super().__init__(entry.name)
        self.entry = entry


class Library:
    def __init__(self) -> None:
        self.apps_dir = os.path.join(data_root(), "apps")
        os.makedirs(self.apps_dir, exist_ok=True)

    def load(self) -> list[Entry]:
        entries: list[Entry] = []
        for slug in sorted(os.listdir(self.apps_dir)):
            meta = os.path.join(self.apps_dir, slug, "meta.json")
            try:
                with open(meta, encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, ValueError):
                continue
            if not isinstance(payload, dict):
                continue
            payload["slug"] = slug
            known = {f for f in Entry.__dataclass_fields__}
            try:
                entry = Entry(**{k: v for k, v in payload.items() if k in known})
            except TypeError:
                continue  # в записи нет обязательных полей — она не наша
            # Запись могла быть испорчена или написана другой версией: число
            # строкой, пустое имя. Раньше одна такая запись роняла чтение всей
            # библиотеки — приводим типы, а что не приводится, пропускаем.
            try:
                entry.name = str(entry.name or entry.package or slug)
                entry.package = str(entry.package or "")
                entry.version = str(entry.version or "")
                entry.added = float(entry.added or 0)
                entry.last_run = float(entry.last_run or 0)
                entry.android_user = int(entry.android_user or 0)
                entry.abis = [str(abi) for abi in (entry.abis or [])]
            except (TypeError, ValueError):
                continue
            # Имена из записи уходят в команды adb. Запись могла быть сделана
            # версией Merci, которая манифест ещё не проверяла, поэтому
            # проверяем и при чтении: запись с недопустимым пакетом не
            # показываем вовсе, а недопустимую activity просто забываем.
            if not apk.valid_package(str(entry.package)):
                continue
            activity = apk.full_activity(entry.package, str(entry.activity or ""))
            entry.activity = activity if apk.valid_activity(activity) else ""
            entries.append(entry)
        entries.sort(key=lambda e: (-e.last_run, e.name.lower()))
        return entries

    def package_conflicts(self, package: str, exclude: str = "") -> list[Entry]:
        """Записи с тем же именем пакета — кроме указанной."""
        return [
            e for e in self.load() if e.package == package and e.slug != exclude
        ]

    def add(self, source_path: str) -> Entry:
        """Копирует APK в библиотеку. Исходный файл не трогаем: пользователь
        мог перетащить его из папки, которую мы вообще не должны менять."""
        info = apk.inspect(source_path)
        with _ADD_LOCK:
            return self._add(source_path, info)

    def _add(self, source_path: str, info) -> Entry:
        slug = _make_slug(info.package, source_path)
        directory = os.path.join(self.apps_dir, slug)
        # Ключ записи — пакет и сумма файла: тот же файл даёт тот же ключ.
        # Раньше повторное добавление стирало каталог записи и писало его
        # заново: данное человеком имя возвращалось к имени из манифеста,
        # выбранный профиль Android и время запуска обнулялись, а окно
        # сообщало «добавлено», будто запись новая. Идущему в эту секунду
        # запуску оно ещё и выдёргивало APK из-под установки.
        previous = next((e for e in self.load() if e.slug == slug), None)
        if previous is not None and _same_size(previous.apk_path, source_path):
            raise AlreadyInLibrary(previous)

        entry = Entry(
            slug=slug,
            name=info.label or _name_from_filename(source_path, info.package),
            package=info.package,
            version=info.version_name or info.version_code,
            activity=info.launch_activity,
            abis=info.abis,
            added=time.time(),
        )
        if previous is not None:
            # Запись есть, а файла при ней нет или он оборван: чиним её, не
            # теряя того, что человек в ней настроил.
            entry.name = previous.name
            entry.added = previous.added or entry.added
            entry.last_run = previous.last_run
            entry.android_user = previous.android_user
            entry.width, entry.height = previous.width, previous.height

        if os.path.isdir(directory):
            return self._repair(directory, entry, source_path, info)

        staging = directory + ".incomplete"
        shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(os.path.join(staging, "android"), exist_ok=True)
        try:
            shutil.copyfile(source_path, os.path.join(staging, "app.apk"))
            apk.extract_icon(source_path, info.icon_entry, os.path.join(staging, "icon.png"))
            _write_meta(staging, entry)
            # Переименование — последний шаг, чтобы в библиотеке никогда
            # не появилась наполовину скопированная запись.
            os.replace(staging, directory)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return entry

    @staticmethod
    def _repair(directory: str, entry: Entry, source_path: str, info) -> Entry:
        """Чинит запись на месте: каталог её уже есть.

        Так бывает, когда копия APK оборвана или сведения о записи не
        читаются. Каталог не пересоздаём — подменяем в нём файл приложения,
        значок и сведения, а остальное не трогаем вовсе. Пересоздание
        стирало бы всё, что в каталоге лежит, — а запись с нечитаемыми
        сведениями выглядит для библиотеки новой, и стёрлось бы молча. К тому
        же каталог записи бывает ссылкой на другой диск: заменить ссылку
        каталогом нельзя, и починка падала на середине.
        """
        os.makedirs(os.path.join(directory, "android"), exist_ok=True)
        part = os.path.join(directory, "app.apk.incomplete")
        try:
            shutil.copyfile(source_path, part)
            # Подмена одним движением: идущая установка дочитает прежний
            # файл, а не половину нового.
            os.replace(part, os.path.join(directory, "app.apk"))
            apk.extract_icon(source_path, info.icon_entry, os.path.join(directory, "icon.png"))
            _write_meta(directory, entry)
        except Exception:
            with suppress(OSError):
                os.remove(part)
            raise
        return entry

    def save(self, entry: Entry) -> None:
        _write_meta(entry.directory, entry)

    def remove(self, entry: Entry) -> None:
        target = entry.directory
        # Страховка от os.walk по чужому дереву, если slug вдруг окажется
        # чем-то вроде "../..".
        if os.path.dirname(os.path.realpath(target)) != os.path.realpath(self.apps_dir):
            raise ValueError("подозрительный путь записи, удаление отменено")
        shutil.rmtree(target, ignore_errors=True)

    def reset_data(self, entry: Entry) -> None:
        shutil.rmtree(entry.android_data, ignore_errors=True)
        os.makedirs(entry.android_data, exist_ok=True)


def _same_size(first: str, second: str) -> bool:
    """Одного ли размера два файла. Суммы у них уже равны по построению
    ключа записи; размер отличает целую копию от оборванной."""
    try:
        return os.path.getsize(first) == os.path.getsize(second)
    except OSError:
        return False


def _write_meta(directory: str, entry: Entry) -> None:
    payload = asdict(entry)
    payload.pop("slug", None)
    path = os.path.join(directory, "meta.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _make_slug(package: str, source_path: str) -> str:
    """Пакет плюс хеш файла: две сборки одного пакета не затирают друг друга."""
    digest = hashlib.sha256()
    with open(source_path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", package) or "unknown"
    return f"{safe}-{digest.hexdigest()[:12]}"


def _name_from_filename(source_path: str, package: str) -> str:
    stem = os.path.splitext(os.path.basename(source_path))[0]
    stem = re.sub(r"[._-]+", " ", stem).strip()
    return stem or package
