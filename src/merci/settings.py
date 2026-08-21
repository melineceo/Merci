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
    def multiuser(self) -> bool:
        return bool(self.get("multiuser", False))

    @multiuser.setter
    def multiuser(self, value: bool) -> None:
        self.set("multiuser", bool(value))
