# Operations

Run these commands from the installation directory: `~/noname-records` on macOS or Linux, or `$HOME\noname-records` in Windows PowerShell. Keep Docker running on personal computers. A server installation runs continuously through Docker Compose.

## Updates

Finish or stop active book, review, sound and recognition runs before updating. Restarting workers interrupts active tasks; book processing can be started again from the hub, where completed chapters are skipped.

On macOS or Linux:

```bash
./update.sh
```

On Windows:

```powershell
powershell -ExecutionPolicy Bypass -File update.ps1
```

The updater creates a backup before replacing application files, downloads the new version through Git or an archive, rebuilds the image and restarts services. Existing `.env`, `data/` and `backups/` are preserved during archive updates. A Git installation updates with `git pull --ff-only`; local code changes or divergent history may prevent that update. Resolve the Git issue before trying again.

The studio calendar is part of the database backup. Updates and restores preserve
its saved timezone; changing `STUDIO_TIMEZONE` in `.env` does not move existing
deadlines or regroup spending history. Databases created before calendar selection
retain their legacy UTC+3 spending and deadline rules. Their existing display zones
also remain unchanged. New installations use the IANA timezone selected during setup
for both calculations and display, including daylight saving time.

Use `--dry-run` to describe the update without changing files or restarting services:

```bash
./update.sh --dry-run
```

```powershell
powershell -ExecutionPolicy Bypass -File update.ps1 --dry-run
```

Do not rerun the installer to update: it refuses to overwrite an existing `.env`.

## Backups

On macOS or Linux:

```bash
./backup.sh
```

On Windows:

```powershell
powershell -ExecutionPolicy Bypass -File backup.ps1
```

A backup is a timestamped `backups/noname-YYYYMMDDTHHMMSSZ.tar.gz` archive containing `data/` and `.env`. The script stops the application services that are currently running and write data, then restarts those services when the backup operation ends. Redis is not included in the archive, so queued tasks are not a substitute for saved application data.

The shell script protects the archive with permissions `600` and the backup directory with `700`. The PowerShell script restricts archive access to the current Windows account. These archives contain API keys and other credentials from `.env`; handle them as private data. Keep another copy on a separate device or storage service. The backup does not copy an external NAS mount or other files outside `data/`.

Both backup scripts accept `--dry-run`. Check their exit status and the printed archive path before relying on a backup.

## Recovery

Restore the database, recordings and configuration together from the same backup. Preserve `SECRET_KEY` and `KEYS_ENCRYPTION_KEY`; changing the encryption configuration can make previously saved provider keys unreadable.

For a shell installation, inspect the selected archive before recovery:

```bash
tar -tzf backups/noname-YYYYMMDDTHHMMSSZ.tar.gz
```

Replace that example filename with your actual backup. Stop the services before replacing their data:

```bash
docker compose stop
```

Move the current `data/` directory and `.env` to a separate recovery directory, rather than extracting over existing files. Extract the backup into the installation directory, verify that it contains `data/` and `.env`, then restart:

```bash
tar -xzf backups/noname-YYYYMMDDTHHMMSSZ.tar.gz
docker compose up -d
```

On Windows, stop Docker Compose services, move the existing `data` and `.env` aside, and extract the same archive with `tar -xzf`. Restore appropriate access permissions for the Windows account running the installation. Confirm the login page, books and recordings after startup. Database migrations run when the web service starts; a backup does not contain the application source or container image.

## Service controls and logs

Stop all services:

```bash
docker compose stop
```

Start them again:

```bash
docker compose up -d
```

Inspect service state and recent logs:

```bash
docker compose ps
docker compose logs --tail 100 web worker worker-consilium scheduler
```

Check the local health endpoint with the port configured by `APP_PORT` (default `8080`):

```bash
curl -fsS http://localhost:8080/health
```

For a server, also open its public HTTPS login page. A successful local health response does not verify DNS, HTTPS or access from another device.

## Configuration changes

The installer creates `.env`; [.env.example](.env.example) explains common settings. After editing `.env`, apply it with:

```bash
docker compose up -d
```

Keep environment key names unchanged. Provider keys can also be managed through **Settings → AI providers**, with a built-in check. Recordings use `AUDIO_STORAGE_PATH`; an optional mirror uses `AUDIO_NAS_PATH`. The mirror directory must already exist, and neither storage root may be nested inside the other. Include external storage in your own backup process.

The strict narrator audit is optional, paid and disabled by default. Set `ASR_AUDIT_ENABLED=1` only when you want it and have configured its provider key. Its price depends on duration and current provider tariffs. Retrying an audit may incur another charge.

## Troubleshooting

### Docker did not start

On macOS and Windows, open Docker Desktop and wait for its engine to be running before retrying. On Linux, check the Docker service and whether your account can run Docker or use `sudo`.

### The installer refuses to overwrite an installation

An existing `.env` is preserved deliberately. Use the update script. If the download script finds an unrelated directory at the target location, move it elsewhere before retrying; it does not replace those files.

### The public website is unreachable

Check the domain's DNS A record, the server's inbound ports 80 and 443, and Caddy's logs. Once DNS points to the correct server, restart Caddy if needed:

```bash
docker compose logs --tail 100 caddy
docker compose restart caddy
```

### Book processing will not start

Check the selected model, its provider key and balance in Settings. Read the book log and worker logs. Review an error before starting the task again; do not repeatedly restart workers while a paid task is running.

### A saved provider key cannot be read

Restore the matching encryption settings from your backup, or enter the provider key again in Settings. Do not publish `.env` or send it with a diagnostic report.

### The administrator password is lost

Generate a replacement hash interactively:

```bash
docker compose run --rm web python scripts/generate_password_hash.py
```

Put the returned value in `ADMIN_PASSWORD_HASH` in `.env`, retaining its literal dollar signs and appropriate quoting, then run `docker compose up -d`.

### Telegram is silent

Verify that the bot has a public HTTPS server and that the token, username, webhook secret and notification settings are configured. Inspect the webhook with `docker compose exec web python -m scripts.telegram_set_webhook --info`.
