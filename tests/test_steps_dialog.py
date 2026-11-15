"""Проверки мастера шагов (src/merci/installer.py) — на настоящих виджетах,
но без показа окон и без исполнения шагов: вместо исполнителя стоит
заглушка, которая только записывает, что её позвали.

Запуск из корня проекта:  python3 -m unittest discover -s tests -v
Нужен графический сеанс (GTK должен суметь подключиться к дисплею); без него
проверки пропускаются.
"""

import os
import sys
import time
import unittest
import warnings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_updates import HAVE_GI  # noqa: E402

READY = False
if HAVE_GI:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, GLib, Gtk

        READY = bool(Gtk.init_check())
        if READY:
            Adw.init()
    except (ImportError, ValueError):
        READY = False


def spin(rounds=30):
    """Даёт главному циклу отработать отложенные вызовы."""
    context = GLib.MainContext.default()
    with warnings.catch_warnings():
        # PyGObject на свежем Python ворчит про asyncio при каждом обороте
        # цикла; к проверяемому это отношения не имеет.
        warnings.simplefilter("ignore", DeprecationWarning)
        for _ in range(rounds):
            while context.iteration(False):
                pass
            time.sleep(0.01)


class FakeRunner:
    """Исполнитель шага, который ничего не исполняет."""

    log: list = []
    last = None
    outcome = "hang"  # hang — шаг «идёт»; fail и ok — сразу кончается
    reason = "помощник Merci уже занят другой операцией с Waydroid — дождитесь её конца"

    def __init__(self, step, on_line, on_done, on_progress=None, on_locked=None):
        self.step, self.on_line, self.on_done = step, on_line, on_done
        self.on_locked = on_locked or (lambda: None)
        # Как у настоящего: шаг с остановкой контейнера без отменяемого
        # начала заперт сразу.
        self.locked = step.then_session and not step.prelude
        FakeRunner.last = self

    def start(self):
        FakeRunner.log.append(("start", self.step.key))
        if FakeRunner.outcome == "fail":
            for line in ("останавливаем Waydroid", FakeRunner.reason,
                         "поднимаем Waydroid", "код возврата: 1"):
                self.on_line(line)
            GLib.idle_add(lambda: (self.on_done(False), False)[1])
        elif FakeRunner.outcome == "ok":
            GLib.idle_add(lambda: (self.on_done(True), False)[1])

    def reach_root(self):
        """Сценарий шага напечатал метку «дальше не отменить»."""
        self.locked = True
        self.on_locked()

    def cancel(self):
        if not self.locked:
            FakeRunner.log.append(("cancel", self.step.key))


@unittest.skipUnless(READY, "нужен графический сеанс и PyGObject")
class SingleStepDialogTest(unittest.TestCase):
    def setUp(self):
        from merci import i18n, installer, waydroid

        self.installer, self.w, self.i18n = installer, waydroid, i18n
        self.language = i18n.language()
        i18n.set_language("ru")
        self.saved = {name: getattr(waydroid, name) for name in
                      ("StepRunner", "forget_state", "status", "plan_async", "in_thread")}
        waydroid.StepRunner = FakeRunner
        waydroid.forget_state = lambda: None
        waydroid.status = lambda use_cache=True: (True, "готово")
        # «В потоке» здесь значит «сразу»: проверкам ждать поток незачем.
        waydroid.in_thread = lambda work, callback=None: callback(work(), None)
        FakeRunner.log = []
        FakeRunner.last = None
        FakeRunner.outcome = "hang"
        self.results = []
        self.made = []

    def tearDown(self):
        # Мастер, оставленный «в работе», держит таймеры часов и полосы.
        for dialog in self.made:
            dialog._stop_running()
        for name, value in self.saved.items():
            setattr(self.w, name, value)
        self.i18n.set_language(self.language)

    def step(self, **flags):
        values = {"key": "demo", "title": "Убрать root (Magisk)", "hint": "подсказка",
                  "argv": ["true"]}
        values.update(flags)
        return self.w.Step(**values)

    def dialog(self, step, **extra):
        made = self.installer.SingleStepDialog(
            None, step, on_result=lambda ok, key: self.results.append((ok, key)), **extra)
        self.made.append(made)
        spin()
        return made

    def test_step_starts_by_itself_under_its_own_title(self):
        dialog = self.dialog(self.step())
        self.assertEqual(FakeRunner.log, [("start", "demo")])
        self.assertEqual(dialog.get_title(), "Убрать root (Magisk)")
        self.assertFalse(dialog.primary.get_visible())  # второй кнопки «Установить» нет
        self.assertFalse(dialog.recheck.get_visible())

    def test_wizard_closed_before_the_start_runs_nothing(self):
        """Автозапуск отложен на оборот цикла. Закрытый за это время мастер
        не должен запускать шаг: окно уже сняло бы свой замок."""
        made = self.installer.SingleStepDialog(None, self.step(asks_root=True, then_session=True))
        self.made.append(made)
        self.assertFalse(made.get_can_close())  # да и закрыть его до старта нельзя
        made.do_closed()
        spin()
        self.assertEqual(FakeRunner.log, [])

    def test_step_that_restarts_the_container_cannot_be_cancelled(self):
        """Работу под root между остановкой и подъёмом контейнера «Отмена»
        не останавливает — поэтому её и нет."""
        dialog = self.dialog(self.step(asks_root=True, then_session=True))
        self.assertFalse(dialog.secondary.get_visible())
        self.assertFalse(dialog.get_can_close())
        dialog._on_secondary()
        # Закрытие главного окна шаг тоже не рвёт.
        dialog.do_closed()
        self.assertNotIn(("cancel", "demo"), FakeRunner.log)
        self.assertEqual(self.results, [])

    def test_download_can_be_cancelled_until_root_work_begins(self):
        """У шага с отменяемым началом (качает архив, потом зовёт помощника)
        «Отмена» есть, пока сценарий не сообщил, что дошёл до работы под
        root, — и пропадает с этой минуты."""
        for flags in ({"asks_root": True, "then_session": True, "prelude": True},
                      {"asks_root": True}):  # второе — сборка и замена пакета NVIDIA
            FakeRunner.log = []
            dialog = self.dialog(self.step(**flags))
            self.assertTrue(dialog.secondary.get_visible(), flags)
            FakeRunner.last.reach_root()
            self.assertFalse(dialog.secondary.get_visible(), flags)
            dialog._on_secondary()
            dialog.do_closed()
            self.assertNotIn(("cancel", "demo"), FakeRunner.log)

    def test_plain_step_can_be_cancelled_and_repeated(self):
        dialog = self.dialog(self.step())
        self.assertTrue(dialog.secondary.get_visible())
        dialog.secondary.emit("clicked")
        spin()
        self.assertIn(("cancel", "demo"), FakeRunner.log)
        self.assertEqual(self.results, [(False, "cancelled")])
        self.assertTrue(dialog.primary.get_visible())
        dialog.primary.emit("clicked")
        spin()
        self.assertEqual(FakeRunner.log.count(("start", "demo")), 2)

    def test_failure_shows_what_the_step_said(self):
        FakeRunner.outcome = "fail"
        dialog = self.dialog(self.step(asks_root=True, then_session=True))
        self.assertEqual(self.results, [(False, "demo")])
        detail = dialog.detail_label.get_text()
        # Причина — слова помощника, а не строки обёртки вокруг него.
        self.assertTrue(detail.startswith("помощник Merci уже занят другой операцией"), detail)
        self.assertNotIn("поднимаем Waydroid", detail)
        self.assertTrue(dialog.get_can_close())
        self.assertTrue(dialog.log_expander.get_expanded())

    def test_failure_reason_follows_the_interface_language(self):
        self.i18n.set_language("en")
        FakeRunner.outcome = "fail"
        dialog = self.dialog(self.step(asks_root=True, then_session=True))
        detail = dialog.detail_label.get_text()
        self.assertTrue(detail.startswith("Merci's helper is busy"), detail)

    def test_success_uses_the_given_title_and_reports_once(self):
        FakeRunner.outcome = "ok"
        dialog = self.dialog(self.step(asks_root=True, then_session=True), done_title="Root убран")
        spin(60)
        self.assertEqual(dialog.phase_label.get_text(), "Root убран")
        self.assertEqual(self.results, [(True, "")])

    def wizard(self, plans, **extra):
        """Мастер подготовки с заранее заданными планами: по одному на
        каждое «Проверить заново»."""
        queue = list(plans)
        self.w.plan_async = lambda needs_bridge, callback, monitor=(0, 0): callback(queue.pop(0))
        dialog = self.installer.InstallerDialog(None, False, **extra)
        self.made.append(dialog)
        spin()
        return dialog

    def test_wizard_tells_the_window_when_it_works(self):
        """Окно занято, пока мастер исполняет шаги, — а не всё время, что он
        открыт: с открытым мастером, который ничего не делает, трей отвечал
        «идёт настройка — дождитесь её конца»."""
        work = []
        step = self.step(key="init", title="Образ Android")
        dialog = self.wizard([[step], [step]], on_work=lambda on: work.append(on))
        self.assertEqual(work, [])                    # план показан — окно свободно
        dialog.primary.emit("clicked")
        spin()
        self.assertEqual(work, [True])
        dialog.secondary.emit("clicked")              # отмена
        spin()
        self.assertEqual(work, [True, False])
        FakeRunner.outcome = "fail"
        dialog.primary.emit("clicked")                # «Повторить» — и неудача
        spin()
        self.assertEqual(work, [True, False, True, False])
        FakeRunner.outcome = "ok"
        dialog.recheck.emit("clicked")
        spin()
        dialog.primary.emit("clicked")                # и удача
        spin(60)
        self.assertEqual(work, [True, False, True, False, True, False])
        self.assertEqual(dialog.phase_label.get_text(), "Waydroid готов")
        dialog.do_closed()
        self.assertEqual(len(work), 6)                # снимать уже нечего

    def test_close_after_success_closes(self):
        """«Закрыть» после пройденных шагов запускала проверку итога заново:
        мастер оставался на экране, а открывшему его второй раз сообщалось
        «готово» — у обновлений это вторая сверка и второй снятый замок."""
        work = []
        step = self.step(key="init", title="Образ Android")
        FakeRunner.outcome = "ok"
        dialog = self.wizard([[step], [step]], on_work=lambda on: work.append(on))
        dialog.primary.emit("clicked")
        spin(60)
        self.assertEqual(dialog.primary.get_label(), "Закрыть")
        closed = []
        dialog.close = lambda: closed.append(1)
        dialog.primary.emit("clicked")
        spin(60)
        self.assertEqual(closed, [1])
        self.assertEqual(work, [True, False])
        self.assertEqual(FakeRunner.log, [("start", "init")])
        # «Проверить заново» возвращает кнопке её дело — запускать шаги.
        dialog.recheck.emit("clicked")
        spin()
        self.assertEqual(dialog.primary.get_label(), "Установить")
        dialog.primary.emit("clicked")
        spin(60)
        self.assertEqual(FakeRunner.log.count(("start", "init")), 2)
        self.assertEqual(closed, [1])

    def test_real_close_after_success_unmaps_the_dialog(self):
        """Настоящий Adw.Dialog тоже закрывается, а не только подмена close()."""
        step = self.step(key="init", title="Образ Android")
        FakeRunner.outcome = "ok"
        dialog = self.wizard([[step]])
        window = Adw.ApplicationWindow()
        try:
            window.set_default_size(700, 500)
            window.present()
            dialog.present(window)
            spin()
            dialog.primary.emit("clicked")
            spin(60)
            self.assertEqual(dialog.primary.get_label(), "Закрыть")
            self.assertTrue(dialog.get_mapped())
            dialog.primary.emit("clicked")
            spin(60)
            self.assertFalse(dialog.get_mapped())
        finally:
            window.destroy()

    def test_single_step_reports_once_and_closes(self):
        FakeRunner.outcome = "ok"
        dialog = self.dialog(self.step(asks_root=True, then_session=True),
                             done_title="Root убран")
        spin(60)
        closed = []
        dialog.close = lambda: closed.append(1)
        dialog.primary.emit("clicked")
        spin(60)
        self.assertEqual(closed, [1])
        self.assertEqual(self.results, [(True, "")])

    def test_not_ready_yet_checks_again(self):
        """«Почти готово»: кнопка проверяет ещё раз, а не закрывает."""
        step = self.step(key="init", title="Образ Android")
        FakeRunner.outcome = "ok"
        self.w.status = lambda use_cache=True: (False, "сессия остановлена")
        dialog = self.wizard([[step]])
        dialog.primary.emit("clicked")
        spin(60)
        self.assertEqual(dialog.primary.get_label(), "Проверить снова")
        closed = []
        dialog.close = lambda: closed.append(1)
        self.w.status = lambda use_cache=True: (True, "готово")
        dialog.primary.emit("clicked")
        spin(60)
        self.assertEqual(closed, [])
        self.assertEqual(dialog.phase_label.get_text(), "Waydroid готов")
        dialog.primary.emit("clicked")
        spin()
        self.assertEqual(closed, [1])

    def test_wizard_does_not_start_while_the_window_is_busy(self):
        step = self.step(key="init", title="Образ Android")
        dialog = self.wizard([[step]], on_work=lambda on: False)
        dialog.primary.emit("clicked")
        spin()
        self.assertEqual(FakeRunner.log, [])
        self.assertTrue(dialog.primary.get_visible())
        self.assertIn("занято другой операцией", dialog.detail_label.get_text())
        self.assertTrue(dialog.get_can_close())

    def test_closing_the_wizard_releases_the_window(self):
        """Страховка: замок окна не переживает мастер ни при каком раскладе."""
        work = []
        step = self.step(key="init", title="Образ Android")
        dialog = self.wizard([[step]], on_work=lambda on: work.append(on))
        dialog.primary.emit("clicked")
        spin()
        dialog.do_closed()
        self.assertEqual(work, [True, False])

    def test_wizard_can_be_run_again_after_a_failed_step(self):
        """Шаг не удался, человек жмёт «Проверить заново» — кнопка под
        «Готовы установить» обязана снова запускать шаги, а не копировать
        команду; вторая кнопка при новом запуске — снова «Отмена»."""
        step = self.step(key="init", title="Образ Android")
        FakeRunner.outcome = "fail"
        dialog = self.wizard([[step], [step]])
        dialog.primary.emit("clicked")
        spin()
        self.assertEqual(FakeRunner.log, [("start", "init")])
        self.assertEqual(dialog.primary.get_label(), "Скопировать команду")
        self.assertEqual(dialog.secondary.get_label(), "Закрыть")

        dialog.recheck.emit("clicked")
        spin()
        self.assertEqual(dialog.primary.get_label(), "Установить")
        self.assertTrue(dialog.primary.get_visible())
        FakeRunner.outcome = "hang"
        dialog.primary.emit("clicked")
        spin()
        self.assertEqual(FakeRunner.log.count(("start", "init")), 2)
        self.assertTrue(dialog.secondary.get_visible())
        self.assertEqual(dialog.secondary.get_label(), "Отмена")

    def test_wizard_with_nothing_left_to_do_closes_by_its_button(self):
        """Шаг не удался, причину устранили руками, «Проверить заново» —
        план пуст. Кнопка «Закрыть» обязана закрывать, а не копировать
        команду прошлого шага."""
        step = self.step(key="init", title="Образ Android")
        FakeRunner.outcome = "fail"
        dialog = self.wizard([[step], []])
        dialog.primary.emit("clicked")
        spin()
        closed = []
        dialog.close = lambda: closed.append(1)
        dialog.recheck.emit("clicked")
        spin()
        self.assertEqual(dialog.phase_label.get_text(), "Waydroid готов")
        self.assertEqual(dialog.primary.get_label(), "Закрыть")
        dialog.primary.emit("clicked")
        spin()
        self.assertEqual(closed, [1])
        # Копирование команды оставило бы свой след в подписи.
        self.assertNotEqual(dialog.detail_label.get_text(), "Команда в буфере обмена")


if __name__ == "__main__":
    unittest.main()
