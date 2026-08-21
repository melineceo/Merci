"""Экран подготовки Waydroid: шаги, живой прогресс и журнал.

Главное требование к этому окну — чтобы в любой момент было понятно, идёт
работа или всё встало. Поэтому у него три уровня подробности: крупная
надпись о текущей фазе, полоса с процентами и цифрами загрузки, и журнал
для тех, кому нужны детали.
"""

from __future__ import annotations

import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import waydroid  # noqa: E402

_LOG_LIMIT = 400  # строк; больше в мини-логе всё равно не прочитать


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} с"
    return f"{seconds // 60} мин {seconds % 60:02d} с"


class StepRow(Adw.ActionRow):
    """Строка шага: ожидание → выполняется → получилось или нет."""

    _ICONS = {
        "wait": ("radio-symbolic", "dim-label"),
        "done": ("object-select-symbolic", "success"),
        "fail": ("dialog-error-symbolic", "error"),
        "skip": ("media-skip-forward-symbolic", "dim-label"),
    }

    def __init__(self, step: waydroid.Step) -> None:
        super().__init__(title=step.title, subtitle=step.hint)
        self.step = step
        self.add_css_class("merci-step")

        self.spinner = Adw.Spinner()
        self.spinner.set_size_request(18, 18)
        self.spinner.set_visible(False)
        self.icon = Gtk.Image.new_from_icon_name("radio-symbolic")
        self.icon.add_css_class("dim-label")

        box = Gtk.Box(spacing=6)
        box.set_valign(Gtk.Align.CENTER)
        box.append(self.spinner)
        box.append(self.icon)
        self.add_prefix(box)

        if step.downloads:
            self.set_subtitle(f"{step.hint} · скачает {step.downloads}")

    def set_state(self, state: str) -> None:
        running = state == "run"
        self.spinner.set_visible(running)
        self.icon.set_visible(not running)
        for css in ("dim-label", "success", "error"):
            self.icon.remove_css_class(css)
        if not running:
            name, css = self._ICONS.get(state, self._ICONS["wait"])
            self.icon.set_from_icon_name(name)
            self.icon.add_css_class(css)
        self.set_opacity(1.0 if state != "wait" else 0.65)


class InstallerDialog(Adw.Dialog):
    """Ведёт от «ничего нет» до работающей сессии Waydroid."""

    def __init__(
        self,
        parent: Gtk.Window,
        needs_bridge: bool,
        on_finished=None,
        monitor: tuple[int, int] = (0, 0),
    ) -> None:
        super().__init__(title="Подготовка Waydroid", content_width=640, content_height=680)
        self._parent = parent
        self._needs_bridge = needs_bridge
        # Размер монитора нужен, чтобы посчитать разрешение рендера: узнать
        # его может только окно, у хоста мы бы спрашивали лишнее.
        self._monitor = monitor
        self._on_finished = on_finished
        self._runner: waydroid.StepRunner | None = None
        self._steps: list[waydroid.Step] = []
        self._rows: list[StepRow] = []
        self._index = 0
        self._log_lines = 0
        self._started_at = 0.0
        self._step_started_at = 0.0
        self._pulse_source = 0
        self._clock_source = 0
        self._running = False

        self._build()
        self._reload_plan()

    # -- интерфейс -------------------------------------------------------

    def _build(self) -> None:
        self.phase_label = Gtk.Label(label="Проверяем, что уже готово", xalign=0.5)
        self.phase_label.add_css_class("title-2")
        self.phase_label.set_wrap(True)
        self.phase_label.set_justify(Gtk.Justification.CENTER)

        self.detail_label = Gtk.Label(label="", xalign=0.5)
        self.detail_label.add_css_class("dim-label")
        self.detail_label.set_wrap(True)
        self.detail_label.set_justify(Gtk.Justification.CENTER)

        self.percent_label = Gtk.Label(label="", xalign=0.5)
        self.percent_label.add_css_class("merci-percent")

        self.progress = Gtk.ProgressBar()
        self.progress.add_css_class("merci-progress")
        self.progress.set_hexpand(True)

        self.step_label = Gtk.Label(label="", xalign=0)
        self.step_label.add_css_class("caption")
        self.step_label.add_css_class("dim-label")
        self.elapsed_label = Gtk.Label(label="", xalign=1)
        self.elapsed_label.add_css_class("caption")
        self.elapsed_label.add_css_class("dim-label")
        self.elapsed_label.set_hexpand(True)

        meta = Gtk.Box(spacing=8)
        meta.append(self.step_label)
        meta.append(self.elapsed_label)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        hero.set_margin_top(22)
        hero.append(self.percent_label)
        hero.append(self.phase_label)
        hero.append(self.detail_label)

        progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        progress_box.set_margin_top(6)
        progress_box.append(self.progress)
        progress_box.append(meta)

        self.group = Adw.PreferencesGroup(title="Шаги")

        self.log_view = Gtk.TextView(editable=False, monospace=True, cursor_visible=False)
        self.log_view.add_css_class("merci-log")
        self.log_view.set_left_margin(10)
        self.log_view.set_right_margin(10)
        self.log_view.set_top_margin(6)
        self.log_view.set_bottom_margin(6)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_scroll = Gtk.ScrolledWindow()
        self.log_scroll.set_child(self.log_view)
        self.log_scroll.set_size_request(-1, 170)

        self.log_expander = Adw.ExpanderRow(
            title="Журнал", subtitle="Полный вывод команд на хосте"
        )
        self.log_expander.add_row(Adw.ActionRow(child=self.log_scroll))
        log_group = Adw.PreferencesGroup()
        log_group.add(self.log_expander)

        self.primary = Gtk.Button(label="Установить")
        self.primary.add_css_class("suggested-action")
        self.primary.add_css_class("pill")
        self.primary.connect("clicked", self._on_primary)

        self.secondary = Gtk.Button(label="Отмена")
        self.secondary.add_css_class("pill")
        self.secondary.set_visible(False)
        self.secondary.connect("clicked", self._on_secondary)

        buttons = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
        buttons.set_margin_top(4)
        buttons.append(self.secondary)
        buttons.append(self.primary)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        column.set_margin_start(16)
        column.set_margin_end(16)
        column.set_margin_bottom(20)
        column.append(hero)
        column.append(progress_box)
        column.append(self.group)
        column.append(log_group)
        column.append(buttons)

        scroller = Gtk.ScrolledWindow(vexpand=True)
        scroller.set_child(Adw.Clamp(maximum_size=600, child=column))

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.recheck = Gtk.Button(icon_name="view-refresh-symbolic")
        self.recheck.set_tooltip_text("Проверить заново")
        self.recheck.connect("clicked", lambda *_: self._reload_plan())
        header.pack_end(self.recheck)
        toolbar.add_top_bar(header)
        toolbar.set_content(scroller)
        self.set_child(toolbar)

    # -- план ------------------------------------------------------------

    def _reload_plan(self) -> None:
        for row in self._rows:
            self.group.remove(row)
        self._rows.clear()

        self.phase_label.set_text("Проверяем, что уже готово")
        self.detail_label.set_text("Опрашиваем хост")
        self.percent_label.set_text("")
        self.primary.set_sensitive(False)
        self._pulse(True)

        waydroid.forget_state()
        waydroid.plan_async(self._needs_bridge, self._apply_plan, self._monitor)

    def _apply_plan(self, steps: list[waydroid.Step]) -> None:
        self._pulse(False)
        self._steps = steps
        self._index = 0
        self.primary.set_sensitive(True)

        if not self._steps:
            self._show_success("Waydroid готов", "Можно запускать приложения")
            return

        minutes = sum(step.minutes for step in self._steps)
        self.percent_label.set_text("")
        self.phase_label.set_text("Готовы установить")
        self.detail_label.set_text(
            f"{len(self._steps)} шаг(ов), примерно {minutes} мин. "
            "Шаги с правами root подтверждаются паролем — Merci его не видит."
        )
        self.progress.set_fraction(0.0)
        self.step_label.set_text("")
        self.elapsed_label.set_text("")

        for step in self._steps:
            row = StepRow(step)
            row.set_state("wait")
            self.group.add(row)
            self._rows.append(row)

    # -- выполнение ------------------------------------------------------

    def _on_primary(self, *_args) -> None:
        if self._running:
            return
        if not self._steps:
            self.close()
            return
        self._running = True
        self._started_at = time.monotonic()
        self.primary.set_visible(False)
        self.secondary.set_visible(True)
        self.recheck.set_sensitive(False)
        self.set_can_close(False)
        self._clock_source = GLib.timeout_add_seconds(1, self._tick)
        self._run_current()

    def _on_secondary(self, *_args) -> None:
        if self._running:
            if self._runner is not None:
                self._runner.cancel()
            self._append_log("отменено пользователем")
            self._stop_running()
            self.phase_label.set_text("Установка отменена")
            self.detail_label.set_text("Незавершённый шаг можно повторить")
            self.primary.set_label("Повторить")
            self.primary.set_visible(True)
            self.secondary.set_visible(False)
            self.recheck.set_sensitive(True)
            self.set_can_close(True)
            return
        self.close()

    def _run_current(self) -> None:
        if self._index >= len(self._steps):
            self._all_done()
            return

        step = self._steps[self._index]
        row = self._rows[self._index]
        row.set_state("run")
        self._step_started_at = time.monotonic()

        self.step_label.set_text(f"Шаг {self._index + 1} из {len(self._steps)}")
        self.phase_label.set_text(step.title)
        self.detail_label.set_text(step.hint)
        self.percent_label.set_text("")
        self._pulse(True)
        self._append_log(f"$ {step.command_line()}")

        self._runner = waydroid.StepRunner(
            step, self._append_log, self._step_done, self._on_step_progress
        )
        self._runner.start()

    def _on_step_progress(self, fraction: float | None, detail: str) -> None:
        """Прогресс шага: доля известна — показываем проценты, нет —
        обновляем только текст и продолжаем пульсировать."""
        if fraction is None:
            if detail:
                self.phase_label.set_text(detail)
            return

        self._pulse(False)
        self.progress.set_fraction(fraction)
        self.percent_label.set_text(f"{fraction * 100:.0f}%")
        self.detail_label.set_text(detail)

    def _step_done(self, ok: bool) -> None:
        row = self._rows[self._index]
        row.set_state("done" if ok else "fail")
        self._index += 1
        self._pulse(False)
        self.percent_label.set_text("")
        self.progress.set_fraction(self._index / max(len(self._steps), 1))

        if not ok:
            self._failed(row.step)
            return
        for row in self._rows[self._index :]:
            row.set_state("wait")
        # Небольшая пауза, чтобы галочка успела прочитаться.
        GLib.timeout_add(350, lambda: (self._run_current(), False)[1])

    def _tick(self) -> bool:
        if not self._running:
            return False
        self.elapsed_label.set_text(f"прошло {_duration(time.monotonic() - self._started_at)}")
        return True

    def _pulse(self, active: bool) -> None:
        """Неопределённый прогресс: полоса ходит туда-обратно, пока
        неизвестно, сколько всего работы."""
        if active and not self._pulse_source:
            self.progress.set_pulse_step(0.08)
            self._pulse_source = GLib.timeout_add(120, self._do_pulse)
        elif not active and self._pulse_source:
            GLib.source_remove(self._pulse_source)
            self._pulse_source = 0

    def _do_pulse(self) -> bool:
        self.progress.pulse()
        return True

    def _stop_running(self) -> None:
        self._running = False
        self._pulse(False)
        if self._clock_source:
            GLib.source_remove(self._clock_source)
            self._clock_source = 0

    # -- финал -----------------------------------------------------------

    _HINTS = {
        "bridge": (
            "Не удалось поставить трансляцию ARM64",
            "Архив транслятора качается с GitHub, и соединение оборвалось "
            "(в журнале — SSL: RECORD_LAYER_FAILURE или таймаут). Merci уже "
            "делает три попытки и пробует запасной libhoudini.\n\n"
            "Обычно помогает другая локация VPN: канал должен держать "
            "непрерывную передачу в несколько сотен мегабайт. "
            "Всё остальное уже установлено, повторить можно только этот шаг.",
        ),
        "network": (
            "Сервер образов Waydroid не отвечает",
            "ota.waydro.id раздаётся через GitHub Pages, и у вас он недоступен. "
            "Сам waydroid init начинает именно с этого адреса и в такой ситуации "
            "висит бесконечно и молча — поэтому Merci проверяет связь заранее.\n\n"
            "Что можно сделать: включить VPN и повторить, либо прописать своё "
            "зеркало в файл ota.conf в данных Merci — первая строка system, "
            "вторая vendor.",
        ),
    }

    def _failed(self, step: waydroid.Step) -> None:
        self._stop_running()
        self.set_can_close(True)
        self.recheck.set_sensitive(True)

        title, detail = self._HINTS.get(
            step.key,
            (
                f"Шаг «{step.title}» не выполнился",
                "Подробности в журнале. Ту же команду можно выполнить вручную — "
                "кнопка скопирует её в буфер обмена.",
            ),
        )
        self.phase_label.set_text(title)
        self.detail_label.set_text(detail)
        self.log_expander.set_expanded(step.key not in self._HINTS)
        self.primary.set_label("Скопировать команду")
        self.primary.remove_css_class("suggested-action")
        self.primary.set_visible(True)
        self.secondary.set_label("Закрыть")
        self.secondary.set_visible(True)
        self._command_to_copy = step.command_line()
        self.primary.disconnect_by_func(self._on_primary)
        self.primary.connect("clicked", self._on_copy)

    def _on_copy(self, *_args) -> None:
        self.get_clipboard().set(self._command_to_copy)
        self._append_log("команда скопирована в буфер обмена")
        self.detail_label.set_text("Команда в буфере обмена")

    def _all_done(self) -> None:
        self._stop_running()
        waydroid.forget_state()
        ready, detail = waydroid.status(use_cache=False)
        if ready:
            self._show_success(
                "Waydroid готов",
                f"Заняло {_duration(time.monotonic() - self._started_at)}",
            )
        else:
            self.phase_label.set_text("Почти готово")
            self.detail_label.set_text(detail)
            self.percent_label.set_text("")
            self.primary.set_label("Проверить снова")
            self.primary.set_visible(True)
            self.secondary.set_visible(False)
        self.set_can_close(True)
        self.recheck.set_sensitive(True)
        if self._on_finished is not None:
            self._on_finished()

    def _show_success(self, title: str, detail: str) -> None:
        self._pulse(False)
        self.progress.set_fraction(1.0)
        self.percent_label.set_text("100%")
        self.percent_label.add_css_class("success")
        self.phase_label.set_text(title)
        self.detail_label.set_text(detail)
        self.step_label.set_text("")
        self.primary.set_label("Закрыть")
        self.primary.set_visible(True)
        self.primary.set_sensitive(True)
        self.secondary.set_visible(False)

    # -- журнал ----------------------------------------------------------

    def _append_log(self, line: str) -> None:
        buffer = self.log_view.get_buffer()
        buffer.insert(buffer.get_end_iter(), line + "\n")

        self._log_lines += 1
        if self._log_lines > _LOG_LIMIT:
            start = buffer.get_start_iter()
            end = buffer.get_iter_at_line(self._log_lines - _LOG_LIMIT)[1]
            buffer.delete(start, end)
            self._log_lines = _LOG_LIMIT

        adjustment = self.log_scroll.get_vadjustment()
        GLib.idle_add(
            lambda: adjustment.set_value(
                adjustment.get_upper() - adjustment.get_page_size()
            )
            or False
        )

    def do_closed(self) -> None:  # noqa: D401
        if self._runner is not None:
            self._runner.cancel()
        self._stop_running()


class SingleStepDialog(InstallerDialog):
    """Тот же экран, но для одного заранее известного шага.

    Нужен действиям вроде смены транслятора: план строить не из чего,
    а прогресс, журнал и отмена — те же самые.
    """

    def __init__(self, parent: Gtk.Window, step: waydroid.Step, on_finished=None) -> None:
        self._single = step
        super().__init__(parent, needs_bridge=False, on_finished=on_finished)


    def _reload_plan(self) -> None:
        for row in self._rows:
            self.group.remove(row)
        self._rows.clear()
        self._apply_plan([self._single])
