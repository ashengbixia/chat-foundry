import json
import unittest
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from backend.tests import test_account_tools as fixtures
from backend.app.tools.runtime import ToolContext
from backend.app.tools.registry import execute_tool, get_available_tools
from backend.app.tools.local import read_webpage as web
from backend.app.tools import summaries, workspace
from backend.app.models.chat import Message
from backend.app.models.file import File
from backend.app.models.knowledge import KnowledgeBase
from backend.app.rag import embedding, knowledge_discovery


class ToolRefinementTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AccountToolTests.asyncSetUp
    asyncTearDown = fixtures.AccountToolTests.asyncTearDown
    context = fixtures.AccountToolTests.context
    call = fixtures.AccountToolTests.call

    async def test_large_web_result_pages_before_marking_delivered(self):
        ctx = ToolContext(web_search_enabled=True)
        doc = {'url': 'https://example.com/doc', 'title': 'Example', 'text': 'x' * 1048576, 'retrieved_at': 0}
        with patch.object(web.settings, 'web_search_enabled', True), patch.object(web, 'fetch_page', AsyncMock(return_value=doc)) as fetch:
            first = json.loads(await execute_tool('read_webpage', json.dumps({'url': doc['url']}), context=ctx))
            self.assertLessEqual(len(json.dumps(first, ensure_ascii=False).encode()), 1048576)
            self.assertTrue(first['has_more'])
            second = json.loads(await execute_tool('read_webpage', json.dumps({'url': doc['url'], 'offset': first['next_offset']}), context=ctx))
            self.assertEqual(first['chunks'][0]['content'] + second['chunks'][0]['content'], doc['text'])
            self.assertFalse(second['has_more'])
            fetch.assert_awaited_once()

    async def test_discovery_retains_tools_and_legacy_read_still_works(self):
        async with self.sessions() as session:
            ctx = await self.context(session, ['f'])
            await self.call(ctx, 'search_tools', query='view_file', count=1)
            await self.call(ctx, 'search_tools', query='grep_chat_files', count=1)
            names = {item['function']['name'] for item in get_available_tools({'f'}, context=ctx)}
            self.assertTrue({'view_file', 'grep_chat_files'} <= names)
            self.assertNotIn('read_uploaded_file', names)
            result = await self.call(ctx, 'read_uploaded_file', file_id='f')
            self.assertIn('FT-204', result['text_content'])

    async def test_summary_resumes_from_saved_output_and_rejects_changed_file_or_other_chat(self):
        async with self.sessions() as session:
            file = await session.get(File, 'f')
            file.text_content = 'x' * 36000
            await session.commit()
            ctx = await self.context(session, ['f'])
            limits = []
            async def model(messages, tools, connection, **kwargs):
                limits.append(kwargs['max_output_tokens'])
                yield {'type': 'text_delta', 'text': '本段摘要'}
            with patch.object(summaries, 'stream_model_events', model):
                first = await self.call(ctx, 'summarize_uploaded_files', file_ids=['f'], focus='主要规则')
                self.assertFalse(first['complete'])
                cursor = first['continuation']['cursor']
                session.add(Message(chat_id='current', sequence=1, role='assistant', status='completed', output=[
                    {'type': 'function_call', 'name': 'summarize_uploaded_files', 'call_id': 'summary'},
                    {'type': 'function_call_output', 'call_id': 'summary', 'output': json.dumps(first)}]))
                await session.commit()
                fresh = await self.context(session, ['f'])
                second = await self.call(fresh, 'summarize_uploaded_files', resume_cursor=cursor)
                self.assertEqual(second['chunks'][0]['start_offset'], 32000)
                self.assertEqual(second['coverage'][0]['read_chars'], 36000)
                self.assertTrue(second['complete'])
                self.assertEqual(limits, [1024] * 9)
                other = await self.context(session, ['f']); other.chat_id = 'old'
                with self.assertRaises(ValueError):
                    await self.call(other, 'summarize_uploaded_files', resume_cursor=cursor)
                file.text_content += 'changed'
                await session.commit()
                with self.assertRaisesRegex(ValueError, '发生变化'):
                    await self.call(await self.context(session, ['f']), 'summarize_uploaded_files', resume_cursor=cursor)

    async def test_global_knowledge_ranking_caches_metadata_and_scopes_multiple_libraries(self):
        cache = {}
        def cached(owner, key, fingerprint):
            return cache.get((owner, key, fingerprint))
        def save(owner, key, fingerprint, vectors):
            cache[owner, key, fingerprint] = vectors
        async def encode(texts):
            return [[0., 1.] if '目标' in text else [1., 0.] for text in texts]
        async with self.sessions() as session:
            session.add_all(KnowledgeBase(id=f'b{i:02}', user_id='u', name=f'库{i}', description='说明' * 495 + ('目标' if i == 59 else '一般')) for i in range(60))
            await session.commit()
            ctx = await self.context(session)
            with patch.object(knowledge_discovery, 'cached_vectors', cached), patch.object(knowledge_discovery, 'save_vectors', save), \
                 patch.object(embedding, 'split_for_embedding', AsyncMock(side_effect=lambda text: [{'content': text}])), \
                 patch.object(embedding, 'embed_query', AsyncMock(return_value=[0., 1.])), patch.object(embedding, 'embed_texts', AsyncMock(side_effect=encode)) as embeddings:
                result = await self.call(ctx, 'query_knowledge_bases', query='目标', count=1)
                self.assertEqual(result['knowledge_bases'][0]['id'], 'b59')
                self.assertEqual(result['total'], 61)
                calls = embeddings.await_count
                await self.call(ctx, 'query_knowledge_bases', query='目标', count=1)
                self.assertEqual(embeddings.await_count, calls)
            with patch.object(workspace, 'search_uploaded_files', AsyncMock(return_value={'chunks': []})) as retrieve:
                await self.call(ctx, 'query_knowledge_files', query='规则', knowledge_ids=['k', 'b59'])
                self.assertEqual(retrieve.await_args.args[2], {'f'})
                with self.assertRaises(ValueError):
                    await self.call(ctx, 'query_knowledge_files', query='规则', knowledge_ids=['k', 'private'])

    async def test_history_search_handles_separate_keywords_and_listing_cursors_are_independent(self):
        async with self.sessions() as session:
            row = await session.scalar(select(Message).where(Message.chat_id == 'old', Message.sequence == 1))
            row.content = 'Python的基础学习，适合从这里入门。'
            await session.commit()
            ctx = await self.context(session, ['f', 'standalone'])
            result = await self.call(ctx, 'search_chats', query='Python 入门')
            self.assertEqual([item['chat_id'] for item in result['chats']], ['old'])
            ctx.history_sources = {str(i): {'filename': f'Example{i}', 'content': 'sample'} for i in range(3)}
            page = await self.call(ctx, 'list_available_files', limit=1, offset=0, history_offset=1)
            self.assertEqual(page['next_offset'], 1)
            self.assertEqual(page['next_history_offset'], 2)
