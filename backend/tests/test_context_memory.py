import asyncio
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from backend.app.core.database import create_tables
from backend.app.models.chat import Chat, Message
from backend.app.chat import compaction
from backend.app.chat import service
from backend.app.chat.context import build_model_messages
from backend.app.chat.memory import replay_history
from backend.app.chat.history_lookup import bind_history_lookup
from backend.app.repositories.chat import list_recent_messages


def evidence(file_id, text):
    return [{'type': 'function_call', 'call_id': 'same-id', 'name': 'read_uploaded_file', 'arguments': '{}', 'reasoning_content': '实际思考'},
            {'type': 'function_call_output', 'call_id': 'same-id', 'output': json.dumps({'file_id': file_id, 'filename': file_id + '.txt', 'offset': 0, 'text_content': text, 'citation_id': 1})},
            {'type': 'message', 'content': [{'type': 'output_text', 'text': '结论[1]'}]}]


class ContextMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_usage_takes_precedence_and_model_switch_uses_fallback(self):
        async with self.sessions() as session:
            rows = await list_recent_messages(session, 'main')
            rows[-2].usage = {'prompt_tokens': 90000, 'completion_tokens': 100}
            rows[-2].model = 'tested-model'
            connection = SimpleNamespace(model='tested-model')
            self.assertIsNotNone(compaction.select_boundary(rows, None, [], connection=connection))
            rows[-2].usage = {'prompt_tokens': 100, 'completion_tokens': 10}
            rows[0].content = '中文历史' * 30000
            self.assertIsNone(compaction.select_boundary(rows, None, [], connection=connection))
            self.assertIsNotNone(compaction.select_boundary(rows, None, [], connection=SimpleNamespace(model='different-model')))

    async def test_failed_summary_cools_down_the_same_checkpoint_without_moving_it(self):
        async with self.sessions() as session:
            rows = await list_recent_messages(session, 'main')
            with patch.object(compaction.settings, 'context_compaction_token_threshold', 1):
                with patch.object(compaction, 'summarize_history', AsyncMock(side_effect=ValueError('simulated'))):
                    with self.assertRaises(ValueError):
                        await compaction.compact_history(session, rows, 4, None)
                self.assertIsNone(compaction.select_boundary(rows, None, []))
                self.assertEqual([row.sequence for row in await list_recent_messages(session, 'main')], list(range(1, 8)))
                with patch.object(compaction.time, 'monotonic', return_value=10**20):
                    self.assertIsNotNone(compaction.select_boundary(rows, None, []))

    async def test_summary_failure_is_attempted_once_per_turn_and_usage_is_saved(self):
        count = 0
        async def model(messages, tools, connection=None):
            nonlocal count
            count += 1
            self.assertTrue(tools)
            if count == 1:
                yield {'type': 'tool_call_delta', 'tool_calls': [{'index': 0, 'id': 'live', 'function': {'name': 'get_current_time', 'arguments': '{}'}}]}
            else:
                yield {'type': 'text_delta', 'text': '继续回答'}
                yield {'type': 'usage', 'usage': {'prompt_tokens': 1200, 'completion_tokens': 20}}
        with patch.object(service.settings, 'context_compaction_enabled', True), patch.object(compaction, 'select_boundary', return_value=4) as boundary, patch.object(compaction, 'summarize_history', AsyncMock(side_effect=ValueError('simulated'))) as summary, patch.object(service, 'stream_model_events', model), patch.object(service, 'prepare_auto_reference', AsyncMock(return_value=[])), patch.object(service, 'execute_tool', AsyncMock(return_value='{}')), self.assertLogs(service.logger, level='WARNING'):
            async with self.sessions() as session:
                events = [event async for event in service.stream_chat_history_events(session, 'main')]
                saved = await session.scalar(select(Message).where(Message.chat_id == 'main').order_by(Message.sequence.desc()).limit(1))
                self.assertEqual(saved.usage, {'prompt_tokens': 1200, 'completion_tokens': 20})
                self.assertEqual(saved.status, 'completed')
                self.assertEqual(summary.await_count, 1)
                self.assertEqual(boundary.call_count, 1)
                self.assertTrue(events[-1].startswith('event: done'))

    async def test_old_protocol_text_is_not_replayed_or_mutated(self):
        text = '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="read_webpage"></｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>'
        output = [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]
        row = SimpleNamespace(id='old', role='assistant', status='completed', content=text, output=copy.deepcopy(output))
        replayed, _, _ = replay_history([row], {})
        messages = build_model_messages(replayed)
        self.assertNotIn('DSML', str(messages))
        self.assertIn('未执行', str(messages))
        self.assertEqual(row.output, output)
        self.assertEqual(row.content, text)

    async def asyncSetUp(self):
        current = patch.object(compaction, '_failures', compaction.OrderedDict())
        current.start()
        self.addCleanup(current.stop)
        self.engine = create_async_engine('sqlite+aiosqlite:///:memory:')
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        await create_tables(self.engine)
        async with self.sessions() as session:
            session.add_all([Chat(id='main'), Chat(id='other')])
            await session.flush()
            session.add_all([Message(id=str(i), chat_id='main', sequence=i, role='user' if i % 2 else 'assistant', content='问题' if i % 2 else '结论[1]',
                output=evidence(str(i), '原文' + str(i)) if i % 2 == 0 else []) for i in range(1, 8)])
            session.add(Message(id='secret', chat_id='other', sequence=20, role='assistant', content='其他账号资料', output=evidence('private', '不能读取')))
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_replay_renumbers_sources_keeps_reasoning_and_does_not_mutate_history(self):
        async with self.sessions() as session:
            history = await list_recent_messages(session, 'main')
            original = copy.deepcopy(history[1].output)
            replayed, snapshots, raw = replay_history(history, {})
            messages = build_model_messages(replayed)
            calls = [item for item in messages if item.get('tool_calls')]
            self.assertEqual(len({item['tool_calls'][0]['id'] for item in calls}), 3)
            self.assertTrue(all(item['reasoning_content'] == '实际思考' for item in calls))
            self.assertEqual([item['content'] for item in messages if item.get('role') == 'assistant' and not item.get('tool_calls')], ['结论[1]', '结论[2]', '结论[3]'])
            self.assertEqual(len(raw), 3)
            self.assertEqual(len(snapshots), 3)
            self.assertEqual(history[1].output, original)
            # After the original tool turn is outside the retained range, persisted replay evidence still works.
            cached = SimpleNamespace(id='cached', role='assistant', status='completed', content='结论[2]', output=[{'type': 'context_sources', 'chunks': snapshots}])
            next_history, next_snapshots, _ = replay_history([cached], {})
            self.assertTrue(any(item['role'] == 'tool' for item in build_model_messages(next_history)))
            self.assertEqual(len(next_snapshots), 3)

    async def test_checkpoint_survives_reload_excludes_anchor_and_merges_next_time(self):
        async with self.sessions() as session:
            history = await list_recent_messages(session, 'main')
            with patch.object(compaction, 'summarize_history', AsyncMock(return_value='第一份摘要')) as summarize:
                kept, checkpoint = await compaction.compact_history(session, history, 4, None)
            self.assertEqual([row.sequence for row in summarize.await_args.args[0]], [1, 2, 3, 4])
            self.assertEqual(kept[0].sequence, 5)
            self.assertEqual(checkpoint['through_sequence'], 4)
        async with self.sessions() as session:
            reloaded = await list_recent_messages(session, 'main')
            self.assertEqual([row.sequence for row in reloaded], [5, 6, 7])
            request = build_model_messages(reloaded)
            self.assertIn('第一份摘要', request[1]['content'])
            self.assertEqual(request[2]['role'], 'user')
            with patch.object(compaction, 'summarize_history', AsyncMock(return_value='合并后的摘要')) as summarize:
                await compaction.compact_history(session, reloaded, 2, None)
            self.assertEqual(summarize.await_args.args[1], '第一份摘要')
            self.assertEqual(await session.scalar(select(func.count()).select_from(Message).where(Message.chat_id == 'main')), 7)
        async with self.sessions() as session:
            self.assertEqual([row.sequence for row in await list_recent_messages(session, 'main')], [7])

    async def test_failed_or_cancelled_summary_never_advances_boundary(self):
        async with self.sessions() as session:
            history = await list_recent_messages(session, 'main')
            for error in [ValueError('失败'), asyncio.CancelledError()]:
                with patch.object(compaction, 'summarize_history', AsyncMock(side_effect=error)):
                    with self.assertRaises(type(error)):
                        await compaction.compact_history(session, history, 4, None)
            self.assertEqual([row.sequence for row in await list_recent_messages(session, 'main')], list(range(1, 8)))

    async def test_older_source_lookup_is_bound_to_chat(self):
        async with self.sessions() as session:
            lookup = bind_history_lookup(session, 'main')
            _, catalog = await lookup(before_sequence=5, query='原文')
            self.assertEqual({item['source_id'] for item in catalog['sources']}, {'M4:1', 'M2:1'})
            sources, _ = await lookup(source_ids=['M2:1'])
            self.assertEqual(sources['M2:1']['content'], '原文2')
            with self.assertRaises(ValueError):
                await lookup(source_ids=['M20:1'])

    async def test_summary_batches_include_all_old_content_and_failure_does_not_succeed(self):
        seen = []
        async def stream(messages, tools, connection, **kwargs):
            self.assertIsNone(tools)
            seen.append(json.loads(messages[1]['content']))
            yield {'type': 'text_delta', 'text': '合并目标与证据位置'}
        rows = [SimpleNamespace(sequence=1, role='user', content='长文本' * 5000, output=[])]
        config = SimpleNamespace(llm_context_window=6000, llm_max_output_tokens=500, llm_context_margin=100)
        with patch.object(compaction, 'settings', config), patch.object(compaction, 'stream_model_events', stream):
            self.assertEqual(await compaction.summarize_history(rows, '旧摘要', None), '合并目标与证据位置')
        joined = ''.join(item['history_fragment'] for item in seen)
        self.assertEqual(json.loads(joined)[0]['content'], rows[0].content)
        self.assertEqual(seen[0]['previous_summary'], '旧摘要')
        self.assertTrue(len(seen) > 1)

    async def test_budget_boundary_is_a_user_turn(self):
        async with self.sessions() as session:
            rows = await list_recent_messages(session, 'main')
            rows[0].content = '很长的旧问题' * 3000
            with patch.object(compaction.settings, 'context_compaction_token_threshold', 1000):
                boundary = compaction.select_boundary(rows, None, [])
            self.assertIsNotNone(boundary)
            self.assertEqual(rows[boundary].role, 'user')
            self.assertEqual(rows[boundary - 1].role, 'assistant')

    async def test_midturn_compaction_preserves_running_call_and_archived_citations(self):
        requests = []
        async def model(messages, tools, connection=None):
            requests.append(copy.deepcopy(messages))
            if len(requests) == 1:
                yield {'type': 'tool_call_delta', 'tool_calls': [{'index': 0, 'id': 'live-call', 'function': {'name': 'get_current_time', 'arguments': '{}'}}]}
            else:
                self.assertTrue(any(item.get('tool_call_id') == 'live-call' for item in messages))
                self.assertTrue(any(call['id'] == 'live-call' for item in messages for call in item.get('tool_calls', [])))
                self.assertIn('压缩后的旧背景', str(messages))
                yield {'type': 'text_delta', 'text': '已完成[1]'}
        with patch.object(service.settings, 'context_compaction_enabled', True), patch.object(compaction, 'select_boundary', side_effect=[None, None, 4]), patch.object(compaction, 'summarize_history', AsyncMock(return_value='压缩后的旧背景')), patch.object(service, 'stream_model_events', model), patch.object(service, 'prepare_auto_reference', AsyncMock(return_value=[])), patch.object(service, 'execute_tool', AsyncMock(return_value='{"time":"12:00"}')):
            async with self.sessions() as session:
                events = [event async for event in service.stream_chat_history_events(session, 'main')]
                saved = await session.scalar(select(Message).where(Message.chat_id == 'main').order_by(Message.sequence.desc()).limit(1))
                self.assertEqual(saved.status, 'completed')
                archived = next(item for item in saved.output if item['type'] == 'context_sources')['chunks']
                self.assertTrue(any(item.get('in_context') is False for item in archived))
                self.assertTrue(any('context_checkpoint' in event for event in events))
                replayed, snapshots, _ = replay_history([saved], {})
                self.assertNotIn('原文2', str(build_model_messages(replayed)))
                self.assertEqual(len(snapshots), 0)
