"""Английские строки вида (пакет ``ui``).

Ключ — русская строка из кода вида, значение — перевод. Подключается так
же, как ``lang_en.TABLE``: ``tr`` ищет строку в общем словаре, поэтому
ведущий сливает этот словарь с основным при выборе английского языка.

Где строка уже есть в ``lang_en.TABLE``, перевод взят оттуда же — у одной
русской строки не должно быть двух английских.

Строки с подстановками (``{n}``, ``{title}``) сохраняют те же имена.
Переводы нарочно короткие там, где место ограничено: метки на «экранах»
окон, быстрые кнопки острова.
"""

from __future__ import annotations

TABLE: dict[str, str] = {
    # -- библиотека --
    "Библиотека": "Library",
    "Меню Merci": "Merci menu",
    "Добавить APK": "Add APK",
    "Здесь появятся добавленные APK": "Added APKs will appear here",
    "пока пусто": "empty for now",
    "приложение": "app",
    "приложения": "apps",
    "приложений": "apps",
    "запущено: {n}": "running: {n}",
    "открытых окон: {n}": "open windows: {n}",

    # -- пустая библиотека --
    "Перетащите APK сюда": "Drop an APK here",
    "Игры и приложения Android запустятся в своих окнах — по одному "
    "Android на окно":
        "Android games and apps launch in their own windows — one Android per window",
    "Выбрать файл…": "Choose a file…",

    # -- страница --
    "Сведения": "Details",
    "Запуск": "Launch",
    "Сборка в контейнере": "Build in the container",
    "проверяем…": "checking…",
    "Профиль Android": "Android profile",
    "Управление записью": "Manage entry",
    "Удалить из контейнера": "Remove from the container",
    "Удалить из библиотеки": "Delete from the library",
    "Запустить": "Launch",

    # -- окна --
    "Окна": "Windows",
    "Окно": "Window",
    "Запустить все": "Launch all",
    "Остановить все": "Stop all",
    "Выключить дополнительные окна": "Power off extra windows",
    "Действия с окнами": "Window actions",
    "Добавить окно — ещё один Android": "Add a window — one more Android",
    "Ещё окно": "One more window",
    "отдельный Android со своим аккаунтом": "a separate Android with its own account",
    "Проверяем окна…": "Checking windows…",
    "Окон пока нет": "No windows yet",
    "Окно: действия": "Window actions",
    "{title}: действия": "{title}: actions",
    "Открыть приложение": "Open the app",
    "Показать приложение": "Show the app",
    "Закрыть приложение": "Close the app",
    "Перезапустить окно": "Restart window",
    "Размер окон…": "Window size…",
    "Выключить окно": "Power off window",
    "Удалить окно…": "Delete window…",
    # метки на «экране» окна
    "Открыто": "Open",
    "В фоне": "Background",
    "Закрыто": "Closed",
    "Не установлено": "Not installed",
    "Выключено": "Off",
    "Загрузка": "Booting",
    "Ждём ответа": "Waiting",
    "Не отвечает": "Not responding",
    "Другая сборка": "Other build",
    "Операция": "Busy",

    # -- контейнер --
    "Контейнер Waydroid": "Waydroid container",
    "Контейнер Waydroid — общие настройки": "Waydroid container — shared settings",
    "Настройки контейнера": "Container settings",
    "общий для всех приложений": "shared by all apps",
    "Свернуть": "Collapse",
    "Waydroid не готов к запуску приложений": "Waydroid is not ready to run apps",
    "Подготовить": "Set up",
    "Открыть окно Android": "Open the Android window",
    "Перезапустить": "Restart",
    "Перезапустить Waydroid": "Restart Waydroid",
    "Выключить": "Stop",
    "Выключить Waydroid": "Stop Waydroid",
    "Система": "System",
    "Оптимизация": "Optimization",
    "Трансляция ARM": "ARM translation",
    "Сменить": "Change",
    "Сменить транслятор ARM": "Switch ARM translator",
    "Рендер контейнера": "Container rendering",
    "Размер всех окон": "Size of all windows",
    "ширина × высота, Enter — применить": "width × height, Enter to apply",
    "Подставить размер монитора": "Use monitor size",
    "Применить размер": "Apply size",
    "Режим экономии (Eco Mode)": "Eco Mode",
    "меньше анимаций Android, ниже нагрузка": "fewer Android animations, lower load",
    "Ограничение FPS": "FPS Limit",
    "Без ограничений": "Unlimited",
    "Свободный многооконный режим": "Freeform Multi-Window Mode",
    "все игры и окна открываются рядом": "all games and windows open side by side",
    "Применить": "Apply",
    "Очистить память Android (RAM Trim)": "Clean Android RAM (RAM Trim)",
    "сбросить кэш и неактивную память": "drop caches and inactive memory",
    "Очистить": "Clean",
    "меньше анимаций, ниже нагрузка": "fewer animations, lower load",
    "игры и окна открываются рядом": "games and windows open side by side",
    "Применить свободный многооконный режим": "Apply freeform multi-window mode",
    "Очистить память Android": "Clean Android RAM",
    "RAM Trim: кэш и неактивная память": "RAM Trim: caches and inactive memory",
    "Закрыть": "Close",
    "Окно Android": "Android window",
    "Waydroid: работает": "Waydroid: running",
    "Waydroid: остановлен": "Waydroid: stopped",
    "Waydroid: не готов": "Waydroid: not ready",
    "Waydroid: нет ответа": "Waydroid: no answer",
    "Подробности": "Details",
    "Повторить": "Retry",
    "Waydroid: подождите…": "Waydroid: please wait…",
    "Многооконный режим": "Multi-window mode",

    # -- переименование и обновления --
    "Переименовать запись": "Rename the entry",
    "Переименовать «{name}»": "Rename “{name}”",
    "Обновления": "Updates",
    "Обновления: {n}": "Updates: {n}",
    "Есть обновления компонентов": "Component updates are available",

    # -- ход работы контейнера --
    "Контейнер: {state}": "Container: {state}",
    "Контейнер готовится…": "The container is getting ready…",
    "Применяем размер окон…": "Applying the window size…",
}
