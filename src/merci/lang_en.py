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
    "контейнер ещё поднимается — сеть внутри не готова":
        "the container is still coming up — its network is not ready yet",
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
    # Копии приложений: профиль-клон Android и многооконный режим.
    "Копия приложения": "App copy",
    # Окна — отдельные контейнеры Android, по одному на окно.
    "Окна": "Windows",
    "Поднимаем основное окно…": "Bringing the main window up…",
    "Android внутри не отвечает — помогает полный перезапуск контейнера":
        "Android inside is not answering — a full container restart helps",
    "Размер окон": "Window size",
    "Android не поднялся — перезапускаем контейнер целиком…":
        "Android did not come up — restarting the whole container…",
    "Размер всех окон (напр. 1600x900)": "Size of every window (e.g. 1600x900)",
    "Возвращаем выбранный размер окон…": "Restoring the chosen window size…",
    "Приводим размер основного окна к общему…":
        "Bringing the main window size in line with the shared one…",
    "Поднимаем окна заново…": "Bringing the windows back up…",
    "Укажите размер, например 1600x900": "Enter a size, for example 1600x900",
    "Перезапускаем окно {number}…": "Restarting window {number}…",
    "Размер применится ко всем окнам сразу — и к тем, что появятся позже. Контейнеры для этого перезапускаются, по сорок секунд на каждый работающий.":
        "The size applies to every window at once — including the ones created later. "
        "The containers restart for that, about forty seconds per running one.",
    "Записываем размер для всех окон…": "Writing the size for every window…",
    "Перезапускаем основное окно…": "Restarting the main window…",
    "Не удалось применить размер": "Could not apply the size",
    "Все окна теперь {width}×{height}": "Every window is now {width}×{height}",
    "Выбрать размер окна": "Choose the window size",
    "Размер окна {number}": "Size of window {number}",
    "Окно примет выбранный размер после перезапуска этого контейнера — около сорока секунд. Другие окна не затрагиваются.":
        "The window takes the chosen size after this container restarts — about forty "
        "seconds. Other windows are not affected.",
    "Под текущее окно": "Match the current window",
    "Подогнать приложение под размер окна": "Fit the app to the window size",
    "Не удалось подогнать под окно": "Could not fit to the window",
    "Подогнано под {width}×{height}": "Fitted to {width}×{height}",
    "окно этого приложения на экране не найдено":
        "no window of this app was found on screen",
    "окно слишком мало для подгонки": "the window is too small to fit into",
    "Гасим дополнительные окна перед стартом…":
        "Stopping the extra windows before the start…",
    "Возвращаем оверлей с транслятором…": "Restoring the overlay with the translator…",
    "Вернуть транслятор ARM64": "Restore the ARM64 translator",
    "Waydroid отключил оверлей, а в нём libhoudini — контейнер перезапустится":
        "Waydroid turned the overlay off, and libhoudini lives there — the container "
        "will restart",
    "переключиться не удалось": "the switch did not work",
    "+ Окно": "+ Window",
    "Окно {number} — основное": "Window {number} — main",
    "Окно {number}": "Window {number}",
    "остановлено": "stopped",
    "работает · приложения здесь ещё нет": "running · the app is not here yet",
    "работает · {ip}": "running · {ip}",
    "Открыть приложение в этом окне": "Open the app in this window",
    "Закрыть приложение в этом окне": "Close the app in this window",
    "Удалить окно вместе с его данными": "Delete the window along with its data",
    "Не удалось прочитать список окон": "Could not read the list of windows",
    "Создаём окно {number}…": "Creating window {number}…",
    "Запускаем Android в окне {number}…": "Starting Android in window {number}…",
    "Запускаем окно {number}…": "Starting window {number}…",
    "Ждём загрузки Android…": "Waiting for Android to boot…",
    "Ставим приложение в окно {number}…": "Installing the app into window {number}…",
    "Открываем окно {number}…": "Opening window {number}…",
    "Удаляем окно {number}…": "Deleting window {number}…",
    "Окно {number} готово": "Window {number} is ready",
    "Открыто окно {number}": "Window {number} is open",
    "Окно {number} удалено": "Window {number} is deleted",
    "Открыто окон: {count}": "Windows opened: {count}",
    "Открыто окон: {count}, с ошибками: {bad}": "Windows opened: {count}, with errors: {bad}",
    "Приложение закрыто во всех окнах": "The app is closed in every window",
    "Не удалось создать окно": "Could not create the window",
    "Окно не открылось": "The window did not open",
    "Окна не открылись": "The windows did not open",
    "Не удалось закрыть приложение": "Could not close the app",
    "Не удалось удалить окно": "Could not delete the window",
    "Удалить окно {number}?": "Delete window {number}?",
    "Контейнер и все его данные будут стёрты: установленные приложения, входы, кеш. Другие окна это не затронет.": "The container and all of its data will be erased: installed apps, logins, cache. Other windows are not affected.",
    "окно не найдено": "the window was not found",
    "окно не получило адрес в сети": "the window did not get a network address",
    "Android в этом окне не загрузился": "Android in this window did not boot",
    "файл APK не найден": "the APK file was not found",
    "помощник для контейнеров не найден": "the container helper was not found",
    "не удалось подготовить помощника для контейнеров: {error}": "could not prepare the container helper: {error}",
    "не удалось": "it did not work",
    "Окна работают в основном профиле": "Windows work in the main profile",
    "у приложения выбран профиль №{number} — кнопка «Запустить» уходит в него, "
    "и окна с экрана пропадают. Запуск любого окна возвращает контейнер на "
    "основной профиль.":
        "the app is set to profile #{number} — the «Launch» button goes there and "
        "the windows leave the screen. Launching any window returns the container "
        "to the main profile.",
    "Открылись не все окна — {details}": "Not every window opened — {details}",
    "Больше окон Android не даёт: у основного профиля может быть "
    "только один клон и один рабочий профиль":
        "Android gives no more windows: the main profile may have only one clone "
        "and one work profile",
    "не удалось создать окно": "could not create the window",
    "не удалось поставить приложение в новое окно":
        "could not install the app into the new window",
    "то же приложение с отдельными данными — для второго аккаунта или проверки с чистого листа": "the same app with its own data — for a second account or a clean-slate test",
    "есть — работает рядом с оригиналом, данные отдельные": "exists — runs next to the original, with separate data",
    "есть, но окно у контейнера одно: копия и оригинал показываются по очереди — включите отдельные окна в настройках": "exists, but the container has a single window: the copy and the original take turns — turn separate windows on in the settings",
    "Создать": "Create",
    "Удалить копию": "Delete the copy",
    "Удалить копию?": "Delete the copy?",
    "Данные копии «{name}» будут стёрты: вход, кеш, настройки. Само приложение и его основные данные останутся нетронутыми.": "The data of the «{name}» copy will be erased: the login, the cache, the settings. The app itself and its main data stay untouched.",
    "создаём копию…": "creating the copy…",
    "открываем копию…": "opening the copy…",
    "удаляем копию…": "deleting the copy…",
    "запущена": "running",
    "Копия «{name}» готова": "The «{name}» copy is ready",
    "Копия «{name}» запущена": "The «{name}» copy is running",
    "Копия удалена": "The copy is deleted",
    "Не удалось создать копию": "Could not create the copy",
    "Копия не открылась": "The copy did not open",
    "Не удалось удалить копию": "Could not delete the copy",
    "копия этого приложения уже есть": "this app already has a copy",
    "копии этого приложения нет": "this app has no copy",
    "этот образ Android не умеет клонировать приложения": "this Android image cannot clone apps",
    "не удалось создать профиль для копий": "could not create the profile for copies",
    "не удалось поставить приложение в копию": "could not install the app into the copy",
    "В контейнере уже {count} профилей — это предел": "The container already holds {count} profiles — that is the limit",
    "Отдельные окна для приложений": "A separate window per app",
    "копия и оригинал становятся видны одновременно; полноэкранные игры это "
    "не берут — Android показывает их по очереди. Контейнер перезапустится":
        "the copy and the original become visible at the same time; fullscreen games "
        "do not take it — Android shows them one at a time. The container will restart",
    "есть — но приложение не разрешает менять размер окна, поэтому копия и "
    "оригинал показываются по очереди":
        "exists — but the app does not allow resizing its window, so the copy and the "
        "original are shown one at a time",
    "есть — копия живёт в основном профиле, при запуске контейнер "
    "переключится на него":
        "exists — the copy lives in the main profile, and the container will switch "
        "to it on launch",
    "Не удалось переключить режим окон": "Could not switch the window mode",
    "Каждое приложение теперь в своём окне": "Every app now gets its own window",
    "Контейнер снова показывает одно окно": "The container shows a single window again",
    # Ход выполнения шага: фазы из вывода waydroid и счётчики загрузки.
    "Поднимаем контейнер Android": "Bringing the Android container up",
    "Подтвердите пароль в системном окне": "Confirm the password in the system dialog",
    "Подтвердите пароль в окне {app}": "Confirm the password in the {app} window",
    "{done} из {total} МБ": "{done} of {total} MB",
    "Скачано {mb} МБ образа": "{mb} MB of the image downloaded",
    "скачано {percent}%": "{percent}% downloaded",
    "{done} из {total} МБ · {speed} {unit}/с": "{done} of {total} MB · {speed} {unit}/s",
    " · осталось ~{n} мин": " · ~{n} min left",
    " · осталось ~{n} с": " · ~{n} s left",
    "Загружаем образ системы": "Downloading the system image",
    "Проверяем контрольную сумму": "Verifying the checksum",
    "Распаковываем образ": "Extracting the image",
    "Настраиваем контейнер": "Setting the container up",
    "Готовим контейнер": "Preparing the container",
    "Запускаем сессию": "Starting the session",
    "Устанавливаем": "Installing",
    "Разбираем зависимости": "Resolving dependencies",
    "Ставим пакет": "Installing the package",
    # Подписи с подстановкой: значения ставятся уже после перевода, поэтому
    # имена в скобках должны совпадать с русской строкой один в один.
    "рендер {width}×{height}, растянутый на {screen_width}×{screen_height}":
        "rendering {width}×{height}, stretched to {screen_width}×{screen_height}",
    "окно {width}×{height} — gamescope на этой машине не работает, "
    "растягивать нечем":
        "a {width}×{height} window — gamescope does not work on this machine, "
        "so there is nothing to stretch with",
    "проверка {url}": "checking {url}",
    "Архив транслятора ({target})": "Translator archive ({target})",
    "waydroid_script {action} magisk, с перезапуском контейнера":
        "waydroid_script {action} magisk, with a container restart",
    "Переключить транслятор на {target}": "Switch the translator to {target}",
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

    # --- Мультиокна / Инстансы / Оптимизация ---
    "Окна и копии (Multi-Instance)": "Windows & Clones (Multi-Instance)",
    "+ Окно (Клон)": "+ Window (Clone)",
    "Создать новое независимое окно этого приложения": "Create a new independent window for this app",
    "▶ Запустить все": "▶ Launch All",
    "Открыть все окна одновременно": "Open all windows simultaneously",
    "⏹ Остановить все": "⏹ Stop All",
    "Остановить все окна этого приложения": "Stop all windows of this app",
    "Оптимизация и производительность": "Optimization & Performance",
    "Режим экономии (Eco Mode)": "Eco Mode",
    "отключает анимации Android и снижает нагрузку на процессор и видеокарту":
        "disables Android animations and reduces CPU and GPU load",
    "Ограничение FPS": "FPS Limit",
    "лимит кадров в секунду для экономии ресурсов при мультибоксинге":
        "frame rate limit to save resources during multi-boxing",
    "Без ограничений (Макс. FPS)": "No limit (Max FPS)",
    "15 FPS (Макс. экономия)": "15 FPS (Max power save)",
    "30 FPS (Рекомендуется для окон)": "30 FPS (Recommended for multi-window)",
    "45 FPS": "45 FPS",
    "60 FPS": "60 FPS",
    "Свободный многооконный режим": "Freeform Multi-Window Mode",
    "принудительно разрешает всем играм и окнам открываться рядом":
        "forces all games and windows to open side-by-side",
    "Применить": "Apply",
    "Очистить память Android (RAM Trim)": "Clean Android RAM (RAM Trim)",
    "освобождает неактивную оперативную память и сбрасывает кэш":
        "frees inactive RAM and clears system cache",
    "Очистить": "Clean",
    "🟢 Запущено": "🟢 Running",
    "⚪ Остановлено": "⚪ Stopped",
    "Запустить это окно": "Launch this window",
    "Остановить это окно": "Stop this window",
    "Сбросить данные окна (вход, кэш)": "Reset window data (login, cache)",
    "Удалить это окно": "Delete this window",
    "Не удалось загрузить список окон": "Failed to load window list",
    "Создаём и подписываем новое окно…": "Building and signing new window…",
    "Не удалось создать окно": "Failed to create window",
    "Новое окно «{label}» готово!": "New window \"{label}\" is ready!",
    "Открываем {label}…": "Opening {label}…",
    "Не удалось открыть окно": "Failed to open window",
    "Окно {label} запущено": "Window {label} launched",
    "Окно {label} остановлено": "Window {label} stopped",
    "Не удалось остановить окно": "Failed to stop window",
    "Сбросить данные окна?": "Reset window data?",
    "Данные окна «{label}» будут очищены: вход, кэш и настройки. Остальные окна останутся нетронутыми.":
        "Window \"{label}\" data will be cleared: login, cache and settings. Other windows will remain untouched.",
    "Сбросить": "Reset",
    "Не удалось сбросить данные": "Failed to reset data",
    "Данные {label} очищены": "Data for {label} cleared",
    "Удалить окно {label}?": "Delete window {label}?",
    "Окно «{label}» и все его данные будут полностью удалены из Waydroid.":
        "Window \"{label}\" and all its data will be completely removed from Waydroid.",
    "Не удалось удалить окно": "Failed to delete window",
    "Окно {label} удалено": "Window {label} deleted",
    "Запускаем все окна…": "Launching all windows…",
    "Ошибка запуска окон": "Error launching windows",
    "Все окна запущены!": "All windows launched!",
    "Останавливаем все окна…": "Stopping all windows…",
    "Ошибка остановки окон": "Error stopping windows",
    "Все окна остановлены": "All windows stopped",
    "Режим экономии включён (анимации отключены)": "Eco Mode enabled (animations disabled)",
    "Режим экономии выключен": "Eco Mode disabled",
    "Установлен лимит {fps} FPS": "FPS limit set to {fps} FPS",
    "Лимит FPS снят": "FPS limit removed",
    "Применяем настройки многооконности…": "Applying multi-window settings…",
    "Свободный режим многооконности активен!": "Freeform multi-window mode is active!",
    "Не удалось применить: {err}": "Failed to apply: {err}",
    "Очищаем память Android…": "Cleaning Android memory…",
    "Память Android успешно очищена!": "Android memory successfully cleaned!",
    "Ошибка очистки памяти: {err}": "Memory cleaning error: {err}",
    "Окно 0 (Оригинал)": "Window 0 (Original)",
    "Окно 0 (Основное)": "Window 0 (Main)",
    "Окно {index}": "Window {index}",
    "Окно {index} ({name})": "Window {index} ({name})",
    "Сборка клона #{index}…": "Building clone #{index}…",
    "Установка окна #{index} в Waydroid…": "Installing window #{index} in Waydroid…",
    "Не удалось установить клон": "Failed to install clone",
    "Запуск {name}…": "Launching {name}…",
    # --------------------------------------------- картинка контейнера ----
    "Ждём первый кадр приложения…": "Waiting for the app's first frame…",
    "Запускаем окно {number}… (до трёх минут)":
        "Starting window {number}… (up to three minutes)",
    "Закрываем прежнее окно приложения…": "Closing the app's previous window…",
    "работает · нет сети — нужен перезапуск окна":
        "running · no network — the window needs a restart",
    "работает · не отвечает — нужен перезапуск окна":
        "running · not answering — the window needs a restart",
    "Готовим машину к старту контейнера…":
        "Getting the machine ready for the container…",
    "Android не отвечает — возвращаем…": "Android is not answering — bringing it back…",
    "Картинка Android застряла — возвращаем…":
        "Android's picture is stuck — bringing it back…",
    "Окно не появилось — возвращаем картинку Android…":
        "No window appeared — bringing Android's picture back…",
    "Waydroid запустил приложение, но окна на экране так и нет":
        "Waydroid started the app, but no window ever reached the screen",
    "приложение запустилось, но картинки в этом окне нет":
        "the app started, but this window shows no picture",
    "Картинка застряла — перезапускаем окно {number}…":
        "The picture is stuck — restarting window {number}…",
    "контейнер не отвечает: список приложений пуст":
        "the container is not answering: the list of apps came back empty",
    "запуск не удался": "the launch failed",
    "Запуск окна не удался": "Failed to launch window",
    "Файл клона не найден, создайте его заново": "Clone file not found, please create it again",
    "Создаём клон-профиль…": "Creating clone profile…",
    "Создаём рабочий профиль…": "Creating work profile…",
    "Активируем приложение в новом окне…": "Activating app in new window…",
    "Достигнут максимум профилей Android в контейнере": "Maximum Android profile limit reached in container",
    "Окно не найдено": "Window not found",
}
