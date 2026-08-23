"""Язык интерфейса.

Переводим по самому тексту, а не по выдуманным ключам: в коде остаётся
`tr("Запустить")`, а словарь сопоставляет русскую строку английской. У такого
подхода два свойства, ради которых он и выбран:

* пропущенный перевод показывает русскую строку, а не «window.play_button» —
  интерфейс остаётся рабочим, просто частично непереведённым;
* строки видно прямо в коде, и читать его можно без словаря под рукой.

Язык хранится в настройках и применяется при следующем построении окна:
GTK забирает текст у виджета один раз, поэтому смена языка перестраивает окно
целиком — так же, как это делает переключение темы в приложениях GNOME.
"""

from __future__ import annotations

import locale
import os

# Поддерживаемые языки: код -> название на нём самом.
LANGUAGES = {
    "ru": "Русский",
    "en": "English",
}

_current = "ru"
_table: dict[str, str] = {}


def system_language() -> str:
    """Язык системы, если он нам знаком. Иначе английский.

    Русскому пользователю приложение должно открыться по-русски без
    настройки, а всем остальным — по-английски: русский интерфейс для того,
    кто его не читает, хуже отсутствия перевода.
    """
    raw = ""
    for name in ("LC_ALL", "LC_MESSAGES", "LANG"):
        raw = os.environ.get(name, "")
        if raw:
            break
    if not raw:
        try:
            raw = locale.getlocale()[0] or ""
        except ValueError:
            raw = ""
    return "ru" if raw.lower().startswith("ru") else "en"


def set_language(code: str) -> None:
    """Переключает язык. Неизвестный код — русский."""
    global _current, _table
    _current = code if code in LANGUAGES else "ru"
    if _current == "en":
        from .lang_en import TABLE

        _table = TABLE
    else:
        _table = {}


def language() -> str:
    return _current


def tr(text: str, **fmt: object) -> str:
    """Перевод строки. Подстановки — как у str.format.

    Не найденный перевод возвращает исходную строку: пропуск в словаре
    выглядит как непереведённая надпись, а не как поломка.
    """
    out = _table.get(text, text)
    return out.format(**fmt) if fmt else out
