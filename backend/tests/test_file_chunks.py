import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from backend.app.api import files as file_api
from backend.app.core import database
from backend.app.files import service
from backend.app.files.splitter import split_text
from backend.app.models.file import File
from backend.app.models.file_chunk import FileChunk
from backend.app.repositories.file import update_file

class SplitTextTests(unittest.TestCase):
    def test_default_overlap_and_last_chunk(self):
        text = "0123456789" * 250
        chunks = split_text(text)
        self.assertEqual([(item["start_offset"], item["end_offset"]) for item in chunks], [(0, 1000), (850, 1850), (1700, 2500)])
        self.assertEqual([item["chunk_index"] for item in chunks], [0, 1, 2])
        for item in chunks:
            self.assertEqual(item["content"], text[item["start_offset"]:item["end_offset"]])
        self.assertEqual(chunks[0]["content"][-150:], chunks[1]["content"][:150])

    def test_unicode_whitespace_and_reconstruction(self):
        text = " 甲😀乙\n丙丁戊\t己 "
        chunks = split_text(text, chunk_size=5, overlap=2)
        reconstructed = chunks[0]["content"] + "".join(item["content"][2:] for item in chunks[1:])
        self.assertEqual(reconstructed, text)
        for item in chunks:
            self.assertEqual(len(item["content"]), item["end_offset"] - item["start_offset"])

    def test_empty_short_exact_boundary_and_zero_overlap(self):
        for text in ["", " \n\t"]:
            self.assertEqual(split_text(text), [])
        for size in [1, 999, 1000]:
            self.assertEqual(len(split_text("文" * size)), 1)
        self.assertEqual([item["content"] for item in split_text("12345678", 4, 0)], ["1234", "5678"])
        self.assertEqual(len(split_text("文" * 1850)), 2)  # 最后一块结束后不多产生重复尾块

    def test_invalid_parameters(self):
        for size, overlap in [(0, 0), (-1, 0), (True, 0), (2.5, 0), (5, -1), (5, 5), (5, True), (5, 1.5)]:
            with self.subTest(size=size, overlap=overlap), self.assertRaises(ValueError):
                split_text("正文", size, overlap)

class FileChunkTests(unittest.IsolatedAsyncioTestCase):  # 只操作临时文件与内存数据库
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        with patch.object(database, "engine", self.engine):
            await database.init_db()
        self.directory = tempfile.TemporaryDirectory()
        self.upload_dir = Path(self.directory.name) / "uploads"
        self.path_patch = patch.object(service, "UPLOAD_DIR", self.upload_dir)
        self.path_patch.start()
        async with self.sessions() as session:
            session.add_all([File(id="legacy", filename="旧文件.txt", storage_path="legacy.txt", text_content="旧正文", status="completed", size=9), File(id="pending", filename="p.txt", storage_path="p.txt"), File(id="failed", filename="f.txt", storage_path="f.txt", status="failed")])
            await session.commit()
        app = FastAPI()
        app.include_router(file_api.router)
        async def override_db():
            async with self.sessions() as session:
                yield session
        app.dependency_overrides[database.get_db] = override_db
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://chunk-test")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.path_patch.stop()
        await self.engine.dispose()
        self.directory.cleanup()

    async def upload(self, text):
        response = await self.client.post("/api/files", files={"file": ("切片测试.txt", text.encode(), "text/plain")})
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    async def chunks(self, file_id):
        async with self.sessions() as session:
            return list((await session.execute(select(FileChunk).where(FileChunk.file_id == file_id).order_by(FileChunk.chunk_index))).scalars().all())

    def fail_insert(self, connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO FILE_CHUNKS"):
            raise RuntimeError("模拟切片写入失败，不应返回此内部信息")

    async def test_upload_automatically_persists_chunks(self):
        text = "0123456789" * 250
        file_id = await self.upload(text)
        response = await self.client.get(f"/api/files/{file_id}/chunks")
        self.assertEqual(response.status_code, 200)
        page = response.json()
        self.assertEqual((page["total"], page["offset"], page["limit"]), (3, 0, 20))
        self.assertEqual([item["chunk_index"] for item in page["items"]], [0, 1, 2])
        for item in page["items"]:
            self.assertEqual(item["content"], text[item["start_offset"]:item["end_offset"]])
            self.assertEqual(item["file_id"], file_id)
        self.assertEqual(len(await self.chunks(file_id)), 3)

    async def test_pagination_and_file_isolation(self):
        file_id = await self.upload("文" * 40000)
        other = await self.upload("另一份资料")
        indices = []
        total = None
        for offset in [0, 20, 40]:
            response = await self.client.get(f"/api/files/{file_id}/chunks", params={"offset": offset, "limit": 20})
            page = response.json()
            total = page["total"]
            indices.extend(item["chunk_index"] for item in page["items"])
            self.assertTrue(all(item["file_id"] == file_id for item in page["items"]))
        self.assertEqual(indices, list(range(total)))
        self.assertEqual((await self.client.get(f"/api/files/{other}/chunks")).json()["total"], 1)
        self.assertEqual((await self.client.get(f"/api/files/{file_id}/chunks", params={"offset": 1000})).json()["items"], [])

    async def test_legacy_file_can_be_rebuilt_repeatedly_without_duplicates(self):
        self.assertEqual((await self.client.get("/api/files/legacy/chunks")).json()["total"], 0)
        for _ in range(2):
            response = await self.client.post("/api/files/legacy/chunks")
            self.assertEqual(response.json(), {"file_id": "legacy", "chunk_count": 1})
            chunks = await self.chunks("legacy")
            self.assertEqual(len(chunks), 1)
            self.assertEqual(chunks[0].content, "旧正文")

    async def test_missing_unprocessed_and_invalid_parameters(self):
        for method in [self.client.get, self.client.post]:
            self.assertEqual((await method("/api/files/missing/chunks")).status_code, 404)
            for file_id in ["pending", "failed"]:
                self.assertEqual((await method(f"/api/files/{file_id}/chunks")).status_code, 409)
        for params in [{"offset": -1}, {"limit": 0}, {"limit": 101}, {"offset": "bad"}]:
            self.assertEqual((await self.client.get("/api/files/legacy/chunks", params=params)).status_code, 422)

    async def test_rebuild_failure_preserves_old_chunks(self):
        await self.client.post("/api/files/legacy/chunks")
        before = [(item.id, item.content) for item in await self.chunks("legacy")]
        event.listen(self.engine.sync_engine, "before_cursor_execute", self.fail_insert)
        try:
            with patch.object(file_api, "logger", Mock()):
                response = await self.client.post("/api/files/legacy/chunks")
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", self.fail_insert)
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("内部信息", response.text)
        self.assertEqual([(item.id, item.content) for item in await self.chunks("legacy")], before)

    async def test_body_status_and_chunks_rollback_together(self):
        await self.client.post("/api/files/legacy/chunks")
        before = [(item.id, item.content) for item in await self.chunks("legacy")]
        event.listen(self.engine.sync_engine, "before_cursor_execute", self.fail_insert)
        try:
            async with self.sessions() as session:
                with self.assertRaises(RuntimeError):
                    await update_file(session, "legacy", 100, text_content="不应提交的新正文")
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", self.fail_insert)
        async with self.sessions() as session:
            record = await session.get(File, "legacy")
            self.assertEqual((record.text_content, record.status, record.size), ("旧正文", "completed", 9))
        self.assertEqual([(item.id, item.content) for item in await self.chunks("legacy")], before)

    async def test_failed_upload_cleans_file_and_partial_chunks(self):
        event.listen(self.engine.sync_engine, "before_cursor_execute", self.fail_insert)
        try:
            with patch.object(service, "logger", Mock()):
                response = await self.client.post("/api/files", files={"file": ("失败.txt", "测试正文".encode(), "text/plain")})
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", self.fail_insert)
        self.assertEqual(response.status_code, 500)
        async with self.sessions() as session:
            record = (await session.execute(select(File).where(File.filename == "失败.txt"))).scalar_one()
            self.assertEqual((record.status, record.text_content), ("failed", ""))
            self.assertEqual(await self.chunks(record.id), [])
        self.assertEqual(list(self.upload_dir.iterdir()), [])

    async def test_marking_file_failed_clears_chunks(self):
        await self.client.post("/api/files/legacy/chunks")
        async with self.sessions() as session:
            await update_file(session, "legacy", 9, error="测试失败状态")
        self.assertEqual(await self.chunks("legacy"), [])

    async def test_chunk_listing_does_not_load_original_body(self):
        await self.client.post("/api/files/legacy/chunks")
        statements = []
        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
        try:
            self.assertEqual((await self.client.get("/api/files/legacy/chunks")).status_code, 200)
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
        self.assertTrue(statements)
        self.assertTrue(all("files.text_content" not in statement for statement in statements))

if __name__ == "__main__":
    unittest.main()
