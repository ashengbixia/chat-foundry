import asyncio
import json
import threading
import unittest
from unittest.mock import AsyncMock
from pydantic import Field
from backend.app.tools.runtime import ToolArguments, ToolContext, ToolRegistry, ToolSpec


class EchoArguments(ToolArguments):
    text: str = Field(min_length=1, max_length=50)


class ToolRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_sync_handler_runs_off_loop_and_parameters_share_schema(self):
        registry, threads = ToolRegistry(), []
        def echo(text):
            threads.append(threading.get_ident())
            return {'text': text}
        spec = registry.register(ToolSpec('echo', '回显文字', '原样返回文字', EchoArguments, echo))
        self.assertEqual(spec.definition()['function']['parameters']['properties']['text']['maxLength'], 50)
        self.assertEqual(json.loads(await registry.execute('echo', '{"text":"你好"}', ToolContext())), {'text': '你好'})
        self.assertNotEqual(threads[0], threading.get_ident())
        for arguments in ['[]', '{}', '{"text":1}', '{"text":"ok","session":"secret"}']:
            with self.assertRaises(ValueError):
                await registry.execute('echo', arguments, ToolContext())
        self.assertEqual(len(threads), 1)
        with self.assertRaises(ValueError):
            registry.register(spec)

    async def test_async_timeout_and_external_cancellation(self):
        registry = ToolRegistry()
        async def slow():
            await asyncio.sleep(60)
        registry.register(ToolSpec('slow', '慢工具', '超时验证', ToolArguments, slow, timeout=.02))
        with self.assertRaisesRegex(ValueError, '超时'):
            await registry.execute('slow', '{}', ToolContext())
        task = asyncio.create_task(registry.execute('slow', '{}', ToolContext()))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_scope_is_rechecked_before_handler_and_cannot_expand(self):
        registry, handler = ToolRegistry(), AsyncMock(return_value={'ok': True})
        registry.register(ToolSpec('files', '文件工具', '权限验证', ToolArguments, handler, scope='files'))
        context = ToolContext(allowed_file_ids={'f1'}, check_access=AsyncMock(return_value={'f1', 'secret'}))
        await registry.execute('files', '{}', context)
        self.assertEqual(context.allowed_file_ids, {'f1'})
        context.check_access.return_value = set()
        with self.assertRaisesRegex(ValueError, '权限'):
            await registry.execute('files', '{}', context)
        self.assertEqual(handler.await_count, 1)
        self.assertEqual(registry.definitions(context), [])

    async def test_cache_is_per_answer_and_only_reuses_identical_call(self):
        registry, handler = ToolRegistry(), AsyncMock(return_value={'ok': True})
        registry.register(ToolSpec('once', '归纳工具', '缓存验证', EchoArguments, handler, once_per_turn=True))
        first = ToolContext()
        for _ in range(2):
            await registry.execute('once', '{"text":"same"}', first)
        with self.assertRaisesRegex(ValueError, '本轮已执行'):
            await registry.execute('once', '{"text":"changed"}', first)
        await registry.execute('once', '{"text":"same"}', ToolContext())
        self.assertEqual(handler.await_count, 2)

    async def test_result_size_and_serialization_are_checked(self):
        registry = ToolRegistry()
        registry.register(ToolSpec('large', '大结果', '结果上限验证', ToolArguments, lambda: '字' * 20, max_result_bytes=20))
        with self.assertRaisesRegex(ValueError, '过大'):
            await registry.execute('large', '{}', ToolContext())
        registry.register(ToolSpec('invalid', '错误结果', '结果序列化验证', ToolArguments, lambda: float('nan')))
        with self.assertRaisesRegex(ValueError, '无法保存'):
            await registry.execute('invalid', '{}', ToolContext())
