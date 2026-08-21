#!/usr/bin/env bash
# Выкладывает содержимое dist/ в ветку gh-pages.
#
# Отдельной веткой, а не каталогом docs/ в основной: репозиторий ostree —
# это тысячи мелких файлов, и в истории основной ветки им делать нечего.
# Ветка переписывается целиком на каждую публикацию, поэтому история
# раздачи не растёт (у ostree она своя, внутри репозитория).
set -euo pipefail

cd "$(dirname "$0")/.."

[ -d dist/repo ] || { echo "сначала: bash scripts/publish.sh"; exit 1; }
git rev-parse --git-dir >/dev/null 2>&1 || { echo "здесь нет git-репозитория"; exit 1; }

REMOTE="${MERCI_REMOTE:-origin}"
git remote get-url "$REMOTE" >/dev/null 2>&1 || {
  echo "не настроен remote «$REMOTE»"
  exit 1
}

VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' src/merci/main.py)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo ":: готовим ветку gh-pages"
cp -r dist/. "$WORK/"
rm -rf "$WORK/build"

git -C "$WORK" init -q -b gh-pages
git -C "$WORK" add -A
git -C "$WORK" -c user.name="Merci" -c user.email="merci@localhost" \
  commit -qm "Раздача Merci $VERSION"

echo ":: отправляем (ветка перезаписывается целиком)"
git -C "$WORK" push -q --force "$(git remote get-url "$REMOTE")" gh-pages

cat <<INFO

готово. проверьте, что в настройках репозитория GitHub Pages включён
и берёт ветку gh-pages (корень).

через минуту-другую заработает:
  https://<владелец>.github.io/Merci/
INFO
