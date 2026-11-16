import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
import httpx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from backend.app.core.database import create_tables, get_db
from backend.app.models.chat import Chat, Message
from backend.app.models.user import User
from backend.app.models.memory import UserToolSetting
from backend.app.models.model_setting import UserModelSetting
from backend.app.auth import get_current_user
from backend.app.api.chats import router
from backend.app.api.follow_ups import router as follow_router
from backend.app.api.model_settings import get_user_model_connection
from backend.app.repositories.chat import create_message, get_latest_message
from backend.app.chat import service, follow_ups
from backend.app.chat.questions import pending_question
from backend.app.tools.registry import execute_tool
from backend.app.tools.runtime import ToolContext
from backend.app.llm.connection import ModelConnection

QUESTIONS = [{'id': 'scope', 'question': '你希望覆盖哪种范围？', 'options': [{'label': '基础', 'description': '先覆盖常用流程'}, {'label': '完整', 'description': '覆盖更多场景'}], 'allow_other': True}]


def call(name, arguments, call_id, index=0):
    return {'type': 'tool_call_delta', 'tool_calls': [{'index': index, 'id': call_id, 'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}]}


class UserInteractionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        await create_tables(self.engine)
        async with self.sessions() as session:
            session.add_all([User(id='u', username='one', name='One', password_hash='unused'), User(id='v', username='two', name='Two', password_hash='unused')])
            await session.flush()
            session.add(Chat(id='chat', user_id='u'))
            session.add(UserModelSetting(user_id='u', provider='custom', base_url='https://not-called.test/v1', model='sample', timeout=30, key_source='none'))
            await session.commit()
            await create_message(session, 'chat', 'user', '帮我规划一个学习任务')
        self.connection = ModelConnection('https://not-called.test/v1', 'sample', 30, SecretStr('test-only'))
        self.app = FastAPI(); self.app.include_router(router); self.app.include_router(follow_router)
        async def db():
            async with self.sessions() as session:
                yield session
        self.app.dependency_overrides[get_db] = db
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id='u', role='user')
        self.app.dependency_overrides[get_user_model_connection] = lambda: self.connection
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url='http://interaction')

    async def asyncTearDown(self):
        await follow_ups.close_jobs()
        await self.client.aclose(); await self.engine.dispose()

    async def ask(self):
        requests, executions = [], []
        actual = service.execute_tool
        async def execute(name, arguments, **kwargs):
            executions.append(name)
            return await actual(name, arguments, **kwargs)
        async def model(messages, tools, connection=None):
            requests.append(messages)
            yield call('delegate_task', {'task': '应暂缓'}, 'deferred', 0)
            yield call('ask_user', {'questions': QUESTIONS}, 'ask', 1)
        with patch.object(service, 'stream_model_events', model), patch.object(service, 'execute_tool', execute):
            async with self.sessions() as session:
                events = [event async for event in service.stream_chat_history_events(session, 'chat', self.connection)]
                message = await get_latest_message(session, 'chat')
                result = message.id
                self.assertEqual(message.status, 'completed')
                self.assertIsNotNone(pending_question(message.output))
                deferred = next(item for item in message.output if item.get('type') == 'function_call_output' and item['call_id'] == 'deferred')
                self.assertEqual(json.loads(deferred['output'])['status'], 'deferred')
        self.assertEqual(len(requests), 1)
        self.assertEqual(executions, ['ask_user'])
        self.assertTrue(events[-1].startswith('event: done'))
        return result

    async def test_question_persists_then_answer_resumes_original_tool_history(self):
        message_id = await self.ask()
        reply = {'message_id': message_id, 'call_id': 'ask', 'answers': {'scope': '完整'}, 'cancelled': False}
        response = await self.client.post('/api/chats/chat/messages', json={'content': '我选择完整范围', 'clarification': reply})
        self.assertEqual(response.status_code, 201, response.text)
        async def model(messages, tools, connection=None):
            results = [json.loads(item['content']) for item in messages if item['role'] == 'tool']
            self.assertTrue(any(item.get('answers', {}).get('scope') == '完整' for item in results))
            yield {'type': 'text_delta', 'text': '按完整范围继续。'}
        with patch.object(service, 'stream_model_events', model):
            async with self.sessions() as session:
                events = [item async for item in service.stream_chat_history_events(session, 'chat', self.connection)]
        self.assertTrue(events[-1].startswith('event: done'))
        from backend.app.chat.titles import title_round
        async with self.sessions() as session:
            self.assertEqual((await title_round(session, 'chat'))[0], '帮我规划一个学习任务')
        duplicate = await self.client.post('/api/chats/chat/messages', json={'content': '重复提交', 'clarification': reply})
        self.assertEqual(duplicate.status_code, 409)
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id='v', role='user')
        self.assertEqual((await self.client.post('/api/chats/chat/messages', json={'content': '越权', 'clarification': reply})).status_code, 404)

    async def test_plain_answer_and_skip_are_real_input_not_default_selection(self):
        message_id = await self.ask()
        bad = await self.client.post('/api/chats/chat/messages', json={'content': '错误答案', 'clarification': {'message_id': message_id, 'call_id': 'ask', 'answers': {}}})
        self.assertEqual(bad.status_code, 409)
        response = await self.client.post('/api/chats/chat/messages', json={'content': '我想自己描述范围'})
        self.assertEqual(response.status_code, 201)
        async with self.sessions() as session:
            row = await session.get(Message, message_id)
            data = json.loads(next(item['output'] for item in row.output if item.get('type') == 'function_call_output' and item['call_id'] == 'ask'))
            self.assertEqual(data['answers'], {'free_text': '我想自己描述范围'})
        with self.assertRaises(ValueError):
            await execute_tool('ask_user', json.dumps({'questions': QUESTIONS}), context=ToolContext(is_subagent=True))
        second = await self.ask()
        reply = {'message_id': second, 'call_id': 'ask', 'answers': {}, 'cancelled': True}
        self.assertEqual((await self.client.post('/api/chats/chat/messages', json={'content': '跳过并继续', 'clarification': reply})).status_code, 201)
        async def continue_without_asking(messages, tools, connection=None):
            self.assertNotIn('ask_user', {item['function']['name'] for item in tools or []})
            yield {'type': 'text_delta', 'text': '先给出基于已知信息的方案。'}
        with patch.object(service, 'stream_model_events', continue_without_asking):
            async with self.sessions() as session:
                events = [item async for item in service.stream_chat_history_events(session, 'chat', self.connection)]
        self.assertTrue(events[-1].startswith('event: done'))

    async def test_followups_background_cached_and_never_change_answer(self):
        async with self.sessions() as session:
            answer = await create_message(session, 'chat', 'assistant', '学习计划正文')
            answer_id = answer.id
        started, release = asyncio.Event(), asyncio.Event()
        async def generate(context, connection):
            started.set(); await release.wait()
            return ['第一周如何安排？', '怎样检查学习效果？']
        with patch.object(follow_ups, 'generate', generate):
            response = await self.client.post(f'/api/chats/chat/messages/{answer_id}/follow-ups')
            self.assertEqual(response.status_code, 200, response.text)
            state = response.json()
            self.assertEqual(state['status'], 'generating')
            await asyncio.wait_for(started.wait(), 2)
            async with self.sessions() as session:
                self.assertEqual((await follow_ups.start(session, self.sessions, 'chat', answer_id, 'u', self.connection))['status'], 'generating')
                self.assertEqual((await session.get(Message, answer_id)).content, '学习计划正文')
            job = follow_ups._jobs[answer_id]; release.set(); await job
        async with self.sessions() as session:
            row, state = await follow_ups.get_state(session, 'chat', answer_id, 'u')
            self.assertEqual(state['questions'][0], '第一周如何安排？')
            self.assertEqual(row.status, 'completed')
            self.assertEqual((await follow_ups.start(session, self.sessions, 'chat', answer_id, 'u', self.connection))['status'], 'completed')
            with self.assertRaises(ValueError):
                await follow_ups.get_state(session, 'chat', answer_id, 'v')

    async def test_followup_failure_or_switch_off_does_not_fail_answer(self):
        async with self.sessions() as session:
            row = await create_message(session, 'chat', 'assistant', '正常回答')
            message_id = row.id
        with patch.object(follow_ups, 'generate', AsyncMock(side_effect=ValueError('模拟接口失败'))):
            async with self.sessions() as session:
                await follow_ups.start(session, self.sessions, 'chat', message_id, 'u', self.connection)
            await follow_ups._jobs[message_id]
        async with self.sessions() as session:
            row, state = await follow_ups.get_state(session, 'chat', message_id, 'u')
            self.assertEqual(state['status'], 'failed'); self.assertEqual(row.status, 'completed')
            session.add(UserToolSetting(user_id='u', follow_ups_enabled=False)); await session.commit()
        async with self.sessions() as session:
            self.assertEqual((await follow_ups.get_state(session, 'chat', message_id, 'u'))[1]['status'], 'disabled')
        self.assertEqual(follow_ups.clean_questions({'follow_ups': ['重复问题', '重复问题', '有效问题？', '']}, '重复问题'), ['有效问题？'])

    async def test_recommendation_request_never_offers_business_tools(self):
        seen = []
        def handle(request):
            payload = json.loads(request.content)
            seen.append(payload)
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"follow_ups":["第一周怎么安排？","第一周怎么安排？"]}'}}]})
        with patch.object(follow_ups, 'model_client', lambda *a, **kw: httpx.AsyncClient(transport=httpx.MockTransport(handle))):
            result = await follow_ups.generate([{'role': 'user', 'content': '学习计划'}, {'role': 'assistant', 'content': '先学习基础'}], self.connection)
        self.assertEqual(result, ['第一周怎么安排？'])
        self.assertNotIn('tools', seen[0])
        self.assertFalse(seen[0]['stream'])
