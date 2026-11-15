"""Обновления: что на машине устарело.

Merci стоит на чужих плечах — образ Android, пакет Waydroid (или
waydroid-nvidia), транслятор ARM, — и у каждого своя жизнь: образ выходит
раз в месяц-другой, пакет меняется вместе с драйверами. Раньше об этом
напоминал только встроенный обновлятор Android — стопкой уведомлений внутри
контейнера; а ставить обновление приходилось руками, и после него слетали
настройки.

Здесь — только проверка: что установлено, что доступно и чем это ставится.
Модуль ничего не меняет на машине и не зависит от GTK. Хост опрашивается
через ``run(argv, timeout) -> (код, вывод)`` — её даёт вызывающий
(``waydroid.check_updates``), поэтому всё остальное проверяется без хоста.
Ставят обновления шаги мастера (``waydroid.update_steps``).
"""

from __future__ import annotations

import configparser
import json
import re
import time
from dataclasses import dataclass, field, replace
from typing import Callable

from . import hostinfo
from .i18n import tr

APP_ID = "xyz.hackerstone.Merci"
RELEASES_API = "https://api.github.com/repos/melineceo/Merci/releases/latest"
RELEASES_PAGE = "https://github.com/melineceo/Merci/releases"
AUR_RPC = "https://aur.archlinux.org/rpc/v5/info?arg[]="
WAYDROID_CFG = "/var/lib/waydroid/waydroid.cfg"
# Каталоги, куда образ кладёт пакет дистрибутива: Waydroid такие не обновляет.
PREINSTALLED_IMAGES = ("/etc/waydroid-extra/images", "/usr/share/waydroid-extra/images")
MEGABYTE = 1024 * 1024

# Опрос хоста: (argv, таймаут в секундах) -> (код возврата, вывод).
Run = Callable[[list, int], "tuple[int, str]"]

# Как обновление ставится.
STEP = "step"        # шагом мастера Merci (с журналом и ходом)
FLATPAK = "flatpak"  # сама Merci: flatpak update
MANUAL = "manual"    # человеком: Merci говорит, что выполнить


@dataclass(frozen=True)
class Update:
    """Одно доступное обновление."""

    key: str                  # images | package | bridge | merci
    title: str
    current: str = ""         # что стоит
    available: str = ""       # что доступно
    detail: str = ""          # что произойдёт при установке
    size_mb: int = 0          # сколько качать; 0 — неизвестно или мало
    kind: str = STEP
    root: bool = False        # спросит пароль
    risky: bool = False       # крупное обновление: стоит прочесть пояснение
    command: str = ""         # для MANUAL: команда или адрес
    # Сведения для шага установки — не для показа.
    data: dict = field(default_factory=dict, compare=False, hash=False)


@dataclass(frozen=True)
class Report:
    """Итог проверки."""

    updates: tuple[Update, ...] = ()
    notes: tuple[str, ...] = ()       # что проверить не удалось
    checked_at: float = 0.0
    backup: dict = field(default_factory=dict, compare=False, hash=False)

    def find(self, key: str) -> Update | None:
        return next((update for update in self.updates if update.key == key), None)


# -- разбор ответов ---------------------------------------------------------


def parse_cfg(text: str) -> dict[str, dict[str, str]]:
    """waydroid.cfg как {раздел: {ключ: значение}}; испорченный — пусто."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # имена свойств Android чувствительны к регистру
    try:
        parser.read_string(text)
    except configparser.Error:
        return {}
    return {section: dict(parser[section]) for section in parser.sections()}


def parse_ota(text: str) -> list[dict]:
    """Сборки из канала образов Waydroid (JSON с ключом ``response``)."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    builds = data.get("response") if isinstance(data, dict) else None
    return [
        build
        for build in builds or []
        if isinstance(build, dict) and isinstance(build.get("datetime"), int)
    ]


def newer_build(builds: list[dict], current: int) -> dict | None:
    """Сборка, которую поставит `waydroid upgrade`.

    Он берёт первую в списке канала, что новее установленной, — список
    идёт от новых к старым, так что это самая свежая.
    """
    for build in builds:
        if build["datetime"] > current:
            return build
    return None


def build_date(stamp: int) -> str:
    """Дата сборки образа — числами, одинаково на любом языке."""
    return time.strftime("%d.%m.%Y", time.gmtime(stamp)) if stamp > 0 else tr("неизвестно")


# LineageOS считает версии по-своему; людям понятнее версия Android.
_ANDROID_BY_LINEAGE = {
    "17": "10", "18": "11", "19": "12", "20": "13", "21": "14", "22": "15", "23": "16",
}


def android_name(version: str) -> str:
    """«20.0» → «LineageOS 20 (Android 13)»."""
    major = str(version).split(".")[0]
    if not major.isdigit():
        return ""
    android = _ANDROID_BY_LINEAGE.get(major)
    return f"LineageOS {major} (Android {android})" if android else f"LineageOS {major}"


def _segments(text: str) -> list:
    return [
        (1, int(piece)) if piece.isdigit() else (0, piece)
        for piece in re.findall(r"\d+|[A-Za-z]+", text)
    ]


def _split_version(text: str) -> tuple[int, str, str]:
    epoch, _, rest = text.rpartition(":") if ":" in text else ("", "", text)
    version, _, release = rest.partition("-")
    return (int(epoch) if epoch.isdigit() else 0), version, release


def version_newer(installed: str, candidate: str) -> bool:
    """Новее ли ``candidate``, чем ``installed``.

    Правила — как у менеджеров пакетов: сначала эпоха, потом версия по
    кускам (числа сравниваются как числа и старше букв), потом номер
    сборки пакета. Пустая строка старше не бывает.
    """
    if not candidate or not installed:
        return False
    old, new = _split_version(installed), _split_version(candidate)
    if old[0] != new[0]:
        return new[0] > old[0]
    for index in (1, 2):
        left, right = _segments(old[index]), _segments(new[index])
        if left != right:
            for a, b in zip(left, right):
                if a != b:
                    return b > a
            return len(right) > len(left)
    return False


def big_jump(installed: str, candidate: str) -> bool:
    """Меняется ли старшая часть версии (0.1.x → 0.2.x, 1.x → 2.x)."""
    old = re.findall(r"\d+", _split_version(installed)[1])[:2]
    new = re.findall(r"\d+", _split_version(candidate)[1])[:2]
    return bool(old and new and old != new)


def parse_aur(text: str, name: str) -> str:
    """Версия пакета из ответа AUR; пусто — пакета там нет или ответ не тот."""
    try:
        data = json.loads(text)
    except ValueError:
        return ""
    for result in data.get("results", []) if isinstance(data, dict) else []:
        if isinstance(result, dict) and result.get("Name") == name:
            return str(result.get("Version") or "")
    return ""


def parse_fields(text: str) -> dict[str, str]:
    """Строки «Ключ: значение» (flatpak info, apt-cache policy) как словарь."""
    fields: dict[str, str] = {}
    for line in text.splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip() and name.strip() not in fields:
            fields[name.strip()] = value.strip()
    return fields


_PACKAGE_NAME = re.compile(r"[A-Za-z0-9@._+-]{1,80}")


# -- отдельные проверки -----------------------------------------------------


def images_update(cfg: dict, fetch: Callable[[str], "str | None"]) -> tuple[Update | None, list]:
    """Есть ли образ Android новее установленного.

    ``fetch(адрес)`` отдаёт текст канала или None, если сервер не ответил.
    Смотрим ровно туда же, куда смотрит `waydroid upgrade`: адреса каналов
    и даты установленных сборок записаны в waydroid.cfg.
    """
    section = cfg.get("waydroid", {})
    if not section or section.get("images_path", "") in PREINSTALLED_IMAGES:
        return None, []

    notes: list[str] = []
    found = []
    for part, label in (("system", tr("система")), ("vendor", tr("драйверы"))):
        address = section.get(f"{part}_ota", "")
        if not address.startswith(("https://", "http://")):
            continue  # канал выключен либо образ ведёт сам пакет (waydroid-nvidia)
        try:
            current = int(section.get(f"{part}_datetime", "0"))
        except ValueError:
            current = 0
        text = fetch(address)
        if text is None:
            notes.append(tr("сервер образов Android не ответил ({part})", part=label))
            continue
        builds = parse_ota(text)
        build = newer_build(builds, current)
        if build is not None:
            now = next((b for b in builds if b["datetime"] == current), {})
            found.append((part, label, current, build, str(now.get("version") or "")))
    if not found:
        return None, notes

    size = sum(int(build.get("size") or 0) for _p, _l, _c, build, _v in found)
    versions = {str(build.get("version") or "") for _p, _l, _c, build, _v in found}
    target = android_name(sorted(versions)[-1]) if versions else ""
    # Другая версия Android — это уже не «свежая сборка»: транслятор ARM и
    # ускорение собраны под конкретную версию.
    risky = any(
        was and str(build.get("version") or "") and was.split(".")[0]
        != str(build.get("version")).split(".")[0]
        for _p, _l, _c, build, was in found
    )
    # В строке списка — одна дата, главной части; обе — в пояснении.
    _part, _label, current, build, _was = found[0]
    parts = "; ".join(
        tr("{part}: {old} → {new}", part=label, old=build_date(old),
           new=build_date(fresh["datetime"]))
        for _p, label, old, fresh, _v in found
    )
    detail = parts + ".\n\n" + tr(
        "Waydroid и все окна выключатся на время обновления. Приложения и их "
        "данные остаются. Прежний образ сохраняется: если новый не заработает, "
        "его можно вернуть."
    )
    if risky:
        detail = tr(
            "Это другая версия Android: транслятор ARM и ускорение собраны под "
            "нынешнюю и после обновления могут не заработать."
        ) + " " + detail
    return (
        Update(
            key="images",
            title=tr("Образ Android"),
            current=tr("сборка от {date}", date=build_date(current)),
            available=tr("сборка от {date}", date=build_date(build["datetime"]))
            + (f" · {target}" if target else ""),
            detail=detail,
            size_mb=round(size / MEGABYTE),
            root=True,
            risky=risky,
            data={
                "parts": [part for part, *_rest in found],
                "download_mb": round(size / MEGABYTE),
            },
        ),
        notes,
    )


def _waydroid_package_arch(run: Run) -> tuple[str, str, bool]:
    """(имя пакета, версия, из AUR ли) — кому принадлежит /usr/bin/waydroid."""
    code, out = run(["sh", "-c", "LC_ALL=C pacman -Qqo /usr/bin/waydroid 2>/dev/null"], 20)
    name = out.strip().splitlines()[0].strip() if code == 0 and out.strip() else ""
    if not _PACKAGE_NAME.fullmatch(name):
        return "", "", False
    code, out = run(["sh", "-c", f"LC_ALL=C pacman -Q {name} 2>/dev/null"], 20)
    words = out.split()
    version = words[1] if code == 0 and len(words) >= 2 else ""
    code, out = run(["sh", "-c", "LC_ALL=C pacman -Qqm 2>/dev/null"], 20)
    return name, version, name in out.split()


def nvidia_mode(cfg: dict) -> str:
    """Чем рисует waydroid-nvidia: nvidia-prolapse, glibc-shim, venus —
    или пусто, если это обычный Waydroid.

    Версии до 0.2 знали один способ, Venus, и режим в настройки не
    писали: узнаём их по сокету его сервера.
    """
    section = cfg.get("waydroid", {})
    mode = section.get("nvidia_driver_mode", "")
    if mode == "rust-driver":  # прежнее имя nvidia-prolapse
        mode = "nvidia-prolapse"
    if mode in ("nvidia-prolapse", "glibc-shim", "venus"):
        return mode
    return "venus" if section.get("nvidia_venus_socket") else ""


def package_update(run: Run, host: hostinfo.Host, cfg: dict | None = None) -> tuple[Update | None, list]:
    """Есть ли обновление пакета, которому принадлежит Waydroid."""
    notes: list[str] = []
    if host.family == "arch":
        name, installed, foreign = _waydroid_package_arch(run)
        if not name or not installed:
            return None, notes
        if foreign:
            code, out = run(["curl", "-fsS", "--max-time", "20", AUR_RPC + name], 30)
            latest = parse_aur(out, name) if code == 0 else ""
            if not latest:
                notes.append(tr("AUR не ответил: обновление пакета {name} не проверено",
                                name=name))
                return None, notes
            if not version_newer(installed, latest):
                return None, notes
            nvidia = name.startswith("waydroid-nvidia")
            risky = big_jump(installed, latest)
            detail = (
                tr(
                    "Пакет соберётся из AUR. После него Merci заново настроит "
                    "ускорение (без этого контейнер не стартует), оставит "
                    "нынешний режим рисования и вернёт свои настройки."
                )
                if nvidia
                else tr("Пакет соберётся из AUR, после него Merci пересоберёт "
                        "настройки контейнера и вернёт свои.")
            )
            if risky and nvidia:
                detail = tr(
                    "Крупное обновление: у новой версии другой режим рисования "
                    "по умолчанию."
                ) + " " + detail
            if nvidia and nvidia_mode(cfg or {}) == "venus":
                # Так просит сам waydroid-nvidia: правило udev для буферов
                # видео применяется к сеансу только после нового входа.
                detail += " " + tr(
                    "Если после обновления картинка не появится, выйдите из "
                    "сеанса рабочего стола и войдите снова."
                )
            return (
                Update(
                    key="package",
                    title=tr("Waydroid ({name})", name=name),
                    current=installed,
                    available=latest,
                    detail=detail,
                    root=True,
                    risky=risky,
                    data={"name": name, "source": "aur", "nvidia": nvidia,
                          "installed": installed, "latest": latest},
                ),
                notes,
            )
        # Пакет из репозитория: по одному пакеты в Arch не обновляют —
        # частичное обновление там прямой путь к поломке. Только сообщаем.
        code, out = run(["sh", "-c", "command -v checkupdates >/dev/null && "
                                     "LC_ALL=C checkupdates 2>/dev/null"], 90)
        for line in out.splitlines():
            words = line.split()
            if len(words) >= 4 and words[0] == name and words[2] == "->":
                return (
                    Update(
                        key="package",
                        title=tr("Waydroid ({name})", name=name),
                        current=words[1],
                        available=words[3],
                        detail=tr("Придёт с обычным обновлением системы: по одному "
                                  "пакеты в Arch не обновляют."),
                        kind=MANUAL,
                        root=True,
                        command="sudo pacman -Syu",
                        data={"name": name, "source": "repo"},
                    ),
                    notes,
                )
        return None, notes

    if host.family == "debian":
        code, out = run(["sh", "-c", "LC_ALL=C apt-cache policy waydroid 2>/dev/null"], 30)
        fields = parse_fields(out)
        installed, latest = fields.get("Installed", ""), fields.get("Candidate", "")
        if code != 0 or installed in ("", "(none)") or latest in ("", "(none)"):
            return None, notes
        if installed == latest or not version_newer(installed, latest):
            return None, notes
        return (
            Update(
                key="package",
                title=tr("Waydroid ({name})", name="waydroid"),
                current=installed,
                available=latest,
                detail=tr("После пакета Merci пересоберёт настройки контейнера и вернёт свои."),
                root=True,
                data={"name": "waydroid", "source": "apt"},
            ),
            notes,
        )

    if host.family == "fedora" and host.package_manager == "dnf":
        code, out = run(["sh", "-c", "LC_ALL=C rpm -q --qf '%{VERSION}-%{RELEASE}' waydroid "
                                     "2>/dev/null"], 20)
        installed = out.strip() if code == 0 else ""
        if not installed:
            return None, notes
        code, out = run(["sh", "-c", "LC_ALL=C dnf -q list --upgrades waydroid 2>/dev/null"], 120)
        latest = ""
        for line in out.splitlines():
            words = line.split()
            if len(words) >= 2 and words[0].startswith("waydroid."):
                latest = words[1]
        if not latest:
            return None, notes
        return (
            Update(
                key="package",
                title=tr("Waydroid ({name})", name="waydroid"),
                current=installed,
                available=latest,
                detail=tr("После пакета Merci пересоберёт настройки контейнера и вернёт свои."),
                root=True,
                data={"name": "waydroid", "source": "dnf"},
            ),
            notes,
        )
    return None, notes


def merci_update(run: Run, version: str) -> tuple[Update | None, list]:
    """Есть ли новая Merci.

    Установленная из репозитория обновляется как обычный флатпак — сверяем
    свой коммит с тем, что в репозитории. Установленная файлом или собранная
    на месте обновиться сама не умеет: для неё смотрим страницу выпусков.
    """
    notes: list[str] = []
    code, out = run(["sh", "-c", f"LC_ALL=C flatpak info {APP_ID} 2>/dev/null"], 30)
    fields = parse_fields(out) if code == 0 else {}
    origin = fields.get("Origin", "")
    commit = fields.get("Commit", "")
    scope = "--system" if fields.get("Installation", "user") == "system" else "--user"
    ref = fields.get("Ref", "")
    # Имя попадает в команду оболочки: только то, что выглядит как ссылка.
    if not re.fullmatch(r"app/[A-Za-z0-9_.-]{1,120}/[A-Za-z0-9_]{1,20}/[A-Za-z0-9_.-]{1,60}", ref):
        ref = ""

    remote = ""
    if re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", origin):
        code, out = run(
            ["sh", "-c", f"LC_ALL=C flatpak remotes {scope} --columns=name,url 2>/dev/null"], 30
        )
        for line in out.splitlines():
            words = line.split()
            if len(words) >= 2 and words[0] == origin:
                remote = words[1]
    if remote.startswith(("https://", "http://")) and commit and ref:
        code, out = run(
            ["sh", "-c", f"LC_ALL=C flatpak remote-info {scope} {origin} {ref} 2>/dev/null"], 60
        )
        if code != 0:
            notes.append(tr("репозиторий Merci не ответил"))
            return None, notes
        there = parse_fields(out)
        latest = there.get("Commit", "")
        if not latest or latest == commit:
            return None, notes
        return (
            Update(
                key="merci",
                title="Merci",
                current=version,
                available=there.get("Version", "") or tr("новая сборка"),
                detail=tr("Обновится как обычный флатпак; новая версия заработает "
                          "после перезапуска Merci."),
                kind=FLATPAK,
                root=scope == "--system",
                data={"scope": scope, "ref": ref},
            ),
            notes,
        )

    # Сборка без репозитория: сама не обновится — сверяем номер версии с
    # последним выпуском.
    code, out = run(
        ["curl", "-fsS", "--max-time", "15", "-H", "Accept: application/vnd.github+json",
         RELEASES_API], 25,
    )
    if code != 0:
        return None, notes  # нет сети или выпусков — молчим: это не поломка
    try:
        tag = str(json.loads(out).get("tag_name") or "")
    except (ValueError, AttributeError):
        return None, notes
    latest = tag.lstrip("vV")
    if not version_newer(version, latest):
        return None, notes
    return (
        Update(
            key="merci",
            title="Merci",
            current=version,
            available=latest,
            detail=tr("Эта копия Merci установлена не из репозитория и сама не "
                      "обновляется: возьмите новую на странице выпусков."),
            kind=MANUAL,
            command=RELEASES_PAGE,
        ),
        notes,
    )


def read_backup(run: Run, cfg: dict) -> dict:
    """Резервная копия образов, если она есть: когда сделана и чего."""
    images = cfg.get("waydroid", {}).get("images_path", "/var/lib/waydroid/images")
    if not re.fullmatch(r"/[A-Za-z0-9._/-]{1,200}", images) or ".." in images:
        return {}
    code, out = run(["cat", f"{images}/merci-backup/backup.json"], 15)
    if code != 0:
        return {}
    try:
        data = json.loads(out)
    except ValueError:
        return {}
    if not isinstance(data, dict) or not str(data.get("system_datetime", "")).isdigit():
        return {}
    return data


# -- всё вместе -------------------------------------------------------------


def check(
    run: Run,
    host: hostinfo.Host,
    version: str,
    *,
    bridge: Update | None = None,
) -> Report:
    """Полная проверка. Блокирующая: звать из потока.

    ``bridge`` — готовое обновление транслятора ARM, если оно есть: про
    транслятор знает ядро Merci, а не этот модуль.
    """
    updates: list[Update] = []
    notes: list[str] = []

    code, text = run(["cat", WAYDROID_CFG], 15)
    cfg = parse_cfg(text) if code == 0 else {}

    def fetch(address: str) -> str | None:
        code, out = run(["curl", "-fsSL", "--max-time", "25", address], 35)
        return out if code == 0 and out.strip() else None

    for probe in (
        lambda: package_update(run, host, cfg),
        lambda: images_update(cfg, fetch) if cfg else (None, []),
        lambda: (bridge, []),
        lambda: merci_update(run, version),
    ):
        try:
            update, extra = probe()
        except Exception as failure:  # noqa: BLE001 — одна проверка не должна ронять остальные
            update, extra = None, [tr("проверка не удалась: {error}", error=failure)]
        if update is not None:
            updates.append(update)
        notes.extend(extra)

    # waydroid-nvidia подкладывает в контейнер свои части графики, собранные
    # под образ своего времени: со свежим образом надёжнее работает свежий
    # пакет. Порядок в списке тот же — «Обновить всё» идёт сверху вниз.
    package = next((item for item in updates if item.key == "package"), None)
    if package is not None and package.data.get("nvidia"):
        updates = [
            replace(
                item,
                detail=tr("Сначала стоит обновить waydroid-nvidia: его части "
                          "собраны под свежий образ.") + " " + item.detail,
            )
            if item.key == "images"
            else item
            for item in updates
        ]

    return Report(
        updates=tuple(updates),
        notes=tuple(dict.fromkeys(notes)),
        checked_at=time.time(),
        backup=read_backup(run, cfg) if cfg else {},
    )


def summary(report: Report) -> str:
    """Одной строкой: что обновилось — для подсказки и уведомления."""
    return ", ".join(
        f"{update.title} → {update.available}" if update.available else update.title
        for update in report.updates
    )
