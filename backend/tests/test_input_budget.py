import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from backend.app.chat import service
from backend.app.chat.context import build_model_messages, trim_model_messages
from backend.app.core import database
from backend.app.llm import client as llm
from backend.app.llm.budget import InputBudgetError, ensure_input_budget, estimate_input_tokens, get_input_budget
from backend.app.models.chat import Chat, Message
from backend.app.repositories.chat import create_message
from backend.app.tools.registry import get_available_tools

class InputBudgetTests(unittest.TestCase):
    def test_reserves_and_invalid_settings(self):
        self.assertEqual(get_input_budget(32768, 4096, 1024), 27648)
        for values in [(100, 100, 0), (100, 80, 20), (0, 1, 0), (100, -1, 0), (100, 1, -1), (True, 1, 0)]:
            with self.subTest(values=values), self.assertRaises(InputBudgetError):
                get_input_budget(*values)

    def test_utf8_estimate_and_tool_definitions_are_counted(self):
        messages = [{"role": "user", "content": "你好"}]
        payload = {"messages": messages}
        self.assertEqual(estimate_input_tokens(messages), len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()))
        self.assertGreater(estimate_input_tokens(messages, get_available_tools(set())), estimate_input_tokens(messages))
        self.assertEqual(estimate_input_tokens(messages, []), estimate_input_tokens(messages))

    def test_exact_boundary_and_copy_without_modifying_history(self):
        messages = [{"role": "system", "content": "系统"}, {"role": "user", "content": "当前问题"}]
        original = copy.deepcopy(messages)
        size = estimate_input_tokens(messages)
        result = trim_model_messages(messages, None, size + 10, 10, 0)
        self.assertEqual(result, messages)
        self.assertIsNot(result, messages)
        self.assertEqual(messages, original)
        ensure_input_budget(result, None, size + 10, 10, 0)

    def test_old_tool_call_and_result_are_removed_as_a_complete_turn(self):
        system = {"role": "system", "content": "系统"}
        latest = {"role": "user", "content": "当前问题"}
        messages = [system, {"role": "user", "content": "旧问题"}, {"role": "assistant", "content": None, "tool_calls": [{"id": "old-call"}]}, {"role": "tool", "tool_call_id": "old-call", "content": "旧结果" * 100}, {"role": "assistant", "content": "旧回答"}, latest]
        original = copy.deepcopy(messages)
        size = estimate_input_tokens([system, latest])
        self.assertEqual(trim_model_messages(messages, None, size + 10, 10, 0), [system, latest])
        self.assertEqual(messages, original)

    def test_current_turn_keeps_tool_pairs_while_older_turns_are_removed(self):
        current = [{"role": "user", "content": "当前问题"}, {"role": "assistant", "content": None, "tool_calls": [{"id": "current"}]}, {"role": "tool", "tool_call_id": "current", "content": "本轮结果"}]
        messages = [{"role": "user", "content": "旧一"}, {"role": "assistant", "content": "旧回答"}, {"role": "user", "content": "旧二"}, {"role": "assistant", "content": "旧回答"}] + current
        size = estimate_input_tokens(current)
        self.assertEqual(trim_model_messages(messages, None, size + 10, 10, 0), current)

    def test_current_question_tools_or_result_alone_too_large(self):
        for messages, tools in [([{"role": "user", "content": "文" * 100}], None), ([{"role": "user", "content": "问题"}], get_available_tools(set())), ([{"role": "user", "content": "问题"}, {"role": "assistant", "tool_calls": [{"id": "call"}]}, {"role": "tool", "tool_call_id": "call", "content": "结果" * 100}], None)]:
            with self.assertRaisesRegex(InputBudgetError, "本轮工具结果"):
                trim_model_messages(messages, tools, 100, 10, 0)

class BudgetClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.real_client = httpx.AsyncClient
        self.settings = SimpleNamespace(llm_api_key=SimpleNamespace(get_secret_value=lambda: "test-only"), llm_base_url="https://test.invalid/v1", llm_model="test", llm_timeout=5, llm_context_window=32768, llm_max_output_tokens=128, llm_context_margin=64)
        self.config_patch = patch.object(llm, "settings", self.settings)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    async def test_both_request_paths_stop_before_creating_http_client(self):
        self.settings.llm_context_window = 200
        with patch.object(llm.httpx, "AsyncClient") as client:
            with self.assertRaises(InputBudgetError):
                await llm.generate_reply("文" * 1000)
            with self.assertRaises(InputBudgetError):
                async for _ in llm.stream_model_events([{"role": "user", "content": "文" * 1000}]):
                    pass
        client.assert_not_called()

    async def test_output_limit_is_sent_in_both_request_paths(self):
        requests = []
        def handler(request):
            payload = json.loads(request.content)
            requests.append(payload)
            if payload["stream"]:
                data = json.dumps({"choices": [{"delta": {"content": "回答"}, "finish_reason": "stop"}]})
                return httpx.Response(200, text=f"data: {data}\n\ndata: [DONE]\n\n")
            return httpx.Response(200, json={"choices": [{"message": {"content": "回答"}, "finish_reason": "stop"}]})
        with patch.object(llm.httpx, "AsyncClient", side_effect=lambda **kwargs: self.real_client(transport=httpx.MockTransport(handler), **kwargs)):
            self.assertEqual(await llm.generate_reply("问题"), "回答")
            self.assertEqual([event async for event in llm.stream_model_events([{"role": "user", "content": "问题"}])], [{"type": "text_delta", "text": "回答"}])
        self.assertEqual([request["max_tokens"] for request in requests], [128, 128])

    async def test_legacy_endpoint_returns_clear_budget_error(self):
        from backend.app import main
        with patch.object(main, "generate_reply", AsyncMock(side_effect=InputBudgetError("模型输入超过预算"))):
            async with self.real_client(transport=httpx.ASGITransport(app=main.app), base_url="http://test") as client:
                response = await client.post("/api/chat", json={"message": "问题"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "模型输入超过预算")

    async def test_nonstream_output_limit_or_filter_is_not_silently_accepted(self):
        for reason, expected in [("length", "长度限制"), ("content_filter", "中止")]:
            def handler(request):
                return httpx.Response(200, json={"choices": [{"message": {"content": "部分回答"}, "finish_reason": reason}]})
            with patch.object(llm.httpx, "AsyncClient", side_effect=lambda **kwargs: self.real_client(transport=httpx.MockTransport(handler), **kwargs)):
                with self.assertRaisesRegex(llm.ModelStreamError, expected):
                    await llm.generate_reply("问题")

class BudgetServiceTests(unittest.IsolatedAsyncioTestCase):  # 真实数据库操作配合模拟模型，不修改真实历史
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        await database.create_tables(self.engine)
        async with self.sessions() as session:
            session.add(Chat(id="chat", title="预算测试"))
            await session.commit()
            await create_message(session, "chat", "user", "旧问题" * 200)
            await create_message(session, "chat", "assistant", "旧回答" * 200)
            await create_message(session, "chat", "user", "最新问题")
            self.history = (await session.execute(select(Message).order_by(Message.sequence))).scalars().all()
            self.all_messages = build_model_messages(self.history)
            self.latest_messages = build_model_messages(self.history[-1:])
        self.tools = get_available_tools(set())

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def run_chat(self, budget, stream, tool_result=None):
        settings = SimpleNamespace(llm_model="test", llm_context_window=budget + 48, llm_max_output_tokens=32, llm_context_margin=16)
        with patch.object(service, "settings", settings), patch.object(service, "stream_model_events", stream), patch.object(service, "logger", Mock()):
            if tool_result is None:
                async with self.sessions() as session:
                    return [event async for event in service.stream_chat_history_events(session, "chat")]
            with patch.object(service, "execute_tool", return_value=tool_result):
                async with self.sessions() as session:
                    return [event async for event in service.stream_chat_history_events(session, "chat")]

    async def test_history_is_trimmed_for_request_but_kept_in_database(self):
        requests = []
        async def stream(messages, tools):
            requests.append(copy.deepcopy(messages))
            yield {"type": "text_delta", "text": "回答"}
        await self.run_chat(estimate_input_tokens(self.latest_messages, self.tools), stream)
        self.assertEqual(requests, [self.latest_messages])
        async with self.sessions() as session:
            records = (await session.execute(select(Message).order_by(Message.sequence))).scalars().all()
            self.assertEqual(len(records), 4)
            self.assertEqual(records[0].content, "旧问题" * 200)
            self.assertEqual(records[1].content, "旧回答" * 200)

    async def test_tool_result_triggers_another_budget_check_and_full_turn_trim(self):
        requests = []
        async def stream(messages, tools):
            index = len(requests)
            requests.append(copy.deepcopy(messages))
            if index == 0:
                yield {"type": "tool_call_delta", "tool_calls": [{"index": 0, "id": "c1", "function": {"name": "get_current_time", "arguments": "{}"}}]}
            else:
                yield {"type": "text_delta", "text": "回答"}
        await self.run_chat(estimate_input_tokens(self.all_messages, self.tools), stream, json.dumps({"result": "x" * 3000}))
        self.assertEqual(len(requests), 2)
        self.assertEqual(len([message for message in requests[0] if message["role"] == "user"]), 2)
        self.assertEqual([message["role"] for message in requests[1]], ["system", "user", "assistant", "tool"])
        self.assertEqual(requests[1][-2]["tool_calls"][0]["id"], requests[1][-1]["tool_call_id"])

    async def test_current_tool_result_over_budget_stops_next_model_request(self):
        requests = []
        async def stream(messages, tools):
            requests.append(copy.deepcopy(messages))
            yield {"type": "tool_call_delta", "tool_calls": [{"index": 0, "id": "c1", "function": {"name": "get_current_time", "arguments": "{}"}}]}
        events = await self.run_chat(estimate_input_tokens(self.latest_messages, self.tools), stream, json.dumps({"result": "x" * 10000}))
        self.assertEqual(len(requests), 1)
        self.assertTrue(events[-1].startswith("event: error"))
        self.assertIn("输入预算", events[-1])
        async with self.sessions() as session:
            latest = (await session.execute(select(Message).order_by(Message.sequence.desc()).limit(1))).scalar_one()
            self.assertEqual(latest.status, "failed")
            self.assertEqual([item["type"] for item in latest.output], ["function_call", "function_call_output"])

    async def test_oversized_current_question_never_calls_model(self):
        stream = Mock()
        events = await self.run_chat(10, stream)
        stream.assert_not_called()
        self.assertIn("输入预算", events[-1])
