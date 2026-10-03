$ErrorActionPreference = 'Stop'
$OutputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Yes = $false
$DryRun = $false
$Domain = $env:DOMAIN
for ($i = 0; $i -lt $args.Count; $i++) {
    switch ($args[$i]) {
        { $_ -in @('--yes', '-Yes') } { $Yes = $true; break }
        { $_ -in @('--dry-run', '-DryRun') } { $DryRun = $true; break }
        { $_ -in @('--domain', '-Domain') } {
            $i++
            if ($i -ge $args.Count) { throw 'Укажите домен после --domain.' }
            $Domain = $args[$i]
            break
        }
        default { throw "Неизвестный параметр: $($args[$i])" }
    }
}

if ($DryRun) {
    Write-Host 'Проверка Docker Desktop и сборка приложения.'
    Write-Host 'Вычисление хеша пароля в контейнере, создание защищённого .env.'
    Write-Host 'Запуск сервисов, проверка /health и вывод адреса входа.'
    Write-Host 'В интерактивном локальном режиме — открытие сайта в браузере.'
    exit 0
}
if (Test-Path .env) { throw 'Система уже установлена: файл .env сохранён. Для новой версии запустите update.ps1, для копии данных — backup.ps1.' }

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
    Write-Host 'Установите и запустите Docker Desktop: https://www.docker.com/products/docker-desktop/'
    Start-Process 'https://www.docker.com/products/docker-desktop/'
    for ($i = 0; $i -lt 120; $i++) {
        if (Test-Docker) { break }
        Start-Sleep -Seconds 5
    }
    if (-not (Test-Docker)) { throw 'Docker не запустился. Запустите Docker Desktop и повторите установку.' }
}

if ($Yes) {
    $studio = if ($env:STUDIO_NAME) { $env:STUDIO_NAME } else { 'Noname Records' }
    $login = if ($env:ADMIN_LOGIN) { $env:ADMIN_LOGIN } else { 'admin' }
    $password = $env:ADMIN_PASSWORD
    if (-not $password) { throw 'Для -Yes задайте ADMIN_PASSWORD.' }
} else {
    $studio = Read-Host 'Название студии (по умолчанию Noname Records)'
    if (-not $studio) { $studio = 'Noname Records' }
    $login = Read-Host 'Логин администратора (по умолчанию admin)'
    if (-not $login) { $login = 'admin' }
    $secure = Read-Host 'Пароль администратора' -AsSecureString
    $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
    if (-not $password) { throw 'Пароль не может быть пустым.' }
    if (-not $Domain) {
        $mode = Read-Host 'Режим: компьютер или сервер (по умолчанию компьютер)'
        if ($mode -eq 'сервер') { $Domain = Read-Host 'Домен (например, studio.example.com)' }
    }
    $env:DEEPSEEK_API_KEY = Read-Host 'Ключ DeepSeek — нужен для разметки книги (можно пропустить и добавить позже)'
    $env:OPENAI_API_KEY = Read-Host 'Ключ OpenAI — для сверки записей с текстом (можно пропустить)'
    $env:ROUTERAI_API_KEY = Read-Host 'Ключ RouterAI — для сверки записей, консилиума и звуковой разметки (можно пропустить)'
    $env:CLAUDE_API_KEY = Read-Host 'Ключ Claude — для разметки вместо DeepSeek (можно пропустить)'
    $env:ELEVENLABS_API_KEY = Read-Host 'Ключ ElevenLabs — для фоновых звуков (можно пропустить)'
}

if ($Domain -and $Domain -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}$') { throw 'Неверный домен.' }
$ApiKeys = @($env:DEEPSEEK_API_KEY,$env:OPENAI_API_KEY,$env:ROUTERAI_API_KEY,$env:CLAUDE_API_KEY,$env:ELEVENLABS_API_KEY)
foreach ($value in @($studio,$login,$password,$env:STUDIO_CONTACT_NAME) + $ApiKeys) {
    if ($value -match "[`r`n]") { throw 'Значения не должны содержать перевод строки.' }
}
foreach ($value in @($studio,$login,$env:STUDIO_CONTACT_NAME) + $ApiKeys) {
    if ($value -match '[$#]') { throw 'В настройках нельзя использовать символы $ или #.' }
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

Write-Host 'Собираю приложение. Первый запуск может занять несколько минут.'
& docker compose build web
if ($LASTEXITCODE -ne 0) { throw 'Не удалось собрать образ.' }
$hashOutput = $password | & docker compose run --no-deps --rm -T web python scripts/generate_password_hash.py | Select-Object -Last 1
$hashExit = $LASTEXITCODE
$password = $null
$env:ADMIN_PASSWORD = $null
$hash = [string]$hashOutput
$hash = $hash.Trim()
if ($hashExit -ne 0 -or -not $hash.StartsWith('$pbkdf2-sha256$')) { throw 'Не удалось вычислить хеш пароля.' }

$bytes = New-Object byte[] 48
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
$secret = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
New-Item -ItemType Directory -Force -Path data/recordings | Out-Null
$envContent = @"
APP_NAME=Noname Records
STUDIO_NAME=$studio
STUDIO_CONTACT_NAME=$($env:STUDIO_CONTACT_NAME)
APP_BASE_URL=$baseUrl
PRODUCTION=1
SECRET_KEY=$secret
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
if ($LASTEXITCODE -ne 0) { throw 'Не удалось ограничить доступ к .env.' }

& docker compose up -d --build
if ($LASTEXITCODE -ne 0) { throw 'Не удалось запустить сервисы. Проверьте docker compose logs.' }
for ($i = 0; $i -lt 90; $i++) {
    try {
        $result = Invoke-WebRequest "http://localhost:$port/health" -TimeoutSec 3 -UseBasicParsing
        if ($result.StatusCode -eq 200) {
            Write-Host "Система готова. Вход: $baseUrl/app/login"
            if (-not $Yes -and -not $Domain) {
                try { Start-Process -FilePath $baseUrl | Out-Null } catch {}
            }
            exit 0
        }
    } catch {}
    Start-Sleep -Seconds 2
}
throw 'Сайт не ответил за 3 минуты. Проверьте docker compose logs web.'
