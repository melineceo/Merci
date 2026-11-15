"""Проверки слоя переносимости (src/merci/platform.py).

Запуск из корня проекта:  python3 -m unittest discover -s tests -v

Хост не нужен: ``detect`` получает поддельную ``run``. Тексты os-release
ниже воспроизводят опознавательные поля (ID, ID_LIKE, VARIANT_ID) настоящих
систем, но это не побайтные копии файлов: остальные поля (ссылки, цвета)
отброшены, они на выбор семейства не влияют.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from merci import hostinfo as P  # noqa: E402

OS_RELEASE = {
    "arch": 'NAME="Arch Linux"\nPRETTY_NAME="Arch Linux"\nID=arch\nBUILD_ID=rolling\n',
    "cachyos": (
        'NAME="CachyOS Linux"\nPRETTY_NAME="CachyOS"\nID=cachyos\nID_LIKE=arch\n'
        "BUILD_ID=rolling\n"
    ),
    "endeavouros": (
        'NAME="EndeavourOS"\nPRETTY_NAME="EndeavourOS"\nID=endeavouros\nID_LIKE=arch\n'
    ),
    "debian": (
        'PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nNAME="Debian GNU/Linux"\n'
        'VERSION_ID="13"\nVERSION_CODENAME=trixie\nID=debian\n'
    ),
    "ubuntu": (
        'PRETTY_NAME="Ubuntu 24.04.2 LTS"\nNAME="Ubuntu"\nVERSION_ID="24.04"\n'
        "ID=ubuntu\nID_LIKE=debian\nVERSION_CODENAME=noble\n"
    ),
    "mint": (
        'NAME="Linux Mint"\nVERSION_ID="22"\nID=linuxmint\nID_LIKE="ubuntu debian"\n'
        'PRETTY_NAME="Linux Mint 22"\n'
    ),
    "fedora": (
        'NAME="Fedora Linux"\nVERSION_ID=43\nID=fedora\n'
        'PRETTY_NAME="Fedora Linux 43 (Workstation Edition)"\nVARIANT_ID=workstation\n'
    ),
    "silverblue": (
        'NAME="Fedora Linux"\nVERSION_ID=43\nID=fedora\nVARIANT="Silverblue"\n'
        "VARIANT_ID=silverblue\n"
    ),
    "kinoite": (
        'NAME="Fedora Linux"\nVERSION_ID=43\nID=fedora\nVARIANT="Kinoite"\n'
        "VARIANT_ID=kinoite\n"
    ),
    "tumbleweed": (
        'NAME="openSUSE Tumbleweed"\nID="opensuse-tumbleweed"\n'
        'ID_LIKE="opensuse suse"\nPRETTY_NAME="openSUSE Tumbleweed"\n'
    ),
    "leap": (
        'NAME="openSUSE Leap"\nVERSION_ID="16.0"\nID="opensuse-leap"\n'
        'ID_LIKE="suse opensuse"\n'
    ),
    "nixos": (
        'NAME=NixOS\nID=nixos\nVERSION_ID="25.05"\nPRETTY_NAME="NixOS 25.05 (Warbler)"\n'
    ),
    "void": 'NAME="Void"\nID="void"\nPRETTY_NAME="Void Linux"\n',
    "alpine": (
        "NAME=\"Alpine Linux\"\nID=alpine\nVERSION_ID=3.22.0\n"
        "PRETTY_NAME=\"Alpine Linux v3.22\"\n"
    ),
    "gentoo": 'NAME=Gentoo\nID=gentoo\nPRETTY_NAME="Gentoo Linux"\n',
    "steamos": (
        'NAME="SteamOS"\nID=steamos\nID_LIKE=arch\nPRETTY_NAME="SteamOS Holo"\n'
    ),
}


def host(name, session="wayland", desktop="", init="systemd", ostree=False, tools=()):
    env = {"XDG_SESSION_TYPE": session, "XDG_CURRENT_DESKTOP": desktop}
    return P.make_host(OS_RELEASE[name], env=env, init=init, ostree=ostree, tools=tools)


class FamilyTests(unittest.TestCase):
    def check(self, name, family, manager, **kwargs):
        h = host(name, **kwargs)
        self.assertEqual(h.family, family, name)
        self.assertEqual(h.package_manager, manager, name)
        return h

    def test_arch_family(self):
        self.check("arch", "arch", "pacman")
        cachy = self.check("cachyos", "arch", "pacman")
        self.assertEqual(cachy.distro, "cachyos")
        self.assertEqual(cachy.like, ("arch",))
        self.check("endeavouros", "arch", "pacman")

    def test_debian_family(self):
        self.check("debian", "debian", "apt")
        self.check("ubuntu", "debian", "apt")
        mint = self.check("mint", "debian", "apt")
        self.assertEqual(mint.like, ("ubuntu", "debian"))

    def test_fedora_family(self):
        fedora = self.check("fedora", "fedora", "dnf")
        self.assertFalse(fedora.immutable)

    def test_immutable_fedora_uses_rpm_ostree(self):
        for name in ("silverblue", "kinoite"):
            h = self.check(name, "fedora", "rpm-ostree")
            self.assertTrue(h.immutable, name)

    def test_ostree_marker_alone_is_enough(self):
        # Поле VARIANT_ID может отсутствовать (производные образы), а маркер
        # /run/ostree-booted — есть всегда.
        h = host("fedora", ostree=True)
        self.assertEqual(h.package_manager, "rpm-ostree")
        self.assertTrue(h.immutable)

    def test_suse(self):
        self.check("tumbleweed", "suse", "zypper")
        self.check("leap", "suse", "zypper")

    def test_nixos_is_immutable(self):
        h = self.check("nixos", "nix", "nix")
        self.assertTrue(h.immutable)

    def test_void_and_alpine_init(self):
        self.assertEqual(self.check("void", "void", "xbps", init="runit").init, "runit")
        self.assertEqual(self.check("alpine", "alpine", "apk", init="openrc").init, "openrc")

    def test_unknown_distro(self):
        h = self.check("gentoo", "unknown", "")
        self.assertFalse(h.immutable)

    def test_steamos_is_arch_like_and_immutable(self):
        h = self.check("steamos", "arch", "pacman")
        self.assertTrue(h.immutable)

    def test_empty_os_release(self):
        h = P.make_host("")
        self.assertEqual((h.family, h.package_manager, h.distro), ("unknown", "", ""))


class ParseTests(unittest.TestCase):
    def test_quotes_and_comments(self):
        data = P.parse_os_release('# comment\nID="debian"\nNAME=\'Debian GNU/Linux\'\nBAD LINE\nlower=1\n')
        self.assertEqual(data["ID"], "debian")
        self.assertEqual(data["NAME"], "Debian GNU/Linux")
        self.assertNotIn("lower", data)

    def test_session_detection(self):
        self.assertEqual(host("arch", session="wayland").session, "wayland")
        self.assertEqual(host("arch", session="x11").session, "x11")
        # tty и пустое значение ничего не говорят: смотрим на дисплеи.
        h = P.make_host("ID=arch", env={"XDG_SESSION_TYPE": "tty", "WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0"})
        self.assertEqual(h.session, "wayland")
        h = P.make_host("ID=arch", env={"DISPLAY": ":0"})
        self.assertEqual(h.session, "x11")
        h = P.make_host("ID=arch", env={})
        self.assertEqual(h.session, "unknown")

    def test_desktop_split_and_lowercase(self):
        h = host("ubuntu", desktop="ubuntu:GNOME")
        self.assertEqual(h.desktop, ("ubuntu", "gnome"))
        self.assertEqual(host("arch", desktop="Hyprland").desktop, ("hyprland",))
        self.assertEqual(host("arch", desktop="X-Cinnamon").desktop, ("x-cinnamon",))


class DetectTests(unittest.TestCase):
    PROBE = (
        "@os-release\n" + OS_RELEASE["ubuntu"] +
        "@env\nXDG_SESSION_TYPE=x11\nXDG_CURRENT_DESKTOP=ubuntu:GNOME\nDISPLAY=:0\n"
        "@user-env\nWAYLAND_DISPLAY=wayland-0\n"
        "@init\nsystemd\nsystemd-dir\n"
        "@flags\napparmor\nbinder-device\n"
        "@tools\nsudo\nzenity\ncage\nufw\n"
        "@active\nufw\n"
    )

    def test_host_from_probe(self):
        h = P.host_from_probe(self.PROBE)
        self.assertEqual((h.distro, h.family, h.session, h.init), ("ubuntu", "debian", "x11", "systemd"))
        self.assertEqual(h.desktop, ("ubuntu", "gnome"))
        # Команда ufw и включённый ufw — разные факты, оба должны сохраниться.
        self.assertIn("ufw", h.tools)
        self.assertIn("active:ufw", h.tools)
        # В GNOME агент polkit — внутри оболочки, отдельного процесса нет.
        self.assertIn("polkit-agent", h.tools)

    def test_env_of_the_call_beats_user_manager(self):
        h = P.host_from_probe(self.PROBE)
        self.assertEqual(h.session, "x11")

    def test_detect_uses_run_once(self):
        calls = []

        def run(argv):
            calls.append(argv)
            return 0, self.PROBE

        h = P.detect(run)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][:2], ["sh", "-c"])
        self.assertEqual(h.family, "debian")

    def test_detect_falls_back_to_plain_os_release(self):
        def run(argv):
            if argv[0] == "sh":
                return 127, ""
            return 0, OS_RELEASE["fedora"]

        h = P.detect(run)
        self.assertEqual((h.family, h.session, h.init), ("fedora", "unknown", "unknown"))

    def test_detect_survives_a_dead_host(self):
        def run(argv):
            raise OSError("flatpak-spawn нет")

        h = P.detect(run)
        self.assertEqual(h.family, "unknown")

    def test_probe_script_does_not_match_itself(self):
        # pgrep -f видит командную строку оболочки, а в ней лежит весь сценарий:
        # без скобки в первой букве шаблон нашёл бы сам себя.
        script = P.PROBE_SCRIPT
        self.assertIn("[p]olkit", script)
        self.assertNotIn('"polkit.*agent', script)

    def test_probe_script_is_posix_sh_runnable(self):
        import subprocess

        result = subprocess.run(["sh", "-n", "-c", P.PROBE_SCRIPT], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class PackageTests(unittest.TestCase):
    def test_waydroid_names(self):
        for name in ("arch", "cachyos", "debian", "ubuntu", "mint", "fedora", "void", "alpine"):
            self.assertEqual(P.packages(host(name), "waydroid"), ["waydroid"], name)

    def test_waydroid_unknown_where_not_sure(self):
        # openSUSE без официального пакета, NixOS — декларативно, Gentoo — не знаем.
        for name in ("tumbleweed", "leap", "nixos", "gentoo"):
            self.assertIsNone(P.packages(host(name), "waydroid"), name)

    def test_adb_names(self):
        self.assertEqual(P.packages(host("arch"), "adb"), ["android-tools"])
        self.assertEqual(P.packages(host("fedora"), "adb"), ["android-tools"])
        self.assertEqual(P.packages(host("tumbleweed"), "adb"), ["android-tools"])
        self.assertEqual(P.packages(host("void"), "adb"), ["android-tools"])
        self.assertEqual(P.packages(host("alpine"), "adb"), ["android-tools"])
        # У Debian и Ubuntu пакет называется просто adb.
        self.assertEqual(P.packages(host("debian"), "adb"), ["adb"])
        self.assertEqual(P.packages(host("ubuntu"), "adb"), ["adb"])
        self.assertEqual(P.packages(host("mint"), "adb"), ["adb"])

    def test_gamescope_absent_on_debian(self):
        self.assertEqual(P.packages(host("arch"), "gamescope"), ["gamescope"])
        self.assertEqual(P.packages(host("fedora"), "gamescope"), ["gamescope"])
        self.assertIsNone(P.packages(host("debian"), "gamescope"))

    def test_unknown_what(self):
        self.assertIsNone(P.packages(host("arch"), "no-such-thing"))
        self.assertIsNone(P.install_script(host("arch"), "no-such-thing"))

    def test_packages_returns_a_copy(self):
        names = P.packages(host("arch"), "waydroid")
        names.append("x")
        self.assertEqual(P.packages(host("arch"), "waydroid"), ["waydroid"])


class InstallScriptTests(unittest.TestCase):
    def test_arch_script_keeps_pacman_lock_handling(self):
        script = P.install_script(host("cachyos"), "waydroid")
        self.assertIn("pacman -S --needed --noconfirm waydroid", script)
        self.assertIn("db.lck", script)
        self.assertIn("PACMAN_EXIT", script)

    def test_apt_script_is_noninteractive_and_guarded(self):
        script = P.install_script(host("ubuntu"), "waydroid")
        self.assertIn("apt-get install -y", script)
        self.assertIn("DEBIAN_FRONTEND=noninteractive", script)
        # Без пакета в apt сценарий не пытается подключать чужой репозиторий.
        self.assertIn("apt-cache policy waydroid", script)
        self.assertIn("exit 3", script)
        self.assertNotIn("repo.waydro.id", script)

    def test_apt_script_for_adb_has_no_repo_guard(self):
        script = P.install_script(host("debian"), "adb")
        self.assertIn("apt-get install -y", script)
        self.assertIn(" adb", script)
        self.assertNotIn("apt-cache policy", script)

    def test_other_managers(self):
        self.assertEqual(P.install_script(host("fedora"), "adb"), "dnf install -y android-tools")
        self.assertEqual(
            P.install_script(host("tumbleweed"), "cage"),
            "zypper --non-interactive install cage",
        )
        self.assertEqual(P.install_script(host("void"), "waydroid"), "xbps-install -Sy waydroid")
        self.assertEqual(P.install_script(host("alpine"), "waydroid"), "apk add waydroid")

    def test_nothing_piped_into_a_shell(self):
        for name in OS_RELEASE:
            for what in P.WANTS:
                script = P.install_script(host(name), what) or ""
                self.assertNotIn("| sh", script, (name, what))
                self.assertNotIn("| bash", script, (name, what))
                self.assertNotIn("curl -s", script, (name, what))

    def test_immutable_systems_never_get_a_script(self):
        for name in ("silverblue", "kinoite", "nixos", "steamos"):
            self.assertIsNone(P.install_script(host(name), "waydroid"), name)
            self.assertIsNone(P.install_script(host(name), "adb"), name)

    def test_unknown_where_we_do_not_know(self):
        self.assertIsNone(P.install_script(host("tumbleweed"), "waydroid"))
        self.assertIsNone(P.install_script(host("gentoo"), "waydroid"))

    def test_reboot_only_for_rpm_ostree(self):
        self.assertTrue(P.needs_reboot(host("silverblue")))
        self.assertFalse(P.needs_reboot(host("fedora")))
        self.assertFalse(P.needs_reboot(host("arch")))


class HintTests(unittest.TestCase):
    def test_silverblue_hint_names_the_command_and_reboot(self):
        text = P.install_hint(host("silverblue"), "waydroid")
        self.assertIn("rpm-ostree install waydroid", text)
        self.assertIn("перезагруз", text)

    def test_nixos_hint(self):
        text = P.install_hint(host("nixos"), "waydroid")
        self.assertIn("virtualisation.waydroid.enable = true;", text)
        self.assertIn("Wayland", text)
        self.assertIn("android-tools", P.install_hint(host("nixos"), "adb"))

    def test_suse_waydroid_points_to_community_guide(self):
        text = P.install_hint(host("tumbleweed"), "waydroid")
        self.assertIn("discussions/1463", text)

    def test_debian_hint_does_not_tell_merci_to_pipe_scripts(self):
        text = P.install_hint(host("debian"), "waydroid")
        self.assertIn("apt install waydroid", text)
        self.assertIn("не исполняет", text)

    def test_unknown_family_has_a_docs_link(self):
        self.assertIn("docs.waydro.id", P.install_hint(host("gentoo"), "waydroid"))

    def test_hint_for_known_package(self):
        text = P.install_hint(host("fedora"), "adb")
        self.assertIn("android-tools", text)
        self.assertIn("dnf", text)

    def test_binder_hint_is_never_empty(self):
        for name in OS_RELEASE:
            self.assertTrue(P.binder_hint(host(name)), name)
        self.assertIn("binder_linux-dkms", P.binder_hint(host("arch")))

    def test_security_notes(self):
        notes = P.security_notes(host("fedora", tools=("selinux-enforcing",)))
        self.assertEqual(len(notes), 1)
        self.assertIn("SELinux", notes[0])
        notes = P.security_notes(host("ubuntu", tools=("apparmor",)))
        self.assertIn("waydroid-lxc", notes[0])
        self.assertEqual(P.security_notes(host("arch")), [])


class NestedCompositorTests(unittest.TestCase):
    def test_wayland_needs_nothing(self):
        h = host("arch", session="wayland")
        self.assertFalse(P.needs_nested_compositor(h))
        self.assertIsNone(P.nested_compositor(h, {"cage", "weston"}))

    def test_x11_prefers_cage(self):
        h = host("ubuntu", session="x11", desktop="XFCE")
        self.assertTrue(P.needs_nested_compositor(h))
        self.assertEqual(P.nested_compositor(h, {"cage", "weston"}), ["cage", "--"])

    def test_x11_falls_back_to_weston(self):
        h = host("ubuntu", session="x11")
        argv = P.nested_compositor(h, {"weston"})
        self.assertEqual(argv[:2], ["sh", "-c"])
        self.assertIn("weston --socket", argv[2])
        self.assertIn('"$@"', argv[2])

    def test_x11_without_any_compositor(self):
        self.assertIsNone(P.nested_compositor(host("ubuntu", session="x11"), set()))

    def test_session_start_script_on_wayland_matches_current_merci(self):
        script = P.session_start_script(host("arch"), set())
        self.assertEqual(script, 'S="session start"; (setsid waydroid $S >/dev/null 2>&1 &)')

    def test_session_start_script_on_x11(self):
        h = host("debian", session="x11")
        self.assertEqual(
            P.session_start_script(h, {"cage"}),
            'S="session start"; (setsid cage -- waydroid $S >/dev/null 2>&1 &)',
        )
        # Негде поднять — честное «нельзя», а не команда, которая упадёт.
        self.assertIsNone(P.session_start_script(h, set()))

    def test_session_words_do_not_appear_in_the_script_text(self):
        # Шаблон pkill ищет «waydroid session start» подряд; в тексте шага такой
        # подстроки быть не должно.
        script = P.session_start_script(host("debian", session="x11"), {"cage"})
        self.assertNotIn("waydroid session start", script)


class WindowQueryTests(unittest.TestCase):
    def test_choice_by_desktop(self):
        self.assertEqual(P.window_query(host("arch", desktop="Hyprland")), "hyprland")
        self.assertEqual(P.window_query(host("arch", desktop="sway")), "sway")
        self.assertIsNone(P.window_query(host("fedora", desktop="GNOME")))

    def test_kde_needs_kdotool(self):
        h = host("fedora", desktop="KDE")
        self.assertIsNone(P.window_query(h, set()))
        self.assertEqual(P.window_query(h, {"kdotool"}), "kwin")

    def test_tool_missing(self):
        h = host("arch", desktop="Hyprland")
        self.assertIsNone(P.window_query(h, {"sudo"}))

    def test_x11_has_no_window_query(self):
        self.assertIsNone(P.window_query(host("ubuntu", session="x11", desktop="XFCE")))

    def test_argv(self):
        self.assertEqual(P.window_argv("hyprland"), ["hyprctl", "clients", "-j"])
        self.assertEqual(P.window_argv("sway"), ["swaymsg", "-t", "get_tree"])
        self.assertIsNone(P.window_argv("kwin"))
        self.assertIsNone(P.window_argv(None))

    def test_parse_hyprland(self):
        text = (
            '[{"class":"kitty","size":[800,600],"focusHistoryID":1},'
            '{"class":"waydroid.com.example.app","size":[1280,720],"focusHistoryID":0},'
            '{"class":"waydroid.org.other","size":[640,480],"focusHistoryID":2}]'
        )
        windows = P.parse_windows("hyprland", text)
        self.assertEqual(
            windows,
            [
                {"package": "com.example.app", "width": 1280, "height": 720, "focused": True},
                {"package": "org.other", "width": 640, "height": 480, "focused": False},
            ],
        )

    def test_parse_sway_walks_tree_and_floating_nodes(self):
        text = (
            '{"nodes":[{"nodes":[{"app_id":"waydroid.com.example.app","focused":true,'
            '"rect":{"width":1282,"height":742},"window_rect":{"width":1280,"height":720},'
            '"nodes":[]}],"floating_nodes":[{"app_id":"waydroid.org.other","focused":false,'
            '"rect":{"width":500,"height":400},"nodes":[]},'
            '{"app_id":"firefox","rect":{"width":9,"height":9},"nodes":[]}]}]}'
        )
        windows = P.parse_windows("sway", text)
        self.assertEqual(len(windows), 2)
        first = [w for w in windows if w["package"] == "com.example.app"][0]
        # Размер картинки Android — содержимое окна, без рамки.
        self.assertEqual((first["width"], first["height"], first["focused"]), (1280, 720, True))
        second = [w for w in windows if w["package"] == "org.other"][0]
        self.assertEqual((second["width"], second["height"]), (500, 400))

    def test_parse_garbage_is_empty(self):
        for kind in ("hyprland", "sway", "kwin", None):
            self.assertEqual(P.parse_windows(kind, ""), [])
            self.assertEqual(P.parse_windows(kind, "not json"), [])
            self.assertEqual(P.parse_windows(kind, '{"a":1}'), [])
        self.assertEqual(P.parse_windows("hyprland", '[{"class":"waydroid.x","size":"bad"}]'), [])


class RootPrefixTests(unittest.TestCase):
    def test_sudo_with_window_password(self):
        prefix = P.root_prefix({"sudo", "pkexec", "zenity", "polkit-agent"}, "/data/askpass.sh")
        self.assertEqual(prefix, ["env", "SUDO_ASKPASS=/data/askpass.sh", "sudo", "-A"])

    def test_no_askpass_path_means_no_dash_a(self):
        # Окно есть, а помощника ещё нет — без него «sudo -A» не работает.
        self.assertEqual(P.root_prefix({"sudo", "zenity"}), ["sudo"])

    def test_pkexec_only_with_agent(self):
        self.assertEqual(P.root_prefix({"pkexec", "polkit-agent"}), ["pkexec"])
        # Агента нет — pkexec повис бы молча, поэтому выбираем то, что спросит в терминале.
        self.assertEqual(P.root_prefix({"pkexec", "doas"}), ["doas"])
        self.assertEqual(P.root_prefix({"pkexec"}), [])

    def test_run0_and_doas(self):
        self.assertEqual(P.root_prefix({"run0", "polkit-agent"}), ["run0"])
        self.assertEqual(P.root_prefix({"doas"}), ["doas"])
        self.assertEqual(P.root_prefix({"run0"}), ["run0"])

    def test_nothing_available(self):
        self.assertEqual(P.root_prefix(set()), [])

    def test_terminal_needed(self):
        self.assertFalse(P.root_needs_terminal({"sudo", "zenity"}, "/x"))
        self.assertTrue(P.root_needs_terminal({"sudo"}))
        self.assertTrue(P.root_needs_terminal({"doas"}))
        self.assertFalse(P.root_needs_terminal({"pkexec", "polkit-agent"}))
        self.assertFalse(P.root_needs_terminal(set()))

    def test_askpass_script_hands_over_to_the_dialog(self):
        for program in P.ASKPASS_PROGRAMS:
            self.assertIn(f"exec {program}", P.ASKPASS_SCRIPT)
        self.assertTrue(P.ASKPASS_SCRIPT.startswith("#!/bin/sh"))

    def test_terminals(self):
        self.assertEqual(P.terminal_argv({"konsole", "xterm"})[0], "konsole")
        self.assertEqual(P.terminal_argv({"xterm"})[0], "xterm")
        self.assertIsNone(P.terminal_argv(set()))


class FirewallTests(unittest.TestCase):
    def test_firewalld_first(self):
        h = host("fedora", tools={"active:firewalld", "ufw"})
        self.assertEqual(P.firewall(h), "firewalld")

    def test_ufw_must_be_enabled_not_merely_installed(self):
        self.assertEqual(P.firewall(host("ubuntu", tools={"ufw"})), "none")
        self.assertEqual(P.firewall(host("ubuntu", tools={"ufw", "active:ufw"})), "ufw")

    def test_nftables(self):
        self.assertEqual(P.firewall(host("arch", tools={"nft", "active:nftables"})), "nftables")

    def test_explicit_available_overrides_host_tools(self):
        h = host("arch", tools={"active:ufw"})
        self.assertEqual(P.firewall(h, set()), "none")

    def test_none(self):
        self.assertEqual(P.firewall(host("alpine")), "none")

    def test_fix_scripts(self):
        self.assertIn("ufw allow in on waydroid0", P.firewall_fix_script("ufw"))
        self.assertIn("ufw route allow in on waydroid0", P.firewall_fix_script("ufw"))
        script = P.firewall_fix_script("firewalld")
        self.assertIn("--zone=trusted --add-interface=waydroid0", script)
        self.assertIn("--permanent", script)
        self.assertIn("firewall-cmd --reload", script)
        # Чужие правила nftables не трогаем.
        self.assertIsNone(P.firewall_fix_script("nftables"))
        self.assertIsNone(P.firewall_fix_script("none"))

    def test_hints(self):
        for kind in ("ufw", "firewalld", "nftables"):
            self.assertTrue(P.firewall_hint(kind), kind)
        self.assertEqual(P.firewall_hint("none"), "")


class ServiceTests(unittest.TestCase):
    def test_container_service_by_init(self):
        self.assertEqual(
            P.container_service_command(host("arch")), "systemctl restart waydroid-container.service"
        )
        self.assertEqual(
            P.container_service_command(host("alpine", init="openrc"), "stop"),
            "rc-service waydroid-container stop",
        )
        self.assertEqual(
            P.container_service_command(host("void", init="runit")), "sv restart waydroid-container"
        )

    def test_unknown_init_or_action(self):
        self.assertIsNone(P.container_service_command(host("arch", init="unknown")))
        self.assertIsNone(P.container_service_command(host("arch"), "reload; rm -rf /"))

    def test_user_services_only_on_systemd(self):
        self.assertTrue(P.supports_user_services(host("arch")))
        self.assertFalse(P.supports_user_services(host("void", init="runit")))


if __name__ == "__main__":
    unittest.main()
