import json  # 构造模拟的模型 SSE 数据
import unittest  # 不请求真实模型，也不访问数据库
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import Mock, patch
import httpx
from backend.app.llm import client as llm
from backend.app.tools.registry import TOOL_DEFINITIONS

class ModelStream(httpx.AsyncByteStream):  # 模拟模型响应，记录是否释放连接
    def __init__(self, lines: list[str]):
        self.lines = lines
        self.closed = False
    async def __aiter__(self):
        for line in self.lines:
            yield (line + "\n\n").encode()
    async def aclose(self):
        self.closed = True

def chunk(delta: dict | None, finish_reason: str | None = None) -> str:  # 将模型增量和结束原因包装成SSE
    return "data: " + json.dumps({"choices": [{"delta": delta, "finish_reason": finish_reason}]})

class LlmStreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.real_client = httpx.AsyncClient  # 同一测试切换多个模拟响应时，始终使用未被替换的HTTP客户端类
        settings = SimpleNamespace(llm_api_key=SimpleNamespace(get_secret_value=lambda: "test-only"), llm_base_url="https://model.test/v1", llm_model="test-model", llm_timeout=5, llm_context_window=32768, llm_max_output_tokens=4096, llm_context_margin=1024)
        self.settings_patch = patch.object(llm, "settings", settings)  # 替换真实配置，不读取真实密钥
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)

    def mock_model(self, lines: list[str], status: int = 200):  # 使用内存 HTTP transport，禁止外部网络请求
        requests = []
        stream = ModelStream(lines)
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(status, stream=stream)
        original_client = self.real_client
        mock = patch.object(llm.httpx, "AsyncClient", side_effect=lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs))
        mock.start()
        self.addCleanup(mock.stop)
        return requests, stream

    async def test_existing_text_interface(self):  # 原来的 service 仍能得到纯正文，不向模型传工具
        requests, stream = self.mock_model([": heartbeat", chunk({"role": "assistant"}), chunk({"content": "你好"}), chunk({"content": "！"}), "data: [DONE]"])
        messages = [{"role": "user", "content": "你好"}]
        self.assertEqual([text async for text in llm.stream_reply(messages)], ["你好", "！"])
        self.assertEqual(requests[0]["messages"], messages)
        self.assertNotIn("tools", requests[0])
        self.assertNotIn("tool_choice", requests[0])
        self.assertTrue(stream.closed)

    async def test_text_and_tool_fragments(self):  # 工具参数跨片段返回，上层必须能收到全部片段
        first = [{"index": 0, "id": "call_01", "type": "function", "function": {"name": "get_current_time", "arguments": "{"}}]
        second = [{"index": 0, "function": {"arguments": "}"}}]
        requests, stream = self.mock_model([chunk({"content": "查询时间", "tool_calls": first}), chunk({"tool_calls": second}), chunk(None, "tool_calls"), "data: [DONE]"])
        events = [event async for event in llm.stream_model_events([{"role": "user", "content": "几点了"}], TOOL_DEFINITIONS)]
        self.assertEqual(events, [{"type": "text_delta", "text": "查询时间"}, {"type": "tool_call_delta", "tool_calls": first}, {"type": "tool_call_delta", "tool_calls": second}])
        self.assertEqual(requests[0]["tools"], TOOL_DEFINITIONS)
        self.assertEqual(requests[0]["tool_choice"], "auto")
        self.assertTrue(stream.closed)

    async def test_empty_tools_omitted(self):
        requests, _ = self.mock_model([chunk({"content": "正文"}, "stop"), "data: [DONE]"])
        self.assertEqual([event async for event in llm.stream_model_events([], [])], [{"type": "text_delta", "text": "正文"}])
        self.assertNotIn("tools", requests[0])

    async def test_early_close_releases_model_stream(self):  # 停止读取后，兼容层也必须关闭内部 HTTP 响应
        _, stream = self.mock_model([chunk({"content": "第一段"}), chunk({"content": "第二段"})])
        async with aclosing(llm.stream_reply([])) as reply:
            self.assertEqual(await anext(reply), "第一段")
        self.assertTrue(stream.closed)

    async def test_stream_error_propagates(self):
        _, stream = self.mock_model(['data: {"error":{"message":"test-only"}}'])
        with self.assertRaisesRegex(RuntimeError, "流式响应中返回了错误"):
            async for _ in llm.stream_reply([]):
                pass
        self.assertTrue(stream.closed)

    async def test_http_error_propagates(self):
        _, stream = self.mock_model([], status=429)
        with self.assertRaises(httpx.HTTPStatusError):
            async for _ in llm.stream_model_events([]):
                pass
        self.assertTrue(stream.closed)

    async def test_stop_and_usage_chunk_complete_normally(self):
        _, stream = self.mock_model([chunk({"content": "正文"}), chunk({}, "stop"), 'data: {"choices":[],"usage":{"total_tokens":10}}', "data: [DONE]"])
        self.assertEqual([part async for part in llm.stream_reply([])], ["正文"])
        self.assertTrue(stream.closed)

    async def test_clean_eof_without_done_is_not_success(self):
        for ending in [None, "stop"]:
            with self.subTest(ending=ending):
                _, stream = self.mock_model([chunk({"content": "部分正文"}), chunk({}, ending)])
                parts = []
                with self.assertRaisesRegex(llm.ModelStreamError, "没有收到完成标记"):
                    async for part in llm.stream_reply([]):
                        parts.append(part)
                self.assertEqual(parts, ["部分正文"])
                self.assertTrue(stream.closed)

    async def test_length_and_filter_reasons_are_not_success(self):
        for reason, expected in [("length", "长度限制"), ("content_filter", "中止")]:
            for done in [False, True]:
                with self.subTest(reason=reason, done=done):
                    lines = [chunk({"content": "部分正文"}), chunk({}, reason)] + (["data: [DONE]"] if done else [])
                    _, stream = self.mock_model(lines)
                    with self.assertRaisesRegex(llm.ModelStreamError, expected):
                        async for _ in llm.stream_reply([]):
                            pass
                    self.assertTrue(stream.closed)

    async def test_empty_and_whitespace_answers_are_rejected(self):
        for lines in [["data: [DONE]"], [chunk({"role": "assistant"}), "data: [DONE]"], [chunk({"content": " \n"}, "stop"), "data: [DONE]"]]:
            _, stream = self.mock_model(lines)
            with self.assertRaisesRegex(llm.ModelStreamError, "没有返回正文或工具调用"):
                async for _ in llm.stream_model_events([]):
                    pass
            self.assertTrue(stream.closed)

    async def test_invalid_finish_reason_and_missing_tool_data(self):
        call = [{"index": 0, "id": "c1", "function": {"name": "get_current_time", "arguments": "{}"}}]
        for lines, expected in [([chunk({"content": "正文"}, "other"), "data: [DONE]"], "不支持"), ([chunk({}, "tool_calls"), "data: [DONE]"], "没有返回工具调用数据"), ([chunk({"tool_calls": call}, "stop"), "data: [DONE]"], "不一致")]:
            self.mock_model(lines)
            with self.assertRaisesRegex(llm.ModelStreamError, expected):
                async for _ in llm.stream_model_events([]):
                    pass

    async def test_real_parser_failure_saves_partial_output_as_failed(self):  # 从模拟HTTP响应经真实解析器，一直验证到数据库
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from backend.app.core.database import Base
        from backend.app.models.chat import Chat, Message
        from backend.app.repositories.chat import create_message
        from backend.app.chat import service
        for reason, expected in [(None, "没有收到完成标记"), ("length", "长度限制")]:
            lines = [chunk({"content": "收到一半"})] if reason is None else [chunk({"content": "收到一半"}, reason), "data: [DONE]"]
            self.mock_model(lines)
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            sessions = async_sessionmaker(engine, expire_on_commit=False)
            try:
                async with engine.begin() as connection:
                    await connection.run_sync(Base.metadata.create_all)
                async with sessions() as session:
                    session.add(Chat(id="test-chat", title="结束判断"))
                    await session.commit()
                    await create_message(session, "test-chat", "user", "测试问题")
                    with patch.object(service, "logger", Mock()), patch.object(service, "settings", llm.settings):
                        events = [event async for event in service.stream_chat_history_events(session, "test-chat")]
                async with sessions() as session:
                    saved = (await session.execute(select(Message).where(Message.role == "assistant"))).scalar_one()
                    self.assertEqual(saved.status, "failed")
                    self.assertEqual(saved.content, "收到一半")
                    self.assertEqual(saved.output[0]["content"][0]["text"], "收到一半")
                    self.assertIn(expected, saved.error)
                self.assertTrue(events[-1].startswith("event: error"))
                self.assertIn(expected, events[-1])
                self.assertFalse(any(event.startswith("event: done") for event in events))
            finally:
                await engine.dispose()

if __name__ == "__main__":
    unittest.main()
