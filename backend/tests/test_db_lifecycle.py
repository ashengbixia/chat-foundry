"""真实 HTTP 流与极小连接池回归；不依赖垃圾回收归还连接。"""
import asyncio
import gc
import unittest
from unittest.mock import patch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from backend.tests import test_model_settings as fixtures
from backend.tests.test_subagents import call
from backend.app.api import chats
from backend.app.chat import service, subagents
from backend.app.models.subagent import SubagentTask


class DatabaseLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixtures.ModelSettingsTests.asyncSetUp(self)
        response = await self.admin.put(self.url, json=self.draft)
        self.assertEqual(response.status_code, 200, response.text)
        url = self.engine.url
        await self.engine.dispose()
        self.engine = create_async_engine(url, pool_size=1, max_overflow=0, pool_timeout=0.3)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        title = patch.object(chats, 'schedule_title')
        title.start()
        self.addCleanup(title.stop)
        enabled = gc.isenabled()
        gc.disable()
        self.addCleanup(gc.enable if enabled else gc.disable)

    async def asyncTearDown(self):
        await subagents.close_tasks()
        await fixtures.ModelSettingsTests.asyncTearDown(self)

    async def conversation(self):
        response = await self.admin.post('/api/chats', json={'title': '连接生命周期验证'})
        self.assertEqual(response.status_code, 201, response.text)
        chat_id = response.json()['id']
        response = await self.admin.post(f'/api/chats/{chat_id}/messages', json={'content': '完成测试任务'})
        self.assertEqual(response.status_code, 201, response.text)
        return chat_id

    async def test_repeated_streams_and_deletes_release_connections(self):
        async def model(messages, tools=None, connection=None):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            yield {'type': 'text_delta', 'text': '测试回答'}
        with patch.object(service, 'stream_model_events', model):
            for _ in range(5):
                chat_id = await self.conversation()
                response = await self.admin.post(f'/api/chats/{chat_id}/respond')
                self.assertIn('event: done', response.text)
                self.assertEqual(self.engine.pool.checkedout(), 0)
                messages = (await self.admin.get(f'/api/chats/{chat_id}/messages')).json()['items']
                self.assertEqual(messages[-1]['status'], 'completed')
                self.assertEqual(messages[-1]['content'], '测试回答')
                deleted = await self.admin.delete(f'/api/chats/{chat_id}')
                self.assertEqual(deleted.status_code, 204, deleted.text)
                self.assertEqual(self.engine.pool.checkedout(), 0)

    async def test_model_failure_preserves_partial_answer_and_releases_connection(self):
        async def model(messages, tools=None, connection=None):
            yield {'type': 'text_delta', 'text': '部分回答'}
            raise RuntimeError('simulated upstream failure')
        chat_id = await self.conversation()
        with patch.object(service, 'stream_model_events', model), self.assertLogs(service.logger, level='ERROR'):
            response = await self.admin.post(f'/api/chats/{chat_id}/respond')
        self.assertIn('event: error', response.text)
        self.assertEqual(self.engine.pool.checkedout(), 0)
        messages = (await self.admin.get(f'/api/chats/{chat_id}/messages')).json()['items']
        self.assertEqual(messages[-1]['status'], 'failed')
        self.assertIn('部分回答', messages[-1]['content'])
        self.assertEqual((await self.admin.delete(f'/api/chats/{chat_id}')).status_code, 204)

    async def test_foreground_child_and_parent_share_one_connection_without_starvation(self):
        async def model(messages, tools=None, connection=None):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            if '你是执行明确委派任务的子智能体' in messages[0]['content']:
                yield {'type': 'text_delta', 'text': '子任务完成'}
                self.assertEqual(self.engine.pool.checkedout(), 0)
            elif messages[-1]['role'] == 'tool':
                yield {'type': 'text_delta', 'text': '已取得子任务结果'}
            else:
                yield call('delegate_task', {'task': '提供简短测试结果', 'background': False})
        chat_id = await self.conversation()
        with patch.object(service, 'stream_model_events', model):
            response = await asyncio.wait_for(self.admin.post(f'/api/chats/{chat_id}/respond'), 15)
        self.assertIn('event: done', response.text)
        self.assertIn('已取得子任务结果', response.text)
        self.assertEqual(self.engine.pool.checkedout(), 0)
        async with self.sessions() as session:
            task = (await session.execute(select(SubagentTask).where(SubagentTask.parent_chat_id == chat_id))).scalar_one()
            self.assertEqual(task.status, 'completed')
            task_id = task.id
        cancelled = await self.admin.post(f'/api/chats/{chat_id}/subagents/{task_id}/cancel')
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()['status'], 'completed')
        self.assertEqual((await self.admin.delete(f'/api/chats/{chat_id}')).status_code, 204)
        self.assertEqual(self.engine.pool.checkedout(), 0)

    async def test_browser_disconnect_saves_cancelled_answer_and_returns_connection(self):
        chat_id = await self.conversation()
        partial_sent = asyncio.Event()
        sent = []
        request_sent = False
        async def model(messages, tools=None, connection=None):
            yield {'type': 'text_delta', 'text': '断开前的回答'}
            await asyncio.Event().wait()
        async def receive():
            nonlocal request_sent
            if not request_sent:
                request_sent = True
                return {'type': 'http.request', 'body': b'', 'more_body': False}
            await partial_sent.wait()
            return {'type': 'http.disconnect'}
        async def send(message):
            sent.append(message)
            if message['type'] == 'http.response.body' and b'event: text_delta' in message.get('body', b''):
                partial_sent.set()
        request = self.admin.build_request('POST', f'/api/chats/{chat_id}/respond')
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.0'},
                 'http_version': '1.1', 'method': 'POST', 'scheme': 'http',
                 'path': request.url.path, 'raw_path': request.url.raw_path,
                 'query_string': b'', 'root_path': '', 'headers': [(key.lower(), value) for key, value in request.headers.raw],
                 'server': ('accounts', 80), 'client': ('127.0.0.1', 12345)}
        with patch.object(service, 'stream_model_events', model):
            await asyncio.wait_for(self.app(scope, receive, send), 10)
        self.assertTrue(partial_sent.is_set(), sent)
        self.assertEqual(self.engine.pool.checkedout(), 0)
        messages = (await self.admin.get(f'/api/chats/{chat_id}/messages')).json()['items']
        self.assertEqual(messages[-1]['status'], 'cancelled')
        self.assertIn('断开前的回答', messages[-1]['content'])
        self.assertEqual((await self.admin.delete(f'/api/chats/{chat_id}')).status_code, 204)

    async def test_parallel_deletes_do_not_hold_auth_connections_while_stopping_children(self):
        chat_ids = [await self.conversation(), await self.conversation()]
        original = subagents.stop_parent
        both = asyncio.Event()
        entered = 0
        async def synchronized_stop(*args, **kwargs):
            nonlocal entered
            entered += 1
            if entered == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 3)
            return await original(*args, **kwargs)
        with patch.object(subagents, 'stop_parent', synchronized_stop):
            results = await asyncio.gather(*(self.admin.delete(f'/api/chats/{chat_id}') for chat_id in chat_ids))
        self.assertEqual([item.status_code for item in results], [204, 204])
        self.assertEqual(self.engine.pool.checkedout(), 0)
