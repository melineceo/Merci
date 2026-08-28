"""Клонирование APK для одновременного запуска многих окон (Multi-Instance).

Каждое окно получает свой уникальный package name (например, com.roblox.client.m1,
com.roblox.client.m2 и т.д.), уникальные authorities для ContentProvider и
уникальные permissions. При этом имена классов (DEX) остаются нетронутыми,
благодаря чему Android запускает приложение как совершенно отдельный независимый
процесс со своим собственным окном Wayland, хранилищем и аккаунтом.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import zipfile
from dataclasses import dataclass
from typing import Callable

from . import hostexec
from .library import Entry, data_root


class ClonerError(Exception):
    """Ошибка клонирования или сборки APK."""


def _ensure_keystore() -> str:
    """Проверяет или создаёт локальный debug-keystore Merci для подписи клонов."""
    ks_dir = os.path.join(data_root(), "keystore")
    os.makedirs(ks_dir, exist_ok=True)
    ks_path = os.path.join(ks_dir, "merci-debug.keystore")
    if os.path.exists(ks_path):
        return ks_path

    cmd = [
        "keytool",
        "-genkey",
        "-v",
        "-keystore",
        ks_path,
        "-storepass",
        "android",
        "-alias",
        "mercidebug",
        "-keypass",
        "android",
        "-keyalg",
        "RSA",
        "-keysize",
        "2048",
        "-validity",
        "10000",
        "-dname",
        "CN=Merci,O=Merci,C=US",
    ]
    res = hostexec.run(cmd, timeout=30)
    if res.returncode != 0 and not os.path.exists(ks_path):
        raise ClonerError(f"Не удалось создать хранилище ключей подписи: {res.stderr or res.stdout}")
    return ks_path


def patch_axml_manifest(manifest_bytes: bytes, orig_package: str, clone_package: str) -> bytes:
    """Патчит бинарный AXML манифеста: обновляет имя пакета, authorities и permissions."""
    if len(manifest_bytes) < 8:
        raise ClonerError("Некорректный размер AndroidManifest.xml")

    root_type, header_size, total_size = struct.unpack_from("<HHI", manifest_bytes, 0)
    if root_type != 0x0003 or header_size != 0x0008:
        raise ClonerError(f"Неизвестный заголовок AXML: {hex(root_type)}")

    pool_pos = header_size
    pool_type, p_header_size, pool_size = struct.unpack_from("<HHI", manifest_bytes, pool_pos)
    if pool_type != 0x0001:
        raise ClonerError("Не найден чанк StringPool в AXML")

    pool_chunk = manifest_bytes[pool_pos : pool_pos + pool_size]
    str_count, style_count, flags, strings_start, styles_start = struct.unpack_from(
        "<IIIII", pool_chunk, 8
    )
    is_utf8 = bool(flags & (1 << 8))

    offsets = list(struct.unpack_from(f"<{str_count}I", pool_chunk, 28))

    strings: list[str] = []
    for off in offsets:
        pos = strings_start + off
        if is_utf8:
            v1 = pool_chunk[pos]
            pos += 1
            if v1 & 0x80:
                pos += 1
            v2 = pool_chunk[pos]
            pos += 1
            if v2 & 0x80:
                blen = ((v2 & 0x7F) << 8) | pool_chunk[pos]
                pos += 1
            else:
                blen = v2
            s = pool_chunk[pos : pos + blen].decode("utf-8", "replace")
        else:
            length = struct.unpack_from("<H", pool_chunk, pos)[0]
            pos += 2
            if length & 0x8000:
                length = ((length & 0x7FFF) << 16) | struct.unpack_from("<H", pool_chunk, pos)[0]
                pos += 2
            s = pool_chunk[pos : pos + length * 2].decode("utf-16-le", "replace")
        strings.append(s)

    # Правила замены строк:
    # 1. Точное совпадение с пакетом -> новый пакет
    # 2. Пакетные authorities, permissions и taskAffinity -> замена префикса
    # 3. Имена классов (содержащие Activity, Service, Receiver, Provider, Application) -> не меняем
    def should_replace(s: str) -> bool:
        if s == orig_package:
            return True
        # Если это имя класса (Activity, Service, Receiver, Provider, Application), не меняем,
        # чтобы Android находил скомпилированные классы в classes.dex
        if any(
            k in s for k in ("Activity", "Service", "Receiver", "Application", "Provider")
        ) and not any(
            k in s
            for k in (
                "fileprovider",
                "firebaseinitprovider",
                "persona.provider",
                "ShellConfigurationProvider",
                "DYNAMIC_RECEIVER",
            )
        ):
            return False
        if s.startswith(orig_package + ".") and any(
            k in s
            for k in (
                "provider",
                "init",
                "androidx-startup",
                "permission",
                "DYNAMIC_RECEIVER",
                "ShellConfigurationProvider",
                "fileprovider",
                "calling",
            )
        ):
            return True
        return False

    new_strings: list[str] = []
    for s in strings:
        if s == orig_package:
            new_strings.append(clone_package)
        elif should_replace(s):
            new_strings.append(s.replace(orig_package, clone_package))
        else:
            new_strings.append(s)

    # Пересобираем StringPool
    new_str_data = bytearray()
    new_offsets: list[int] = []

    for s in new_strings:
        new_offsets.append(len(new_str_data))
        if is_utf8:
            s_bytes = s.encode("utf-8")
            char_len = len(s)
            byte_len = len(s_bytes)
            if char_len > 127:
                new_str_data.extend(bytes([(char_len >> 8) | 0x80, char_len & 0xFF]))
            else:
                new_str_data.append(char_len)
            if byte_len > 127:
                new_str_data.extend(bytes([(byte_len >> 8) | 0x80, byte_len & 0xFF]))
            else:
                new_str_data.append(byte_len)
            new_str_data.extend(s_bytes)
            new_str_data.append(0)
        else:
            s_bytes = s.encode("utf-16-le")
            char_len = len(s)
            if char_len > 0x7FFF:
                new_str_data.extend(
                    struct.pack("<HH", (char_len >> 16) | 0x8000, char_len & 0xFFFF)
                )
            else:
                new_str_data.extend(struct.pack("<H", char_len))
            new_str_data.extend(s_bytes)
            new_str_data.extend(b"\x00\x00")

    pad = (4 - (len(new_str_data) % 4)) % 4
    new_str_data.extend(b"\x00" * pad)

    styles_data = b""
    if style_count > 0 and styles_start > 0:
        styles_data = pool_chunk[styles_start:]

    new_strings_start = 28 + str_count * 4 + style_count * 4
    new_styles_start = new_strings_start + len(new_str_data) if style_count > 0 else 0
    new_pool_size = new_strings_start + len(new_str_data) + len(styles_data)

    pool_pad = (4 - (new_pool_size % 4)) % 4
    new_pool_size += pool_pad

    new_pool_chunk = bytearray()
    new_pool_chunk.extend(struct.pack("<HHI", pool_type, p_header_size, new_pool_size))
    new_pool_chunk.extend(
        struct.pack(
            "<IIIII",
            str_count,
            style_count,
            flags,
            new_strings_start,
            new_styles_start,
        )
    )
    for off in new_offsets:
        new_pool_chunk.extend(struct.pack("<I", off))
    if style_count > 0:
        style_offset_bytes = pool_chunk[
            28 + str_count * 4 : 28 + str_count * 4 + style_count * 4
        ]
        new_pool_chunk.extend(style_offset_bytes)
    new_pool_chunk.extend(new_str_data)
    new_pool_chunk.extend(styles_data)
    new_pool_chunk.extend(b"\x00" * pool_pad)

    rest_of_axml = manifest_bytes[pool_pos + pool_size :]

    new_total_size = header_size + len(new_pool_chunk) + len(rest_of_axml)
    new_header = struct.pack("<HHI", root_type, header_size, new_total_size)

    return bytes(new_header) + bytes(new_pool_chunk) + rest_of_axml


@dataclass
class InstanceInfo:
    """Информация об экземпляре / окне приложения."""

    index: int
    user_id: int
    package: str
    label: str
    apk_path: str = ""
    is_main: bool = False
    is_running: bool = False


def clone_package_name(orig_package: str, instance_index: int) -> str:
    """Генерирует уникальное имя пакета для инстанса."""
    return f"{orig_package}.m{instance_index}"


def is_signature_file(filename: str) -> bool:
    """Проверяет, является ли файл частью старой подписи v1 (JAR-signature).

    Важно: НЕ удаляем файлы в META-INF/services/ (например, MainDispatcherFactory для корутин)
    и файлы .kotlin_module / .version, иначе рантайм Kotlin/Android падает с ошибкой
    Module with the Main dispatcher is missing!
    """
    fn = filename.upper()
    if fn in ("META-INF/MANIFEST.MF", "META-INF/CERT.SF", "META-INF/CERT.RSA"):
        return True
    if fn.startswith("META-INF/") and fn.count("/") == 1:
        if any(fn.endswith(ext) for ext in (".SF", ".RSA", ".DSA", ".EC")):
            return True
    return False


def build_clone_apk(
    source_apk_path: str,
    output_apk_path: str,
    orig_package: str,
    clone_package: str,
    on_stage: Callable[[str], None] | None = None,
) -> str:
    """Создаёт модифицированный APK с новым именем пакета, выравнивает и подписывает."""
    keystore_path = _ensure_keystore()

    if on_stage:
        on_stage("Модифицируем манифест…")

    with zipfile.ZipFile(source_apk_path, "r") as zin:
        if "AndroidManifest.xml" not in zin.namelist():
            raise ClonerError("В APK отсутствует AndroidManifest.xml")
        orig_manifest = zin.read("AndroidManifest.xml")
        patched_manifest = patch_axml_manifest(
            orig_manifest, orig_package, clone_package
        )

        tmp_raw_apk = output_apk_path + ".raw.tmp"
        tmp_aligned_apk = output_apk_path + ".aligned.tmp"
        for p in (tmp_raw_apk, tmp_aligned_apk, output_apk_path):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

        os.makedirs(os.path.dirname(output_apk_path), exist_ok=True)

        with zipfile.ZipFile(tmp_raw_apk, "w", compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if is_signature_file(item.filename):
                    continue
                data = zin.read(item.filename)
                if item.filename == "AndroidManifest.xml":
                    data = patched_manifest
                zout.writestr(item, data)

    if on_stage:
        on_stage("Выравниваем APK (zipalign)…")

    align_res = hostexec.run(
        ["zipalign", "-f", "-p", "4", tmp_raw_apk, tmp_aligned_apk], timeout=120
    )
    if align_res.returncode != 0 or not os.path.exists(tmp_aligned_apk):
        tmp_aligned_apk = tmp_raw_apk

    if on_stage:
        on_stage("Подписываем APK (apksigner)…")

    sign_res = hostexec.run(
        [
            "apksigner",
            "sign",
            "--ks",
            keystore_path,
            "--ks-pass",
            "pass:android",
            "--ks-key-alias",
            "mercidebug",
            "--key-pass",
            "pass:android",
            "--out",
            output_apk_path,
            tmp_aligned_apk,
        ],
        timeout=180,
    )

    for p in (tmp_raw_apk, tmp_aligned_apk):
        if p != output_apk_path and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass

    if sign_res.returncode != 0 or not os.path.exists(output_apk_path):
        raise ClonerError(
            f"Не удалось подписать APK: {sign_res.stderr or sign_res.stdout}"
        )

    return output_apk_path
