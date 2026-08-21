#!/usr/bin/env bash
# Сборка и установка Merci.
#
#   ./scripts/build.sh                 — под архитектуру машины
#   ./scripts/build.sh --arch=aarch64  — под ARM64 (нужен binfmt + qemu-user-static)
#
set -euo pipefail

cd "$(dirname "$0")/.."

APP_ID=xyz.hackerstone.Merci
MANIFEST="$APP_ID.yaml"
BUILD_DIR=build
REPO=repo
REMOTE=merci-origin

ARCH_ARG=()
ARCH="$(uname -m)"
for arg in "$@"; do
  case "$arg" in
    --arch=*) ARCH="${arg#--arch=}"; ARCH_ARG=("$arg") ;;
    *) echo "неизвестный аргумент: $arg" >&2; exit 2 ;;
  esac
done

if [[ "$ARCH" != "$(uname -m)" ]]; then
  # Флатпак чужой архитектуры исполняется через binfmt_misc, и флаг F
  # обязателен: без него интерпретатор не найдётся внутри песочницы.
  entry="/proc/sys/fs/binfmt_misc/qemu-${ARCH}"
  if [[ ! -e "$entry" ]] || ! grep -q '^flags:.*F' "$entry"; then
    echo "нет binfmt-записи qemu-$ARCH с флагом F." >&2
    echo "поставьте qemu-user-static и qemu-user-static-binfmt, затем:" >&2
    echo "  sudo systemctl restart systemd-binfmt" >&2
    exit 1
  fi
fi

echo ":: зависимости сборки ($ARCH)"

have() { flatpak info --arch="$ARCH" "$1" >/dev/null 2>&1; }

# flathub по умолчанию подключён только системно; сборка ставит приложение
# в пользовательскую область, и репозиторий нужен там же.
if ! flatpak remotes --user --columns=name | grep -qx flathub; then
  flatpak remote-add --user --if-not-exists flathub \
    https://dl.flathub.org/repo/flathub.flatpakrepo
fi

# Платформу и SDK не тянем повторно, если они уже стоят системно.
for runtime in org.gnome.Platform//50 org.gnome.Sdk//50; do
  have "${runtime%%//*}" || flatpak install --user --noninteractive \
    --arch="$ARCH" flathub "$runtime"
done

echo ":: сборка"
rm -rf "$BUILD_DIR"
flatpak-builder --force-clean --repo="$REPO" "${ARCH_ARG[@]+"${ARCH_ARG[@]}"}" \
  "$BUILD_DIR" "$MANIFEST"

echo ":: установка"
# URL задаём принудительно: если remote остался от прежней сборки и смотрит
# в другой каталог, --if-not-exists молча оставит старый адрес.
flatpak remote-add --user --if-not-exists --no-gpg-verify "$REMOTE" "$REPO"
flatpak remote-modify --user --no-gpg-verify --url="file://$PWD/$REPO" "$REMOTE"
flatpak install --user --or-update --noninteractive --arch="$ARCH" "$REMOTE" "$APP_ID"

echo
echo "готово. запуск:"
echo "  flatpak run --arch=$ARCH $APP_ID"
