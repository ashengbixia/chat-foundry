[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('Install', 'Start', 'Stop', 'Restart', 'Status', 'Uninstall')][string]$Action,
    [string]$RuntimeDirectory = 'C:\ChatFoundryRuntime'
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$runtime = (Resolve-Path -LiteralPath $RuntimeDirectory).Path
$names = @('ChatFoundryBackend', 'ChatFoundryGateway')
if ($Action -eq 'Status') { Get-Service -Name $names -ErrorAction SilentlyContinue; return }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this command in an elevated PowerShell.' }
function Run-Wrapper([string]$Name, [string]$Command) {
    $wrapper = Join-Path $runtime "services\$Name.exe"
    & $wrapper $Command
    if ($LASTEXITCODE -ne 0) { throw "$Name $Command failed ($LASTEXITCODE)." }
}
function Stop-App {
    # Stop gateway first to avoid new requests during backend shutdown.
    foreach ($name in @('ChatFoundryGateway', 'ChatFoundryBackend')) {
        $service = Get-Service -Name $name -ErrorAction SilentlyContinue
        if ($service -and $service.Status -ne 'Stopped') {
            Run-Wrapper $name 'stop'
            (Get-Service -Name $name).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(60))
        }
    }
}
function Start-App {
    foreach ($name in $names) {
        if ((Get-Service -Name $name).Status -ne 'Running') { Run-Wrapper $name 'start' }
    }
}
if ($Action -eq 'Install') {
    if ($runtime.Equals($repo, [StringComparison]::OrdinalIgnoreCase) -or $runtime -eq [IO.Path]::GetPathRoot($runtime).TrimEnd('\') -or $repo.StartsWith($runtime.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase) -or $runtime.StartsWith($repo.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Runtime must be separate from the source tree, not a drive root.' }
    foreach ($name in $names) { if (Get-Service -Name $name -ErrorAction SilentlyContinue) { throw "$name already exists. Use Stop/Start for updates, not Install." } }
    foreach ($path in @('bin\caddy.exe', 'bin\WinSW-x64.exe', 'server.env', 'Caddyfile', 'services\ChatFoundryBackend.xml', 'services\ChatFoundryGateway.xml')) {
        if (-not (Test-Path -LiteralPath (Join-Path $runtime $path))) { throw "Missing $path. Run Configure.ps1 and provide official binaries first." }
    }
    # Fixed SIDs work on localized Windows. Private runtime: Administrators/SYSTEM full,
    # LocalService read, and only the mutable data/cache/log/certificate directories writable.
    & icacls.exe $runtime /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' '*S-1-5-19:(OI)(CI)RX' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Runtime ACL setup failed.' }
    foreach ($relative in @('data', 'cache', 'logs', 'caddy')) {
        & icacls.exe (Join-Path $runtime $relative) /grant:r '*S-1-5-19:(OI)(CI)M' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "ACL setup failed: $relative" }
    }
    & icacls.exe $repo /grant '*S-1-5-19:(OI)(CI)RX' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Application read permission failed.' }
    $previousData = $env:XDG_DATA_HOME
    $previousConfig = $env:XDG_CONFIG_HOME
    try {
        $env:XDG_DATA_HOME = Join-Path $runtime 'caddy\data'
        $env:XDG_CONFIG_HOME = Join-Path $runtime 'caddy\config'
        & (Join-Path $runtime 'bin\caddy.exe') validate --config (Join-Path $runtime 'Caddyfile') --adapter caddyfile
        if ($LASTEXITCODE -ne 0) { throw 'Caddy configuration validation failed.' }
    } finally {
        $env:XDG_DATA_HOME = $previousData
        $env:XDG_CONFIG_HOME = $previousConfig
    }
    $installed = @()
    try {
        foreach ($name in $names) {
            Copy-Item -LiteralPath (Join-Path $runtime 'bin\WinSW-x64.exe') -Destination (Join-Path $runtime "services\$name.exe")
            Run-Wrapper $name 'install'
            $installed += $name
        }
    } catch {
        foreach ($name in $installed) { & (Join-Path $runtime "services\$name.exe") uninstall | Out-Null }
        throw
    }
    Write-Host 'Installed under LocalService. Run -Action Start, then trust the local CA as documented.'
} elseif ($Action -eq 'Stop') { Stop-App }
elseif ($Action -eq 'Start') { Start-App }
elseif ($Action -eq 'Restart') { Stop-App; Start-App }
elseif ($Action -eq 'Uninstall') {
    Stop-App
    foreach ($name in @('ChatFoundryGateway', 'ChatFoundryBackend')) {
        if (Get-Service -Name $name -ErrorAction SilentlyContinue) { Run-Wrapper $name 'uninstall' }
    }
    Write-Host 'Services removed. Source, configuration, certificates, and user data were preserved.'
}
