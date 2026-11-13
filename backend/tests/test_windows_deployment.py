import os
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
