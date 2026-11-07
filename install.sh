#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

dry_run=0
yes=0
domain="${DOMAIN:-}"
studio_timezone="${STUDIO_TIMEZONE:-UTC}"
while (($#)); do
  case "$1" in
    --yes) yes=1 ;;
    --dry-run) dry_run=1 ;;
    --domain) shift; domain="${1:?Specify a domain after --domain}" ;;
    --timezone) shift; studio_timezone="${1:?Specify an IANA timezone after --timezone}" ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

# Linux — только сервер (VPS) с доменом. Дикторы и режиссёры работают в DAW на Mac и
# Windows; «компьютер на Linux» никому из них не нужен, а сервер без домена дикторам не открыть
# (нет HTTPS — нет входа и Telegram-бота). Проверка до --dry-run: её видно и без Docker.
linux_needs_domain() {
  echo "On Linux, installation is supported only on a server (VPS) with a domain: ./install.sh --domain studio.example.com" >&2
  echo "For a personal computer, use Mac or Windows; see INSTALLATION.md." >&2
  exit 2
}
if [[ "$(uname -s)" == Linux && -z "$domain" ]]; then
  if ((yes || dry_run)) || [[ ! -t 0 ]]; then linux_needs_domain; fi
  read -r -p "Server domain (for example, studio.example.com): " domain
  [[ -n "$domain" ]] || linux_needs_domain
fi

if ((dry_run)); then
  cat <<'EOF'
Check Docker and Docker Compose.
Build the application and hash the password inside the container.
Create a protected .env file and the data directory.
Start the website, Redis, two workers and the scheduler.
Wait for /health and display the sign-in address.
Open the website in a browser for an interactive local installation.
EOF
  exit 0
fi

if [[ -e .env ]]; then
  echo "Noname Records is already installed; .env has been preserved. Run ./update.sh to update or ./backup.sh to back up your data." >&2
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
        echo "Docker is installed; trying to start its service."
        if [[ $EUID -eq 0 ]]; then systemctl start docker || true; else sudo systemctl start docker || true; fi
      else
        echo "Installing Docker and Docker Compose. You may be asked for an administrator password."
        if ! command -v curl >/dev/null 2>&1 && ! command -v wget >/dev/null 2>&1; then
          if ! command -v apt-get >/dev/null 2>&1; then echo "Install curl and run the installer again." >&2; exit 1; fi
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
      echo "Install and start Docker Desktop: https://www.docker.com/products/docker-desktop/"
      open "https://www.docker.com/products/docker-desktop/" || true
      ;;
    *) echo "For this operating system, use install.ps1 or install Docker manually." >&2; exit 1 ;;
  esac
  echo "Waiting for Docker to start..."
  for _ in {1..120}; do
    if docker_ready; then return; fi
    sleep 5
  done
  echo "Docker did not start. Start Docker Desktop or the Docker service and run the installer again." >&2
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
  [[ -n "$password" ]] || { echo "Set ADMIN_PASSWORD when using --yes." >&2; exit 2; }
else
  studio="$(ask 'Studio name' 'Noname Records')"
  studio_timezone="$(ask 'Studio IANA timezone (for example America/New_York)' "$studio_timezone")"
  login="$(ask 'Administrator login' 'admin')"
  read -r -s -p "Administrator password: " password; echo
  [[ -n "$password" ]] || { echo "The password cannot be empty." >&2; exit 2; }
  if [[ -z "$domain" ]]; then
    mode="$(ask 'Mode: computer or server' 'computer')"
    if [[ "$mode" == "server" || "$mode" == "сервер" ]]; then read -r -p "Domain (for example, studio.example.com): " domain; fi
  fi
fi

if [[ ! "$studio_timezone" =~ ^[A-Za-z0-9._+-]+(/[A-Za-z0-9._+-]+)*$ ]]; then
  echo "Invalid IANA timezone. Use UTC or a name such as America/New_York." >&2; exit 2
fi
echo "Requested studio timezone: $studio_timezone (new empty databases only; existing data keeps its legacy calendar)."

if [[ -n "$domain" && ! "$domain" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}$ ]]; then
  echo "Invalid domain." >&2; exit 2
fi
if (( ! yes )); then
  read -r -p "DeepSeek key for book attribution (optional): " DEEPSEEK_API_KEY
  read -r -p "OpenAI key for recording alignment (optional): " OPENAI_API_KEY
  read -r -p "RouterAI key for recording alignment and review (optional): " ROUTERAI_API_KEY
  read -r -p "Claude key for an additional attribution review (optional): " CLAUDE_API_KEY
  read -r -p "ElevenLabs key for ambient sound (optional): " ELEVENLABS_API_KEY
fi
for value in "$studio" "$login" "$password" "${STUDIO_CONTACT_NAME:-}" "${OPENAI_API_KEY:-}" "${CLAUDE_API_KEY:-}" "${DEEPSEEK_API_KEY:-}" "${ROUTERAI_API_KEY:-}" "${ELEVENLABS_API_KEY:-}"; do
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then echo "Values must not contain line breaks." >&2; exit 2; fi
done
for value in "$studio" "$login" "${STUDIO_CONTACT_NAME:-}" "${OPENAI_API_KEY:-}" "${CLAUDE_API_KEY:-}" "${DEEPSEEK_API_KEY:-}" "${ROUTERAI_API_KEY:-}" "${ELEVENLABS_API_KEY:-}"; do
  if [[ "$value" == *'$'* || "$value" == *'#'* ]]; then echo "Settings must not contain the characters $ or #." >&2; exit 2; fi
done

ensure_docker
export APP_PORT="${APP_PORT:-8080}"
export APP_BIND_HOST="${APP_BIND_HOST:-127.0.0.1}"
if [[ ! "$APP_BIND_HOST" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo "Invalid APP_BIND_HOST address. Specify an IPv4 address or remove this setting." >&2
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

echo "Building the application. The first run may take several minutes."
docker compose build web
docker compose run --no-deps --rm -T web python scripts/validate_studio_timezone.py "$studio_timezone"
password_hash="$(printf '%s\n' "$password" | docker compose run --no-deps --rm -T web python scripts/generate_password_hash.py)"
unset password ADMIN_PASSWORD
[[ "$password_hash" == '$pbkdf2-sha256$'* ]] || { echo "Could not hash the password." >&2; exit 1; }

# Ключ шифрования ключей нейросетей в базе — той же библиотекой, что их потом расшифрует.
keys_encryption_key="$(docker compose run --no-deps --rm -T web python scripts/generate_encryption_key.py)"
[[ ${#keys_encryption_key} -eq 44 ]] || { echo "Could not create the encryption key." >&2; exit 1; }

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
STUDIO_TIMEZONE=$studio_timezone
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

echo "Starting Noname Records..."
docker compose up -d --build
echo "Waiting for the website to respond..."
for _ in {1..90}; do
  if curl -fsS "http://localhost:$APP_PORT/health" >/dev/null 2>&1; then
    echo "Noname Records is ready. Sign in: $base_url/app/login"
    if (( ! yes )) && [[ -z "$domain" ]]; then
      case "$(uname -s)" in
        Darwin)
          if command -v open >/dev/null 2>&1; then open "$base_url" >/dev/null 2>&1 || true; fi
          ;;
      esac
    fi
    exit 0
  fi
  sleep 2
done
echo "The website did not respond within 3 minutes. Check: docker compose logs web" >&2
exit 1
