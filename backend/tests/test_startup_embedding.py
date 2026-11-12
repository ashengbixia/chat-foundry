import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from httpx import ASGITransport, AsyncClient
from backend.app import main
from backend.app.rag import embedding

class StartupEmbeddingTests(unittest.IsolatedAsyncioTestCase):  # 验证真实启动流程，替换数据库和模型加载，避免访问本机数据与网络
    async def asyncSetUp(self):
        self.session = AsyncMock()
        self.factory = Mock(return_value=self.session)
        self.dispose = AsyncMock()
        self.model = Mock()
        self.model.max_seq_length = 512
        self.model.tokenizer.side_effect = lambda texts, **kwargs: {"input_ids": [[1] * (len(text) + 2) for text in texts]}
        self.model.encode.return_value = SimpleNamespace(tolist=lambda: [[1.0, 0.0]])
        self.loader = Mock(return_value=self.model)
        self.init = AsyncMock()
        self.recover_messages = AsyncMock(return_value=0)
        self.recover_indexes = AsyncMock(return_value=0)
        for name in ('recover_interrupted_titles', 'recover_tasks', 'close_title_tasks', 'close_tasks'):
            change = patch.object(main, name, AsyncMock(return_value=0))
            change.start()
            self.addCleanup(change.stop)
        changes = [patch.object(main, "init_db", self.init), patch.object(main, "SessionLocal", self.factory), patch.object(main, "engine", SimpleNamespace(dispose=self.dispose)), patch.object(main, "recover_interrupted_messages", self.recover_messages), patch.object(main, "recover_interrupted_indexes", self.recover_indexes), patch.object(main, "logger", Mock()), patch.object(embedding, "logger", Mock()), patch.object(embedding, "_model", None), patch.object(embedding, "_model_key", None), patch.object(embedding, "_load_model", self.loader), patch.object(embedding, "settings", SimpleNamespace(embedding_model="startup-test-model", embedding_device="cpu", embedding_query_prefix=""))]
        for current in changes:
            current.start()
            self.addCleanup(current.stop)
        cleanup = patch.object(main, "prune_empty_collections", Mock(return_value=0))  # 启动测试不访问真实向量库
        self.prune = cleanup.start()
        self.addCleanup(cleanup.stop)
        worker = patch.object(main, 'cleanup_worker', AsyncMock())  # 本组只验证模型启动，清理任务另用真实临时数据库验证
        worker.start()
        self.addCleanup(worker.stop)

    async def health(self):
        async with AsyncClient(transport=ASGITransport(app=main.app), base_url="http://startup-test") as client:
            response = await client.get("/health")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"status": "ok"})

    async def test_database_recovery_then_prepare_before_serving_and_reuse_model(self):
        order = []
        async def initialize():
            order.append("database")
        async def recover_messages(session):
            order.append("messages")
            return 0
        async def recover_indexes(session):
            order.append("indexes")
            return 0
        def load_model(*args):
            order.append("model")
            return self.model
        self.init.side_effect = initialize
        self.recover_messages.side_effect = recover_messages
        self.recover_indexes.side_effect = recover_indexes
        self.loader.side_effect = load_model
        async with main.lifespan(main.app):
            self.assertEqual(order, ["database", "messages", "indexes", "model"])
            self.model.encode.assert_not_called()
            await self.health()
            await embedding.embed_texts(["准备后的第一次计算"])
            self.assertEqual(self.loader.call_count, 1)
            self.dispose.assert_not_awaited()
        self.dispose.assert_awaited_once()

    async def test_failed_model_does_not_abort_startup_and_can_retry_later(self):
        self.loader.side_effect = [RuntimeError("模拟离线没有缓存"), self.model]
        async with main.lifespan(main.app):
            await self.health()
            self.assertIsNone(embedding._model)
            self.assertEqual(await embedding.embed_texts(["稍后重试"]), [[1.0, 0.0]])
            self.assertEqual(self.loader.call_count, 2)
        self.dispose.assert_awaited_once()

    async def test_database_failure_does_not_start_model_download(self):
        self.init.side_effect = RuntimeError("数据库需要升级")
        with self.assertRaisesRegex(RuntimeError, "数据库需要升级"):
            async with main.lifespan(main.app):
                self.fail("数据库检查失败时不应进入服务阶段")
        self.loader.assert_not_called()
        self.factory.assert_not_called()
        self.dispose.assert_awaited_once()
        self.prune.assert_not_called()

    async def test_cleanup_failure_does_not_abort_startup(self):
        self.prune.side_effect = OSError("模拟目录清理失败")
        async with main.lifespan(main.app):
            await self.health()
        self.prune.assert_called_once()
        main.logger.exception.assert_called_once()

    async def test_preparation_uses_worker_thread_but_startup_waits_for_it(self):
        entered, release = threading.Event(), threading.Event()
        worker_threads = []
        event_thread = threading.get_ident()
        def load_model(*args):
            worker_threads.append(threading.get_ident())
            entered.set()
            if not release.wait(5):
                raise RuntimeError("测试没有及时释放模型加载")
            return self.model
        self.loader.side_effect = load_model
        ready, stop = asyncio.Event(), asyncio.Event()
        async def serving():
            async with main.lifespan(main.app):
                ready.set()
                await stop.wait()
        entering = asyncio.create_task(serving())
        try:
            self.assertTrue(await asyncio.wait_for(asyncio.to_thread(entered.wait, 3), 4))
            self.assertFalse(entering.done())
            self.assertNotEqual(worker_threads[0], event_thread)
        finally:
            release.set()
            await asyncio.wait_for(ready.wait(), 5)
            stop.set()
            await entering
        self.dispose.assert_awaited_once()

    async def test_shutdown_cancellation_still_propagates_and_disposes_engine(self):
        with patch.object(main, "prepare_embedding_model", AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                async with main.lifespan(main.app):
                    self.fail("启动被取消时不应进入服务阶段")
        self.dispose.assert_awaited_once()
