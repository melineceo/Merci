"""Проверки по итогам разбора «действия не смотрят на состояние».

Запуск из корня проекта:  python3 -m unittest discover -s tests -v

Повод — тот же, что у test_menu_state: человек нажимал на установленное и
слышал «не установлено». Разбор всей программы нашёл ещё два десятка мест
того же рода: настройка, включённая при выключенном контейнере, до него не
доходила, а окно говорило «включено»; повторно добавленный APK затирал
запись; «Выключить» спрашивало про уже выключенное; шаги, удавшись, ничего
о себе не запоминали — и предлагались заново.

Хост не нужен: ядро проверяется с подменой опроса хоста и adb, обработчики
окна — на подставном окне, которое только записывает, что у него попросили.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_updates import CFG, HAVE_GI, load_helper  # noqa: E402

from merci import hostinfo, updates  # noqa: E402
from merci.settings import Settings  # noqa: E402

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


def answer(stdout="", returncode=0):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")


class Stubbed(unittest.TestCase):
    """Общее: подмена функций модуля на время проверки и чистые настройки."""

    module = None

    def stub(self, **replacements):
        for name, value in replacements.items():
            self._saved.setdefault(name, getattr(self.module, name))
            setattr(self.module, name, value)

    def setUp(self):
        self._saved = {}
        for key in ("quick_applied", "updater_restore", "multi_windows", "subsurface_off",
                    "nvidia_configured_for"):
            Settings().set(key, None)
        Settings().eco_mode = False
        Settings().fps_limit = 0
        Settings().hide_android_updater = True

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(self.module, name, value)


@unittest.skipUnless(HAVE_GI, "нужен PyGObject")
class Core(Stubbed):
    def setUp(self):
        from merci import waydroid

        self.module = self.w = waydroid
        super().setUp()
        waydroid._quick_sent.clear()
        waydroid._updater_hidden.clear()
        # Окна: основное и два дополнительных; что работает — решает проверка.
        self.windows = {1: ("RUNNING", "192.168.240.112"), 2: ("STOPPED", ""),
                        3: ("STOPPED", "")}
        self.pids = {"waydroid": 100, "waydroid2": 200, "waydroid3": 300}
        self.sent = []
        self.adb_code = 0
        self.adb_out = ""

        def fake_adb(ip, args, timeout=120):
            self.sent.append((ip, args[-1]))
            return answer(self.adb_out, self.adb_code)

        self.stub(
            instances=lambda owner="": [
                {"number": number, "state": state, "ip": ip}
                for number, (state, ip) in sorted(self.windows.items())
            ],
            _container_pids=lambda: {
                self.w.container_name(number): self.pids[self.w.container_name(number)]
                for number, (state, _ip) in self.windows.items() if state == "RUNNING"
            },
            instance_adb=fake_adb,
            adb_available=lambda: True,
        )

    # -- выключенный контейнер молчит, а не отвечает «нет» -----------------

    def test_silent_container_is_read_from_its_files(self):
        self.stub(
            _prop=lambda name: "",
            _host_file=lambda path: "ro.adb.secure=1\npersist.waydroid.use_subsurface=true\n",
            waydroid_cfg=lambda: updates.parse_cfg(CFG.replace(
                "ro.adb.secure = 1", "ro.adb.secure = 1\nfw.max_users = 4")),
        )
        # Раньше: «Подповерхности уже выключены — эта причина исключена».
        self.assertTrue(self.w.subsurface_flicker())
        # Раньше: «в контейнере не разрешено больше одного пользователя».
        self.assertEqual(self.w.multiuser_ready()[0], True)
        # У работающего по-прежнему спрашиваем его самого.
        self.stub(_prop=lambda name: "false" if "subsurface" in name else "")
        self.assertFalse(self.w.subsurface_flicker())

    def test_window_mode_is_remembered_for_a_stopped_container(self):
        self.stub(_prop=lambda name: "true")
        self.assertTrue(self.w.multi_windows_on())
        self.stub(_prop=lambda name: "")
        self.assertTrue(self.w.multi_windows_on())  # раньше — «выключено»

    def test_window_mode_is_not_reported_until_the_container_took_it(self):
        calls = []
        self.stub(
            ensure_session=lambda: calls.append("up"),
            _run=lambda argv, timeout=20: calls.append(" ".join(argv[1:4])) or answer(),
            _prop=lambda name: "false",
            restart_session=lambda stage=None: calls.append("restart"),
        )
        with self.assertRaises(self.w.WaydroidError):
            self.w.set_multi_windows(True)
        # Сначала поднимаем: до выключенного `prop set` не доходит вовсе.
        self.assertEqual(calls, ["up", "prop set persist.waydroid.multi_windows"])
        self.assertFalse(Settings().get("multi_windows", False))
        self.stub(_prop=lambda name: "true")
        self.w.set_multi_windows(True)
        self.assertEqual(calls[-1], "restart")
        self.assertTrue(Settings().get("multi_windows"))

    def test_new_size_reaches_a_main_window_that_was_off(self):
        """Основной контейнер был выключен, когда меняли размер: его склад
        свойств держит прежний, и общий размер он перебивает. Смотрим, с
        каким размером контейнер поднялся, и правим склад изнутри."""
        self.windows = {1: ("STOPPED", "")}
        calls, sizes = [], [(1280, 720), (1600, 900)]
        self.stub(
            _run=lambda argv, timeout=20: answer(),
            _instance_call=lambda *args, **kwargs: {"stopped": []},
            _apply_size_inside=lambda ip, width, height: calls.append(("inside", ip)),
            restart_session=lambda stage=None: calls.append("restart"),
            container_ip=lambda: "192.168.240.112",
            display_size=lambda ip: sizes.pop(0),
            start_instances=lambda numbers: calls.append(("start", numbers)),
        )
        self.w.set_global_size(1600, 900)
        self.assertEqual(calls, ["restart", ("inside", "192.168.240.112"), "restart"])
        self.assertEqual(Settings().window_size, (1600, 900))
        # Поднялся с новым размером сразу — второй перезапуск не нужен.
        calls.clear()
        sizes[:] = [(1600, 900)]
        self.w.set_global_size(1600, 900)
        self.assertEqual(calls, ["restart"])

    # -- «Подгонка под экран» ------------------------------------------------

    def display(self, running=True, actual=(1280, 720)):
        self.stub(
            global_size=lambda: (0, 0),
            gamescope_works=lambda: False,
            session_running=lambda: running,
            resolution=lambda: actual if running else (0, 0),
            askpass_helper=lambda: "",
            _sudo=lambda helper="": "sudo ",
            _session_snippet=lambda: "true",
        )
        return self.w.display_step((1920, 1080))

    def test_display_fit_is_not_offered_to_a_stopped_container(self):
        """Выключенный отвечал «размер 0×0» — и шаг предлагался при каждом
        открытии мастера, перезапуская Waydroid впустую."""
        self.assertIsNone(self.display(running=False))

    def test_display_fit_is_offered_when_the_size_really_differs(self):
        self.windows = {1: ("RUNNING", "192.168.240.112")}
        step = self.display()
        self.assertEqual(step.key, "display")
        self.assertIn("persist.waydroid.width 1920", step.argv[-1])
        self.assertIsNone(self.display(actual=(1920, 1080)))

    def test_display_fit_leaves_open_windows_alone(self):
        self.windows[2] = ("RUNNING", "192.168.240.113")
        self.assertIsNone(self.display())

    # -- шаги под root: один каркас ------------------------------------------

    def test_root_steps_bring_waydroid_back_whatever_happens(self):
        host = hostinfo.Host(family="arch", init="systemd", session="wayland",
                             tools=frozenset({"sudo"}))
        self.stub(
            host=lambda refresh=False: host, host_known=lambda: host,
            askpass_helper=lambda: "", waydroid_cfg=lambda: updates.parse_cfg(CFG),
            _helper_digest=lambda: "0" * 64, _instance_helper=lambda: "/tmp/helper.py",
        )
        steps = {
            "overlay": (self.w.overlay_step(), "'prepare' '0' '0'"),
            "adb-lock": (self.w.adb_lock_step(), "'secure-adb'"),
            "container-restart": (self.w.container_restart_step(), "'restart-container'"),
            "multiuser": (self.w.multiuser_step(), "'secure-adb' '--users' '4'"),
            "subsurface": (self.w.subsurface_step(), "'subsurface' 'false'"),
        }
        for key, (step, command) in steps.items():
            script = step.argv[-1]
            with self.subTest(step=key):
                self.assertEqual(step.key, key)
                parsed = subprocess.run(["sh", "-n", "-c", script], capture_output=True,
                                        text=True)
                self.assertEqual(parsed.returncode, 0, parsed.stderr)
                self.assertTrue(step.asks_root and step.then_session)
                # Каркас: метка «дальше не отменить», ловушка выхода, которая
                # поднимает сессию и после отказа, работа — у помощника.
                self.assertIn(self.w.NO_CANCEL_MARK, script)
                self.assertIn("trap finish EXIT", script)
                self.assertIn(command, script.replace("'\\''", "'"))
                # Сам шаг службу не трогает и пароль по нескольку раз не просит.
                self.assertNotIn("systemctl restart", script)
                self.assertEqual(script.count("sudo "), 1 + (key == "multiuser"), script)

    def test_cancelled_password_does_not_leave_waydroid_off(self):
        """Шаги исполняются по-настоящему — с поддельными sudo, waydroid и adb.

        Отказ в пароле раньше оставлял Waydroid выключенным («Закрыть adb
        ключом», «Перезапустить контейнер целиком») либо кончался «успехом»
        («Вернуть транслятор ARM64»).
        """
        import time

        host = hostinfo.Host(family="arch", init="systemd", session="wayland",
                             tools=frozenset({"sudo"}))
        self.stub(
            host=lambda refresh=False: host, host_known=lambda: host,
            askpass_helper=lambda: "", waydroid_cfg=lambda: updates.parse_cfg(CFG),
            _helper_digest=lambda: "0" * 64, _instance_helper=lambda: "/tmp/helper.py",
        )
        work = tempfile.mkdtemp(prefix="merci-test-steps-")
        self.addCleanup(shutil.rmtree, work, True)
        tools, log = os.path.join(work, "bin"), os.path.join(work, "log")
        os.makedirs(tools)

        def run(step, sudo_code, session):
            open(log, "w").close()
            fakes = {
                "waydroid": f'#!/bin/sh\necho "waydroid $*" >> "{log}"\n'
                            f'[ "$1" = status ] && printf "Session:\\t{session}\\n"\nexit 0\n',
                "pkill": "#!/bin/sh\nexit 1\n",
                "sleep": "#!/bin/sh\nexit 0\n",
                "systemctl": f'#!/bin/sh\necho "systemctl $*" >> "{log}"\nexit 0\n',
                "adb": f'#!/bin/sh\necho "adb $*" >> "{log}"\nexit 0\n',
                "setsid": f'#!/bin/sh\necho "setsid $*" >> "{log}"\nexit 0\n',
                "sudo": f'#!/bin/sh\necho "sudo" >> "{log}"\nexit {sudo_code}\n',
            }
            for name, body in fakes.items():
                with open(os.path.join(tools, name), "w") as handle:
                    handle.write(body)
                os.chmod(os.path.join(tools, name), 0o755)
            env = dict(os.environ, HOME=work, PATH=tools + ":/usr/bin:/bin")
            result = subprocess.run(["sh", "-c", step.argv[-1]], env=env,
                                    capture_output=True, text=True)
            time.sleep(0.4)  # подъём сессии сценарий отправляет в фон
            with open(log) as handle:
                return result.returncode, handle.read().splitlines()

        def started(calls):
            return any(line.startswith("setsid waydroid session start") for line in calls)

        builders = (self.w.overlay_step, self.w.adb_lock_step,
                    self.w.container_restart_step, self.w.multiuser_step,
                    self.w.subsurface_step)
        for build in builders:
            step = build()
            with self.subTest(step=step.key):
                code, calls = run(step, sudo_code=1, session="RUNNING")
                self.assertEqual(code, 1)                 # отказ — это отказ
                self.assertEqual(calls.count("sudo"), 1)  # и спросили один раз
                self.assertTrue(started(calls), calls)    # Waydroid снова работает
                code, calls = run(step, sudo_code=1, session="STOPPED")
                self.assertEqual(code, 1)
                self.assertFalse(started(calls), calls)   # не работал — и не будим
                code, calls = run(step, sudo_code=0, session="STOPPED")
                self.assertEqual(code, 0)
                self.assertTrue(started(calls), calls)
                # Службой и контейнерами занимается помощник, не сценарий.
                self.assertFalse(any("systemctl restart" in line for line in calls))

    # -- память шага ---------------------------------------------------------

    def test_steps_remember_what_they_did(self):
        host = hostinfo.Host(family="arch", init="systemd", session="wayland",
                             tools=frozenset({"sudo"}))
        self.stub(
            host=lambda refresh=False: host, host_known=lambda: host,
            askpass_helper=lambda: "", waydroid_cfg=lambda: updates.parse_cfg(CFG),
            _helper_digest=lambda: "0" * 64, _instance_helper=lambda: "/tmp/helper.py",
        )
        self.assertIs(self.w.nvidia_setup_step().after, self.w.remember_nvidia_setup)
        self.assertIs(self.w.nvidia_step(144).after, self.w.remember_nvidia_setup)
        package = updates.Update(key="package", title="x", data={
            "name": "waydroid-nvidia-bin", "source": "aur", "nvidia": True})
        self.assertIs(self.w.nvidia_update_step(package).after, self.w.remember_nvidia_setup)
        self.assertIs(self.w.subsurface_step().after, self.w.remember_flicker_fix)
        self.assertIsNone(self.w.overlay_step().after)

    def test_after_runs_once_and_only_on_success(self):
        self.stub(in_thread=lambda work, done=None: done(*self.attempt(work)))
        seen, reports = [], []

        def runner(after):
            step = self.w.Step(key="x", title="x", argv=["true"], after=after)
            return self.w.StepRunner(step, lambda line: None, reports.append)

        runner(lambda: seen.append("remembered"))._finish_step(True)
        self.assertEqual((seen, reports), (["remembered"], [True]))
        runner(lambda: seen.append("no"))._finish_step(False)
        self.assertEqual((seen, reports), (["remembered"], [True, False]))
        # Память подвела — шаг всё равно удался: работа сделана.
        runner(lambda: 1 / 0)._finish_step(True)
        self.assertEqual(reports, [True, False, True])
        runner(None)._finish_step(True)
        self.assertEqual(reports[-1], True)

    def test_step_is_not_done_until_the_container_is_up(self):
        """Шаг с подъёмом сессии объявлялся удавшимся через три секунды
        после конца команды, поднялся контейнер или нет: страховочный
        таймер проводил конец команды второй раз. Заодно шаг «запоминал»
        настройку ускорения, с которой контейнер так и не стартовал."""
        up = []
        self.stub(in_thread=lambda work, done=None: done(*self.attempt(work)),
                  status=lambda use_cache=True: (bool(up), "сессия остановлена"),
                  forget_state=lambda: None)
        seen, reports, lines = [], [], []
        step = self.w.Step(key="x", title="x", argv=["true"], asks_root=True,
                           then_session=True, after=lambda: seen.append("remembered"))
        runner = self.w.StepRunner(step, lines.append, reports.append)
        # Команда вышла с кодом 0, вывод дочитан.
        runner._exited, runner._eof, runner._code = True, True, 0
        runner._maybe_finish()
        self.assertEqual((seen, reports), ([], []))
        runner._force_finish()                         # страховочный таймер
        runner._on_eof()                               # и запоздавший конец вывода
        self.assertEqual((seen, reports), ([], []))
        self.assertEqual(lines.count("код возврата: 0"), 1)
        # Контейнер ещё не поднялся — ждём; поднялся — шаг удался.
        self.assertTrue(runner._await_ready())
        self.assertEqual((seen, reports), ([], []))
        up.append(True)
        runner._await_ready()
        self.assertEqual((seen, reports), (["remembered"], [True]))
        # Не поднялся за две минуты — это неудача, и запоминать нечего.
        up.clear()
        seen.clear()
        reports.clear()
        slow = self.w.StepRunner(step, lines.append, reports.append)
        slow._exited, slow._eof, slow._code = True, True, 0
        slow._maybe_finish()
        for _ in range(45):
            slow._await_ready()
        self.assertEqual((seen, reports), ([], [False]))
        self.assertIn("контейнер не поднялся за две минуты", lines)
        # Пока ждём контейнер, «вывода пока нет» не говорим; а попади такая
        # строка в журнал раньше — в причину неудачи она не идёт.
        self.assertFalse(slow._nudge())
        from merci.installer import failure_reason

        self.assertEqual(
            failure_reason(["$ sh -c …", "поднимаем Waydroid", "код возврата: 0",
                            "вывода пока нет — шаг занимает время",
                            "контейнер не поднялся за две минуты"]),
            "контейнер не поднялся за две минуты",
        )

    @staticmethod
    def attempt(work):
        try:
            return work(), None
        except Exception as error:  # noqa: BLE001 — так же делает in_thread
            return None, error

    def test_configured_acceleration_is_not_offered_again(self):
        """Настройку прошли — «Перенастроить» больше не предлагается."""
        log = os.path.join(tempfile.mkdtemp(prefix="merci-test-"), "session.log")
        with open(log, "w") as handle:
            handle.write("запустите waydroid-nvidia-setup\n")
        self.addCleanup(shutil.rmtree, os.path.dirname(log), True)
        self.stub(
            _nvidia_package_version=lambda: "0.2.0-1",
            _session_log=lambda: log,
            _ok=lambda argv, *a, **k: True,
            _run=lambda argv, timeout=20: answer(
                subprocess.run(["tail", "-n", "40", log], capture_output=True,
                               text=True).stdout),
        )
        Settings().set("nvidia_configured_for", "0.1.2-1")
        self.assertTrue(self.w.nvidia_setup_needed())
        self.w.remember_nvidia_setup()
        self.assertFalse(self.w.nvidia_setup_needed())
        self.assertEqual(Settings().get("nvidia_configured_for"), "0.2.0-1")

    # -- режим экономии и предел кадров -------------------------------------

    def test_nothing_is_asked_when_nothing_is_set(self):
        self.stub(instances=lambda owner="": self.fail("хост спрашивать незачем"))
        self.assertEqual(self.w.sync_quick_settings(), (False, False))

    def test_eco_mode_waits_for_a_stopped_container(self):
        self.windows[1] = ("STOPPED", "")
        Settings().eco_mode = True
        self.assertEqual(self.w.apply_eco_mode(True), (False, True))
        self.assertEqual(self.sent, [])
        # Контейнер поднялся — настройка доходит сама.
        self.windows[1] = ("RUNNING", "192.168.240.112")
        self.w.sync_android_settings()
        scripts = [script for _ip, script in self.sent if "animation_scale" in script]
        self.assertEqual(len(scripts), 1)
        self.assertIn("settings put global window_animation_scale 0", scripts[0])
        self.assertIn("animator_duration_scale 0", scripts[0])

    def test_settings_are_sent_once_per_boot(self):
        Settings().fps_limit = 60
        self.assertEqual(self.w.set_fps_limit(60)[0], True)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("peak_refresh_rate 60", self.sent[0][1])
        self.assertIn("setprop debug.sf.fps 60", self.sent[0][1])
        self.w.sync_quick_settings()
        self.assertEqual(len(self.sent), 1)          # то же окно, та же загрузка
        self.pids["waydroid"] = 101                   # контейнер перезапустили
        self.w.sync_quick_settings()
        self.assertEqual(len(self.sent), 2)          # предел кадров живёт до выключения

    def test_switched_off_setting_is_undone_where_it_was_left(self):
        self.windows[2] = ("RUNNING", "192.168.240.113")
        Settings().eco_mode = True
        self.w.apply_eco_mode(True)
        self.assertEqual({ip for ip, _ in self.sent}, {"192.168.240.112", "192.168.240.113"})
        # Окно 2 выключили, потом выключили режим.
        self.windows[2] = ("STOPPED", "")
        self.sent.clear()
        Settings().eco_mode = False
        self.assertEqual(Settings().get("quick_applied")["eco"], [1, 2])
        self.assertEqual(self.w.apply_eco_mode(False), (True, True))
        self.assertEqual([ip for ip, _ in self.sent], ["192.168.240.112"])
        self.assertIn("window_animation_scale 1", self.sent[0][1])
        self.assertEqual(Settings().get("quick_applied")["eco"], [2])
        # Окно 2 поднялось: анимации возвращаются и в нём.
        self.windows[2] = ("RUNNING", "192.168.240.113")
        self.sent.clear()
        self.assertEqual(self.w.sync_quick_settings(), (True, False))
        self.assertEqual([ip for ip, _ in self.sent], ["192.168.240.113"])
        self.assertEqual(Settings().get("quick_applied")["eco"], [])
        # Окно 3 режима не видело — его настройки анимаций Merci не трогает.
        self.windows[3] = ("RUNNING", "192.168.240.114")
        self.sent.clear()
        self.assertEqual(self.w.sync_quick_settings(), (False, False))
        self.assertEqual(self.sent, [])

    def test_setting_that_never_arrived_is_not_undone(self):
        """Включили при выключенном контейнере и выключили обратно: до
        Android ничего не дошло — и возвращать в нём нечего."""
        self.windows[1] = ("STOPPED", "")
        self.w.note_quick_settings()                  # так делает окно перед сменой
        Settings().eco_mode = True
        self.assertEqual(self.w.apply_eco_mode(True), (False, True))
        Settings().eco_mode = False
        self.assertEqual(self.w.apply_eco_mode(False), (False, False))
        self.windows[1] = ("RUNNING", "192.168.240.112")
        self.w.sync_quick_settings()
        self.assertEqual(self.sent, [])

    def test_setting_left_by_the_previous_version_is_undone(self):
        """Прежняя версия слала настройки только в основное окно и не
        помнила об этом: включённое при ней выключается там же."""
        self.windows[1] = ("STOPPED", "")
        Settings().eco_mode = True                    # включено ещё прежней версией
        self.assertEqual(self.w.sync_quick_settings(), (False, True))
        Settings().eco_mode = False
        self.assertEqual(self.w.apply_eco_mode(False), (False, True))
        self.windows[1] = ("RUNNING", "192.168.240.112")
        self.assertEqual(self.w.sync_quick_settings(), (True, False))
        self.assertIn("window_animation_scale 1", self.sent[0][1])

    def test_fps_limit_is_removed(self):
        Settings().fps_limit = 30
        self.w.set_fps_limit(30)
        self.sent.clear()
        Settings().fps_limit = 0
        self.assertEqual(self.w.set_fps_limit(0)[0], True)
        self.assertIn("settings delete system peak_refresh_rate", self.sent[0][1])
        self.assertIn("setprop debug.sf.fps 0", self.sent[0][1])

    def test_refused_setting_is_not_reported_as_applied(self):
        self.adb_code = 1
        Settings().eco_mode = True
        self.assertEqual(self.w.apply_eco_mode(True), (False, True))
        self.adb_code = 0
        self.assertEqual(self.w.sync_quick_settings()[0], True)   # и повторяется
        self.assertEqual(len(self.sent), 2)

    def test_one_time_actions_say_whether_android_heard_them(self):
        self.assertTrue(self.w.trim_android_memory())
        self.assertTrue(self.w.apply_multiwindow_optimizations())
        self.windows[1] = ("STOPPED", "")
        self.assertFalse(self.w.trim_android_memory())
        self.assertFalse(self.w.apply_multiwindow_optimizations())

    # -- встроенный обновлятор ----------------------------------------------

    def test_updater_is_hidden_again_in_a_recreated_window(self):
        self.adb_out = "HID\n"
        self.assertEqual(self.w.hide_android_updater(), 1)
        self.assertEqual(self.w.hide_android_updater(), 0)     # уже спрятан
        self.assertEqual(len(self.sent), 1)
        # Тот же адрес, но процесс контейнера другой: Android уже не тот.
        self.pids["waydroid"] = 555
        self.assertEqual(self.w.hide_android_updater(), 1)
        self.assertEqual(len(self.sent), 2)

    def test_hide_script_succeeds_where_the_updater_is_already_off(self):
        """Сценарий скрытия исполняется по-настоящему, с поддельным pm.
        Раньше он выходил с кодом 1 там, где обновлятор уже выключен: окно
        выглядело отказавшим, переспрашивалось после каждой операции, а
        переключатель отвечал «скроется при запуске» про уже скрытое."""
        self.w.hide_android_updater()
        script = self.sent[-1][1]
        pm = (
            'pm() { case "$1 $2" in '
            '"list users") echo "UserInfo{0:Owner:c13} running"; '
            'echo "UserInfo{10:x:410} running";; '
            '"list packages") [ "$STATE" = on ] && echo "package:%s";; '
            '"disable-user"*) return 0;; esac; }; ' % self.w.UPDATER_PACKAGE
        )
        for state, hidden in (("on", 2), ("off", 0)):
            result = subprocess.run(["sh", "-c", pm + script], capture_output=True,
                                    text=True, env=dict(os.environ, STATE=state))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count("HID"), hidden)

    def test_already_hidden_window_counts_as_reached(self):
        self.adb_out = ""                              # прятать было нечего
        self.assertTrue(self.w.apply_updater_choice(True))
        self.assertEqual(len(self.sent), 1)
        self.w.sync_android_settings()
        self.assertEqual(len(self.sent), 1)            # и больше не спрашиваем

    def test_updater_is_not_hidden_after_the_owner_changed_his_mind(self):
        """Скрытие, стоявшее в очереди за возвратом, не прячет возвращённое."""
        Settings().hide_android_updater = False
        self.assertEqual(self.w.hide_android_updater(), 0)
        self.assertEqual(self.sent, [])
        # И наоборот: возврат, дождавшийся очереди после «скрывать снова».
        Settings().set("updater_restore", [2])
        Settings().hide_android_updater = True
        self.assertEqual(self.w.show_android_updater(), (False, False))
        self.assertEqual(self.sent, [])
        self.assertEqual(Settings().get("updater_restore"), [])

    def test_updater_comes_back_in_windows_that_were_off(self):
        self.windows[2] = ("RUNNING", "192.168.240.113")
        Settings().hide_android_updater = False
        self.assertTrue(self.w.apply_updater_choice(False))
        self.assertEqual({ip for ip, _ in self.sent}, {"192.168.240.112", "192.168.240.113"})
        self.assertTrue(all("pm enable" in script for _ip, script in self.sent))
        self.assertEqual(Settings().get("updater_restore"), [3])
        # Окно 3 поднялось — обновлятор возвращается и в нём, без напоминаний.
        self.windows[3] = ("RUNNING", "192.168.240.114")
        self.sent.clear()
        self.w.sync_android_settings()
        self.assertEqual([ip for ip, _ in self.sent], ["192.168.240.114"])
        self.assertEqual(Settings().get("updater_restore"), [])
        self.sent.clear()
        self.w.sync_android_settings()
        self.assertEqual(self.sent, [])

    def test_nothing_running_is_not_called_returned(self):
        self.windows[1] = ("STOPPED", "")
        Settings().hide_android_updater = False       # выбор окно записывает первым
        self.assertFalse(self.w.apply_updater_choice(False))
        self.assertEqual(Settings().get("updater_restore"), [1, 2, 3])
        # Передумали: скрывать снова — и возвращать уже нечего.
        Settings().hide_android_updater = True
        self.assertFalse(self.w.apply_updater_choice(True))
        self.assertEqual(Settings().get("updater_restore"), [])

    # -- что работает --------------------------------------------------------

    def test_only_a_really_stopped_waydroid_is_called_off(self):
        self.stub(status=lambda use_cache=True: (False, "сессия остановлена"))
        self.assertTrue(self.w.certainly_off())
        self.windows[2] = ("RUNNING", "192.168.240.113")
        self.assertFalse(self.w.certainly_off())          # окно ещё работает
        self.windows[2] = ("STOPPED", "")
        self.stub(status=lambda use_cache=True: (True, "сессия запущена"))
        self.assertFalse(self.w.certainly_off())
        # Завис и не отвечает — выключить его нужнее всего.
        self.stub(status=lambda use_cache=True: (False, "контейнер не отвечает"))
        self.assertFalse(self.w.certainly_off())

    def test_open_entries_names_what_is_on_screen(self):
        self.windows[2] = ("RUNNING", "192.168.240.113")
        catalog = [("roblox-aaaaaaaaaaaa", "com.roblox.client", "aaaaaaaaaaaa"),
                   ("other-bbbbbbbbbbbb", "org.other", "bbbbbbbbbbbb")]
        showing = {"192.168.240.112": ("org.other", True),
                   "192.168.240.113": ("com.roblox.client", True)}
        self.stub(_registry=lambda: {2: "roblox-aaaaaaaaaaaa", 3: "roblox-aaaaaaaaaaaa"},
                  _showing=lambda ip: showing.get(ip))
        self.assertEqual(self.w.open_entries(catalog),
                         {"other-bbbbbbbbbbbb": 1, "roblox-aaaaaaaaaaaa": 1})
        showing["192.168.240.112"] = (self.w.IDLE_SCREEN, True)
        showing["192.168.240.113"] = ("com.roblox.client", False)   # окна на экране нет
        self.assertEqual(self.w.open_entries(catalog), {})

    def test_container_pids_come_from_one_ps(self):
        listing = (
            "    812 /usr/bin/lxc-start -P /var/lib/waydroid/lxc -F -n waydroid -- /init\n"
            "   4410 lxc-start -P /var/lib/waydroid/lxc -F -n waydroid3 -- /init\n"
            "   5000 grep lxc-start -n waydroid9\n"
            "   6000 /usr/bin/python3 /usr/bin/waydroid session start\n"
        )
        self.stub(_container_pids=self._saved["_container_pids"],
                  _run=lambda argv, timeout=20: answer(listing))
        self.assertEqual(self.w._container_pids(), {"waydroid": 812, "waydroid3": 4410})
        self.assertEqual(self.w._running_containers(), {"waydroid", "waydroid3"})

    # -- удаление и отчёты о падениях ----------------------------------------

    def test_absent_app_is_not_uninstalled(self):
        from merci.library import Entry

        calls, errors = [], []
        self.stub(
            in_thread=lambda work, done=None: done(*self.attempt(work)),
            installed_build=lambda package, ip=None: ("", ""),
            uninstall_apk=lambda package: calls.append(package),
            uninstall_for_user=lambda package, user: calls.append((package, user)),
        )
        entry = Entry(slug="x-0123456789ab", name="X", package="org.x")
        self.w.uninstall_async(entry, errors.append)
        self.assertEqual(calls, [])
        self.assertIn("не установлено", str(errors[0]))
        # Стоит — снимаем; спросить не удалось — пробуем как раньше.
        for found in (("0123456789ab", "1.0"), (None, "")):
            self.stub(installed_build=lambda package, ip=None, found=found: found)
            self.w.uninstall_async(entry, errors.append)
        self.assertEqual(calls, ["org.x", "org.x"])
        self.assertEqual(errors[1:], [None, None])

    def test_crash_details_are_the_report_that_was_found(self):
        scripts = []

        def run(argv, timeout=20):
            scripts.append(argv)
            return answer(
                "/home/u/.local/share/waydroid3/data/tombstones/tombstone_04\n"
                "signal 11 (SIGSEGV)\n"
                "      #00 pc 0001 /system/lib64/libndk_translation.so\n"
                "MERCI_REPORT\n"
                "Cmdline: com.roblox.client\nbacktrace:\n  #00 pc 0001\n"
            )

        self.stub(in_thread=lambda work, done=None: done(*self.attempt(work)), _run=run)
        got = []
        self.w.recent_crash_async("com.roblox.client", 1700000000.5,
                                  lambda *found: got.append(found), 3)
        summary, report = got[0]
        self.assertIn("libndk_translation", summary)
        self.assertTrue(report.startswith("/home/u/.local/share/waydroid3/"))
        self.assertIn("backtrace:", report)
        # Окно 3 — его каталог, а не основного; имя пакета — отдельным словом.
        script, _zero, package, since = scripts[0][2:]
        self.assertIn('"$HOME"/.local/share/waydroid3/data/tombstones', script)
        self.assertEqual((package, since), ("com.roblox.client", "1700000000"))
        parsed = subprocess.run(["sh", "-n", "-c", script], capture_output=True, text=True)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)

    def test_java_crash_gives_its_own_lines(self):
        log = ("11-15 01:00:00.000 E AndroidRuntime: FATAL EXCEPTION: main\n"
               "11-15 01:00:00.001 E AndroidRuntime: Process: org.x, PID: 77\n"
               "11-15 01:00:00.002 E AndroidRuntime: java.lang.UnsatisfiedLinkError: no lib\n")
        self.adb_out = log
        self.stub(in_thread=lambda work, done=None: done(*self.attempt(work)),
                  _run=lambda argv, timeout=20: answer(""),
                  container_ip=lambda: "192.168.240.112")
        got = []
        self.w.recent_crash_async("org.x", 1700000000, lambda *found: got.append(found))
        self.assertEqual(got[0][0], "logcat\njava.lang.UnsatisfiedLinkError: no lib")
        self.assertIn("FATAL EXCEPTION", got[0][1])
        self.assertIn("UnsatisfiedLinkError", got[0][1])
        # Ничего не упало — оба ответа пусты.
        self.adb_out = ""
        self.w.recent_crash_async("org.x", 1700000000, lambda *found: got.append(found))
        self.assertEqual(got[1], ("", ""))

    def test_crash_log_leaves_the_words_to_the_window(self):
        self.stub(in_thread=lambda work, done=None: done(*self.attempt(work)))
        got = []
        self.stub(_run=lambda argv, timeout=20: answer(""))
        self.w.crash_log_async(lambda *found: got.append(found))
        self.assertEqual(got[0], ("", ""))           # отчётов нет — и слов нет
        scripts = []

        def run(argv, timeout=20):
            scripts.append(argv[-1])
            return answer("/data/tombstone_01\nline one\nline two\n")

        self.stub(_run=run)
        self.w.crash_log_async(lambda *found: got.append(found))
        self.assertEqual(got[1], ("/data/tombstone_01", "line one\nline two"))
        self.assertNotRegex(scripts[0], "[а-яА-Я]")
        parsed = subprocess.run(["sh", "-n", "-c", scripts[0]], capture_output=True, text=True)
        self.assertEqual(parsed.returncode, 0, parsed.stderr)


class LibraryDuplicateTest(unittest.TestCase):
    def setUp(self):
        from merci import apk, library

        self.library = library
        self.apk = apk
        self.saved = (apk.inspect, apk.extract_icon)
        apk.inspect = lambda path: types.SimpleNamespace(
            package="org.x", label="Из манифеста", version_name="1.0", version_code="1",
            launch_activity="org.x.Main", abis=["x86_64"], icon_entry="")
        apk.extract_icon = lambda source, entry, target: None
        self.dir = tempfile.mkdtemp(prefix="merci-test-")
        self.source = os.path.join(self.dir, "game.apk")
        with open(self.source, "wb") as handle:
            handle.write(b"apk-bytes" * 100)
        self.lib = library.Library()

    def tearDown(self):
        self.apk.inspect, self.apk.extract_icon = self.saved
        for entry in self.lib.load():
            if entry.package == "org.x":
                self.lib.remove(entry)
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_same_file_twice_leaves_the_entry_alone(self):
        entry = self.lib.add(self.source)
        entry.name, entry.android_user, entry.last_run = "Моё имя", 2, 123.0
        self.lib.save(entry)
        with open(os.path.join(entry.android_data, "data.bin"), "w") as handle:
            handle.write("save")
        with self.assertRaises(self.library.AlreadyInLibrary) as caught:
            self.lib.add(self.source)
        self.assertEqual(caught.exception.entry.slug, entry.slug)
        kept = next(e for e in self.lib.load() if e.slug == entry.slug)
        self.assertEqual((kept.name, kept.android_user, kept.last_run), ("Моё имя", 2, 123.0))
        self.assertTrue(os.path.exists(os.path.join(kept.android_data, "data.bin")))

    def test_broken_copy_is_repaired_without_losing_settings(self):
        entry = self.lib.add(self.source)
        entry.name, entry.android_user, entry.last_run = "Моё имя", 2, 123.0
        self.lib.save(entry)
        with open(os.path.join(entry.android_data, "data.bin"), "w") as handle:
            handle.write("save")
        with open(entry.apk_path, "wb") as handle:
            handle.write(b"half")                      # копия оборвана
        again = self.lib.add(self.source)
        self.assertEqual(again.slug, entry.slug)
        self.assertEqual((again.name, again.android_user, again.last_run),
                         ("Моё имя", 2, 123.0))
        self.assertEqual(os.path.getsize(again.apk_path), os.path.getsize(self.source))
        self.assertTrue(os.path.exists(os.path.join(again.android_data, "data.bin")))


    def test_unreadable_record_keeps_what_lies_in_its_folder(self):
        """Сведения о записи испорчены — для библиотеки её как бы нет. Раньше
        добавление того же APK стирало её каталог целиком."""
        entry = self.lib.add(self.source)
        with open(os.path.join(entry.android_data, "save.dat"), "w") as handle:
            handle.write("save")
        with open(os.path.join(entry.directory, "meta.json"), "w") as handle:
            handle.write("{ это не json")
        self.assertEqual([e for e in self.lib.load() if e.slug == entry.slug], [])
        again = self.lib.add(self.source)
        self.assertEqual(again.slug, entry.slug)
        self.assertTrue(os.path.exists(os.path.join(again.android_data, "save.dat")))
        self.assertEqual(len([e for e in self.lib.load() if e.slug == entry.slug]), 1)

    def test_linked_record_folder_is_repaired_in_place(self):
        """Каталог записи — ссылка на другой диск. Ссылку каталогом не
        заменить: починка падала на середине, оставив данные во временном
        каталоге, а следующая попытка их стирала."""
        entry = self.lib.add(self.source)
        elsewhere = os.path.join(self.dir, "other-disk")
        os.replace(entry.directory, elsewhere)
        os.symlink(elsewhere, entry.directory)
        with open(os.path.join(entry.android_data, "save.dat"), "w") as handle:
            handle.write("save")
        with open(entry.apk_path, "wb") as handle:
            handle.write(b"half")
        again = self.lib.add(self.source)
        self.assertTrue(os.path.islink(entry.directory))
        self.assertEqual(os.path.getsize(again.apk_path), os.path.getsize(self.source))
        self.assertTrue(os.path.exists(os.path.join(elsewhere, "android", "save.dat")))
        self.assertFalse(os.path.exists(entry.directory + ".incomplete"))
        with self.assertRaises(self.library.AlreadyInLibrary):
            self.lib.add(self.source)
        os.remove(entry.directory)                     # tearDown ссылки не удаляет
        shutil.rmtree(elsewhere)

    def test_same_file_dropped_twice_at_once_is_added_once(self):
        """Два одинаковых файла, выбранные разом, копируются потоками и
        делили бы один временный каталог."""
        import threading

        twin = os.path.join(self.dir, "copy of game.apk")
        shutil.copyfile(self.source, twin)
        results = []

        def add(path):
            try:
                results.append(self.lib.add(path).slug)
            except self.library.AlreadyInLibrary as known:
                results.append("already:" + known.entry.slug)
            except Exception as error:  # noqa: BLE001 — любая другая и есть провал
                results.append(repr(error))

        threads = [threading.Thread(target=add, args=(path,)) for path in (self.source, twin)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        slug = next(result for result in results if not result.startswith("already:"))
        self.assertEqual(sorted(results), sorted([slug, "already:" + slug]))
        entry = next(e for e in self.lib.load() if e.slug == slug)
        self.assertEqual(os.path.getsize(entry.apk_path), os.path.getsize(self.source))

    def test_failed_repair_leaves_no_half_copied_file(self):
        entry = self.lib.add(self.source)
        with open(entry.apk_path, "wb") as handle:
            handle.write(b"half")

        def broken(source, icon, target):
            raise OSError("диск полон")

        self.apk.extract_icon = broken
        with self.assertRaises(OSError):
            self.lib.add(self.source)
        self.assertFalse(os.path.exists(os.path.join(entry.directory, "app.apk.incomplete")))
        self.assertTrue(os.path.isdir(entry.android_data))


class HelperRestartTest(unittest.TestCase):
    def setUp(self):
        self.h = load_helper()
        self.calls = []
        self.h.stop_everything = lambda: self.calls.append("stop")
        self.h.fix_overlays = lambda: self.calls.append("overlays")

    def run_restart(self, works):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        self.h.container_service = lambda action: self.calls.append(action) or works
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.h.cmd_restart_container(types.SimpleNamespace())
        return out.getvalue()

    def test_restart_goes_through_the_service_of_this_system(self):
        self.assertIn('"restarted"', self.run_restart(True))
        self.assertEqual(self.calls, ["stop", "overlays", "restart"])

    def test_failed_restart_is_a_failure(self):
        """Раньше служба могла не перезапуститься, а шаг кончался «успехом»."""
        with self.assertRaises(SystemExit) as caught:
            self.run_restart(False)
        self.assertNotEqual(caught.exception.code, 0)
        self.assertIn("restart-container", self.h.EXCLUSIVE)


class HelperFlickerTest(unittest.TestCase):
    """Шаг «Исправить мерцание» следом перезапускает основную сессию. Под
    работающими дополнительными окнами Waydroid на это выключает оверлей —
    и транслятор ARM пропадает во всех окнах."""

    def setUp(self):
        self.h = load_helper()
        self.dir = tempfile.mkdtemp(prefix="merci-test-")
        with open(os.path.join(self.dir, "waydroid.cfg"), "w") as handle:
            handle.write(CFG.replace("mount_overlays = True", "mount_overlays = False")
                         .replace("use_subsurface = false", "use_subsurface = true"))
        with open(os.path.join(self.dir, "waydroid_base.prop"), "w") as handle:
            handle.write("persist.waydroid.use_subsurface=true\n")
        self.h.WAYDROID_DIR = self.dir
        self.h.CFG_PATH = os.path.join(self.dir, "waydroid.cfg")
        self.h.BASE_PROP = os.path.join(self.dir, "waydroid_base.prop")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_extra_windows_are_stopped_and_the_overlay_is_restored(self):
        import io
        import json
        from contextlib import redirect_stderr, redirect_stdout

        running, order = {2, 3}, []
        self.h.known_instances = lambda: [1, 2, 3]
        self.h.lxc_state = lambda number: "RUNNING" if number in running else "STOPPED"

        def stop(number):
            running.discard(number)
            order.append(number)

        self.h.stop_container = stop
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            self.h.cmd_subsurface(types.SimpleNamespace(value="false"))
        self.assertEqual(json.loads(out.getvalue().splitlines()[-1]),
                         {"subsurface": "false", "stopped": [2, 3]})
        self.assertEqual(order, [2, 3])
        with open(self.h.CFG_PATH) as handle:
            cfg = handle.read()
        self.assertIn("mount_overlays = True", cfg)
        self.assertIn("persist.waydroid.use_subsurface = false", cfg)
        with open(self.h.BASE_PROP) as handle:
            self.assertIn("persist.waydroid.use_subsurface=false", handle.read())

    def test_window_that_will_not_stop_stops_the_step(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        self.h.known_instances = lambda: [1, 2]
        self.h.lxc_state = lambda number: "RUNNING" if number == 2 else "STOPPED"
        err = io.StringIO()
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), redirect_stderr(err):
            self.h.cmd_subsurface(types.SimpleNamespace(value="false"))
        self.assertIn("окно 2 не остановилось", err.getvalue())
        with open(self.h.BASE_PROP) as handle:             # и ничего не тронуто
            self.assertIn("use_subsurface=true", handle.read())


class Recorder:
    """Вид окна: записывает, что его попросили показать."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return call

    def last(self, name):
        return next(args for called, args, _kw in reversed(self.calls) if called == name)


class FakeWindow:
    """Подставное окно: состояние настоящее, внешний мир — записывается."""

    _closing = False
    _updating = False
    _busy = False
    _syncing = False
    _sync_pending = False
    _merci_updating = False
    _wizard_working = False
    _container_work = None
    _switch_quiet = False
    _build_absent = ""
    _lock_notice = "Идёт настройка Waydroid — дождитесь её конца"
    _updates_dialog = None
    _last_status = None
    selected = None
    visible = True

    def __init__(self):
        self.calls = []
        self.shell = Recorder()
        self.settings = types.SimpleNamespace(eco_mode=False, fps_limit=0,
                                              hide_android_updater=True)
        self._window_ops = set()
        self.entries = []
        self.can_begin = True

    def note(self, kind, **details):
        self.calls.append({"kind": kind, **details})

    def kinds(self):
        return [call["kind"] for call in self.calls]

    def get_visible(self):
        return self.visible

    def _toast(self, message, *_args, **_kwargs):
        self.note("toast", message=message)

    def _error(self, heading, body):
        self.note("error", heading=heading, body=body)

    def _choose(self, heading, body, answers, then, default=""):
        self.note("choose", heading=heading, body=body,
                  answers=[item[0] for item in answers], then=then)

    def _ask(self, dialog):
        self.note("ask", dialog=dialog)

    def _begin_window_op(self, number):
        self.note("begin", number=number)
        if self.can_begin:
            self._window_ops.add(number)
        return self.can_begin

    def _begin_update(self, text, setup=False):
        self.note("lock", setup=setup)
        if self.can_begin:
            self._updating = True
        return self.can_begin

    def _end_update(self):
        self._updating = False
        self.note("unlock")

    def refresh(self, select_slug=None):
        self.note("refresh", slug=select_slug)

    def _monitor_size(self):
        return (1920, 1080)

    def __getattr__(self, name):
        # Всё остальное, о чём окно просит само себя, — просто записываем.
        if name.startswith("__"):
            raise AttributeError(name)

        def call(*args, **kwargs):
            self.note(name, args=args)

        return call


@unittest.skipUnless(READY, "нужен графический сеанс и PyGObject")
class WindowActions(Stubbed):
    METHODS = (
        "_set_eco", "_set_fps", "_apply_freeform_opt", "_trim_ram", "_stop_waydroid",
        "_restart_from_tray", "_raise_running_app", "_show_build", "_render_manage",
        "show_installer", "_on_multi_windows_toggled", "import_apk", "_set_hide_updater",
        "_show_updates", "_drop_image_backup", "_restore_images", "_update_merci",
        "_report_crash", "_sync_containers", "_ask_host", "_catalog", "_start_waydroid",
        "_show_crash_log", "_locked_out", "_show_android_ui", "_on_installer_work",
        "request_quit", "_quit_app", "_confirm_uninstall", "_confirm_remove",
    )

    def setUp(self):
        from merci import hostexec, i18n, waydroid, window

        self.module = self.w = waydroid
        super().setUp()
        self.window, self.i18n, self.hostexec = window, i18n, hostexec
        self.language = i18n.language()
        i18n.set_language("ru")
        self.fake = FakeWindow()
        for name in self.METHODS:
            setattr(self.fake, name,
                    types.MethodType(getattr(window.MerciWindow, name), self.fake))
        # Потоки — на месте и сразу: проверяется решение, а не ожидание.
        self.real_in_thread = hostexec.in_thread
        hostexec.in_thread = lambda work, done=None: (
            done(*Core.attempt(work)) if done else work())

    def tearDown(self):
        self.hostexec.in_thread = self.real_in_thread
        self.i18n.set_language(self.language)
        super().tearDown()

    def toasts(self):
        return [call["message"] for call in self.fake.calls if call["kind"] == "toast"]

    # -- быстрые настройки ---------------------------------------------------

    def test_eco_switch_says_what_android_answered(self):
        noted = []
        self.stub(apply_eco_mode=lambda active: (False, True),
                  # Память заводится, пока в настройках ещё прежнее значение.
                  note_quick_settings=lambda: noted.append(self.fake.settings.eco_mode))
        self.fake._set_eco(True)
        self.assertEqual(noted, [False])
        self.assertTrue(self.fake.settings.eco_mode)
        self.assertEqual(self.toasts()[-1], "Режим экономии включится при запуске Android")
        self.fake._set_eco(False)
        self.assertEqual(self.toasts()[-1],
                         "Режим экономии выключен: анимации вернутся при запуске Android")
        self.stub(apply_eco_mode=lambda active: (True, False))
        self.fake._set_eco(True)
        self.assertEqual(noted, [False, True, False])
        self.assertEqual(self.toasts()[-1], "Режим экономии включён (анимации отключены)")
        self.fake._set_eco(False)
        self.assertEqual(self.toasts()[-1], "Режим экономии выключен")
        # Выключили то, что никуда и не доходило, — возвращать нечего.
        self.fake.settings.eco_mode = True
        self.stub(apply_eco_mode=lambda active: (False, False))
        self.fake._set_eco(False)
        self.assertEqual(self.toasts()[-1], "Режим экономии выключен")

    def test_fps_switch_says_what_android_answered(self):
        self.stub(set_fps_limit=lambda fps: (False, True), note_quick_settings=lambda: None)
        self.fake._set_fps(60)
        self.assertEqual(self.toasts()[-1], "Лимит 60 FPS применится при запуске Android")
        self.fake._set_fps(0)
        self.assertEqual(self.toasts()[-1], "Лимит FPS снимется при запуске Android")
        self.stub(set_fps_limit=lambda fps: (True, False))
        self.fake._set_fps(30)
        self.assertEqual(self.toasts()[-1], "Установлен лимит 30 FPS")
        self.fake._set_fps(0)
        self.assertEqual(self.toasts()[-1], "Лимит FPS снят")
        before = len(self.fake.calls)
        self.fake._set_fps(17)                       # такого выбора нет
        self.assertEqual(len(self.fake.calls), before)

    def test_one_time_buttons_do_not_claim_success_blindly(self):
        self.stub(apply_multiwindow_optimizations=lambda: False,
                  trim_android_memory=lambda: False)
        self.fake._apply_freeform_opt()
        self.assertEqual(self.toasts()[-1],
                         "Не применилось: Android не запущен или не отвечает")
        self.fake._trim_ram()
        self.assertEqual(self.toasts()[-1],
                         "Память не очищена: Android не запущен или не отвечает")
        self.stub(apply_multiwindow_optimizations=lambda: True,
                  trim_android_memory=lambda: True)
        self.fake._apply_freeform_opt()
        self.assertEqual(self.toasts()[-1], "Свободный режим многооконности активен!")
        self.fake._trim_ram()
        self.assertEqual(self.toasts()[-1], "Память Android успешно очищена!")

    # -- трей ----------------------------------------------------------------

    def test_stop_is_not_offered_for_a_stopped_waydroid(self):
        self.stub(certainly_off=lambda: True)
        self.fake._stop_waydroid()
        self.assertEqual(self.toasts(), ["Waydroid уже выключен"])
        self.assertNotIn("choose", self.fake.kinds())
        self.stub(certainly_off=lambda: False)
        self.fake._stop_waydroid()
        question = self.fake.calls[-1]
        self.assertEqual((question["kind"], question["answers"]), ("choose", ["cancel", "stop"]))
        question["then"]("stop")
        self.assertEqual(self.fake.calls[-1]["kind"], "_do_stop_waydroid")

    def test_restart_from_tray_asks_first(self):
        self.stub(certainly_off=lambda: False)
        self.fake._restart_from_tray()
        question = self.fake.calls[-1]
        self.assertEqual((question["kind"], question["answers"]),
                         ("choose", ["cancel", "restart"]))
        self.assertNotIn("_restart_waydroid", self.fake.kinds())
        question["then"]("restart")
        self.assertEqual(self.fake.calls[-1]["kind"], "_restart_waydroid")

    def test_restart_of_a_stopped_waydroid_just_starts_it(self):
        started = []
        self.stub(certainly_off=lambda: True,
                  status=lambda use_cache=True: (False, "сессия остановлена"),
                  ensure_session=lambda: started.append(True),
                  forget_state=lambda: None)
        self.fake._restart_from_tray()
        self.assertEqual(started, [True])
        self.assertNotIn("choose", self.fake.kinds())
        self.assertEqual(self.fake._window_ops, set())
        self.assertEqual(self.toasts()[-1], "Контейнер запущен")

    def test_start_says_when_waydroid_already_runs(self):
        self.stub(status=lambda use_cache=True: (True, "сессия запущена"),
                  ensure_session=lambda: self.fail("поднимать нечего"),
                  forget_state=lambda: None)
        self.fake._start_waydroid()
        self.assertEqual(self.toasts(), ["Waydroid уже работает"])

    def test_open_running_game_does_not_launch_a_stopped_one(self):
        from merci.library import Entry

        old = Entry(slug="a-aaaaaaaaaaaa", name="A", package="org.a", last_run=5.0)
        new = Entry(slug="b-bbbbbbbbbbbb", name="B", package="org.b", last_run=9.0)
        self.fake.entries = [old, new]
        self.stub(open_entries=lambda catalog: {})
        self.fake._raise_running_app()
        self.assertEqual(self.toasts(), ["Сейчас ничего не запущено"])
        self.assertNotIn("_launch_waydroid", self.fake.kinds())
        self.stub(open_entries=lambda catalog: {"a-aaaaaaaaaaaa": 1})
        self.fake._raise_running_app()
        self.assertEqual(self.fake.calls[-1]["args"], (old,))
        self.stub(open_entries=lambda catalog: {"a-aaaaaaaaaaaa": 1, "b-bbbbbbbbbbbb": 2})
        self.fake._raise_running_app()
        self.assertEqual(self.fake.calls[-1]["args"], (new,))   # последнее запущенное

    def test_tray_does_not_wake_the_container_during_root_work(self):
        """Вопрос о состоянии будит службу контейнера, которую помощник как
        раз остановил: пока идёт работа под root, трей хост не спрашивает."""
        def touched(*_args, **_kwargs):
            self.fail("во время работы под root хост спрашивать нельзя")

        self.stub(certainly_off=touched, open_entries=touched, show_full_ui=touched,
                  status=touched)
        self.fake._updating = True
        for action in (self.fake._stop_waydroid, self.fake._restart_from_tray,
                       self.fake._raise_running_app, self.fake._show_android_ui):
            action()
        self.assertEqual(self.toasts(), [self.fake._lock_notice] * 4)
        self.assertEqual(self.fake.kinds(), ["toast"] * 4)

    # -- карточка ------------------------------------------------------------

    def test_uninstall_is_not_offered_for_what_is_not_installed(self):
        from merci.library import Entry

        entry = Entry(slug="x-0123456789ab", name="X", package="org.x")
        self.fake.selected = entry
        self.fake._last_status = self.w.ContainerStatus(ready=True, detail="сессия запущена")
        self.fake._show_build(entry.slug, (("", ""), True), None)
        self.assertEqual(self.fake.shell.last("set_can_uninstall"), (False,))
        self.fake._show_build(entry.slug, (("0123456789ab", "1.0"), True), None)
        self.assertEqual(self.fake.shell.last("set_can_uninstall"), (True,))
        # Спросить не удалось — «не знаем» не значит «нет».
        self.fake._show_build(entry.slug, ((None, ""), True), None)
        self.assertEqual(self.fake.shell.last("set_can_uninstall"), (True,))

    def test_same_apk_again_is_shown_not_added(self):
        from merci.library import AlreadyInLibrary, Entry

        entry = Entry(slug="x-0123456789ab", name="Моё имя", package="org.x")

        def add(path):
            raise AlreadyInLibrary(entry)

        self.fake.library = types.SimpleNamespace(add=add)
        self.fake.import_apk("/tmp/game.apk")
        self.assertEqual(self.toasts(), ["«Моё имя» уже в библиотеке"])
        refreshed = [call for call in self.fake.calls if call["kind"] == "refresh"]
        self.assertEqual(refreshed, [{"kind": "refresh", "slug": entry.slug}])
        self.assertNotIn("error", self.fake.kinds())

    def test_crash_details_show_the_found_report(self):
        self.fake._report_crash("tombstone_04\nsignal 11\n#00 libndk_translation.so", "ОТЧЁТ")
        _title, label, action, _timeout = self.fake.shell.last("toast")
        self.assertEqual(label, "Подробности")
        action()
        self.assertEqual(self.fake.calls[-1],
                         {"kind": "_present_crash_log", "args": ("ОТЧЁТ",)})

    def test_crash_log_words_are_in_the_window_language(self):
        self.stub(crash_log_async=lambda callback: callback("/data/tombstone_01", "строки"))
        self.fake._show_crash_log()
        self.assertEqual(self.fake.calls[-1]["args"], ("Файл: /data/tombstone_01\n\nстроки",))
        self.stub(crash_log_async=lambda callback: callback("", ""))
        self.fake._show_crash_log()
        self.assertEqual(self.fake.calls[-1]["args"], ("",))

    # -- замки ---------------------------------------------------------------

    def test_wizard_holds_the_window_only_while_it_works(self):
        made = []

        class Dialog:
            def __init__(self, parent, needs_bridge, on_finished=None, monitor=(0, 0),
                         on_work=None):
                made.append(self)
                self.on_work = on_work

        self.saved_dialog = self.window.InstallerDialog
        self.window.InstallerDialog = Dialog
        self.addCleanup(setattr, self.window, "InstallerDialog", self.saved_dialog)
        # Мастер открыт и показывает план: окно свободно.
        self.fake.show_installer()
        self.assertEqual(self.fake.kinds(), ["ask"])
        self.assertFalse(self.fake._updating)
        # Нажали «Установить»: замок; шаги кончились — снят. И так каждый раз.
        for _ in range(2):
            self.assertTrue(made[0].on_work(True))
            self.assertTrue(self.fake._updating and self.fake._wizard_working)
            made[0].on_work(False)
            self.assertFalse(self.fake._updating or self.fake._wizard_working)
        self.assertEqual(self.fake.kinds(), ["ask", "lock", "unlock", "lock", "unlock"])
        self.assertTrue(self.fake.calls[1]["setup"])
        # Мастер закрыли, не начав: снимать нечего — и чужой замок не трогаем.
        self.fake.calls.clear()
        made[0].on_work(False)
        self.assertEqual(self.fake.kinds(), [])
        # Идёт запуск или другая операция: шаги не стартуют.
        self.fake.can_begin = False
        self.assertFalse(made[0].on_work(True))
        self.assertFalse(self.fake._wizard_working)
        # А во время работы под root мастер не открывается вовсе.
        self.fake.calls.clear()
        self.fake._updating = True
        self.fake.show_installer()
        self.assertEqual(self.fake.kinds(), ["toast"])
        self.assertEqual(len(made), 1)

    def test_quit_shortcut_respects_work_under_root(self):
        stopped = []
        self.fake.tray = types.SimpleNamespace(stop=lambda: stopped.append(True))
        self.fake._updating = True
        self.fake.request_quit()
        self.assertEqual(stopped, [])
        self.assertEqual(self.toasts(), [self.fake._lock_notice])
        self.fake._updating = False
        self.fake.request_quit()
        self.assertEqual(stopped, [True])

    def test_android_window_is_not_opened_over_a_stop_or_restart(self):
        opened = []
        self.stub(show_full_ui=lambda: opened.append(True))
        self.fake._container_work = ("stop", "выключается…")
        self.fake._show_android_ui()
        self.fake._container_work = None
        self.fake._window_ops = {0}
        self.fake._show_android_ui()
        self.assertEqual(opened, [])
        self.assertEqual(self.toasts(), ["Дождитесь конца текущей операции с окном"] * 2)
        # Запуск в соседнем окне не мешает.
        self.fake._window_ops = {3}
        self.fake._show_android_ui()
        self.assertEqual(opened, [True])

    def test_removal_is_not_asked_about_while_the_windows_are_busy(self):
        from merci.library import Entry

        self.fake.selected = Entry(slug="x-0123456789ab", name="X", package="org.x")
        self.stub(own_windows=lambda entry: [])
        self.fake._window_ops = {1}
        self.fake._confirm_uninstall()
        self.fake._confirm_remove()
        self.fake._window_ops = set()
        self.fake._updating = True
        self.fake._confirm_uninstall()
        self.fake._confirm_remove()
        self.assertEqual(self.fake.kinds(), ["toast"] * 4)
        # Занято чужое окно: снять установку в основном можно, удалить запись — нет.
        self.fake._updating = False
        self.fake._window_ops = {3}
        self.fake.calls.clear()
        self.fake._confirm_uninstall()
        self.fake._confirm_remove()
        self.assertEqual(self.fake.kinds(), ["choose", "toast"])
        self.fake._window_ops = set()
        self.fake._confirm_remove()
        self.assertEqual(self.fake.kinds()[-1], "choose")

    def test_window_mode_switch_is_locked_and_goes_back_on_failure(self):
        class Row:
            def __init__(self):
                self.active, self.sensitive = True, True

            def get_active(self):
                return self.active

            def set_active(self, value):
                self.active = value

            def set_sensitive(self, value):
                self.sensitive = value

        asked = []
        self.stub(set_multi_windows=lambda enabled: asked.append(enabled),
                  forget_state=lambda: None)
        # Идёт другая операция: режим не трогаем, переключатель — обратно.
        row = Row()
        self.fake.can_begin = False
        self.fake._on_multi_windows_toggled(row, None)
        self.assertEqual((asked, row.active), ([], False))
        # Контейнер настройку не принял: ошибка и переключатель обратно.
        self.fake.can_begin = True

        def refuse(enabled):
            raise self.w.WaydroidError("контейнер не принял настройку")

        self.stub(set_multi_windows=refuse)
        row = Row()
        self.fake._on_multi_windows_toggled(row, None)
        self.assertEqual((row.active, row.sensitive), (False, True))
        self.assertIn("error", self.fake.kinds())
        self.assertEqual(self.fake._window_ops, set())
        # Удача: замок снят, переключатель на месте.
        self.stub(set_multi_windows=lambda enabled: asked.append(enabled))
        row = Row()
        self.fake._on_multi_windows_toggled(row, None)
        self.assertEqual((asked, row.active), ([True], True))
        self.assertEqual(self.toasts()[-1], "Каждое приложение теперь в своём окне")
        self.assertEqual(self.fake._window_ops, set())

    def test_updater_switch_says_where_it_reached(self):
        self.stub(apply_updater_choice=lambda hide: False)
        self.fake._set_hide_updater(False)
        self.assertEqual(self.toasts()[-1],
                         "Встроенный обновлятор Android вернётся при запуске Android")
        self.fake._set_hide_updater(True)
        self.assertEqual(self.toasts()[-1],
                         "Встроенный обновлятор Android скроется при запуске Android")
        self.stub(apply_updater_choice=lambda hide: True)
        self.fake._set_hide_updater(False)
        self.assertEqual(self.toasts()[-1], "Встроенный обновлятор Android возвращён")
        self.assertFalse(self.fake.settings.hide_android_updater)

    def test_updates_window_is_closed_to_work_under_root(self):
        self.fake._updating = True
        self.fake._show_updates()
        self.assertEqual(self.toasts(), [self.fake._lock_notice])
        self.fake._drop_image_backup()
        self.fake._restore_images()
        self.assertEqual(self.toasts()[1:], ["Дождитесь конца текущей операции"] * 2)
        self.assertNotIn("choose", self.fake.kinds())
        # Свободно — спрашиваем; но работа началась, пока человек думал.
        self.fake._updating = False
        dropped = []
        self.stub(drop_image_backup=lambda: dropped.append(True))
        self.fake._drop_image_backup()
        question = self.fake.calls[-1]
        self.assertEqual(question["kind"], "choose")
        self.fake._updating = True
        question["then"]("drop")
        self.assertEqual(dropped, [])
        self.fake._updating = False
        question["then"]("drop")
        self.assertEqual(dropped, [True])

    def test_merci_is_not_updated_twice_at_once(self):
        started = []
        self.hostexec.in_thread = lambda work, done=None: started.append(done)
        update = updates.Update(key="merci", title="Merci")
        self.fake._update_merci(update, quiet=True)
        self.fake._update_merci(update)
        self.assertEqual(len(started), 1)
        self.assertEqual(self.toasts(), ["Merci уже обновляется"])
        started[0](None, RuntimeError("нет сети"))      # первое кончилось
        self.fake._update_merci(update, quiet=True)
        self.assertEqual(len(started), 2)

    def test_settings_are_brought_to_windows_one_run_at_a_time(self):
        runs = []
        self.hostexec.in_thread = lambda work, done=None: runs.append((work, done))
        self.fake._updating = True
        self.fake._sync_containers()
        self.assertEqual(runs, [])                   # под root хост не трогаем
        self.fake._updating = False
        self.fake._sync_containers()
        self.fake._sync_containers()
        self.fake._sync_containers()
        self.assertEqual(len(runs), 1)
        self.assertIs(runs[0][0], self.w.sync_android_settings)
        runs[0][1](None, None)                       # первый кончился
        self.assertEqual(len(runs), 2)               # и один повтор — на всё, что просили
        runs[1][1](None, None)
        self.assertEqual(len(runs), 2)


if __name__ == "__main__":
    unittest.main()
