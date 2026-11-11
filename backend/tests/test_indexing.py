from backend.tests.auth_fixture import allow_legacy_test_user
from backend.app.files.splitter import split_text
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from backend.app.api import files as file_api
from backend.app.core import database
from backend.app.files import service
from backend.app.models.file import File
from backend.app.models.file_chunk import FileChunk
from backend.app.rag import indexing
from backend.app.rag.vector_store import get_index_key
from backend.app.repositories.file_chunk import replace_file_chunks
from backend.app.schemas.file import FileResponse

class FileIndexingTests(unittest.IsolatedAsyncioTestCase):  # 临时SQL库加模拟向量，不下载真实模型
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{(self.root / 'test.db').as_posix()}")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        await database.create_tables(self.engine)
        self.vectors = {}  # 用切片ID模拟向量库，验证失败后没有半成品
        self.embed = AsyncMock(side_effect=lambda texts: [[1.0, 0.0] for _ in texts])
        self.save = Mock(side_effect=self.save_vectors)
        self.delete = Mock(side_effect=self.delete_vectors)
        self.delete_all = Mock(side_effect=self.delete_vectors)
        self.patches = [patch.object(indexing, "split_for_embedding", AsyncMock(side_effect=lambda text: split_text(text))), patch.object(indexing, "SessionLocal", self.sessions), patch.object(indexing, "embed_texts", self.embed), patch.object(indexing, "save_chunk_vectors", self.save), patch.object(indexing, "delete_file_vectors", self.delete), patch.object(indexing, "logger", Mock()), patch.object(service, "delete_all_file_vectors", self.delete_all), patch.object(service, "UPLOAD_DIR", self.root)]
        for current in self.patches:
            current.start()
            self.addCleanup(current.stop)
        async with self.sessions() as session:
            session.add_all([File(id="a", filename="资料.txt", storage_path="a.txt", status="completed", text_content="正文" * 1300), File(id="b", filename="其他.txt", storage_path="b.txt", status="completed", text_content="其他资料")])
            await session.flush()
            await replace_file_chunks(session, "a", "正文" * 1300)
            await replace_file_chunks(session, "b", "其他资料")
            await session.commit()
        (self.root / "a.txt").write_text("正文" * 1300, encoding="utf-8")
        app = FastAPI()
        allow_legacy_test_user(app)
        app.include_router(file_api.router)
        async def override_db():
            async with self.sessions() as session:
                yield session
        app.dependency_overrides[database.get_db] = override_db
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://index-test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()
        self.directory.cleanup()

    def save_vectors(self, items, vectors):
        for item, vector in zip(items, vectors):
            self.vectors[item["id"]] = (item["file_id"], item["content"], vector)

    def delete_vectors(self, file_id):
        for chunk_id in [key for key, value in self.vectors.items() if value[0] == file_id]:
            del self.vectors[chunk_id]

    async def reserve(self, file_id="a", rebuild=False):
        async with self.sessions() as session:
            return await indexing.reserve_file_index(session, file_id, rebuild)

    async def file(self, file_id="a"):
        async with self.sessions() as session:
            return await session.get(File, file_id)

    async def chunk_ids(self):
        async with self.sessions() as session:
            return set((await session.execute(select(FileChunk.id).where(FileChunk.file_id == "a"))).scalars())

    async def test_success_is_batched_and_records_model_key(self):
        record, token = await self.reserve()
        self.assertTrue(record.index_busy)
        self.assertEqual(record.index_status, "pending")
        with patch.object(indexing, "BATCH_SIZE", 2):
            await indexing.index_file("a", token)
        record = await self.file()
        self.assertEqual((record.status, record.index_status, record.index_key), ("completed", "ready", get_index_key()))
        self.assertIsNone(record.index_task_id)
        self.assertEqual(set(self.vectors), await self.chunk_ids())
        self.assertEqual(self.embed.await_count, 2)

    async def test_concurrent_reservation_only_allows_one_task(self):
        results = await asyncio.gather(self.reserve(), self.reserve(), return_exceptions=True)
        self.assertEqual(sum(isinstance(item, indexing.IndexBusyError) for item in results), 1)
        self.assertEqual(sum(isinstance(item, tuple) for item in results), 1)

    async def test_duplicate_background_execution_and_busy_actions_are_blocked(self):
        _, token = await self.reserve()
        entered, release = asyncio.Event(), asyncio.Event()
        async def paused(texts):
            entered.set()
            await release.wait()
            return [[1.0, 0.0] for _ in texts]
        self.embed.side_effect = paused
        task = asyncio.create_task(indexing.index_file("a", token))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await indexing.index_file("a", token)
            for suffix, method in [("", self.client.delete), ("/chunks", self.client.post), ("/index", self.client.post)]:
                self.assertEqual((await method(f"/api/files/a{suffix}")).status_code, 409)
            self.assertEqual(self.embed.await_count, 1)
        finally:
            release.set()
            await task
        self.assertEqual((await self.file()).index_status, "ready")

    async def test_failure_keeps_original_and_can_retry_without_leaking_error(self):
        _, token = await self.reserve()
        self.embed.side_effect = RuntimeError("private-path-and-secret")
        await indexing.index_file("a", token)
        record = await self.file()
        self.assertEqual((record.status, record.index_status), ("completed", "failed"))
        self.assertEqual(record.text_content, "正文" * 1300)
        self.assertNotIn("private-path", record.index_error)
        self.assertFalse(record.index_busy)
        self.embed.side_effect = lambda texts: [[1.0, 0.0] for _ in texts]
        _, retry_token = await self.reserve()
        self.assertNotEqual(token, retry_token)
        await indexing.index_file("a", retry_token)
        self.assertEqual((await self.file()).index_status, "ready")

    async def test_partial_write_failure_cleans_partial_vectors(self):
        def fail(items, vectors):
            self.save_vectors(items[:1], vectors[:1])
            raise RuntimeError("模拟写入中断")
        self.save.side_effect = fail
        _, token = await self.reserve()
        await indexing.index_file("a", token)
        self.assertEqual(self.vectors, {})
        self.assertEqual((await self.file()).index_status, "failed")
        self.assertGreater(len(await self.chunk_ids()), 0)

    async def test_cancelled_task_is_released_and_original_remains(self):
        self.embed.side_effect = asyncio.CancelledError()
        _, token = await self.reserve()
        with self.assertRaises(asyncio.CancelledError):
            await indexing.index_file("a", token)
        record = await self.file()
        self.assertEqual(record.index_status, "failed")
        self.assertIn("中断", record.index_error)
        self.assertFalse(record.index_busy)

    async def test_stale_token_cannot_execute_or_overwrite_new_task(self):
        _, old_token = await self.reserve()
        async with self.sessions() as session:
            self.assertEqual(await indexing.recover_interrupted_indexes(session), 1)
        _, new_token = await self.reserve()
        await indexing.index_file("a", old_token)
        await indexing._finish_index("a", old_token, "ready", key="wrong")
        self.assertEqual((await self.file()).index_task_id, new_token)
        self.embed.assert_not_awaited()

    async def test_startup_recovers_queued_and_running_but_preserves_ready(self):
        await self.reserve()
        async with self.sessions() as session:
            other = await session.get(File, "b")
            other.index_status = "ready"
            other.index_key = get_index_key()
            await session.commit()
            self.assertEqual(await indexing.recover_interrupted_indexes(session), 1)
        self.assertEqual((await self.file()).index_status, "failed")
        self.assertEqual((await self.file("b")).index_status, "ready")

    async def test_api_rebuild_returns_accepted_then_ready_and_preserves_chunks(self):
        before = await self.chunk_ids()
        response = await self.client.post("/api/files/a/index")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["index_status"], "pending")
        self.assertTrue(response.json()["index_busy"])
        self.assertNotIn("index_task_id", response.json())
        self.assertEqual((await self.client.get("/api/files/a")).json()["index_status"], "ready")
        self.assertEqual(before, await self.chunk_ids())

    async def test_api_pending_duplicate_is_blocked_before_background_starts(self):
        with patch.object(file_api, "index_file", AsyncMock()):
            self.assertEqual((await self.client.post("/api/files/a/index")).status_code, 202)
            self.assertEqual((await self.client.post("/api/files/a/index")).status_code, 409)
            self.assertEqual((await self.client.delete("/api/files/a")).status_code, 409)

    async def test_rechunk_replaces_old_vectors_and_upload_schedules_index(self):
        await self.client.post("/api/files/a/index")
        before = await self.chunk_ids()
        self.assertEqual((await self.client.post("/api/files/a/chunks")).status_code, 202)
        after = await self.chunk_ids()
        self.assertEqual(before, after)
        self.assertEqual(set(self.vectors), after)
        response = await self.client.post("/api/files", files={"file": ("新文件.txt", "上传自动索引".encode(), "text/plain")})
        self.assertEqual(response.status_code, 201)
        self.assertEqual((await self.file(response.json()["id"])).index_status, "ready")

    async def test_delete_cleans_vectors_only_after_sql_commit(self):
        await self.client.post("/api/files/a/index")
        async with self.sessions() as session:
            with patch.object(session, "commit", AsyncMock(side_effect=RuntimeError("提交失败"))):
                with self.assertRaises(RuntimeError):
                    await service.delete_uploaded_file(session, "a")
        self.delete_all.assert_not_called()
        self.assertEqual(set(self.vectors), await self.chunk_ids())
        self.assertEqual((await self.client.delete("/api/files/a")).status_code, 204)
        self.delete_all.assert_called_once_with("a")
        self.assertEqual(self.vectors, {})

    async def test_missing_file_empty_chunks_and_config_change(self):
        self.assertEqual((await self.client.post("/api/files/missing/index")).status_code, 404)
        async with self.sessions() as session:
            record = await session.get(File, "a")
            record.index_status = "ready"
            record.index_key = "old-model"
            await session.commit()
            self.assertEqual(FileResponse.model_validate(record).index_status, "pending")
            session.add(File(id="empty", filename="旧.txt", storage_path="empty.txt", status="completed", text_content="尚未切片"))
            await session.commit()
        await self.client.post("/api/files/empty/index")
        self.assertEqual((await self.file("empty")).index_status, "ready")  # 索引任务现在自动按模型Token窗口补建旧文件切片
