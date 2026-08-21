#!/usr/bin/env bash
# Собирает Merci и готовит всё для раздачи через Flatpak.
#
# Раздача устроена просто: собранное приложение выкладывается в статический
# репозиторий ostree, который лежит на GitHub Pages. Для пользователя это
# один раз «remote-add», дальше — обычный `flatpak update`.
#
#   dist/repo/                 сам репозиторий (его и публикуем)
#   dist/merci.flatpakrepo     файл-описание для remote-add
#   dist/Merci.flatpak         один файл на случай установки без репозитория
#   dist/index.html            страница со ссылками
#
# Подпись GPG необязательна, но желательна: с ней клиент проверяет не только
# канал (HTTPS), но и сам образ. Ключ берётся из MERCI_GPG_KEY.
set -euo pipefail

cd "$(dirname "$0")/.."

OWNER="${MERCI_OWNER:-melineceo}"
PAGES="https://${OWNER}.github.io/Merci"
APP_ID="xyz.hackerstone.Merci"
MANIFEST="${APP_ID}.yaml"
DIST="dist"
ARCH="${ARCH:-$(flatpak --default-arch)}"

VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' src/merci/main.py)"
[ -n "$VERSION" ] || { echo "не нашёл версию в src/merci/main.py"; exit 1; }
echo ":: Merci $VERSION ($ARCH)"

GPG_ARGS=()
if [ -n "${MERCI_GPG_KEY:-}" ]; then
  echo ":: подписываем ключом $MERCI_GPG_KEY"
  GPG_ARGS=(--gpg-sign="$MERCI_GPG_KEY")
else
  echo ":: без подписи GPG (задайте MERCI_GPG_KEY, чтобы подписывать)"
fi

echo ":: сборка"
rm -rf "$DIST/build"
flatpak-builder --force-clean --repo="$DIST/repo" --arch="$ARCH" \
  "${GPG_ARGS[@]}" "$DIST/build" "$MANIFEST"

echo ":: одиночный файл"
rm -f "$DIST/Merci.flatpak"
flatpak build-bundle --arch="$ARCH" "${GPG_ARGS[@]}" \
  "$DIST/repo" "$DIST/Merci.flatpak" "$APP_ID"

echo ":: описание репозитория"
{
  echo "[Flatpak Repo]"
  echo "Title=Merci"
  echo "Url=$PAGES/repo/"
  echo "Homepage=https://github.com/$OWNER/Merci"
  echo "Comment=Запуск Android-приложений в Linux"
  echo "Description=Библиотека APK с графической оболочкой над Waydroid"
  echo "Icon=$PAGES/icon.png"
  if [ -n "${MERCI_GPG_KEY:-}" ]; then
    echo "GPGKey=$(gpg --export "$MERCI_GPG_KEY" | base64 --wrap=0)"
  fi
} > "$DIST/merci.flatpakrepo"

echo ":: ссылка на приложение"
{
  echo "[Flatpak Ref]"
  echo "Title=Merci"
  echo "Name=$APP_ID"
  echo "Branch=master"
  echo "Url=$PAGES/repo/"
  echo "RuntimeRepo=https://dl.flathub.org/repo/flathub.flatpakrepo"
  echo "IsRuntime=false"
  if [ -n "${MERCI_GPG_KEY:-}" ]; then
    echo "GPGKey=$(gpg --export "$MERCI_GPG_KEY" | base64 --wrap=0)"
  fi
} > "$DIST/Merci.flatpakref"

cp data/icons/256x256/${APP_ID}.png "$DIST/icon.png"

# Страница нужна не для красоты: GitHub Pages отдаёт каталог, и без неё
# по адресу проекта пользователь увидит список файлов.
sed -e "s|__OWNER__|$OWNER|g" -e "s|__VERSION__|$VERSION|g" \
  scripts/pages-index.html > "$DIST/index.html"

# Pages не отдаёт файлы и каталоги, начинающиеся с точки или подчёркивания,
# а ostree-репозиторий без них нерабочий.
touch "$DIST/.nojekyll"

cat <<INFO

готово. в $DIST лежит всё для публикации:

  repo/                 репозиторий Flatpak
  merci.flatpakrepo     для: flatpak remote-add --user merci $PAGES/merci.flatpakrepo
  Merci.flatpakref      для: flatpak install --user $PAGES/Merci.flatpakref
  Merci.flatpak         одиночный файл (без обновлений)

выложить на GitHub Pages:
  bash scripts/publish-pages.sh
INFO
