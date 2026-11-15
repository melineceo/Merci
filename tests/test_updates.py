"""Проверки обновлений: разбор ответов (src/merci/updates.py) и работа
помощника с образами (data/instances/merci-instance.py).

Запуск из корня проекта:  python3 -m unittest discover -s tests -v

Хост не нужен. Проверка получает поддельную ``run``; помощник работает во
временном каталоге вместо /var/lib/waydroid, а вместо настоящего
``waydroid upgrade`` — маленький сценарий, который ведёт себя как он:
переписывает образы и отметки о сборке, либо обрывается на середине.
"""

import atexit
import importlib.util
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))

# Данные Merci на время проверок — во временном каталоге. Шаги читают и
# пишут настройки (Settings), и без этого проверки оставляли бы свои следы
# в ~/.local/share/merci того, кто их запускает.
_DATA = tempfile.mkdtemp(prefix="merci-test-data-")
os.environ["XDG_DATA_HOME"] = _DATA
atexit.register(shutil.rmtree, _DATA, True)

from merci import hostinfo, updates  # noqa: E402

CFG = """[waydroid]
arch = x86_64
images_path = /var/lib/waydroid/images
system_datetime = 1775215493
vendor_datetime = 1777409843
mount_overlays = True
system_ota = https://ota.waydro.id/system/lineage/waydroid_x86_64/VANILLA.json
vendor_ota = https://ota.waydro.id/vendor/waydroid_x86_64/MAINLINE.json
nvidia_venus_socket = /run/waydroid-venus/venus.sock

[properties]
ro.adb.secure = 1
persist.waydroid.use_subsurface = false
ro.product.cpu.abilist = x86_64,x86,arm64-v8a
"""


def ota(*builds):
    return json.dumps({"response": [
        {"datetime": stamp, "filename": f"lineage-{version}-x.zip", "id": "0" * 64,
         "romtype": "VANILLA", "size": size, "url": "https://example.invalid/x.zip",
         "version": version}
        for stamp, version, size in builds
    ]})


SYSTEM = ota((1790539062, "20.0", 847449341), (1775215493, "20.0", 838353482))
VENDOR = ota((1792667170, "20.0", 189818691), (1777409843, "20.0", 189192029))


class ParsingTest(unittest.TestCase):
    def test_cfg_keeps_case_and_sections(self):
        cfg = updates.parse_cfg(CFG)
        self.assertEqual(cfg["waydroid"]["system_datetime"], "1775215493")
        self.assertEqual(cfg["properties"]["ro.product.cpu.abilist"], "x86_64,x86,arm64-v8a")

    def test_broken_cfg_is_empty(self):
        self.assertEqual(updates.parse_cfg("это не конфиг\n[["), {})

    def test_ota_ignores_garbage(self):
        self.assertEqual(updates.parse_ota("<html>502</html>"), [])
        self.assertEqual(updates.parse_ota('{"response": [{"datetime": "x"}, 5]}'), [])
        self.assertEqual(len(updates.parse_ota(SYSTEM)), 2)

    def test_newer_build_is_what_waydroid_takes(self):
        builds = updates.parse_ota(SYSTEM)
        self.assertEqual(updates.newer_build(builds, 1775215493)["datetime"], 1790539062)
        self.assertIsNone(updates.newer_build(builds, 1790539062))

    def test_android_name(self):
        self.assertEqual(updates.android_name("20.0"), "LineageOS 20 (Android 13)")
        self.assertEqual(updates.android_name("18.1"), "LineageOS 18 (Android 11)")
        self.assertEqual(updates.android_name("мусор"), "")

    def test_version_order(self):
        newer = updates.version_newer
        self.assertTrue(newer("0.1.2-1", "0.2.0-1"))
        self.assertTrue(newer("0.1.2-1", "0.1.2-2"))
        self.assertTrue(newer("1.5.4-1", "1.6.0-1"))
        self.assertTrue(newer("1.9", "1.10"))
        self.assertTrue(newer("1.4.3-1", "1:1.0-1"))
        self.assertFalse(newer("0.2.0-1", "0.2.0-1"))
        self.assertFalse(newer("0.2.0-1", "0.1.9-5"))
        self.assertFalse(newer("0.1.2", ""))
        self.assertFalse(newer("", "0.1.2"))

    def test_big_jump(self):
        self.assertTrue(updates.big_jump("0.1.2-1", "0.2.0-1"))
        self.assertFalse(updates.big_jump("0.2.0-1", "0.2.3-1"))

    def test_aur_answer(self):
        text = json.dumps({"results": [{"Name": "waydroid-nvidia-bin", "Version": "0.2.0-1"}]})
        self.assertEqual(updates.parse_aur(text, "waydroid-nvidia-bin"), "0.2.0-1")
        self.assertEqual(updates.parse_aur(text, "другой"), "")
        self.assertEqual(updates.parse_aur("не json", "x"), "")


class ImagesTest(unittest.TestCase):
    def fetch(self, address):
        return SYSTEM if "/system/" in address else VENDOR

    def test_both_parts_found(self):
        update, notes = updates.images_update(updates.parse_cfg(CFG), self.fetch)
        self.assertEqual(notes, [])
        self.assertEqual(update.key, "images")
        # В строке списка — дата главной части; обе части — в пояснении.
        self.assertIn("03.04.2026", update.current)
        self.assertIn("27.09.2026", update.available)
        self.assertIn("28.04.2026 → 22.10.2026", update.detail)
        self.assertEqual(update.data["parts"], ["system", "vendor"])
        self.assertEqual(update.size_mb, round((847449341 + 189818691) / updates.MEGABYTE))
        self.assertFalse(update.risky)
        self.assertTrue(update.root)

    def test_up_to_date(self):
        cfg = updates.parse_cfg(CFG.replace("1775215493", "1790539062")
                                .replace("1777409843", "1792667170"))
        self.assertEqual(updates.images_update(cfg, self.fetch), (None, []))

    def test_silent_server_is_a_note_not_an_update(self):
        update, notes = updates.images_update(updates.parse_cfg(CFG), lambda _address: None)
        self.assertIsNone(update)
        self.assertEqual(len(notes), 2)

    def test_other_android_version_is_risky(self):
        newer = ota((1790539062, "22.0", 900000000), (1775215493, "20.0", 838353482))
        update, _notes = updates.images_update(
            updates.parse_cfg(CFG), lambda address: newer if "/system/" in address else VENDOR
        )
        self.assertTrue(update.risky)

    def test_preinstalled_images_are_left_alone(self):
        cfg = updates.parse_cfg(CFG.replace("/var/lib/waydroid/images",
                                            "/usr/share/waydroid-extra/images"))
        self.assertEqual(updates.images_update(cfg, self.fetch), (None, []))

    def test_package_owned_channel_is_skipped(self):
        # waydroid-nvidia в своём режиме ведёт образ драйверов сам: канал — не адрес.
        cfg = updates.parse_cfg(CFG.replace(
            "vendor_ota = https://ota.waydro.id/vendor/waydroid_x86_64/MAINLINE.json",
            "vendor_ota = /usr/lib/waydroid-nvidia/vendor/channel.json"))
        update, _notes = updates.images_update(cfg, self.fetch)
        self.assertEqual(update.data["parts"], ["system"])


class FakeHost:
    """Поддельный хост: ответы по началу команды."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def __call__(self, argv, _timeout):
        line = " ".join(argv)
        self.asked.append(line)
        for marker, answer in self.answers:
            if marker in line:
                return answer
        return 1, ""


ARCH = hostinfo.Host(distro="cachyos", like=("arch",), family="arch", package_manager="pacman")
DEBIAN = hostinfo.Host(distro="debian", family="debian", package_manager="apt")


class PackageTest(unittest.TestCase):
    def test_aur_package_has_update(self):
        run = FakeHost([
            ("pacman -Qqo", (0, "waydroid-nvidia-bin\n")),
            ("pacman -Qqm", (0, "yay-bin\nwaydroid-nvidia-bin\n")),
            ("pacman -Q waydroid-nvidia-bin", (0, "waydroid-nvidia-bin 0.1.2-1\n")),
            ("aur.archlinux.org", (0, json.dumps(
                {"results": [{"Name": "waydroid-nvidia-bin", "Version": "0.2.0-1"}]}))),
        ])
        update, notes = updates.package_update(run, ARCH)
        self.assertEqual(notes, [])
        self.assertEqual((update.current, update.available), ("0.1.2-1", "0.2.0-1"))
        self.assertEqual(update.kind, updates.STEP)
        self.assertTrue(update.data["nvidia"])
        self.assertTrue(update.risky)

    def test_aur_silence_is_a_note(self):
        run = FakeHost([
            ("pacman -Qqo", (0, "waydroid-nvidia-bin\n")),
            ("pacman -Qqm", (0, "waydroid-nvidia-bin\n")),
            ("pacman -Q waydroid-nvidia-bin", (0, "waydroid-nvidia-bin 0.1.2-1\n")),
        ])
        update, notes = updates.package_update(run, ARCH)
        self.assertIsNone(update)
        self.assertEqual(len(notes), 1)

    def test_repo_package_on_arch_is_manual(self):
        run = FakeHost([
            ("pacman -Qqo", (0, "waydroid\n")),
            ("pacman -Qqm", (0, "yay-bin\n")),
            ("pacman -Q waydroid", (0, "waydroid 1.5.4-1\n")),
            ("checkupdates", (0, "linux 7.0-1 -> 7.1-1\nwaydroid 1.5.4-1 -> 1.6.0-1\n")),
        ])
        update, _notes = updates.package_update(run, ARCH)
        self.assertEqual(update.kind, updates.MANUAL)
        self.assertEqual(update.command, "sudo pacman -Syu")

    def test_strange_owner_name_is_ignored(self):
        run = FakeHost([("pacman -Qqo", (0, "waydroid; rm -rf ~\n"))])
        self.assertEqual(updates.package_update(run, ARCH), (None, []))
        self.assertFalse(any("rm -rf" in line and "pacman -Q " in line for line in run.asked))

    def test_apt_candidate(self):
        run = FakeHost([("apt-cache policy", (0, "waydroid:\n  Installed: 1.4.3-1\n"
                                                  "  Candidate: 1.5.0-1\n"))])
        update, _notes = updates.package_update(run, DEBIAN)
        self.assertEqual((update.current, update.available), ("1.4.3-1", "1.5.0-1"))
        self.assertEqual(update.data["source"], "apt")

    def test_apt_same_version(self):
        run = FakeHost([("apt-cache policy", (0, "waydroid:\n  Installed: 1.5.0-1\n"
                                                  "  Candidate: 1.5.0-1\n"))])
        self.assertEqual(updates.package_update(run, DEBIAN), (None, []))


FLATPAK_INFO = """
Merci - Запуск Android-приложений в Linux

          ID: xyz.hackerstone.Merci
         Ref: app/xyz.hackerstone.Merci/x86_64/master
     Version: 0.1.2
      Origin: merci
Installation: user
      Commit: aaaa
"""


class MerciTest(unittest.TestCase):
    def test_repository_has_newer_commit(self):
        run = FakeHost([
            ("flatpak info", (0, FLATPAK_INFO)),
            ("flatpak remotes", (0, "flathub\thttps://dl.flathub.org/repo/\n"
                                    "merci\thttps://melineceo.github.io/Merci/repo/\n")),
            ("flatpak remote-info", (0, "     Version: 0.1.3\n      Commit: bbbb\n")),
        ])
        update, _notes = updates.merci_update(run, "0.1.2")
        self.assertEqual(update.kind, updates.FLATPAK)
        self.assertEqual(update.available, "0.1.3")
        self.assertEqual(update.data["scope"], "--user")

    def test_same_commit_means_up_to_date(self):
        run = FakeHost([
            ("flatpak info", (0, FLATPAK_INFO)),
            ("flatpak remotes", (0, "merci\thttps://melineceo.github.io/Merci/repo/\n")),
            ("flatpak remote-info", (0, "      Commit: aaaa\n")),
        ])
        self.assertEqual(updates.merci_update(run, "0.1.2"), (None, []))

    def test_local_build_looks_at_releases(self):
        run = FakeHost([
            ("flatpak info", (0, FLATPAK_INFO.replace("Origin: merci", "Origin: merci1-origin"))),
            ("flatpak remotes", (0, "merci1-origin\tfile:///home/x/repo\n")),
            ("api.github.com", (0, json.dumps({"tag_name": "v0.2.0"}))),
        ])
        update, _notes = updates.merci_update(run, "0.1.2")
        self.assertEqual(update.kind, updates.MANUAL)
        self.assertEqual(update.available, "0.2.0")
        self.assertEqual(update.command, updates.RELEASES_PAGE)

    def test_no_network_is_silent(self):
        self.assertEqual(updates.merci_update(FakeHost([]), "0.1.2"), (None, []))


class CheckTest(unittest.TestCase):
    def test_everything_together(self):
        run = FakeHost([
            ("cat /var/lib/waydroid/waydroid.cfg", (0, CFG)),
            ("/system/lineage", (0, SYSTEM)),
            ("/vendor/waydroid", (0, VENDOR)),
            ("pacman -Qqo", (0, "waydroid-nvidia-bin\n")),
            ("pacman -Qqm", (0, "waydroid-nvidia-bin\n")),
            ("pacman -Q waydroid-nvidia-bin", (0, "waydroid-nvidia-bin 0.1.2-1\n")),
            ("aur.archlinux.org", (0, json.dumps(
                {"results": [{"Name": "waydroid-nvidia-bin", "Version": "0.2.0-1"}]}))),
            ("merci-backup/backup.json", (0, json.dumps(
                {"saved_at": 1, "system_datetime": "1775215493", "vendor_datetime": "1", "size": 5}))),
        ])
        report = updates.check(run, ARCH, "0.1.2")
        self.assertEqual([item.key for item in report.updates], ["package", "images"])
        # Пакет waydroid-nvidia идёт первым, и образ об этом говорит.
        self.assertIn("waydroid-nvidia", report.find("images").detail)
        self.assertEqual(report.backup["system_datetime"], "1775215493")
        self.assertGreater(report.checked_at, 0)
        self.assertIn("0.2.0-1", updates.summary(report))

    def test_one_failing_probe_does_not_hide_others(self):
        def run(argv, _timeout):
            line = " ".join(argv)
            if "pacman" in line:
                raise RuntimeError("сломалось")
            if "waydroid.cfg" in line:
                return 0, CFG
            if "/system/" in line:
                return 0, SYSTEM
            if "/vendor/" in line:
                return 0, VENDOR
            return 1, ""

        report = updates.check(run, ARCH, "0.1.2")
        self.assertEqual([item.key for item in report.updates], ["images"])
        self.assertEqual(len(report.notes), 1)


# -- помощник ----------------------------------------------------------------


def load_helper():
    """Помощник как модуль — с отключённым всем, что трогает машину.

    Он написан для работы от root на настоящем Waydroid. В проверках у него
    нет ни прав, ни права что-либо менять: службу контейнера, окна и сигналы
    подменяем сразу при загрузке, а не в каждом тесте по отдельности —
    забытая подмена однажды отправила `systemctl stop` на живую машину.
    """
    path = os.path.join(ROOT, "data", "instances", "merci-instance.py")
    spec = importlib.util.spec_from_file_location("merci_instance_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SERVICE_CALLS = []
    module.container_service = lambda action: module.SERVICE_CALLS.append(action) or False
    module.uninterruptible = lambda: None
    module.known_instances = lambda: [1]
    module.lxc_state = lambda _number: "STOPPED"
    module.stop_container = lambda _number: None

    real_run = module.run

    def guarded(argv, check=True):
        # Копирование файлов во временном каталоге — единственная настоящая
        # команда, которую проверкам можно: всё остальное меняло бы машину.
        if argv and argv[0] == "cp":
            return real_run(argv, check)
        raise AssertionError(f"проверка пытается выполнить настоящую команду: {argv}")

    module.run = guarded
    return module


UPGRADE_OK = """#!/bin/sh
# Как `waydroid upgrade`: новые образы и новые отметки о сборке.
[ "$1" = upgrade ] || exit 2
[ "$2" = "-o" ] && { echo "configs regenerated"; echo offline >> "$W/calls"; exit 0; }
echo new-system > "$W/images/system.img"
echo new-vendor > "$W/images/vendor.img"
sed -i 's/^system_datetime = .*/system_datetime = 1790539062/; s/^vendor_datetime = .*/vendor_datetime = 1792667170/' "$W/waydroid.cfg"
echo "persist.waydroid.use_subsurface=true" > "$W/waydroid_base.prop"
echo upgrade >> "$W/calls"
"""

UPGRADE_BROKEN = """#!/bin/sh
# Обрыв на распаковке: файл образа уже испорчен, отметка о сборке прежняя.
echo half > "$W/images/system.img"
echo "Downloaded system image hash doesn't match" >&2
exit 1
"""


class HelperImagesTest(unittest.TestCase):
    def setUp(self):
        self.h = load_helper()
        self.dir = tempfile.mkdtemp(prefix="merci-test-")
        self.images = os.path.join(self.dir, "images")
        os.makedirs(self.images)
        with open(os.path.join(self.images, "system.img"), "w") as handle:
            handle.write("old-system")
        with open(os.path.join(self.images, "vendor.img"), "w") as handle:
            handle.write("old-vendor")
        cfg = CFG.replace("/var/lib/waydroid/images", self.images).replace(
            "mount_overlays = True", "mount_overlays = False")
        with open(os.path.join(self.dir, "waydroid.cfg"), "w") as handle:
            handle.write(cfg)
        with open(os.path.join(self.dir, "waydroid_base.prop"), "w") as handle:
            handle.write("ro.adb.secure=1\npersist.waydroid.use_subsurface=false\n")
        os.makedirs(os.path.join(self.dir, "overlay_rw"))

        self.h.WAYDROID_DIR = self.dir
        self.h.CFG_PATH = os.path.join(self.dir, "waydroid.cfg")
        self.h.BASE_PROP = os.path.join(self.dir, "waydroid_base.prop")
        self.h.known_instances = lambda: [1]
        self.h.lxc_state = lambda _number: "STOPPED"
        # Настоящий помощник с этого места перестаёт слушать сигналы; в
        # проверках это испортило бы сам прогон.
        self.h.uninterruptible = lambda: None
        os.environ["W"] = self.dir

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        os.environ.pop("W", None)

    def tool(self, text):
        path = os.path.join(self.dir, "waydroid")
        with open(path, "w") as handle:
            handle.write(text)
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        self.h.waydroid_tool = lambda: path

    def args(self, **values):
        base = {"download_mb": 1, "no_backup": False, "width": 0, "height": 0, "subsurface": ""}
        base.update(values)
        return types.SimpleNamespace(**base)

    def read(self, *parts):
        with open(os.path.join(self.dir, *parts)) as handle:
            return handle.read()

    def call(self, function, args):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with redirect_stdout(out), redirect_stderr(err):
            try:
                function(args)
            except SystemExit as stop:
                code = stop.code
        return code, out.getvalue(), err.getvalue()

    def test_update_keeps_a_copy_and_restores_settings(self):
        self.tool(UPGRADE_OK)
        code, out, err = self.call(
            self.h.cmd_update_images, self.args(subsurface="false", width=1600, height=900))
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read("images", "system.img").strip(), "new-system")
        # Прежние образы лежат рядом, с отметками о своих сборках.
        self.assertEqual(self.read("images", "merci-backup", "system.img"), "old-system")
        self.assertEqual(self.read("images", "merci-backup", "vendor.img"), "old-vendor")
        meta = json.loads(self.read("images", "merci-backup", "backup.json"))
        self.assertEqual(meta["system_datetime"], "1775215493")
        # Обновление сбросило подповерхности и оверлей — помощник вернул.
        self.assertIn("persist.waydroid.use_subsurface=false", self.read("waydroid_base.prop"))
        self.assertIn("persist.waydroid.width=1600", self.read("waydroid_base.prop"))
        cfg = self.read("waydroid.cfg")
        self.assertIn("mount_overlays = True", cfg)
        self.assertIn("persist.waydroid.width = 1600", cfg)
        self.assertIn('"updated": ["system.img", "vendor.img"]', out)

    def test_broken_extraction_is_rolled_back(self):
        self.tool(UPGRADE_BROKEN)
        code, _out, err = self.call(self.h.cmd_update_images, self.args())
        self.assertEqual(code, 1)
        self.assertIn("возвращено из копии: system.img", err)
        self.assertEqual(self.read("images", "system.img"), "old-system")
        self.assertEqual(self.read("images", "vendor.img"), "old-vendor")
        self.assertIn("system_datetime = 1775215493", self.read("waydroid.cfg"))

    def test_broken_extraction_without_copy_says_so(self):
        self.tool(UPGRADE_BROKEN)
        code, _out, err = self.call(self.h.cmd_update_images, self.args(no_backup=True))
        self.assertEqual(code, 1)
        self.assertIn("повреждено и не восстановлено: system.img", err)

    def test_no_space_stops_before_anything_changes(self):
        self.tool(UPGRADE_OK)
        code, _out, err = self.call(self.h.cmd_update_images, self.args(download_mb=10 ** 9))
        self.assertEqual(code, 1)
        self.assertIn("мало места", err)
        self.assertEqual(self.read("images", "system.img"), "old-system")
        self.assertFalse(os.path.exists(os.path.join(self.images, "merci-backup")))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "calls")))

    def test_running_window_blocks_the_update(self):
        self.tool(UPGRADE_OK)
        self.h.known_instances = lambda: [1, 2]
        self.h.lxc_state = lambda number: "RUNNING" if number == 2 else "STOPPED"
        self.h.stop_container = lambda _number: None  # «не остановилось»
        code, _out, err = self.call(self.h.cmd_update_images, self.args())
        self.assertEqual(code, 1)
        self.assertIn("окно 2 не остановилось", err)
        self.assertEqual(self.read("images", "system.img"), "old-system")

    def test_restore_brings_images_and_stamps_back(self):
        self.tool(UPGRADE_OK)
        self.call(self.h.cmd_update_images, self.args())
        self.assertIn("system_datetime = 1790539062", self.read("waydroid.cfg"))
        code, out, err = self.call(self.h.cmd_restore_images, self.args())
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read("images", "system.img"), "old-system")
        self.assertEqual(self.read("images", "vendor.img"), "old-vendor")
        cfg = self.read("waydroid.cfg")
        self.assertIn("system_datetime = 1775215493", cfg)
        self.assertIn("vendor_datetime = 1777409843", cfg)
        # Остальное в настройках не тронуто.
        self.assertIn("ro.product.cpu.abilist = x86_64,x86,arm64-v8a", cfg)
        self.assertIn("nvidia_venus_socket = /run/waydroid-venus/venus.sock", cfg)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "overlay_rw")))
        self.assertIn("offline", self.read("calls"))
        self.assertIn('"restored"', out)

    def test_restore_without_copy_refuses(self):
        code, _out, err = self.call(self.h.cmd_restore_images, self.args())
        self.assertEqual(code, 1)
        self.assertIn("резервной копии образов нет", err)

    def test_drop_backup(self):
        self.tool(UPGRADE_OK)
        self.call(self.h.cmd_update_images, self.args())
        code, out, _err = self.call(self.h.cmd_drop_image_backup, self.args())
        self.assertEqual(code, 0)
        self.assertIn('"dropped": true', out)
        self.assertFalse(os.path.exists(os.path.join(self.images, "merci-backup")))

    def test_preinstalled_images_are_refused(self):
        self.h.PREINSTALLED_IMAGES = (self.images,)
        self.tool(UPGRADE_OK)
        code, _out, err = self.call(self.h.cmd_update_images, self.args())
        self.assertEqual(code, 1)
        self.assertIn("пакетом дистрибутива", err)

    def test_set_cfg_values_touches_only_its_section(self):
        self.h.set_cfg_values({"system_datetime": "5", "новый_ключ": "да"})
        cfg = self.read("waydroid.cfg")
        self.assertIn("system_datetime = 5", cfg)
        self.assertIn("новый_ключ = да", cfg)
        self.assertLess(cfg.index("новый_ключ"), cfg.index("[properties]"))
        self.assertIn("ro.adb.secure = 1", cfg)


class HelperNvidiaTest(unittest.TestCase):
    def setUp(self):
        self.h = load_helper()
        self.dir = tempfile.mkdtemp(prefix="merci-test-")
        with open(os.path.join(self.dir, "waydroid.cfg"), "w") as handle:
            handle.write(CFG)
        with open(os.path.join(self.dir, "waydroid_base.prop"), "w") as handle:
            handle.write("persist.waydroid.use_subsurface=false\n")
        self.h.WAYDROID_DIR = self.dir
        self.h.CFG_PATH = os.path.join(self.dir, "waydroid.cfg")
        self.h.BASE_PROP = os.path.join(self.dir, "waydroid_base.prop")
        self.h.known_instances = lambda: [1]
        self.h.lxc_state = lambda _number: "STOPPED"
        self.h.uninterruptible = lambda: None
        self.setup = os.path.join(self.dir, "waydroid-nvidia-setup")
        self.h.NVIDIA_SETUP = self.setup
        os.environ["W"] = self.dir
        # Проверку владельца обходим только здесь: в жизни файл принадлежит root.
        real_stat = os.stat

        def as_root(path, *a, **k):
            info = real_stat(path, *a, **k)
            if path == self.setup:
                return os.stat_result((info.st_mode & ~0o022,) + tuple(info)[1:4] + (0,)
                                      + tuple(info)[5:])
            return info

        self.h.os = types.SimpleNamespace(**{name: getattr(os, name) for name in dir(os)})
        self.h.os.stat = as_root

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        os.environ.pop("W", None)

    def script(self, body):
        with open(self.setup, "w") as handle:
            handle.write(body)
        os.chmod(self.setup, 0o755)

    def args(self, **values):
        base = {"mode": "", "refresh": "", "width": 0, "height": 0, "subsurface": ""}
        base.update(values)
        return types.SimpleNamespace(**base)

    def call(self, args):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with redirect_stdout(out), redirect_stderr(err):
            try:
                self.h.nvidia_setup(args)
            except SystemExit as stop:
                code = stop.code
        return code, out.getvalue(), err.getvalue()

    NEW_SETUP = """#!/bin/sh
# новая настройка: знает --mode; как и настоящая, включает подповерхности
echo "$@" > "$W/args"
sed -i 's/^persist.waydroid.use_subsurface = .*/persist.waydroid.use_subsurface = true/' "$W/waydroid.cfg"
echo "persist.waydroid.use_subsurface=true" > "$W/waydroid_base.prop"
"""

    def test_mode_is_passed_and_subsurface_is_kept_off(self):
        self.script(self.NEW_SETUP)
        code, _out, err = self.call(self.args(mode="venus", refresh="180"))
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.dir, "args")) as handle:
            self.assertEqual(handle.read().strip(), "--mode venus --refresh 180")
        with open(self.h.CFG_PATH) as handle:
            self.assertIn("persist.waydroid.use_subsurface = false", handle.read())
        with open(self.h.BASE_PROP) as handle:
            self.assertIn("persist.waydroid.use_subsurface=false", handle.read())

    def test_old_setup_gets_no_mode(self):
        self.script("#!/bin/sh\necho \"$@\" > \"$W/args\"\n")
        code, _out, err = self.call(self.args(mode="venus", refresh="60"))
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.dir, "args")) as handle:
            self.assertEqual(handle.read().strip(), "--refresh 60")

    def test_old_setup_cannot_switch_mode(self):
        self.script("#!/bin/sh\nexit 0\n")
        code, _out, err = self.call(self.args(mode="nvidia-prolapse"))
        self.assertEqual(code, 1)
        self.assertIn("только режим venus", err)

    def test_bad_arguments_are_refused(self):
        self.script(self.NEW_SETUP)
        for bad in (self.args(mode="venus; reboot"), self.args(refresh="60; reboot"),
                    self.args(refresh="100000")):
            code, _out, _err = self.call(bad)
            self.assertEqual(code, 1)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "args")))

    def test_failing_setup_is_reported(self):
        self.script("#!/bin/sh\necho 'gate failed' >&2\nexit 3\n")
        code, _out, err = self.call(self.args())
        self.assertEqual(code, 1)
        self.assertIn("завершился с ошибкой", err)



class HelperConfigTest(unittest.TestCase):
    """Настройки окна снимаются с настроек основного контейнера. Если
    Waydroid начнёт писать их иначе, помощник обязан отказаться, а не
    поднять окно на данных и устройствах основного."""

    NODES = (
        "lxc.mount.entry = /dev/binder dev/binder none bind,create=file,optional 0 0\n"
        "lxc.mount.entry = /dev/vndbinder dev/vndbinder none bind,create=file,optional 0 0\n"
        "lxc.mount.entry = /dev/hwbinder dev/hwbinder none bind,create=file,optional 0 0\n"
    )

    def setUp(self):
        self.h = load_helper()
        self.dir = tempfile.mkdtemp(prefix="merci-test-")
        self.home = os.path.join(self.dir, "home")
        self.lxc = os.path.join(self.dir, "lxc")
        self.base = os.path.join(self.lxc, "waydroid")
        os.makedirs(self.base)
        os.makedirs(self.home)
        self.h.LXC_PATH = self.lxc
        self.write("config", f"lxc.uts.name = waydroid\nlxc.include = {self.lxc}/waydroid/config_nodes\n"
                             f"lxc.net.0.hwaddr = {self.h.mac_for(1)}\n"
                             "lxc.hook.post-stop = /usr/lib/waydroid/data/scripts/waydroid-post-stop.sh\n")
        self.write("config_session",
                   f"lxc.mount.entry = {self.h.data_dir(self.home, 1)} data none rbind 0 0\n")
        self.write("config_nodes", self.NODES)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, text):
        with open(os.path.join(self.base, name), "w") as handle:
            handle.write(text)

    def make(self):
        err = io.StringIO()
        with redirect_stderr(err):
            try:
                return self.h.make_config(2, self.home), err.getvalue()
            except SystemExit:
                return None, err.getvalue()

    def read(self, name):
        with open(os.path.join(self.lxc, "waydroid2", name)) as handle:
            return handle.read()

    def test_clone_gets_its_own_paths(self):
        target, err = self.make()
        self.assertTrue(target, err)
        self.assertIn("lxc.uts.name = waydroid2", self.read("config"))
        self.assertIn(self.h.mac_for(2), self.read("config"))
        self.assertNotIn("post-stop", self.read("config"))
        self.assertIn(self.h.data_dir(self.home, 2), self.read("config_session"))
        self.assertNotIn("= /dev/binder ", self.read("config_nodes"))
        self.assertIn(f"{self.h.binderfs_dir(2)}/hwbinder ", self.read("config_nodes"))

    def test_missing_file_is_refused(self):
        os.unlink(os.path.join(self.base, "config_session"))
        target, err = self.make()
        self.assertIsNone(target)
        self.assertIn("нет файла config_session", err)
        self.assertFalse(os.path.exists(os.path.join(self.lxc, "waydroid2")))

    def test_other_binder_layout_is_refused(self):
        # Waydroid стал подключать устройства из другого места: подстановка
        # ничего не заменит — окно делило бы binder с основным.
        self.write("config_nodes", self.NODES.replace("/dev/", "/dev/binderfs/"))
        target, err = self.make()
        self.assertIsNone(target)
        self.assertIn("устройства binder", err)
        self.assertFalse(os.path.exists(os.path.join(self.lxc, "waydroid2")))

    def test_other_data_layout_is_refused(self):
        self.write("config_session", "lxc.mount.entry = /somewhere/else data none rbind 0 0\n")
        target, err = self.make()
        self.assertIsNone(target)
        self.assertIn("данные", err)



class HelperLockTest(unittest.TestCase):
    """Один помощник на одно дело: обновлению нужен весь Waydroid, окна
    друг другу не мешают."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="merci-test-")
        self.first, self.second = load_helper(), load_helper()
        for helper in (self.first, self.second):
            helper.LOCK_PATH = os.path.join(self.dir, "lock")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def refused(self, helper, command):
        err = io.StringIO()
        with redirect_stderr(err):
            try:
                handle = helper.take_lock(command)
            except SystemExit:
                return True
        if handle is not None:
            handle.close()
        return False

    def test_update_excludes_everything(self):
        held = self.first.take_lock("update-images")
        try:
            self.assertTrue(self.refused(self.second, "start"))
            self.assertTrue(self.refused(self.second, "update-images"))
            self.assertTrue(self.refused(self.second, "prepare"))
            self.assertFalse(self.refused(self.second, "list"))
        finally:
            held.close()
        self.assertFalse(self.refused(self.second, "update-images"))

    def test_windows_do_not_block_each_other_but_block_updates(self):
        held = self.first.take_lock("start")
        try:
            self.assertFalse(self.refused(self.second, "restart"))
            self.assertTrue(self.refused(self.second, "nvidia-update"))
        finally:
            held.close()



class HelperStreamTest(unittest.TestCase):
    """Вывод команд идёт через помощника: оборванный канал не убивает
    команду, а зависшую команду помощник останавливает сам."""

    def setUp(self):
        self.h = load_helper()

    def test_output_is_forwarded_and_code_returned(self):
        out = io.StringIO()
        out.buffer = io.BytesIO()
        self.h.sys = types.SimpleNamespace(stdout=self.h.Tolerant(out), stderr=sys.stderr)
        code = self.h.stream(["sh", "-c", "printf 'раз\\rдва\\n'; exit 7"])
        self.assertEqual(code, 7)
        self.assertEqual(out.buffer.getvalue().decode(), "раз\rдва\n")

    def test_closed_reader_does_not_stop_the_command(self):
        class Closed:
            buffer = None

            def write(self, _text):
                raise BrokenPipeError

            def flush(self):
                raise BrokenPipeError

        marker = os.path.join(tempfile.mkdtemp(prefix="merci-test-"), "done")
        self.h.sys = types.SimpleNamespace(stdout=self.h.Tolerant(Closed()), stderr=sys.stderr)
        code = self.h.stream(["sh", "-c", f"echo one; echo two; echo three; : > {marker}"])
        self.assertEqual(code, 0)
        self.assertTrue(os.path.exists(marker))
        shutil.rmtree(os.path.dirname(marker), ignore_errors=True)

    def test_silent_command_is_stopped(self):
        out = io.StringIO()
        out.buffer = io.BytesIO()
        self.h.sys = types.SimpleNamespace(stdout=self.h.Tolerant(out), stderr=sys.stderr)
        self.h.select = types.SimpleNamespace(select=lambda r, w, x, t: ([], [], []))
        clock = iter(range(0, 100000, 400))
        self.h.time = types.SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda _s: None)
        code = self.h.stream(["sleep", "30"], silence=600)
        self.assertEqual(code, 124)



UPGRADE_PARTIAL = """#!/bin/sh
# Система обновилась, загрузка драйверов оборвалась: как у настоящего
# `waydroid upgrade`, записи поверх образа и настройки остались прежними.
[ "$2" = "-o" ] && { echo offline >> "$W/calls"; exit 0; }
echo new-system > "$W/images/system.img"
sed -i 's/^system_datetime = .*/system_datetime = 1790539062/' "$W/waydroid.cfg"
echo "Failed to get vendor OTA channel" >&2
exit 1
"""


class HelperPartialTest(HelperImagesTest):
    def test_partial_upgrade_is_brought_to_a_consistent_state(self):
        self.tool(UPGRADE_PARTIAL)
        code, _out, err = self.call(self.h.cmd_update_images, self.args())
        self.assertEqual(code, 1)
        self.assertIn("обновлено: system.img", err)
        # Новая система — значит, записи поверх прежней стёрты, а настройки
        # контейнера пересобраны: Waydroid сам до этого не дошёл.
        self.assertFalse(os.path.exists(os.path.join(self.dir, "overlay_rw")))
        self.assertIn("offline", self.read("calls"))
        self.assertEqual(self.read("images", "vendor.img"), "old-vendor")

    def test_max_users_is_written_to_both_files(self):
        self.tool(UPGRADE_OK)
        code, _out, err = self.call(self.h.cmd_update_images, self.args(max_users=4))
        self.assertEqual(code, 0, err)
        self.assertIn("fw.max_users=4", self.read("waydroid_base.prop"))
        self.assertIn("fw.max_users = 4", self.read("waydroid.cfg"))

    def test_strange_user_limit_is_refused(self):
        self.tool(UPGRADE_OK)
        code, _out, _err = self.call(self.h.cmd_update_images, self.args(max_users=500))
        self.assertEqual(code, 1)

    def test_container_service_is_never_touched_without_root(self):
        # Настоящая функция без прав root обязана молча отказаться: иначе
        # systemctl пошёл бы спрашивать пароль через polkit.
        fresh = importlib.util.spec_from_file_location(
            "merci_instance_plain", os.path.join(ROOT, "data", "instances", "merci-instance.py"))
        module = importlib.util.module_from_spec(fresh)
        fresh.loader.exec_module(module)
        if os.geteuid() == 0:
            self.skipTest("проверка имеет смысл только без прав root")
        called = []
        module.run = lambda argv, check=True: called.append(argv)
        self.assertFalse(module.container_service("stop"))
        self.assertEqual(called, [])


class HelperNvidiaFailureTest(HelperNvidiaTest):
    FAILING_SETUP = """#!/bin/sh
# как настоящая настройка: сначала пишет свойства, потом может упасть
sed -i 's/^persist.waydroid.use_subsurface = .*/persist.waydroid.use_subsurface = true/' "$W/waydroid.cfg"
echo "persist.waydroid.use_subsurface=true" > "$W/waydroid_base.prop"
echo "gate failed" >&2
exit 3
"""

    def test_failed_setup_still_restores_our_settings_and_the_service(self):
        self.script(self.FAILING_SETUP)
        code, _out, err = self.call(self.args(subsurface="false"))
        self.assertEqual(code, 1)
        self.assertIn("завершился с ошибкой", err)
        with open(self.h.CFG_PATH) as handle:
            self.assertIn("persist.waydroid.use_subsurface = false", handle.read())
        with open(self.h.BASE_PROP) as handle:
            self.assertIn("persist.waydroid.use_subsurface=false", handle.read())
        self.assertIn("start", self.h.SERVICE_CALLS)


class HelperPackageTest(unittest.TestCase):
    """Пакет ставится только тот, что собрала Merci: по имени и по сумме."""

    def setUp(self):
        self.h = load_helper()
        self.home = tempfile.mkdtemp(prefix="merci-test-home-")
        self.package = os.path.join(self.home, "merci-update.pkg.tar.zst")
        with open(self.package, "wb") as handle:
            handle.write(b"package-bytes")
        import hashlib

        self.digest = hashlib.sha256(b"package-bytes").hexdigest()
        self.installed = []
        self.name = "waydroid-nvidia-bin"

        def fake_run(argv, check=True):
            if argv[:2] == ["pacman", "-Qqp"]:
                return types.SimpleNamespace(returncode=0, stdout=self.name + "\n", stderr="")
            if argv[:2] == ["pacman", "-Qq"]:
                return types.SimpleNamespace(returncode=0, stdout="lxc\nwaydroid\n", stderr="")
            raise AssertionError(f"неожиданная команда: {argv}")

        self.h.run = fake_run
        self.h.stream = lambda argv, silence=0: self.installed.append(argv) or 0
        self.h.nvidia_setup = lambda args: self.installed.append(["setup", args.mode])
        self.h.tempfile = types.SimpleNamespace(
            mkdtemp=lambda prefix="", dir=None: tempfile.mkdtemp(prefix="merci-test-pkg-"))

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def call(self, **values):
        base = {"package": self.package, "sha256": self.digest, "home": self.home,
                "mode": "venus", "refresh": "", "width": 0, "height": 0, "subsurface": "",
                "max_users": 0}
        base.update(values)
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), redirect_stderr(err):
            try:
                self.h.cmd_nvidia_update(types.SimpleNamespace(**base))
            except SystemExit as stop:
                return stop.code, err.getvalue()
        return 0, err.getvalue()

    def test_package_then_setup(self):
        code, err = self.call()
        self.assertEqual(code, 0, err)
        install, setup = self.installed
        self.assertEqual(install[:3], ["pacman", "-U", "--noconfirm"])
        # Обычный waydroid установлен — замена идёт одной операцией.
        self.assertIn("--ask", install)
        self.assertNotIn(self.package, install)  # ставится своя копия, не файл пользователя
        self.assertEqual(setup, ["setup", "venus"])

    def test_changed_file_is_refused(self):
        with open(self.package, "wb") as handle:
            handle.write(b"something else")
        code, err = self.call()
        self.assertEqual(code, 1)
        self.assertIn("изменился после сборки", err)
        self.assertEqual(self.installed, [])

    def test_other_package_is_refused(self):
        self.name = "sudo"
        code, err = self.call()
        self.assertEqual(code, 1)
        self.assertIn("ждали waydroid-nvidia", err)
        self.assertEqual(self.installed, [])

    def test_file_outside_home_is_refused(self):
        outside = tempfile.mkdtemp(prefix="merci-test-other-")
        path = os.path.join(outside, "x.pkg.tar.zst")
        open(path, "wb").close()
        code, err = self.call(package=path)
        shutil.rmtree(outside, ignore_errors=True)
        self.assertEqual(code, 1)
        self.assertIn("домашнем каталоге", err)

    def test_symlink_is_refused(self):
        link = os.path.join(self.home, "link.pkg.tar.zst")
        os.symlink("/etc/passwd", link)
        code, _err = self.call(package=link)
        self.assertEqual(code, 1)
        self.assertEqual(self.installed, [])


try:
    import gi  # noqa: F401

    HAVE_GI = True
except ImportError:
    HAVE_GI = False


@unittest.skipUnless(HAVE_GI, "нужен PyGObject")
class StepScriptsTest(unittest.TestCase):
    """Шаги обновления — это сценарии оболочки: хотя бы разбираться должны."""

    def setUp(self):
        from merci import waydroid

        self.w = waydroid
        self.saved = {name: getattr(waydroid, name) for name in (
            "host", "host_known", "askpass_helper", "waydroid_cfg", "_helper_digest",
            "_instance_helper", "status", "custom_channels", "nvidia_setup_needed",
            "overlays_disabled", "firewall_blocks_container", "gamescope_available",
            "kernel_has_binder", "adb_available", "_ok", "session_running")}
        host = hostinfo.Host(family="arch", init="systemd", session="wayland",
                             tools=frozenset({"sudo"}))
        waydroid.host = lambda refresh=False: host
        waydroid.host_known = lambda: host
        waydroid.askpass_helper = lambda: ""
        waydroid.waydroid_cfg = lambda: updates.parse_cfg(CFG)
        waydroid._helper_digest = lambda: "0" * 64
        waydroid._instance_helper = lambda: "/tmp/helper.py"

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(self.w, name, value)

    def check(self, step):
        import subprocess

        script = step.argv[-1]
        result = subprocess.run(["sh", "-n", "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(step.asks_root and step.then_session)
        # Слова команды запуска сессии не должны стоять в тексте шага подряд:
        # иначе pkill по ним нашёл бы оболочку самого шага.
        self.assertNotIn("waydroid session start", script.replace("'^[^ ]*python[^ ]* [^ ]*waydroid[^ ]* session start'", ""))
        return script

    def test_all_update_steps_parse(self):
        images = updates.Update(key="images", title="x", data={"download_mb": 989})
        script = self.check(self.w.images_update_step(images))
        self.assertIn("'update-images' '--download-mb' '989'", script.replace("'\\''", "'"))
        self.check(self.w.images_restore_step())
        self.check(self.w.nvidia_setup_step())
        package = updates.Update(key="package", title="x",
                                 data={"name": "waydroid-nvidia-bin", "source": "aur", "nvidia": True})
        script = self.check(self.w.nvidia_update_step(package))
        # Режим называется явно, сумма пакета уходит в командную строку root.
        self.assertIn("--mode", script)
        self.assertIn('"$SUM"', script)
        self.check(self.w.package_upgrade_step(
            updates.Update(key="package", title="x", data={"source": "apt"})))

    def test_half_downloaded_source_is_fetched_again(self):
        """Файл от оборванной загрузки не должен ломать все следующие сборки."""
        import subprocess

        work = tempfile.mkdtemp(prefix="merci-test-aur-")
        fakes = {
            # makepkg: один http-источник; проверка сумм проходит только у целого файла
            "makepkg": '#!/bin/sh\n'
                       'case "$1" in\n'
                       '  --printsrcinfo) echo "\tsource = big.tar.zst::https://example.invalid/big.tar.zst";;\n'
                       '  --verifysource) [ "$(cat big.tar.zst 2>/dev/null)" = whole ];;\n'
                       'esac\n',
            # curl: пишет целый файл и считает вызовы
            "curl": '#!/bin/sh\n'
                    'echo call >> curl.calls\n'
                    'while [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done\n'
                    'printf whole > "$out"\n',
        }
        for name, text in fakes.items():
            path = os.path.join(work, name)
            with open(path, "w") as handle:
                handle.write(text)
            os.chmod(path, 0o755)
        with open(os.path.join(work, "big.tar.zst"), "w") as handle:
            handle.write("half")
        env = dict(os.environ, PATH=work + ":/usr/bin:/bin")
        result = subprocess.run(
            ["sh", "-c", "set -e; " + self.w._AUR_PREFETCH + "; echo PREFETCH-OK"],
            cwd=work, env=env, capture_output=True, text=True,
        )
        try:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("PREFETCH-OK", result.stdout)
            with open(os.path.join(work, "big.tar.zst")) as handle:
                self.assertEqual(handle.read(), "whole")
            with open(os.path.join(work, "curl.calls")) as handle:
                self.assertEqual(handle.read().count("call"), 1)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_strange_package_name_is_refused(self):
        bad = updates.Update(key="package", title="x",
                             data={"name": "waydroid-nvidia; rm -rf ~", "source": "aur", "nvidia": True})
        with self.assertRaises(self.w.WaydroidError):
            self.w.nvidia_update_step(bad)

    def test_copied_command_is_quoted(self):
        step = self.w.images_restore_step()
        self.assertTrue(step.command_line().startswith("sh -c '"))

    def test_plan_without_image_does_not_crash(self):
        w = self.w
        w.adb_available = lambda: True
        w.session_running = lambda: False
        w.kernel_has_binder = lambda: True
        w._ok = lambda argv, *a, **k: True
        w.status = lambda use_cache=True: (False, "образ Android не загружен")
        w.custom_channels = lambda: ("", "")
        w.nvidia_setup_needed = lambda: False
        w.overlays_disabled = lambda: False
        w.firewall_blocks_container = lambda: False
        w.gamescope_available = lambda: True
        keys = [step.key for step in w.plan(False)]
        self.assertEqual(keys[:3], ["network", "init", "session"])


if __name__ == "__main__":
    unittest.main()
