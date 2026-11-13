[CmdletBinding()]
param([string]$OutputDirectory, [switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $repo 'release' }
$outputRoot = [IO.Path]::GetFullPath($OutputDirectory)
$packageRoot = Join-Path $outputRoot ('ChatFoundry-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 6))
if (-not $SkipBuild) {
    Push-Location (Join-Path $repo 'frontend')
    try {
        if (-not (Test-Path -LiteralPath 'node_modules')) {
            & npm.cmd ci
            if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
        }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed' }
    } finally { Pop-Location }
}
if (-not (Test-Path -LiteralPath (Join-Path $repo 'frontend\dist\index.html'))) { throw 'Missing frontend/dist/index.html; build first.' }
New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null
# Copy release files only; exclude development data, secrets, environments, and caches.
$backendTarget = Join-Path $packageRoot 'backend'
Write-Verbose "Backend source: $repo; destination: $backendTarget"
$copyMessages = & robocopy (Join-Path $repo 'backend') $backendTarget /E /R:1 /W:1 /XD data __pycache__ .venv venv .pytest_cache tests /XF .env '.env.*' '*.pyc' '*.key' '*.db' '*.db-*' '*.log' /NFL /NDL /NJH /NJS /NP
if ($LASTEXITCODE -ge 8) { Write-Host ($copyMessages -join [Environment]::NewLine); throw 'Backend copy failed' }
New-Item -ItemType Directory -Path (Join-Path $packageRoot 'frontend') -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $repo 'frontend\dist') -Destination (Join-Path $packageRoot 'frontend\dist') -Recurse
$deployTarget = Join-Path $packageRoot 'deploy\windows'
New-Item -ItemType Directory -Path $deployTarget -Force | Out-Null
Get-ChildItem -LiteralPath $PSScriptRoot -File | Where-Object { $_.Extension -in '.ps1', '.py', '.md' -or $_.Name -eq 'server.env.example' } | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $deployTarget
}
$archive = $packageRoot + '.zip'
Compress-Archive -Path (Join-Path $packageRoot '*') -DestinationPath $archive
Write-Host "Package: $archive"
Write-Host 'No accounts, uploads, API keys, Python environment, or external tools are included.'
