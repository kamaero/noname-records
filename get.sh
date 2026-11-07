#!/usr/bin/env bash
set -euo pipefail

NONAME_REPO="${NONAME_REPO:-https://github.com/kamaero/noname-records.git}"
NONAME_ARCHIVE_URL="${NONAME_ARCHIVE_URL:-${NONAME_REPO%.git}/archive/refs/heads/main.tar.gz}"
target="${HOME:?Home directory is not set}/noname-records"

if [[ -f "$target/.env" ]]; then
  echo "Noname Records is already installed in $target. To update: cd \"$target\" && ./update.sh"
  exit 0
fi
noninteractive=0
for arg in "$@"; do
  if [[ "$arg" == "--yes" || "$arg" == "--dry-run" ]]; then noninteractive=1; fi
done
if ((!noninteractive)) && ! (: </dev/tty) 2>/dev/null; then
  echo "An interactive terminal is required. Open Terminal and run this command there." >&2
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
    echo "Continuing installation from $target..."
    run_installer "$@"
    exit
  fi
  echo "The directory $target already exists, but contains no Noname Records installation. Rename it and run the installer again; existing files have not been changed." >&2
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
    echo "Downloading requires curl or wget. Install either tool and run this command again." >&2
    exit 1
  fi
}

if command -v git >/dev/null 2>&1 && [[ "${NONAME_FORCE_ARCHIVE:-0}" != 1 ]]; then
  echo "Downloading Noname Records with Git..."
  git clone --depth 1 "$NONAME_REPO" "$stage/checkout"
  source_dir="$stage/checkout"
else
  echo "Downloading the Noname Records archive..."
  download_archive
  mkdir "$stage/extract"
  tar -xzf "$stage/source.tar.gz" -C "$stage/extract"
  shopt -s nullglob
  roots=("$stage/extract"/*)
  shopt -u nullglob
  if (("${#roots[@]}" != 1)) || [[ ! -d "${roots[0]}" ]]; then
    echo "The archive has an unexpected structure. Installation stopped; try downloading it again." >&2
    exit 1
  fi
  source_dir="${roots[0]}"
fi

if [[ ! -f "$source_dir/install.sh" || ! -f "$source_dir/update.sh" || ! -f "$source_dir/Dockerfile" ]]; then
  echo "The download is missing required application files. Installation stopped." >&2
  exit 1
fi
mv "$source_dir" "$target"
echo "Noname Records downloaded to $target"

run_installer "$@"
