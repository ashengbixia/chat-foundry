import asyncio
import unittest
import json
import httpx
from pydantic import SecretStr
from unittest.mock import AsyncMock, patch
from sqlalchemy import update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from backend.app.core.database import create_tables
from backend.app.models.chat import Chat
from backend.app.repositories.chat import create_chat, create_message, rename_chat, delete_chat
from backend.app.chat import titles
from backend.app.llm.connection import ModelConnection


class ChatTitleTests(unittest.IsolatedAsyncioTestCase):
    async def test_greeting_stays_pending_until_substantive_answer_even_after_recovery(self):
        async with self.sessions() as session:
            chat_id = (await create_chat(session)).id
            await create_message(session, chat_id, 'user', '你好！')
            await create_message(session, chat_id, 'assistant', '你好，有什么需要帮助的？')
        with patch.object(titles, 'generate_title', AsyncMock(return_value='电影推荐研究')) as model:
            await titles.title_chat(self.sessions, chat_id, None)
            model.assert_not_awaited()
            async with self.sessions() as session:
                await titles.recover_interrupted_titles(session)
                self.assertEqual((await session.get(Chat, chat_id)).title_status, 'pending')
                await create_message(session, chat_id, 'user', '你好，帮我比较电影推荐')
                await create_message(session, chat_id, 'assistant', '可以按电影类型比较。')
            await titles.title_chat(self.sessions, chat_id, None)
            self.assertEqual(model.await_args.args[0], '你好，帮我比较电影推荐')

    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        await create_tables(self.engine)
        async with self.sessions() as session:
            self.chat_id = (await create_chat(session)).id
            await create_message(session, self.chat_id, 'user', '帮我找一些Python学习网站')

    async def asyncTearDown(self):
        await titles.close_title_tasks()
        await self.engine.dispose()

    async def complete(self):
        async with self.sessions() as session:
            await create_message(session, self.chat_id, 'assistant', 'Python官方文档与W3Schools适合学习入门。')

    async def read(self):
        async with self.sessions() as session:
            return await session.get(Chat, self.chat_id)

    async def test_first_completed_round_generates_once_and_keeps_sort_time(self):
        with patch.object(titles, 'generate_title', AsyncMock(return_value='Python入门学习网站')) as model:
            await titles.title_chat(self.sessions, self.chat_id, None)
            model.assert_not_awaited()  # 未回答时不命名
            await self.complete()
            before = (await self.read()).updated_at
            await titles.title_chat(self.sessions, self.chat_id, None)
            await titles.title_chat(self.sessions, self.chat_id, None)
            model.assert_awaited_once()
            self.assertIn('Python学习网站', model.await_args.args[0])
            self.assertIn('W3Schools', model.await_args.args[1])
        chat = await self.read()
        self.assertEqual((chat.title, chat.title_status), ('Python入门学习网站', 'generated'))
        self.assertEqual(chat.updated_at, before)

    async def test_manual_rename_even_to_placeholder_wins_and_deleted_chat_is_not_restored(self):
        await self.complete()
        async def model(*_):
            async with self.sessions() as session:
                await rename_chat(session, self.chat_id, '新会话')
            return '自动标题'
        with patch.object(titles, 'generate_title', model):
            await titles.title_chat(self.sessions, self.chat_id, None)
        chat = await self.read()
        self.assertEqual((chat.title, chat.title_status), ('新会话', 'manual'))
        async with self.sessions() as session:
            await session.execute(update(Chat).where(Chat.id == self.chat_id).values(title_status='pending'))
            await session.commit()
        async def deleting(*_):
            async with self.sessions() as session:
                await delete_chat(session, self.chat_id)
            return '迟到的标题'
        with patch.object(titles, 'generate_title', deleting):
            await titles.title_chat(self.sessions, self.chat_id, None)
        self.assertIsNone(await self.read())

    async def test_failures_and_interrupted_tasks_use_fallback_without_model_retry(self):
        await self.complete()
        with patch.object(titles, 'generate_title', AsyncMock(side_effect=TimeoutError)) as model:
            await titles.title_chat(self.sessions, self.chat_id, None)
            await titles.title_chat(self.sessions, self.chat_id, None)
        model.assert_awaited_once()
        chat = await self.read()
        self.assertEqual(chat.title_status, 'fallback')
        self.assertIn('Python', chat.title)
        async with self.sessions() as session:
            chat = await session.get(Chat, self.chat_id)
            chat.title_status = 'generating'
            await session.commit()
            self.assertEqual(await titles.recover_interrupted_titles(session), 1)
        self.assertEqual((await self.read()).title_status, 'fallback')

    async def test_scheduled_title_is_independent_and_duplicate_schedule_reuses_task(self):
        await self.complete()
        waiting, finish = asyncio.Event(), asyncio.Event()
        async def model(*_):
            waiting.set()
            await finish.wait()
            return '学习网站推荐'
        with patch.object(titles, 'generate_title', model):
            titles.schedule_title(self.sessions, self.chat_id, None)
            titles.schedule_title(self.sessions, self.chat_id, None)
            await asyncio.wait_for(waiting.wait(), 2)
            self.assertEqual(len(titles._tasks), 1)
            task = titles._tasks[self.chat_id]
            finish.set()
            await task
        self.assertEqual((await self.read()).title, '学习网站推荐')

    async def test_model_request_is_short_uses_same_connection_and_validates_output(self):
        seen = []
        def handler(request):
            seen.append(json.loads(request.content))
            self.assertEqual(request.headers['authorization'], 'Bearer test-only-key')
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '标题：Python入门学习'}}]})
        client = httpx.AsyncClient
        with patch.object(titles.httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)):
            title = await titles.generate_title('如何学Python', '从变量和函数开始', ModelConnection('https://model.test/v1', 'test-model', 60, SecretStr('test-only-key')))
        self.assertEqual(title, 'Python入门学习')
        self.assertNotIn('tools', seen[0])
        self.assertFalse(seen[0]['stream'])
        self.assertLessEqual(seen[0]['max_tokens'], 1024)
        with self.assertRaises(ValueError):
            titles.clean_title('说明\n这是标题')
