"""真实Cookie、临时数据库与假向量，验证工作空间跨模块行为。"""
import json
import unittest
from unittest.mock import patch
from sqlalchemy import select
from backend.tests import test_model_settings as model_tests
from backend.app.api import workspace, branches, documents, knowledge, chats
from backend.app.models.chat import Message
from backend.app.models.model_setting import ModelServiceConnection
from backend.app.tools.runtime import ToolContext
from backend.app.tools.workspace import knowledge_file_ids


class WorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await model_tests.ModelSettingsTests.asyncSetUp(self)
        for router in (workspace.router, branches.router, documents.router, knowledge.router):
            self.app.include_router(router)

    async def asyncTearDown(self):
        await model_tests.ModelSettingsTests.asyncTearDown(self)

    async def uploaded(self, content=b'original evidence'):
        response = await self.admin.post('/api/files', files={'file': ('guide.txt', content, 'text/plain')})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()['id']

    async def test_connections_keep_encrypted_keys_profiles_and_isolate_users(self):
        saved = await self.admin.put(self.url, json=self.draft)
        self.assertEqual(saved.status_code, 200, saved.text)
        first = (await self.admin.get('/api/workspace/connections')).json()[0]
        created = await self.admin.post('/api/workspace/connections', json={**self.draft, 'name': 'Second', 'base_url': 'https://second.test/v1', 'api_key': 'second-secret', 'model': 'model-b', 'capabilities': {'tools': False}})
        self.assertEqual(created.status_code, 201, created.text)
        second = created.json()
        self.assertNotIn('second-secret', json.dumps(second))
        self.assertNotIn('encrypted_api_key', second)
        self.assertEqual((await self.other.get('/api/workspace/connections')).json(), [])
        self.assertEqual((await self.other.post(f'/api/workspace/connections/{second["id"]}/activate', json={})).status_code, 404)
        switched = await self.admin.post(f'/api/workspace/connections/{second["id"]}/activate', json={})
        self.assertFalse(switched.json()['config']['capabilities']['tools'])
        await self.admin.patch(self.url + '/model', json={'base_url': second['base_url'], 'model': 'model-c'})
        await self.admin.post(f'/api/workspace/connections/{first["id"]}/activate', json={})
        self.assertEqual((await self.admin.get(self.url)).json()['config']['model'], self.draft['model'])
        async with self.sessions() as session:
            row = await session.get(ModelServiceConnection, second['id'])
            self.assertEqual(row.model, 'model-c')
            self.assertNotEqual(row.encrypted_api_key, 'second-secret')
        self.assertEqual((await self.admin.post('/api/workspace/connections', json={**self.draft, 'name': 'empty', 'model': ''})).status_code, 400)

    async def test_assistant_pins_model_tools_prompt_and_knowledge_without_global_config(self):
        connection = (await self.admin.post('/api/workspace/connections', json={**self.draft, 'name': 'Private', 'capabilities': {'tools': False}})).json()
        base = (await self.admin.post('/api/knowledge', json={'name': 'Reference'})).json()
        assistant = await self.admin.post('/api/workspace/assistants', json={'name': 'Reader', 'system_prompt': 'Explain simply.', 'connection_id': connection['id'], 'tool_names': [], 'knowledge_ids': [base['id']]})
        self.assertEqual(assistant.status_code, 201, assistant.text)
        aid = assistant.json()['id']
        self.assertEqual((await self.other.post('/api/chats', json={'assistant_id': aid})).status_code, 404)
        chat = (await self.admin.post('/api/chats', json={'assistant_id': aid})).json()
        self.assertEqual(chat['assistant_id'], aid)
        self.assertEqual(chat['knowledge_bases'][0]['id'], base['id'])
        self.assertEqual((await self.admin.delete('/api/workspace/connections/' + connection['id'])).status_code, 409)
        await self.admin.delete(self.url)
        user_message = await self.admin.post(f'/api/chats/{chat["id"]}/messages', json={'content': 'hello'})
        self.assertEqual(user_message.status_code, 201, user_message.text)
        seen = []
        async def fake_stream(**kwargs):
            seen.append(kwargs['connection'])
            yield 'event: done\ndata: {}\n\n'
        with patch.object(chats, 'stream_chat_history_events', fake_stream), patch.object(chats, 'schedule_title'):
            result = await self.admin.post(f'/api/chats/{chat["id"]}/respond')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(seen[0].system_prompt, 'Explain simply.')
        self.assertEqual(seen[0].tool_names, frozenset())
        self.assertFalse(seen[0].capabilities.tools)
        await self.admin.delete('/api/workspace/assistants/' + aid)
        self.assertIsNone((await self.admin.get('/api/chats/' + chat['id'])).json()['assistant_id'])

    async def test_branch_edit_and_regeneration_preserve_original_evidence(self):
        fid = await self.uploaded()
        chat = (await self.admin.post('/api/chats', json={'title': 'Original'})).json()
        user = (await self.admin.post(f'/api/chats/{chat["id"]}/messages', json={'content': 'original question', 'file_ids': [fid]})).json()
        async with self.sessions() as session:
            answer = Message(chat_id=chat['id'], sequence=2, role='assistant', content='original answer', output=[{'type': 'context_sources', 'chunks': [{'file_id': fid, 'content': 'snapshot'}]}], status='completed')
            session.add(answer); await session.commit(); answer_id = answer.id
        future_file = await self.uploaded(b'future-only evidence')
        await self.admin.post(f'/api/chats/{chat["id"]}/messages', json={'content': 'future question', 'file_ids': [future_file]})
        url = f'/api/chats/{chat["id"]}/branches'
        edited = await self.admin.post(url, json={'message_id': user['id'], 'content': 'edited question'})
        self.assertEqual(edited.status_code, 201, edited.text)
        page = (await self.admin.get(f'/api/chats/{edited.json()["id"]}/messages')).json()['items']
        self.assertEqual([item['content'] for item in page], ['edited question'])
        self.assertNotEqual(page[0]['id'], user['id'])
        self.assertEqual(page[0]['attachments'][0]['file_id'], fid)
        self.assertEqual([item['file_id'] for item in edited.json()['files']], [fid])
        regenerated = await self.admin.post(url, json={'message_id': answer_id, 'regenerate': True})
        self.assertEqual(regenerated.status_code, 201, regenerated.text)
        full = await self.admin.post(url, json={'message_id': answer_id})
        copied = (await self.admin.get(f'/api/chats/{full.json()["id"]}/messages')).json()['items']
        self.assertEqual(copied[-1]['output'][0]['chunks'][0]['content'], 'snapshot')
        original = (await self.admin.get(f'/api/chats/{chat["id"]}/messages')).json()['items']
        self.assertEqual([item['content'] for item in original], ['original question', 'original answer', 'future question'])
        self.assertEqual(len((await self.admin.get(url)).json()), 4)
        self.assertEqual((await self.other.post(url, json={'message_id': user['id']})).status_code, 404)

    async def test_shared_knowledge_read_only_tool_scope_and_revocation(self):
        fid = await self.uploaded()
        base = (await self.admin.post('/api/knowledge', json={'name': 'Shared'})).json()['id']
        await self.admin.post(f'/api/knowledge/{base}/files', json={'file_ids': [fid]})
        self.assertEqual((await self.other.get(f'/api/files/{fid}/content')).status_code, 404)
        grant = await self.admin.post(f'/api/workspace/knowledge/{base}/shares', json={'username': self.other_user['username']})
        self.assertEqual(grant.status_code, 200, grant.text)
        catalog = (await self.other.get('/api/knowledge')).json()
        self.assertFalse(catalog[0]['can_edit'])
        self.assertEqual((await self.other.get(f'/api/knowledge/{base}/files')).json()[0]['id'], fid)
        self.assertEqual((await self.other.get(f'/api/files/{fid}/original')).content, b'original evidence')
        self.assertEqual((await self.other.delete(f'/api/files/{fid}')).status_code, 404)
        self.assertEqual((await self.other.patch(f'/api/knowledge/{base}', json={'name': 'stolen'})).status_code, 404)
        chat = (await self.other.post('/api/chats', json={'knowledge_base_ids': [base]})).json()
        async with self.sessions() as session:
            ctx = ToolContext(session=session, account_id=self.other_user['id'], account_features={'knowledge_enabled': True})
            self.assertEqual(await knowledge_file_ids(ctx, base), {fid})
        await self.admin.delete(f'/api/workspace/knowledge/{base}/shares/{self.other_user["id"]}')
        self.assertEqual((await self.other.get(f'/api/files/{fid}/original')).status_code, 404)
        self.assertEqual((await self.other.get('/api/chats/' + chat['id'])).json()['knowledge_bases'], [])
        async with self.sessions() as session:
            ctx = ToolContext(session=session, account_id=self.other_user['id'], account_features={'knowledge_enabled': True})
            with self.assertRaises(ValueError): await knowledge_file_ids(ctx, base)

    async def test_document_versions_replace_library_but_keep_chat_and_original(self):
        fid = await self.uploaded()
        base = (await self.admin.post('/api/knowledge', json={'name': 'Versioned'})).json()['id']
        await self.admin.post(f'/api/knowledge/{base}/files', json={'file_ids': [fid]})
        chat = (await self.admin.post('/api/chats', json={})).json()['id']
        await self.admin.post(f'/api/chats/{chat}/messages', json={'content': 'read', 'file_ids': [fid]})
        result = await self.admin.post(f'/api/files/{fid}/versions', files={'file': ('guide-v2.txt', b'new evidence', 'text/plain')})
        self.assertEqual(result.status_code, 201, result.text)
        second = result.json()['file_id']
        self.assertEqual(result.json()['replaced_libraries'], 1)
        self.assertEqual((await self.admin.get(f'/api/knowledge/{base}/files')).json()[0]['id'], second)
        self.assertEqual((await self.admin.get(f'/api/files/{fid}/original')).content, b'original evidence')
        self.assertEqual((await self.admin.get(f'/api/chats/{chat}')).json()['files'][0]['file_id'], fid)
        versions = (await self.admin.get(f'/api/files/{second}/versions')).json()
        self.assertEqual([v['revision'] for v in versions], [1, 2])
        self.assertEqual((await self.other.post(f'/api/files/{second}/versions', files={'file': ('hack.txt', b'hack')})).status_code, 404)
        self.assertEqual((await self.admin.post(f'/api/files/{fid}/versions', files={'file': ('v3.txt', b'wrong head')})).status_code, 409)
        same = await self.admin.post(f'/api/files/{second}/versions', files={'file': ('same.txt', b'new evidence')})
        self.assertTrue(same.json()['unchanged'])
        from backend.app.repositories.knowledge_access import independent_files
        from backend.app.models.file import File
        async with self.sessions() as session:
            discovered = set((await session.execute(select(File.id).where(File.user_id == self.admin_user['id'], independent_files()))).scalars())
            self.assertNotIn(fid, discovered)

    async def test_assistant_prompt_and_empty_tools_reach_shared_chat_engine(self):
        from backend.app.chat import service
        saved = await self.admin.put(self.url, json=self.draft)
        self.assertEqual(saved.status_code, 200)
        assistant = (await self.admin.post('/api/workspace/assistants', json={'name': 'No tools', 'system_prompt': 'Use plain language.', 'tool_names': []})).json()
        chat = (await self.admin.post('/api/chats', json={'assistant_id': assistant['id'], 'knowledge_base_ids': []})).json()
        self.assertEqual(chat['knowledge_bases'], [])
        await self.admin.post(f'/api/chats/{chat["id"]}/messages', json={'content': 'hello'})
        seen = []
        async def fake_model(messages, tools, connection):
            seen.append((messages, tools, connection))
            yield {'type': 'text_delta', 'text': 'simple answer'}
        with patch.object(service, 'stream_model_events', fake_model), patch.object(chats, 'schedule_title'):
            result = await self.admin.post(f'/api/chats/{chat["id"]}/respond')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIn('simple answer', result.text)
        self.assertIn('Use plain language.', seen[0][0][0]['content'])
        self.assertFalse(seen[0][1])
        page = (await self.admin.get(f'/api/chats/{chat["id"]}/messages')).json()['items']
        self.assertEqual(page[-1]['status'], 'completed')
        self.assertEqual(page[-1]['content'], 'simple answer')


if __name__ == '__main__': unittest.main()
