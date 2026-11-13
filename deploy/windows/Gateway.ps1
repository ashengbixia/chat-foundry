# Pure gateway configuration helpers; also used by Configure.ps1 -Preview.
function Resolve-GatewaySettings {
    param([string]$RuntimeDirectory, [System.Collections.IDictionary]$Overrides)
    $values = @{ version = 1; site_host = 'localhost'; listen_address = '127.0.0.1'; tls_mode = 'Internal'; acme_email = '' }
    $settingsPath = Join-Path $RuntimeDirectory 'gateway.json'
    $caddyPath = Join-Path $RuntimeDirectory 'Caddyfile'
    if (Test-Path -LiteralPath $settingsPath) {
        try { $saved = [IO.File]::ReadAllText($settingsPath) | ConvertFrom-Json }
        catch { throw 'Cannot read gateway.json. Preserve the file and correct its JSON before configuring.' }
        if ($saved.version -ne 1) { throw 'Unsupported gateway.json version.' }
        foreach ($key in @('site_host', 'listen_address', 'tls_mode', 'acme_email')) {
            if ($null -eq $saved.$key -or $saved.$key -isnot [string]) { throw "Invalid gateway.json field: $key" }
            $values[$key] = $saved.$key
        }
    } elseif (Test-Path -LiteralPath $caddyPath) {
        # Upgrade the Caddyfile emitted by earlier versions without resetting the endpoint.
        $legacy = [IO.File]::ReadAllText($caddyPath)
        $hostMatch = [regex]::Matches($legacy, '(?m)^https://([A-Za-z0-9.-]+)\s*\{\s*$')
        $bindMatch = [regex]::Matches($legacy, '(?m)^\s*bind\s+(127\.0\.0\.1|0\.0\.0\.0)\s*$')
        if ($hostMatch.Count -eq 1 -and $bindMatch.Count -eq 1) {
            $values.site_host = $hostMatch[0].Groups[1].Value
            $values.listen_address = $bindMatch[0].Groups[1].Value
            if ($legacy -notmatch '(?m)^\s*tls\s+internal\s*$' -and -not ($Overrides.ContainsKey('SiteHost') -and $Overrides.ContainsKey('ListenAddress') -and $Overrides.ContainsKey('TlsMode'))) {
                throw 'Custom Caddyfile detected. Back it up, then explicitly supply SiteHost, ListenAddress and TlsMode after reviewing the configuration.'
            }
        } elseif (-not ($Overrides.ContainsKey('SiteHost') -and $Overrides.ContainsKey('ListenAddress') -and $Overrides.ContainsKey('TlsMode'))) {
            throw 'Cannot infer existing Caddyfile settings. Supply SiteHost, ListenAddress and TlsMode explicitly; back up custom directives first.'
        }
    }
    $previousMode = $values.tls_mode
    foreach ($mapping in @(@('SiteHost', 'site_host'), @('ListenAddress', 'listen_address'), @('TlsMode', 'tls_mode'), @('AcmeEmail', 'acme_email'))) {
        if ($Overrides.ContainsKey($mapping[0])) { $values[$mapping[1]] = [string]$Overrides[$mapping[0]] }
    }
    $values.site_host = $values.site_host.Trim().TrimEnd('.').ToLowerInvariant()
    $domainPattern = '^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$'
    if ($values.site_host -notmatch $domainPattern) { throw 'SiteHost must be a hostname or IPv4 address, without a scheme, port, path, wildcard or whitespace.' }
    if ($values.listen_address -notin @('127.0.0.1', '0.0.0.0')) { throw 'ListenAddress must be 127.0.0.1 or 0.0.0.0.' }
    if ($values.tls_mode -notin @('Internal', 'Public')) { throw 'TlsMode must be Internal or Public.' }
    if ($values.acme_email -and $values.acme_email -notmatch '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$') { throw 'AcmeEmail must be an ASCII email address, or an empty string.' }
    if ($values.tls_mode -eq 'Public') {
        $ip = $null
        if ([Net.IPAddress]::TryParse($values.site_host, [ref]$ip) -or $values.site_host -notmatch '\.[A-Za-z][A-Za-z0-9-]*$' -or $values.site_host -match '(^|\.)(localhost|local|internal|invalid|test|example)$') {
            throw 'Public TLS requires a real DNS domain. Use Internal for localhost and private IP testing.'
        }
        if ($previousMode -ne 'Public' -and -not $Overrides.ContainsKey('ListenAddress')) { $values.listen_address = '0.0.0.0' }
    }
    return [pscustomobject]$values
}

function Get-GatewayCaddyfile {
    param($Settings, [string]$WebRoot)
    $globals = '    admin off'
    $tls = ''
    if ($Settings.tls_mode -eq 'Internal') {
        $globals += "`n    skip_install_trust"
        $tls = '    tls internal'
    } elseif ($Settings.acme_email) { $globals += "`n    email $($Settings.acme_email)" }
    $root = $WebRoot.Replace('\', '/')
    return @"
{
$globals
}
https://$($Settings.site_host) {
    bind $($Settings.listen_address)
$tls
    header {
        X-Content-Type-Options nosniff
        Referrer-Policy same-origin
        X-Frame-Options DENY
        -Server
    }
    request_body {
        max_size 8MB
    }
    handle /api/* {
        reverse_proxy 127.0.0.1:8000 {
            flush_interval -1
            header_up X-Forwarded-For {remote_host}
            header_up X-Forwarded-Proto {scheme}
        }
    }
    handle /health {
        reverse_proxy 127.0.0.1:8000
    }
    handle {
        root * "$root"
        try_files {path} /index.html
        file_server
    }
}
"@
}
