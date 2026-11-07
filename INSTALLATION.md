# Installation

Noname Records runs on your computer or on a server you control. Docker Compose starts the application, its workers, Redis and the scheduler. Book text and recordings are stored in `data/`; your selected AI providers receive the material needed for the tasks you start.

For a personal installation, use macOS or Windows with Docker Desktop. The Linux installer supports a server with a domain. A server is the supported way to give voice actors remote access and use Telegram webhooks.

## macOS

Install and start Docker Desktop for your Mac. Wait until its engine is running, then open Terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/kamaero/noname-records/main/get.sh | bash
```

The installer asks for your studio name, administrator login and password, installation mode, and optional API keys. For a local installation, accept the default `computer` mode. Keep your administrator credentials for signing in.

When the installer reports that Noname Records is ready, open `http://localhost:8080/app/login`. The files are in `~/noname-records`. During an interactive local installation, the installer also tries to open the website in your browser.

## Windows

Install Docker Desktop with its WSL 2 backend and start it. Open Windows PowerShell and run:

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/kamaero/noname-records/main/get.ps1 | iex"
```

Answer the same questions as for macOS. Accept `computer` mode for a local installation. Open `http://localhost:8080/app/login` after startup completes. The installation directory is `$HOME\noname-records`.

The PowerShell installer sets access permissions on `.env` for the current Windows account. Use the same account for subsequent updates and backups.

## Linux server with HTTPS

Prepare a Linux server, a domain whose DNS A record points to that server, and inbound access to TCP ports 80 and 443. Caddy uses those ports to provide HTTPS. Keep enough disk space for recordings, generated sessions and backups.

Connect to the server with SSH. Replace `studio.example.com` with your domain:

```bash
curl -fsSL https://raw.githubusercontent.com/kamaero/noname-records/main/get.sh | bash -s -- --domain studio.example.com
```

The installer checks Docker and Docker Compose and can install Docker on Linux. It asks for studio and administrator details, creates `.env`, builds the application and starts the services. A domain enables the `https` Compose profile and secure cookies. Open `https://studio.example.com/app/login` when startup completes.

Linux installation without a domain is rejected, including in `--yes` and `--dry-run` mode. Local macOS and Windows installations do not require a domain. The application's direct port is bound to `127.0.0.1` by default; the server's public entry point is Caddy.

## Installing a downloaded checkout

If you already have the source files, run the installer from their directory:

```bash
./install.sh
```

On a Linux server, include `--domain studio.example.com`. On Windows:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

The download scripts use Git when it is available and an archive otherwise. Updates support both installation types. `NONAME_REPO` and `NONAME_ARCHIVE_URL` can select another source; `NONAME_FORCE_ARCHIVE` can force archive download. The shell updater selects Git when the existing checkout has a remote; the PowerShell updater also honours `NONAME_FORCE_ARCHIVE`.

## Preview and unattended installation

Dry runs describe the steps without building containers or writing an installation:

```bash
./install.sh --dry-run --domain studio.example.com
```

On Windows:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1 --dry-run
```

For unattended installation, supply `ADMIN_PASSWORD` securely in the environment and use `--yes`. Optional environment settings include `STUDIO_NAME`, `ADMIN_LOGIN`, `DOMAIN`, `APP_PORT` and provider keys. The shell installer also accepts `APP_BIND_HOST`. On Linux, set `DOMAIN` or pass `--domain`; the domain remains mandatory. The installer hashes the administrator password inside the application container and stores `ADMIN_PASSWORD_HASH`, not the original password.

An existing `.env` prevents the installer from overwriting an installation. Use the update script for an existing installation.

## AI keys and book language

You can skip API keys during installation and add them later in **Settings → AI providers**. Keys saved through the application take precedence over environment fallback keys. The installer creates `KEYS_ENCRYPTION_KEY` to protect keys stored in the database; retain it with your backups.

DeepSeek is the default provider for character extraction and attribution. OpenAI or RouterAI can provide speech recognition. RouterAI also supports optional review and sound tasks; ElevenLabs provides optional ambient generation. You pay the providers directly. Check the estimate before starting a paid task and configure your monthly limit in Settings.

Choose the book's language when uploading it. English books use English attribution prompts and skip Russian stress marking. Russian books retain their Russian language processing. Changing the interface language does not translate source text, character names or existing annotations.

## Optional Telegram bot

Use BotFather to create a bot. In `.env`, configure `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` without `@`, a random `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_NOTIFY_ENABLED=true`, and your `OWNER_TELEGRAM_ID` for owner notifications. Keep these values private.

From the installation directory, apply the settings and register the webhook:

```bash
docker compose up -d
docker compose exec web python -m scripts.telegram_set_webhook
```

Set the website domain for the bot with BotFather's `/setdomain` command if you want Telegram sign-in. Webhooks require a publicly reachable HTTPS server. To inspect the webhook:

```bash
docker compose exec web python -m scripts.telegram_set_webhook --info
```

For updates, backups, recovery and troubleshooting, see [Operations](OPERATIONS.md). The full configuration index is [Environment variables](docs/reference/ENV_VARS.md).

## Studio calendar

New installations use UTC unless you choose another IANA timezone, such as
`America/New_York` or `Europe/London`. The interactive installer shows the choice;
unattended installation accepts `--timezone America/New_York` (or `STUDIO_TIMEZONE`)
and logs its UTC default when no choice is supplied. The name is validated before
the installer writes `.env`. Manual installations should set `STUDIO_TIMEZONE`
before the first database migration.

The calendar is saved once in the database. It controls monthly AI spending periods,
new date-based role deadlines and deadline notifications, including daylight saving
time. Existing deadlines remain stored as their original UTC instants.

Upgraded installations retain their legacy UTC+3 calendar and historical spending
periods. Changing `.env` later does not reinterpret existing data. Moving an active
installation to a different calendar requires a separate reviewed migration; there
is no live timezone switch.
