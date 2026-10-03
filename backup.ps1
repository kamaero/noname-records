$ErrorActionPreference = 'Stop'
$OutputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DryRun = $args.Count -eq 1 -and $args[0] -in @('--dry-run', '-DryRun')
if ($args.Count -gt 0 -and -not $DryRun) { throw 'Использование: backup.ps1 [--dry-run]' }
if ($DryRun) {
    Write-Host 'Остановить процессы записи, сохранить data и .env в защищённый архив, затем запустить процессы.'
    exit 0
}
if (-not (Test-Path data) -or -not (Test-Path .env)) { throw 'Нет data или .env — сначала выполните установку.' }
& docker info | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker недоступен; резервная копия не создана.' }
New-Item -ItemType Directory -Force -Path backups | Out-Null
$archive = "backups/noname-$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')).tar.gz"
$active = @(& docker compose ps --status running --services | Where-Object { $_ -in @('web','worker','worker-consilium','scheduler') })
if ($active.Count -gt 0) {
    & docker compose stop @active
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось остановить процессы записи.' }
}
try {
    & tar -czf $archive data .env
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать архив.' }
    $principal = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls $archive /inheritance:r /grant:r "${principal}:(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось ограничить доступ к архиву.' }
    Write-Host "Резервная копия: $archive"
} finally {
    if ($active.Count -gt 0) { & docker compose start @active | Out-Null }
}
