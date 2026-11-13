[CmdletBinding()]
param(
    [string]$RuntimeDirectory = 'C:\ChatFoundryRuntime',
    [string]$PythonExe,
    [string]$SiteHost = 'localhost',
    [ValidateSet('127.0.0.1', '0.0.0.0')][string]$ListenAddress = '127.0.0.1',
    [ValidateSet('Internal', 'Public')][string]$TlsMode = 'Internal',
    [AllowEmptyString()][string]$AcmeEmail = '',
    [switch]$Preview,
    [switch]$InstallDependencies,
    [switch]$SkipResourceCheck
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if ($RuntimeDirectory -notmatch '^[A-Za-z]:[\\/]') { throw 'RuntimeDirectory must be a local absolute drive path.' }
$runtime = [IO.Path]::GetFullPath($RuntimeDirectory).TrimEnd('\', '/')
$repoPrefix = $repo.TrimEnd('\') + '\'
if ($runtime -eq [IO.Path]::GetPathRoot($runtime).TrimEnd('\') -or $runtime.Equals($repo, [StringComparison]::OrdinalIgnoreCase) -or $runtime.StartsWith($repoPrefix, [StringComparison]::OrdinalIgnoreCase) -or $repo.StartsWith($runtime + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Use a separate runtime directory, outside the source tree and not a drive root.'
}
foreach ($reserved in @($env:windir, $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
    if ($reserved -and ($runtime.Equals($reserved, [StringComparison]::OrdinalIgnoreCase) -or $runtime.StartsWith($reserved + '\', [StringComparison]::OrdinalIgnoreCase))) { throw 'Do not use a system directory for runtime data.' }
}
. (Join-Path $PSScriptRoot 'Gateway.ps1')
$gateway = Resolve-GatewaySettings -RuntimeDirectory $runtime -Overrides $PSBoundParameters
$caddy = Get-GatewayCaddyfile -Settings $gateway -WebRoot (Join-Path $repo 'frontend\dist')
if ($Preview) {
    Write-Host "Gateway: https://$($gateway.site_host); bind=$($gateway.listen_address); TLS=$($gateway.tls_mode)"
    Write-Output $caddy
    return
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run configuration in an elevated PowerShell to protect runtime secrets.' }
foreach ($name in @('ChatFoundryBackend', 'ChatFoundryGateway')) {
    $service = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne 'Stopped') { throw "Stop $name before reconfiguring." }
}
if (-not $SkipResourceCheck) {
    $os = Get-CimInstance Win32_OperatingSystem
    $freeMemoryMB = [math]::Round($os.FreePhysicalMemory / 1024)
    $drive = Get-CimInstance Win32_LogicalDisk -Filter ("DeviceID='" + [IO.Path]::GetPathRoot($runtime).Substring(0, 2) + "'")
    Write-Host "Available memory: $freeMemoryMB MB; free runtime disk: $([math]::Round($drive.FreeSpace / 1GB, 1)) GB"
    if ($freeMemoryMB -lt 768) { throw 'Less than 768 MB available memory. Free memory first; this minimum check does not guarantee RAG capacity.' }
    if ($drive.FreeSpace -lt 6GB) { throw 'Less than 6 GB free disk. Free disk space before installing dependencies.' }
}
if (-not (Test-Path -LiteralPath (Join-Path $repo 'frontend\dist\index.html'))) { throw 'Missing built frontend. Use Build-Package.ps1 on the development PC first.' }
$utf8 = New-Object System.Text.UTF8Encoding($false)
foreach ($relative in @('', 'data', 'cache\huggingface', 'logs', 'services', 'bin', 'caddy\data', 'caddy\config')) {
    New-Item -ItemType Directory -Path (Join-Path $runtime $relative) -Force | Out-Null
}
& icacls.exe $runtime /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' '*S-1-5-19:(OI)(CI)RX' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Unable to protect the runtime directory.' }
$envPath = Join-Path $runtime 'server.env'
if (-not (Test-Path -LiteralPath $envPath)) {
    $text = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'server.env.example'))
    $text = $text.Replace('C:/ChatFoundryRuntime', $runtime.Replace('\', '/'))
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    $token = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant()
    $text = $text.Replace('BOOTSTRAP_TOKEN=', ('BOOTSTRAP_TOKEN=' + $token))
    [IO.File]::WriteAllText($envPath, $text, $utf8)
    Write-Host "Created private configuration: $envPath (bootstrap token is not printed)."
} else { Write-Host 'Existing server.env preserved.' }
$venvPython = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    if ($PythonExe) { & $PythonExe -m venv (Join-Path $repo '.venv') }
    else { & py.exe -3.10 -m venv (Join-Path $repo '.venv') }
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.10 virtual environment creation failed.' }
}
if ($InstallDependencies) {
    & $venvPython -m pip install --no-cache-dir --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'pip upgrade failed.' }
    # CPU wheel prevents accidentally downloading a CUDA runtime on this server.
    & $venvPython -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.6.0
    if ($LASTEXITCODE -ne 0) { throw 'CPU PyTorch installation failed.' }
    & $venvPython -m pip install --no-cache-dir -r (Join-Path $repo 'backend\requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
    & $venvPython -c "import torch; assert torch.version.cuda is None, 'CPU build required'; print('CPU PyTorch ready: ' + torch.__version__)"
    if ($LASTEXITCODE -ne 0) { throw 'CPU PyTorch import failed. Check available memory and the Microsoft Visual C++ x64 runtime.' }
}
& $venvPython (Join-Path $PSScriptRoot 'runtime.py') --env-file $envPath check
if ($LASTEXITCODE -ne 0) { throw 'Configuration or dependencies are not ready. Install dependencies before continuing.' }

# Validate before replacing the previous gateway configuration.
$caddyPath = Join-Path $runtime 'Caddyfile'
$caddyExe = Join-Path $runtime 'bin\caddy.exe'
$candidate = Join-Path $runtime ('Caddyfile-' + [Guid]::NewGuid().ToString('N') + '.tmp')
[IO.File]::WriteAllText($candidate, $caddy, $utf8)
try {
    if (Test-Path -LiteralPath $caddyExe) {
        $previousData = $env:XDG_DATA_HOME
        $previousConfig = $env:XDG_CONFIG_HOME
        try {
            $env:XDG_DATA_HOME = Join-Path $runtime 'caddy\data'
            $env:XDG_CONFIG_HOME = Join-Path $runtime 'caddy\config'
            & $caddyExe validate --config $candidate --adapter caddyfile
            if ($LASTEXITCODE -ne 0) { throw 'Caddy validation failed; previous gateway configuration was preserved.' }
        } finally {
            $env:XDG_DATA_HOME = $previousData
            $env:XDG_CONFIG_HOME = $previousConfig
        }
    } else { Write-Host 'Caddy is not installed yet. Configuration will be validated during service installation.' }
    if (Test-Path -LiteralPath $caddyPath) {
        Copy-Item -LiteralPath $caddyPath -Destination ($caddyPath + '.before-configure') -Force
    }
    [IO.File]::WriteAllText($caddyPath, $caddy, $utf8)
    [IO.File]::WriteAllText((Join-Path $runtime 'gateway.json'), ($gateway | ConvertTo-Json), $utf8)
} finally { Remove-Item -LiteralPath $candidate -Force -ErrorAction SilentlyContinue }
function Escape-Xml([string]$Value) { return [Security.SecurityElement]::Escape($Value) }
function Write-Service([string]$Id, [string]$Executable, [string]$Arguments, [string]$ExtraEnvironment) {
    $xml = @"
<service>
  <id>$Id</id>
  <name>$Id</name>
  <description>ChatFoundry native Windows deployment</description>
  <executable>$(Escape-Xml $Executable)</executable>
  <arguments>$(Escape-Xml $Arguments)</arguments>
  <workingdirectory>$(Escape-Xml $repo)</workingdirectory>
  <serviceaccount><domain>NT AUTHORITY</domain><user>LocalService</user></serviceaccount>
  <startmode>Automatic</startmode>
  <stoptimeout>45 sec</stoptimeout>
  <onfailure action="restart" delay="10 sec" />
  <env name="PYTHONUTF8" value="1" />
  <env name="PYTHONUNBUFFERED" value="1" />
  <env name="PYTHONDONTWRITEBYTECODE" value="1" />
  $ExtraEnvironment
  <logpath>$(Escape-Xml (Join-Path $runtime 'logs'))</logpath>
  <log mode="roll-by-size"><sizeThreshold>10240</sizeThreshold><keepFiles>3</keepFiles></log>
</service>
"@
    [IO.File]::WriteAllText((Join-Path $runtime "services\$Id.xml"), $xml, $utf8)
}
$runner = Join-Path $PSScriptRoot 'runtime.py'
Write-Service 'ChatFoundryBackend' $venvPython ('"' + $runner + '" --env-file "' + $envPath + '" serve') ''
$extra = '<env name="XDG_DATA_HOME" value="' + (Escape-Xml (Join-Path $runtime 'caddy\data')) + '" /><env name="XDG_CONFIG_HOME" value="' + (Escape-Xml (Join-Path $runtime 'caddy\config')) + '" />'
Write-Service 'ChatFoundryGateway' (Join-Path $runtime 'bin\caddy.exe') ('run --config "' + (Join-Path $runtime 'Caddyfile') + '" --adapter caddyfile') $extra
Write-Host "Configured https://$($gateway.site_host), listening on $($gateway.listen_address), TLS=$($gateway.tls_mode). Settings saved to gateway.json."
if ($gateway.tls_mode -eq 'Public') {
    Write-Host 'Public TLS: configure DNS and a reachable public endpoint; allow TCP 80/443 through cloud and Windows firewalls. Caddy obtains/renews certificates automatically.'
} else { Write-Host 'Internal TLS: trust this deployment local CA on each accessing client.' }
Write-Host 'No firewall rule or Windows service has been installed.'
Write-Host "Next: place caddy.exe and WinSW-x64.exe in $runtime\bin, then run Manage-Services.ps1 -Action Install."
