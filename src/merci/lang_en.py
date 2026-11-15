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
    "Трансляторы ARM и порядок их установки": "ARM translators and how they are installed",
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
    "Перезапускаем контейнер…": "Restarting the container…",
    "Поднимаем контейнер…": "Starting the container…",
    "Отмена": "Cancel",
    "Установить": "Install",
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
    "проверяем состояние…": "checking state…",
    "проверяем…": "checking…",
    "спрашиваем контейнер…": "asking the container…",
    "Новый профиль…": "New profile…",
    "заводим профиль…": "creating the profile…",
    "Android-приложения (*.apk)": "Android applications (*.apk)",
    "другая сборка": "another build",
    "из профиля Android №{n}": "from Android profile #{n}",
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
    "Библиотека": "Library",
    "Перетащите APK сюда": "Drop an APK here",
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
    "Удалить из библиотеки": "Delete from the library",
    "Удалить из контейнера": "Remove from the container",
    "версия {version}": "version {version}",
    "по умолчанию": "default",
    "без нативного кода": "no native code",

    # ---------------------------------------------------------- диалоги ----
    "Не удалось определить размер монитора": "Could not determine the monitor size",
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
    "Waydroid не запустил APK": "Waydroid did not launch the APK",
    "{message}\n\nОткрыть проверку готовности?":
        "{message}\n\nOpen the readiness check?",
    "Переключить на {target}?": "Switch to {target}?",
    "Сейчас стоит {current}. Если приложение падает внутри "
    "транслятора, второй вариант иногда справляется. Контейнер "
    "перезапустится, всё запущенное в нём закроется; потребуется пароль.":
        "{current} is installed right now. When an app crashes inside the "
        "translator, the other one sometimes copes. The container will "
        "restart and everything running in it will close; your password "
        "will be required.",
    "Поставить транслятор ARM?": "Install an ARM translator?",
    "Транслятора ARM в контейнере сейчас нет: приложения, собранные "
    "только под ARM, не запустятся. Merci поставит {target}. "
    "Контейнер перезапустится; потребуется пароль.":
        "There is no ARM translator in the container right now: apps built "
        "for ARM only will not start. Merci will install {target}. The "
        "container will restart; your password will be required.",
    "Поставить": "Install",
    "Транслятор установлен": "Translator installed",
    "Транслятор переключён": "Translator switched",
    "Мерцание исправлено": "Flicker fixed",
    "Исправление мерцания сброшено настройкой ускорения":
        "The flicker fix was reset by the acceleration setup",
    "Merci не умеет ставить ускорение на этой системе":
        "Merci cannot install acceleration on this system",
    "waydroid-nvidia Merci собирает из AUR — это есть только в Arch и "
    "производных. На других системах его ставят по описанию самого "
    "проекта; уже установленное Merci узнает и будет поддерживать.":
        "Merci builds waydroid-nvidia from the AUR — that exists only on Arch "
        "and its derivatives. On other systems it is installed following the "
        "project's own instructions; once installed, Merci will recognise and "
        "maintain it.",
    "Ускорение включено": "Acceleration enabled",
    "Ускорение настроено": "Acceleration configured",
    "Аппаратное ускорение уже включено": "Hardware acceleration is already enabled",
    "Ускорение уже включено: waydroid-nvidia {version}, режим {mode}.":
        "Acceleration is already enabled: waydroid-nvidia {version}, {mode} mode.",
    "без пакета": "no package",
    "Но драйвер NVIDIA сейчас не найден — без него контейнер "
    "не стартует.":
        "But the NVIDIA driver is not found right now — without it the "
        "container will not start.",
    "После обновления драйвера или пакета ему нужна повторная "
    "настройка: без неё контейнер не стартует.":
        "After a driver or package update it needs to be set up again: "
        "without that the container will not start.",
    "Но контейнер сейчас рисует так: {renderer}. Обычно это "
    "исправляет повторная настройка.":
        "But the container currently renders like this: {renderer}. Setting "
        "it up again usually fixes that.",
    "Контейнер сейчас рисует так: {renderer}.":
        "The container currently renders like this: {renderer}.",
    "Контейнер выключен — как он рисует, будет видно после "
    "запуска.":
        "The container is off — how it renders will be visible once it starts.",
    "Ставить заново ничего не нужно. Повторная настройка сохраняет "
    "режим ускорения и настройки Merci; контейнер при ней "
    "перезапускается, потребуется пароль.":
        "Nothing needs to be installed again. Setting it up again keeps the "
        "acceleration mode and Merci's settings; the container restarts and "
        "your password is required.",
    "Перенастроить": "Set up again",
    "Root уже установлен": "Root is already installed",
    "Magisk Delta уже стоит в системе Android и запускается "
    "вместе с ней. Ставить его заново нужно, только если root "
    "перестал работать.":
        "Magisk Delta is already in the Android system and starts with it. "
        "It only needs reinstalling if root has stopped working.",
    "Переустановка вернёт файлы Magisk на место; выданные "
    "приложениям права и модули останутся. Контейнер "
    "перезапустится, всё запущенное в нём закроется; потребуется "
    "пароль.":
        "Reinstalling puts the Magisk files back; the rights granted to apps "
        "and the modules stay. The container will restart and everything "
        "running in it will close; your password will be required.",
    "Переустановить": "Reinstall",
    "Root не установлен — убирать нечего": "Root is not installed — nothing to remove",
    "Убрать следы root?": "Remove the traces of root?",
    "Сам root уже снят, но от него остались следы: служебные "
    "файлы в системе Android или приложение Magisk. Merci уберёт "
    "их.\n\nКонтейнер перезапустится, всё запущенное в нём "
    "закроется; потребуется пароль.":
        "Root itself is already removed, but traces of it remain: service "
        "files in the Android system or the Magisk app. Merci will remove "
        "them.\n\nThe container will restart and everything running in it "
        "will close; your password will be required.",
    "Убрать следы": "Remove the traces",
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
    "и перезапустит контейнер: всё запущенное в Android закроется, "
    "дополнительные окна выключатся. Потребуется пароль.":
        "Android layers are currently drawn into Wayland subsurfaces. A "
        "synchronous subsurface is only shown when the parent surface commits, "
        "so the frame freezes and refreshes only on events: a key press, the "
        "pointer entering the window, the on-screen keyboard appearing.\n\n"
        "Merci will turn that mode off in both settings files and restart the "
        "container: everything running in Android will be closed and the extra "
        "windows will be stopped. Your password will be required.",
    "Не подходит для этой машины": "Not suitable for this machine",
    "Включить аппаратное ускорение?": "Enable hardware acceleration?",
    "Установить root в контейнере?": "Install root inside the container?",
    "Magisk Delta даст root внутри Waydroid — это ваш собственный "
    "Android, так что доступ к системным разделам и модулям тут "
    "нормальная вещь.\n\nПроверку устройства играми это не проходит: "
    "для них контейнер с root выглядит наоборот подозрительнее.":
        "Magisk Delta gives you root inside Waydroid — it is your own Android, "
        "so access to system partitions and modules is a normal thing here."
        "\n\nIt does not pass device checks in games: to them a rooted "
        "container looks even more suspicious.",
    "Merci ставит только сборку Magisk Delta с известной контрольной "
    "суммой. Контейнер перезапустится, всё запущенное в нём закроется; "
    "потребуется пароль.":
        "Merci installs only the Magisk Delta build whose checksum it knows. "
        "The container will restart and everything running in it will close; "
        "your password will be required.",
    "Merci ставит только сборку Magisk Delta с известной контрольной "
    "суммой, а этого файла на компьютере нет: место, откуда он "
    "раздавался, удалено с GitHub. Merci поищет файл в «Загрузках» и "
    "попробует скачать; не найдёт — откажется до запроса пароля, "
    "ничего не изменив.":
        "Merci installs only the Magisk Delta build whose checksum it knows, "
        "and that file is not on this computer: the place it was served from "
        "has been deleted from GitHub. Merci will look for the file in "
        "Downloads and try to download it; if it finds none, it refuses "
        "before the password prompt, changing nothing.",
    "Убрать root из контейнера?": "Remove root from the container?",
    "Magisk Delta будет снят вместе со своим приложением. Модули, "
    "которые вы через него ставили, перестанут работать.\n\n"
    "Контейнер перезапустится, всё запущенное в нём закроется; "
    "потребуется пароль.":
        "Magisk Delta will be removed together with its app. Modules you "
        "installed through it will stop working.\n\nThe container will "
        "restart and everything running in it will close; your password "
        "will be required.",
    "Root установлен": "Root installed",
    # Чем шаги и помощник объясняют неудачу: мастер показывает эти строки
    # как причину (installer.failure_reason), остальной журнал не переводится.
    "Проверенного файла Magisk Delta взять негде: место, откуда он "
    "раздавался, удалено с GitHub, а ставить под root файл без известной "
    "контрольной суммы Merci не станет.":
        "There is nowhere to get a verified Magisk Delta file: the place it "
        "was served from has been deleted from GitHub, and Merci will not "
        "install a file without a known checksum as root.",
    "Если такой файл у вас сохранился, положите его в «Загрузки» и "
    "повторите. Его сумма SHA-256:":
        "If you still have such a file, put it into Downloads and try again. "
        "Its SHA-256 checksum:",
    "Качать нечем: на этой системе нет curl.":
        "Nothing to download with: this system has no curl.",
    "Сервер ответил отказом: архива по этому адресу больше нет.":
        "The server refused: the archive is no longer at this address.",
    "По этому адресу лежит другой файл — больше, чем архив транслятора.":
        "A different file sits at this address — larger than the translator archive.",
    "Архив транслятора не скачался целиком: связь с GitHub рвётся.":
        "The translator archive did not download completely: the connection "
        "to GitHub keeps breaking.",
    "Обычно помогает другая локация VPN. Архив можно скачать и браузером — "
    "положите его в «Загрузки», Merci возьмёт его оттуда.":
        "Another VPN location usually helps. You can also download the archive "
        "with a browser — put it into Downloads and Merci will take it from there.",
    "помощник Merci уже занят другой операцией с Waydroid — дождитесь её конца":
        "Merci's helper is busy with another Waydroid operation — wait for it to finish",
    "помощник Merci изменён посторонней программой — запуск отменён":
        "Merci's helper was modified by another program — the launch is cancelled",
    "этот файл — не та сборка Magisk, которой Merci доверяет: под root "
    "ставится только файл с известной контрольной суммой":
        "this file is not the Magisk build Merci trusts: only a file with a "
        "known checksum is installed as root",
    "архив транслятора не из числа известных: под root ставится только "
    "архив с известной контрольной суммой":
        "the translator archive is not one of the known ones: only an archive "
        "with a known checksum is installed as root",
    "образ Android всё ещё подключён: выключите Waydroid и повторите":
        "the Android image is still mounted: switch Waydroid off and try again",
    "polkit отказал или запрос пароля отменён.":
        "polkit refused or the password prompt was cancelled.",
    "Попробовать": "Try",
    "идёт настройка Waydroid": "Waydroid is being set up",
    "Идёт настройка Waydroid — дождитесь её конца":
        "Waydroid is being set up — wait for it to finish",
    "Идёт настройка Waydroid…": "Setting up Waydroid…",
    "настраивается…": "setting up…",
    "Root убран": "Root removed",

    # -------------------------------------------------------- настройки ----
    "Профили Android": "Android profiles",
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
    "{name} убрано из контейнера": "{name} removed from the container",
    "Нужно подтвердить в Android": "Confirmation needed in Android",
    "Удалить из библиотеки?": "Delete from the library?",

    # ------------------------------------------------------- сообщения ----
    "спросить не удалось — контейнер не ответил":
        "could not ask — the container did not answer",
    "Профиль не завёлся": "The profile was not created",
    "Waydroid не готов: {detail}": "Waydroid is not ready: {detail}",
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
    "Образ Android": "Android image",
    "загрузка системного образа LineageOS": "downloading the LineageOS system image",
    "образ Android (~1 ГБ)": "the Android image (~1 GB)",
    "Сессия Waydroid": "Waydroid session",
    "запуск контейнера, прав root не требует":
        "starts the container; does not need root",
    "Связь с сервером образов": "Connection to the image server",
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
    # Окна — отдельные контейнеры Android, по одному на окно.
    "Окна": "Windows",
    "Поднимаем основное окно…": "Bringing the main window up…",
    "Android внутри не отвечает — помогает полный перезапуск контейнера":
        "Android inside is not answering — a full container restart helps",
    "Размер окон": "Window size",
    "Android не поднялся — перезапускаем контейнер целиком…":
        "Android did not come up — restarting the whole container…",
    "Поднимаем окна заново…": "Bringing the windows back up…",
    "Размер применится ко всем окнам сразу — и к тем, что появятся позже. Контейнеры для этого перезапускаются, по сорок секунд на каждый работающий.":
        "The size applies to every window at once — including the ones created later. "
        "The containers restart for that, about forty seconds per running one.",
    "Записываем размер для всех окон…": "Writing the size for every window…",
    "Перезапускаем основное окно…": "Restarting the main window…",
    "Не удалось применить размер": "Could not apply the size",
    "Все окна теперь {width}×{height}": "Every window is now {width}×{height}",
    "окно этого приложения на экране не найдено":
        "no window of this app was found on screen",
    "окно слишком мало для подгонки": "the window is too small to fit into",
    "Вернуть транслятор ARM64": "Restore the ARM64 translator",
    "Waydroid отключил оверлей, а в нём libhoudini — контейнер перезапустится":
        "Waydroid turned the overlay off, and libhoudini lives there — the container "
        "will restart",
    "переключиться не удалось": "the switch did not work",
    "Окно {number}": "Window {number}",
    "Не удалось прочитать список окон": "Could not read the list of windows",
    "Ждём загрузки Android…": "Waiting for Android to boot…",
    "Ставим приложение в окно {number}…": "Installing the app into window {number}…",
    "Открываем окно {number}…": "Opening window {number}…",
    "Удаляем окно {number}…": "Deleting window {number}…",
    "Окно {number} готово": "Window {number} is ready",
    "Открыто окно {number}": "Window {number} is open",
    "Окно {number} удалено": "Window {number} is deleted",
    "Открыто окон: {count}": "Windows opened: {count}",
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
    "помощник для контейнеров не найден": "the container helper was not found",
    "не удалось подготовить помощника для контейнеров: {error}": "could not prepare the container helper: {error}",
    "не удалось": "it did not work",
    "Удалить копию": "Delete the copy",
    "Не удалось удалить копию": "Could not delete the copy",
    "Отдельные окна для приложений": "A separate window per app",
    "копия и оригинал становятся видны одновременно; полноэкранные игры это "
    "не берут — Android показывает их по очереди. Контейнер перезапустится":
        "the copy and the original become visible at the same time; fullscreen games "
        "do not take it — Android shows them one at a time. The container will restart",
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
    "файлы Magisk убираются из системы и данных Android, контейнер перезапускается":
        "Magisk files are removed from the Android system and data, the container restarts",
    "Magisk Delta из файла с известной контрольной суммой, контейнер перезапускается":
        "Magisk Delta from a file with a known checksum, the container restarts",
    "Magisk Delta, если его источник ответит": "Magisk Delta, if its source answers",
    "из архива с известной контрольной суммой, контейнер перезапускается":
        "from an archive with a known checksum, the container restarts",
    "неизвестный транслятор": "unknown translator",
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
    "Режим экономии (Eco Mode)": "Eco Mode",
    "Ограничение FPS": "FPS Limit",
    "Применить": "Apply",
    "Очистить память Android (RAM Trim)": "Clean Android RAM (RAM Trim)",
    "Очистить": "Clean",
    "Не удалось создать окно": "Failed to create window",
    "Не удалось удалить окно": "Failed to delete window",
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
    # --------------------------------------------- картинка контейнера ----
    "«{name}» запустилось и зависло: у транслятора ARM истёк срок "
    "действия. Обновите его — кнопка «Подготовить»":
        "“{name}” started and hung: the ARM translator has expired. "
        "Update it with the “Set up” button",
    "«{name}» запустилось, но за {seconds} с не нарисовало ни кадра — "
    "приложение зависает при старте":
        "“{name}” started but drew no frame in {seconds} s — the app hangs on startup",
    "картинка контейнера не отвечает": "the container's display is not answering",
    "приложение работает, но окно на экране не открылось":
        "the app is running, but its window never opened",
    "Транслятор ARM устарел: приложения зависают при запуске":
        "The ARM translator has expired: apps hang on launch",
    "{bridge} — срок действия сборки истёк, нужно обновить":
        "{bridge} — this build has expired and needs updating",
    "Нужна трансляция ARM64 → x86_64": "ARM64 → x86_64 translation is needed",
    "Обновить транслятор ARM": "Update the ARM translator",
    "у прежней сборки libhoudini истёк срок: приложения зависают при запуске":
        "the previous libhoudini build has expired: apps hang on launch",
    "libhoudini из проверенного архива": "libhoudini from a verified archive",
    "Ждём первый кадр приложения…": "Waiting for the app's first frame…",
    "Запускаем окно {number}… (до трёх минут)":
        "Starting window {number}… (up to three minutes)",
    "Закрываем прежнее окно приложения…": "Closing the app's previous window…",
    "Готовим машину к старту контейнера…":
        "Getting the machine ready for the container…",
    "Android не отвечает — возвращаем…": "Android is not answering — bringing it back…",
    "Картинка Android застряла — возвращаем…":
        "Android's picture is stuck — bringing it back…",
    "Waydroid запустил приложение, но окна на экране так и нет":
        "Waydroid started the app, but no window ever reached the screen",
    "Картинка застряла — перезапускаем окно {number}…":
        "The picture is stuck — restarting window {number}…",
    "запуск не удался": "the launch failed",

    # ------------------------------------------- пульс и строки окон ----
    "выключено": "off",
    "Android запускается — сети ещё нет": "Android is starting — no network yet",
    "запущено, но сети нет — перезапустите окно":
        "started, but there is no network — restart the window",
    "Android пока не отвечает": "Android is not answering yet",
    "Android не отвечает — перезапустите окно":
        "Android is not answering — restart the window",
    "приложение открыто": "the app is open",
    "приложение работает в фоне — окна на экране нет":
        "the app is running in the background — no window on screen",
    "Android работает · приложения здесь ещё нет":
        "Android is running · the app is not installed here yet",
    "Android работает · приложение закрыто": "Android is running · the app is closed",
    "идёт операция…": "working…",
    "Размер окон…": "Window size…",
    "Перезапустить окно": "Restart window",
    "Выключить окно": "Power off window",
    "Удалить окно…": "Delete window…",
    "Дождитесь конца текущей операции с окном":
        "Wait for the current window operation to finish",
    "окно не запущено": "the window is not running",
    "Приложение закрыто в окне {number}": "App closed in window {number}",
    "Перезапускаем окно {number}… (до трёх минут)":
        "Restarting window {number}… (up to three minutes)",
    "Окно не перезапустилось": "The window did not restart",
    "Окно {number} перезапущено": "Window {number} restarted",
    "Выключаем окно {number}…": "Powering off window {number}…",
    "Окно не выключилось": "The window did not power off",
    "Окно {number} выключено": "Window {number} powered off",
    "Выключаем дополнительные окна…": "Powering off the extra windows…",
    "Окна не выключились": "The windows did not power off",
    "Выключено окон: {count}": "Windows powered off: {count}",
    "Закрылось не везде": "Not closed everywhere",
    "Открылись не все окна": "Not every window opened",
    "Открыто окон: {count}.": "Windows opened: {count}.",
    "окно {number}: {error}": "window {number}: {error}",
    "Окно {number} не отвечает — перезапускаем…":
        "Window {number} is not answering — restarting…",
    "Запускаем окна: {numbers}… (до трёх минут)":
        "Starting windows: {numbers}… (up to three minutes)",
    "не хватает памяти: нужно около {need} МБ, свободно {free} МБ — "
    "закройте что-нибудь или выключите другие окна":
        "not enough memory: about {need} MB needed, {free} MB free — "
        "close something or power off other windows",

    # ------------------------------------------- состояние и выключение ----
    "состояние неясно — выключить можно и так":
        "state unclear — you can still stop it",
    "Дополнительные окна, если они работают, выключатся тоже — "
    "для этого понадобится пароль.":
        "Extra windows, if any are running, are powered off as well — "
        "that takes your password.",
    "дополнительные окна остались включены: {error}":
        "the extra windows are still on: {error}",
    "контейнер всё ещё работает: {detail}": "the container is still running: {detail}",
    "не удалось выключить": "could not stop it",
    "Подробности — в окне Merci.": "Details are in the Merci window.",

    # ------------------------------------------- закрытие приложения ----
    "недопустимое имя пакета": "invalid package name",
    "приложение не закрылось": "the app did not close",
    "контейнер не ответил на команду": "the container did not answer the command",

    # ------------------------------------------------- adb под ключом ----
    "adb контейнера открыт без пароля — стоит закрыть его ключом":
        "The container's adb is open without a password — lock it with a key",
    "Закрыть adb контейнера ключом": "Lock the container's adb with a key",
    "сейчас к нему без пароля подключится любое приложение Android "
    "и сосед по сети":
        "right now any Android app and any neighbour on the network can "
        "connect to it without a password",
    "Android не ответил на вопрос о профилях":
        "Android did not answer the question about profiles",
    "Не удалось спросить контейнер": "Could not ask the container",
    "окон слишком много: больше {limit} не заводим":
        "too many windows: no more than {limit} are created",
    "окно создано, но Android в нём не загрузился — "
    "перезапустите окно из его меню":
        "the window was created, but Android in it did not boot — "
        "restart the window from its menu",

    # ------------------------------------------- у каждой сборки свой дом ----
    "Android не видит это приложение — похоже, оно не установилось":
        "Android cannot see this app — it looks like the install did not happen",
    "занято другой сборкой: {who} — она открыта":
        "taken by another build: {who} — it is open",
    "занято другой сборкой: {who} — она работает в фоне":
        "taken by another build: {who} — it is running in the background",
    "занято другой сборкой: {who}": "taken by another build: {who}",
    "версия {version}": "version {version}",
    "Основное окно занято другой сборкой": "The main window holds another build",
    "Отдельное окно": "Separate window",
    "Создаём окно {number}… (до трёх минут)":
        "Creating window {number}… (up to three minutes)",
    "Ждём загрузки Android в окне {number}…":
        "Waiting for Android to boot in window {number}…",
    "контейнер не отвечает по сети": "the container is not answering on the network",

    # ------------------------------------------- переносимость и замена ----
    "Это нужно сделать самому: {title}": "This one is for you to do: {title}",
    "ставится из репозитория: {packages}": "installed from the repository: {packages}",
    "adb — связь с Android": "adb — the link to Android",
    "Композитор Wayland для сеанса X11": "Wayland compositor for the X11 session",
    "в сеансе X11 для Waydroid нужен композитор Wayland — "
    "откройте «Подготовить Waydroid»: она поставит cage":
        "in an X11 session Waydroid needs a Wayland compositor — open "
        "“Set up Waydroid”: it will install cage",
    "на компьютере нет adb — без него Merci не может управлять "
    "Android. Откройте «Подготовить Waydroid»: она поставит его":
        "this computer has no adb — without it Merci cannot control Android. "
        "Open “Set up Waydroid”: it will install it",
    "не удалось узнать, какая сборка стоит в контейнере: Android не ответил":
        "could not find out which build is in the container: Android did not answer",
    "Обновляем сборку на месте…": "Updating the build in place…",
    "операция под root не уложилась в {seconds} с — возможно, окно "
    "пароля осталось без ответа":
        "the root operation did not finish in {seconds} s — the password "
        "window may have been left unanswered",
    "основное окно занято другой сборкой, а своих окон у записи нет":
        "the main window holds another build, and this entry has no windows of its own",
    "удалить не удалось": "could not remove it",

    # ------------------------------------------- новое окно: логика ----
    "ANDROID · {arch} НАПРЯМУЮ": "ANDROID · NATIVE {arch}",
    "ANDROID · ЧЕРЕЗ ТРАНСЛЯЦИЮ ARM": "ANDROID · VIA ARM TRANSLATION",
    "в основном окне стоит {who} — запуск откроет окно {number}":
        "the main window holds {who} — Launch will open window {number}",
    "в основном окне стоит {who} — при запуске Merci спросит, куда поставить эту":
        "the main window holds {who} — on launch Merci will ask where to put this one",
    "Нет adb: без него Merci не может управлять Android":
        "No adb: Merci cannot control Android without it",
    "Сеанс X11: для Android нужен вложенный композитор Wayland":
        "X11 session: Android needs a nested Wayland compositor",
    "Контейнер без доступа в интернет: мешает сетевой экран ({name})":
        "The container has no internet access: the firewall ({name}) is in the way",
    "Профили Android включены, но контейнер их не держит: "
    "новый профиль не создать":
        "Android profiles are on, but the container does not support them: "
        "a new profile cannot be created",
    "не закрывайте Merci": "keep Merci open",
    "идёт запуск «{name}»": "launching “{name}”",
    "идёт запуск": "a launch is in progress",
    "Waydroid выключен — поднимется при запуске": "Waydroid is off — it starts on launch",
    "открыто окон: {open} из {total}": "windows open: {open} of {total}",
    "окон: {total} · приложение не открыто": "windows: {total} · the app is not open",
    "окон: {total} · открыто: {open} · у каждого свой Android":
        "windows: {total} · open: {open} · each has its own Android",
    "одно окно · добавьте ещё — у каждого будет свой Android":
        "one window · add more — each gets its own Android",
    "основное": "main",
    "свой Android": "own Android",
    "Дождитесь конца текущего запуска": "Wait for the current launch to finish",
    "«{name}» открыто в окне {number}": "“{name}” is open in window {number}",
    "«{name}» открыто": "“{name}” is open",
    "В окне {number} стоит другая сборка": "Window {number} holds another build",
    "В окне {number} стоит {other}. Поставить вместо неё «{name}»?":
        "Window {number} holds {other}. Put “{name}” there instead?",
    "Merci попробует обновить сборку на месте, не трогая данные. "
    "Если Android не позволит — другая подпись или версия ниже, — "
    "Merci спросит ещё раз, прежде чем что-то стирать.":
        "Merci will try to update the build in place, keeping its data. If "
        "Android refuses — a different signature or a lower version — Merci "
        "will ask again before erasing anything.",
    "Поставить в это окно": "Put it in this window",
    "В основном окне стоит {other}. Android держит одно имя пакета "
    "({package}) как одну установку на контейнер, поэтому две "
    "сборки в одном окне не живут.":
        "The main window holds {other}. Android keeps one package name "
        "({package}) as a single install per container, so two builds cannot "
        "live in one window.",
    "Отдельное окно: у «{name}» будет свой Android и свои данные, "
    "а прежняя сборка останется как есть. Понадобится пароль и "
    "около гигабайта памяти, пока окно работает.":
        "A separate window: “{name}” gets its own Android and its own data, "
        "and the other build stays as it is. It needs your password and about "
        "a gigabyte of memory while the window is running.",
    "В основное окно: Merci попробует обновить сборку на месте, не "
    "трогая данные. Если Android не позволит — другая подпись или "
    "версия ниже, — Merci спросит ещё раз, прежде чем что-то стирать.":
        "Into the main window: Merci will try to update the build in place, "
        "keeping its data. If Android refuses — a different signature or a "
        "lower version — Merci will ask again before erasing anything.",
    "В основное окно": "Into the main window",
    "Заменяем сборку…": "Replacing the build…",
    "основное окно": "the main window",
    "окно {number}": "window {number}",
    "Android отказался обновить сборку на месте: у «{name}» другая "
    "подпись или версия ниже. Чтобы поставить её в {where}, прежнюю "
    "сборку придётся снять — вместе с её данными: входом в аккаунт, "
    "сохранениями, настройками.":
        "Android refused to update the build in place: “{name}” has a different "
        "signature or a lower version. To put it into {where}, the current "
        "build has to be removed — together with its data: the account "
        "sign-in, saves and settings.",
    "Данные сотрутся в профилях Android: {users}.":
        "Data will be erased in these Android profiles: {users}.",
    "Этой установкой пользуются записи: {names}.":
        "These entries use that install: {names}.",
    "Отдельное окно ничего не стирает.": "A separate window erases nothing.",
    "Стереть и поставить": "Erase and install",
    "Обновить на месте нельзя": "Cannot update in place",
    "Создаём окно… (до трёх минут)": "Creating a window… (up to three minutes)",
    "Как монитор — {w}×{h}": "Same as the monitor — {w}×{h}",
    "Под окно на экране": "Fit the window on screen",
    "Размер задаётся как 1600x900": "The size is written as 1600x900",
    "Слишком странный размер": "That size is too odd",
    "{name} добавлено. Пакет {package} уже есть в библиотеке — "
    "при запуске Merci предложит этой сборке своё окно":
        "{name} added. Package {package} is already in the library — on "
        "launch Merci will offer this build a window of its own",
    "из основного окна": "from the main window",
    "«{name}» будет убрано {where} вместе со своими данными "
    "внутри Android — учётной записью в игре, кешем, настройками.\n\n"
    "APK останется в библиотеке, и запустить его можно будет снова. "
    "Окна этой записи и то, что стоит в них, это не затронет.":
        "“{name}” will be removed {where} together with its data inside "
        "Android — the in-game account, cache and settings.\n\n"
        "The APK stays in the library and can be launched again. This entry's "
        "own windows and what is installed in them are not affected.",
    "Убираем из контейнера…": "Removing from the container…",
    "«{name}» будет убрано из библиотеки: сам APK и его установка "
    "в основном окне вместе с данными внутри Android.":
        "“{name}” will be removed from the library: the APK itself and its "
        "install in the main window, together with its data inside Android.",
    "Окна этой записи ({numbers}) — её собственные контейнеры — "
    "будут стёрты целиком, со всем, что в них стоит. Для этого "
    "понадобится пароль.":
        "This entry's windows ({numbers}) — its own containers — will be "
        "erased completely, with everything installed in them. This needs "
        "your password.",
    "Профиль Android №{n} останется: в нём может жить что-то ещё.":
        "Android profile #{n} stays: something else may live in it.",
    "Удаляем «{name}»…": "Removing “{name}”…",
    "Окна записи стереть не удалось, поэтому запись осталась "
    "на месте: {error}":
        "The entry's windows could not be erased, so the entry was kept: {error}",
    "{name} удалено, но установка в контейнере осталась: {error}":
        "{name} removed, but its install is still in the container: {error}",
    "Оформление": "Appearance",
    "Тема": "Theme",
    "цвета берутся из значка выбранного приложения":
        "colours come from the icon of the selected app",
    "Как в системе": "Follow the system",
    "Светлая": "Light",
    "Тёмная": "Dark",
    "Меньше прозрачности": "Reduce transparency",
    "плотные поверхности вместо стекла: легче для слабой "
    "видеокарты и для глаз":
        "solid surfaces instead of glass: easier on a weak GPU and on the eyes",
    "Профили Android дают одному приложению отдельные "
    "данные: свой вход, свой кеш, свои настройки. Так запускают "
    "второй аккаунт, не выходя из первого.\n\n"
    "Двух разных сборок одного пакета это не даёт и дать не может: "
    "имя пакета для Android — одна установка на всё устройство, "
    "профили делят между собой код приложения. Разным сборкам "
    "нужны разные окна.":
        "Android profiles give one app separate data: its own sign-in, cache "
        "and settings. That is how a second account runs without leaving the "
        "first.\n\n"
        "They do not and cannot give you two different builds of one package: "
        "to Android a package name is a single install for the whole device, "
        "and profiles share the app's code. Different builds need different "
        "windows.",
    "в основном окне это приложение не установлено":
        "this app is not installed in the main window",
    "в основном окне стоит другая сборка этого пакета — её Merci не трогает":
        "the main window holds another build of this package — Merci leaves it alone",

    # ------------------------------------------- другие системы ----
    "менеджер пакетов": "the package manager",
    "Поставьте пакет «{pkgs}» через {manager}.": "Install the “{pkgs}” package with {manager}.",
    "Поставьте «{what}» средствами вашего дистрибутива.":
        "Install “{what}” with your distribution's tools.",
    "Поставьте waydroid средствами вашего дистрибутива: {url}":
        "Install waydroid with your distribution's tools: {url}",
    "Для этой системы Merci не знает имя пакета «{what}»: поставьте его вручную.":
        "Merci does not know the package name for “{what}” on this system: install it by hand.",
    "Добавьте пакет «{what}» в environment.systemPackages и выполните "
    "nixos-rebuild switch.":
        "Add the “{what}” package to environment.systemPackages and run "
        "nixos-rebuild switch.",
    "Добавьте «pkgs.android-tools» в environment.systemPackages "
    "и выполните nixos-rebuild switch.":
        "Add “pkgs.android-tools” to environment.systemPackages and run "
        "nixos-rebuild switch.",
    "NixOS настраивается декларативно. Добавьте в configuration.nix "
    "«virtualisation.waydroid.enable = true;» (на новых ядрах может понадобиться "
    "«virtualisation.waydroid.package = pkgs.waydroid-nftables;»), выполните "
    "«sudo nixos-rebuild switch» и затем «sudo waydroid init». "
    "Waydroid на NixOS работает только в сессии Wayland.":
        "NixOS is configured declaratively. Add "
        "“virtualisation.waydroid.enable = true;” to configuration.nix (newer "
        "kernels may also need "
        "“virtualisation.waydroid.package = pkgs.waydroid-nftables;”), run "
        "“sudo nixos-rebuild switch” and then “sudo waydroid init”. "
        "On NixOS Waydroid only works in a Wayland session.",
    "На этой системе пакеты добавляются слоем образа: выполните "
    "«rpm-ostree install {pkgs}» и перезагрузите компьютер, затем запустите "
    "Merci снова.":
        "On this system packages are layered onto the image: run "
        "“rpm-ostree install {pkgs}”, reboot, then start Merci again.",
    "Корень этой системы неизменяем, поэтому Merci не ставит пакеты сама. "
    "Добавьте «{pkgs}» способом, который предусмотрен вашей системой (слой "
    "образа, transactional-update и т.п.), перезагрузитесь и запустите Merci снова.":
        "The root of this system is immutable, so Merci does not install "
        "packages itself. Add “{pkgs}” the way your system provides (an image "
        "layer, transactional-update and the like), reboot and start Merci again.",
    "В основных репозиториях openSUSE пакета waydroid нет; есть только "
    "пакеты сообщества, и их состояние меняется. Инструкция сообщества: "
    "{url}. Затем выполните «sudo waydroid init».":
        "The main openSUSE repositories have no waydroid package; there are "
        "only community packages, and their state changes. The community "
        "guide: {url}. Then run “sudo waydroid init”.",
    "Waydroid входит в репозитории Debian 14+ и Ubuntu 26.10+ (в Debian 13 — "
    "в backports): «sudo apt install waydroid». На более старых релизах "
    "сначала подключите репозиторий проекта по инструкции {url} — Merci не "
    "исполняет чужие сценарии под root.":
        "Waydroid is in the repositories of Debian 14+ and Ubuntu 26.10+ (in "
        "backports on Debian 13): “sudo apt install waydroid”. On older "
        "releases first add the project's repository following {url} — Merci "
        "does not run other people's scripts as root.",
    "Ядру нужен binder: в ядрах linux и linux-zen он есть; для других поставьте "
    "binder_linux-dkms из AUR и модуль binder_linux с "
    "devices=binder,hwbinder,vndbinder.":
        "The kernel needs binder: the linux and linux-zen kernels have it; for "
        "others install binder_linux-dkms from the AUR and load binder_linux "
        "with devices=binder,hwbinder,vndbinder.",
    "Ядру нужен binder (CONFIG_ANDROID_BINDER_IPC и CONFIG_ANDROID_BINDERFS). "
    "Проверьте «grep BINDER /boot/config-$(uname -r)»; если binder собран модулем, "
    "он может лежать в linux-modules-extra для вашего ядра. Иначе — ядро с "
    "поддержкой binder.":
        "The kernel needs binder (CONFIG_ANDROID_BINDER_IPC and "
        "CONFIG_ANDROID_BINDERFS). Check “grep BINDER /boot/config-$(uname -r)”; "
        "if binder is built as a module it may be in linux-modules-extra for "
        "your kernel. Otherwise use a kernel with binder support.",
    "Ядру нужен binder; на этой системе ядро меняется вместе с образом или "
    "конфигурацией.":
        "The kernel needs binder; on this system the kernel changes together "
        "with the image or the configuration.",
    "Ядру нужен binder (CONFIG_ANDROID_BINDER_IPC, CONFIG_ANDROID_BINDERFS). "
    "Проверьте «grep BINDER /boot/config-$(uname -r)» или «zgrep BINDER "
    "/proc/config.gz»; если их нет — выберите ядро с поддержкой binder.":
        "The kernel needs binder (CONFIG_ANDROID_BINDER_IPC, "
        "CONFIG_ANDROID_BINDERFS). Check “grep BINDER /boot/config-$(uname -r)” "
        "or “zgrep BINDER /proc/config.gz”; if they are missing, pick a kernel "
        "with binder support.",
    "SELinux в режиме enforcing. Если контейнер не стартует, посмотрите отказы "
    "(«sudo ausearch -m avc -ts recent»): известны отказы на метках binderfs. "
    "Режим permissive — только для диагностики, не как решение.":
        "SELinux is enforcing. If the container does not start, look at the "
        "denials (“sudo ausearch -m avc -ts recent”): denials on binderfs "
        "labels are known. Permissive mode is for diagnosis only, not a fix.",
    "AppArmor включён. Если контейнер не стартует с ошибкой dnsmasq и pid-файлом, "
    "добавьте в /etc/apparmor.d/local/usr.sbin.dnsmasq строки "
    "«@{run}/waydroid-lxc/ r,» и «@{run}/waydroid-lxc/* rw,».":
        "AppArmor is on. If the container fails to start with a dnsmasq "
        "pid-file error, add the lines “@{run}/waydroid-lxc/ r,” and "
        "“@{run}/waydroid-lxc/* rw,” to /etc/apparmor.d/local/usr.sbin.dnsmasq.",
    "firewalld не доверяет интерфейсу waydroid0: контейнеру нужна зона trusted":
        "firewalld does not trust the waydroid0 interface: the container needs the trusted zone",
    "Включена служба nftables со своими правилами: Merci их не меняет. Разрешите "
    "на интерфейсе waydroid0 входящие DHCP (67/udp) и DNS (53) и пересылку наружу.":
        "The nftables service is on with its own rules: Merci does not change "
        "them. Allow incoming DHCP (67/udp) and DNS (53) on the waydroid0 "
        "interface and forwarding to the outside.",

    # ------------------------------------------------------ обновления ----
    "неизвестно": "unknown",
    "система": "system",
    "драйверы": "drivers",
    "сервер образов Android не ответил ({part})":
        "the Android image server did not answer ({part})",
    "Образ Android": "Android image",
    "Waydroid и все окна выключатся на время обновления. Приложения и их "
    "данные остаются. Прежний образ сохраняется: если новый не заработает, "
    "его можно вернуть.":
        "Waydroid and all windows are switched off during the update. Apps and "
        "their data stay. The current image is kept: if the new one does not "
        "work, you can bring it back.",
    "Это другая версия Android: транслятор ARM и ускорение собраны под "
    "нынешнюю и после обновления могут не заработать.":
        "This is a different Android version: the ARM translator and the "
        "acceleration are built for the current one and may not work after "
        "the update.",
    "Сначала стоит обновить waydroid-nvidia: его части "
    "собраны под свежий образ.":
        "It is better to update waydroid-nvidia first: its parts are built "
        "for the fresh image.",
    "Waydroid ({name})": "Waydroid ({name})",
    "AUR не ответил: обновление пакета {name} не проверено":
        "the AUR did not answer: the {name} package update was not checked",
    "Пакет соберётся из AUR. После него Merci заново настроит "
    "ускорение (без этого контейнер не стартует), оставит "
    "нынешний режим рисования и вернёт свои настройки.":
        "The package is built from the AUR. After it Merci sets the "
        "acceleration up again (the container does not start without that), "
        "keeps the current rendering mode and restores its own settings.",
    "Пакет соберётся из AUR, после него Merci пересоберёт "
    "настройки контейнера и вернёт свои.":
        "The package is built from the AUR; after it Merci regenerates the "
        "container settings and restores its own.",
    "Крупное обновление: у новой версии другой режим рисования "
    "по умолчанию.":
        "A major update: the new version has a different default rendering mode.",
    "Придёт с обычным обновлением системы: по одному "
    "пакеты в Arch не обновляют.":
        "It arrives with a regular system update: on Arch packages are not "
        "upgraded one by one.",
    "После пакета Merci пересоберёт настройки контейнера и вернёт свои.":
        "After the package Merci regenerates the container settings and restores its own.",
    "репозиторий Merci не ответил": "the Merci repository did not answer",
    "новая сборка": "a new build",
    "Обновится как обычный флатпак; новая версия заработает "
    "после перезапуска Merci.":
        "It updates like any Flatpak; the new version starts working after "
        "Merci is restarted.",
    "Эта копия Merci установлена не из репозитория и сама не "
    "обновляется: возьмите новую на странице выпусков.":
        "This copy of Merci was not installed from the repository and cannot "
        "update itself: get the new one from the releases page.",
    "проверка не удалась: {error}": "the check failed: {error}",

    "ещё не проверялось": "not checked yet",
    "проверено только что": "checked just now",
    "проверено {n} мин назад": "checked {n} min ago",
    "проверено {n} ч назад": "checked {n} h ago",
    "проверено {date}": "checked {date}",
    "Обновления": "Updates",
    "Обновления: {count}": "Updates: {count}",
    "Доступно": "Available",
    "Прежний образ Android": "Previous Android image",
    "Копия, сделанная перед обновлением. Если новый образ "
    "работает, её можно удалить.":
        "A copy made before the update. If the new image works, you can delete it.",
    "Как обновляться": "How to update",
    "Проверять обновления автоматически": "Check for updates automatically",
    "при запуске и раз в сутки; ставятся они по вашему нажатию":
        "at start-up and once a day; they are installed when you say so",
    "Скрывать встроенный обновлятор Android": "Hide Android's built-in updater",
    "он ставит образ по нажатию на своё уведомление — под "
    "работающими окнами; обновления ставит Merci":
        "it installs the image when its notification is tapped — underneath "
        "running windows; Merci installs updates instead",
    "Обновить всё": "Update all",
    "Проверить снова": "Check again",
    "Проверяем обновления…": "Checking for updates…",
    "образ Android, пакет Waydroid, транслятор, сама Merci":
        "the Android image, the Waydroid package, the translator, Merci itself",
    "Обновления ещё не проверялись": "Updates have not been checked yet",
    "нажмите «Проверить снова» вверху": "press “Check again” at the top",
    "Доступно обновлений: {count}": "Updates available: {count}",
    "Всё актуально": "Everything is up to date",
    "Не удалось проверить: {what}": "Could not check: {what}",
    "крупное": "major",
    "≈ {size} МБ": "≈ {size} MB",
    "Как обновить": "How to update",
    "Обновить": "Update",
    "Сборка от {date}": "Build of {date}",
    "сохранена {date} · до {size} МБ на диске": "saved {date} · up to {size} MB on disk",
    "Вернуть": "Restore",
    "Вернуть прежний образ Android вместо нынешнего":
        "Put the previous Android image back in place of the current one",
    "Удалить копию": "Delete the copy",

    "Обновить образ Android": "Update the Android image",
    "окна выключаются, прежний образ сохраняется, настройки возвращаются":
        "windows are switched off, the current image is kept, settings are restored",
    "образ Android (~{size} МБ)": "the Android image (~{size} MB)",
    "Вернуть прежний образ Android": "Restore the previous Android image",
    "из копии, сделанной перед обновлением; приложения и данные остаются":
        "from the copy made before the update; apps and data stay",
    "Перенастроить ускорение NVIDIA": "Set up NVIDIA acceleration again",
    "после обновления драйвера или пакета контейнер без этого не стартует":
        "after a driver or package update the container does not start without it",
    "Обновить waydroid-nvidia": "Update waydroid-nvidia",
    "сборка из AUR, установка и настройка ускорения в прежнем режиме":
        "built from the AUR, installed, acceleration set up in the same mode",
    "{name} из AUR": "{name} from the AUR",
    "Обновить пакет Waydroid": "Update the Waydroid package",
    "менеджером пакетов системы; затем настройки контейнера пересобираются":
        "with the system package manager; then the container settings are regenerated",
    "для этой системы Merci не знает, как обновить пакет":
        "Merci does not know how to update the package on this system",
    "неизвестный пакет": "unknown package",
    "это обновление ставится не мастером": "this update is not installed by the wizard",
    "Транслятор ARM": "ARM translator",
    "libhoudini, срок действия сборки истёк": "libhoudini, the build has expired",
    "свежая сборка libhoudini": "a fresh libhoudini build",
    "У прежней сборки истёк срок: ARM-приложения зависают при "
    "запуске. Контейнер перезапустится.":
        "The current build has expired: ARM apps hang on launch. The container "
        "will restart.",
    "flatpak не обновил Merci": "flatpak did not update Merci",
    "сессия Waydroid не поднялась": "the Waydroid session did not start",
    "контейнер не получил адрес в сети": "the container did not get a network address",
    "Android внутри не отвечает": "Android inside does not respond",
    "картинка Android не отвечает": "the Android picture does not respond",
    "пропал транслятор ARM": "the ARM translator is gone",
    "контейнер рисует процессором: ускорение не включилось":
        "the container renders on the CPU: acceleration did not come up",
    "открытые модули": "open kernel modules",
    "закрытые модули": "proprietary kernel modules",
    "через Venus": "through Venus",
    "аппаратный ({detail})": "hardware ({detail})",
    "программный ({detail}) — рисует процессор": "software ({detail}) — the CPU renders",

    "запускается…": "starting…",
    "перезапускается…": "restarting…",
    "выключается…": "stopping…",
    "контейнер выключен — проверим при запуске":
        "the container is off — checked on launch",
    "Название": "Name",
    "Сохранить": "Save",
    "Переименовать запись": "Rename the entry",
    "Имя видно только в Merci: на само приложение оно не влияет.":
        "The name is only shown in Merci: it does not affect the app itself.",
    "Не удалось сохранить имя": "Could not save the name",
    "Закрывать нечего: в окнах этой записи оно не запущено":
        "Nothing to close: it is not running in this entry's windows",
    "Сейчас контейнер рисует процессором: Waydroid ходит в Mesa, "
    "а Mesa не умеет проприетарный драйвер NVIDIA.\n\n"
    "waydroid-nvidia ставит в контейнер драйвер, который рисует на "
    "видеокарте NVIDIA (у вас драйвер {detail}). Перед установкой он "
    "сам испытывает его на этой машине и, если не подходит, называет "
    "причину.\n\n"
    "Пакет waydroid будет заменён на waydroid-nvidia-bin — это тот же "
    "Waydroid с патчами, образ Android и данные остаются на месте. "
    "Частота обновления возьмётся из монитора: {refresh} Гц.":
        "Right now the container renders on the CPU: Waydroid goes through "
        "Mesa, and Mesa cannot use the proprietary NVIDIA driver.\n\n"
        "waydroid-nvidia puts a driver into the container that renders on the "
        "NVIDIA card (your driver is {detail}). Before installing, it tests the "
        "driver on this machine and, if it does not fit, names the reason.\n\n"
        "The waydroid package is replaced with waydroid-nvidia-bin — the same "
        "Waydroid with patches; the Android image and data stay in place. The "
        "refresh rate is taken from the monitor: {refresh} Hz.",
    "Проверить обновления не удалось: {error}": "Could not check for updates: {error}",
    "Доступны обновления: {what}": "Updates available: {what}",
    "Показать": "Show",
    "Встроенный обновлятор Android скрыт: обновления ставит Merci":
        "Android's built-in updater is hidden: Merci installs updates",
    "Встроенный обновлятор Android скрыт": "Android's built-in updater is hidden",
    "Встроенный обновлятор Android возвращён": "Android's built-in updater is back",
    "Обновить Merci?": "Update Merci?",
    "Дождитесь конца текущей операции": "Wait for the current operation to finish",
    "Всё запущенное в Android закроется. Понадобится пароль.":
        "Everything running in Android will be closed. Your password is needed.",
    "Загрузка — около {size} МБ.": "The download is about {size} MB.",
    "Обновить «{title}»?": "Update “{title}”?",
    "Установить обновления: {count}?": "Install updates: {count}?",
    "Не удалось подготовить обновление": "Could not prepare the update",
    "Обновление": "Update",
    "Обновление установлено": "The update is installed",
    "Обновление установлено: Android отвечает, настройки на месте":
        "The update is installed: Android responds, settings are in place",
    "После обновления не всё в порядке:": "Not everything is right after the update:",
    "Обновление установлено, но…": "The update is installed, but…",
    "Обновление не прижилось": "The update did not take",
    "Прежний образ Android сохранён: его можно вернуть — приложения "
    "и данные останутся как есть.":
        "The previous Android image is kept: you can bring it back — apps and "
        "data stay as they are.",
    "Вернуть прежний образ": "Restore the previous image",
    "Вернуть прежний образ Android?": "Restore the previous Android image?",
    "Нынешний образ заменится копией, сделанной перед обновлением. "
    "Приложения и их данные останутся. Всё запущенное в Android "
    "закроется, понадобится пароль.":
        "The current image is replaced with the copy made before the update. "
        "Apps and their data stay. Everything running in Android will be "
        "closed; your password is needed.",
    "Не удалось подготовить возврат": "Could not prepare the restore",
    "Возврат образа": "Restoring the image",
    "Прежний образ возвращён": "The previous image is back",
    "Удаляем копию прежнего образа…": "Deleting the copy of the previous image…",
    "Не удалось удалить копию": "Could not delete the copy",
    "Копия прежнего образа удалена": "The copy of the previous image is deleted",
    "Удалить копию прежнего образа?": "Delete the copy of the previous image?",
    "Вернуться к прежнему образу Android после этого будет нельзя. "
    "Понадобится пароль.":
        "After that you cannot go back to the previous Android image. Your "
        "password is needed.",
    "{title}: {version}": "{title}: {version}",
    "Команда скопирована": "Command copied",
    "Открыть страницу": "Open the page",
    "Обновляем Merci…": "Updating Merci…",
    "Merci не обновилась": "Merci was not updated",
    "Merci обновлена — новая версия заработает после перезапуска":
        "Merci is updated — the new version starts after a restart",
    "Перезапустить": "Restart",
    "Образ Android, пакет Waydroid, транслятор ARM и сама "
    "Merci. Проверка ничего не ставит — ставите вы, из окна "
    "обновлений.":
        "The Android image, the Waydroid package, the ARM translator and Merci "
        "itself. Checking installs nothing — you install, from the updates window.",
    "Проверить обновления": "Check for updates",
    "откроет окно обновлений": "opens the updates window",
    "Новый образ Android установлен, но Waydroid с ним не поднялся.":
        "The new Android image is installed, but Waydroid did not start with it.",
    "{part}: {old} → {new}": "{part}: {old} → {new}",
    "сборка от {date}": "build of {date}",
    "Похоже на известную ошибку waydroid-nvidia: его композитор "
    "иногда зависает, создавая окно. В новой версии она исправлена — "
    "обновление ждёт в окне обновлений.":
        "This looks like a known waydroid-nvidia bug: its composer sometimes "
        "hangs while creating a window. The new version fixes it — the update "
        "is waiting in the updates window.",
    "Если после обновления картинка не появится, выйдите из "
    "сеанса рабочего стола и войдите снова.":
        "If there is no picture after the update, log out of the desktop "
        "session and log in again.",
    "Обновлять саму Merci без вопросов": "Update Merci itself without asking",
    "новая версия ставится в фоне и включается после "
    "перезапуска; остальное Merci только предлагает":
        "the new version installs in the background and takes effect after a "
        "restart; everything else Merci only offers",
    "Проверка готовности не удалась": "The readiness check failed",
    "Merci не смогла выяснить, что уже установлено: {error}":
        "Merci could not find out what is already installed: {error}",
    "хост не ответил": "the host did not answer",
    "обновляется…": "updating…",
    "Идёт обновление Waydroid — дождитесь его конца":
        "Waydroid is being updated — wait for it to finish",
    "Идёт обновление Waydroid…": "Updating Waydroid…",
    "Возвращаем прежний образ Android…": "Restoring the previous Android image…",
    "«{name}» будет убрано из библиотеки. Waydroid сейчас выключен, "
    "поэтому установка в основном окне, если она там есть, останется: "
    "чтобы убрать и её, сначала включите Waydroid.":
        "“{name}” will be removed from the library. Waydroid is off right now, "
        "so its install in the main window, if there is one, stays: to remove "
        "that too, start Waydroid first.",
    "Не удалось подготовить шаг": "Could not prepare the step",
    "Открыть в окне {number}": "Open in window {number}",
    "У «{name}» есть своё окно {number} — можно открыть её там.":
        "“{name}” has its own window {number} — it can be opened there.",
    "Обновление не завершилось": "The update did not finish",
    "Что пошло не так — в журнале мастера. Если пакет уже "
    "обновился, а настройка ускорения не прошла, Waydroid не "
    "запустится, пока её не повторить: это сделает "
    "«Подготовить Waydroid».":
        "What went wrong is in the wizard's log. If the package is already "
        "updated but the acceleration setup failed, Waydroid will not start "
        "until the setup is repeated: “Set up Waydroid” does that.",
    "Проверяем, что получилось…": "Checking the result…",
    "идёт обновление Waydroid": "Waydroid is being updated",
    # ------------------------------- действия, которые смотрят на состояние ----
    "контейнер не принял настройку — попробуйте ещё раз":
        "the container did not take the setting — try again",
    "в этом профиле приложение не установлено": "the app is not installed in this profile",
    "Основное окно держит прежний размер — исправляем…":
        "The main window kept the old size — fixing…",
    "Перезапустить Waydroid?": "Restart Waydroid?",
    "Всё запущенное в Android закроется.": "Everything running in Android will be closed.",
    "Сейчас ничего не запущено": "Nothing is running right now",
    "Waydroid уже работает": "Waydroid is already running",
    "Waydroid уже выключен": "Waydroid is already off",
    "Режим экономии включится при запуске Android":
        "Eco Mode will turn on when Android starts",
    "Режим экономии выключен: анимации вернутся при запуске Android":
        "Eco Mode disabled: animations will come back when Android starts",
    "Лимит {fps} FPS применится при запуске Android":
        "The {fps} FPS limit will apply when Android starts",
    "Лимит FPS снимется при запуске Android":
        "The FPS limit will be removed when Android starts",
    "Не применилось: Android не запущен или не отвечает":
        "Not applied: Android is not running or not responding",
    "Память не очищена: Android не запущен или не отвечает":
        "Memory not cleaned: Android is not running or not responding",
    "«{name}» уже в библиотеке": "“{name}” is already in the library",
    "Встроенный обновлятор Android скроется при запуске Android":
        "Android's built-in updater will be hidden when Android starts",
    "Встроенный обновлятор Android вернётся при запуске Android":
        "Android's built-in updater will come back when Android starts",
    "Merci уже обновляется": "Merci is already updating",
    "Файл: {path}": "File: {path}",
    "Окно Merci занято другой операцией — дождитесь её конца и нажмите ещё раз":
        "The Merci window is busy with another operation — wait for it to "
        "finish and press again",
    # Отказ исполнителя шага: теперь его и правда можно увидеть.
    "контейнер не поднялся за две минуты":
        "the container did not come up in two minutes",
}
