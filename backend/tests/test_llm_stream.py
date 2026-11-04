import json  # 构造模拟的模型 SSE 数据
import unittest  # 不请求真实模型，也不访问数据库
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import patch
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

def chunk(delta: dict) -> str:  # 将模型增量包装成 SSE
    return "data: " + json.dumps({"choices": [{"delta": delta}]})

class LlmStreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        settings = SimpleNamespace(llm_api_key=SimpleNamespace(get_secret_value=lambda: "test-only"), llm_base_url="https://model.test/v1", llm_model="test-model", llm_timeout=5)
        self.settings_patch = patch.object(llm, "settings", settings)  # 替换真实配置，不读取真实密钥
        self.settings_patch.start()
        self.addCleanup(self.settings_patch.stop)

    def mock_model(self, lines: list[str], status: int = 200):  # 使用内存 HTTP transport，禁止外部网络请求
        requests = []
        stream = ModelStream(lines)
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(status, stream=stream)
        original_client = httpx.AsyncClient
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
        requests, stream = self.mock_model([chunk({"content": "查询时间", "tool_calls": first}), chunk({"tool_calls": second}), chunk(None), "data: [DONE]"])
        events = [event async for event in llm.stream_model_events([{"role": "user", "content": "几点了"}], TOOL_DEFINITIONS)]
        self.assertEqual(events, [{"type": "text_delta", "text": "查询时间"}, {"type": "tool_call_delta", "tool_calls": first}, {"type": "tool_call_delta", "tool_calls": second}])
        self.assertEqual(requests[0]["tools"], TOOL_DEFINITIONS)
        self.assertEqual(requests[0]["tool_choice"], "auto")
        self.assertTrue(stream.closed)

    async def test_empty_tools_omitted(self):
        requests, _ = self.mock_model(["data: [DONE]"])
        self.assertEqual([event async for event in llm.stream_model_events([], [])], [])
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

if __name__ == "__main__":
    unittest.main()
