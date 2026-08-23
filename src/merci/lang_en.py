"""Английский словарь интерфейса.

Ключ — русская строка из кода, значение — её перевод. Порядок разделов
повторяет порядок в интерфейсе: сначала главное окно, потом мастер
подготовки, потом шаги и сообщения о состоянии контейнера.

Строки с подстановками (``{name}``) обязаны сохранять те же имена: подстановка
делается уже после перевода.
"""

from __future__ import annotations

TABLE: dict[str, str] = {
    # ---------------------------------------------------- главное окно ----
    "Работает поверх": "Built on",
    "Библиотека APK с запуском через Waydroid: перетащите файл — "
    "Merci разберёт манифест, подготовит контейнер и отдаст "
    "приложение ему.":
        "An APK library that launches through Waydroid: drop a file in and "
        "Merci parses the manifest, prepares the container and hands the app "
        "over to it.",

    "{value} ГБ": "{value} GB",
    "Б": "B",
    "КБ": "KB",
    "МБ": "MB",
    "ГБ": "GB",
    "ещё не запускалось": "never launched",
    "только что": "just now",
    "{n} мин назад": "{n} min ago",
    "{n} ч назад": "{n} h ago",

    # ------------------------------------------------------------ меню ----
    "Добавить APK": "Add APK",
    "Подготовить Waydroid": "Set up Waydroid",
    "Окно Android": "Android window",
    "Выключить Waydroid": "Stop Waydroid",
    "Журнал сбоев Android": "Android crash log",
    "Сменить транслятор ARM": "Switch ARM translator",
    "Установить root (Magisk)": "Install root (Magisk)",
    "Убрать root (Magisk)": "Remove root (Magisk)",
    "Аппаратное ускорение NVIDIA": "NVIDIA hardware acceleration",
    "Исправить мерцание картинки": "Fix flickering picture",
    "Ссылки в браузере хоста": "Links in the host browser",
    "Настройки": "Settings",
    "Папка библиотеки": "Library folder",
    "О программе": "About",
    "Перезапустить Waydroid": "Restart Waydroid",

    # -------------------------------------------------------- действия ----
    "Подготовить": "Set up",
    "Подставить размер монитора": "Use monitor size",
    "Подставил {w}x{h} — нажмите галочку, чтобы применить":
        "Filled in {w}x{h} — press the check mark to apply",
    "Перезапускаем контейнер…": "Restarting the container…",
    "Поднимаем контейнер…": "Starting the container…",
    "Отмена": "Cancel",
    "Установить": "Install",
    "Заменить": "Replace",
    "Закрыть": "Close",
    "Переключить": "Switch",
    "Собрать и установить": "Build and install",
    "Исправить": "Fix",
    "Включить": "Enable",
    "Убрать": "Remove",
    "Потом": "Later",
    "Скопировать": "Copy",
    "Перезапустить контейнер": "Restart container",
    "Выключить": "Stop",
    "Выключаем контейнер…": "Stopping the container…",
    "Удалить": "Delete",
    "Понятно": "Got it",
    "Открыть окно Android": "Open the Android window",
    "Запустить": "Launch",
    "Готовим…": "Preparing…",
    "Читаем отчёт о падении": "Reading the crash report",
    "Подробности": "Details",

    # ------------------------------------------------------- состояния ----
    "Разрешение рендера, растянется на монитор (напр. 1600x900)":
        "Render resolution, stretched to the monitor (e.g. 1600x900)",
    "Разрешение окна (напр. 1600x900)": "Window size (e.g. 1600x900)",
    "проверяем состояние…": "checking state…",
    "проверяем…": "checking…",
    "спрашиваем контейнер…": "asking the container…",
    "другая{version} — при запуске Merci предложит заменить":
        "a different one{version} — Merci will offer to replace it on launch",
    " (версия {v})": " (version {v})",
    "Новый профиль…": "New profile…",
    "заводим профиль…": "creating the profile…",
    "Android-приложения (*.apk)": "Android applications (*.apk)",
    "{name} уходит в Waydroid": "{name} is going to Waydroid",
    "другая сборка": "another build",
    "из профиля Android №{n}": "from Android profile #{n}",
    "из контейнера": "from the container",
    "{name}: убираем из контейнера…": "{name}: removing from the container…",
    "{name} удалено": "{name} deleted",

    # ------------------------------------------------------- библиотека ----
    "Есть код под этот процессор: пойдёт напрямую, без трансляции":
        "Has code for this CPU: runs directly, without translation",
    "Только ARM-код: пойдёт через трансляцию в контейнере":
        "ARM code only: runs through translation inside the container",
    "Открыть Merci": "Open Merci",
    "Открыть запущенную игру": "Open the running game",
    "Включить Waydroid": "Start Waydroid",
    "Выйти из Merci": "Quit Merci",
    "Нечего открывать: ещё ничего не запускали":
        "Nothing to open: nothing has been launched yet",
    "Пусто": "Empty",
    "Перетащите APK в окно": "Drop an APK into the window",
    "Библиотека": "Library",
    "Перетащите APK сюда": "Drop an APK here",
    "Merci разберёт манифест и сам выберет, как запускать: APK с кодом "
    "под этот процессор идут внутри Merci, APK только под ARM — через "
    "Waydroid.":
        "Merci reads the manifest and picks how to run it: APKs with code for "
        "this CPU run inside Merci, ARM-only APKs go through Waydroid.",
    "Выбрать файл…": "Choose a file…",
    "Сведения": "Details",
    "Пакет": "Package",
    "Архитектура": "Architecture",
    "Занимает места": "Disk usage",
    "Последний запуск": "Last launch",
    "Запуск": "Launch",
    "Профиль Android": "Android profile",
    "Сборка в контейнере": "Build in the container",
    "Трансляция ARM64 → x86_64": "ARM64 → x86_64 translation",
    "Рендер контейнера": "Container rendering",
    "Полный рабочий стол контейнера": "The container's full desktop",
    "если контейнер запущен, а сети у него нет":
        "if the container is running but has no network",
    "Управление": "Manage",
    "Удалить из библиотеки": "Delete from the library",
    "APK и все его данные": "the APK and all of its data",
    "Удалить из контейнера": "Remove from the container",
    "снять установку в Waydroid, запись в библиотеке останется":
        "uninstall from Waydroid; the library entry stays",
    "версия {version}": "version {version}",
    "по умолчанию": "default",
    "без нативного кода": "no native code",

    # ---------------------------------------------------------- диалоги ----
    "Не удалось определить размер монитора": "Could not determine the monitor size",
    "Нужен gamescope": "gamescope is required",
    "Чтобы рисовать в {w}x{h} и занимать весь экран, "
    "контейнер запускается внутри gamescope — он и растягивает "
    "картинку. Waydroid сам этого не умеет: его окно всегда равно "
    "разрешению контейнера.\n\nПоставить gamescope из репозитория?":
        "To render at {w}x{h} and still fill the screen, the container runs "
        "inside gamescope — it does the stretching. Waydroid cannot do this "
        "itself: its window always matches the container resolution.\n\n"
        "Install gamescope from the repository?",
    "контейнер не ответил": "the container did not answer",
    "не установлена — поставится при запуске":
        "not installed — will be installed on launch",
    "эта — можно запускать": "this one — ready to launch",
    "контейнер не ответил: {error}": "the container did not answer: {error}",
    "основной": "main",
    "№{n} — {name}": "#{n} — {name}",
    "свои данные приложения: вход, кеш, настройки":
        "its own app data: login, cache, settings",
    "общие данные приложения": "shared app data",
    "Готов профиль №{n}": "Profile #{n} is ready",
    "состояние неизвестно": "state unknown",
    "{text}. Меньше разрешение — выше частота кадров":
        "{text}. Lower resolution means a higher frame rate",
    "сессия запущена": "session running",
    "{detail} — нажмите, чтобы подготовить": "{detail} — click to set up",
    "контейнер работает — остановить его и все приложения в нём":
        "the container is running — stop it and everything inside",
    "контейнер не запущен": "the container is not running",
    "не настроена — arm64-APK Waydroid не примет":
        "not configured — Waydroid will not accept arm64 APKs",
    "Выберите APK": "Choose an APK",
    "Такой источник перетащить нельзя — выберите файл вручную":
        "That source cannot be dropped — pick the file manually",
    "{file}: это не APK": "{file}: not an APK",
    "Добавляем APK": "Adding the APK",
    "{file} копируется в библиотеку…": "{file} is being copied to the library…",
    "Окно откроет Waydroid": "Waydroid will open the window",
    "В контейнере другая сборка этого приложения":
        "The container holds a different build of this app",
    "Пакет {package} уже занят: там стоит {other}. "
    "Android держит одно имя пакета как одну установку на всё "
    "устройство — профили делят между собой код приложения и "
    "различаются только данными, поэтому две разные сборки рядом "
    "не живут.\n\nЗаменить установку на «{name}»? Данные прежней "
    "сборки внутри Android будут стёрты — так требует Android при "
    "смене подписи.":
        "The package {package} is taken: {other} is installed there. For "
        "Android one package name is one installation for the whole device — "
        "profiles share the app code and differ only in data, so two different "
        "builds cannot live side by side.\n\nReplace the installation with "
        "“{name}”? The previous build's data inside Android will be erased — "
        "Android requires that when the signature changes.",
    "{name}: установка заменена, открываем":
        "{name}: installation replaced, opening",
    "Waydroid не запустил APK": "Waydroid did not launch the APK",
    "{message}\n\nОткрыть проверку готовности?":
        "{message}\n\nOpen the readiness check?",
    "Переключить на {target}?": "Switch to {target}?",
    "Сейчас стоит {current}. Если приложение падает внутри "
    "транслятора, второй вариант иногда справляется. Займёт несколько "
    "минут и потребует пароль.":
        "{current} is installed right now. When an app crashes inside the "
        "translator, the other one sometimes copes. Takes a few minutes and "
        "asks for your password.",
    "Перехватчик ссылок уже установлен": "The link forwarder is already installed",
    "Открывать ссылки на компьютере?": "Open links on the computer?",
    "Сейчас ссылка из приложения открывается браузером самого Android: "
    "передачи ссылок из контейнера на хост в Waydroid нет — это открытая "
    "заявка waydroid#210.\n\nMerci соберёт маленькое Android-приложение, "
    "которое ловит ссылку и отдаёт её службе на хосте, а та открывает её "
    "вашим браузером. Для сборки нужны JDK и инструменты Android SDK — "
    "Merci поставит их сама. Займёт несколько минут и потребует пароль.":
        "Right now a link from an app opens in Android's own browser: Waydroid "
        "has no way to pass links from the container to the host — that is open "
        "issue waydroid#210.\n\nMerci will build a tiny Android app that "
        "catches the link and hands it to a service on the host, which opens it "
        "in your browser. Building needs a JDK and Android SDK tools — Merci "
        "installs them itself. Takes a few minutes and asks for your password.",
    "Подповерхности уже выключены — эта причина исключена":
        "Subsurfaces are already off — that cause is ruled out",
    "Исправить мерцание?": "Fix the flickering?",
    "Android-слои сейчас рисуются в подповерхностях Wayland. Синхронная "
    "подповерхность показывается только когда коммитит родительская "
    "поверхность, поэтому кадр замирает и обновляется лишь на события: "
    "нажатие клавиши, вход курсора в окно, появление экранной "
    "клавиатуры.\n\nMerci выключит этот режим в обоих файлах настроек "
    "и перезапустит контейнер. Потребуется пароль.":
        "Android layers are currently drawn into Wayland subsurfaces. A "
        "synchronous subsurface is only shown when the parent surface commits, "
        "so the frame freezes and refreshes only on events: a key press, the "
        "pointer entering the window, the on-screen keyboard appearing.\n\n"
        "Merci will turn that mode off in both settings files and restart the "
        "container. Your password will be required.",
    "Не подходит для этой машины": "Not suitable for this machine",
    "Включить аппаратное ускорение?": "Enable hardware acceleration?",
    "Сейчас контейнер рисует процессором: Waydroid ходит в Mesa, "
    "а Mesa не умеет проприетарный драйвер NVIDIA.\n\n"
    "waydroid-nvidia подставляет гостю Mesa Venus и проксирует Vulkan "
    "в настоящий драйвер ({detail}), так что рисует видеокарта.\n\n"
    "Пакет waydroid будет заменён на waydroid-nvidia-bin — это тот же "
    "Waydroid с патчами, образ Android и данные остаются на месте. "
    "Частота обновления возьмётся из монитора: {refresh} Гц.":
        "The container currently renders on the CPU: Waydroid goes through "
        "Mesa, and Mesa cannot drive the proprietary NVIDIA driver.\n\n"
        "waydroid-nvidia gives the guest Mesa Venus and proxies Vulkan into "
        "the real driver ({detail}), so the GPU does the drawing.\n\n"
        "The waydroid package will be replaced with waydroid-nvidia-bin — the "
        "same Waydroid with patches; the Android image and your data stay "
        "where they are. The refresh rate is taken from the monitor: "
        "{refresh} Hz.",
    "Установить root в контейнере?": "Install root inside the container?",
    "Magisk Delta даст root внутри Waydroid — это ваш собственный "
    "Android, так что доступ к системным разделам и модулям тут "
    "нормальная вещь.\n\nПроверку устройства играми это не проходит: "
    "для них контейнер с root выглядит наоборот подозрительнее. "
    "Займёт несколько минут и потребует пароль.":
        "Magisk Delta gives you root inside Waydroid — it is your own Android, "
        "so access to system partitions and modules is a normal thing here."
        "\n\nIt does not pass device checks in games: to them a rooted "
        "container looks even more suspicious. Takes a few minutes and asks "
        "for your password.",
    "Убрать root из контейнера?": "Remove root from the container?",
    "Magisk Delta будет снят, контейнер перезапустится. Модули, "
    "которые вы через него ставили, перестанут работать.\n\n"
    "Займёт несколько минут и потребует пароль.":
        "Magisk Delta will be removed and the container restarted. Modules you "
        "installed through it will stop working.\n\nTakes a few minutes and "
        "asks for your password.",

    # -------------------------------------------------------- настройки ----
    "Профили Android": "Android profiles",
    "Профили Android дают одному приложению отдельные "
    "данные: свой вход, свой кеш, свои настройки. Так запускают "
    "второй аккаунт, не выходя из первого.\n\n"
    "Двух разных сборок одного пакета это не даёт и дать не может: "
    "имя пакета для Android — одна установка на всё устройство, "
    "профили делят между собой код приложения.":
        "Android profiles give one app separate data: its own login, its own "
        "cache, its own settings. That is how you run a second account without "
        "logging out of the first.\n\nThey do not give you two different "
        "builds of one package, and cannot: for Android a package name is one "
        "installation for the whole device, and profiles share the app code.",
    "Использовать MultiUser": "Use MultiUser",
    "в карточке появится выбор профиля; контейнер "
    "переключается на нужный при запуске":
        "a profile selector appears in the card; the container switches to it "
        "on launch",
    "Окно": "Window",
    "Сворачивать Merci при запуске приложения":
        "Hide Merci when an app launches",
    "окно прячется в трей через пару секунд после запуска; "
    "вернуть — нажатием на значок":
        "the window goes to the tray a couple of seconds after launch; click "
        "the icon to bring it back",
    "Язык интерфейса": "Interface language",
    "сохраняется; окно перерисуется сразу":
        "saved; the window is rebuilt right away",
    "MultiUser выключен: запуск идёт в основном профиле":
        "MultiUser is off: launching in the main profile",
    "не удалось спросить хост": "could not ask the host",
    "MultiUser включён": "MultiUser is on",
    "Контейнер к этому не готов": "The container is not ready for that",
    "{detail}.\n\nMerci может подготовить его: разрешит Android "
    "нескольких пользователей, откроет себе доступ к контейнеру через "
    "adb и поставит android-tools. Контейнер перезапустится, "
    "потребуется пароль.":
        "{detail}.\n\nMerci can prepare it: allow Android to have several "
        "users, open itself access to the container over adb and install "
        "android-tools. The container will restart and your password will be "
        "required.",
    "Записей о сбоях нет.": "No crash reports.",
    "Контейнер не отвечает": "The container is not responding",
    "{message}.\n\nAndroid внутри переживает перезапуск сессии, "
    "поэтому его мало: нужен перезапуск самого контейнера. Всё "
    "запущенное в нём закроется, потребуется пароль.":
        "{message}.\n\nAndroid inside survives a session restart, so that is "
        "not enough: the container itself has to be restarted. Everything "
        "running inside will close and your password will be required.",
    "Выключить Waydroid?": "Stop Waydroid?",
    "Контейнер остановится, и всё запущенное в нём закроется. "
    "Следующий запуск приложения поднимет его заново — это займёт "
    "около полуминуты.":
        "The container will stop and everything running inside will close. The "
        "next app launch brings it back up — that takes about half a minute.",
    "Удалить из контейнера?": "Remove from the container?",
    "«{name}» будет убрано {where} вместе со своими данными "
    "внутри Android — учётной записью в игре, кешем, настройками.\n\n"
    "APK останется в библиотеке, и запустить его можно будет снова.":
        "“{name}” will be removed {where} together with its data inside "
        "Android — the game account, the cache, the settings.\n\nThe APK "
        "stays in the library and can be launched again.",
    "{name} убрано из контейнера": "{name} removed from the container",
    "Нужно подтвердить в Android": "Confirmation needed in Android",
    "Удалить из библиотеки?": "Delete from the library?",
    "«{name}» и все его данные будут стёрты безвозвратно — "
    "и APK здесь, и установка в контейнере вместе с её данными "
    "внутри Android.":
        "“{name}” and all of its data will be erased for good — the APK here "
        "and the installation in the container along with its data inside "
        "Android.",

    # ------------------------------------------------------- сообщения ----
    "х": "x",
    "Слишком странное разрешение": "That resolution looks wrong",
    "Не удалось настроить дисплей": "Could not configure the display",
    "Готово: размер сброшен": "Done: size reset",
    "спросить не удалось — контейнер не ответил":
        "could not ask — the container did not answer",
    "проверить нечем: adb появится вместе с MultiUser":
        "nothing to check with: adb comes with MultiUser",
    "Профиль не завёлся": "The profile was not created",
    "Waydroid не готов: {detail}": "Waydroid is not ready: {detail}",
    "Нужна трансляция ARM64 → x86_64 (libndk)":
        "ARM64 → x86_64 translation is required (libndk)",
    "Контейнер без доступа в интернет: мешает ufw":
        "The container has no internet access: ufw is in the way",
    "{name}: пакет {package} уже занят — "
    "при запуске Merci предложит заменить установку":
        "{name}: the package {package} is taken — Merci will offer to replace "
        "the installation on launch",
    "{name} добавлено": "{name} added",
    "Приложение упало внутри транслятора": "The app crashed inside the translator",
    "Приложение упало после запуска": "The app crashed after launching",
    "Не выключилось": "It did not stop",
    "Waydroid выключен": "Waydroid stopped",
    "Не удалось открыть окно Android": "Could not open the Android window",
    "Удалить из контейнера не вышло": "Removing from the container failed",
    "Удаление отменено": "Deletion cancelled",
    "Не вышло: {error}": "Did not work: {error}",
    "Контейнер запущен": "The container is running",
    "Разрешение задаётся как 1600x900": "Resolution is written like 1600x900",
    "Готово: рендер {w}x{h} растянут на экран":
        "Done: {w}x{h} render stretched to the screen",
    "Готово: контейнер в {w}x{h}": "Done: container at {w}x{h}",
    "Не получилось добавить APK": "Could not add the APK",
    "Ошибка файловой системы": "File system error",
    "Перезапустить не вышло": "The restart failed",
    "Контейнер перезапущен": "The container has been restarted",
    "профиль": "profile",
    "ничего": "nothing",

    # ------------------------------------------------ мастер подготовки ----
    "{m} мин {s} с": "{m} min {s} s",
    "{n} с": "{n} s",
    "Проверить заново": "Check again",
    "Проверяем, что уже готово": "Checking what is already in place",
    "Опрашиваем хост": "Asking the host",
    "Готовы установить": "Ready to install",
    "{count} шаг(ов), примерно {minutes} мин. "
    "Шаги с правами root подтверждаются паролем — Merci его не видит.":
        "{count} step(s), about {minutes} min. Steps that need root are "
        "confirmed with your password — Merci never sees it.",
    "Шаг {n} из {total}": "Step {n} of {total}",
    "прошло {time}": "{time} elapsed",
    "Не удалось поставить трансляцию ARM64":
        "Could not install ARM64 translation",
    "Архив транслятора качается с GitHub, и соединение оборвалось "
    "(в журнале — SSL: RECORD_LAYER_FAILURE или таймаут). Merci уже "
    "делает три попытки и пробует запасной libhoudini.\n\n"
    "Обычно помогает другая локация VPN: канал должен держать "
    "непрерывную передачу в несколько сотен мегабайт. Всё остальное "
    "уже установлено, повторить можно только этот шаг.":
        "The translator archive is downloaded from GitHub and the connection "
        "broke (the log shows SSL: RECORD_LAYER_FAILURE or a timeout). Merci "
        "already retries three times and falls back to libhoudini.\n\n"
        "Another VPN location usually helps: the link has to hold a continuous "
        "transfer of several hundred megabytes. Everything else is installed "
        "already, so only this step needs repeating.",
    "Сервер образов Waydroid не отвечает":
        "The Waydroid image server is not responding",
    "ota.waydro.id раздаётся через GitHub Pages, и у вас он недоступен. "
    "Сам waydroid init начинает именно с этого адреса и в такой ситуации "
    "висит бесконечно и молча — поэтому Merci проверяет связь заранее."
    "\n\nЧто можно сделать: включить VPN и повторить, либо прописать "
    "своё зеркало в файл ota.conf в данных Merci — первая строка system, "
    "вторая vendor.":
        "ota.waydro.id is served through GitHub Pages and is unreachable from "
        "here. waydroid init starts with exactly that address and in this "
        "situation hangs forever and silently — which is why Merci checks the "
        "connection in advance.\n\nWhat you can do: turn a VPN on and retry, "
        "or write your own mirror into the ota.conf file in Merci's data — "
        "first line system, second line vendor.",
    "Скопировать команду": "Copy the command",
    "команда скопирована в буфер обмена": "the command is on the clipboard",
    "Команда в буфере обмена": "Command copied",
    "{hint} · скачает {what}": "{hint} · downloads {what}",
    "Подготовка Waydroid": "Setting up Waydroid",
    "Шаги": "Steps",
    "Журнал": "Log",
    "Полный вывод команд на хосте": "Full output of the commands on the host",
    "Waydroid готов": "Waydroid is ready",
    "Можно запускать приложения": "You can launch applications",
    "отменено пользователем": "cancelled by the user",
    "Установка отменена": "Installation cancelled",
    "Незавершённый шаг можно повторить": "The unfinished step can be retried",
    "Повторить": "Retry",
    "Шаг «{title}» не выполнился": "The step “{title}” did not complete",
    "Подробности в журнале. Ту же команду можно выполнить вручную — "
    "кнопка скопирует её в буфер обмена.":
        "Details are in the log. The same command can be run by hand — the "
        "button copies it to the clipboard.",
    "Заняло {time}": "Took {time}",
    "Почти готово": "Almost there",
    "Проверить снова": "Check again",

    # ------------------------------------------------ состояние контейнера ----
    "состояние неясно": "state unclear",
    "готово": "ready",
    "не установлен": "not installed",
    "образ Android не загружен": "the Android image is not downloaded",
    "сессия остановлена": "session stopped",
    "недоступен": "unavailable",
    "определить не удалось — контейнер не отвечает":
        "could not determine — the container is not responding",
    "аппаратный (через Venus)": "hardware (through Venus)",
    "драйвер NVIDIA не найден": "the NVIDIA driver was not found",
    "нужны открытые модули ядра (nvidia-open), у вас закрытые":
        "open kernel modules are required (nvidia-open); yours are proprietary",
    "на хосте нет adb (пакет android-tools)":
        "there is no adb on the host (the android-tools package)",
    "в контейнере не разрешено больше одного пользователя":
        "the container does not allow more than one user",

    # -------------------------------------------------------- этапы запуска ----
    "Готовим профиль Android…": "Preparing the Android profile…",
    "Устанавливаем в профиль…": "Installing into the profile…",
    "Переключаем профиль…": "Switching the profile…",
    "Возвращаемся в основной профиль…": "Returning to the main profile…",
    "Устанавливаем в контейнер…": "Installing into the container…",
    "Открываем…": "Opening…",
    "Убираем прежнюю сборку…": "Removing the previous build…",
    "Ждём сеть контейнера…": "Waiting for the container's network…",

    # -------------------------------------------------------------- ошибки ----
    "файл APK пропал из библиотеки": "the APK file has disappeared from the library",
    "неизвестно имя пакета": "the package name is unknown",
    "в APK не нашлось activity для запуска":
        "no launchable activity was found in the APK",
    "сессия не остановилась": "the session did not stop",
    "контейнер не поднялся": "the container did not come up",
    "контейнер не получил адрес в сети":
        "the container did not get a network address",
    "контейнер не переключился на этого пользователя":
        "the container did not switch to that user",
    "Android не даёт завести ещё одного пользователя — предел уже достигнут":
        "Android will not create another user — the limit is already reached",
    "исходники перехватчика не найдены":
        "the forwarder sources were not found",
    "контейнер просит разрешить отладку: откройте окно Android и "
    "нажмите «Разрешить» в появившемся вопросе":
        "the container asks you to allow debugging: open the Android window and "
        "press “Allow” in the prompt that appears",
    "контейнер не дал убрать приложение сам, поэтому Android спросит "
    "об этом в своём окне — подтвердите удаление там":
        "the container would not remove the app itself, so Android will ask "
        "about it in its own window — confirm the removal there",

    # --------------------------------------------------- шаги подготовки ----
    "Модуль ядра binder": "binder kernel module",
    "ядро без поддержки binder — нужен внешний модуль из AUR":
        "the kernel has no binder support — an external module from AUR is needed",
    "Пакет waydroid": "The waydroid package",
    "ставится из репозитория extra": "installed from the extra repository",
    "Образ Android": "Android image",
    "загрузка системного образа LineageOS": "downloading the LineageOS system image",
    "образ Android (~1 ГБ)": "the Android image (~1 GB)",
    "Сессия Waydroid": "Waydroid session",
    "запуск контейнера, прав root не требует":
        "starts the container; does not need root",
    "Связь с сервером образов": "Connection to the image server",
    "libndk_translation через waydroid_script":
        "libndk_translation through waydroid_script",
    "waydroid_script с github.com/casualsnek/waydroid_script и libndk из образа Android x86_64":
        "waydroid_script from github.com/casualsnek/waydroid_script and libndk "
        "from the Android x86_64 image",
    "переустановка через waydroid_script": "reinstall through waydroid_script",
    "Доступ в интернет для контейнера": "Internet access for the container",
    "ufw блокирует DHCP и выход наружу с waydroid0":
        "ufw blocks DHCP and outbound traffic from waydroid0",
    "Открывать ссылки в браузере хоста": "Open links in the host browser",
    "собирает и ставит перехватчик ссылок, поднимает службу":
        "builds and installs the link forwarder, starts the service",
    "инструменты сборки Android (AUR)": "Android build tools (AUR)",
    "выключает подповерхности: из-за них кадр обновляется только на ввод":
        "turns subsurfaces off: they make the frame refresh only on input",
    "Подгонка под экран": "Fit to the screen",
    "берём из загрузок или качаем с докачкой":
        "taken from downloads or fetched with resume support",
    "Установить gamescope": "Install gamescope",
    "им растягивается картинка контейнера на весь экран":
        "it stretches the container picture to the whole screen",
    "waydroid-nvidia: Vulkan гостя проксируется в драйвер хоста":
        "waydroid-nvidia: the guest's Vulkan is proxied into the host driver",
    "waydroid-nvidia-bin из AUR": "waydroid-nvidia-bin from AUR",
    "Перезапустить контейнер целиком": "Restart the whole container",
    "служба waydroid-container и сессия заново":
        "the waydroid-container service and the session anew",
    "Разрешить несколько пользователей Android":
        "Allow several Android users",
    "fw.max_users, доступ к adb контейнера и перезапуск":
        "fw.max_users, access to the container's adb and a restart",
    "перезапуск после правки правил": "restart after editing the rules",
}
