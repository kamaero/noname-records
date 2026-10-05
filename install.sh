#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

dry_run=0
yes=0
domain="${DOMAIN:-}"
while (($#)); do
  case "$1" in
    --yes) yes=1 ;;
    --dry-run) dry_run=1 ;;
    --domain) shift; domain="${1:?Укажите домен после --domain}" ;;
    *) echo "Неизвестный параметр: $1" >&2; exit 2 ;;
  esac
  shift
done

if ((dry_run)); then
  cat <<'EOF'
Проверка Docker и Docker Compose.
Сборка приложения и вычисление хеша пароля внутри контейнера.
Создание защищённого файла .env и каталога data.
Запуск сайта, Redis, двух обработчиков и планировщика.
Ожидание /health и вывод адреса входа.
В интерактивном локальном режиме — открытие сайта в браузере.
EOF
  exit 0
fi

if [[ -e .env ]]; then
  echo "Система уже установлена: файл .env сохранён. Для новой версии запустите ./update.sh, для копии данных — ./backup.sh." >&2
  exit 1
fi

USE_SUDO_DOCKER=0
docker() {
  if ((USE_SUDO_DOCKER)); then sudo docker "$@"; else command docker "$@"; fi
}
docker_ready() {
  if ! type -P docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then return 1; fi
  if docker info >/dev/null 2>&1; then return 0; fi
  if [[ "$(uname -s)" == Linux && $EUID -ne 0 ]] && sudo docker info >/dev/null 2>&1; then
    USE_SUDO_DOCKER=1
    return 0
  fi
  return 1
}
ensure_docker() {
  if docker_ready; then return; fi
  case "$(uname -s)" in
    Linux)
      if type -P docker >/dev/null 2>&1; then
        echo "Docker установлен; пробую запустить службу."
        if [[ $EUID -eq 0 ]]; then systemctl start docker || true; else sudo systemctl start docker || true; fi
      else
        echo "Устанавливаю Docker и Docker Compose. Может потребоваться пароль администратора."
        if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
          if ! command -v apt-get >/dev/null 2>&1; then echo "Установите curl и повторите установку." >&2; exit 1; fi
          if [[ $EUID -eq 0 ]]; then apt-get update && apt-get install -y curl ca-certificates; else sudo apt-get update && sudo apt-get install -y curl ca-certificates; fi
        fi
        if command -v curl >/dev/null 2>&1; then
          curl -fsSL https://get.docker.com -o /tmp/noname-get-docker.sh
        else
          wget -qO /tmp/noname-get-docker.sh https://get.docker.com
        fi
        if [[ $EUID -eq 0 ]]; then sh /tmp/noname-get-docker.sh; else sudo sh /tmp/noname-get-docker.sh; fi
        rm -f /tmp/noname-get-docker.sh
      fi
      ;;
    Darwin)
      echo "Установите и запустите Docker Desktop: https://www.docker.com/products/docker-desktop/"
      open "https://www.docker.com/products/docker-desktop/" || true
      ;;
    *) echo "Для этой системы используйте install.ps1 или установите Docker вручную." >&2; exit 1 ;;
  esac
  echo "Жду запуска Docker..."
  for _ in {1..120}; do
    if docker_ready; then return; fi
    sleep 5
  done
  echo "Docker не запустился. Запустите Docker Desktop или службу Docker и повторите установку." >&2
  exit 1
}

ask() {
  local message="$1" default="$2" value
  read -r -p "$message [$default]: " value
  printf '%s' "${value:-$default}"
}

if ((yes)); then
  studio="${STUDIO_NAME:-Noname Records}"
  login="${ADMIN_LOGIN:-admin}"
  password="${ADMIN_PASSWORD:-}"
  [[ -n "$password" ]] || { echo "Для --yes задайте ADMIN_PASSWORD." >&2; exit 2; }
else
  studio="$(ask 'Название студии' 'Noname Records')"
  login="$(ask 'Логин администратора' 'admin')"
  read -r -s -p "Пароль администратора: " password; echo
  [[ -n "$password" ]] || { echo "Пароль не может быть пустым." >&2; exit 2; }
  if [[ -z "$domain" ]]; then
    mode="$(ask 'Режим: компьютер или сервер' 'компьютер')"
    if [[ "$mode" == "сервер" ]]; then read -r -p "Домен (например, studio.example.com): " domain; fi
  fi
fi

if [[ -n "$domain" && ! "$domain" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}$ ]]; then
  echo "Неверный домен." >&2; exit 2
fi
if (( ! yes )); then
  read -r -p "Ключ DeepSeek для разметки книги (можно пропустить): " DEEPSEEK_API_KEY
  read -r -p "Ключ OpenAI для сверки записей (можно пропустить): " OPENAI_API_KEY
  read -r -p "Ключ RouterAI для сверки записей и консилиума (можно пропустить): " ROUTERAI_API_KEY
  read -r -p "Ключ Claude для дополнительной проверки разметки (можно пропустить): " CLAUDE_API_KEY
  read -r -p "Ключ ElevenLabs для фоновых звуков (можно пропустить): " ELEVENLABS_API_KEY
fi
for value in "$studio" "$login" "$password" "${STUDIO_CONTACT_NAME:-}" "${OPENAI_API_KEY:-}" "${CLAUDE_API_KEY:-}" "${DEEPSEEK_API_KEY:-}" "${ROUTERAI_API_KEY:-}" "${ELEVENLABS_API_KEY:-}"; do
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then echo "Значения не должны содержать перевод строки." >&2; exit 2; fi
done
for value in "$studio" "$login" "${STUDIO_CONTACT_NAME:-}" "${OPENAI_API_KEY:-}" "${CLAUDE_API_KEY:-}" "${DEEPSEEK_API_KEY:-}" "${ROUTERAI_API_KEY:-}" "${ELEVENLABS_API_KEY:-}"; do
  if [[ "$value" == *'$'* || "$value" == *'#'* ]]; then echo "В настройках нельзя использовать символы $ или #." >&2; exit 2; fi
done

ensure_docker
export APP_PORT="${APP_PORT:-8080}"
export APP_BIND_HOST="${APP_BIND_HOST:-127.0.0.1}"
if [[ ! "$APP_BIND_HOST" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "Неверный адрес APP_BIND_HOST. Укажите IP-адрес или уберите эту настройку." >&2
  exit 2
fi
asr_provider=openai
if [[ -n "${ROUTERAI_API_KEY:-}" && -z "${OPENAI_API_KEY:-}" ]]; then asr_provider=routerai; fi
if [[ -n "$domain" ]]; then
  export DOMAIN="$domain"
  export COMPOSE_PROFILES=https
  compose_profiles=https
  base_url="https://$domain"
  cookie_secure=true
  reserve_gb=5
else
  unset COMPOSE_PROFILES
  base_url="http://localhost:$APP_PORT"
  cookie_secure=false
  reserve_gb=2
  compose_profiles=
fi

echo "Собираю приложение. Первый запуск может занять несколько минут."
docker compose build web
password_hash="$(printf '%s\n' "$password" | docker compose run --no-deps --rm -T web python scripts/generate_password_hash.py)"
unset password ADMIN_PASSWORD
[[ "$password_hash" == '$pbkdf2-sha256$'* ]] || { echo "Не удалось вычислить хеш пароля." >&2; exit 1; }

# Ключ шифрования ключей нейросетей в базе — той же библиотекой, что их потом расшифрует.
keys_encryption_key="$(docker compose run --no-deps --rm -T web python scripts/generate_encryption_key.py)"
[[ ${#keys_encryption_key} -eq 44 ]] || { echo "Не удалось создать ключ шифрования." >&2; exit 1; }

if command -v openssl >/dev/null 2>&1; then
  secret_key="$(openssl rand -hex 48)"
else
  secret_key="$(od -An -N48 -tx1 /dev/urandom | tr -d ' \n')"
fi
umask 077
mkdir -p data/recordings
env_tmp="$(mktemp .env.XXXXXX)"
trap 'rm -f "$env_tmp"' EXIT
cat > "$env_tmp" <<EOF
APP_NAME=Noname Records
STUDIO_NAME=$studio
STUDIO_CONTACT_NAME=${STUDIO_CONTACT_NAME:-}
APP_BASE_URL=$base_url
PRODUCTION=1
SECRET_KEY=$secret_key
KEYS_ENCRYPTION_KEY=$keys_encryption_key
ADMIN_LOGIN=$login
ADMIN_PASSWORD_HASH='$password_hash'
DATABASE_URL=sqlite:///./data/noname.db
REDIS_URL=redis://redis:6379/0
AUDIO_STORAGE_PATH=/app/data/recordings
AUDIO_NAS_PATH=
LOCAL_RESERVE_GB=$reserve_gb
COOKIE_SECURE=$cookie_secure
ASR_PROVIDER=$asr_provider
TELEGRAM_NOTIFY_ENABLED=false
DOMAIN=$domain
COMPOSE_PROFILES=$compose_profiles
APP_PORT=$APP_PORT
APP_BIND_HOST=$APP_BIND_HOST
OPENAI_API_KEY=${OPENAI_API_KEY:-}
CLAUDE_API_KEY=${CLAUDE_API_KEY:-}
DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-}
ROUTERAI_API_KEY=${ROUTERAI_API_KEY:-}
ELEVENLABS_API_KEY=${ELEVENLABS_API_KEY:-}
EOF
chmod 600 "$env_tmp"
mv "$env_tmp" .env
trap - EXIT

echo "Запускаю систему..."
docker compose up -d --build
echo "Жду ответа сайта..."
for _ in {1..90}; do
  if curl -fsS "http://localhost:$APP_PORT/health" >/dev/null 2>&1; then
    echo "Система готова. Вход: $base_url/app/login"
    if (( ! yes )) && [[ -z "$domain" ]]; then
      case "$(uname -s)" in
        Darwin)
          if command -v open >/dev/null 2>&1; then open "$base_url" >/dev/null 2>&1 || true; fi
          ;;
        Linux)
          if [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] && command -v xdg-open >/dev/null 2>&1; then
            xdg-open "$base_url" >/dev/null 2>&1 || true
          fi
          ;;
      esac
    fi
    exit 0
  fi
  sleep 2
done
echo "Сайт не ответил за 3 минуты. Проверьте: docker compose logs web" >&2
exit 1
