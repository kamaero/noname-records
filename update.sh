#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

NONAME_REPO="${NONAME_REPO:-https://github.com/kamaero/noname-records.git}"
NONAME_ARCHIVE_URL="${NONAME_ARCHIVE_URL:-${NONAME_REPO%.git}/archive/refs/heads/main.tar.gz}"

docker() {
  if command docker info >/dev/null 2>&1; then command docker "$@"; else sudo docker "$@"; fi
}
if [[ "${1:-}" == "--dry-run" ]]; then
  echo "Создать резервную копию, получить новую версию через Git или архив, пересобрать образ и перезапустить сервисы."
  exit 0
fi
if (($#)); then echo "Использование: $0 [--dry-run]" >&2; exit 2; fi

if [[ -d .git ]] && command -v git >/dev/null 2>&1 && [[ -n "$(git remote)" ]]; then
  ./backup.sh
  git pull --ff-only
  docker compose up -d --build
  echo "Обновление выполнено."
  exit 0
fi

echo "Получаю новую версию из архива..."
stage="$(mktemp -d "${TMPDIR:-/tmp}/noname-update.XXXXXX")"
new_sh=
new_ps=
cleanup() {
  rm -rf "$stage"
  [[ -z "$new_sh" ]] || rm -f "$new_sh"
  [[ -z "$new_ps" ]] || rm -f "$new_ps"
}
trap cleanup EXIT

if command -v curl >/dev/null 2>&1; then
  curl -fLsS "$NONAME_ARCHIVE_URL" -o "$stage/source.tar.gz"
elif command -v wget >/dev/null 2>&1; then
  wget -qO "$stage/source.tar.gz" "$NONAME_ARCHIVE_URL"
else
  echo "Для обновления нужен curl или wget. Установите одну из этих программ." >&2
  exit 1
fi
mkdir "$stage/extract"
tar -xzf "$stage/source.tar.gz" -C "$stage/extract"
shopt -s nullglob
roots=("$stage/extract"/*)
shopt -u nullglob
if (("${#roots[@]}" != 1)) || [[ ! -d "${roots[0]}" ]]; then
  echo "Архив имеет неожиданный вид. Существующая версия сохранена." >&2
  exit 1
fi
source_dir="${roots[0]}"
if [[ ! -f "$source_dir/install.sh" || ! -f "$source_dir/update.sh" || ! -f "$source_dir/Dockerfile" ]]; then
  echo "В архиве нет нужных файлов системы. Существующая версия сохранена." >&2
  exit 1
fi

# Prepare sibling files now; replacing this running script is the final step.
new_sh="$(mktemp "./.update.sh.XXXXXX")"
cp "$source_dir/update.sh" "$new_sh"
chmod 755 "$new_sh"
if [[ -f "$source_dir/update.ps1" ]]; then
  new_ps="$(mktemp "./.update.ps1.XXXXXX")"
  cp "$source_dir/update.ps1" "$new_ps"
fi

./backup.sh
if command -v rsync >/dev/null 2>&1; then
  rsync -a --exclude=.env --exclude=data/ --exclude=backups/ --exclude=.git/ \
    --exclude=.venv/ --exclude=frontend/node_modules/ --exclude=update.sh \
    --exclude=update.ps1 "$source_dir/" "./"
else
  (cd "$source_dir" && tar -cf - --exclude='./.env' --exclude='./data' \
    --exclude='./backups' --exclude='./.git' --exclude='./.venv' \
    --exclude='./frontend/node_modules' --exclude='./update.sh' \
    --exclude='./update.ps1' .) | tar -xf - -C .
fi
docker compose up -d --build
if [[ -n "$new_ps" ]]; then mv -f "$new_ps" update.ps1; new_ps=; fi
mv -f "$new_sh" update.sh
new_sh=
echo "Обновление выполнено."
