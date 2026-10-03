#!/usr/bin/env bash
set -euo pipefail

NONAME_REPO="${NONAME_REPO:-https://github.com/kamaero/noname-records.git}"
NONAME_ARCHIVE_URL="${NONAME_ARCHIVE_URL:-${NONAME_REPO%.git}/archive/refs/heads/main.tar.gz}"
target="${HOME:?Не задан домашний каталог}/noname-records"

if [[ -f "$target/.env" ]]; then
  echo "Система уже установлена в $target. Обновление: cd \"$target\" && ./update.sh"
  exit 0
fi
noninteractive=0
for arg in "$@"; do
  if [[ "$arg" == "--yes" || "$arg" == "--dry-run" ]]; then noninteractive=1; fi
done
if ((!noninteractive)) && ! (: </dev/tty) 2>/dev/null; then
  echo "Для ответов на вопросы нужен терминал. Откройте Терминал и запустите команду там." >&2
  exit 1
fi
run_installer() {
  if ((noninteractive)); then
    bash "$target/install.sh" "$@"
  else
    bash "$target/install.sh" "$@" </dev/tty
  fi
}
if [[ -e "$target" ]]; then
  if [[ -f "$target/install.sh" && -f "$target/Dockerfile" ]]; then
    echo "Продолжаю установку из $target..."
    run_installer "$@"
    exit
  fi
  echo "Папка $target уже существует, но файлов системы в ней нет. Переименуйте её и запустите установку снова; существующие файлы не изменены." >&2
  exit 1
fi

mkdir -p "$HOME"
stage="$(mktemp -d "$HOME/.noname-download.XXXXXX")"
trap 'rm -rf "$stage"' EXIT

download_archive() {
  if command -v curl >/dev/null 2>&1; then
    curl -fLsS "$NONAME_ARCHIVE_URL" -o "$stage/source.tar.gz"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO "$stage/source.tar.gz" "$NONAME_ARCHIVE_URL"
  else
    echo "Для загрузки нужен curl или wget. Установите одну из этих программ и повторите команду." >&2
    exit 1
  fi
}

if command -v git >/dev/null 2>&1 && [[ "${NONAME_FORCE_ARCHIVE:-0}" != 1 ]]; then
  echo "Скачиваю Noname Records через Git..."
  git clone --depth 1 "$NONAME_REPO" "$stage/checkout"
  source_dir="$stage/checkout"
else
  echo "Скачиваю архив Noname Records..."
  download_archive
  mkdir "$stage/extract"
  tar -xzf "$stage/source.tar.gz" -C "$stage/extract"
  shopt -s nullglob
  roots=("$stage/extract"/*)
  shopt -u nullglob
  if (("${#roots[@]}" != 1)) || [[ ! -d "${roots[0]}" ]]; then
    echo "Архив имеет неожиданный вид. Установка остановлена; попробуйте скачать его снова." >&2
    exit 1
  fi
  source_dir="${roots[0]}"
fi

if [[ ! -f "$source_dir/install.sh" || ! -f "$source_dir/update.sh" || ! -f "$source_dir/Dockerfile" ]]; then
  echo "В загрузке нет нужных файлов системы. Установка остановлена." >&2
  exit 1
fi
mv "$source_dir" "$target"
echo "Система скачана в $target"

run_installer "$@"
