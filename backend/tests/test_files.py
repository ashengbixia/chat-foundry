from backend.tests.auth_fixture import allow_legacy_test_user
import asyncio
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from fastapi import FastAPI, UploadFile
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from backend.app.api.files import router
from backend.app.core.database import Base, get_db
from backend.app.files import service
from backend.app.models.file import File
from backend.app.repositories.file import list_files
from backend.tests.index_fixture import mock_file_vectors

class FileUploadTests(unittest.IsolatedAsyncioTestCase):  # 测试仅使用临时目录和内存数据库
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        mock_file_vectors(self, self.sessions)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.directory = tempfile.TemporaryDirectory()
        self.upload_dir = Path(self.directory.name) / "uploads"
        self.path_patch = patch.object(service, "UPLOAD_DIR", self.upload_dir)
        self.path_patch.start()
        self.app = FastAPI()
        allow_legacy_test_user(self.app)
        self.app.include_router(router)
        async def override_db():
            async with self.sessions() as session:
                yield session
        self.app.dependency_overrides[get_db] = override_db
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://files-test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.path_patch.stop()
        await self.engine.dispose()
        self.directory.cleanup()

    async def upload(self, data=b"test text", filename="test.txt", content_type="text/plain"):
        return await self.client.post("/api/files", files={"file": (filename, data, content_type)})

    async def records(self):  # 用新的数据库会话重读，验证数据确实提交
        async with self.sessions() as session:
            return list((await session.execute(select(File))).scalars().all())

    async def test_upload_and_query_utf8_bom(self):
        data = b"\xef\xbb\xbf" + "中文测试\n第二行".encode()
        response = await self.upload(data, "资料.txt")
        self.assertEqual(response.status_code, 201)
        record = response.json()
        self.assertEqual(record["status"], "completed")
        self.assertEqual(record["size"], len(data))
        self.assertIsNone(record["error"])
        self.assertNotIn("storage_path", record)
        self.assertNotIn("text_content", record)
        latest = (await self.client.get(f'/api/files/{record["id"]}')).json()
        self.assertEqual(record["index_status"], "pending")  # 上传响应先返回，测试传输器随后等待后台任务执行完
        self.assertEqual(latest["index_status"], "ready")
        self.assertEqual(latest["id"], record["id"])
        self.assertFalse(latest["index_busy"])
        self.assertEqual((await self.client.get(f'/api/files/{record["id"]}/content')).json(), {"id": record["id"], "text_content": "中文测试\n第二行"})
        saved = (await self.records())[0]
        self.assertEqual((self.upload_dir / saved.storage_path).read_bytes(), data)

    async def test_same_filename_does_not_overwrite(self):
        first = (await self.upload(b"first", "same.txt")).json()
        second = (await self.upload(b"second", "same.txt")).json()
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual({path.read_bytes() for path in self.upload_dir.iterdir()}, {b"first", b"second"})

    async def test_extension_and_filename_validation(self):
        for filename in ["test.pdf", "test.txt.exe", "a" * 252 + ".txt"]:
            self.assertEqual((await self.upload(filename=filename)).status_code, 400)
        self.assertEqual(await self.records(), [])
        self.assertFalse(self.upload_dir.exists())

    async def test_mime_is_not_used_instead_of_actual_content(self):
        self.assertEqual((await self.upload(b"valid text", "TEST.TXT", "application/octet-stream")).status_code, 201)
        self.assertEqual((await self.upload(b"\xff\xfe", "invalid.txt", "text/plain")).status_code, 400)
        records = await self.records()
        failed = next(record for record in records if record.status == "failed")
        self.assertIn("UTF-8", failed.error)
        self.assertFalse((self.upload_dir / failed.storage_path).exists())
        self.assertEqual((await self.client.get(f"/api/files/{failed.id}/content")).status_code, 409)

    async def test_empty_whitespace_and_binary_fail_with_cleanup(self):
        for data in [b"", b" \n\t", b"text\x00binary"]:
            self.assertEqual((await self.upload(data)).status_code, 400)
        self.assertEqual(len(await self.records()), 3)
        self.assertTrue(all(record.status == "failed" for record in await self.records()))
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    async def test_size_boundary_and_oversized_file(self):
        with patch.object(service, "MAX_FILE_SIZE", 8), patch.object(service, "READ_CHUNK_SIZE", 3):
            self.assertEqual((await self.upload(b"12345678")).status_code, 201)
            response = await self.upload(b"123456789")
        self.assertEqual(response.status_code, 413)
        records = await self.records()
        self.assertEqual(sorted(record.status for record in records), ["completed", "failed"])
        self.assertEqual(len(list(self.upload_dir.iterdir())), 1)
        self.assertEqual(next(record for record in records if record.status == "failed").size, 9)

    async def test_client_paths_cannot_control_storage(self):
        response = await self.upload(filename="../../folder/escape.txt")
        self.assertEqual(response.status_code, 201)
        saved = (await self.records())[0]
        self.assertEqual(saved.filename, "escape.txt")
        self.assertEqual(saved.storage_path, f"{saved.id}.txt")
        self.assertEqual((self.upload_dir / saved.storage_path).parent, self.upload_dir)

    async def test_disk_error_is_hidden_and_failure_is_saved(self):
        with patch.object(service.anyio, "open_file", AsyncMock(side_effect=OSError("private-disk-path"))), patch.object(service, "logger", Mock()):
            response = await self.upload()
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("private-disk-path", response.text)
        self.assertEqual((await self.records())[0].status, "failed")

    async def test_cancelled_upload_removes_partial_file(self):
        upload = UploadFile(file=io.BytesIO(b"test"), filename="cancel.txt")
        try:
            with patch.object(upload, "read", AsyncMock(side_effect=[b"partial", asyncio.CancelledError()])):
                async with self.sessions() as session:
                    with self.assertRaises(asyncio.CancelledError):
                        await service.process_text_file(session, upload)
        finally:
            await upload.close()
        self.assertEqual((await self.records())[0].status, "failed")
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    async def test_missing_and_processing_file(self):
        for suffix in ["missing", "missing/content"]:
            self.assertEqual((await self.client.get(f"/api/files/{suffix}")).status_code, 404)
        self.assertEqual((await self.client.post("/api/files")).status_code, 422)
        async with self.sessions() as session:
            session.add(File(id="pending", filename="pending.txt", storage_path="pending.txt"))
            await session.commit()
        self.assertEqual((await self.client.get("/api/files/pending")).json()["status"], "processing")
        self.assertEqual((await self.client.get("/api/files/pending/content")).status_code, 409)

    async def test_list_all_file_summaries_without_loading_text(self):  # 超过100份文件也完整返回，列表SQL本身不读取正文
        async with self.sessions() as session:
            session.add_all([File(id=f"file-{index:03d}", filename="同名.txt", storage_path=f"{index}.txt", text_content="不应在列表读取的正文", status=["completed", "processing", "failed"][index % 3], created_at=1, updated_at=index + 1) for index in range(103)])
            session.add_all([File(id="tie-a", filename="A.txt", storage_path="a.txt", created_at=1, updated_at=999), File(id="tie-b", filename="B.txt", storage_path="b.txt", created_at=1, updated_at=999)])
            await session.commit()
        statements = []
        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
        try:
            response = await self.client.get("/api/files")
            self.assertEqual(response.status_code, 200)
            records = response.json()
            self.assertEqual(len(records), 105)
            self.assertEqual([record["id"] for record in records[:2]], ["tie-b", "tie-a"])
            self.assertEqual([record["id"] for record in records[2:]], [f"file-{index:03d}" for index in reversed(range(103))])
            self.assertTrue(all(set(record) == {"id", "filename", "content_type", "size", "status", "error", "created_at", "updated_at", "index_status", "index_error", "index_key", "index_busy"} for record in records))
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
        self.assertTrue(statements)
        self.assertTrue(all("files.text_content" not in statement and "files.storage_path" not in statement for statement in statements))
        async with self.sessions() as session:
            records = await list_files(session)
            self.assertIn("text_content", inspect(records[0]).unloaded)
            self.assertIn("storage_path", inspect(records[0]).unloaded)

    async def test_empty_file_list(self):
        response = await self.client.get("/api/files")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

if __name__ == "__main__":
    unittest.main()
