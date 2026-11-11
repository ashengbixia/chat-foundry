import json
import unittest
from unittest.mock import patch, AsyncMock
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from backend.app.api.knowledge import router
from backend.app.api.chats import router as chats_router
from backend.app.auth import get_current_user
from backend.app.core.database import create_tables, get_db
from backend.app.models.user import User
from backend.app.models.file import File
from backend.app.models.chat import Chat, Message
from backend.app.models.knowledge import KnowledgeBaseFile
from backend.app.repositories.knowledge import resolve_knowledge_scope
from backend.app.repositories.chat import create_message
from backend.app.repositories.file import delete_file_rows
from backend.app.chat import service
from backend.app.repositories.resources import resolve_chat_files
from backend.app.models.chat_file import ChatFile
from backend.app.tools import summaries
from backend.app.chat.history_sources import collect_history_sources
from backend.app.chat.reference_context import prepare_auto_reference, resolve_reference_scope
from backend.tests.index_fixture import mock_file_vectors
from types import SimpleNamespace


class KnowledgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        await create_tables(self.engine)
        async with self.sessions() as session:
            session.add_all([User(id="u1", username="one", name="One", password_hash="unused", role="user"), User(id="u2", username="two", name="Two", password_hash="unused", role="user")])
            await session.flush()
            session.add_all([Chat(id="chat", user_id="u1", title="问答"), File(id="f1", user_id="u1", filename="专业资料.txt", storage_path="unused.txt", status="completed", text_content="FT-204表示泵出口流量。"), File(id="f2", user_id="u1", filename="临时.txt", storage_path="unused2.txt", status="completed", text_content="临时附件"), File(id="secret", user_id="u2", filename="秘密.txt", storage_path="secret.txt", status="completed", text_content="其他账号资料")])
            await session.commit()
        self.app = FastAPI()
        self.app.include_router(router)
        self.app.include_router(chats_router)
        async def database():
            async with self.sessions() as session:
                yield session
        self.user_id = "u1"
        async def current_user():
            async with self.sessions() as session:
                return await session.get(User, self.user_id)
        self.app.dependency_overrides[get_db] = database
        self.app.dependency_overrides[get_current_user] = current_user
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://knowledge")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()

    async def make_base(self):
        result = await self.client.post("/api/knowledge", json={"name": "泵维护", "description": "资料测试"})
        self.assertEqual(result.status_code, 201, result.text)
        return result.json()["id"]

    async def bind(self, knowledge_id):
        response = await self.client.put("/api/chats/chat/knowledge", json={"knowledge_base_ids": [knowledge_id]})
        self.assertEqual(response.status_code, 200, response.text)

    async def test_bulk_index_only_queues_outdated_owned_members(self):
        mock_file_vectors(self, self.sessions)
        base = await self.make_base()
        await self.client.post(f'/api/knowledge/{base}/files', json={'file_ids': ['f1', 'f2']})
        url = f'/api/knowledge/{base}/index'
        first = await self.client.post(url)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(first.json()['queued'], 2)
        second = (await self.client.post(url)).json()
        self.assertEqual((second['queued'], second['ready']), (0, 2))
        self.user_id = 'u2'
        self.assertEqual((await self.client.post(url)).status_code, 404)

    async def test_new_attachment_overview_and_followup_scope(self):
        async with self.sessions() as session:
            file = await session.get(File, 'f1')
            file.text_content = '长文件内容' * 1500
            await session.commit()
            users = [SimpleNamespace(role='user', content='里面有什么内容', attachments=[SimpleNamespace(file_id='f1')])]
            automatic = await prepare_auto_reference(session, users[0].content, {'f1', 'f2'}, [{'role': 'user', 'content': users[0].content}], [], None, AsyncMock(return_value={'f1', 'f2'}), history=users)
            self.assertEqual(automatic[0][0], 'summarize_uploaded_files')
            self.assertEqual(automatic[0][1]['file_ids'], ['f1'])
        users = [SimpleNamespace(role='user', content='绿萝怎么浇水', attachments=[]), SimpleNamespace(role='user', content='那另一个呢', attachments=[])]
        targets, query = resolve_reference_scope('那另一个呢', {'f1', 'f2'}, [], users)
        self.assertEqual(targets, {'f1', 'f2'})
        self.assertIn('绿萝怎么浇水', query)

    async def test_membership_reuse_restore_and_delete_without_deleting_source(self):
        base = await self.make_base()
        url = f"/api/knowledge/{base}/files"
        rejected = await self.client.post(url, json={"file_ids": ["f1", "secret"]})
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual((await self.client.get(url)).json(), [])
        for _ in range(2):
            self.assertEqual((await self.client.post(url, json={"file_ids": ["f1", "f1"]})).status_code, 204)
        self.assertEqual((await self.client.get("/api/knowledge")).json()[0]["file_count"], 1)
        await self.bind(base)
        restored = (await self.client.get("/api/chats/chat")).json()
        self.assertEqual(restored["knowledge_bases"], [{"id": base, "name": "泵维护"}])
        async with self.sessions() as session:
            self.assertEqual((await resolve_knowledge_scope(session, "chat"))[1], {"f1"})
        await self.client.delete(url + "/f1")
        async with self.sessions() as session:
            self.assertEqual((await resolve_knowledge_scope(session, "chat"))[1], set())
            self.assertIsNotNone(await session.get(File, "f1"))
        await self.client.post(url, json={"file_ids": ["f1"]})
        await self.client.delete(f"/api/knowledge/{base}")
        self.assertEqual((await self.client.get("/api/chats/chat")).json()["knowledge_bases"], [])
        async with self.sessions() as session:
            self.assertEqual((await session.get(File, "f1")).text_content, "FT-204表示泵出口流量。")

    async def test_other_account_cannot_read_modify_or_bind_private_knowledge(self):
        base = await self.make_base()
        self.user_id = "u2"
        self.assertEqual((await self.client.get("/api/knowledge")).json(), [])
        for method, path, body in [("GET", f"/api/knowledge/{base}/files", None), ("PATCH", f"/api/knowledge/{base}", {"name": "越权"}), ("DELETE", f"/api/knowledge/{base}", None), ("POST", f"/api/knowledge/{base}/files", {"file_ids": ["secret"]}), ("GET", "/api/chats/chat/knowledge", None), ("PUT", "/api/chats/chat/knowledge", {"knowledge_base_ids": [base]})]:
            response = await self.client.request(method, path, **({"json": body} if body is not None else {}))
            self.assertEqual(response.status_code, 404, response.text)
        response = await self.client.post("/api/chats", json={"title": "越权绑定", "knowledge_base_ids": [base]})
        self.assertEqual(response.status_code, 400)

    async def test_first_chat_selection_and_source_deletion_cleanup(self):
        base = await self.make_base()
        await self.client.post(f"/api/knowledge/{base}/files", json={"file_ids": ["f1"]})
        response = await self.client.post("/api/chats", json={"title": "首次发送", "knowledge_base_ids": [base]})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["knowledge_bases"][0]["id"], base)
        async with self.sessions() as session:
            await delete_file_rows(session, "f1")
            await session.commit()
            self.assertEqual(list((await session.execute(select(KnowledgeBaseFile))).scalars()), [])
        self.assertEqual((await self.client.get("/api/knowledge")).json()[0]["file_count"], 0)

    async def test_tool_citation_persists_and_removed_knowledge_revokes_future_reads(self):
        base = await self.make_base()
        await self.client.post(f"/api/knowledge/{base}/files", json={"file_ids": ["f1"]})
        await self.bind(base)
        async with self.sessions() as session:
            await create_message(session, "chat", "user", "旧附件", file_ids=["f2"])
            await create_message(session, "chat", "user", "FT-204什么意思")
        await self.client.delete('/api/chats/chat/files/f2')  # 只有明确移出会话，普通附件才停止授权后续读取
        requests = []
        async def model(messages, tools):
            requests.append(messages.copy())
            if len(requests) == 1:
                self.assertIn("泵维护", messages[0]["content"])
                yield {"type": "tool_call_delta", "tool_calls": [{"index": 0, "id": "read1", "function": {"name": "read_uploaded_file", "arguments": '{"file_id":"f1"}'}}]}
            elif len(requests) == 2:
                await self.client.delete(f"/api/knowledge/{base}/files/f1")
                yield {"type": "tool_call_delta", "tool_calls": [{"index": 0, "id": "read2", "function": {"name": "read_uploaded_file", "arguments": '{"file_id":"f1"}'}}]}
            elif len(requests) == 3:
                self.assertIn('error', json.loads(messages[-1]["content"]))
                yield {"type": "tool_call_delta", "tool_calls": [{"index": 0, "id": "read3", "function": {"name": "read_uploaded_file", "arguments": '{"file_id":"f2"}'}}]}
            else:
                self.assertIn('error', json.loads(messages[-1]["content"]))
                yield {"type": "text_delta", "text": "泵出口流量[1]"}
        with patch.object(service, "prepare_auto_reference", AsyncMock(return_value=[])), patch.object(service, "stream_model_events", model):
            async with self.sessions() as session:
                events = [item async for item in service.stream_chat_history_events(session, "chat")]
        self.assertTrue(events[-1].startswith("event: done"), events)
        async with self.sessions() as session:
            answer = (await session.execute(select(Message).where(Message.role == "assistant"))).scalar_one()
            output = next(item for item in answer.output if item["type"] == "function_call_output")
            self.assertEqual(json.loads(output["output"])["citation_id"], 1)
        await self.client.delete(f"/api/knowledge/{base}")
        async with self.sessions() as session:
            self.assertEqual((await resolve_knowledge_scope(session, "chat"))[1], set())
            restored = await session.get(Message, answer.id)
            self.assertIn("FT-204表示泵出口流量", str(restored.output))

    async def test_attachment_survives_history_window_and_explicit_removal_does_not_delete_file(self):
        async with self.sessions() as session:
            await create_message(session, 'chat', 'user', '先看文件', file_ids=['f1'])
            for index in range(52):
                await create_message(session, 'chat', 'user', f'后续问题{index}')
            self.assertEqual(await resolve_chat_files(session, 'chat'), {'f1'})
        response = await self.client.get('/api/chats/chat')
        self.assertEqual(response.json()['files'][0]['file_id'], 'f1')
        self.user_id = 'u2'
        self.assertEqual((await self.client.delete('/api/chats/chat/files/f1')).status_code, 404)
        self.user_id = 'u1'
        self.assertEqual((await self.client.delete('/api/chats/chat/files/f1')).status_code, 204)
        async with self.sessions() as session:
            self.assertEqual(await resolve_chat_files(session, 'chat'), set())
            self.assertIsNotNone(await session.get(File, 'f1'))
            await create_message(session, 'chat', 'user', '重新选入', file_ids=['f1'])
            self.assertEqual(await resolve_chat_files(session, 'chat'), {'f1'})
            await delete_file_rows(session, 'f1')
            await session.commit()
            self.assertTrue((await session.get(ChatFile, ('chat', 'f1'))).deleted)
            self.assertEqual(await resolve_chat_files(session, 'chat'), set())

    async def test_auto_full_text_then_deleted_library_history_has_real_rebased_citation(self):
        base = await self.make_base()
        await self.client.post(f'/api/knowledge/{base}/files', json={'file_ids': ['f1']})
        await self.bind(base)
        async with self.sessions() as session:
            await create_message(session, 'chat', 'user', '请总结资料')
        async def first_model(messages, tools):
            source = json.loads(messages[-1]['content'])
            self.assertEqual(source['text_content'], 'FT-204表示泵出口流量。')
            self.assertFalse(source['has_more'])
            yield {'type': 'text_delta', 'text': '泵出口流量[1]'}
        with patch.object(service, 'stream_model_events', first_model):
            async with self.sessions() as session:
                events = [item async for item in service.stream_chat_history_events(session, 'chat')]
                first_id = (await session.execute(select(Message.id).where(Message.role == 'assistant'))).scalar_one()
        self.assertTrue(events[-1].startswith('event: done'), events)
        await self.client.delete(f'/api/knowledge/{base}')
        async with self.sessions() as session:
            await create_message(session, 'chat', 'user', '刚才读到的FT-204是什么')
        turns = []
        async def followup(messages, tools):
            turns.append(messages.copy())
            if len(turns) == 1:
                self.assertIn('read_history_sources', str(tools))
                self.assertNotIn('search_uploaded_files', str(tools))
                self.assertNotIn('text_content', str(messages))
                yield {'type': 'tool_call_delta', 'tool_calls': [{'index': 0, 'id': 'history-1', 'function': {'name': 'read_history_sources', 'arguments': '{"source_ids":["H1"]}'}}]}
            else:
                latest = json.loads(messages[-1]['content'])
                if not latest['chunks']:
                    self.assertEqual(len(latest['omitted_duplicate_chunks']), 1)
                source = next(data['chunks'][0] for item in messages if item['role'] == 'tool' for data in [json.loads(item['content'])] if data.get('chunks'))
                self.assertTrue(source['historical'])
                self.assertEqual(source['origin_message_id'], first_id)
                self.assertEqual(source['citation_id'], 1)
                yield {'type': 'text_delta', 'text': '依据历史快照，是泵出口流量[1]。'}
        with patch.object(service, 'stream_model_events', followup):
            async with self.sessions() as session:
                events = [item async for item in service.stream_chat_history_events(session, 'chat')]
        self.assertTrue(events[-1].startswith('event: done'), events)
        async with self.sessions() as session:
            answers = list((await session.execute(select(Message).where(Message.role == 'assistant').order_by(Message.sequence))).scalars())
            pool = collect_history_sources(answers)
            self.assertEqual(pool['H1']['origin_message_id'], first_id)
            self.assertEqual(pool['H1']['content'], 'FT-204表示泵出口流量。')

    async def test_long_summary_reads_pages_and_reports_incomplete_after_call_cap(self):
        for size, expected_calls, complete in [(9000, 3, True), (40000, 8, False)]:
            async with self.sessions() as session:
                file = await session.get(File, 'f1')
                file.text_content = '养护条件。' * (size // 5)
                await session.commit()
            calls = []
            async def summarize(messages, connection=None):
                calls.append(messages)
                yield '本段介绍养护条件。'
            async with self.sessions() as session:
                with patch.object(summaries, 'stream_reply', summarize):
                    result = await summaries.summarize_uploaded_files(session, {'f1'}, focus='总结全文')
            self.assertEqual(len(calls), expected_calls)
            self.assertEqual(result['complete'], complete)
            self.assertEqual(sum(len(item['content']) for item in result['chunks']), min(size, 32000))
            self.assertEqual(result['coverage'][0]['read_chars'], min(size, 32000))
            if complete:
                async with self.sessions() as session:
                    await create_message(session, 'chat', 'user', '请总结全文', file_ids=['f1'])
                async def final_answer(messages, tools):
                    data = json.loads(messages[-1]['content'])
                    self.assertTrue(data['complete'])
                    self.assertEqual(data['complete_files'], 1)
                    yield {'type': 'text_delta', 'text': '已按各段归纳全文[1]。'}
                with patch.object(summaries, 'stream_reply', summarize), patch.object(service, 'stream_model_events', final_answer):
                    async with self.sessions() as session:
                        events = [item async for item in service.stream_chat_history_events(session, 'chat')]
                        saved = (await session.execute(select(Message).where(Message.role == 'assistant'))).scalar_one()
                        tool_result = next(item for item in saved.output if item['type'] == 'function_call_output')
                        data = json.loads(tool_result['output'])
                        self.assertEqual(sum(len(item['content']) for item in data['chunks']), size)
                        self.assertTrue(all('citation_id' in item for item in data['chunks']))
                self.assertTrue(events[-1].startswith('event: done'), events)
