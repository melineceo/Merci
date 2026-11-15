"""Проверки установки и удаления root (Magisk).

Запуск из корня проекта:  python3 -m unittest discover -s tests -v

Помощник работает во временном каталоге вместо /var/lib/waydroid и с
поддельным домом; вместо настоящего Magisk — маленький архив той же
раскладки. Ничего на машине проверки не трогают и прав не просят: смена
владельца, которую помощник делает от root, здесь только записывается.
"""

import gzip
import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_updates import CFG, HAVE_GI, load_helper  # noqa: E402

from merci import hostinfo, updates  # noqa: E402


def fake_apk(extra=None, skip=()):
    """Архив с раскладкой Magisk Delta: библиотеки четырёх архитектур,
    сценарии и лишнее, чему в системе делать нечего."""
    files = {
        "AndroidManifest.xml": b"manifest",
        "classes.dex": b"dex",
        "assets/addon.d.sh": b"#!/sbin/sh\naddon\n",
        "assets/boot_patch.sh": b"#!/system/bin/sh\npatch\n",
        "assets/stub.apk": b"stub",
        "assets/util_functions.sh": b"util\n",
        "assets/uninstaller.sh": b"uninstall\n",
        "assets/chromeos/futility": b"futility",
        "assets/chromeos/kernel.keyblock": b"keyblock",
    }
    for abi in ("x86_64", "arm64-v8a"):
        for name in ("busybox", "magisk64", "magiskboot", "magiskinit", "magiskpolicy"):
            files[f"lib/{abi}/lib{name}.so"] = f"{abi}:{name}".encode()
    files["lib/x86/libmagisk32.so"] = b"x86:magisk32"
    files.update(extra or {})
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as archive:
        for name, data in files.items():
            if name not in skip:
                archive.writestr(name, data)
    return blob.getvalue()


class HelperMagiskTest(unittest.TestCase):
    def setUp(self):
        self.h = load_helper()
        self.dir = tempfile.mkdtemp(prefix="merci-test-")
        self.home = tempfile.mkdtemp(prefix="merci-test-home-")
        with open(os.path.join(self.dir, "waydroid.cfg"), "w") as handle:
            handle.write(CFG)
        self.h.WAYDROID_DIR = self.dir
        self.h.CFG_PATH = os.path.join(self.dir, "waydroid.cfg")
        self.init = os.path.join(self.dir, "overlay", "system", "etc", "init")
        os.makedirs(self.init)
        os.makedirs(os.path.join(self.dir, "overlay_rw", "system"))
        self.data = os.path.join(self.home, ".local/share/waydroid/data")
        os.makedirs(self.data)

        self.blob = fake_apk()
        self.apk = os.path.join(self.home, "magisk.apk")
        with open(self.apk, "wb") as handle:
            handle.write(self.blob)
        self.h.MAGISK_APKS = {hashlib.sha256(self.blob).hexdigest(): "Magisk для проверки"}
        # Кто позвал помощника: в жизни это выясняет check_home.
        self.h.CALLER_UID = os.getuid()
        # Настоящая проверка образа читает /var/lib/waydroid — здесь её нет.
        self.image_check = self.h.image_has_magisk
        self.h.image_has_magisk = lambda: False

        # Владельца помощник меняет от root; здесь только запоминаем, кому
        # что досталось.
        self.owners = {}
        patches = [
            mock.patch.object(self.h.os, "fchown", self.fchown),
            mock.patch.object(self.h.os, "chown", self.chown),
            mock.patch.object(self.h.os, "uname",
                              lambda: types.SimpleNamespace(machine="x86_64")),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def fchown(self, descriptor, uid, gid):
        self.owners[os.readlink(f"/proc/self/fd/{descriptor}")] = (uid, gid)

    def chown(self, path, uid, gid):
        self.owners[path] = (uid, gid)

    def call(self, function, **values):
        base = {"home": self.home, "apk": self.apk}
        base.update(values)
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with redirect_stdout(out), redirect_stderr(err):
            try:
                function(types.SimpleNamespace(**base))
            except SystemExit as stop:
                code = stop.code
        return code, out.getvalue(), err.getvalue()

    def system(self, *parts):
        return os.path.join(self.init, *parts)

    def read(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    # -- установка ----------------------------------------------------------

    def test_install_lays_out_system_and_data(self):
        code, out, err = self.call(self.h.cmd_magisk_install)
        self.assertEqual(code, 0, err)
        answer = json.loads(out.splitlines()[-1])
        self.assertEqual(answer["magisk"], "installed")
        self.assertTrue(answer["data"])

        # Библиотеки своей архитектуры стали исполняемыми файлами.
        for name in ("busybox", "magisk64", "magiskboot", "magiskinit", "magiskpolicy"):
            path = self.system("magisk", name)
            self.assertEqual(self.read(path), f"x86_64:{name}".encode())
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o755)
        self.assertFalse(os.path.exists(self.system("magisk", "magisk32")))
        self.assertEqual(self.read(self.system("magisk", "magisk.apk")), self.blob)
        self.assertEqual(self.read(self.system("magisk", "chromeos", "futility")), b"futility")
        for name in ("addon.d.sh", "boot_patch.sh", "stub.apk", "util_functions.sh"):
            self.assertTrue(os.path.isfile(self.system("magisk", name)), name)
        # Чего в системе быть не должно: прочее содержимое APK.
        self.assertFalse(os.path.exists(self.system("magisk", "uninstaller.sh")))
        self.assertFalse(os.path.exists(self.system("magisk", "classes.dex")))
        self.assertTrue(os.path.isdir(os.path.join(self.dir, "overlay", "sbin")))

        # Строки запуска и «исходный» bootanim.rc рядом.
        rc = self.read(self.system("bootanim.rc")).decode()
        self.assertIn("service bootanim /system/bin/bootanimation", rc)
        self.assertIn("/system/etc/init/magisk/magisk64 --auto-selinux --setup-sbin", rc)
        self.assertEqual(rc.count("on post-fs-data"), 1)
        original = gzip.decompress(self.read(self.system("bootanim.rc.gz"))).decode()
        self.assertIn("service bootanim", original)
        self.assertNotIn("magisk", original)

        # В данных Android — исполняемые (иначе Zygisk молча не стартует).
        for name in ("magisk64", "busybox", "util_functions.sh", "chromeos/futility"):
            path = os.path.join(self.data, "adb", "magisk", name)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o755, name)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.data, "adb")).st_mode), 0o700)

        # Владельцы: исполняемые в системе — root:shell, остальное — root.
        self.assertEqual(self.owners[self.system("magisk", "chromeos")], (0, 2000))
        self.assertEqual(
            self.owners[os.path.realpath(self.system("magisk", "magisk64.merci-new"))], (0, 2000))
        self.assertEqual(
            self.owners[os.path.realpath(self.system("bootanim.rc.merci-new"))], (0, 0))
        self.assertEqual(
            self.owners[os.path.realpath(os.path.join(self.data, "adb", "magisk", "magisk64"))],
            (0, 0))

    def test_unknown_file_is_refused_before_anything_is_touched(self):
        with open(self.apk, "wb") as handle:
            handle.write(fake_apk(extra={"assets/extra": b"x"}))
        stopped = []
        self.h.stop_container = lambda number: stopped.append(number)
        code, _out, err = self.call(self.h.cmd_magisk_install)
        self.assertEqual(code, 1)
        self.assertIn("не та сборка Magisk", err)
        self.assertEqual(os.listdir(self.init), [])
        self.assertFalse(os.path.exists(os.path.join(self.data, "adb")))
        self.assertEqual(self.h.SERVICE_CALLS, [])

    def test_real_build_is_the_only_one_trusted_by_default(self):
        fresh = load_helper()
        self.assertEqual(
            list(fresh.MAGISK_APKS),
            ["a3c37c4172b6ce7fdfbcccb2a608ef84b0a267d7aaef7c183047adceb8494c80"],
        )

    def test_only_the_callers_own_regular_file_is_read(self):
        link = os.path.join(self.home, "link.apk")
        os.symlink(self.apk, link)
        self.assertEqual(self.call(self.h.cmd_magisk_install, apk=link)[0], 1)  # ссылка
        code, _out, err = self.call(self.h.cmd_magisk_install, apk=self.home)  # каталог
        self.assertEqual(code, 1)
        self.assertIn("не обычный файл", err)
        self.assertEqual(os.listdir(self.init), [])
        # Свой файл может лежать где угодно: данные Flatpak нередко выносят
        # на другой диск, и ~/.var тогда ведёт за пределы дома.
        outside = tempfile.mkdtemp(prefix="merci-test-other-")
        self.addCleanup(shutil.rmtree, outside, True)
        other = os.path.join(outside, "magisk.apk")
        shutil.copy(self.apk, other)
        self.assertEqual(self.call(self.h.cmd_magisk_install, apk=other)[0], 0)
        # А чужой файл под root не открываем, что бы в нём ни лежало.
        if os.getuid() != 0:
            self.h.CALLER_UID = os.getuid() + 1
            code, _out, err = self.call(self.h.cmd_magisk_install)
            self.assertEqual(code, 1)
            self.assertIn("другому пользователю", err)

    def test_incomplete_archive_is_refused(self):
        blob = fake_apk(skip=("lib/x86_64/libmagisk64.so",))
        with open(self.apk, "wb") as handle:
            handle.write(blob)
        self.h.MAGISK_APKS = {hashlib.sha256(blob).hexdigest(): "неполный"}
        code, _out, err = self.call(self.h.cmd_magisk_install)
        self.assertEqual(code, 1)
        self.assertIn("magisk64", err)
        self.assertEqual(os.listdir(self.init), [])

    def test_other_architecture_is_refused(self):
        with mock.patch.object(self.h.os, "uname",
                               lambda: types.SimpleNamespace(machine="i686")):
            code, _out, err = self.call(self.h.cmd_magisk_install)
        self.assertEqual(code, 1)
        self.assertIn("64-разрядных", err)

    def test_interrupted_install_leaves_magisk_switched_off(self):
        """Сорвалась запись посреди установки — строк запуска быть не должно:
        Android обязан грузиться как раньше."""
        self.assertEqual(self.call(self.h.cmd_magisk_install)[0], 0)
        real = self.h._install_file
        calls = []

        def failing(dest, data, mode, gid=0):
            calls.append(dest)
            if len(calls) == 3:
                raise OSError(28, "No space left on device")
            real(dest, data, mode, gid)

        self.h._install_file = failing
        code, _out, err = self.call(self.h.cmd_magisk_install)
        self.assertEqual(code, 1)
        self.assertIn("root не установлен", err)
        self.assertIn("No space left", err)
        # Прежняя установка снята первой, а новая не включена и подчищена.
        self.assertEqual(os.listdir(self.init), [])

    def test_reinstall_replaces_stale_files_and_upper_leftovers(self):
        os.makedirs(self.system("magisk"))
        with open(self.system("magisk", "old-binary"), "w") as handle:
            handle.write("old")
        upper = os.path.join(self.dir, "overlay_rw", "system", "system", "etc", "init")
        os.makedirs(os.path.join(upper, "magisk"))
        with open(os.path.join(upper, "bootanim.rc"), "w") as handle:
            handle.write("patched by the app")
        os.makedirs(os.path.join(self.data, "adb", "magisk"))
        with open(os.path.join(self.data, "adb", "magisk", "stale"), "w") as handle:
            handle.write("stale")
        with open(os.path.join(self.data, "adb", "magisk.db"), "w") as handle:
            handle.write("grants")

        self.assertEqual(self.call(self.h.cmd_magisk_install)[0], 0)
        self.assertFalse(os.path.exists(self.system("magisk", "old-binary")))
        self.assertEqual(os.listdir(upper), [])
        self.assertFalse(os.path.exists(os.path.join(self.data, "adb", "magisk", "stale")))
        # Выданные разрешения root переустановка не стирает.
        self.assertTrue(os.path.exists(os.path.join(self.data, "adb", "magisk.db")))

    def test_half_copied_data_is_removed(self):
        """Сорвалась раскладка в /data/adb/magisk — половину не оставляем:
        без каталога Magisk сам попросит «дополнительную настройку»."""
        real = self.h.write_at
        calls = []

        def failing(directory, name, data, mode):
            calls.append(name)
            if len(calls) == 3:
                raise OSError(28, "No space left on device")
            real(directory, name, data, mode)

        self.h.write_at = failing
        code, out, err = self.call(self.h.cmd_magisk_install)
        self.assertEqual(code, 0, err)
        self.assertFalse(json.loads(out.splitlines()[-1])["data"])
        self.assertIn("дополнительную настройку", out)
        self.assertFalse(os.path.exists(os.path.join(self.data, "adb", "magisk")))
        self.assertTrue(os.path.exists(self.system("bootanim.rc")))

    def test_data_moved_outside_home_is_left_alone(self):
        """Данные Android на другом диске (ссылкой): /data/adb там принадлежит
        root, и от чужого каталога root его не отличить — не трогаем."""
        outside = tempfile.mkdtemp(prefix="merci-test-disk-")
        self.addCleanup(shutil.rmtree, outside, True)
        shutil.rmtree(os.path.join(self.home, ".local/share/waydroid"))
        os.makedirs(os.path.join(outside, "data", "adb", "magisk"))
        with open(os.path.join(outside, "data", "adb", "magisk.db"), "w") as handle:
            handle.write("grants")
        os.symlink(outside, os.path.join(self.home, ".local/share/waydroid"))

        code, out, err = self.call(self.h.cmd_magisk_remove)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out.splitlines()[-1])["data"], 0)
        self.assertTrue(os.path.exists(os.path.join(outside, "data", "adb", "magisk.db")))

        code, out, err = self.call(self.h.cmd_magisk_install)
        self.assertEqual(code, 0, err)
        self.assertFalse(json.loads(out.splitlines()[-1])["data"])
        self.assertEqual(os.listdir(os.path.join(outside, "data", "adb", "magisk")), [])

    def test_install_without_android_data_skips_the_data_part(self):
        shutil.rmtree(self.data)
        code, out, err = self.call(self.h.cmd_magisk_install)
        self.assertEqual(code, 0, err)
        self.assertFalse(json.loads(out.splitlines()[-1])["data"])
        self.assertFalse(os.path.exists(self.data))
        self.assertTrue(os.path.exists(self.system("bootanim.rc")))

    def test_install_turns_overlays_back_on(self):
        with open(self.h.CFG_PATH, "w") as handle:
            handle.write(CFG.replace("mount_overlays = True", "mount_overlays = False"))
        self.assertEqual(self.call(self.h.cmd_magisk_install)[0], 0)
        with open(self.h.CFG_PATH) as handle:
            self.assertIn("mount_overlays = True", handle.read())

    def test_running_window_blocks_the_change(self):
        self.h.known_instances = lambda: [1, 2]
        self.h.lxc_state = lambda number: "RUNNING" if number == 2 else "STOPPED"
        code, _out, err = self.call(self.h.cmd_magisk_install)
        self.assertEqual(code, 1)
        self.assertIn("не остановилось", err)
        self.assertEqual(os.listdir(self.init), [])

    # -- удаление -----------------------------------------------------------

    def test_remove_takes_everything_out(self):
        self.assertEqual(self.call(self.h.cmd_magisk_install)[0], 0)
        with open(os.path.join(self.data, "adb", "magisk.db"), "w") as handle:
            handle.write("grants")
        os.makedirs(os.path.join(self.data, "adb", "modules", "some"))
        # Другим файлам оверлея — например, транслятору — ничего не грозит.
        with open(self.system("houdini.rc"), "w") as handle:
            handle.write("houdini")
        upper = os.path.join(self.dir, "overlay_rw")
        os.makedirs(os.path.join(upper, "system", "system", "addon.d"))
        with open(os.path.join(upper, "system", "system", "addon.d", "99-magisk.sh"), "w") as handle:
            handle.write("addon")
        os.makedirs(os.path.join(upper, "vendor", "etc", "selinux"))
        with open(os.path.join(upper, "vendor", "etc", "selinux", "precompiled_sepolicy"), "w") as handle:
            handle.write("policy")

        code, out, err = self.call(self.h.cmd_magisk_remove)
        self.assertEqual(code, 0, err)
        self.assertIn("root убран", out)
        self.assertEqual(json.loads(out.splitlines()[-1])["magisk"], "removed")
        self.assertEqual(os.listdir(self.init), ["houdini.rc"])
        self.assertFalse(os.path.exists(os.path.join(self.dir, "overlay", "sbin")))
        self.assertEqual(os.listdir(os.path.join(upper, "system", "system", "addon.d")), [])
        self.assertEqual(os.listdir(os.path.join(upper, "vendor", "etc", "selinux")), [])
        # Модули человека остаются: вернёт root — вернутся и они.
        self.assertEqual(os.listdir(os.path.join(self.data, "adb")), ["modules"])

    def test_remove_is_safe_to_repeat_and_cleans_old_leftovers(self):
        """То, что осталось на машине после прежнего удаления скриптом:
        сжатый bootanim.rc и пустой /sbin."""
        with open(self.system("bootanim.rc.gz"), "wb") as handle:
            handle.write(gzip.compress(b"original"))
        os.makedirs(os.path.join(self.dir, "overlay", "sbin"))
        code, out, _err = self.call(self.h.cmd_magisk_remove)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.splitlines()[-1])["system"], 2)
        self.assertEqual(os.listdir(self.init), [])

        code, out, _err = self.call(self.h.cmd_magisk_remove)
        self.assertEqual(code, 0)
        self.assertIn("убирать нечего", out)
        self.assertEqual(json.loads(out.splitlines()[-1]), {"magisk": "removed", "system": 0, "data": 0})

    def test_magisk_inside_the_image_is_reported_not_hidden(self):
        """Magisk, записанный в сам образ (при выключенных оверлеях), файлами
        оверлея не убрать — и говорить «root убран» было бы неправдой."""
        self.assertEqual(self.call(self.h.cmd_magisk_install)[0], 0)
        self.h.image_has_magisk = lambda: True
        code, out, err = self.call(self.h.cmd_magisk_remove)
        self.assertEqual(code, 1)
        self.assertIn("в сам образ Android", err)
        self.assertNotIn("root убран", out)
        self.assertEqual(os.listdir(self.init), [])  # что могли — убрали

    def test_image_is_inspected_without_mounting(self):
        images = os.path.join(self.dir, "images")
        os.makedirs(images)
        open(os.path.join(images, "system.img"), "w").close()
        with open(self.h.CFG_PATH, "w") as handle:
            handle.write(CFG.replace("/var/lib/waydroid/images", images))
        calls = []
        answer = {"stdout": "Inode: 1665   Type: directory    Mode:  0755\n"}

        def fake_run(argv, check=True):
            calls.append(argv)
            return types.SimpleNamespace(returncode=0, stdout=answer["stdout"], stderr="")

        self.h.run = fake_run
        with mock.patch.object(self.h.shutil, "which", lambda name: "/usr/bin/debugfs"):
            self.assertTrue(self.image_check())
            # Так debugfs отвечает про путь, которого нет: в stdout пусто.
            answer["stdout"] = ""
            self.assertFalse(self.image_check())
        self.assertEqual(calls[0][:3], ["/usr/bin/debugfs", "-c", "-R"])
        self.assertEqual(calls[0][3], "stat /system/etc/init/magisk")
        self.assertEqual(calls[0][4], os.path.join(images, "system.img"))
        # Нет debugfs — не знаем, значит, не пугаем.
        with mock.patch.object(self.h.shutil, "which", lambda name: None):
            self.assertFalse(self.image_check())

    def test_links_inside_the_overlay_are_not_followed(self):
        """На месте каталога в оверлее — ссылка наружу (положить её туда мог
        только root). Ни удаление, ни установка по ней не идут."""
        outside = tempfile.mkdtemp(prefix="merci-test-outside-")
        self.addCleanup(shutil.rmtree, outside, True)
        os.makedirs(os.path.join(outside, "magisk"))
        for name in ("bootanim.rc", "bootanim.rc.gz", "magisk/magisk64"):
            with open(os.path.join(outside, name), "w") as handle:
                handle.write("magisk outside")
        os.rmdir(self.init)
        os.symlink(outside, self.init)

        code, out, err = self.call(self.h.cmd_magisk_remove)
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(os.listdir(outside)), ["bootanim.rc", "bootanim.rc.gz", "magisk"])
        code, _out, err = self.call(self.h.cmd_magisk_install)
        self.assertEqual(code, 1)
        self.assertIn("стоит ссылка", err)
        self.assertEqual(os.listdir(os.path.join(outside, "magisk")), ["magisk64"])

    def test_half_written_leftovers_are_swept(self):
        """Файлы, недописанные убитой установкой, следующая убирает: init
        читает из /system/etc/init всё подряд, как бы файл ни назывался."""
        with open(self.system("ndk_translation.rc.merci-new"), "w") as handle:
            handle.write("half")
        self.assertEqual(self.call(self.h.cmd_magisk_install)[0], 0)
        self.assertFalse([name for name in os.listdir(self.init) if name.endswith(".merci-new")])

    def test_remove_leaves_foreign_bootanim_alone(self):
        with open(self.system("bootanim.rc"), "w") as handle:
            handle.write("service bootanim /system/bin/bootanimation\n    disabled\n")
        self.assertEqual(self.call(self.h.cmd_magisk_remove)[0], 0)
        self.assertTrue(os.path.exists(self.system("bootanim.rc")))

    def test_remove_cleans_every_window_and_follows_no_links(self):
        precious = tempfile.mkdtemp(prefix="merci-test-precious-")
        self.addCleanup(shutil.rmtree, precious, True)
        with open(os.path.join(precious, "keep"), "w") as handle:
            handle.write("keep")
        # Основное окно: на месте каталога Magisk — ссылка наружу.
        os.makedirs(os.path.join(self.data, "adb"))
        os.symlink(precious, os.path.join(self.data, "adb", "magisk"))
        # Второе окно: обычные остатки, внутри ещё одна ссылка.
        second = os.path.join(self.home, ".local/share/waydroid2/data/adb")
        os.makedirs(os.path.join(second, "magisk"))
        os.symlink(precious, os.path.join(second, "magisk", "link"))
        with open(os.path.join(second, "magisk.db"), "w") as handle:
            handle.write("grants")
        # Третье окно: сам каталог adb — ссылка в чужое место.
        third = os.path.join(self.home, ".local/share/waydroid3/data")
        os.makedirs(third)
        os.symlink("/etc", os.path.join(third, "adb"))
        self.h.known_instances = lambda: [1, 2, 3]

        code, out, err = self.call(self.h.cmd_magisk_remove)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out.splitlines()[-1])["data"], 3)
        self.assertEqual(os.listdir(os.path.join(self.data, "adb")), [])
        self.assertEqual(os.listdir(second), [])
        self.assertEqual(os.listdir(precious), ["keep"])
        self.assertTrue(os.path.islink(os.path.join(third, "adb")))

    def test_service_is_stopped_for_the_change_and_brought_back(self):
        calls = self.h.SERVICE_CALLS

        def service(action):
            calls.append(action)
            return True

        self.h.container_service = service
        self.assertEqual(self.call(self.h.cmd_magisk_remove)[0], 0)
        self.assertEqual(calls, ["is-active", "stop", "start"])
        del calls[:]
        # Сорвалась установка — служба всё равно возвращается.
        self.h._install_file = mock.Mock(side_effect=OSError(28, "No space left on device"))
        self.assertEqual(self.call(self.h.cmd_magisk_install)[0], 1)
        self.assertEqual(calls, ["is-active", "stop", "start"])

    def test_both_commands_want_the_whole_waydroid(self):
        self.assertIn("magisk-install", self.h.EXCLUSIVE)
        self.assertIn("magisk-remove", self.h.EXCLUSIVE)


@unittest.skipUnless(HAVE_GI, "нужен PyGObject")
class MagiskStepTest(unittest.TestCase):
    """Шаги установки и удаления — сценарии оболочки; часть, которая ищет
    файл Magisk, исполняется здесь по-настоящему, с поддельным curl."""

    def setUp(self):
        from merci import waydroid

        self.w = waydroid
        self.saved = {name: getattr(waydroid, name) for name in (
            "host", "host_known", "askpass_helper", "waydroid_cfg", "_helper_digest",
            "_instance_helper", "_script_cache", "github_mirror", "_MAGISK_BUILDS",
            "_MAGISK_SOURCES")}
        host = hostinfo.Host(family="arch", init="systemd", session="wayland",
                             tools=frozenset({"sudo"}))
        waydroid.host = lambda refresh=False: host
        waydroid.host_known = lambda: host
        waydroid.askpass_helper = lambda: ""
        waydroid.waydroid_cfg = lambda: updates.parse_cfg(CFG)
        waydroid._helper_digest = lambda: "0" * 64
        waydroid._instance_helper = lambda: "/tmp/helper.py"
        waydroid.github_mirror = lambda: ""

        self.work = tempfile.mkdtemp(prefix="merci-test-magisk-")
        self.home = os.path.join(self.work, "home")
        os.makedirs(os.path.join(self.home, "Загрузки"))
        waydroid._script_cache = lambda: os.path.join(self.work, "cache")
        self.good = b"the trusted build"
        waydroid._MAGISK_BUILDS = {hashlib.sha256(self.good).hexdigest(): len(self.good)}
        waydroid._MAGISK_SOURCES = ("https://example.invalid/magisk.apk",)
        self.apk = waydroid._magisk_apk()

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(self.w, name, value)
        shutil.rmtree(self.work, ignore_errors=True)

    def curl(self, body):
        """Поддельный curl: считает вызовы и ведёт себя, как велено."""
        tools = os.path.join(self.work, "bin")
        os.makedirs(tools, exist_ok=True)
        path = os.path.join(tools, "curl")
        with open(path, "w") as handle:
            handle.write(
                "#!/bin/sh\n"
                f'echo call >> "{self.work}/curl.calls"\n'
                'while [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done\n'
                + body + "\n"
            )
        os.chmod(path, 0o755)
        # sleep между попытками проверкам ни к чему
        with open(os.path.join(tools, "sleep"), "w") as handle:
            handle.write("#!/bin/sh\nexit 0\n")
        os.chmod(os.path.join(tools, "sleep"), 0o755)

    def fetch(self):
        tools = os.path.join(self.work, "bin")
        env = dict(os.environ, HOME=self.home, PATH=tools + ":/usr/bin:/bin")
        script = "set -e; " + self.w._magisk_fetch(self.apk) + "echo FETCH-OK"
        return subprocess.run(["sh", "-c", script], env=env, capture_output=True, text=True)

    def calls(self):
        try:
            with open(os.path.join(self.work, "curl.calls")) as handle:
                return handle.read().count("call")
        except OSError:
            return 0

    def test_steps_parse_and_keep_the_session_promise(self):
        for step in (self.w.magisk_step(), self.w.magisk_step(remove=True)):
            script = step.argv[-1]
            result = subprocess.run(["sh", "-n", "-c", script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(step.asks_root and step.then_session and not step.root)
            # Чужой код под root больше не исполняется.
            self.assertNotIn("waydroid_script", script.replace("waydroid-script", ""))
            self.assertNotIn("git clone", script)
            self.assertNotIn("waydroid session start", script.replace(
                "'^[^ ]*python[^ ]* [^ ]*waydroid[^ ]* session start'", ""))
        remove = self.w.magisk_step(remove=True).argv[-1]
        self.assertIn("'magisk-remove'", remove.replace("'\\''", "'"))
        # Удаление ничего не качает.
        self.assertNotIn("curl", remove)
        install = self.w.magisk_step().argv[-1]
        self.assertIn("'magisk-install'", install.replace("'\\''", "'"))
        # Файл ищется до остановки Waydroid.
        self.assertLess(install.index("sha256sum"), install.index("останавливаем Waydroid"))
        # Метка «дальше не отменить» — после поиска файла и до остановки;
        # сигналы перестают слушаться раньше, чем ставится ловушка выхода.
        mark = f"echo {self.w.NO_CANCEL_MARK}"
        for script in (install, remove):
            self.assertEqual(script.count(mark), 1)
            self.assertLess(script.index("trap '' TERM INT"), script.index(mark))
            self.assertLess(script.index(mark), script.index("trap finish EXIT"))
            self.assertLess(script.index(mark), script.index("останавливаем Waydroid"))
        self.assertLess(install.index("sha256sum"), install.index(mark))
        # Поиск файла отменить можно, удаление — нельзя с самого начала.
        self.assertTrue(self.w.magisk_step().prelude)
        self.assertFalse(self.w.magisk_step(remove=True).prelude)

    def test_runner_locks_itself_when_the_script_says_so(self):
        """Строка-метка из сценария запирает шаг: мастер прячет «Отмену»,
        а в журнал метка не попадает."""
        w = self.w
        lines, locked = [], []
        plain = w.Step(key="demo", title="x", argv=["true"], asks_root=True)
        runner = w.StepRunner(plain, lines.append, lambda ok: None, on_locked=lambda: locked.append(1))
        self.assertFalse(runner.locked)
        runner._handle("качаем архив")
        runner._handle(w.NO_CANCEL_MARK)
        runner._handle(w.NO_CANCEL_MARK)
        self.assertTrue(runner.locked)
        self.assertEqual(locked, [1])
        self.assertEqual(lines, ["качаем архив"])
        runner.cancel()  # запертый шаг отмена не трогает
        self.assertFalse(runner._done)
        # Шаг с остановкой контейнера заперт сразу, с отменяемым началом — нет.
        stop = w.Step(key="demo", title="x", argv=["true"], asks_root=True, then_session=True)
        self.assertTrue(w.StepRunner(stop, lines.append, lambda ok: None).locked)
        fetch = w.Step(key="demo", title="x", argv=["true"], asks_root=True, then_session=True,
                       prelude=True)
        self.assertFalse(w.StepRunner(fetch, lines.append, lambda ok: None).locked)

    def test_first_nvidia_install_goes_the_guarded_way(self):
        """Первое включение ускорения идёт тем же путём, что обновление пакета:
        сборка от пользователя, один заход помощника под root, настройки
        Merci возвращаются. Прежний сценарий снимал пакет waydroid отдельной
        командой и звал настройку мимо помощника — она сбрасывала исправление
        мерцания."""
        # Обычный Waydroid: ускорения ещё нет, режим не выбран.
        plain_cfg = CFG.replace("nvidia_venus_socket = /run/waydroid-venus/venus.sock\n", "")
        self.w.waydroid_cfg = lambda: updates.parse_cfg(plain_cfg)
        step = self.w.nvidia_step(144)
        script = step.argv[-1]
        parsed = subprocess.run(["sh", "-n", "-c", script], capture_output=True, text=True)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)
        self.assertEqual(step.key, "nvidia")
        self.assertNotIn("--mode", script)  # режим выберет сама настройка
        self.assertTrue(step.asks_root and step.then_session and step.prelude)
        plain = script.replace("'\\''", "'")
        self.assertIn("'nvidia-update'", plain)
        self.assertIn("'--refresh' '144'", plain)
        # Исправление мерцания помощник вернёт на место (в настройках из
        # проверочного конфига подповерхности выключены).
        self.assertIn("'--subsurface' 'false'", plain)
        for gone in ("pacman -Rdd", "pacman -U", "waydroid-nvidia-setup --refresh"):
            self.assertNotIn(gone, script)
        # Сборку отменить можно, а с остановки Waydroid — уже нет.
        mark = script.index(f"echo {self.w.NO_CANCEL_MARK}")
        self.assertLess(script.index("makepkg -s"), mark)
        self.assertLess(mark, script.index("'nvidia-update'".replace("'", "'\\''")))
        # Режим выбирает настройка: сервер Venus поднимаем, только если она
        # выбрала его, — узнаём уже после неё.
        self.assertIn("grep -qE '^nvidia_driver_mode *= *venus'", script)
        self.assertIn("systemctl --user enable wd-venus.service", script)

    def test_package_update_keeps_the_chosen_mode(self):
        """Обновление пакета называет режим явно — иначе новая версия
        настройки молча выбрала бы свой."""
        update = updates.Update(key="package", title="x",
                                data={"name": "waydroid-nvidia-bin", "source": "aur",
                                      "nvidia": True})
        step = self.w.nvidia_update_step(update)
        plain = step.argv[-1].replace("'\\''", "'")
        self.assertEqual(step.key, "update-package")
        self.assertIn("'--mode' 'venus'", plain)
        self.assertNotIn("grep -qE '^nvidia_driver_mode", plain)  # режим известен заранее
        self.assertIn("systemctl --user restart wd-venus.service", plain)

    def test_flicker_fix_goes_through_the_helper(self):
        step = self.w.subsurface_step()
        script = step.argv[-1]
        parsed = subprocess.run(["sh", "-n", "-c", script], capture_output=True, text=True)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)
        plain = script.replace("'\\''", "'")
        self.assertIn("'subsurface' 'false'", plain)
        self.assertNotIn("sed -i", script)  # три sudo подряд остались в прошлом
        self.assertEqual(script.count("sudo "), 1)
        self.assertTrue(step.asks_root and step.then_session)

    def test_download_note_only_when_there_is_something_to_download(self):
        self.assertTrue(self.w.magisk_step().downloads)
        os.makedirs(os.path.dirname(self.apk))
        with open(self.apk, "wb") as handle:
            handle.write(self.good)
        self.assertEqual(self.w.magisk_step().downloads, "")

    def test_cached_file_needs_no_network(self):
        os.makedirs(os.path.dirname(self.apk))
        with open(self.apk, "wb") as handle:
            handle.write(self.good)
        self.curl("exit 7")
        result = self.fetch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("FETCH-OK", result.stdout)
        self.assertEqual(self.calls(), 0)

    def test_file_from_downloads_is_taken(self):
        # Тот же размер, другое содержимое: решает сумма, а не размер.
        with open(os.path.join(self.home, "Загрузки", "какой-то.apk"), "wb") as handle:
            handle.write(b"an untrusted file")
        with open(os.path.join(self.home, "Загрузки", "большой.apk"), "wb") as handle:
            handle.write(b"x" * 4096)
        with open(os.path.join(self.home, "Загрузки", "Magisk Delta.apk"), "wb") as handle:
            handle.write(self.good)
        self.curl("exit 7")
        result = self.fetch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("берём готовый файл", result.stdout)
        with open(self.apk, "rb") as handle:
            self.assertEqual(handle.read(), self.good)
        self.assertEqual(self.calls(), 0)

    def test_gone_source_fails_at_once_and_says_why(self):
        self.curl("exit 22")
        result = self.fetch()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertNotIn("FETCH-OK", result.stdout)
        self.assertIn("файла по этому адресу больше нет", result.stdout)
        self.assertIn("взять негде", result.stdout)
        self.assertEqual(self.calls(), 1)  # отказ сервера не повторяем
        self.assertFalse(os.path.exists(self.apk))

    def test_wrong_download_is_thrown_away(self):
        self.curl('printf "something else" > "$out"')
        result = self.fetch()
        self.assertEqual(result.returncode, 1)
        self.assertIn("сумма не совпала", result.stdout)
        self.assertFalse(os.path.exists(self.apk))
        self.assertFalse(os.path.exists(self.apk + ".part"))

    def test_broken_connection_is_retried(self):
        marker = os.path.join(self.work, "second")
        self.curl(
            f'if [ -e "{marker}" ]; then printf "the trusted build" > "$out"; exit 0; fi\n'
            f'touch "{marker}"; printf "the tru" > "$out"; exit 56'
        )
        result = self.fetch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("связь оборвалась", result.stdout)
        self.assertEqual(self.calls(), 2)
        with open(self.apk, "rb") as handle:
            self.assertEqual(handle.read(), self.good)

    def test_other_cached_build_is_not_used(self):
        os.makedirs(os.path.dirname(self.apk))
        with open(self.apk, "wb") as handle:
            handle.write(b"some other magisk")
        self.curl("exit 22")
        result = self.fetch()
        self.assertEqual(result.returncode, 1)
        self.assertIn("другая сборка Magisk", result.stdout)

    def test_both_sides_trust_the_same_builds(self):
        """Список сборок у Merci и у помощника под root — один и тот же."""
        helper = load_helper()
        self.assertEqual(set(self.saved["_MAGISK_BUILDS"]), set(helper.MAGISK_APKS))

    def test_missing_curl_is_said_plainly(self):
        """Нет curl — так и говорим, а не «связь оборвалась» десять раз."""
        tools = os.path.join(self.work, "plain")
        os.makedirs(tools)
        for tool in ("sh", "sha256sum", "cut", "stat", "mkdir", "dirname", "cp", "mv", "rm"):
            os.symlink(shutil.which(tool), os.path.join(tools, tool))
        script = "set -e; " + self.w._magisk_fetch(self.apk) + "echo FETCH-OK"
        result = subprocess.run([shutil.which("sh"), "-c", script],
                                env={"HOME": self.home, "PATH": tools},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("нет curl", result.stdout)
        self.assertNotIn("связь оборвалась", result.stdout)
        self.assertIn("взять негде", result.stdout)

    def test_larger_file_at_the_address_is_not_downloaded(self):
        self.curl("exit 63")
        result = self.fetch()
        self.assertEqual(result.returncode, 1)
        self.assertIn("больше, чем нужный", result.stdout)
        self.assertEqual(self.calls(), 1)

    def test_curl_gets_size_and_stall_limits(self):
        self.curl("exit 22")  # заводит каталог с поддельными командами
        seen = os.path.join(self.work, "args")
        with open(os.path.join(self.work, "bin", "curl"), "w") as handle:
            handle.write(f'#!/bin/sh\necho "$@" > "{seen}"\nexit 22\n')
        self.fetch()
        with open(seen) as handle:
            args = handle.read()
        self.assertIn(f"--max-filesize {len(self.good)}", args)
        self.assertIn("--speed-time", args)
        self.assertIn("--connect-timeout", args)
        self.assertNotIn("--retry-all-errors", args)

    def test_server_without_resume_starts_the_file_over(self):
        marker = os.path.join(self.work, "second")
        stale = os.path.join(self.work, "stale")
        self.curl(
            f'if [ -e "{marker}" ]; then [ -e "$out" ] && touch "{stale}"; '
            'printf "the trusted build" > "$out"; exit 0; fi\n'
            f'touch "{marker}"; printf "the tru" > "$out"; exit 33'
        )
        result = self.fetch()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.calls(), 2)
        self.assertFalse(os.path.exists(stale))  # обрывок убран до второй попытки

    def test_term_during_root_work_waits_for_the_helper(self):
        """Шаг, который просят завершиться посреди работы под root, обязан
        дождаться помощника и только потом решать судьбу сессии: раньше
        оболочка выходила сразу, с кодом «всё хорошо», и поднимала Waydroid
        рядом с помощником, который ещё переписывал оверлей."""
        import signal
        import time

        tools = os.path.join(self.work, "bin")
        os.makedirs(tools, exist_ok=True)
        log = os.path.join(self.work, "log")
        fakes = {
            "waydroid": f'#!/bin/sh\necho "waydroid $*" >> "{log}"\n'
                        '[ "$1" = status ] && printf "Session:\\tRUNNING\\n"\nexit 0\n',
            "pkill": "#!/bin/sh\nexit 1\n",
            "sleep": "#!/bin/sh\nexit 0\n",
            "systemctl": "#!/bin/sh\nexit 0\n",
            "setsid": f'#!/bin/sh\necho "setsid $*" >> "{log}"\nexit 0\n',
            "sudo": f'#!/bin/sh\necho "sudo begin" >> "{log}"\n/usr/bin/sleep 1.5\n'
                    f'echo "sudo end" >> "{log}"\nexit 3\n',
        }
        for name, body in fakes.items():
            with open(os.path.join(tools, name), "w") as handle:
                handle.write(body)
            os.chmod(os.path.join(tools, name), 0o755)
        open(log, "w").close()
        env = dict(os.environ, HOME=self.home, PATH=tools + ":/usr/bin:/bin")
        script = self.w.magisk_step(remove=True).argv[-1]
        process = subprocess.Popen(["sh", "-c", script], env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True)
        for _ in range(100):
            with open(log) as handle:
                if "sudo begin" in handle.read():
                    break
            time.sleep(0.05)
        process.send_signal(signal.SIGTERM)
        process.communicate(timeout=30)
        time.sleep(0.5)  # подъём сессии сценарий отправляет в фон
        with open(log) as handle:
            lines = handle.read().splitlines()
        self.assertIn("sudo end", lines)
        start = next(i for i, line in enumerate(lines) if line.startswith("setsid"))
        self.assertGreater(start, lines.index("sudo end"), lines)
        # Итог — настоящий код помощника, а не «всё хорошо».
        self.assertEqual(process.returncode, 3)


    def fakes(self, sudo):
        """Поддельные команды для прогона шага целиком."""
        tools = os.path.join(self.work, "bin")
        os.makedirs(tools, exist_ok=True)
        log = os.path.join(self.work, "log")
        bodies = {
            "waydroid": f'#!/bin/sh\necho "waydroid $*" >> "{log}"\n'
                        '[ "$1" = status ] && printf "Session:\\tRUNNING\\n"\nexit 0\n',
            "pkill": "#!/bin/sh\nexit 1\n",
            "sleep": "#!/bin/sh\nexit 0\n",
            "systemctl": "#!/bin/sh\nexit 0\n",
            "setsid": f'#!/bin/sh\necho "setsid $*" >> "{log}"\nexit 0\n',
            "sudo": f'#!/bin/sh\necho "sudo begin" >> "{log}"\n{sudo}\n',
        }
        for name, body in bodies.items():
            with open(os.path.join(tools, name), "w") as handle:
                handle.write(body)
            os.chmod(os.path.join(tools, name), 0o755)
        open(log, "w").close()
        return log, dict(os.environ, HOME=self.home, PATH=tools + ":/usr/bin:/bin")

    def test_step_reports_its_end_through_a_file(self):
        """Запущенный в терминале шаг пишет код возврата в файл: по нему Merci
        узнаёт о конце работы, даже если окно терминала закрыли."""
        code_file = os.path.join(self.work, "code")
        for wanted in (0, 3):
            log, env = self.fakes(f"exit {wanted}")
            env["MERCI_CODE_FILE"] = code_file
            result = subprocess.run(["sh", "-c", self.w.magisk_step(remove=True).argv[-1]],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, wanted)
            with open(code_file) as handle:
                self.assertEqual(handle.read().strip(), str(wanted))
            os.unlink(code_file)
        # Без просьбы файл не пишется (обычный запуск — вывод читает сама Merci).
        log, env = self.fakes("exit 0")
        subprocess.run(["sh", "-c", self.w.magisk_step(remove=True).argv[-1]],
                       env=env, capture_output=True, text=True)
        self.assertFalse(os.path.exists(code_file))

    def test_hangup_stops_the_step_only_before_root_work(self):
        """Закрытое окно терминала (HUP): пока шаг ищет и качает файл —
        останавливает его; с началом работы под root — уже нет."""
        import signal
        import time

        # До метки: «качаем» — и получаем HUP.
        log, env = self.fakes("exit 0")
        self.curl("/usr/bin/sleep 3; exit 56")
        process = subprocess.Popen(["sh", "-c", self.w.magisk_step().argv[-1]], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for _ in range(100):
            if self.calls():
                break
            time.sleep(0.05)
        process.send_signal(signal.SIGHUP)
        process.communicate(timeout=20)
        self.assertEqual(process.returncode, -signal.SIGHUP)
        with open(log) as handle:
            self.assertNotIn("waydroid session stop", handle.read())

        # После метки: помощник работает — HUP шаг не рвёт.
        log, env = self.fakes('/usr/bin/sleep 1.5\necho "sudo end" >> "' + os.path.join(
            self.work, "log") + '"\nexit 0')
        process = subprocess.Popen(["sh", "-c", self.w.magisk_step(remove=True).argv[-1]],
                                   env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True)
        for _ in range(100):
            with open(log) as handle:
                if "sudo begin" in handle.read():
                    break
            time.sleep(0.05)
        process.send_signal(signal.SIGHUP)
        process.communicate(timeout=30)
        self.assertEqual(process.returncode, 0)
        time.sleep(0.5)  # подъём сессии сценарий отправляет в фон
        with open(log) as handle:
            lines = handle.read().splitlines()
        self.assertIn("sudo end", lines)
        self.assertTrue(any(line.startswith("setsid waydroid session start") for line in lines))

    def test_closed_terminal_does_not_end_a_step_doing_root_work(self):
        """Шаг шёл в терминале, сценарий дошёл до работы под root, окно
        терминала закрыли. Мастер обязан ждать настоящего конца — иначе окно
        сняло бы замок, пока помощник ещё работает."""
        w = self.w
        lines, done = [], []
        step = w.Step(key="demo", title="x", argv=["true"], asks_root=True, then_session=True,
                      prelude=True)
        runner = w.StepRunner(step, lines.append, done.append)
        runner._on_done = done.append  # без ожидания контейнера: проверяем только конец шага
        runner._tail_path = os.path.join(self.work, "step.log")
        runner._code_path = os.path.join(self.work, "step.code")
        runner._handle(w.NO_CANCEL_MARK)
        self.assertFalse(runner._after_terminal())
        self.assertEqual(done, [])  # не «не удалось», а ожидание
        self.assertTrue(any("идёт до конца" in line for line in lines))
        self.assertTrue(runner._await_code())  # файла ещё нет — ждём дальше
        with open(runner._code_path, "w") as handle:
            handle.write("0\n")
        self.assertFalse(runner._await_code())
        self.assertEqual(done, [True])

        # А шаг, не дошедший до работы под root, окно терминала обрывает сразу.
        lines.clear()
        done.clear()
        early = w.StepRunner(step, lines.append, done.append)
        early._on_done = done.append
        early._tail_path = runner._tail_path
        early._code_path = runner._code_path
        early._after_terminal()
        self.assertEqual(done, [False])


@unittest.skipUnless(HAVE_GI, "нужен PyGObject")
class ForgetMagiskAppTest(unittest.TestCase):
    """Уборка приложения Magisk после удаления root: тихо и без пароля."""

    def setUp(self):
        from merci import waydroid

        self.w = waydroid
        self.calls = []
        self.answers = []
        names = ("adb_available", "container_ip", "adb_reachable", "instance_adb",
                 "_installs_changed", "adb_connect", "_instance_call")
        self.saved = {name: getattr(waydroid, name) for name in names}
        waydroid.adb_available = lambda: True
        waydroid.container_ip = lambda: "192.168.240.112"
        waydroid.adb_reachable = lambda ip, seconds=1.5: True
        waydroid._installs_changed = lambda: None

        def forbidden(*_args, **_kwargs):
            raise AssertionError("уборка не должна звать помощника и просить пароль")

        waydroid.adb_connect = forbidden
        waydroid._instance_call = forbidden

        def adb(ip, args, timeout=120):
            self.calls.append(args)
            code, text = self.answers.pop(0)
            return types.SimpleNamespace(returncode=code, stdout=text, stderr="")

        waydroid.instance_adb = adb
        patch = mock.patch.object(waydroid.time, "sleep", lambda _seconds: None)
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(self.w, name, value)

    def test_waits_for_android_and_uninstalls(self):
        self.answers = [
            (20, "cmd: Can't find service: package"),  # Android ещё грузится
            (0, "package:io.github.huskydg.magisk\n"),
            (0, "Success\n"),
        ]
        self.assertTrue(self.w.forget_magisk_app())
        self.assertEqual(self.calls[-1], ["uninstall", "io.github.huskydg.magisk"])
        self.assertEqual(len(self.calls), 3)

    def test_only_the_exact_package_is_touched(self):
        self.answers = [(0, "package:io.github.huskydg.magisk.something\n")]
        self.assertFalse(self.w.forget_magisk_app())
        self.assertEqual(len(self.calls), 1)  # до удаления не дошло

    def test_absent_app_is_not_an_error(self):
        self.answers = [(0, "")]
        self.assertFalse(self.w.forget_magisk_app())

    def test_unauthorized_container_is_left_alone(self):
        self.answers = [(1, "error: device unauthorized.")]
        self.assertFalse(self.w.forget_magisk_app())
        self.assertEqual(len(self.calls), 1)

    def test_no_adb_no_work(self):
        self.w.adb_available = lambda: False
        self.assertFalse(self.w.forget_magisk_app())
        self.assertEqual(self.calls, [])


@unittest.skipUnless(HAVE_GI, "нужен PyGObject")
class FailureReasonTest(unittest.TestCase):
    """Когда шаг не удался, мастер показывает, чем шаг это объяснил."""

    def reason(self, lines):
        from merci import installer

        return installer.failure_reason(lines)

    def test_explanation_of_the_step_is_shown(self):
        lines = [
            "$ sh -c 'set -e; A=…'",
            "качаем Magisk Delta: https://example.invalid/app-release.apk",
            "#=#=#",
            "##O#-#          curl: (22) The requested URL returned error: 404",
            "сервер ответил отказом: файла по этому адресу больше нет",
            "Проверенного файла Magisk Delta взять негде: репозиторий удалён с GitHub.",
            "Ставить под root файл без известной контрольной суммы Merci не станет.",
            "Если у вас сохранился файл, положите его в «Загрузки» и повторите.",
            "код возврата: 1",
        ]
        self.assertEqual(self.reason(lines), "\n".join(lines[5:8]))

    def test_python_crash_is_reduced_to_its_last_line(self):
        lines = [
            "$ sudo sh -c '…'",
            "Traceback (most recent call last):",
            '  File "/run/merci-helper.x/helper.py", line 10, in <module>',
            "    main()",
            "OSError: [Errno 28] No space left on device",
            "код возврата: 1",
        ]
        self.assertEqual(self.reason(lines), "OSError: [Errno 28] No space left on device")

    def test_script_crash_keeps_the_verdict_and_the_error(self):
        """Так выглядел отказ, с которого всё началось."""
        lines = [
            "$ sudo sh -c '…'",
            "INFO: Uninstallation finished",
            "Traceback (most recent call last):",
            "    raise subprocess.CalledProcessError(",
            "subprocess.CalledProcessError: Command '['waydroid', 'upgrade', '-o']' "
            "returned non-zero exit status 0.",
            "не удалось выполнить: remove magisk",
            "код возврата: 1",
        ]
        self.assertEqual(self.reason(lines), "\n".join(lines[4:6]))

    def test_nothing_to_say(self):
        self.assertEqual(self.reason(["$ sh -c 'false'", "код возврата: 1"]), "")
        self.assertEqual(self.reason([]), "")

    def test_previous_step_does_not_leak(self):
        lines = ["$ first", "причина из прошлого шага", "код возврата: 0",
                 "$ second", "код возврата: 1"]
        self.assertEqual(self.reason(lines), "")

    def test_long_line_is_cut(self):
        text = self.reason(["$ x", "a" * 1000, "код возврата: 1"])
        self.assertLessEqual(len(text), 260)
        self.assertTrue(text.endswith("…"))


if __name__ == "__main__":
    unittest.main()
