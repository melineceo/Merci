"""Проверки того, что действия меню смотрят на состояние: не предлагают
поставить уже стоящее, убрать отсутствующее и переключить то, чего нет.

Запуск из корня проекта:  python3 -m unittest discover -s tests -v

Повод — ошибка с живой машины: пункт «Аппаратное ускорение NVIDIA» всегда
предлагал «включить». Человек с уже работающим ускорением соглашался, пакет
переустанавливался, а настройка сбрасывала исправление мерцания.

Функции состояния проверяются с подменой опроса хоста; обработчики окна — на
подставном «окне», которое только записывает, что у него попросили: ни окон,
ни шагов, ни обращений к машине.
"""

import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_updates import CFG, HAVE_GI, load_helper  # noqa: E402

from merci import hostinfo, updates  # noqa: E402

READY = False
if HAVE_GI:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Gtk

        READY = bool(Gtk.init_check())
    except (ImportError, ValueError):
        READY = False


@unittest.skipUnless(HAVE_GI, "нужен PyGObject")
class StateFunctionsTest(unittest.TestCase):
    def setUp(self):
        from merci import waydroid

        self.w = waydroid
        names = ("nvidia_ready", "waydroid_cfg", "_nvidia_package_version", "_ok",
                 "session_running", "renderer", "nvidia_setup_needed", "host", "_run",
                 "magisk_file_ready")
        self.saved = {name: getattr(waydroid, name) for name in names}
        waydroid.nvidia_ready = lambda: (True, "620.70, открытые модули")
        waydroid.waydroid_cfg = lambda: updates.parse_cfg(
            CFG.replace("nvidia_venus_socket = /run/waydroid-venus/venus.sock",
                        "nvidia_driver_mode = venus"))
        waydroid._nvidia_package_version = lambda: "0.2.0-1"
        waydroid._ok = lambda argv, *a, **k: True
        waydroid.session_running = lambda: True
        waydroid.renderer = lambda: ("аппаратный (через Venus)", True)
        waydroid.nvidia_setup_needed = lambda: False
        waydroid.host = lambda refresh=False: hostinfo.Host(family="arch", init="systemd",
                                                             session="wayland")

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(self.w, name, value)

    def test_working_acceleration_is_recognised(self):
        state = self.w.nvidia_state()
        self.assertTrue(state["installed"])
        self.assertEqual((state["version"], state["mode"]), ("0.2.0-1", "venus"))
        self.assertEqual(state["hardware"], True)
        self.assertFalse(state["needs_setup"])

    def test_stopped_container_is_not_called_software(self):
        """Контейнер выключен: стоит ли ускорение, видно и так, а как он
        рисует — неизвестно; врать «программный» нельзя."""
        self.w.session_running = lambda: False
        self.w.renderer = lambda: self.fail("у выключенного контейнера не спрашиваем")
        state = self.w.nvidia_state()
        self.assertTrue(state["installed"])
        self.assertIsNone(state["hardware"])
        self.assertEqual(state["renderer"], "")

    def test_plain_waydroid_is_not_installed(self):
        self.w.waydroid_cfg = lambda: updates.parse_cfg(
            CFG.replace("nvidia_venus_socket = /run/waydroid-venus/venus.sock\n", ""))
        self.w._nvidia_package_version = lambda: ""
        self.w._ok = lambda argv, *a, **k: False
        state = self.w.nvidia_state()
        self.assertFalse(state["installed"])
        self.assertTrue(state["suitable"] and state["installable"])

    def test_installed_from_source_counts_too(self):
        """Не пакет pacman, а установка из исходников: средство настройки на
        месте и режим записан — значит, стоит."""
        self.w._nvidia_package_version = lambda: ""
        self.assertTrue(self.w.nvidia_state()["installed"])

    def test_leftover_mode_without_the_tool_is_not_installed(self):
        """waydroid-nvidia снят, а строка режима в настройках осталась."""
        self.w._nvidia_package_version = lambda: ""
        self.w._ok = lambda argv, *a, **k: False
        self.assertFalse(self.w.nvidia_state()["installed"])

    def test_other_systems_are_not_offered_the_aur(self):
        self.w.host = lambda refresh=False: hostinfo.Host(family="debian", init="systemd",
                                                           session="wayland")
        self.assertFalse(self.w.nvidia_state()["installable"])

    def test_root_state_is_read_from_the_host(self):
        self.w.magisk_file_ready = lambda: True
        seen = []

        def fake_run(argv, timeout=20):
            seen.append(argv[-1])
            return types.SimpleNamespace(returncode=0, stdout="installed\ntraces\n", stderr="")

        self.w._run = fake_run
        self.assertEqual(self.w.magisk_state(),
                         {"installed": True, "traces": True, "file": True})
        # Смотрим и на файлы Magisk, и на строки запуска, и на приложение.
        for word in ("magisk/magisk64", "bootanim.rc", "overlay/sbin", self.w.MAGISK_PACKAGE):
            self.assertIn(word, seen[0])
        import subprocess

        parsed = subprocess.run(["sh", "-n", "-c", seen[0]], capture_output=True, text=True)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)

        self.w._run = lambda argv, timeout=20: types.SimpleNamespace(
            returncode=0, stdout="traces\n", stderr="")
        self.assertEqual(self.w.magisk_state()["installed"], False)
        self.w._run = lambda argv, timeout=20: types.SimpleNamespace(
            returncode=0, stdout="", stderr="")
        state = self.w.magisk_state()
        self.assertFalse(state["installed"] or state["traces"])

    def test_lost_flicker_fix_is_noticed(self):
        from merci.settings import Settings

        on = CFG.replace("persist.waydroid.use_subsurface = false",
                         "persist.waydroid.use_subsurface = true")
        Settings().set("subsurface_off", False)
        self.w.waydroid_cfg = lambda: updates.parse_cfg(on)
        self.assertFalse(self.w.flicker_fix_lost())  # человек его и не включал
        self.w.remember_flicker_fix()
        self.assertTrue(self.w.flicker_fix_lost())   # включал — а настройка сбросила
        self.w.waydroid_cfg = lambda: updates.parse_cfg(CFG)
        self.assertFalse(self.w.flicker_fix_lost())  # на месте
        Settings().set("subsurface_off", False)


class HelperSubsurfaceTest(unittest.TestCase):
    def setUp(self):
        self.h = load_helper()
        self.dir = tempfile.mkdtemp(prefix="merci-test-")
        on = CFG.replace("persist.waydroid.use_subsurface = false",
                         "persist.waydroid.use_subsurface = true")
        with open(os.path.join(self.dir, "waydroid.cfg"), "w") as handle:
            handle.write(on)
        with open(os.path.join(self.dir, "waydroid_base.prop"), "w") as handle:
            handle.write("ro.adb.secure=1\npersist.waydroid.use_subsurface=true\n")
        self.h.WAYDROID_DIR = self.dir
        self.h.CFG_PATH = os.path.join(self.dir, "waydroid.cfg")
        self.h.BASE_PROP = os.path.join(self.dir, "waydroid_base.prop")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_both_files_are_written(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.h.cmd_subsurface(types.SimpleNamespace(value="false"))
        self.assertEqual(json.loads(out.getvalue().splitlines()[-1]),
                         {"subsurface": "false", "stopped": []})
        with open(self.h.BASE_PROP) as handle:
            base = handle.read()
        self.assertIn("persist.waydroid.use_subsurface=false", base)
        self.assertNotIn("use_subsurface=true", base)
        self.assertIn("ro.adb.secure=1", base)
        with open(self.h.CFG_PATH) as handle:
            cfg = handle.read()
        self.assertIn("persist.waydroid.use_subsurface = false", cfg)
        self.assertNotIn("use_subsurface = true", cfg)
        self.assertIn("ro.adb.secure = 1", cfg)
        self.assertIn("subsurface", self.h.EXCLUSIVE)


class FakeWindow:
    """Подставное окно: записывает, что у него попросили."""

    _closing = False
    _updating = False
    _flicker_warned = False

    def __init__(self):
        self.calls = []

    def _error(self, heading, body):
        self.calls.append({"kind": "error", "heading": heading, "body": body})

    def _confirm_step(self, heading, body, label, step, destructive=False, done_title="",
                      on_result=None):
        self.calls.append({"kind": "confirm", "heading": heading, "body": body,
                           "label": label, "step": step, "done": done_title})

    def _choose(self, heading, body, answers, then, default=""):
        self.calls.append({"kind": "choose", "heading": heading, "body": body,
                           "answers": [answer[0] for answer in answers], "then": then})

    def _toast(self, message, *_args, **_kwargs):
        self.calls.append({"kind": "toast", "message": message})

    def _run_step_later(self, builder, done_title="", on_result=None):
        self.calls.append({"kind": "run", "builder": builder, "done": done_title})

    def _monitor_refresh(self):
        return 144


@unittest.skipUnless(READY, "нужен графический сеанс и PyGObject")
class MenuActionsTest(unittest.TestCase):
    def setUp(self):
        from merci import i18n, waydroid, window

        self.w, self.i18n = waydroid, i18n
        self.language = i18n.language()
        i18n.set_language("ru")
        self.fake = FakeWindow()
        for name in ("_offer_nvidia", "_nvidia_installed", "_offer_magisk", "_offer_unroot",
                     "_offer_bridge_switch", "_offer_flicker_fix", "_flicker_fixed",
                     "_magisk_removed"):
            setattr(self.fake, name,
                    types.MethodType(getattr(window.MerciWindow, name), self.fake))

    def tearDown(self):
        self.i18n.set_language(self.language)

    def nvidia(self, **changes):
        state = {"suitable": True, "driver": "620.70, открытые модули", "installable": True,
                 "installed": True, "version": "0.2.0-1", "mode": "venus",
                 "needs_setup": False, "renderer": "аппаратный (через Venus)",
                 "hardware": True}
        state.update(changes)
        self.fake._offer_nvidia(state)
        return self.fake.calls[-1]

    def test_installed_acceleration_is_not_offered_again(self):
        """Та самая ошибка: «опять пишет — не установлено»."""
        call = self.nvidia()
        self.assertEqual(call["kind"], "choose")
        self.assertEqual(call["heading"], "Аппаратное ускорение уже включено")
        self.assertIn("waydroid-nvidia 0.2.0-1, режим venus", call["body"])
        self.assertIn("аппаратный (через Venus)", call["body"])
        self.assertNotIn("рисует процессором", call["body"])
        self.assertEqual(call["answers"], ["close", "setup"])
        # «Перенастроить» идёт через помощника — тот вернёт настройки Merci.
        call["then"]("setup")
        self.assertIs(self.fake.calls[-1]["builder"], self.w.nvidia_setup_step)
        self.assertFalse(any(c["kind"] == "confirm" for c in self.fake.calls))

    def test_installed_but_broken_says_what_is_wrong(self):
        self.assertIn("повторная", self.nvidia(needs_setup=True)["body"])
        software = self.nvidia(hardware=False, renderer="программный (swiftshader)")
        self.assertIn("программный (swiftshader)", software["body"])
        stopped = self.nvidia(hardware=None, renderer="")
        self.assertIn("Контейнер выключен", stopped["body"])
        self.assertIn("драйвер NVIDIA сейчас не найден", self.nvidia(suitable=False)["body"])

    def test_fresh_machine_is_offered_the_install(self):
        call = self.nvidia(installed=False, version="", mode="")
        self.assertEqual((call["kind"], call["label"]), ("confirm", "Включить"))
        self.assertIn("144 Гц", call["body"])
        self.assertEqual(self.nvidia(installed=False, suitable=False,
                                     driver="драйвер NVIDIA не найден")["kind"], "error")
        other = self.nvidia(installed=False, installable=False)
        self.assertEqual(other["kind"], "error")
        self.assertIn("AUR", other["body"])

    def test_root_install_looks_at_what_is_there(self):
        self.fake._offer_magisk({"installed": True, "traces": True, "file": True})
        call = self.fake.calls[-1]
        self.assertEqual((call["heading"], call["label"]), ("Root уже установлен",
                                                             "Переустановить"))
        self.fake._offer_magisk({"installed": True, "traces": True, "file": False})
        call = self.fake.calls[-1]
        self.assertEqual((call["kind"], call["answers"]), ("choose", ["close"]))
        self.fake._offer_magisk({"installed": False, "traces": False, "file": True})
        self.assertEqual(self.fake.calls[-1]["label"], "Установить")
        self.fake._offer_magisk({"installed": False, "traces": False, "file": False})
        call = self.fake.calls[-1]
        self.assertEqual(call["label"], "Попробовать")
        self.assertIn("удалено с GitHub", call["body"])

    def test_root_removal_looks_at_what_is_there(self):
        self.fake._offer_unroot({"installed": False, "traces": False, "file": True})
        self.assertEqual(self.fake.calls[-1],
                         {"kind": "toast", "message": "Root не установлен — убирать нечего"})
        self.fake._offer_unroot({"installed": False, "traces": True, "file": True})
        call = self.fake.calls[-1]
        self.assertEqual((call["heading"], call["label"]), ("Убрать следы root?",
                                                             "Убрать следы"))
        self.fake._offer_unroot({"installed": True, "traces": True, "file": True})
        self.assertEqual(self.fake.calls[-1]["label"], "Убрать")

    def test_translator_switch_knows_when_there_is_none(self):
        self.fake._offer_bridge_switch("")
        call = self.fake.calls[-1]
        self.assertEqual(call["heading"], "Поставить транслятор ARM?")
        self.assertIn(self.w.DEFAULT_BRIDGE, call["body"])
        self.fake._offer_bridge_switch("libndk")
        self.assertIn("libhoudini", self.fake.calls[-1]["heading"])
        self.fake._offer_bridge_switch("libhoudini")
        self.assertIn("libndk", self.fake.calls[-1]["heading"])

    def test_flicker_fix_is_not_offered_when_already_applied(self):
        self.fake._offer_flicker_fix(False)
        self.assertEqual(self.fake.calls[-1]["kind"], "toast")
        self.fake._offer_flicker_fix(True)
        self.assertEqual(self.fake.calls[-1]["label"], "Исправить")


if __name__ == "__main__":
    unittest.main()
