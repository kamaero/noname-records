$ErrorActionPreference = 'Stop'
$OutputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Yes = $false
$DryRun = $false
$Domain = $env:DOMAIN
$StudioTimezone = if ($env:STUDIO_TIMEZONE) { $env:STUDIO_TIMEZONE } else { 'UTC' }
for ($i = 0; $i -lt $args.Count; $i++) {
    switch ($args[$i]) {
        { $_ -in @('--yes', '-Yes') } { $Yes = $true; break }
        { $_ -in @('--dry-run', '-DryRun') } { $DryRun = $true; break }
        { $_ -in @('--domain', '-Domain') } {
            $i++
            if ($i -ge $args.Count) { throw 'Specify a domain after --domain.' }
            $Domain = $args[$i]
            break
        }
        { $_ -in @('--timezone', '-Timezone') } {
            $i++
            if ($i -ge $args.Count) { throw 'Specify an IANA timezone after --timezone.' }
            $StudioTimezone = $args[$i]
            break
        }
        default { throw "Unknown option: $($args[$i])" }
    }
}

if ($DryRun) {
    Write-Host 'Check Docker Desktop and build the application.'
    Write-Host 'Hash the password inside the container and create a protected .env file.'
    Write-Host 'Start services, check /health and display the sign-in address.'
    Write-Host 'Open the website in a browser for an interactive local installation.'
    exit 0
}
if (Test-Path .env) { throw 'Noname Records is already installed; .env has been preserved. Run update.ps1 to update or backup.ps1 to back up your data.' }

function Test-Docker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { return $false }
    try {
        & docker compose version 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { return $false }
        & docker info 2>$null | Out-Null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}
if (-not (Test-Docker)) {
    Write-Host 'Install and start Docker Desktop: https://www.docker.com/products/docker-desktop/'
    Start-Process 'https://www.docker.com/products/docker-desktop/'
    for ($i = 0; $i -lt 120; $i++) {
        if (Test-Docker) { break }
        Start-Sleep -Seconds 5
    }
    if (-not (Test-Docker)) { throw 'Docker did not start. Start Docker Desktop and run the installer again.' }
}

if ($Yes) {
    $studio = if ($env:STUDIO_NAME) { $env:STUDIO_NAME } else { 'Noname Records' }
    $login = if ($env:ADMIN_LOGIN) { $env:ADMIN_LOGIN } else { 'admin' }
    $password = $env:ADMIN_PASSWORD
    if (-not $password) { throw 'Set ADMIN_PASSWORD when using -Yes.' }
} else {
    $studio = Read-Host 'Studio name (default: Noname Records)'
    if (-not $studio) { $studio = 'Noname Records' }
    $ChosenTimezone = Read-Host "Studio IANA timezone, for example America/New_York (default: $StudioTimezone)"
    if ($ChosenTimezone) { $StudioTimezone = $ChosenTimezone }
    $login = Read-Host 'Administrator login (default: admin)'
    if (-not $login) { $login = 'admin' }
    $secure = Read-Host 'Administrator password' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
    if (-not $password) { throw 'The password cannot be empty.' }
    if (-not $Domain) {
        $mode = Read-Host 'Mode: computer or server (default: computer)'
        if ($mode -in @('server', 'сервер')) { $Domain = Read-Host 'Domain (for example, studio.example.com)' }
    }
    $env:DEEPSEEK_API_KEY = Read-Host 'DeepSeek key for book attribution (optional; you can add it later)'
    $env:OPENAI_API_KEY = Read-Host 'OpenAI key for aligning recordings with the text (optional)'
    $env:ROUTERAI_API_KEY = Read-Host 'RouterAI key for recording alignment, review and sound planning (optional)'
    $env:CLAUDE_API_KEY = Read-Host 'Claude key for attribution instead of DeepSeek (optional)'
    $env:ELEVENLABS_API_KEY = Read-Host 'ElevenLabs key for ambient sound (optional)'
}

if ($StudioTimezone -notmatch '^[A-Za-z0-9._+-]+(/[A-Za-z0-9._+-]+)*$') { throw 'Invalid IANA timezone. Use UTC or a name such as America/New_York.' }
Write-Host "Requested studio timezone: $StudioTimezone (new empty databases only; existing data keeps its legacy calendar)."

if ($Domain -and $Domain -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}$') { throw 'Invalid domain.' }
$ApiKeys = @($env:DEEPSEEK_API_KEY,$env:OPENAI_API_KEY,$env:ROUTERAI_API_KEY,$env:CLAUDE_API_KEY,$env:ELEVENLABS_API_KEY)
foreach ($value in @($studio,$login,$password,$env:STUDIO_CONTACT_NAME) + $ApiKeys) {
    if ($value -match "[`r`n]") { throw 'Values must not contain line breaks.' }
}
foreach ($value in @($studio,$login,$env:STUDIO_CONTACT_NAME) + $ApiKeys) {
    if ($value -match '[$#]') { throw 'Settings must not contain the characters $ or #.' }
}
$asrProvider = if ($env:ROUTERAI_API_KEY -and -not $env:OPENAI_API_KEY) { 'routerai' } else { 'openai' }
$port = if ($env:APP_PORT) { $env:APP_PORT } else { '8080' }
$env:APP_PORT = $port
if ($Domain) {
    $env:DOMAIN = $Domain
    $env:COMPOSE_PROFILES = 'https'
    $composeProfiles = 'https'
    $baseUrl = "https://$Domain"
    $cookieSecure = 'true'
    $reserveGb = 5
} else {
    $env:COMPOSE_PROFILES = ''
    $baseUrl = "http://localhost:$port"
    $cookieSecure = 'false'
    $reserveGb = 2
    $composeProfiles = ''
}

Write-Host 'Building the application. The first run may take several minutes.'
& docker compose build web
if ($LASTEXITCODE -ne 0) { throw 'Could not build the image.' }
& docker compose run --no-deps --rm -T web python scripts/validate_studio_timezone.py $StudioTimezone
if ($LASTEXITCODE -ne 0) { throw 'Invalid studio IANA timezone. Installation stopped before writing .env.' }
$hashOutput = $password | & docker compose run --no-deps --rm -T web python scripts/generate_password_hash.py | Select-Object -Last 1
$hashExit = $LASTEXITCODE
$password = $null
$env:ADMIN_PASSWORD = $null
$hash = [string]$hashOutput
$hash = $hash.Trim()
if ($hashExit -ne 0 -or -not $hash.StartsWith('$pbkdf2-sha256$')) { throw 'Could not hash the password.' }

$bytes = New-Object byte[] 48
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
$secret = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
# Ключ шифрования ключей нейросетей в базе — той же библиотекой, что их потом расшифрует.
$keysEncryptionKey = & docker compose run --no-deps --rm -T web python scripts/generate_encryption_key.py | Select-Object -Last 1
$keysEncryptionKey = "$keysEncryptionKey".Trim()
if ($keysEncryptionKey.Length -ne 44) {
    throw 'Could not create the encryption key. Check that Docker Desktop is running and run the installer again.'
}
New-Item -ItemType Directory -Force -Path data/recordings | Out-Null
$envContent = @"
APP_NAME=Noname Records
STUDIO_NAME=$studio
STUDIO_TIMEZONE=$StudioTimezone
STUDIO_CONTACT_NAME=$($env:STUDIO_CONTACT_NAME)
APP_BASE_URL=$baseUrl
PRODUCTION=1
SECRET_KEY=$secret
KEYS_ENCRYPTION_KEY=$keysEncryptionKey
ADMIN_LOGIN=$login
ADMIN_PASSWORD_HASH='$hash'
DATABASE_URL=sqlite:///./data/noname.db
REDIS_URL=redis://redis:6379/0
AUDIO_STORAGE_PATH=/app/data/recordings
AUDIO_NAS_PATH=
LOCAL_RESERVE_GB=$reserveGb
COOKIE_SECURE=$cookieSecure
DOMAIN=$Domain
COMPOSE_PROFILES=$composeProfiles
APP_PORT=$port
APP_BIND_HOST=127.0.0.1
DEEPSEEK_API_KEY=$($env:DEEPSEEK_API_KEY)
OPENAI_API_KEY=$($env:OPENAI_API_KEY)
ROUTERAI_API_KEY=$($env:ROUTERAI_API_KEY)
ASR_PROVIDER=$asrProvider
CLAUDE_API_KEY=$($env:CLAUDE_API_KEY)
ELEVENLABS_API_KEY=$($env:ELEVENLABS_API_KEY)
TELEGRAM_NOTIFY_ENABLED=false
"@
[IO.File]::WriteAllText((Join-Path (Get-Location) '.env'), $envContent + "`n", [Text.UTF8Encoding]::new($false))
$principal = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls .env /inheritance:r /grant:r "${principal}:(F)" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict access to .env.' }

& docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw 'Could not start services. Check docker compose logs.' }
for ($i = 0; $i -lt 90; $i++) {
    try {
        $result = Invoke-WebRequest "http://localhost:$port/health" -TimeoutSec 3 -UseBasicParsing
        if ($result.StatusCode -eq 200) {
            Write-Host "Noname Records is ready. Sign in: $baseUrl/app/login"
            if (-not $Yes -and -not $Domain) {
                try { Start-Process -FilePath $baseUrl | Out-Null } catch {}
            }
            exit 0
        }
    } catch {}
    Start-Sleep -Seconds 2
}
throw 'The website did not respond within 3 minutes. Check docker compose logs web.'
