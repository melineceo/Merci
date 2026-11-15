"""Слой переносимости: на какой системе стоит хост и какие команды ему подходят.

Merci писалась и проверялась на одной машине (CachyOS, Hyprland, Wayland,
NVIDIA), и это зашито в десятки мест: pacman, AUR, hyprctl, kdialog. Здесь
всё, что зависит от системы, собрано в одном месте и не зависит от GTK:
только стандартная библиотека, ни одного обращения к GLib и к хосту.

Главное правило: **модуль ничего не запускает сам**. Хост опрашивает
вызывающий — функцией ``run(argv) -> (код, вывод)``, которая обязана идти
через ``flatpak-spawn --host`` (внутри песочницы ``/etc/os-release`` — это
среда GNOME, а не хост, и переменные сессии тоже свои). Всё остальное —
чистые функции от ``Host``, поэтому их можно проверять без хоста.

Принцип для команд под root: если не знаем, как поставить пакет на этой
системе, возвращаем ``None``, а не догадку. Merci исполняет эти команды с
правами root на чужих машинах; «не знаем» лучше выдуманного имени пакета.
Поэтому здесь нет ни одного ``curl | sh``, ни правки профилей AppArmor или
правил SELinux: в таких случаях ``install_hint`` объясняет человеку, что
сделать самому.

Строки для человека — русские исходники, как и во всём приложении;
подсказки с подстановками (имена пакетов, адреса) переводятся прямо здесь
через ``tr()`` — вызывающему собрать такой перевод было бы не из чего.

Что из таблиц проверено по первоисточникам, а что нет — в
``.dev/design/portability-report.md`` (раздел «Что не удалось проверить»).
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from typing import Callable, Collection, Iterable, Mapping

from .i18n import tr

# Опрос хоста: argv -> (код возврата, объединённый вывод).
Run = Callable[[list], "tuple[int, str]"]

# -- что и как ставим ----------------------------------------------------

#: Имена «что поставить», которые понимают packages() и install_script().
WANTS = ("waydroid", "adb", "cage", "weston", "gamescope", "zenity", "curl", "git", "unzip")

_FAMILY_BY_TOKEN = {
    # Arch и производные (у большинства ID_LIKE=arch, но свои ID надёжнее).
    "arch": "arch", "archarm": "arch", "cachyos": "arch", "endeavouros": "arch",
    "manjaro": "arch", "garuda": "arch", "artix": "arch", "arcolinux": "arch",
    "steamos": "arch", "blackarch": "arch",
    # Debian и производные.
    "debian": "debian", "ubuntu": "debian", "linuxmint": "debian", "pop": "debian",
    "raspbian": "debian", "kali": "debian", "neon": "debian", "zorin": "debian",
    "elementary": "debian", "devuan": "debian", "parrot": "debian", "mx": "debian",
    # Fedora и семейство RPM от Red Hat.
    "fedora": "fedora", "rhel": "fedora", "centos": "fedora", "rocky": "fedora",
    "almalinux": "fedora", "ol": "fedora", "nobara": "fedora", "bazzite": "fedora",
    # SUSE.
    "suse": "suse", "opensuse": "suse", "opensuse-leap": "suse",
    "opensuse-tumbleweed": "suse", "opensuse-slowroll": "suse", "sles": "suse",
    "opensuse-microos": "suse", "opensuse-aeon": "suse", "opensuse-kalpa": "suse",
    # Остальные.
    "nixos": "nix", "void": "void", "alpine": "alpine", "postmarketos": "alpine",
}

_PACKAGE_MANAGER = {
    "arch": "pacman", "debian": "apt", "fedora": "dnf", "suse": "zypper",
    "nix": "nix", "void": "xbps", "alpine": "apk",
}

# Образы Fedora и прочих «атомарных» систем: корень только для чтения.
_IMMUTABLE_VARIANTS = {
    "silverblue", "kinoite", "sericea", "onyx", "iot", "coreos", "cosmic-atomic",
    "budgie-atomic", "microos", "aeon", "kalpa",
}
_IMMUTABLE_IDS = {"nixos", "steamos", "opensuse-microos", "opensuse-aeon", "opensuse-kalpa"}

# Пакеты по семействам. Нет записи — значит «не знаем», а не «пакета нет».
# Ссылки на то, откуда взято, — в отчёте. Помечено «?» там, где имя пакета
# проверить не удалось.
_PACKAGES: dict[str, dict[str, list]] = {
    "arch": {
        "waydroid": ["waydroid"],
        "adb": ["android-tools"],
        "cage": ["cage"],
        "weston": ["weston"],          # ? имя из общих сведений, не проверено
        "gamescope": ["gamescope"],
        "zenity": ["zenity"],
        "curl": ["curl"], "git": ["git"], "unzip": ["unzip"],
    },
    "debian": {
        "waydroid": ["waydroid"],      # Debian 14+/Ubuntu 26.10+ — в репозитории; ниже — см. hint
        "adb": ["adb"],                # Debian stable и Ubuntu noble: пакет adb
        "cage": ["cage"],
        "weston": ["weston"],
        # gamescope в Debian stable (trixie) отсутствует — записи нет намеренно.
        "zenity": ["zenity"],
        "curl": ["curl"], "git": ["git"], "unzip": ["unzip"],
    },
    "fedora": {
        "waydroid": ["waydroid"],
        "adb": ["android-tools"],
        "cage": ["cage"],
        "weston": ["weston"],          # ?
        "gamescope": ["gamescope"],
        "zenity": ["zenity"],
        "curl": ["curl"], "git": ["git"], "unzip": ["unzip"],
    },
    "suse": {
        # Официального пакета waydroid у openSUSE нет (только сообщества,
        # home:...) — записи нет намеренно, см. install_hint.
        "adb": ["android-tools"],
        "cage": ["cage"],
        "weston": ["weston"],
        "zenity": ["zenity"],
        "curl": ["curl"], "git": ["git"], "unzip": ["unzip"],
    },
    "void": {
        "waydroid": ["waydroid"],
        "adb": ["android-tools"],
        "cage": ["cage"],
        "weston": ["weston"],          # ?
        "zenity": ["zenity"],
        "curl": ["curl"], "git": ["git"], "unzip": ["unzip"],
    },
    "alpine": {
        "waydroid": ["waydroid"],      # community, проверено на edge
        "adb": ["android-tools"],      # community, проверено на edge
        "cage": ["cage"],
        "weston": ["weston"],          # ?
        "zenity": ["zenity"],
        "curl": ["curl"], "git": ["git"], "unzip": ["unzip"],
    },
    # nix: пакеты в NixOS ставятся декларативно (configuration.nix), см. hint.
}

DOCS_INSTALL = "https://docs.waydro.id/usage/install-on-desktops"
SUSE_GUIDE = "https://github.com/waydroid/waydroid/discussions/1463"

# -- хост ----------------------------------------------------------------


@dataclass(frozen=True)
class Host:
    distro: str = ""                         # ID из os-release
    like: tuple = ()                         # ID_LIKE
    family: str = "unknown"                  # arch|debian|fedora|suse|nix|void|alpine|unknown
    package_manager: str = ""                # pacman|apt|dnf|zypper|rpm-ostree|nix|xbps|apk|""
    session: str = "unknown"                 # wayland|x11|unknown
    desktop: tuple = ()                      # XDG_CURRENT_DESKTOP по частям, нижний регистр
    init: str = "unknown"                    # systemd|openrc|runit|unknown
    immutable: bool = False
    # Дополнительно: нужно для выбора команд и для сообщений.
    name: str = ""                           # PRETTY_NAME
    version: str = ""                        # VERSION_ID
    variant: str = ""                        # VARIANT_ID (silverblue, kinoite…)
    tools: frozenset = frozenset()           # найденные на хосте команды и пометки (см. PROBE_TOOLS)


def parse_os_release(text: str) -> dict:
    """Разбирает os-release: KEY=VALUE, значения в кавычках или без."""
    result: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not re.fullmatch(r"[A-Z0-9_]+", key):
            continue
        value = value.strip()
        try:
            parts = shlex.split(value, posix=True)
            value = " ".join(parts) if parts else ""
        except ValueError:
            value = value.strip("\"'")
        result[key] = value
    return result


def _family(distro: str, like: Iterable) -> str:
    for token in (distro, *like):
        if token in _FAMILY_BY_TOKEN:
            return _FAMILY_BY_TOKEN[token]
    return "unknown"


def _parse_session(env: Mapping) -> str:
    kind = (env.get("XDG_SESSION_TYPE") or "").strip().lower()
    if kind in ("wayland", "x11"):
        return kind
    # tty, mir, unspecified и пустое значение ничего не говорят о графике:
    # смотрим, какие дисплеи есть. WAYLAND_DISPLAY раньше DISPLAY — в
    # сессии Wayland DISPLAY тоже есть (Xwayland).
    if env.get("WAYLAND_DISPLAY"):
        return "wayland"
    if env.get("DISPLAY"):
        return "x11"
    return "unknown"


def _parse_desktop(env: Mapping) -> tuple:
    raw = env.get("XDG_CURRENT_DESKTOP") or env.get("XDG_SESSION_DESKTOP") or ""
    return tuple(part.strip().lower() for part in raw.split(":") if part.strip())


def make_host(
    os_release: str,
    env: Mapping | None = None,
    init: str = "systemd",
    ostree: bool = False,
    tools: Iterable = (),
) -> Host:
    """Собирает Host из текста os-release и уже известных фактов.

    Отдельно от опроса хоста — чтобы тесты и вызывающий код могли строить Host
    без хоста. ``env`` — переменные ГРАФИЧЕСКОЙ сессии хоста.
    """
    env = env or {}
    data = parse_os_release(os_release)
    distro = data.get("ID", "").lower()
    like = tuple(token for token in data.get("ID_LIKE", "").lower().split() if token)
    variant = data.get("VARIANT_ID", "").lower()
    found = frozenset(tools)
    if "gnome" in _parse_desktop(env):
        # Агент polkit в GNOME живёт в самой оболочке: отдельного процесса,
        # который нашёл бы pgrep, у него нет.
        found = found | {"polkit-agent"}

    family = _family(distro, like)
    package_manager = _PACKAGE_MANAGER.get(family, "")
    immutable = (
        ostree
        or distro in _IMMUTABLE_IDS
        or variant in _IMMUTABLE_VARIANTS
        or "transactional-update" in found
    )
    if immutable and family == "fedora" and (ostree or variant in _IMMUTABLE_VARIANTS):
        package_manager = "rpm-ostree"
    return Host(
        distro=distro,
        like=like,
        family=family,
        package_manager=package_manager,
        session=_parse_session(env),
        desktop=_parse_desktop(env),
        init=init,
        immutable=immutable,
        name=data.get("PRETTY_NAME") or data.get("NAME", ""),
        version=data.get("VERSION_ID", ""),
        variant=variant,
        tools=found,
    )


#: Команды, наличие которых полезно знать. Результат опроса попадает в
#: ``Host.tools`` вместе с пометками (``active:…``, ``ostree`` и т.п.).
PROBE_TOOLS = (
    "sudo", "doas", "pkexec", "run0",
    "kdialog", "zenity", "qarma", "yad",
    "cage", "weston", "gamescope",
    "hyprctl", "swaymsg", "kdotool", "xdotool", "wmctrl",
    "adb", "curl", "git", "unzip", "pgrep", "timeout", "runuser",
    "ufw", "firewall-cmd", "nft", "iptables", "ip", "ping",
    "systemctl", "loginctl", "rc-service", "sv",
    "kitty", "alacritty", "foot", "konsole", "gnome-terminal", "xterm",
    "xfce4-terminal", "lxterminal", "x-terminal-emulator",
    "xdg-open", "flatpak-spawn", "lxc-info",
)

# Один сеанс связи с хостом вместо десятка: каждый вызов flatpak-spawn стоит
# десятков миллисекунд. Вывод — разделы, начинающиеся с «@».
PROBE_SCRIPT = (
    'echo "@os-release"\n'
    'if [ -r /etc/os-release ]; then cat /etc/os-release;'
    ' elif [ -r /usr/lib/os-release ]; then cat /usr/lib/os-release; fi\n'
    'echo "@env"\n'
    'for v in XDG_SESSION_TYPE XDG_CURRENT_DESKTOP XDG_SESSION_DESKTOP WAYLAND_DISPLAY DISPLAY; do\n'
    '  eval "x=\\${$v-}"; [ -n "$x" ] && printf "%s=%s\\n" "$v" "$x"\n'
    'done\n'
    # Окружение пользовательского менеджера systemd: именно его видит
    # flatpak-spawn --host, и туда сессия кладёт свои переменные.
    'echo "@user-env"\n'
    'systemctl --user show-environment 2>/dev/null |'
    ' grep -E "^(XDG_SESSION_TYPE|XDG_CURRENT_DESKTOP|XDG_SESSION_DESKTOP|WAYLAND_DISPLAY|DISPLAY)="\n'
    'echo "@init"\n'
    'cat /proc/1/comm 2>/dev/null\n'
    '[ -d /run/systemd/system ] && echo systemd-dir\n'
    '[ -d /run/openrc ] && echo openrc-dir\n'
    '[ -d /run/runit ] && echo runit-dir\n'
    'echo "@flags"\n'
    '[ -e /run/ostree-booted ] && echo ostree\n'
    'command -v transactional-update >/dev/null 2>&1 && echo transactional-update\n'
    '[ "$(getenforce 2>/dev/null)" = Enforcing ] && echo selinux-enforcing\n'
    '[ "$(cat /sys/module/apparmor/parameters/enabled 2>/dev/null)" = Y ] && echo apparmor\n'
    '[ -e /dev/binderfs ] || [ -e /dev/binder ] && echo binder-device\n'
    # Агент polkit: в GNOME и Plasma он живёт в самой оболочке (процесса с таким
    # именем нет), в остальных сессиях — отдельный процесс. Без агента pkexec и
    # run0 молча виснут. Для GNOME и Plasma вызывающему стоит смотреть на desktop.
    # Скобки в первой букве — чтобы pgrep не нашёл самого себя: текст шаблона
    # входит в командную строку оболочки, которая этот сценарий исполняет.
    'pgrep -f "[p]olkit.*agent|[p]olkit-gnome|[h]yprpolkitagent|[l]xpolkit|[x]fce-polkit"'
    ' >/dev/null 2>&1 && echo polkit-agent\n'
    'echo "@tools"\n'
    'for t in ' + " ".join(PROBE_TOOLS) + '; do command -v "$t" >/dev/null 2>&1 && echo "$t"; done\n'
    'echo "@active"\n'
    'for u in firewalld nftables ufw; do systemctl is-active --quiet "$u" 2>/dev/null && echo "$u"; done\n'
    'grep -q "^ENABLED=yes" /etc/ufw/ufw.conf 2>/dev/null && echo ufw\n'
    'true\n'
)


def _sections(text: str) -> dict:
    result: dict = {}
    current = None
    for line in text.splitlines():
        if line.startswith("@") and line[1:] in (
            "os-release", "env", "user-env", "init", "flags", "tools", "active"
        ):
            current = line[1:]
            result[current] = []
        elif current is not None:
            result[current].append(line)
    return result


def _key_values(lines: Iterable) -> dict:
    values = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and key:
            values[key.strip()] = value.strip()
    return values


def _init_from(lines: Iterable) -> str:
    seen = {line.strip() for line in lines if line.strip()}
    if "systemd" in seen or "systemd-dir" in seen:
        return "systemd"
    if seen & {"runit", "runit-init"} or "runit-dir" in seen:
        return "runit"
    if "openrc-init" in seen or "openrc-dir" in seen:
        return "openrc"
    return "unknown"


def host_from_probe(output: str) -> Host:
    """Разбирает вывод PROBE_SCRIPT. Чистая функция — её и проверяют тесты."""
    parts = _sections(output)
    env = _key_values(parts.get("user-env", []))
    # Переменные самого вызова важнее: они ближе к процессу, который спросил.
    env.update(_key_values(parts.get("env", [])))

    flags = {line.strip() for line in parts.get("flags", []) if line.strip()}
    found = {line.strip() for line in parts.get("tools", []) if line.strip()}
    # Включённые службы помечаем «active:», чтобы не путать с командой того же
    # имени (ufw стоит и работает — разные факты).
    active = {"active:" + line.strip() for line in parts.get("active", []) if line.strip()}
    return make_host(
        "\n".join(parts.get("os-release", [])),
        env=env,
        init=_init_from(parts.get("init", [])),
        ostree="ostree" in flags,
        tools=flags | found | active,
    )


def detect(run: Run) -> Host:
    """Определяет хост. ``run`` обязан спрашивать ХОСТ (flatpak-spawn --host)."""
    try:
        code, output = run(["sh", "-c", PROBE_SCRIPT])
    except OSError:
        code, output = 127, ""
    if code == 0 and output.strip():
        return host_from_probe(output)
    # Хост не ответил на сложный вопрос — спросим хотя бы про систему.
    try:
        code, output = run(["cat", "/etc/os-release"])
    except OSError:
        code, output = 127, ""
    return make_host(output if code == 0 else "", env={}, init="unknown")


def has(host: Host, tool: str) -> bool:
    return tool in host.tools


# -- пакеты ---------------------------------------------------------------


def packages(host: Host, what: str) -> "list | None":
    """Имена пакетов для ``what`` или None, если на этой системе не знаем."""
    return list(_PACKAGES.get(host.family, {}).get(what, [])) or None


# Замок pacman: ждём, пока освободится, и снимаем только осиротевший (владельца
# у него нет). Перенесено из Merci без изменений по сути.
_PACMAN_WRAPPER = (
    'for i in $(seq 1 60); do '
    'pgrep -x "pacman|pamac|pamac-daemon|yay|paru" >/dev/null 2>&1 || break; '
    '[ "$i" = 1 ] && echo "ждём другой менеджер пакетов"; sleep 3; done; '
    'if [ -e /var/lib/pacman/db.lck ] && ! pgrep -x pacman >/dev/null; then '
    'echo "снимаю осиротевший замок /var/lib/pacman/db.lck"; rm -f /var/lib/pacman/db.lck; fi; '
    "pacman -S --needed --noconfirm {pkgs}; "
    'code=$?; echo "PACMAN_EXIT:$code"; exit $code'
)

# Для Debian: если у apt нет кандидата waydroid, значит репозиторий проекта не
# подключён (релизы старше Debian 14 / Ubuntu 26.10). Сами его не подключаем:
# официальная инструкция — `curl | sudo bash`, а такое под root без человека
# исполнять нельзя. Сообщаем и выходим кодом 3.
_APT_NEEDS_REPO = (
    'if ! apt-cache policy waydroid 2>/dev/null | grep -q "Candidate: [^(]"; then '
    'echo "в apt нет пакета waydroid: подключите репозиторий проекта '
    '(' + DOCS_INSTALL + ') и повторите"; exit 3; fi; '
)


def install_script(host: Host, what: str) -> "str | None":
    """Команда оболочки для установки; выполняется под root. None — «не знаем».

    Неизменяемые системы (rpm-ostree, NixOS, SteamOS, MicroOS) здесь всегда
    None: слой образа меняют с перезагрузкой, и решает это человек —
    ``install_hint`` говорит, что именно выполнить.
    """
    if host.immutable:
        return None
    names = packages(host, what)
    if names is None:
        return None
    pkgs = " ".join(shlex.quote(name) for name in names)
    family = host.family
    if family == "arch":
        return _PACMAN_WRAPPER.format(pkgs=pkgs)
    if family == "debian":
        guard = _APT_NEEDS_REPO if what == "waydroid" else ""
        return (
            "export DEBIAN_FRONTEND=noninteractive; "
            "apt-get update; "  # без set -e: устаревший индекс не повод отказаться
            + guard
            + f"apt-get install -y -o DPkg::Lock::Timeout=300 {pkgs}"
        )
    if family == "fedora":
        return f"dnf install -y {pkgs}"
    if family == "suse":
        return f"zypper --non-interactive install {pkgs}"
    if family == "void":
        return f"xbps-install -Sy {pkgs}"
    if family == "alpine":
        return f"apk add {pkgs}"
    return None


def needs_reboot(host: Host, what: str = "waydroid") -> bool:
    """Нужна ли перезагрузка после установки (слой rpm-ostree применяется после неё)."""
    return host.package_manager == "rpm-ostree"


def install_hint(host: Host, what: str) -> str:
    """Что сказать человеку, когда поставить сами не можем (или не вправе)."""
    names = packages(host, what)
    pkgs = " ".join(names) if names else what

    if host.family == "nix":
        if what == "waydroid":
            return tr(
                "NixOS настраивается декларативно. Добавьте в configuration.nix "
                "«virtualisation.waydroid.enable = true;» (на новых ядрах может понадобиться "
                "«virtualisation.waydroid.package = pkgs.waydroid-nftables;»), выполните "
                "«sudo nixos-rebuild switch» и затем «sudo waydroid init». "
                "Waydroid на NixOS работает только в сессии Wayland."
            )
        if what == "adb":
            return tr(
                "Добавьте «pkgs.android-tools» в environment.systemPackages "
                "и выполните nixos-rebuild switch."
            )
        return tr(
            "Добавьте пакет «{what}» в environment.systemPackages и выполните "
            "nixos-rebuild switch.",
            what=what,
        )

    if host.package_manager == "rpm-ostree":
        return tr(
            "На этой системе пакеты добавляются слоем образа: выполните "
            "«rpm-ostree install {pkgs}» и перезагрузите компьютер, затем запустите "
            "Merci снова.",
            pkgs=pkgs,
        )

    if host.immutable:
        return tr(
            "Корень этой системы неизменяем, поэтому Merci не ставит пакеты сама. "
            "Добавьте «{pkgs}» способом, который предусмотрен вашей системой (слой "
            "образа, transactional-update и т.п.), перезагрузитесь и запустите Merci снова.",
            pkgs=pkgs,
        )

    if what == "waydroid":
        if host.family == "suse":
            return tr(
                "В основных репозиториях openSUSE пакета waydroid нет; есть только "
                "пакеты сообщества, и их состояние меняется. Инструкция сообщества: "
                "{url}. Затем выполните «sudo waydroid init».",
                url=SUSE_GUIDE,
            )
        if host.family == "debian":
            return tr(
                "Waydroid входит в репозитории Debian 14+ и Ubuntu 26.10+ (в Debian 13 — "
                "в backports): «sudo apt install waydroid». На более старых релизах "
                "сначала подключите репозиторий проекта по инструкции {url} — Merci не "
                "исполняет чужие сценарии под root.",
                url=DOCS_INSTALL,
            )
        if host.family == "unknown":
            return tr("Поставьте waydroid средствами вашего дистрибутива: {url}",
                      url=DOCS_INSTALL)

    if names is None:
        if host.family == "unknown":
            return tr("Поставьте «{what}» средствами вашего дистрибутива.", what=what)
        return tr(
            "Для этой системы Merci не знает имя пакета «{what}»: поставьте его вручную.",
            what=what,
        )

    return tr(
        "Поставьте пакет «{pkgs}» через {manager}.",
        pkgs=pkgs, manager=host.package_manager or tr("менеджер пакетов"),
    )


def binder_hint(host: Host) -> str:
    """Что сказать, когда в ядре нет binder. Сами модули ядра не ставим."""
    if host.family == "arch":
        return tr(
            "Ядру нужен binder: в ядрах linux и linux-zen он есть; для других поставьте "
            "binder_linux-dkms из AUR и модуль binder_linux с "
            "devices=binder,hwbinder,vndbinder."
        )
    if host.family == "debian":
        return tr(
            "Ядру нужен binder (CONFIG_ANDROID_BINDER_IPC и CONFIG_ANDROID_BINDERFS). "
            "Проверьте «grep BINDER /boot/config-$(uname -r)»; если binder собран модулем, "
            "он может лежать в linux-modules-extra для вашего ядра. Иначе — ядро с "
            "поддержкой binder."
        )
    if host.immutable or host.family == "nix":
        return tr(
            "Ядру нужен binder; на этой системе ядро меняется вместе с образом или "
            "конфигурацией."
        )
    return tr(
        "Ядру нужен binder (CONFIG_ANDROID_BINDER_IPC, CONFIG_ANDROID_BINDERFS). "
        "Проверьте «grep BINDER /boot/config-$(uname -r)» или «zgrep BINDER "
        "/proc/config.gz»; если их нет — выберите ядро с поддержкой binder."
    )


def security_notes(host: Host) -> list:
    """Подсказки про SELinux и AppArmor. Сами профили Merci не меняет."""
    notes = []
    if "selinux-enforcing" in host.tools:
        notes.append(tr(
            "SELinux в режиме enforcing. Если контейнер не стартует, посмотрите отказы "
            "(«sudo ausearch -m avc -ts recent»): известны отказы на метках binderfs. "
            "Режим permissive — только для диагностики, не как решение."
        ))
    if "apparmor" in host.tools:
        notes.append(tr(
            "AppArmor включён. Если контейнер не стартует с ошибкой dnsmasq и pid-файлом, "
            "добавьте в /etc/apparmor.d/local/usr.sbin.dnsmasq строки "
            "«@{run}/waydroid-lxc/ r,» и «@{run}/waydroid-lxc/* rw,»."
        ))
    return notes


# -- вложенный композитор (сессия X11) ------------------------------------


def needs_nested_compositor(host: Host) -> bool:
    """Waydroid требует композитор Wayland. В сессии X11 нужен вложенный."""
    return host.session == "x11"


def nested_compositor(host: Host, available: Collection = ()) -> "list | None":
    """Префикс команды, поднимающей окно-композитор Wayland; None — не нужен или нечем.

    Сессия Wayland — None (нужды нет). X11 без cage и weston — тоже None;
    тогда ``packages(host, "cage")`` скажет, что поставить.

    cage — однооконный «киоск»: команда после ``--`` становится его единственным
    приложением, WAYLAND_DISPLAY для неё задаёт сам cage, а выходит cage вместе с
    ней. Это проверенный способ: ``cage -- waydroid show-full-ui``. weston — полноценный
    композитор с несколькими окнами, но способ его запуска ниже НЕ проверен на живой
    системе.
    """
    if host.session != "x11":
        return None
    if "cage" in available:
        return ["cage", "--"]
    if "weston" in available:
        # Свой сокет, чтобы не спутать с сокетом основной сессии; ждём его появления.
        return [
            "sh", "-c",
            'S="merci-wl-$$"; weston --socket="$S" >/dev/null 2>&1 & W=$!; '
            'for i in 1 2 3 4 5 6 7 8 9 10; do '
            '[ -S "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/$S" ] && break; sleep 0.5; done; '
            'WAYLAND_DISPLAY="$S" "$@"; code=$?; kill "$W" 2>/dev/null; exit $code',
            "merci-nested",
        ]
    return None


def session_start_script(host: Host, available: Collection = (), words: str = "session start") -> "str | None":
    """Фрагмент оболочки: поднять сессию Waydroid отдельным процессом.

    Слова ``session start`` разнесены по переменной: так текст шага не совпадает с
    шаблоном pkill по командной строке сессии (причина — в waydroid.py). На X11 сессия
    живёт внутри вложенного композитора; None — поднять негде.
    """
    nested = nested_compositor(host, available)
    if host.session == "x11" and nested is None:
        return None
    prefix = " ".join(shlex.quote(part) for part in nested) + " " if nested else ""
    return f'S="{words}"; (setsid {prefix}waydroid $S >/dev/null 2>&1 &)'


# -- размер окон на хосте ---------------------------------------------------


def window_query(host: Host, available: "Collection | None" = None) -> "str | None":
    """Чем узнать размер окна приложения: hyprland | sway | kwin | None.

    ``available`` — найденные на хосте команды; None — не проверяем наличие.
    GNOME не даёт посторонним программам список окон (org.gnome.Shell.Introspect
    отвечает отказом всем, кроме порталов), поэтому для него — None, и размер
    придётся вводить руками. Сессия X11 тоже None: приложения живут внутри
    вложенного композитора, и размер видит только он.
    """
    if host.session == "x11":
        return None

    def present(tool: str) -> bool:
        return available is None or tool in available

    if "hyprland" in host.desktop and present("hyprctl"):
        return "hyprland"
    if "sway" in host.desktop and present("swaymsg"):
        return "sway"
    if "kde" in host.desktop and available is not None and "kdotool" in available:
        # Команды kdotool для поиска по классу окна не проверены — см. отчёт.
        return "kwin"
    return None


def window_argv(kind: "str | None") -> "list | None":
    """Команда, печатающая окна в JSON, или None, если для этого композитора её нет."""
    if kind == "hyprland":
        return ["hyprctl", "clients", "-j"]
    if kind == "sway":
        return ["swaymsg", "-t", "get_tree"]
    return None


def _waydroid_package(app_id: str) -> str:
    return app_id[len("waydroid."):] if app_id.startswith("waydroid.") else ""


def _walk_sway(node: Mapping, found: list) -> None:
    app_id = str(node.get("app_id") or "")
    package = _waydroid_package(app_id)
    if package:
        # window_rect — содержимое окна без рамки и заголовка, то есть как раз
        # размер картинки Android.
        rect = node.get("window_rect") or node.get("rect") or {}
        found.append(
            {
                "package": package,
                "width": int(rect.get("width", 0)),
                "height": int(rect.get("height", 0)),
                "focused": bool(node.get("focused")),
            }
        )
    for key in ("nodes", "floating_nodes"):
        for child in node.get(key) or []:
            if isinstance(child, Mapping):
                _walk_sway(child, found)


def parse_windows(kind: "str | None", text: str) -> list:
    """Окна Waydroid из вывода window_argv(): пакет, размер, в фокусе ли.

    Формат тот же, что у waydroid.host_windows(): ``package``, ``width``,
    ``height``, ``focused``. На любую ошибку разбора — пустой список.
    """
    try:
        data = json.loads(text or "null")
    except ValueError:
        return []
    found: list = []
    try:
        if kind == "hyprland" and isinstance(data, list):
            for item in data:
                package = _waydroid_package(str(item.get("class") or ""))
                if package:
                    size = item.get("size") or [0, 0]
                    found.append(
                        {
                            "package": package,
                            "width": int(size[0]),
                            "height": int(size[1]),
                            "focused": item.get("focusHistoryID") == 0,
                        }
                    )
        elif kind == "sway" and isinstance(data, dict):
            _walk_sway(data, found)
    except (TypeError, ValueError, IndexError, AttributeError):
        return []
    return found


# -- права root ---------------------------------------------------------------

#: Помощник для ``sudo -A``: пароль спрашивает системное окно, Merci его не видит.
#: ``exec`` обязателен — после него в цепочке передачи пароля нет наших команд.
ASKPASS_PROGRAMS = ("kdialog", "zenity", "qarma", "yad")

ASKPASS_SCRIPT = """#!/bin/sh
# Спрашивает пароль системным окном и отдаёт его напрямую sudo.
# exec обязателен: после него в цепочке передачи пароля нет ни одной нашей команды.
if command -v kdialog >/dev/null 2>&1; then
  exec kdialog --password "$1" --title "Merci"
fi
if command -v zenity >/dev/null 2>&1; then
  exec zenity --password --title "Merci"
fi
if command -v qarma >/dev/null 2>&1; then
  exec qarma --password --title "Merci"
fi
if command -v yad >/dev/null 2>&1; then
  exec yad --entry --hide-text --title "Merci" --text "$1"
fi
echo "не найдено ни одного окна для пароля (kdialog, zenity, qarma, yad)" >&2
exit 1
"""


def can_ask_password_in_window(available: Collection) -> bool:
    return any(program in available for program in ASKPASS_PROGRAMS)


def root_prefix(available: Collection, askpass: str = "") -> list:
    """Приставка к команде, исполняемой под root. Пустой список — повышать нечем.

    Порядок: ``sudo -A`` с окном пароля (если есть окно и путь к помощнику),
    затем pkexec и run0 — но только при живом агенте polkit (без него они молча
    виснут: сеанс считает агента зарегистрированным, а спросить некому), затем
    «голый» sudo / doas / run0 — их надо запускать в терминале, см.
    ``root_needs_terminal``.

    Помощнику под root (merci-instance.py) нужно знать, кто его позвал: sudo и
    run0 сообщают это в SUDO_UID, pkexec — в PKEXEC_UID, doas — только в
    DOAS_USER (имя, не uid). Проверка домашнего каталога в помощнике сейчас читает
    только SUDO_UID и на других приставках молча отключается — см. отчёт.
    """
    agent = "polkit-agent" in available
    if "sudo" in available and askpass and can_ask_password_in_window(available):
        return ["env", f"SUDO_ASKPASS={askpass}", "sudo", "-A"]
    if "pkexec" in available and agent:
        return ["pkexec"]
    if "run0" in available and agent:
        return ["run0"]
    if "sudo" in available:
        return ["sudo"]
    if "doas" in available:
        return ["doas"]
    if "run0" in available:
        return ["run0"]
    return []


def root_needs_terminal(available: Collection, askpass: str = "") -> bool:
    """Нужен ли терминал, чтобы ввести пароль для приставки из root_prefix()."""
    prefix = root_prefix(available, askpass)
    return bool(prefix) and prefix[0] in ("sudo", "doas", "run0")


#: Терминалы, в которых можно ввести пароль: (команда, приставка к ``sh -lc скрипт``).
#: Первые шесть — как в Merci сейчас; остальные добавлены по общеизвестным ключам и
#: на живых системах не проверялись.
TERMINALS = (
    ("kitty", ["kitty", "--title", "Merci", "sh", "-lc"]),
    ("alacritty", ["alacritty", "--title", "Merci", "-e", "sh", "-lc"]),
    ("foot", ["foot", "-T", "Merci", "sh", "-lc"]),
    ("konsole", ["konsole", "-e", "sh", "-lc"]),
    ("gnome-terminal", ["gnome-terminal", "--", "sh", "-lc"]),
    ("xterm", ["xterm", "-e", "sh", "-lc"]),
    ("xfce4-terminal", ["xfce4-terminal", "-x", "sh", "-lc"]),
    ("lxterminal", ["lxterminal", "-e", "sh", "-lc"]),
    ("x-terminal-emulator", ["x-terminal-emulator", "-e", "sh", "-lc"]),
)


def terminal_argv(available: Collection) -> "list | None":
    for name, argv in TERMINALS:
        if name in available:
            return list(argv)
    return None


# -- сетевой экран ----------------------------------------------------------


def firewall(host: Host, available: "Collection | None" = None) -> str:
    """Какой экран реально включён: ufw | firewalld | nftables | none.

    Смотрим на включённость, а не на наличие: ufw в Ubuntu стоит, но выключен.
    ``available`` по умолчанию — ``host.tools``. Из-за разного состояния «стоит»
    и «работает» firewalld идёт первым: он по умолчанию включён в Fedora и openSUSE.
    nftables означает «служба nftables загружает свои правила»: что в них, без
    root не прочитать, и менять их Merci не берётся.
    """
    marks = host.tools if available is None else available
    if "active:firewalld" in marks:
        return "firewalld"
    if "active:ufw" in marks:
        return "ufw"
    if "active:nftables" in marks:
        return "nftables"
    return "none"


def firewall_fix_script(kind: str) -> "str | None":
    """Команды под root, открывающие контейнеру выход в сеть; None — не знаем как.

    * ufw: правила только для интерфейса waydroid0 (проект советует шире —
      ``ufw default allow FORWARD``, что открывает пересылку для всех).
    * firewalld: интерфейс в зону trusted — команда из документации проекта;
      ``--add-forward`` нужен в новых firewalld и в старых просто не принимается.
    * nftables: Merci чужие правила не правит — только подсказка.
    """
    if kind == "ufw":
        return "ufw allow in on waydroid0; ufw route allow in on waydroid0"
    if kind == "firewalld":
        return (
            "firewall-cmd --permanent --zone=trusted --add-interface=waydroid0; "
            "firewall-cmd --permanent --zone=trusted --add-forward || true; "
            "firewall-cmd --reload"
        )
    return None


def firewall_hint(kind: str) -> str:
    if kind == "ufw":
        return tr("ufw блокирует DHCP и выход наружу с waydroid0")
    if kind == "firewalld":
        return tr("firewalld не доверяет интерфейсу waydroid0: контейнеру нужна зона trusted")
    if kind == "nftables":
        return tr(
            "Включена служба nftables со своими правилами: Merci их не меняет. Разрешите "
            "на интерфейсе waydroid0 входящие DHCP (67/udp) и DNS (53) и пересылку наружу."
        )
    return ""


# -- службы -------------------------------------------------------------------

CONTAINER_SERVICE = "waydroid-container"


def container_service_command(host: Host, action: str = "restart") -> "str | None":
    """Команда под root для службы контейнера Waydroid; None — не знаем как.

    * systemd — ``systemctl <действие> waydroid-container.service`` (так сейчас
      во всём Merci);
    * OpenRC — ``rc-service waydroid-container <действие>``: пакет Alpine ставит
      /etc/init.d/waydroid-container (по APKBUILD);
    * runit — ``sv <действие> waydroid-container``: шаблон пакета Void вызывает
      ``vsv waydroid-container``, то есть служба с таким именем есть.
    Для OpenRC и runit команды выведены из метаданных пакетов и на живых системах
    не запускались.
    """
    if action not in ("start", "stop", "restart"):
        return None
    if host.init == "systemd":
        return f"systemctl {action} {CONTAINER_SERVICE}.service"
    if host.init == "openrc":
        return f"rc-service {CONTAINER_SERVICE} {action}"
    if host.init == "runit":
        return f"sv {action} {CONTAINER_SERVICE}"
    return None


def supports_user_services(host: Host) -> bool:
    """Есть ли у пользователя ``systemctl --user`` (служба приёма ссылок, wd-venus)."""
    return host.init == "systemd"


# -- проверка ядра -----------------------------------------------------------

#: Проверка binder одной командой оболочки (код 0 — есть). Шире, чем в Merci: учтён
#: /boot/config-* (Debian, Fedora) — /proc/config.gz есть не везде.
BINDER_PROBE = (
    "test -e /dev/binderfs || test -e /dev/binder || grep -qw binder /proc/filesystems "
    "|| modinfo binder_linux >/dev/null 2>&1 "
    "|| zgrep -q '^CONFIG_ANDROID_BINDERFS=[ym]' /proc/config.gz 2>/dev/null "
    "|| grep -q '^CONFIG_ANDROID_BINDERFS=[ym]' \"/boot/config-$(uname -r)\" 2>/dev/null"
)
