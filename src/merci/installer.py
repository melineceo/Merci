"""Экран подготовки Waydroid: шаги, живой прогресс и журнал.

Главное требование к этому окну — чтобы в любой момент было понятно, идёт
работа или всё встало. Поэтому у него три уровня подробности: крупная
надпись о текущей фазе, полоса с процентами и цифрами загрузки, и журнал
для тех, кому нужны детали.
"""

from __future__ import annotations

import collections
import re
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from . import waydroid  # noqa: E402
from .i18n import tr

_LOG_LIMIT = 400  # строк; больше в мини-логе всё равно не прочитать


def failure_reason(lines, limit: int = 3, width: int = 260) -> str:
    """Чем шаг объяснил свою неудачу: последние содержательные строки вывода.

    Причина почти всегда там. Но журнал свёрнут, а раскрытый начинается с
    команды длиной в экран — до объяснения приходилось докручивать. Служебные
    строки (сама команда, код возврата, обрывки полосы загрузки) пропускаем.
    """
    picked: list[str] = []
    for line in reversed(list(lines)):
        text = line.strip()
        if text.startswith("$ "):
            break  # дальше — вывод предыдущего шага
        if text in _WRAPPER_LINES or text.startswith(_FILLER_LINES):
            continue  # обёртка шага говорит о себе, а не о причине
        if not text or text[0] in "#=" or text.startswith(("код возврата", "процесс убит")):
            if picked:
                break
            continue
        if text.startswith("polkit отказал"):
            # Дальше в этой строке — команда шага целиком; в причину она
            # попала бы обрезком сценария.
            text = text.split(". ")[0].rstrip(".") + "."
        picked.append(text if len(text) <= width else text[: width - 1] + "…")
        if len(picked) == limit or _ERROR_LINE.match(text):
            # Строка вида «OSError: …» — итог падения сценария на Python;
            # над ней — строки его кода, человеку они ни к чему.
            break
    if picked and _ERROR_LINE.match(picked[0]):
        picked = picked[:1]
    return "\n".join(reversed(picked))


_ERROR_LINE = re.compile(r"^[A-Za-z_][\w.]*(Error|Exception)\b")
# Что сценарии шагов пишут о собственном ходе вокруг работы помощника.
_WRAPPER_LINES = frozenset({
    "останавливаем Waydroid",
    "поднимаем Waydroid",
    "останавливаем контейнер",
    "поднимаем контейнер",
    "ставим транслятор из проверенного архива",
    "пароль спросит системное окно",
})
# Чем сам исполнитель шага заполняет тишину: к причине неудачи не относится.
_FILLER_LINES = ("вывода пока нет", "waydroid печатает прогресс не сразу")


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return tr("{n} с", n=seconds)
    return tr("{m} мин {s} с", m=seconds // 60, s=f"{seconds % 60:02d}")


class StepRow(Adw.ActionRow):
    """Строка шага: ожидание → выполняется → получилось или нет."""

    _ICONS = {
        "wait": ("radio-symbolic", "dim-label"),
        "done": ("object-select-symbolic", "success"),
        "fail": ("dialog-error-symbolic", "error"),
        "skip": ("media-skip-forward-symbolic", "dim-label"),
    }

    def __init__(self, step: waydroid.Step) -> None:
        super().__init__(title=tr(step.title), subtitle=tr(step.hint))
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
            self.set_subtitle(tr("{hint} · скачает {what}", hint=tr(step.hint), what=step.downloads))

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

    # Заголовок удачного конца; у обновлений он свой (см. StepsDialog).
    _done_title = ""
    # Можно ли прервать идущий шаг. У обновлений — нельзя: работа под root
    # от «Отмены» не останавливается (сигнал доходит только до оболочки
    # шага), и «Повторить» запустило бы вторую такую же поверх первой.
    _cancellable = True

    def __init__(
        self,
        parent: Gtk.Window,
        needs_bridge: bool,
        on_finished=None,
        monitor: tuple[int, int] = (0, 0),
        on_result=None,
        on_work=None,
    ) -> None:
        super().__init__(title=tr("Подготовка Waydroid"), content_width=640, content_height=680)
        self._parent = parent
        self._needs_bridge = needs_bridge
        # Замок окна на время работы: on_work(True) зовётся перед стартом
        # шагов и может отказать (вернуть False) — тогда мастер не стартует;
        # on_work(False) — когда шаги кончились, чем бы ни кончились. Пока
        # мастер просто открыт (показывает план или итог), окно свободно:
        # замок на всё время, что диалог на экране, запирал бы трей и опрос
        # состояния и тогда, когда ничего не идёт.
        self._on_work = on_work
        self._working = False
        # Чем кончилось: on_result(получилось, ключ шага, на котором встало).
        # В отличие от on_finished, зовётся и при неудаче, и при отмене —
        # обновлению важно знать, что оно оборвалось.
        self._on_result = on_result
        self._reported = False
        self._closed = False
        # Главная кнопка после неудачи шага копирует его команду (см. _failed).
        self._primary_copies = False
        # Главная кнопка закрывает мастер: всё сделано (см. _show_success).
        self._primary_closes = False
        # Размер монитора нужен, чтобы посчитать разрешение рендера: узнать
        # его может только окно, у хоста мы бы спрашивали лишнее.
        self._monitor = monitor
        self._on_finished = on_finished
        self._runner: waydroid.StepRunner | None = None
        self._steps: list[waydroid.Step] = []
        self._rows: list[StepRow] = []
        self._index = 0
        self._log_lines = 0
        # Хвост вывода: из него берётся объяснение, когда шаг не удался.
        self._recent: collections.deque[str] = collections.deque(maxlen=80)
        self._started_at = 0.0
        self._step_started_at = 0.0
        self._pulse_source = 0
        self._clock_source = 0
        self._running = False

        self._build()
        self._reload_plan()

    # -- интерфейс -------------------------------------------------------

    def _build(self) -> None:
        self.phase_label = Gtk.Label(label=tr("Проверяем, что уже готово"), xalign=0.5)
        self.phase_label.add_css_class("title-2")
        self.phase_label.set_wrap(True)
        self.phase_label.set_justify(Gtk.Justification.CENTER)

        self.detail_label = Gtk.Label(label="", xalign=0.5)
        self.detail_label.add_css_class("dim-label")
        self.detail_label.set_wrap(True)
        # Сюда попадает и вывод команд: путь или контрольную сумму по словам
        # не перенести, и без переноса по буквам они распирали бы окно.
        self.detail_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
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

        self.group = Adw.PreferencesGroup(title=tr("Шаги"))

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
            title=tr("Журнал"), subtitle=tr("Полный вывод команд на хосте")
        )
        self.log_expander.add_row(Adw.ActionRow(child=self.log_scroll))
        log_group = Adw.PreferencesGroup()
        log_group.add(self.log_expander)

        self.primary = Gtk.Button(label=tr("Установить"))
        self.primary.add_css_class("suggested-action")
        self.primary.add_css_class("pill")
        self.primary.connect("clicked", self._on_primary)

        self.secondary = Gtk.Button(label=tr("Отмена"))
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
        self.recheck.set_tooltip_text(tr("Проверить заново"))
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

        self.phase_label.set_text(tr("Проверяем, что уже готово"))
        self.detail_label.set_text(tr("Опрашиваем хост"))
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
        # До ветки «план пуст»: и там кнопка обязана закрывать окно, а не
        # копировать команду шага, не удавшегося в прошлый раз.
        self._reset_primary()
        self.percent_label.remove_css_class("success")

        if not self._steps:
            self._show_success(tr("Waydroid готов"), tr("Можно запускать приложения"))
            return

        minutes = sum(step.minutes for step in self._steps)
        self.percent_label.set_text("")
        self.phase_label.set_text(tr("Готовы установить"))
        self.detail_label.set_text(
            tr("{count} шаг(ов), примерно {minutes} мин. "
               "Шаги с правами root подтверждаются паролем — Merci его не видит.",
               count=len(self._steps), minutes=minutes)
        )
        self.progress.set_fraction(0.0)
        self.step_label.set_text("")
        self.elapsed_label.set_text("")

        for step in self._steps:
            row = StepRow(step)
            row.set_state("wait")
            self.group.add(row)
            self._rows.append(row)

    def _reset_primary(self) -> None:
        """Главная кнопка снова запускает шаги.

        После неудачи она копировала команду шага, и «Проверить заново»
        приводило в тупик: надпись «Готовы установить», а кнопка под ней
        по-прежнему «Скопировать команду» — запустить было нечем.
        """
        if self._primary_copies:
            self.primary.disconnect_by_func(self._on_copy)
            self.primary.connect("clicked", self._on_primary)
            self._primary_copies = False
        self._primary_closes = False
        self.primary.set_label(tr("Установить"))
        self.primary.add_css_class("suggested-action")
        self.primary.set_visible(True)
        # Вторая кнопка после неудачи называлась «Закрыть»; при новом
        # запуске она снова отменяет шаг — и называться должна так же.
        self.secondary.set_label(tr("Отмена"))
        self.secondary.set_visible(False)

    def _can_cancel(self) -> bool:
        """Можно ли прервать то, что идёт сейчас.

        Нельзя — когда мастер неотменяем целиком (обновления) или шаг дошёл
        до работы под root: её «Отмена» не останавливает (см. NO_CANCEL_MARK
        в waydroid.py).
        """
        return self._cancellable and not (self._runner is not None and self._runner.locked)

    def _on_step_locked(self) -> None:
        """Шаг сообщил, что дальше его не прервать."""
        self.secondary.set_visible(False)

    # -- выполнение ------------------------------------------------------

    def _on_primary(self, *_args) -> None:
        if self._running or self._closed:
            # Закрытый мастер шагов не запускает: отложенный автозапуск мог
            # прийти уже после того, как окно закрыли.
            return
        if not self._steps or self._primary_closes:
            # «Закрыть» после удачи. Раньше кнопка закрывала мастер, только
            # когда план был пуст с самого начала: после пройденных шагов
            # она запускала проверку итога заново — мастер оставался на
            # экране, а открывшему его второй раз сообщалось «готово».
            self.close()
            return
        if not self._set_working(True):
            self.detail_label.set_text(
                tr("Окно Merci занято другой операцией — дождитесь её конца "
                   "и нажмите ещё раз")
            )
            return
        self._running = True
        self._reported = False
        self._started_at = time.monotonic()
        self.primary.set_visible(False)
        self.recheck.set_sensitive(False)
        self.set_can_close(False)
        self._clock_source = GLib.timeout_add_seconds(1, self._tick)
        self._run_current()

    def _set_working(self, working: bool) -> bool:
        """Говорит открывшему мастер, что шаги пошли или кончились.

        Отдаёт False, только если начать нельзя: окно занято другим.
        """
        if working == self._working:
            return True
        if self._on_work is not None and self._on_work(working) is False and working:
            return False
        self._working = working
        return True

    def _on_secondary(self, *_args) -> None:
        if self._running:
            if not self._can_cancel():
                return
            if self._runner is not None:
                self._runner.cancel()
            self._append_log(tr("отменено пользователем"))
            self._stop_running()
            self._set_working(False)
            self._report(False, "cancelled")
            self.phase_label.set_text(tr("Установка отменена"))
            self.detail_label.set_text(tr("Незавершённый шаг можно повторить"))
            self.primary.set_label(tr("Повторить"))
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
        if step.manual:
            self._manual(step, row)
            return
        row.set_state("run")
        self._step_started_at = time.monotonic()

        self.step_label.set_text(tr("Шаг {n} из {total}", n=self._index + 1, total=len(self._steps)))
        self.phase_label.set_text(tr(step.title))
        self.detail_label.set_text(tr(step.hint))
        self.percent_label.set_text("")
        self._pulse(True)
        self._append_log(f"$ {step.command_line()}")

        self._runner = waydroid.StepRunner(
            step, self._append_log, self._step_done, self._on_step_progress,
            self._on_step_locked,
        )
        # «Отмена» — пока шаг и правда можно отменить.
        self.secondary.set_visible(self._can_cancel())
        self._runner.start()

    def _manual(self, step: waydroid.Step, row) -> None:
        """Шаг, который Merci выполнить не может или не вправе.

        На этой системе она не знает нужной команды — либо та меняет
        систему целиком (слой образа с перезагрузкой), и решать это человеку.
        Показываем, что сделать, и ждём: после этого — «Проверить снова».
        """
        row.set_state("fail")
        self._stop_running()
        self._set_working(False)
        self.set_can_close(True)
        self.recheck.set_sensitive(True)
        self.phase_label.set_text(tr("Это нужно сделать самому: {title}", title=tr(step.title)))
        self.detail_label.set_text(tr(step.hint))
        self._append_log(tr(step.hint))
        self.log_expander.set_expanded(True)
        self.primary.set_visible(False)
        self.secondary.set_label(tr("Закрыть"))
        self.secondary.set_visible(True)

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
        self.elapsed_label.set_text(
            tr("прошло {time}", time=_duration(time.monotonic() - self._started_at))
        )
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

    # Тело класса выполняется при импорте — то есть раньше, чем выбран язык,
    # поэтому здесь лежат русские исходники, а перевод берётся в _failed,
    # в момент показа.
    # У шага транслятора готовой подсказки больше нет: она говорила про
    # оборванную загрузку, а шаг «bridge» теперь — работа помощника, и не
    # удаться он может по совсем другой причине (её и покажет _failed).
    _HINTS = {
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

    def _report(self, ok: bool, key: str = "") -> None:
        """Сообщает итог тому, кто открыл мастер, — один раз за прогон."""
        if self._on_result is not None and not self._reported:
            self._reported = True
            self._on_result(ok, key)

    def _failed(self, step: waydroid.Step) -> None:
        self._stop_running()
        self._set_working(False)
        self.set_can_close(True)
        self.recheck.set_sensitive(True)
        self._report(False, step.key)

        hint = self._HINTS.get(step.key)
        if hint is None:
            title = tr("Шаг «{title}» не выполнился", title=tr(step.title))
            detail = tr("Подробности в журнале. Ту же команду можно выполнить вручную — "
                        "кнопка скопирует её в буфер обмена.")
            # Что сказал сам шаг — первым: это и есть ответ на «почему».
            # Вывод шагов идёт по-русски; у главных отказов есть перевод.
            reason = "\n".join(tr(line) for line in failure_reason(self._recent).splitlines())
            if reason:
                detail = reason + "\n\n" + detail
        else:
            title, detail = (tr(text) for text in hint)
        self.phase_label.set_text(title)
        self.detail_label.set_text(detail)
        self.log_expander.set_expanded(step.key not in self._HINTS)
        # Журнал, пока был свёрнут, не прокручивался: раскрытый, он показал бы
        # начало — команду шага, — а причина неудачи в конце.
        GLib.timeout_add(400, self._scroll_log_to_end)
        self.primary.set_label(tr("Скопировать команду"))
        self.primary.remove_css_class("suggested-action")
        self.primary.set_visible(True)
        self.secondary.set_label(tr("Закрыть"))
        self.secondary.set_visible(True)
        self._command_to_copy = step.command_line()
        if not self._primary_copies:
            self.primary.disconnect_by_func(self._on_primary)
            self.primary.connect("clicked", self._on_copy)
            self._primary_copies = True

    def _on_copy(self, *_args) -> None:
        self.get_clipboard().set(self._command_to_copy)
        self._append_log(tr("команда скопирована в буфер обмена"))
        self.detail_label.set_text(tr("Команда в буфере обмена"))

    def _all_done(self) -> None:
        self._stop_running()
        waydroid.forget_state()
        # Состояние спрашиваем в потоке: `waydroid status` на только что
        # поднятом контейнере отвечает секундами, и окно на это время
        # замирало — ровно в момент, когда человек ждёт слова «готово».
        self.phase_label.set_text(tr("Проверяем, что получилось…"))
        waydroid.in_thread(
            lambda: waydroid.status(use_cache=False),
            lambda result, _error: self._closed or self._show_outcome(
                *(result or (False, "состояние неизвестно"))
            ),
        )

    def _show_outcome(self, ready: bool, detail: str) -> None:
        self._set_working(False)
        if ready:
            self._show_success(
                self._done_title or tr("Waydroid готов"),
                tr("Заняло {time}", time=_duration(time.monotonic() - self._started_at)),
            )
        else:
            self._primary_closes = False
            self.phase_label.set_text(tr("Почти готово"))
            self.detail_label.set_text(tr(detail))
            self.percent_label.set_text("")
            self.primary.set_label(tr("Проверить снова"))
            self.primary.set_visible(True)
            self.secondary.set_visible(False)
        self.set_can_close(True)
        self.recheck.set_sensitive(True)
        if self._on_finished is not None:
            self._on_finished()
        self._report(ready, "" if ready else "session")

    def _show_success(self, title: str, detail: str) -> None:
        self._pulse(False)
        self.progress.set_fraction(1.0)
        self.percent_label.set_text("100%")
        self.percent_label.add_css_class("success")
        self.phase_label.set_text(title)
        self.detail_label.set_text(detail)
        self.step_label.set_text("")
        self._primary_closes = True
        self.primary.set_label(tr("Закрыть"))
        self.primary.set_visible(True)
        self.primary.set_sensitive(True)
        self.secondary.set_visible(False)

    # -- журнал ----------------------------------------------------------

    def _append_log(self, line: str) -> None:
        self._recent.append(line)
        buffer = self.log_view.get_buffer()
        buffer.insert(buffer.get_end_iter(), line + "\n")

        self._log_lines += 1
        if self._log_lines > _LOG_LIMIT:
            start = buffer.get_start_iter()
            end = buffer.get_iter_at_line(self._log_lines - _LOG_LIMIT)[1]
            buffer.delete(start, end)
            self._log_lines = _LOG_LIMIT

        GLib.idle_add(self._scroll_log_to_end)

    def _scroll_log_to_end(self) -> bool:
        adjustment = self.log_scroll.get_vadjustment()
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        return False

    def do_closed(self) -> None:  # noqa: D401
        self._closed = True
        if self._runner is not None and self._can_cancel():
            self._runner.cancel()
        self._stop_running()
        # Страховка: во время шага мастер закрыть нельзя, но замок окна не
        # должен пережить мастер ни при каком раскладе.
        self._set_working(False)


class StepsDialog(InstallerDialog):
    """Тот же экран, но для заранее известных шагов.

    Нужен действиям вроде смены транслятора и обновлений: план строить не
    из чего, а прогресс, журнал и отмена — те же самые.
    """

    def __init__(
        self,
        parent: Gtk.Window,
        steps: list[waydroid.Step],
        on_finished=None,
        on_result=None,
        title: str = "",
        done_title: str = "",
        autostart: bool = False,
        cancellable: bool = True,
    ) -> None:
        self._given = list(steps)
        self._done_title = done_title
        self._cancellable = cancellable
        # Человек уже ответил «да» в вопросе перед мастером — второй раз
        # нажимать «Установить» незачем.
        self._autostart = autostart
        super().__init__(parent, needs_bridge=False, on_finished=on_finished, on_result=on_result)
        if title:
            self.set_title(title)
        if autostart and self._given:
            # Шаг стартует в ближайший оборот цикла; успей человек закрыть
            # мастер раньше, окно сняло бы свой замок, а шаг всё равно
            # пошёл бы — без мастера и без замка.
            self.set_can_close(False)
        # «Проверить заново» пересчитывает план, а здесь он задан: кнопка
        # лишь возвращала бы к началу уже пройденные шаги — причём мимо
        # вопроса и замков, под которыми их запускает окно.
        self.recheck.set_visible(False)

    def _reload_plan(self) -> None:
        for row in self._rows:
            self.group.remove(row)
        self._rows.clear()
        self._apply_plan(list(self._given))
        if self._autostart and self._steps:
            self._autostart = False
            GLib.idle_add(lambda: (self._on_primary(), False)[1])


class SingleStepDialog(StepsDialog):
    """Один заранее известный шаг.

    Открывают его после вопроса, на который человек уже ответил кнопкой с
    названием действия («Убрать», «Переключить»), поэтому шаг стартует сам.
    Иначе следом шла вторая кнопка — «Установить», одна на все случаи: ею
    приходилось подтверждать и удаление.

    Отменить шаг можно, пока он качает или собирает; с началом работы под
    root «Отмена» пропадает (см. _can_cancel).
    """

    def __init__(
        self,
        parent: Gtk.Window,
        step: waydroid.Step,
        on_finished=None,
        on_result=None,
        done_title: str = "",
    ) -> None:
        super().__init__(
            parent,
            [step],
            on_finished=on_finished,
            on_result=on_result,
            title=tr(step.title),
            done_title=done_title,
            autostart=True,
        )
