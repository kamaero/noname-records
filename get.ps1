$ErrorActionPreference = 'Stop'
$OutputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$InstallArgs = @($args)
$InstallDirectory = Join-Path $HOME 'noname-records'
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

if (Test-Path -LiteralPath $InstallDirectory) {
    if (Test-Path -LiteralPath (Join-Path $InstallDirectory '.env') -PathType Leaf) {
        Write-Host "Noname Records is already installed in $InstallDirectory. To update: Set-Location `"$InstallDirectory`"; .\update.ps1"
        return
    }
    throw "The directory $InstallDirectory already exists, but contains no Noname Records installation. Move its contents elsewhere and run the installer again."
}

$StagePath = Join-Path $HOME ('.noname-records-install-' + [Guid]::NewGuid().ToString('N'))
$CheckoutPath = Join-Path $StagePath 'checkout'
$ArchivePath = Join-Path $StagePath 'noname-records.zip'
$ExtractPath = Join-Path $StagePath 'archive'
$InstallPublished = $false

try {
    New-Item -ItemType Directory -Path $StagePath | Out-Null

    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git -and -not $ForceArchive) {
        Write-Host 'Downloading Noname Records with Git...'
        & $git.Source clone -- $RepositoryUrl $CheckoutPath
        if ($LASTEXITCODE -ne 0) { throw 'Could not download Noname Records with Git.' }
        $PayloadPath = $CheckoutPath
    } else {
        Write-Host 'Downloading the Noname Records archive...'
        Copy-OrDownloadFile -Source $ArchiveUrl -Destination $ArchivePath
        New-Item -ItemType Directory -Path $ExtractPath | Out-Null
        Expand-Archive -LiteralPath $ArchivePath -DestinationPath $ExtractPath

        $payloadCandidates = @(Get-ChildItem -LiteralPath $ExtractPath -Directory -Force | Where-Object {
            (Test-Path -LiteralPath (Join-Path $_.FullName 'install.ps1') -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $_.FullName 'update.ps1') -PathType Leaf) -and
            (Test-Path -LiteralPath (Join-Path $_.FullName 'docker-compose.yml') -PathType Leaf)
        })
        if ($payloadCandidates.Count -ne 1) { throw 'The archive has an unexpected structure or is damaged.' }
        $PayloadPath = $payloadCandidates[0].FullName
    }

    foreach ($requiredFile in @('install.ps1','update.ps1','docker-compose.yml')) {
        if (-not (Test-Path -LiteralPath (Join-Path $PayloadPath $requiredFile) -PathType Leaf)) {
            throw "The download is incomplete: $requiredFile is missing."
        }
    }
    Move-Item -LiteralPath $PayloadPath -Destination $InstallDirectory
    $InstallPublished = $true
} catch {
    if ($InstallPublished -and (Test-Path -LiteralPath $InstallDirectory)) {
        Remove-Item -LiteralPath $InstallDirectory -Recurse -Force
    }
    throw
} finally {
    if (Test-Path -LiteralPath $StagePath) {
        Remove-Item -LiteralPath $StagePath -Recurse -Force
    }
}

$Installer = Join-Path $InstallDirectory 'install.ps1'
if (-not (Test-Path -LiteralPath $Installer -PathType Leaf)) {
    throw 'The download is incomplete: install.ps1 is missing.'
}
& $Installer @InstallArgs
