import asyncio
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from pydantic import SecretStr
from backend.app.core.backup import backup, restore
from backend.app.core.instance import InstanceLock
from backend.app.llm import http
from backend.app.llm.connection import ModelConnection


class OfflineDataTests(unittest.TestCase):
    def test_locked_backup_roundtrip_and_unsafe_restore(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'source'
            root.mkdir()
            with closing(sqlite3.connect(root / 'app.db')) as database:
                database.execute('create table sample (value text)')
                database.execute("insert into sample values ('saved')")
                database.commit()
            (root / 'secrets').mkdir()
            (root / 'secrets' / 'model-settings.key').write_bytes(b'test-only')
            archive = Path(temporary) / 'snapshot.zip'
            with InstanceLock(root):
                with self.assertRaises(RuntimeError):
                    backup(root, archive)
            backup(root, archive)
            destination = Path(temporary) / 'restored'
            restore(archive, destination)
            self.assertEqual((destination / 'secrets' / 'model-settings.key').read_bytes(), b'test-only')
            with closing(sqlite3.connect(destination / 'app.db')) as database:
                self.assertEqual(database.execute('select value from sample').fetchone()[0], 'saved')
            with self.assertRaises(ValueError):
                restore(archive, destination)
            unsafe = Path(temporary) / 'unsafe.zip'
            with zipfile.ZipFile(unsafe, 'w') as output:
                output.writestr('manifest.json', json.dumps({'version': 1, 'files': {'app.db': 'bad', '../escape': 'bad'}}))
                output.writestr('app.db', b'bad')
                output.writestr('../escape', b'bad')
            with self.assertRaises(ValueError):
                restore(unsafe, Path(temporary) / 'rejected')
            self.assertFalse((Path(temporary) / 'escape').exists())


class ModelDeploymentTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_transport_pins_ip_preserves_tls_and_blocks_private_dns(self):
        seen = []
        async def send(_self, request):
            seen.append(request)
            return httpx.Response(200)
        with patch.object(http, 'resolve_public', AsyncMock(return_value='8.8.8.8')), patch.object(http.settings, 'model_internal_urls', []), patch.object(httpx.AsyncHTTPTransport, 'handle_async_request', send):
            async with http.ModelTransport('https://model.example/v1') as transport:
                await transport.handle_async_request(httpx.Request('POST', 'https://model.example/v1/chat/completions'))
            self.assertEqual(seen[0].url.host, '8.8.8.8')
            self.assertEqual(seen[0].headers['host'], 'model.example')
            self.assertEqual(seen[0].extensions['sni_hostname'], 'model.example')
            async with http.ModelTransport('http://127.0.0.1:11434/v1') as transport:
                with self.assertRaises(httpx.ConnectError):
                    await transport.handle_async_request(httpx.Request('GET', 'http://127.0.0.1:11434/v1/models'))
        answers = [(None, None, None, None, ('127.0.0.1', 443))]
        with patch.object(asyncio.get_running_loop(), 'getaddrinfo', AsyncMock(return_value=answers)):
            with self.assertRaises(httpx.ConnectError):
                await http.resolve_public('rebound.example', 443)

    async def test_request_slot_timeout_and_cancel_release(self):
        connection = ModelConnection('https://model.example/v1', 'test', 1, SecretStr(''), True, 'user')
        entered = asyncio.Event()
        async def hold():
            async with http.model_client(connection):
                entered.set()
                await asyncio.Event().wait()
        with patch.object(http.settings, 'model_user_concurrency', 1), patch.object(http.settings, 'model_queue_timeout', 0.05):
            task = asyncio.create_task(hold())
            await entered.wait()
            with self.assertRaises(httpx.PoolTimeout):
                async with http.model_client(connection):
                    self.fail('must wait')
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertNotIn('user', http._active)
            async with http.model_client(connection):
                pass
