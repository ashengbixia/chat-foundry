import json
import unittest
from unittest.mock import patch, AsyncMock
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from backend.app.auth import get_current_user
from backend.app.core.database import create_tables, get_db
from backend.app.models.user import User
from backend.app.models.chat import Chat, Message
from backend.app.models.file import File
from backend.app.models.knowledge import KnowledgeBase, KnowledgeBaseFile
from backend.app.models.memory import UserToolSetting
from backend.app.api.memories import router
from backend.app.tools.registry import execute_tool, get_available_tools
from backend.app.tools.runtime import ToolContext, registry
from backend.app.chat.citations import add_citation_ids
from backend.app.chat.history_sources import collect_history_sources
from backend.app.tools.account import load_features
from backend.app.tools import workspace
from types import SimpleNamespace


class AccountToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        await create_tables(self.engine)
        async with self.sessions() as session:
            session.add_all([User(id='u', username='one', name='One', password_hash='unused'), User(id='v', username='two', name='Two', password_hash='unused')])
            await session.flush()
            session.add_all([Chat(id='current', user_id='u'), Chat(id='old', user_id='u', title='旧项目'), Chat(id='secret', user_id='v', title='旧项目'),
                File(id='f', user_id='u', filename='规则.txt', storage_path='not-read', text_content='第一行\nFT-204是流量\n第三行\n', status='completed'),
                File(id='standalone', user_id='u', filename='独立规则.txt', storage_path='not-read', text_content='独立原文', status='completed'),
                File(id='s', user_id='v', filename='秘密.txt', storage_path='not-read', text_content='FT-204秘密', status='completed'),
                KnowledgeBase(id='k', user_id='u', name='养护规则', description='绿植知识'), KnowledgeBase(id='private', user_id='v', name='养护规则', description='秘密'),
                UserToolSetting(user_id='u', memory_enabled=True)])
            await session.flush()
            session.add_all([KnowledgeBaseFile(knowledge_base_id='k', file_id='f'), KnowledgeBaseFile(knowledge_base_id='private', file_id='s'),
                Message(chat_id='old', sequence=1, role='user', content='过去的需求'), Message(chat_id='old', sequence=2, role='assistant', content='甲' * 18000),
                Message(chat_id='secret', sequence=1, role='user', content='过去的需求')])
            await session.commit()
        self.app = FastAPI()
        self.app.include_router(router)
        async def db():
            async with self.sessions() as session:
                yield session
        self.app.dependency_overrides[get_db] = db
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id='u', role='user')
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()

    async def context(self, session, files=None):
        return ToolContext(session=session, chat_id='current', account_id='u', account_features=await load_features(session, 'u'), allowed_file_ids=set(files or []), resource_tools=True)

    async def call(self, ctx, name, **values):
        return json.loads(await execute_tool(name, json.dumps(values, ensure_ascii=False), context=ctx))

    async def test_discovery_bounds_schema_and_rechecks_disabled_features(self):
        async with self.sessions() as session:
            ctx = await self.context(session)
            before = {tool['function']['name'] for tool in get_available_tools(set(), context=ctx)}
            self.assertNotIn('add_memory', before)
            self.assertIn('search_tools', before)
            result = await self.call(ctx, 'search_tools', query='add_memory', count=1)
            self.assertEqual(result['tools'][0]['name'], 'add_memory')
            self.assertIn('add_memory', {tool['function']['name'] for tool in get_available_tools(set(), context=ctx)})
            found = await self.call(ctx, 'search_tools', query='按行读取', count=3)
            self.assertIn('view_knowledge_file', {item['name'] for item in found['tools']})
            row = await session.get(UserToolSetting, 'u'); row.memory_enabled = False; await session.commit()
            with self.assertRaises(ValueError):
                await self.call(ctx, 'add_memory', content='持久偏好')

    async def test_memory_cross_chat_persistence_revision_and_owner_isolation(self):
        async with self.sessions() as session:
            ctx = await self.context(session)
            saved = await self.call(ctx, 'add_memory', content='喜欢简洁回答', path='/偏好')
            changed = await self.call(ctx, 'replace_memory_content', memory_id=saved['id'], revision=1, old_content='简洁', new_content='中文简洁')
            self.assertEqual(changed['revision'], 2)
            with self.assertRaises(ValueError):
                await self.call(ctx, 'delete_memory', memory_id=saved['id'], revision=1)
        async with self.sessions() as session:
            ctx = await self.context(session); ctx.chat_id = 'old'
            self.assertEqual((await self.call(ctx, 'search_memories', query='中文'))['memories'][0]['path'], '/偏好')
            ctx.account_id = 'v'; ctx.account_features = {'memory_enabled': True}
            session.add(UserToolSetting(user_id='v', memory_enabled=True)); await session.commit()
            with self.assertRaises(ValueError):
                await self.call(ctx, 'delete_memory', memory_id=saved['id'], revision=2)

    async def test_account_tools_never_widen_child_scope(self):
        async with self.sessions() as session:
            ctx = await self.context(session, ['f']); ctx.is_subagent = True
            for name, args in [('search_chats', {'query': '需求'}), ('list_knowledge_bases', {}), ('add_memory', {'content': '不应保存'})]:
                with self.assertRaises(ValueError):
                    await self.call(ctx, name, **args)
            result = await self.call(ctx, 'view_file', file_id='f', start_line=2, end_line=2)
            self.assertEqual(result['chunks'][0]['start_offset'], 4)

    async def test_history_search_and_long_message_paging_never_leak_other_account(self):
        async with self.sessions() as session:
            ctx = await self.context(session)
            result = await self.call(ctx, 'search_chats', query='需求')
            self.assertEqual([item['chat_id'] for item in result['chats']], ['old'])
            page = await self.call(ctx, 'view_chat', target_chat_id='old')
            self.assertTrue(page['messages'][1]['truncated'])
            text = await self.call(ctx, 'read_chat_message', target_chat_id='old', sequence=2, offset=4000, max_chars=12000)
            self.assertEqual(len(text['content']), 12000)
            self.assertEqual(text['next_offset'], 16000)
            with self.assertRaises(ValueError):
                await self.call(ctx, 'view_chat', target_chat_id='secret')

    async def test_discovered_knowledge_read_owns_its_sources_and_citations(self):
        async with self.sessions() as session:
            ctx = await self.context(session)
            self.assertEqual((await self.call(ctx, 'list_knowledge_bases'))['knowledge_bases'][0]['id'], 'k')
            result = await self.call(ctx, 'view_knowledge_file', file_id='f', start_line=2, end_line=2, line_numbers=True)
            numbered = add_citation_ids('view_knowledge_file', json.dumps(result), {}, set())
            data = json.loads(numbered)
            self.assertEqual(data['chunks'][0]['citation_id'], 1)
            self.assertEqual(data['chunks'][0]['content'], 'FT-204是流量\n')
            self.assertIn('2:', data['numbered_text'])
            history = SimpleNamespace(role='assistant', status='completed', id='answer', output=[{'type': 'function_call', 'call_id': 'c', 'name': 'view_knowledge_file'}, {'type': 'function_call_output', 'call_id': 'c', 'output': numbered}])
            self.assertEqual(collect_history_sources([history])['H1']['content'], 'FT-204是流量\n')
            with self.assertRaises(ValueError):
                await self.call(ctx, 'view_knowledge_file', file_id='s')
            from backend.app.rag import embedding
            with patch.object(embedding, 'split_for_embedding', AsyncMock(return_value=[{'content': '养护规则'}])), patch.object(embedding, 'embed_texts', AsyncMock(return_value=[[1.0, 0.0]])), patch.object(embedding, 'embed_query', AsyncMock(return_value=[1.0, 0.0])):
                matches = await self.call(ctx, 'query_knowledge_bases', query='绿植')
            self.assertEqual(matches['knowledge_bases'][0]['id'], 'k')

    async def test_exact_regex_search_and_virtual_commands_are_bounded(self):
        async with self.sessions() as session:
            ctx = await self.context(session, ['f'])
            result = await self.call(ctx, 'grep_chat_files', pattern='FT-\\d+', use_regex=True)
            self.assertEqual(result['chunks'][0]['line_number'], 2)
            self.assertEqual((await self.call(ctx, 'kb_exec', command='sed 2,2p f', knowledge_id='k'))['chunks'][0]['content'], 'FT-204是流量\n')
            for command in ['cat s', 'cat f | powershell', 'rm f', 'cat C:/Windows/system.ini']:
                with self.assertRaises(ValueError):
                    await self.call(ctx, 'kb_exec', command=command, knowledge_id='k')
            with self.assertRaises(ValueError):
                await self.call(ctx, 'grep_chat_files', pattern='[', use_regex=True)
            row = await session.get(File, 'f')
            row.text_content = ('匹配' + '甲' * 1998 + '\n') * 20
            await session.commit()
            page = await self.call(ctx, 'grep_chat_files', pattern='匹配', file_id='f', count=20)
            self.assertTrue(page['has_more'])
            self.assertEqual(page['next_offset'], len(page['chunks']))
            self.assertLessEqual(sum(len(item['content']) for item in page['chunks']), 18000)

    async def test_memory_management_api_defaults_catalog_and_stale_revision(self):
        created = (await self.client.post('/api/memories', json={'content': '管理页面新增'})).json()
        self.assertEqual((await self.client.get('/api/memories')).json()['memories'][0]['id'], created['id'])
        self.assertEqual((await self.client.put('/api/memories/' + created['id'], json={'content': '新内容', 'revision': 1})).status_code, 200)
        self.assertEqual((await self.client.delete('/api/memories/' + created['id'] + '?revision=1')).status_code, 409)
        self.assertEqual((await self.client.delete('/api/memories/' + created['id'] + '?revision=2')).status_code, 204)
        catalog = (await self.client.get('/api/tools')).json()
        self.assertEqual(len(catalog['tools']), len(registry._tools))
        self.assertEqual((await self.client.get('/api/tools?chat_id=secret')).status_code, 404)
        self.app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id='v', role='user')
        self.assertFalse((await self.client.get('/api/settings/tools')).json()['memory_enabled'])

    async def test_chat_engine_discovers_then_executes_account_tool(self):
        from backend.app.chat import service
        from backend.app.repositories.chat import create_message
        requests = []
        async def model(messages, tools, connection=None):
            names = {item['function']['name'] for item in tools or []}
            requests.append(names)
            if len(requests) == 1:
                self.assertNotIn('search_chats', names)
                yield {'type': 'tool_call_delta', 'tool_calls': [{'index': 0, 'id': 'discover', 'function': {'name': 'search_tools', 'arguments': '{"query":"search_chats","count":1}'}}]}
            elif len(requests) == 2:
                self.assertIn('search_chats', names)
                yield {'type': 'tool_call_delta', 'tool_calls': [{'index': 0, 'id': 'history', 'function': {'name': 'search_chats', 'arguments': '{"query":"需求"}'}}]}
            else:
                result = json.loads(messages[-1]['content'])
                self.assertEqual([item['chat_id'] for item in result['chats']], ['old'])
                yield {'type': 'text_delta', 'text': '找到了旧项目会话。'}
        async with self.sessions() as session:
            await create_message(session, 'current', 'user', '查找以前讨论需求的会话')
            with patch.object(service, 'stream_model_events', model):
                events = [event async for event in service.stream_chat_history_events(session, 'current')]
        self.assertEqual(len(requests), 3)
        self.assertTrue(events[-1].startswith('event: done'))

    async def test_resource_catalog_visible_before_answer_and_read_tools_directly_offered(self):
        from backend.app.chat import service
        from backend.app.repositories.chat import create_message
        seen = []
        async def model(messages, tools, connection=None):
            names = {item['function']['name'] for item in tools or []}
            seen.append(messages)
            self.assertTrue({'query_knowledge_files', 'search_knowledge_files', 'view_knowledge_file', 'search_files', 'view_account_file'} <= names)
            prompt = messages[0]['content']
            self.assertIn('养护规则', prompt)
            self.assertIn('规则.txt', prompt)
            self.assertNotIn('秘密.txt', prompt)
            self.assertNotIn('FT-204是流量', prompt)
            self.assertIn('不能仅因自己知道常识而跳过相关资料', prompt)
            if len(seen) == 1:
                yield {'type': 'tool_call_delta', 'tool_calls': [{'index': 0, 'id': 'read', 'function': {'name': 'view_knowledge_file', 'arguments': '{"file_id":"f"}'}}]}
            else:
                self.assertIn('FT-204是流量', messages[-1]['content'])
                yield {'type': 'text_delta', 'text': '依据资料，FT-204是流量[1]。'}
        async with self.sessions() as session:
            await create_message(session, 'current', 'user', '介绍一下相关规则')
            with patch.object(service, 'stream_model_events', model):
                events = [event async for event in service.stream_chat_history_events(session, 'current')]
        self.assertEqual(len(seen), 2)
        self.assertTrue(events[-1].startswith('event: done'))

    async def test_other_uploaded_files_have_separate_access_without_chat_attachment(self):
        from backend.app.chat.resource_discovery import add_resource_catalog
        async with self.sessions() as session:
            ctx = await self.context(session)
            system = {'role': 'system', 'content': ''}
            await add_resource_catalog(ctx, system)
            names = {item['function']['name'] for item in get_available_tools(set(), context=ctx)}
            self.assertIn('view_account_file', names)
            found = await self.call(ctx, 'search_files', query='规则')
            self.assertEqual([item['file_id'] for item in found['files']], ['standalone'])
            result = await self.call(ctx, 'view_account_file', file_id='standalone')
            self.assertEqual(ctx.allowed_file_ids, set())
            numbered = json.loads(add_citation_ids('view_account_file', json.dumps(result), {}, set()))
            self.assertEqual(numbered['chunks'][0]['citation_id'], 1)
            with self.assertRaises(ValueError):
                await self.call(ctx, 'view_account_file', file_id='s')
            with self.assertRaises(ValueError):
                await self.call(ctx, 'view_account_file', file_id='f')
            settings = await session.get(UserToolSetting, 'u')
            settings.files_enabled = False; settings.knowledge_enabled = False
            await session.commit()
            with self.assertRaises(ValueError):
                await self.call(ctx, 'view_account_file', file_id='standalone')
            other = await self.context(session, ['f'])
            system = {'role': 'system', 'content': ''}
            await add_resource_catalog(other, system)
            self.assertNotIn('resource_catalog', other.state)
            self.assertNotIn('query_knowledge_files', {tool['function']['name'] for tool in get_available_tools({'f'}, context=other)})
            self.assertIn('text_content', await self.call(other, 'read_uploaded_file', file_id='f'))

    async def test_catalog_metadata_bounded_and_pageable_and_never_inherited_by_child(self):
        from backend.app.chat.resource_discovery import add_resource_catalog
        async with self.sessions() as session:
            for index in range(15):
                session.add(KnowledgeBase(id=f'kb-{index:02}', user_id='u', name='目录名' * 25, description='目录说明' * 200))
                session.add(File(id=f'file-{index:02}', user_id='u', filename=('文档名' * 60) + str(index), storage_path='not-read', text_content='正文不应出现在目录中', status='completed'))
            await session.commit()
            ctx = await self.context(session)
            await add_resource_catalog(ctx, {'role': 'system', 'content': ''})
            catalog = ctx.state['resource_catalog']
            self.assertLessEqual(len(json.dumps(catalog, ensure_ascii=False).encode('utf-8')), 3600)
            kb, files = catalog['knowledge'], catalog['uploaded_files']
            self.assertTrue(kb['has_more']); self.assertTrue(files['has_more'])
            next_kbs = await self.call(ctx, 'list_knowledge_bases', skip=kb['next_skip'])
            self.assertFalse({item['id'] for item in kb['knowledge_bases']} & {item['id'] for item in next_kbs['knowledge_bases']})
            next_files = await self.call(ctx, 'search_files', skip=files['next_skip'])
            self.assertFalse({item['file_id'] for item in files['files']} & {item['file_id'] for item in next_files['files']})
            ctx.is_subagent = True; ctx.state.clear()
            message = {'role': 'system', 'content': ''}
            await add_resource_catalog(ctx, message)
            self.assertEqual(message['content'], '')
            with self.assertRaises(ValueError):
                await self.call(ctx, 'view_account_file', file_id='f')
