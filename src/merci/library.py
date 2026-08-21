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
import time
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
            payload["slug"] = slug
            known = {f for f in Entry.__dataclass_fields__}
            entries.append(Entry(**{k: v for k, v in payload.items() if k in known}))
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

        slug = _make_slug(info.package, source_path)
        directory = os.path.join(self.apps_dir, slug)
        staging = directory + ".incomplete"
        shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(os.path.join(staging, "android"), exist_ok=True)

        try:
            shutil.copyfile(source_path, os.path.join(staging, "app.apk"))
            apk.extract_icon(source_path, info.icon_entry, os.path.join(staging, "icon.png"))

            entry = Entry(
                slug=slug,
                name=info.label or _name_from_filename(source_path, info.package),
                package=info.package,
                version=info.version_name or info.version_code,
                activity=info.launch_activity,
                abis=info.abis,
                added=time.time(),
            )
            _write_meta(staging, entry)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        # Переименование — последний шаг, чтобы в библиотеке никогда
        # не появилась наполовину скопированная запись.
        shutil.rmtree(directory, ignore_errors=True)
        os.replace(staging, directory)
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
