"""Настройки Merci.

Один маленький файл рядом с библиотекой. GSettings не берём: схему пришлось
бы устанавливать в систему и компилировать при сборке ради двух значений,
которые всё равно читаются раз за запуск.
"""

from __future__ import annotations

import json
import os
import threading

from .library import data_root


# Настройки пишут и окно, и ядро — из разных потоков и через разные
# экземпляры Settings. Запись «прочитать, поменять своё, записать» идёт под
# этим замком, иначе две записи подряд теряли бы одна другую.
_WRITE_LOCK = threading.Lock()


class Settings:
    def __init__(self) -> None:
        self.path = os.path.join(data_root(), "settings.json")
        self._values: dict = {}
        self._stamp = -1
        self._refresh()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as handle:
                loaded = json.load(handle)
        except (OSError, ValueError):
            # Испорченный или отсутствующий файл — это просто «настроек нет».
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _refresh(self) -> None:
        """Перечитывает файл, если его с тех пор кто-то записал.

        Экземпляр у окна живёт всё время работы программы, а ядро заводит
        свои, короткие, — и пишет через них (выбранный размер окон,
        например). Раньше окно держало снимок с момента запуска и каждой
        своей записью возвращало в файл устаревшие чужие значения: размер
        окон молча откатывался при первой же проверке обновлений.
        """
        try:
            stamp = os.stat(self.path).st_mtime_ns
        except OSError:
            stamp = 0
        if stamp != self._stamp:
            self._values = self._load()
            self._stamp = stamp

    def get(self, key: str, default=None):
        self._refresh()
        return self._values.get(key, default)

    def set(self, key: str, value) -> None:
        with _WRITE_LOCK:
            # Меняем только свой ключ — в свежем содержимом файла.
            values = self._load()
            values[key] = value
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(values, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            self._values = values
            try:
                self._stamp = os.stat(self.path).st_mtime_ns
            except OSError:
                self._stamp = -1

    @property
    def language(self) -> str:
        """Язык интерфейса: 'ru' или 'en'.

        Пока не выбран — берём язык системы: русскому пользователю Merci
        откроется по-русски без настройки, остальным по-английски.
        """
        from .i18n import LANGUAGES, system_language

        value = self.get("language", "")
        return value if value in LANGUAGES else system_language()

    @language.setter
    def language(self, value: str) -> None:
        self.set("language", value)

    @property
    def minimize_on_launch(self) -> bool:
        """Прятать окно Merci, когда приложение ушло в Waydroid.

        По умолчанию включено: библиотека нужна ровно до нажатия
        «Запустить», а дальше только мешает поверх игры. Вернуть окно —
        нажатием на значок в трее.
        """
        return bool(self.get("minimize_on_launch", True))

    @minimize_on_launch.setter
    def minimize_on_launch(self, value: bool) -> None:
        self.set("minimize_on_launch", bool(value))

    @property
    def reduced_glass(self) -> bool:
        """«Меньше прозрачности»: плотные поверхности вместо стекла.

        Размытие фона считает видеокарта на каждый кадр; на слабой или при
        программной отрисовке оно заметно. Облик остаётся тем же.
        """
        return bool(self.get("reduced_glass", False))

    @reduced_glass.setter
    def reduced_glass(self, value: bool) -> None:
        self.set("reduced_glass", bool(value))

    @property
    def color_scheme(self) -> str:
        """Тема: ``system`` (как на рабочем столе), ``light`` или ``dark``."""
        value = self.get("color_scheme", "system")
        return value if value in ("system", "light", "dark") else "system"

    @color_scheme.setter
    def color_scheme(self, value: str) -> None:
        self.set("color_scheme", value)

    @property
    def auto_update_check(self) -> bool:
        """Проверять обновления самой — раз в сутки и при запуске.

        Проверка ничего не ставит: только смотрит, что вышло, и говорит об
        этом. Ставит человек — у большинства обновлений просят пароль.
        """
        return bool(self.get("auto_update_check", True))

    @auto_update_check.setter
    def auto_update_check(self, value: bool) -> None:
        self.set("auto_update_check", bool(value))

    @property
    def auto_update_merci(self) -> bool:
        """Ставить новую Merci самой, без вопроса.

        Только там, где это не требует пароля (флатпак, установленный для
        пользователя). Новая версия включается после перезапуска программы.
        """
        return bool(self.get("auto_update_merci", True))

    @auto_update_merci.setter
    def auto_update_merci(self, value: bool) -> None:
        self.set("auto_update_merci", bool(value))

    @property
    def hide_android_updater(self) -> bool:
        """Прятать встроенный обновлятор Android.

        Он ставит новый образ по нажатию на своё уведомление — переписывая
        файл образа под работающими окнами. Пока обновлениями занимается
        Merci, он не нужен, а его уведомления только копятся.
        """
        return bool(self.get("hide_android_updater", True))

    @hide_android_updater.setter
    def hide_android_updater(self, value: bool) -> None:
        self.set("hide_android_updater", bool(value))

    @property
    def updates_checked_at(self) -> float:
        """Когда обновления проверялись в последний раз (0 — никогда)."""
        try:
            return float(self.get("updates_checked_at", 0) or 0)
        except (TypeError, ValueError):
            return 0.0

    @updates_checked_at.setter
    def updates_checked_at(self, value: float) -> None:
        self.set("updates_checked_at", float(value))

    @property
    def updates_found(self) -> list[dict]:
        """Что нашла последняя проверка: [{key, title, available}].

        Нужно, чтобы отметка об обновлениях была на месте сразу при запуске,
        а сообщение о находке не повторялось каждый день заново.
        """
        value = self.get("updates_found", [])
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    @updates_found.setter
    def updates_found(self, value: list[dict]) -> None:
        self.set("updates_found", list(value))

    @property
    def window_size(self) -> tuple[int, int]:
        """Выбранный размер окон. (0, 0) — не выбирали.

        Помним его у себя, а не только в waydroid_base.prop: тот файл
        переписывают и Waydroid при обновлении образа, и сторонние
        скрипты — размер оттуда пропадает без предупреждения, и окна
        молча возвращаются к размеру монитора.
        """
        width = int(self.get("window_width", 0) or 0)
        height = int(self.get("window_height", 0) or 0)
        return (width, height) if width >= 320 and height >= 240 else (0, 0)

    @window_size.setter
    def window_size(self, value: tuple[int, int]) -> None:
        self.set("window_width", int(value[0]))
        self.set("window_height", int(value[1]))

    @property
    def multiuser(self) -> bool:
        return bool(self.get("multiuser", False))

    @multiuser.setter
    def multiuser(self, value: bool) -> None:
        self.set("multiuser", bool(value))

    @property
    def eco_mode(self) -> bool:
        """Режим экономии для мульти-инстансов (отключение анимаций, облегчение рендера)."""
        return bool(self.get("eco_mode", False))

    @eco_mode.setter
    def eco_mode(self, value: bool) -> None:
        self.set("eco_mode", bool(value))

    @property
    def fps_limit(self) -> int:
        """Ограничение FPS в Android (0 — без лимита, 15, 30, 45, 60)."""
        return int(self.get("fps_limit", 0))

    @fps_limit.setter
    def fps_limit(self, value: int) -> None:
        self.set("fps_limit", int(value))

    @property
    def audio_mute_clones(self) -> bool:
        """Отключение звука для фоновых окон/клонов."""
        return bool(self.get("audio_mute_clones", False))

    @audio_mute_clones.setter
    def audio_mute_clones(self, value: bool) -> None:
        self.set("audio_mute_clones", bool(value))
