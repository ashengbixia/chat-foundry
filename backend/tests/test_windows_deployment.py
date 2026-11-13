import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / 'deploy' / 'windows' / 'runtime.py'


class NativeDeploymentTests(unittest.TestCase):
    def test_selected_configuration_production_and_lazy_embedding_without_database_creation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / 'server.env'
            data = root / 'separate-data'
            config.write_text('APP_ENV=development\nAUTH_COOKIE_SECURE=true\nEMBEDDING_PRELOAD=false\nDATA_DIR=' + data.as_posix() + '\n', encoding='utf-8')
            script = """
import runpy, sys
from pathlib import Path
module = runpy.run_path(sys.argv[1])
module['load_environment'](Path(sys.argv[2]))
from backend.app.core.config import settings
assert settings.app_env == 'production'
assert settings.auth_cookie_secure is True
assert settings.embedding_preload is False
assert settings.data_dir == Path(sys.argv[3])
"""
            result = subprocess.run([sys.executable, '-c', script, str(RUNNER), str(config), str(data)], cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([sys.executable, str(RUNNER), '--env-file', str(config), 'check'], cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30,
                                    env={**os.environ, 'PYTHONUTF8': '1'})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(data.exists())

    def test_native_deployment_rejects_insecure_cookie(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / 'server.env'
            config.write_text('AUTH_COOKIE_SECURE=false\nDATA_DIR=' + root.as_posix() + '\n', encoding='utf-8')
            result = subprocess.run([sys.executable, str(RUNNER), '--env-file', str(config), 'check'], cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30,
                                    env={**os.environ, 'PYTHONUTF8': '1'})
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('AUTH_COOKIE_SECURE=true', result.stderr)


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell configuration')
class GatewayConfigurationTests(unittest.TestCase):
    def probe(self, directory, overrides):
        root = Path(directory)
        (root / 'overrides.json').write_text(json.dumps(overrides), encoding='utf-8')
        script = root / 'probe.ps1'
        script.write_text('''param([string]$Helper, [string]$Runtime)
$ErrorActionPreference = 'Stop'
. $Helper
$inputValues = [IO.File]::ReadAllText((Join-Path $Runtime 'overrides.json')) | ConvertFrom-Json
$overrides = @{}
foreach ($property in $inputValues.PSObject.Properties) { $overrides[$property.Name] = $property.Value }
$settings = Resolve-GatewaySettings -RuntimeDirectory $Runtime -Overrides $overrides
$config = Get-GatewayCaddyfile -Settings $settings -WebRoot 'C:\\ChatFoundry\\frontend\\dist'
@{ settings = $settings; config = $config } | ConvertTo-Json -Depth 5
''', encoding='ascii')
        executable = str(Path(os.environ['WINDIR']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
        result = subprocess.run([executable, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script),
                                 '-Helper', str(ROOT / 'deploy/windows/Gateway.ps1'), '-Runtime', str(root)],
                                capture_output=True, text=True, timeout=20)
        return result

    def test_public_certificates_and_saved_endpoint_survive_update(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.probe(directory, {'SiteHost': 'chat.example.com', 'TlsMode': 'Public'})
            self.assertEqual(result.returncode, 0, result.stderr)
            public = json.loads(result.stdout)
            self.assertEqual(public['settings']['listen_address'], '0.0.0.0')
            self.assertNotIn('tls internal', public['config'])
            self.assertIn('reverse_proxy 127.0.0.1:8000', public['config'])
            saved = Path(directory) / 'gateway.json'
            saved.write_text(json.dumps(public['settings']), encoding='utf-8')
            again = self.probe(directory, {})
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertEqual(json.loads(again.stdout), public)
            internal = self.probe(directory, {'SiteHost': 'localhost', 'TlsMode': 'Internal', 'ListenAddress': '127.0.0.1'})
            self.assertEqual(internal.returncode, 0, internal.stderr)
            self.assertIn('tls internal', json.loads(internal.stdout)['config'])

    def test_legacy_internal_endpoint_is_preserved_and_preview_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            caddy = root / 'Caddyfile'
            original = 'https://192.0.2.10 {\n    bind 0.0.0.0\n    tls internal\n}\n'
            caddy.write_text(original, encoding='ascii')
            result = self.probe(directory, {})
            self.assertEqual(result.returncode, 0, result.stderr)
            settings = json.loads(result.stdout)['settings']
            self.assertEqual(settings['site_host'], '192.0.2.10')
            self.assertEqual(settings['listen_address'], '0.0.0.0')
            self.assertEqual(settings['tls_mode'], 'Internal')
            executable = str(Path(os.environ['WINDIR']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
            preview = subprocess.run([executable, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                      str(ROOT / 'deploy/windows/Configure.ps1'), '-RuntimeDirectory', directory,
                                      '-Preview', '-SiteHost', 'chat.example.com', '-TlsMode', 'Public'],
                                     capture_output=True, text=True, timeout=20)
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertNotIn('tls internal', preview.stdout)
            self.assertFalse((root / 'gateway.json').exists())
            self.assertFalse((root / 'server.env').exists())
            self.assertEqual(caddy.read_text('ascii'), original)

    def test_public_ip_or_injected_hostname_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for host in ('localhost', '192.0.2.10', 'chat.local', 'bad.example.com {\n tls internal'):
                result = self.probe(directory, {'SiteHost': host, 'TlsMode': 'Public'})
                self.assertNotEqual(result.returncode, 0)
            self.assertFalse((Path(directory) / 'gateway.json').exists())
