from backend.tests.auth_fixture import allow_legacy_test_user
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from backend.app.api import files as file_api
from backend.app.core import database
from backend.app.files import service
from backend.app.models.chat import Chat, Message
from backend.app.models.message_file import MessageFile
from backend.app.chat.context import build_model_messages
from backend.app.schemas.chat import MessageResponse
from backend.app.tools.files import read_uploaded_file
from backend.app.models.file import File
from backend.tests.index_fixture import mock_file_vectors
from backend.app.models.file_chunk import FileChunk
from backend.app.repositories.chat import create_message
from backend.app.models.file_cleanup import FileCleanup
from backend.app.files import cleanup as cleanup_service

class FileDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):  # 仅操作测试数据库和临时目录
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        @event.listens_for(self.engine.sync_engine, "connect")
        def enable_fk(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        mock_file_vectors(self, self.sessions)
        await database.create_tables(self.engine)
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "uploads"
        self.path_patch = patch.object(service, "UPLOAD_DIR", self.root)
        self.path_patch.start()
        app = FastAPI()
        allow_legacy_test_user(app)
        app.include_router(file_api.router)
        async def override_db():
            async with self.sessions() as session:
                yield session
        app.dependency_overrides[database.get_db] = override_db
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://deletion-test")
        self.body = "测试正文" * 400
        response = await self.client.post("/api/files", files={"file": ("测试.txt", self.body.encode(), "text/plain")})
        self.assertEqual(response.status_code, 201)
        self.file_id = response.json()["id"]
        self.path = self.root / f"{self.file_id}.txt"

    async def asyncTearDown(self):
        await self.client.aclose()
        self.path_patch.stop()
        await self.engine.dispose()
        self.directory.cleanup()

    async def count_chunks(self):
        async with self.sessions() as session:
            return (await session.execute(select(func.count()).select_from(FileChunk).where(FileChunk.file_id == self.file_id))).scalar_one()

    async def test_delete_removes_original_record_and_chunks(self):
        self.assertGreater(await self.count_chunks(), 0)
        response = await self.client.delete(f"/api/files/{self.file_id}")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertEqual((await self.client.get(f"/api/files/{self.file_id}")).status_code, 404)
        self.assertEqual(await self.count_chunks(), 0)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual((await self.client.delete(f"/api/files/{self.file_id}")).status_code, 404)

    async def test_referenced_file_is_deleted_but_history_snapshot_is_preserved(self):
        async with self.sessions() as session:
            session.add(Chat(id="chat", title="引用测试"))
            await session.commit()
            message = await create_message(session, "chat", "user", "问题", file_ids=[self.file_id])
            message_id = message.id
        response = await self.client.delete(f"/api/files/{self.file_id}")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(self.path.exists())
        self.assertEqual(await self.count_chunks(), 0)
        async with self.sessions() as session:
            message = await session.get(Message, message_id)
            snapshot = message.attachments[0]
            self.assertEqual((snapshot.file_id, snapshot.filename, snapshot.size), (self.file_id, "测试.txt", len(self.body.encode())))
            self.assertIsNone(snapshot.linked_file_id)
            self.assertTrue(snapshot.deleted)
            data = MessageResponse.model_validate(message).model_dump()
            self.assertTrue(data["attachments"][0]["deleted"])
            self.assertIn('"deleted": true', build_model_messages([message])[-1]["content"])
            with self.assertRaisesRegex(ValueError, "已删除"):
                await read_uploaded_file(self.file_id, session, {self.file_id})
            self.assertIsNotNone(await session.get(Chat, "chat"))
        self.assertEqual((await self.client.get(f"/api/files/{self.file_id}")).status_code, 404)

    async def test_commit_failure_restores_file_and_live_attachment_link(self):
        async with self.sessions() as session:
            session.add(Chat(id="chat", title="回滚测试"))
            await session.commit()
            message = await create_message(session, "chat", "user", "问题", file_ids=[self.file_id])
            message_id = message.id
        async with self.sessions() as session:
            with patch.object(session, "commit", AsyncMock(side_effect=RuntimeError("提交失败"))):
                with self.assertRaises(RuntimeError):
                    await service.delete_uploaded_file(session, self.file_id)
        async with self.sessions() as session:
            snapshot = (await session.get(Message, message_id)).attachments[0]
            self.assertEqual(snapshot.linked_file_id, self.file_id)
            self.assertFalse(snapshot.deleted)
        self.assertTrue(self.path.exists())

    async def test_deleting_shared_file_marks_all_message_snapshots(self):
        async with self.sessions() as session:
            session.add(Chat(id="chat", title="多消息引用"))
            await session.commit()
            await create_message(session, "chat", "user", "问题一", file_ids=[self.file_id])
            await create_message(session, "chat", "user", "问题二", file_ids=[self.file_id])
        self.assertEqual((await self.client.delete(f"/api/files/{self.file_id}")).status_code, 204)
        async with self.sessions() as session:
            snapshots = (await session.execute(select(MessageFile))).scalars().all()
            self.assertEqual(len(snapshots), 2)
            self.assertTrue(all(item.deleted and item.filename == "测试.txt" for item in snapshots))

    async def test_processing_file_is_preserved(self):
        async with self.sessions() as session:
            record = await session.get(File, self.file_id)
            record.status = "processing"
            await session.commit()
        self.assertEqual((await self.client.delete(f"/api/files/{self.file_id}")).status_code, 409)
        self.assertTrue(self.path.is_file())

    async def test_missing_disk_file_can_still_be_removed(self):
        self.path.unlink()
        self.assertEqual((await self.client.delete(f"/api/files/{self.file_id}")).status_code, 204)
        self.assertEqual(await self.count_chunks(), 0)

    async def test_vector_failure_keeps_durable_job_until_retry_succeeds(self):
        with patch.object(service, 'delete_all_file_vectors', Mock(side_effect=OSError('locked'))), patch('backend.app.files.cleanup.logger', Mock()):
            self.assertEqual((await self.client.delete(f'/api/files/{self.file_id}')).status_code, 204)
        async with self.sessions() as session:
            self.assertIsNone(await session.get(File, self.file_id))
            job = await session.get(FileCleanup, self.file_id)
            self.assertEqual(job.attempts, 1)
            self.assertGreater(job.next_retry_at, 0)
            job.next_retry_at = 0
            await session.commit()
        vectors = Mock()
        with patch.object(cleanup_service, 'delete_all_file_vectors', vectors):
            await cleanup_service.retry_file_cleanups(self.sessions, self.root)
        vectors.assert_called_once_with(self.file_id)
        async with self.sessions() as session:
            self.assertIsNone(await session.get(FileCleanup, self.file_id))

    async def test_failed_record_can_be_removed(self):
        async with self.sessions() as session:
            (await session.get(File, self.file_id)).status = "failed"
            await session.commit()
        self.assertEqual((await self.client.delete(f"/api/files/{self.file_id}")).status_code, 204)

    async def test_commit_failure_restores_original_and_database(self):
        async with self.sessions() as session:
            with patch.object(session, "commit", AsyncMock(side_effect=RuntimeError("提交失败"))):
                with self.assertRaises(RuntimeError):
                    await service.delete_uploaded_file(session, self.file_id)
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.body)
        self.assertGreater(await self.count_chunks(), 0)
        self.assertEqual(list(self.root.iterdir()), [self.path])
        self.assertEqual((await self.client.get(f"/api/files/{self.file_id}")).status_code, 200)

    async def test_outside_path_and_directory_are_never_deleted(self):
        outside = Path(self.directory.name) / "outside.txt"
        outside.write_text("不能删除", encoding="utf-8")
        folder = self.root / "folder"
        folder.mkdir()
        for target in [outside, folder]:
            async with self.sessions() as session:
                (await session.get(File, self.file_id)).storage_path = str(target)
                await session.commit()
            with patch.object(file_api, "logger", Mock()):
                response = await self.client.delete(f"/api/files/{self.file_id}")
            self.assertEqual(response.status_code, 500)
            self.assertTrue(target.exists())
        self.assertEqual(outside.read_text(encoding="utf-8"), "不能删除")

if __name__ == "__main__":
    unittest.main()
