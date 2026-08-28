"""Настройки Merci.

Один маленький файл рядом с библиотекой. GSettings не берём: схему пришлось
бы устанавливать в систему и компилировать при сборке ради двух значений,
которые всё равно читаются раз за запуск.
"""

from __future__ import annotations

import json
import os

from .library import data_root


class Settings:
    def __init__(self) -> None:
        self.path = os.path.join(data_root(), "settings.json")
        self._values: dict = {}
        try:
            with open(self.path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                self._values = loaded
        except (OSError, ValueError):
            # Испорченный или отсутствующий файл — это просто «настроек нет».
            pass

    def get(self, key: str, default=None):
        return self._values.get(key, default)

    def set(self, key: str, value) -> None:
        self._values[key] = value
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self._values, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

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
