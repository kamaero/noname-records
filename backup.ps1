$ErrorActionPreference = 'Stop'
$OutputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Set-Location (Split-Path -Parent $MyInvocation.MyCommand.Path)
$DryRun = $args.Count -eq 1 -and $args[0] -in @('--dry-run', '-DryRun')
if ($args.Count -gt 0 -and -not $DryRun) { throw 'Usage: backup.ps1 [--dry-run]' }
if ($DryRun) {
    Write-Host 'Stop processes that write data, save data and .env in a protected archive, then restart those processes.'
    exit 0
}
if (-not (Test-Path data) -or -not (Test-Path .env)) { throw 'data or .env is missing. Run the installer first.' }
& docker info | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Docker is unavailable; no backup was created.' }
New-Item -ItemType Directory -Force -Path backups | Out-Null
$archive = "backups/noname-$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ')).tar.gz"
$active = @(& docker compose ps --status running --services | Where-Object { $_ -in @('web','worker','worker-consilium','scheduler') })
if ($active.Count -gt 0) {
    & docker compose stop @active
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop processes that write data.' }
}
try {
    & tar -czf $archive data .env
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the archive.' }
    $principal = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls $archive /inheritance:r /grant:r "${principal}:(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not restrict access to the archive.' }
    Write-Host "Backup: $archive"
} finally {
    if ($active.Count -gt 0) { & docker compose start @active | Out-Null }
}
