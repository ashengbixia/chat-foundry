import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from backend.app.rag import embedding

class ModelCacheLoadingTests(unittest.TestCase):
    def test_complete_cache_stays_offline_and_missing_cache_falls_back_to_download(self):
        constructor = Mock(return_value=object())
        with patch.dict('sys.modules', {'sentence_transformers': SimpleNamespace(SentenceTransformer=constructor)}):
            embedding._load_model('model', 'cpu')
            self.assertTrue(constructor.call_args.kwargs['local_files_only'])
            self.assertEqual(constructor.call_count, 1)
            constructor.reset_mock()
            constructor.side_effect = [OSError('cache missing'), object()]
            embedding._load_model('model', 'cpu')
            self.assertEqual([call.kwargs['local_files_only'] for call in constructor.call_args_list], [True, False])

class EmbeddingTests(unittest.IsolatedAsyncioTestCase):  # 模拟模型，不下载权重、不调用模型API
    async def asyncSetUp(self):
        self.settings = SimpleNamespace(embedding_model="test-model", embedding_device="cpu", embedding_query_prefix="检索：")
        self.model = Mock()
        self.model.max_seq_length = 512
        self.model.tokenizer.side_effect = lambda texts, **kwargs: {"input_ids": [[1] * (len(text) + 2) for text in texts]}
        self.model.encode.return_value = SimpleNamespace(tolist=lambda: [[1.0, 0.0]])
        self.loader = Mock(return_value=self.model)
        self.patches = [patch.object(embedding, "settings", self.settings), patch.object(embedding, "_model", None), patch.object(embedding, "_model_key", None), patch.object(embedding, "_load_model", self.loader), patch.object(embedding, "logger", Mock())]
        for current in self.patches:
            current.start()
            self.addCleanup(current.stop)

    async def test_texts_use_normalized_embeddings_and_reuse_loaded_model(self):
        self.assertEqual(await embedding.embed_texts(["住宿标准"]), [[1.0, 0.0]])
        await embedding.embed_texts(["餐费标准"])
        self.loader.assert_called_once_with("test-model", "cpu")
        self.model.encode.assert_called_with(["餐费标准"], batch_size=32, normalize_embeddings=True, show_progress_bar=False)

    async def test_empty_and_invalid_input_does_not_load_model(self):
        self.assertEqual(await embedding.embed_texts([]), [])
        for texts in ["不是列表", None, [""], [" \n"], [1], ["有效", None]]:
            with self.subTest(texts=texts), self.assertRaises(ValueError):
                await embedding.embed_texts(texts)
        self.loader.assert_not_called()

    async def test_queries_add_prefix_but_documents_do_not(self):
        await embedding.embed_texts(["正文"])
        self.assertEqual(self.model.encode.call_args.args[0], ["正文"])
        self.assertEqual(await embedding.embed_query("  住宿上限？  "), [1.0, 0.0])
        self.assertEqual(self.model.encode.call_args.args[0], ["检索：住宿上限？"])
        self.settings.embedding_query_prefix = ""
        await embedding.embed_query("问题")
        self.assertEqual(self.model.encode.call_args.args[0], ["问题"])

    async def test_invalid_query_does_not_load_model(self):
        for query in ["", " \n", None, 1]:
            with self.subTest(query=query), self.assertRaises(ValueError):
                await embedding.embed_query(query)
        self.loader.assert_not_called()

    async def test_oversized_input_is_rejected_before_encoding(self):
        with self.assertRaisesRegex(ValueError, "Token上限"):
            await embedding.embed_texts(["文" * 511])
        self.model.encode.assert_not_called()

    async def test_concurrent_calls_load_model_only_once(self):
        await asyncio.gather(*(embedding.embed_texts([f"片段{i}"]) for i in range(6)))
        self.assertEqual(self.loader.call_count, 1)
        self.assertEqual(self.model.encode.call_count, 6)

    async def test_changed_model_or_device_reloads_cache(self):
        await embedding.embed_texts(["正文"])
        self.settings.embedding_model = "another-model"
        await embedding.embed_texts(["正文"])
        self.settings.embedding_device = "another-device"
        await embedding.embed_texts(["正文"])
        self.assertEqual(self.loader.call_count, 3)
        self.loader.assert_called_with("another-model", "another-device")

    async def test_failed_loading_can_be_retried(self):
        self.loader.side_effect = [RuntimeError("模拟加载失败"), self.model]
        with self.assertRaisesRegex(RuntimeError, "模拟加载失败"):
            await embedding.embed_texts(["正文"])
        self.assertEqual(await embedding.embed_texts(["正文"]), [[1.0, 0.0]])

    async def test_computation_runs_outside_event_loop(self):
        event_thread = threading.get_ident()
        worker_threads = []
        def encode(*args, **kwargs):
            worker_threads.append(threading.get_ident())
            return SimpleNamespace(tolist=lambda: [[1.0, 0.0]])
        self.model.encode.side_effect = encode
        await embedding.embed_texts(["正文"])
        self.assertNotEqual(worker_threads[0], event_thread)

    async def test_startup_preparation_loads_once_without_encoding_and_is_reused(self):
        self.assertTrue(await embedding.prepare_embedding_model())
        self.assertTrue(await embedding.prepare_embedding_model())
        self.model.encode.assert_not_called()
        await embedding.embed_texts(["正文"])
        self.loader.assert_called_once_with("test-model", "cpu")
        self.model.encode.assert_called_once()

    async def test_startup_and_first_request_share_the_same_loading_lock(self):
        result = await asyncio.gather(embedding.prepare_embedding_model(), embedding.embed_texts(["正文"]), embedding.prepare_embedding_model())
        self.assertEqual(result, [True, [[1.0, 0.0]], True])
        self.assertEqual(self.loader.call_count, 1)

    async def test_failed_preparation_returns_false_and_request_can_retry(self):
        self.loader.side_effect = [RuntimeError("模拟下载失败"), self.model]
        self.assertFalse(await embedding.prepare_embedding_model())
        self.assertIsNone(embedding._model)
        self.assertIsNone(embedding._model_key)
        embedding.logger.exception.assert_called_once()
        self.assertEqual(await embedding.embed_texts(["正文"]), [[1.0, 0.0]])
        self.assertEqual(self.loader.call_count, 2)

    async def test_preparation_cancellation_is_not_swallowed(self):
        with patch.object(embedding.anyio.to_thread, "run_sync", AsyncMock(side_effect=asyncio.CancelledError())):
            with self.assertRaises(asyncio.CancelledError):
                await embedding.prepare_embedding_model()
        self.loader.assert_not_called()
        embedding.logger.exception.assert_not_called()
