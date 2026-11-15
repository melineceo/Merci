"""Проверки установки транслятора ARM помощником (libhoudini и libndk).

Запуск из корня проекта:  python3 -m unittest discover -s tests -v

Помощник работает во временном каталоге вместо /var/lib/waydroid; вместо
настоящих архивов — маленькие той же раскладки
(<имя>/prebuilts/{bin,etc,lib,lib64}/…). Шаги проверяются как сценарии
оболочки с поддельными командами. Ни машина, ни сеть не затрагиваются.
"""

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import types
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_updates import CFG, HAVE_GI, load_helper  # noqa: E402

from merci import hostinfo, updates  # noqa: E402


def fake_archive(kind, extra=None, skip=()):
    """Архив транслятора в раскладке supremegamers/vendor_*: рядом с
    prebuilts лежит и то, чему в системе делать нечего."""
    top = f"vendor_{kind}-0123abcd/"
    pre = top + "prebuilts/"
    files = {top + "README.md": b"readme", top + "Android.mk": b"makefile"}
    if kind == "libhoudini":
        files.update({
            pre + "bin/houdini": b"houdini",
            pre + "bin/houdini64": b"houdini64",
            pre + "lib/libhoudini.so": b"houdini lib32",
            pre + "lib64/libhoudini.so": b"houdini lib64",
            pre + "lib/arm/libc.so": b"arm libc (houdini)",
            pre + "lib64/arm64/libc.so": b"arm64 libc (houdini)",
            pre + "lib64/arm64/libonly_houdini.so": b"only houdini has it",
            pre + "etc/binfmt_misc/arm_exe": b":arm_exe:houdini",
            pre + "etc/init/houdini.rc": b"init file from the archive",
        })
    else:
        files.update({
            pre + "bin/ndk_translation_program_runner_binfmt_misc": b"runner",
            pre + "bin/arm64/linker64": b"linker64",
            pre + "lib/libndk_translation.so": b"ndk lib32",
            pre + "lib64/libndk_translation.so": b"ndk lib64",
            pre + "lib/arm/libc.so": b"arm libc (ndk)",
            pre + "lib64/arm64/libc.so": b"arm64 libc (ndk)",
            pre + "etc/binfmt_misc/arm_exe": b":arm_exe:ndk",
            pre + "etc/init/ndk_translation.rc": b"ndk init",
            pre + "etc/ld.config.arm.txt": b"ld config",
            pre + "etc/cpuinfo.arm.txt": b"cpuinfo",
        })
    files.update(extra or {})
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as archive:
        for name, data in files.items():
            if name not in skip:
                archive.writestr(name, data)
    return blob.getvalue()


class HelperBridgeTest(unittest.TestCase):
    def setUp(self):
        self.h = load_helper()
        self.dir = tempfile.mkdtemp(prefix="merci-test-")
        self.home = tempfile.mkdtemp(prefix="merci-test-home-")
        with open(os.path.join(self.dir, "waydroid.cfg"), "w") as handle:
            handle.write(CFG)
        with open(os.path.join(self.dir, "waydroid_base.prop"), "w") as handle:
            handle.write("ro.adb.secure=1\n")
        self.h.WAYDROID_DIR = self.dir
        self.h.CFG_PATH = os.path.join(self.dir, "waydroid.cfg")
        self.h.BASE_PROP = os.path.join(self.dir, "waydroid_base.prop")
        self.h.CALLER_UID = os.getuid()
        self.system = os.path.join(self.dir, "overlay", "system")

        self.blobs = {kind: fake_archive(kind) for kind in ("libhoudini", "libndk")}
        self.paths = {}
        for kind, blob in self.blobs.items():
            self.paths[kind] = os.path.join(self.home, kind + ".zip")
            with open(self.paths[kind], "wb") as handle:
                handle.write(blob)
        self.h.BRIDGE_ARCHIVES = {
            hashlib.sha256(blob).hexdigest(): kind for kind, blob in self.blobs.items()
        }
        self.h.BRIDGE_ARCHIVES_MD5 = {}

        self.owners = {}

        def fchown(descriptor, uid, gid):
            self.owners[os.readlink(f"/proc/self/fd/{descriptor}")] = (uid, gid)

        patch = mock.patch.object(self.h.os, "fchown", fchown)
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        shutil.rmtree(self.home, ignore_errors=True)

    def install(self, archive):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with redirect_stdout(out), redirect_stderr(err):
            try:
                self.h.cmd_bridge_install(types.SimpleNamespace(home=self.home, archive=archive))
            except SystemExit as stop:
                code = stop.code
        return code, out.getvalue(), err.getvalue()

    def read(self, *parts):
        with open(os.path.join(self.system, *parts), "rb") as handle:
            return handle.read()

    def mode(self, *parts):
        return stat.S_IMODE(os.stat(os.path.join(self.system, *parts)).st_mode)

    def props(self, name):
        with open(os.path.join(self.dir, name)) as handle:
            text = handle.read()
        return {key.strip(): value.strip() for key, _eq, value in
                (line.partition("=") for line in text.splitlines() if "=" in line)}

    def listing(self):
        found = []
        for root, _dirs, files in os.walk(self.system):
            found += [os.path.relpath(os.path.join(root, name), self.system) for name in files]
        return sorted(found)

    def test_houdini_is_laid_out(self):
        code, out, err = self.install(self.paths["libhoudini"])
        self.assertEqual(code, 0, err)
        answer = json.loads(out.splitlines()[-1])
        self.assertEqual(answer["target"], "libhoudini")
        self.assertEqual(answer["lib64_md5"], hashlib.md5(b"houdini lib64").hexdigest())

        self.assertEqual(self.read("bin", "houdini64"), b"houdini64")
        self.assertEqual(self.mode("bin", "houdini"), 0o755)
        self.assertEqual(self.mode("lib64", "libhoudini.so"), 0o644)
        self.assertEqual(self.read("etc", "binfmt_misc", "arm_exe"), b":arm_exe:houdini")
        # init-файл свой, а не из архива: тот рассчитан на раскладку /vendor.
        self.assertIn(b"/system/bin/houdini64", self.read("etc", "init", "houdini.rc"))
        self.assertNotIn("README.md", " ".join(self.listing()))
        # Владельцы: исполняемые — root:shell, библиотеки — root.
        self.assertEqual(
            self.owners[os.path.realpath(os.path.join(self.system, "bin", "houdini.merci-new"))],
            (0, 2000))
        self.assertEqual(
            self.owners[os.path.realpath(
                os.path.join(self.system, "lib64", "libhoudini.so.merci-new"))],
            (0, 0))
        # Свойства — в оба файла: второй переживает обновление образа.
        for name in ("waydroid_base.prop", "waydroid.cfg"):
            props = self.props(name)
            self.assertEqual(props["ro.dalvik.vm.native.bridge"], "libhoudini.so", name)
            self.assertEqual(props["ro.enable.native.bridge.exec"], "1", name)
        self.assertEqual(self.props("waydroid_base.prop")["ro.adb.secure"], "1")

    def test_ndk_takes_its_whole_etc(self):
        code, out, err = self.install(self.paths["libndk"])
        self.assertEqual(code, 0, err)
        answer = json.loads(out.splitlines()[-1])
        self.assertEqual(answer["target"], "libndk")
        self.assertEqual(answer["lib64_md5"], hashlib.md5(b"ndk lib64").hexdigest())
        self.assertEqual(self.read("etc", "init", "ndk_translation.rc"), b"ndk init")
        self.assertEqual(self.read("etc", "ld.config.arm.txt"), b"ld config")
        self.assertEqual(self.read("etc", "cpuinfo.arm.txt"), b"cpuinfo")
        self.assertEqual(self.mode("bin", "arm64", "linker64"), 0o755)
        self.assertEqual(self.mode("etc", "ld.config.arm.txt"), 0o644)
        props = self.props("waydroid_base.prop")
        self.assertEqual(props["ro.dalvik.vm.native.bridge"], "libndk_translation.so")
        self.assertEqual(props["ro.ndk_translation.version"], "0.2.3")
        self.assertFalse(os.path.exists(os.path.join(self.system, "etc", "init", "houdini.rc")))
        self.assertFalse([rel for rel in self.listing() if rel.endswith(".merci-new")])

    def test_switching_leaves_exactly_the_new_translator(self):
        """После смены в оверлее лежит ровно то, что принёс новый транслятор:
        ни init-файла прежнего (он регистрировал бы ARM-программы на себя),
        ни его библиотек вперемешку с новыми."""
        self.assertEqual(self.install(self.paths["libhoudini"])[0], 0)
        houdini_only = self.listing()
        code, out, err = self.install(self.paths["libndk"])
        self.assertEqual(code, 0, err)
        self.assertGreater(json.loads(out.splitlines()[-1])["pruned"], 0)
        self.assertEqual(self.read("lib64", "arm64", "libc.so"), b"arm64 libc (ndk)")
        self.assertEqual(self.read("etc", "binfmt_misc", "arm_exe"), b":arm_exe:ndk")
        self.assertEqual(self.props("waydroid.cfg")["ro.dalvik.vm.native.bridge"],
                         "libndk_translation.so")
        for gone in ("bin/houdini", "bin/houdini64", "lib64/libhoudini.so",
                     "etc/init/houdini.rc", "lib64/arm64/libonly_houdini.so"):
            self.assertNotIn(gone, self.listing())
        ndk_only = self.listing()

        self.assertEqual(self.install(self.paths["libhoudini"])[0], 0)
        self.assertEqual(self.listing(), houdini_only)  # как после чистой установки
        self.assertEqual(self.read("lib64", "arm64", "libc.so"), b"arm64 libc (houdini)")
        self.assertFalse(os.path.isdir(os.path.join(self.system, "bin", "arm64")))
        for name in ("waydroid_base.prop", "waydroid.cfg"):
            self.assertEqual(self.props(name)["ro.dalvik.vm.native.bridge"], "libhoudini.so")

        self.assertEqual(self.install(self.paths["libndk"])[0], 0)
        self.assertEqual(self.listing(), ndk_only)

    def test_reinstall_keeps_foreign_files_of_the_overlay(self):
        """Чужое в оверлее — не трансляторское — уборка не трогает."""
        self.assertEqual(self.install(self.paths["libndk"])[0], 0)
        os.makedirs(os.path.join(self.system, "etc", "init"), exist_ok=True)
        os.makedirs(os.path.join(self.system, "priv-app", "Some"), exist_ok=True)
        for rel in ("etc/init/bootanim.rc", "etc/init/resetprop.rc", "priv-app/Some/Some.apk",
                    "lib64/libfoo.so", "bin/resetprop.sh"):
            with open(os.path.join(self.system, rel), "w") as handle:
                handle.write("foreign")
        self.assertEqual(self.install(self.paths["libhoudini"])[0], 0)
        for rel in ("etc/init/bootanim.rc", "etc/init/resetprop.rc", "priv-app/Some/Some.apk",
                    "lib64/libfoo.so", "bin/resetprop.sh"):
            self.assertIn(rel, self.listing())

    def test_no_space_touches_nothing(self):
        self.assertEqual(self.install(self.paths["libhoudini"])[0], 0)
        before = {rel: self.read(*rel.split("/")) for rel in self.listing()}
        usage = types.SimpleNamespace(total=10**9, used=10**9 - 1000, free=1000)
        with mock.patch.object(self.h.shutil, "disk_usage", lambda _path: usage):
            code, _out, err = self.install(self.paths["libndk"])
        self.assertEqual(code, 1)
        self.assertIn("мало места", err)
        self.assertEqual({rel: self.read(*rel.split("/")) for rel in self.listing()}, before)
        self.assertEqual(self.props("waydroid_base.prop")["ro.dalvik.vm.native.bridge"],
                         "libhoudini.so")

    def test_failed_write_touches_nothing(self):
        """Запись оборвалась посреди смены — прежний транслятор цел, свойства
        прежние, недописанных файлов не осталось."""
        self.assertEqual(self.install(self.paths["libhoudini"])[0], 0)
        before = {rel: self.read(*rel.split("/")) for rel in self.listing()}
        real = self.h.stage_file
        calls = []

        def failing(dest, data, mode, gid=0):
            calls.append(dest)
            if len(calls) == 4:
                raise OSError(28, "No space left on device")
            return real(dest, data, mode, gid)

        self.h.stage_file = failing
        code, _out, err = self.install(self.paths["libndk"])
        self.assertEqual(code, 1)
        self.assertIn("прежний транслятор не тронут", err)
        self.assertEqual({rel: self.read(*rel.split("/")) for rel in self.listing()}, before)
        self.assertFalse([rel for rel in self.listing() if rel.endswith(".merci-new")])
        self.assertEqual(self.props("waydroid.cfg")["ro.dalvik.vm.native.bridge"],
                         "libhoudini.so")

    def test_image_is_released_and_overlays_are_on(self):
        calls = self.h.SERVICE_CALLS

        def service(action):
            calls.append(action)
            return True

        self.h.container_service = service
        with open(self.h.CFG_PATH, "w") as handle:
            handle.write(CFG.replace("mount_overlays = True", "mount_overlays = False"))
        self.assertEqual(self.install(self.paths["libhoudini"])[0], 0)
        self.assertEqual(calls, ["is-active", "stop", "start"])
        with open(self.h.CFG_PATH) as handle:
            self.assertIn("mount_overlays = True", handle.read())
        # Отказ посреди работы службу всё равно возвращает.
        del calls[:]
        self.h.stage_file = mock.Mock(side_effect=OSError(5, "Input/output error"))
        self.assertEqual(self.install(self.paths["libndk"])[0], 1)
        self.assertEqual(calls, ["is-active", "stop", "start"])

    def test_running_window_blocks_the_change(self):
        self.h.known_instances = lambda: [1, 3]
        self.h.lxc_state = lambda number: "RUNNING" if number == 3 else "STOPPED"
        code, _out, err = self.install(self.paths["libndk"])
        self.assertEqual(code, 1)
        self.assertIn("не остановилось", err)
        self.assertFalse(os.path.exists(self.system))

    def test_unknown_archive_is_refused_before_anything_is_touched(self):
        other = os.path.join(self.home, "other.zip")
        with open(other, "wb") as handle:
            handle.write(fake_archive("libndk", extra={"x/prebuilts/bin/evil": b"evil"}))
        code, _out, err = self.install(other)
        self.assertEqual(code, 1)
        self.assertIn("не из числа известных", err)
        self.assertFalse(os.path.exists(self.system))
        with open(self.h.BASE_PROP) as handle:
            self.assertNotIn("native.bridge", handle.read())

    def test_legacy_archive_is_recognised_by_md5(self):
        """Архивы для Android 11 закреплены суммой MD5 (см. помощника)."""
        blob = self.blobs["libhoudini"]
        self.h.BRIDGE_ARCHIVES = {}
        self.h.BRIDGE_ARCHIVES_MD5 = {hashlib.md5(blob).hexdigest(): "libhoudini"}
        code, out, err = self.install(self.paths["libhoudini"])
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out.splitlines()[-1])["target"], "libhoudini")

    def test_strange_paths_and_incomplete_archives_are_refused(self):
        tricky = fake_archive("libndk", extra={"x/prebuilts/lib/../../../escape": b"out"})
        short = fake_archive("libndk", skip=("vendor_libndk-0123abcd/prebuilts/lib64/"
                                             "libndk_translation.so",))
        for name, blob, message in (("tricky.zip", tricky, "подозрительный путь"),
                                    ("short.zip", short, "не нашлось файлов транслятора")):
            path = os.path.join(self.home, name)
            with open(path, "wb") as handle:
                handle.write(blob)
            self.h.BRIDGE_ARCHIVES = {hashlib.sha256(blob).hexdigest(): "libndk"}
            code, _out, err = self.install(path)
            self.assertEqual(code, 1, name)
            self.assertIn(message, err)
            self.assertFalse(os.path.exists(self.system), name)

    def test_links_inside_the_overlay_are_not_followed(self):
        """На месте каталога в оверлее — ссылка наружу (положить её туда мог
        только root). Уборка по ней не идёт, установка отказывается — и
        снаружи ничего не тронуто."""
        outside = tempfile.mkdtemp(prefix="merci-test-outside-")
        self.addCleanup(shutil.rmtree, outside, True)
        for name in ("victim.so", "libndk_victim.so", "libc.so"):
            with open(os.path.join(outside, name), "w") as handle:
                handle.write("precious")
        os.makedirs(os.path.join(self.system, "lib"))
        os.symlink(outside, os.path.join(self.system, "lib", "arm"))
        os.symlink(outside, os.path.join(self.system, "lib64"))

        self.assertEqual(self.h.bridge_prune("libhoudini", set()), 0)
        self.assertEqual(sorted(os.listdir(outside)), ["libc.so", "libndk_victim.so", "victim.so"])

        code, _out, err = self.install(self.paths["libhoudini"])
        self.assertEqual(code, 1)
        self.assertIn("стоит ссылка", err)
        self.assertIn("прежний транслятор не тронут", err)
        self.assertEqual(sorted(os.listdir(outside)), ["libc.so", "libndk_victim.so", "victim.so"])
        with open(os.path.join(outside, "libc.so")) as handle:
            self.assertEqual(handle.read(), "precious")
        self.assertFalse([rel for rel in self.listing() if rel.endswith(".merci-new")])

    def test_half_written_leftovers_are_swept(self):
        os.makedirs(os.path.join(self.system, "etc", "init"))
        leftover = os.path.join(self.system, "etc", "init", "ndk_translation.rc.merci-new")
        with open(leftover, "w") as handle:
            handle.write("half")
        self.assertEqual(self.install(self.paths["libhoudini"])[0], 0)
        self.assertFalse(os.path.exists(leftover))

    def test_link_inside_the_archive_is_refused(self):
        blob = io.BytesIO()
        with zipfile.ZipFile(blob, "a") as archive:
            archive.writestr("x/prebuilts/lib64/libndk_translation.so", b"lib")
            link = zipfile.ZipInfo("x/prebuilts/bin/sh")
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(link, "/etc/shadow")
        path = os.path.join(self.home, "links.zip")
        with open(path, "wb") as handle:
            handle.write(blob.getvalue())
        self.h.BRIDGE_ARCHIVES = {hashlib.sha256(blob.getvalue()).hexdigest(): "libndk"}
        code, _out, err = self.install(path)
        self.assertEqual(code, 1)
        self.assertIn("не обычный файл", err)
        self.assertFalse(os.path.exists(self.system))

    def test_only_the_callers_own_regular_file_is_read(self):
        link = os.path.join(self.home, "link.zip")
        os.symlink(self.paths["libndk"], link)
        self.assertEqual(self.install(link)[0], 1)
        self.assertEqual(self.install(self.home)[0], 1)
        if os.getuid() != 0:
            self.h.CALLER_UID = os.getuid() + 1
            code, _out, err = self.install(self.paths["libndk"])
            self.assertEqual(code, 1)
            self.assertIn("другому пользователю", err)
        self.assertFalse(os.path.exists(self.system))

    def test_real_archives_are_the_only_ones_trusted_by_default(self):
        fresh = load_helper()
        self.assertEqual(fresh.BRIDGE_ARCHIVES, {
            "2e82cdc88ddc4d418f7fb861aeebb7c49c83a91b97f57da10510b5e1146a4ed5": "libhoudini",
            "a142d1586c9eafb5edf62277110f2128bc03066179a6783bcaa33ee322e1cbd0": "libndk",
        })
        self.assertEqual(set(fresh.BRIDGE_ARCHIVES_MD5.values()), {"libhoudini", "libndk"})
        self.assertEqual(set(fresh.BRIDGES), {"libhoudini", "libndk"})
        self.assertIn("bridge-install", fresh.EXCLUSIVE)


@unittest.skipUnless(HAVE_GI, "нужен PyGObject")
class BridgeStepTest(unittest.TestCase):
    """Шаги транслятора: никакого чужого кода под root, архив — до пароля."""

    def setUp(self):
        from merci import waydroid

        self.w = waydroid
        names = ("host", "host_known", "askpass_helper", "waydroid_cfg", "_helper_digest",
                 "_instance_helper", "_script_cache", "github_mirror", "_android_version",
                 "_prop", "_remembered_sdk", "_remember_sdk", "_run", "_ARCHIVES")
        self.saved = {name: getattr(waydroid, name) for name in names}
        host = hostinfo.Host(family="arch", init="systemd", session="wayland",
                             tools=frozenset({"sudo"}))
        waydroid.host = lambda refresh=False: host
        waydroid.host_known = lambda: host
        waydroid.askpass_helper = lambda: ""
        waydroid.waydroid_cfg = lambda: updates.parse_cfg(CFG)
        waydroid._helper_digest = lambda: "0" * 64
        waydroid._instance_helper = lambda: "/tmp/helper.py"
        waydroid.github_mirror = lambda: ""
        waydroid._android_version = lambda: "13"
        self.work = tempfile.mkdtemp(prefix="merci-test-bridge-")
        waydroid._script_cache = lambda: os.path.join(self.work, "cache")

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(self.w, name, value)
        shutil.rmtree(self.work, ignore_errors=True)

    def test_no_third_party_code_is_run_anywhere(self):
        """waydroid_script в Merci больше не участвует: ни клона, ни pip, ни
        запуска чего-либо из каталога пользователя под root."""
        for name in ("_BRIDGE_TEMPLATE", "_bridge_script", "_SCRIPT_STDERR_FIX",
                     "_helper_installs_bridge"):
            self.assertFalse(hasattr(self.w, name), name)
        steps = [
            self.w.switch_bridge_step("libndk"),
            self.w.switch_bridge_step("libhoudini"),
            self.w._bridge_helper_step("x", "y"),
            self.w._ndk_step("libndk"),
            self.w.magisk_step(),
            self.w.magisk_step(remove=True),
        ]
        for step in steps:
            script = step.argv[-1]
            for word in ("git clone", "waydroid_script", "pip install", "runuser", "main.py"):
                self.assertNotIn(word, script, step.key)
            self.assertFalse(step.root, step.key)  # root — только помощник внутри шага
            parsed = subprocess.run(["sh", "-n", "-c", script], capture_output=True, text=True)
            self.assertEqual(parsed.returncode, 0, parsed.stderr)

    def test_switch_step_fetches_before_it_stops_anything(self):
        for target, name in (("libndk", "libndktranslation.zip"), ("libhoudini", "libhoudini.zip")):
            step = self.w.switch_bridge_step(target)
            script = step.argv[-1]
            self.assertEqual(step.key, "switch-bridge")
            self.assertTrue(step.asks_root and step.then_session)
            self.assertLess(script.index("md5sum"), script.index("останавливаем Waydroid"))
            plain = script.replace("'\\''", "'")
            self.assertIn("'bridge-install'", plain)
            self.assertIn(f"/downloads/{name}'", plain)
            self.assertEqual(step.downloads, name)
            # Загрузку отменить можно; с остановки Waydroid — уже нет.
            self.assertTrue(step.prelude)
            mark = script.index(f"echo {self.w.NO_CANCEL_MARK}")
            self.assertLess(script.index("md5sum"), mark)
            self.assertLess(mark, script.index("останавливаем Waydroid"))
            # Загрузка — не в подоболочке: та пережила бы отмену шага.
            self.assertNotIn("( fetch_archive", script)
            self.assertIn("fetch_archive() {", script)
        # В мастере подготовки архив качает отдельный шаг — у шага помощника
        # отменяемого начала нет.
        self.assertFalse(self.w._bridge_helper_step("x", "y").prelude)
        with self.assertRaises(self.w.WaydroidError):
            self.w.switch_bridge_step("libevil")

    def run_step(self, step, curl="exit 22", sudo_code=0, session="RUNNING"):
        tools = os.path.join(self.work, "bin")
        os.makedirs(tools, exist_ok=True)
        log = os.path.join(self.work, "log")
        open(log, "w").close()
        fakes = {
            "waydroid": f'#!/bin/sh\necho "waydroid $*" >> "{log}"\n'
                        f'[ "$1" = status ] && printf "Session:\\t{session}\\n"\nexit 0\n',
            "pkill": "#!/bin/sh\nexit 1\n",
            "sleep": "#!/bin/sh\nexit 0\n",
            "systemctl": "#!/bin/sh\nexit 0\n",
            "setsid": f'#!/bin/sh\necho "setsid $*" >> "{log}"\nexit 0\n',
            "curl": f'#!/bin/sh\necho "curl" >> "{log}"\n'
                    'while [ $# -gt 0 ]; do [ "$1" = -o ] && out="$2"; shift; done\n'
                    f"{curl}\n",
            "sudo": '#!/bin/sh\nfor a in "$@"; do last="$a"; done\n'
                    f'echo "sudo ${{last##*--home}}" >> "{log}"\nexit {sudo_code}\n',
        }
        for name, body in fakes.items():
            with open(os.path.join(tools, name), "w") as handle:
                handle.write(body)
            os.chmod(os.path.join(tools, name), 0o755)
        home = os.path.join(self.work, "home")
        os.makedirs(home, exist_ok=True)
        env = dict(os.environ, HOME=home, PATH=tools + ":/usr/bin:/bin")
        result = subprocess.run(["sh", "-c", step.argv[-1]], env=env,
                                capture_output=True, text=True)
        time.sleep(0.4)  # подъём сессии сценарий отправляет в фон
        with open(log) as handle:
            return result.returncode, handle.read().splitlines(), result.stdout

    def archive(self, target, data):
        """Подменяет таблицу архивов: адрес-заглушка и сумма наших данных."""
        self.w._ARCHIVES = {target: {
            "file": target + ".zip",
            "13": ("https://example.invalid/archive.zip", hashlib.md5(data).hexdigest()),
        }}
        return os.path.join(self.work, "cache", "waydroid-script", "downloads", target + ".zip")

    def test_switch_with_cached_archive_needs_no_network(self):
        local = self.archive("libndk", b"the archive")
        os.makedirs(os.path.dirname(local))
        with open(local, "wb") as handle:
            handle.write(b"the archive")
        code, calls, out = self.run_step(self.w.switch_bridge_step("libndk"))
        self.assertEqual(code, 0, out)
        self.assertNotIn("curl", calls)
        helper = next(i for i, line in enumerate(calls) if line.startswith("sudo"))
        self.assertIn("'bridge-install'", calls[helper])
        self.assertLess(calls.index("waydroid session stop"), helper)
        self.assertTrue(any(line.startswith("setsid waydroid session start") for line in calls))

    def test_switch_without_archive_fails_before_the_password(self):
        self.archive("libndk", b"the archive")
        code, calls, out = self.run_step(self.w.switch_bridge_step("libndk"), curl="exit 22")
        self.assertEqual(code, 1)
        self.assertIn("архива по этому адресу больше нет", out)
        self.assertEqual(calls.count("curl"), 1)  # отказ сервера не повторяем
        self.assertNotIn("waydroid session stop", calls)
        self.assertFalse(any(line.startswith("sudo") for line in calls), calls)

    def test_missing_curl_is_said_plainly(self):
        self.archive("libndk", b"the archive")
        tools = os.path.join(self.work, "plain")
        os.makedirs(tools)
        for tool in ("sh", "md5sum", "mkdir", "dirname", "cp", "mv", "rm"):
            os.symlink(shutil.which(tool), os.path.join(tools, tool))
        home = os.path.join(self.work, "home")
        os.makedirs(home, exist_ok=True)
        result = subprocess.run(
            [shutil.which("sh"), "-c", "set -e; " + self.w._bridge_fetch("libndk") + "echo GOT"],
            env={"HOME": home, "PATH": tools}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("нет curl", result.stdout)
        self.assertNotIn("связь оборвалась", result.stdout)

    def test_download_gets_stall_and_size_limits(self):
        script = self.w._bridge_fetch("libndk")
        for option in ("--connect-timeout", "--speed-limit", "--speed-time", "--max-filesize"):
            self.assertIn(option, script)
        self.assertNotIn("--retry-all-errors", script)

    def test_cancelled_download_stops_downloading(self):
        """Отмена шага, пока качается архив, не оставляет за собой цикла
        попыток: после сигнала оболочке новых запросов нет."""
        import signal
        import time

        self.archive("libndk", b"the archive")
        tools = os.path.join(self.work, "bin")
        os.makedirs(tools, exist_ok=True)
        log = os.path.join(self.work, "curl.log")
        fakes = {
            "curl": f'#!/bin/sh\necho call >> "{log}"\n/usr/bin/sleep 0.6\nexit 56\n',
            "sleep": "#!/bin/sh\nexit 0\n",
        }
        for name, body in fakes.items():
            with open(os.path.join(tools, name), "w") as handle:
                handle.write(body)
            os.chmod(os.path.join(tools, name), 0o755)
        home = os.path.join(self.work, "home")
        os.makedirs(home, exist_ok=True)
        env = dict(os.environ, HOME=home, PATH=tools + ":/usr/bin:/bin")
        process = subprocess.Popen(
            ["sh", "-c", "set -e; " + self.w._bridge_fetch("libndk")], env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if os.path.exists(log):
                break
            time.sleep(0.02)
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=20)
        time.sleep(1.5)  # будь цикл жив, за это время набежало бы ещё два запроса
        with open(log) as handle:
            self.assertEqual(handle.read().count("call"), 1)

    def test_switch_downloads_then_installs(self):
        self.archive("libndk", b"the archive")
        code, calls, out = self.run_step(self.w.switch_bridge_step("libndk"),
                                         curl='printf "the archive" > "$out"')
        self.assertEqual(code, 0, out)
        self.assertIn("curl", calls)
        self.assertTrue(any(line.startswith("sudo") for line in calls))

    def test_failed_install_brings_waydroid_back(self):
        """Отмена пароля больше не оставляет Waydroid выключенным."""
        local = self.archive("libhoudini", b"the archive")
        os.makedirs(os.path.dirname(local))
        with open(local, "wb") as handle:
            handle.write(b"the archive")
        step = self.w._bridge_helper_step("Обновить транслятор ARM", "x")
        code, calls, _out = self.run_step(step, sudo_code=1, session="RUNNING")
        self.assertEqual(code, 1)
        self.assertTrue(any(line.startswith("setsid waydroid session start") for line in calls))
        code, calls, _out = self.run_step(step, sudo_code=1, session="STOPPED")
        self.assertEqual(code, 1)
        self.assertFalse(any(line.startswith("setsid") for line in calls))

    def test_android_version_is_read_from_the_image_when_nothing_else_knows(self):
        w = self.w
        w._android_version = self.saved["_android_version"]
        remembered = []
        w._prop = lambda name: ""
        w._remembered_sdk = lambda: ""
        w._remember_sdk = remembered.append
        seen = []

        def fake_run(argv, timeout=30):
            seen.append(argv)
            return types.SimpleNamespace(returncode=0, stdout="ro.build.version.sdk=30\n",
                                         stderr="")

        w._run = fake_run
        w._image_sdk_cache.clear()
        self.assertEqual(w._android_version(), "11")
        self.assertEqual(remembered, ["30"])
        self.assertIn("debugfs -c -R", seen[0][-1])
        self.assertIn("/var/lib/waydroid/images/system.img", seen[0][-1])
        # Спрашивают версию помногу раз за шаг — образ читается один раз.
        self.assertEqual(w._android_version(), "11")
        self.assertEqual(len(seen), 1)

        empty = types.SimpleNamespace(returncode=0, stdout="", stderr="")
        # Образ не прочитался — верим памяти, а без неё берём Android 13.
        w._run = lambda argv, timeout=30: empty
        w._image_sdk_cache.clear()
        self.assertEqual(w._android_version(), "13")
        w._remembered_sdk = lambda: "30"
        w._image_sdk_cache.clear()
        self.assertEqual(w._android_version(), "11")
        # Образ сменили при выключенном контейнере: память устарела, образ прав.
        w._run = lambda argv, timeout=30: types.SimpleNamespace(
            returncode=0, stdout="ro.build.version.sdk=33\n", stderr="")
        w._image_sdk_cache.clear()
        self.assertEqual(w._android_version(), "13")
        # Работающий контейнер отвечает сам — образ не трогаем.
        w._run = lambda argv, timeout=30: self.fail("образ читать незачем")
        w._image_sdk_cache.clear()
        w._prop = lambda name: "33"
        self.assertEqual(w._android_version(), "13")
        w._image_sdk_cache.clear()

    def test_both_sides_know_the_same_archives(self):
        """Суммы MD5 в таблице Merci и список помощника не расходятся."""
        helper = load_helper()
        table = self.saved["_ARCHIVES"]
        for target in ("libhoudini", "libndk"):
            self.assertEqual(helper.BRIDGE_ARCHIVES_MD5[table[target]["11"][1]], target)
        self.assertEqual(table["libhoudini"]["13"][1], "37fe0899f1e4da7f9a724cca1c1ab1ea")
        self.assertEqual(table["libndk"]["13"][1], "0b2207c490fcb400aa5c87fcf0d52d38")


if __name__ == "__main__":
    unittest.main()
