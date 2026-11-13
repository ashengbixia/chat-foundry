import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pydantic import SecretStr
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI
from backend.app.core.database import create_tables, get_db
from backend.app.models.chat import Chat, Message
from backend.app.models.user import User
from backend.app.models.file import File
from backend.app.models.chat_file import ChatFile
from backend.app.models.subagent import SubagentTask
from backend.app.chat import service, subagents
from backend.app.tools.local import read_webpage
from backend.app.tools.runtime import ToolContext
from backend.app.tools.registry import execute_tool
from backend.app.repositories.chat import create_message, list_chats
from backend.app.api.chats import router
from backend.app.auth import get_current_user
from backend.app.llm.connection import ModelConnection
from backend.app.chat.guards import acquire_generation, release_generation


def call(name, arguments, index=0, call_id='call'):
    return {'type': 'tool_call_delta', 'tool_calls': [{'index': index, 'id': call_id, 'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}]}


class SubagentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine('sqlite+aiosqlite:///' + (Path(self.temp.name) / 'test.db').as_posix())
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        await create_tables(self.engine)
        async with self.sessions() as session:
            session.add_all([User(id='u', username='one', name='One', password_hash='test'), User(id='v', username='two', name='Two', password_hash='test')])
            await session.flush()
            session.add_all([Chat(id='parent', user_id='u', title='父会话'), Chat(id='other', user_id='u', title='另一会话')])
            await session.commit()
            await create_message(session, 'parent', 'user', '比较 https://example.com/a 与 https://example.com/b')
        self.connection = ModelConnection('https://not-called.test/v1', 'test-model', 10, SecretStr('test-only'))

    async def asyncTearDown(self):
        await subagents.close_tasks()
        await self.engine.dispose()
        self.temp.cleanup()

    async def run_parent(self):
        async with self.sessions() as session:
            return [event async for event in service.stream_chat_history_events(session, 'parent', self.connection)]

    async def test_explicit_sites_are_inherited_by_both_children_without_search_roundtrips(self):
        async with self.sessions() as session:
            question = (await session.execute(select(Message).where(Message.chat_id == 'parent', Message.role == 'user'))).scalar_one()
            question.content = '请用两个子智能体查阅 https://docs.alpha.example/ 和 https://docs.beta.example/ 的文档并比较。'
            await session.commit()
        urls = ['https://docs.alpha.example/guide/start.html', 'https://docs.beta.example/tutorial/intro']
        async def model(messages, tools=None, connection=None):
            if '你是执行明确委派任务的子智能体' in messages[0]['content']:
                if messages[-1]['role'] == 'tool':
                    yield {'type': 'text_delta', 'text': '已读取指定的官方教程目录。'}
                else:
                    question = next(item['content'] for item in messages if item['role'] == 'user')
                    yield call('read_webpage', {'url': next(url for url in urls if url in question)}, call_id='read')
            elif messages[-1]['role'] == 'tool':
                results = [json.loads(item['content']) for item in messages if item['role'] == 'tool']
                self.assertEqual(len(results), 2)
                self.assertTrue(all(item['status'] == 'completed' and item['source_count'] == 1 for item in results))
                yield {'type': 'text_delta', 'text': '两份官方教程目录均已读取，可按目录比较学习主题。'}
            else:
                for index, url in enumerate(urls):
                    yield call('delegate_task', {'task': '读取官方教程 ' + url, 'tool_names': ['read_webpage']}, index, f'delegate-{index}')
        async def fetch(url):
            return {'url': url, 'title': '官方教程', 'text': '变量、控制流和函数。', 'retrieved_at': 1}
        with patch.object(service, 'stream_model_events', model), patch.object(read_webpage, 'fetch_page', fetch):
            events = await self.run_parent()
        self.assertTrue(events[-1].startswith('event: done'), events[-1])
        async with self.sessions() as session:
            answer = (await session.execute(select(Message).where(Message.chat_id == 'parent', Message.role == 'assistant'))).scalar_one()
            self.assertIn('[1] [2]', answer.content)

    async def test_two_foreground_delegations_run_concurrently_keep_history_and_remap_sources(self):
        started, both = set(), asyncio.Event()
        async def model(messages, tools=None, connection=None):
            child = '你是执行明确委派任务的子智能体' in messages[0]['content']
            if child:
                self.assertNotIn('delegate_task', str(tools))
                question = next(item['content'] for item in messages if item['role'] == 'user')
                letter = 'a' if 'task-a' in question else 'b'
                if messages[-1]['role'] != 'tool':
                    started.add(letter)
                    if len(started) == 2:
                        both.set()
                    await asyncio.wait_for(both.wait(), 4)
                    yield call('read_webpage', {'url': 'https://example.com/' + letter}, call_id='read-' + letter)
                else:
                    yield {'type': 'text_delta', 'text': '子结论〔citation_id: 1〕'}
            elif messages[-1]['role'] != 'tool':
                for index, letter in enumerate(('a', 'b')):
                    yield call('delegate_task', {'task': 'task-' + letter, 'task_context': '读取 https://example.com/' + letter}, index, 'delegate-' + letter)
            else:
                results = [json.loads(item['content']) for item in messages if item['role'] == 'tool']
                self.assertEqual([result['chunks'][0]['citation_id'] for result in results], [1, 2])
                self.assertEqual([result['summary'] for result in results], ['子结论[1]', '子结论[2]'])
                yield {'type': 'text_delta', 'text': '比较结论[1][2]'}
        async def fetch(url):
            return {'url': url, 'title': '测试网页', 'text': '真实正文：' + url, 'retrieved_at': 1}
        with patch.object(service, 'stream_model_events', model), patch.object(read_webpage, 'fetch_page', fetch):
            events = await self.run_parent()
        self.assertTrue(events[-1].startswith('event: done'), events[-1])
        self.assertEqual(started, {'a', 'b'})
        async with self.sessions() as session:
            tasks = list((await session.execute(select(SubagentTask))).scalars())
            self.assertEqual([task.status for task in tasks], ['completed', 'completed'])
            self.assertEqual([chat.id for chat in await list_chats(session, 'u')], ['parent', 'other'])
            for task in tasks:
                history = list((await session.execute(select(Message).where(Message.chat_id == task.child_chat_id).order_by(Message.sequence))).scalars())
                self.assertEqual(len(history), 2)
                self.assertTrue(any(item['type'] == 'function_call' for item in history[-1].output))
                self.assertEqual(history[-1].content, '子结论〔citation_id: 1〕')
                with self.assertRaises(ValueError):
                    await subagents.task_result(self.sessions, 'other', task.id)
        app = FastAPI(); app.include_router(router)
        async def db():
            async with self.sessions() as session:
                yield session
        user_id = 'u'
        async def user():
            async with self.sessions() as session:
                return await session.get(User, user_id)
        app.dependency_overrides[get_db] = db
        app.dependency_overrides[get_current_user] = user
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            self.assertEqual((await client.get('/api/chats/' + tasks[0].child_chat_id)).status_code, 404)
            history = await client.get(f'/api/chats/parent/subagents/{tasks[0].id}/messages')
            self.assertEqual(history.status_code, 200)
            self.assertEqual(history.json()['items'][-1]['content'], '子结论〔citation_id: 1〕')
            user_id = 'v'
            self.assertEqual((await client.get('/api/chats/parent/subagents')).status_code, 404)
            user_id = 'u'
            self.assertEqual((await client.delete('/api/chats/parent')).status_code, 204)
        async with self.sessions() as session:
            self.assertEqual((await session.execute(select(func.count()).select_from(SubagentTask))).scalar_one(), 0)
            self.assertIsNone(await session.get(Chat, tasks[0].child_chat_id))

    async def test_background_returns_before_child_and_delivers_without_fake_user_message(self):
        finish, started = asyncio.Event(), asyncio.Event()
        async def model(messages, tools=None, connection=None):
            if '你是执行明确委派任务的子智能体' in messages[0]['content']:
                started.set(); await finish.wait()
                yield {'type': 'text_delta', 'text': '后台任务结论'}
            elif '这是此前后台子任务完成后的结果更新' in messages[0]['content']:
                self.assertEqual(json.loads(messages[-1]['content'])['summary'], '后台任务结论')
                yield {'type': 'text_delta', 'text': '此前子任务更新：后台任务结论'}
            elif messages[-1].get('content') == '新的用户问题':
                yield {'type': 'text_delta', 'text': '正常回答新问题'}
            elif messages[-1]['role'] == 'tool':
                self.assertIn(json.loads(messages[-1]['content'])['status'], ['queued', 'running'])
                yield {'type': 'text_delta', 'text': '已交给后台任务处理'}
            else:
                yield call('delegate_task', {'task': '后台研究', 'background': True})
        with patch.object(service, 'stream_model_events', model):
            events = await asyncio.wait_for(self.run_parent(), 5)
            self.assertTrue(events[-1].startswith('event: done'))
            await asyncio.wait_for(started.wait(), 3)
            async with self.sessions() as session:
                task_id = (await session.execute(select(SubagentTask.id))).scalar_one()
            job = subagents._jobs[task_id]
            self.assertTrue(acquire_generation('parent'))
            try:
                finish.set(); await job
                # 用户发送优先，中断等待中的自动续答；下一次普通回答结束后继续投递。
                await subagents.pause_delivery('parent')
            finally:
                release_generation('parent')
            async with self.sessions() as session:
                await create_message(session, 'parent', 'user', '新的用户问题')
            await self.run_parent()
            delivery = subagents._deliveries.get('parent')
            if delivery:
                await asyncio.wait_for(delivery, 5)
        async with self.sessions() as session:
            task = await session.get(SubagentTask, task_id)
            self.assertEqual(task.delivery_status, 'delivered')
            self.assertEqual((await session.execute(select(func.count()).select_from(Message).where(Message.chat_id == 'parent', Message.role == 'user'))).scalar_one(), 2)
            self.assertEqual((await session.execute(select(Message.content).where(Message.chat_id == 'parent').order_by(Message.sequence.desc()).limit(1))).scalar_one(), '此前子任务更新：后台任务结论')

    async def test_recursion_extra_files_and_cross_chat_lookup_are_rejected(self):
        with self.assertRaisesRegex(ValueError, '权限'):
            await execute_tool('delegate_task', '{"task":"再次派生"}', context=ToolContext(is_subagent=True))
        async with self.sessions() as session:
            assistant = await create_message(session, 'parent', 'assistant', '', status='generating')
        context = ToolContext(chat_id='parent', parent_message_id=assistant.id, session_factory=self.sessions)
        with self.assertRaisesRegex(ValueError, '文件'):
            await subagents.delegate(context, '读取秘密', file_ids=['secret'])
        with patch.object(subagents, '_launch'):
            result = await subagents.delegate(context, '研究 https://unapproved.example/ 是否为目标资料来源', background=True)
        async with self.sessions() as session:
            task = await session.get(SubagentTask, result['task_id'])
            self.assertEqual(task.scope['urls'], [])
            self.assertEqual(task.scope['web_origins'], [])  # 候选线索不会变成站点授权。
            child = (await session.execute(select(Message).where(Message.chat_id == task.child_chat_id))).scalar_one()
            child_context = ToolContext(is_subagent=True, history=[child])
            with patch.object(read_webpage.settings, 'web_url_policy', 'sources'), self.assertRaisesRegex(ValueError, '不能自行拼接'):
                await execute_tool('read_webpage', '{"url":"https://unapproved.example/"}', context=child_context)
        with self.assertRaises(ValueError):
            await subagents.task_result(self.sessions, 'other', 'missing')

    async def test_revoked_parent_file_and_cancel_preserve_partial_child_history(self):
        async with self.sessions() as session:
            session.add(File(id='f', user_id='u', filename='范围资料.txt', size=12, storage_path='unused', text_content='授权原文', status='completed'))
            await session.flush()
            session.add(ChatFile(chat_id='parent', file_id='f', linked_file_id='f', filename='范围资料.txt', size=12))
            await session.commit()
        started, hold = asyncio.Event(), asyncio.Event()
        async def model(messages, tools=None, connection=None):
            if '你是执行明确委派任务的子智能体' in messages[0]['content']:
                yield {'type': 'reasoning_delta', 'text': '已开始检查'}
                started.set(); await hold.wait()
            elif '这是此前后台子任务完成后的结果更新' in messages[0]['content']:
                yield {'type': 'text_delta', 'text': '子任务已停止'}
            elif messages[-1]['role'] == 'tool':
                yield {'type': 'text_delta', 'text': '后台运行中'}
            else:
                yield call('delegate_task', {'task': '子检查', 'background': True, 'file_ids': ['f'], 'tool_names': ['read_uploaded_file']})
        with patch.object(service, 'stream_model_events', model):
            await self.run_parent()
            await asyncio.wait_for(started.wait(), 3)
            async with self.sessions() as session:
                task = (await session.execute(select(SubagentTask))).scalar_one()
                scope = subagents.ChildScope(task.id, 'parent', {'f'}, {}, frozenset(), frozenset(), self.sessions)
                self.assertEqual(await subagents.current_child_scope(session, scope), {'f'})
                link = await session.get(ChatFile, ('parent', 'f')); link.active = False
                await session.commit()
                self.assertEqual(await subagents.current_child_scope(session, scope), set())
            result = await subagents.cancel_task(self.sessions, 'parent', task.id)
            self.assertEqual(result['status'], 'cancelled')
            await subagents.close_tasks()
        async with self.sessions() as session:
            answer = (await session.execute(select(Message).where(Message.chat_id == task.child_chat_id, Message.role == 'assistant'))).scalar_one()
            self.assertEqual(answer.status, 'cancelled')
            self.assertTrue(any(item.get('type') == 'reasoning' for item in answer.output))
