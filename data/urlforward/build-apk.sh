#!/bin/sh
# Собирает перехватчик ссылок в APK без Gradle.
#
# Gradle тянул бы за собой полсотни зависимостей и сеть на каждый запуск,
# а нам нужны ровно четыре шага: компиляция классов, перевод их в dex,
# упаковка ресурсов и подпись. Инструменты берутся из android-sdk-build-tools,
# android.jar — из android-platform.
#
# Аргументы: <каталог с исходниками> <куда положить apk>
set -e

SRC="${1:?нужен каталог с исходниками}"
OUT="${2:?нужен путь для apk}"
WORK="$(mktemp -d /tmp/merci-apk.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

SDK="${ANDROID_HOME:-/opt/android-sdk}"
TOOLS="$(ls -d "$SDK"/build-tools/* 2>/dev/null | sort -V | tail -1)"
[ -n "$TOOLS" ] || { echo "не найдены build-tools"; exit 1; }

# android.jar берём готовым файлом, а не пакетом android-platform: тот тянет
# за собой весь android-sdk из AUR ради одной библиотеки для компиляции.
ANDROID_JAR="${ANDROID_JAR:-$SDK/platforms/android-33/android.jar}"
[ -f "$ANDROID_JAR" ] || { echo "не найден android.jar: $ANDROID_JAR"; exit 1; }

echo "android.jar: $ANDROID_JAR"
echo "инструменты: $TOOLS"

# 1. Компилируем java в class-файлы. Целевая версия 8: dex-компилятор
#    старших не понимает без дополнительной настройки.
mkdir -p "$WORK/classes"
javac -source 8 -target 8 -nowarn \
    -bootclasspath "$ANDROID_JAR" \
    -classpath "$ANDROID_JAR" \
    -d "$WORK/classes" \
    $(find "$SRC/src" -name '*.java')

# 2. class → dex.
"$TOOLS/d8" --release --lib "$ANDROID_JAR" \
    --output "$WORK" $(find "$WORK/classes" -name '*.class')

# 3. Собираем APK: манифест плюс dex.
"$TOOLS/aapt2" link \
    -I "$ANDROID_JAR" \
    --manifest "$SRC/AndroidManifest.xml" \
    --min-sdk-version 21 --target-sdk-version 33 \
    -o "$WORK/unsigned.apk" \
    --auto-add-overlay
(cd "$WORK" && zip -q unsigned.apk classes.dex)

# 4. Подпись. Ключ создаём свой, одноразовый: Android ставит только
#    подписанные пакеты, а публиковать это приложение никуда не нужно.
# Ключ держим рядом с исходниками, а не во временном каталоге: с новым
# ключом на каждую сборку Android отказывается обновлять уже установленный
# перехватчик — «signatures do not match newer version», — и переустановка
# становится единственным выходом.
# Пароль ниже не секрет: ключ создаётся на этой же машине, подписывает
# только наш крошечный перехватчик ссылок и никуда не уезжает — в git он не
# попадает (см. .gitignore). Нужен он лишь затем, чтобы Android принимал
# обновления перехватчика как обновления, а не как чужое приложение.
KEYSTORE="${MERCI_KEYSTORE:-$SRC/merci.keystore}"
if [ ! -s "$KEYSTORE" ]; then
    keytool -genkeypair -keystore "$KEYSTORE" -alias merci \
        -storepass merci123 -keypass merci123 \
        -keyalg RSA -keysize 2048 -validity 10000 \
        -dname "CN=Merci URL forwarder" >/dev/null 2>&1
fi

"$TOOLS/apksigner" sign --ks "$KEYSTORE" --ks-key-alias merci \
    --ks-pass pass:merci123 --key-pass pass:merci123 \
    --out "$OUT" "$WORK/unsigned.apk"

echo "готов: $OUT"
