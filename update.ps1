$ErrorActionPreference = 'Stop'
$OutputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$ScriptPath = $MyInvocation.MyCommand.Path
$InstallDirectory = Split-Path -Parent $ScriptPath
Set-Location $InstallDirectory

$DryRun = $args.Count -eq 1 -and $args[0] -in @('--dry-run', '-DryRun')
if ($args.Count -gt 0 -and -not $DryRun) { throw 'Использование: update.ps1 [--dry-run]' }
if ($DryRun) {
    Write-Host 'Создать резервную копию, получить новую версию через Git или архив, пересобрать образ и перезапустить сервисы.'
    exit 0
}

$RepositoryUrl = if ($env:NONAME_REPO) { $env:NONAME_REPO } else { 'https://github.com/kamaero/noname-records.git' }
$ArchiveUrl = if ($env:NONAME_ARCHIVE_URL) {
    $env:NONAME_ARCHIVE_URL
} else {
    ($RepositoryUrl.TrimEnd('/') -replace '\.git$', '') + '/archive/refs/heads/main.zip'
}
$ForceArchive = $env:NONAME_FORCE_ARCHIVE -match '^(1|true|yes|on)$'

function Copy-OrDownloadFile {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination
    )

    if (Test-Path -LiteralPath $Source -PathType Leaf) {
        Copy-Item -LiteralPath $Source -Destination $Destination
        return
    }

    $uri = $null
    if ([Uri]::TryCreate($Source, [UriKind]::Absolute, [ref]$uri) -and $uri.IsFile) {
        Copy-Item -LiteralPath $uri.LocalPath -Destination $Destination
        return
    }

    Invoke-WebRequest -Uri $Source -OutFile $Destination -UseBasicParsing
}

function Copy-ArchiveTree {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Destination,
        [switch]$Root
    )

    if (-not (Test-Path -LiteralPath $Destination -PathType Container)) {
        New-Item -ItemType Directory -Path $Destination | Out-Null
    }
    foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
        if ($Root -and $item.Name -in @('.env','data','backups','.git','update.sh','update.ps1')) { continue }
        $target = Join-Path $Destination $item.Name
        if ($item.PSIsContainer) {
            Copy-ArchiveTree -Source $item.FullName -Destination $target
        } else {
            Copy-Item -LiteralPath $item.FullName -Destination $target -Force
        }
    }
}

$git = Get-Command git -ErrorAction SilentlyContinue
$UseGit = $false
if ($git -and -not $ForceArchive -and (Test-Path -LiteralPath (Join-Path $InstallDirectory '.git'))) {
    try {
        & $git.Source rev-parse --is-inside-work-tree 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $remotes = @(& $git.Source remote)
            $UseGit = $LASTEXITCODE -eq 0 -and $remotes.Count -gt 0
        }
    } catch {
        $UseGit = $false
    }
}

$BackupScript = Join-Path $InstallDirectory 'backup.ps1'
if (-not (Test-Path -LiteralPath $BackupScript -PathType Leaf)) { throw 'Не найден backup.ps1; обновление остановлено.' }
function Invoke-Backup {
    try {
        & $BackupScript
    } catch {
        throw "Не удалось создать резервную копию: $($_.Exception.Message)"
    }
}

if ($UseGit) {
    Invoke-Backup
    & $git.Source pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось получить новую версию.' }
    & docker compose up -d --build
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось перезапустить сервисы.' }
    Write-Host 'Обновление выполнено.'
    exit 0
}

$ParentDirectory = Split-Path -Parent $InstallDirectory
$StagePath = Join-Path $ParentDirectory ('.noname-records-update-' + [Guid]::NewGuid().ToString('N'))
$ArchivePath = Join-Path $StagePath 'noname-records.zip'
$ExtractPath = Join-Path $StagePath 'archive'
$UpdaterCandidate = Join-Path $InstallDirectory ('.update.ps1.' + [Guid]::NewGuid().ToString('N') + '.new')
$ShellUpdaterPath = Join-Path $InstallDirectory 'update.sh'
$ShellUpdaterCandidate = Join-Path $InstallDirectory ('.update.sh.' + [Guid]::NewGuid().ToString('N') + '.new')

try {
    New-Item -ItemType Directory -Path $StagePath | Out-Null
    Write-Host 'Скачиваю свежий архив Noname Records...'
    Copy-OrDownloadFile -Source $ArchiveUrl -Destination $ArchivePath
    New-Item -ItemType Directory -Path $ExtractPath | Out-Null
    Expand-Archive -LiteralPath $ArchivePath -DestinationPath $ExtractPath

    $payloadCandidates = @(Get-ChildItem -LiteralPath $ExtractPath -Directory -Force | Where-Object {
        (Test-Path -LiteralPath (Join-Path $_.FullName 'install.ps1') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $_.FullName 'update.ps1') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $_.FullName 'update.sh') -PathType Leaf) -and
        (Test-Path -LiteralPath (Join-Path $_.FullName 'docker-compose.yml') -PathType Leaf)
    })
    if ($payloadCandidates.Count -ne 1) { throw 'Архив имеет неожиданный формат или повреждён.' }
    $PayloadPath = $payloadCandidates[0].FullName

    Invoke-Backup
    Copy-ArchiveTree -Source $PayloadPath -Destination $InstallDirectory -Root
    & docker compose up -d --build
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось перезапустить сервисы.' }
    Copy-Item -LiteralPath (Join-Path $PayloadPath 'update.sh') -Destination $ShellUpdaterCandidate
    Copy-Item -LiteralPath (Join-Path $PayloadPath 'update.ps1') -Destination $UpdaterCandidate
} catch {
    if (Test-Path -LiteralPath $ShellUpdaterCandidate) { Remove-Item -LiteralPath $ShellUpdaterCandidate -Force }
    if (Test-Path -LiteralPath $UpdaterCandidate) { Remove-Item -LiteralPath $UpdaterCandidate -Force }
    if (Test-Path -LiteralPath $StagePath) { Remove-Item -LiteralPath $StagePath -Recurse -Force }
    throw
}

Remove-Item -LiteralPath $StagePath -Recurse -Force
if (Test-Path -LiteralPath $ShellUpdaterPath -PathType Leaf) {
    [IO.File]::Replace($ShellUpdaterCandidate, $ShellUpdaterPath, $null)
} else {
    Move-Item -LiteralPath $ShellUpdaterCandidate -Destination $ShellUpdaterPath
}
if (Test-Path -LiteralPath $ScriptPath -PathType Leaf) {
    [IO.File]::Replace($UpdaterCandidate, $ScriptPath, $null)
} else {
    Move-Item -LiteralPath $UpdaterCandidate -Destination $ScriptPath
}
Write-Host 'Обновление выполнено.'
