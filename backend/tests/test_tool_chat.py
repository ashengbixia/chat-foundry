import asyncio  # 模拟读取流时取消
import copy  # 保存每次模型请求时的消息快照
import json
import unittest
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from backend.app.chat import service
from backend.app.tools.registry import get_available_tools

def text(value: str) -> dict:  # 构造正文事件
    return {"type": "text_delta", "text": value}

def tool(index: int = 0, name: str = "get_current_time", arguments: str = "{}", call_id: str = "call-1") -> dict:  # 构造工具调用事件
    return {"type": "tool_call_delta", "tool_calls": [{"index": index, "id": call_id, "function": {"name": name, "arguments": arguments}}]}

class ToolChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):  # 只模拟仓库与模型，不读写真实数据库或请求真实模型
        history = [SimpleNamespace(role="user", content="现在几点？", status="completed")]
        self.read = AsyncMock(return_value=history)
        self.create = AsyncMock(return_value=SimpleNamespace(id="assistant-1"))
        self.update = AsyncMock()
        self.logger = Mock()
        for name, value in {"list_recent_messages": self.read, "create_message": self.create, "update_message": self.update, "logger": self.logger, "settings": SimpleNamespace(llm_model="test-model")}.items():
            mock = patch.object(service, name, value)
            mock.start()
            self.addCleanup(mock.stop)

    def model(self, rounds):  # 每一轮都保存独立快照，避免后续 append 改变已经发出的请求
        requests = []
        closed = []
        async def stream(messages, tools):
            index = len(requests)
            requests.append((copy.deepcopy(messages), copy.deepcopy(tools)))
            try:
                for event in rounds[index]:
                    if isinstance(event, BaseException):
                        raise event
                    yield event
            finally:
                closed.append(index)
        mock = patch.object(service, "stream_model_events", stream)
        mock.start()
        self.addCleanup(mock.stop)
        return requests, closed

    async def run_chat(self):  # 收集发送到前端的 SSE 事件
        return [event async for event in service.stream_chat_history_events(None, "chat-1")]

    async def test_plain_answer(self):
        requests, closed = self.model([[text("你好"), text("！")]])
        events = await self.run_chat()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][1], get_available_tools(set()))
        saved = self.update.call_args.kwargs
        self.assertEqual(saved["content"], "你好！")
        self.assertEqual(saved["status"], "completed")
        self.assertEqual(saved["output"][0]["content"][0]["text"], "你好！")
        self.assertTrue(events[-1].startswith("event: done"))
        self.assertEqual(closed, [0])

    async def test_split_call_and_result_round_trip(self):
        requests, _ = self.model([[tool(name="get_current_", arguments="{"), {"type": "tool_call_delta", "tool_calls": [{"index": 0, "function": {"name": "time", "arguments": "}"}}]}], [text("当前是北京时间。")]])
        events = await self.run_chat()
        self.assertEqual(len(requests), 2)
        assistant, result = requests[1][0][-2:]
        self.assertEqual(assistant["role"], "assistant")
        self.assertEqual(assistant["tool_calls"][0]["function"], {"name": "get_current_time", "arguments": "{}"})
        self.assertEqual(result["role"], "tool")
        self.assertEqual(result["tool_call_id"], "call-1")
        self.assertEqual(json.loads(result["content"])["timezone"], "Asia/Shanghai")
        saved = self.update.call_args.kwargs
        self.assertEqual([item["type"] for item in saved["output"]], ["function_call", "function_call_output", "message"])
        self.assertEqual(saved["content"], "当前是北京时间。")
        self.assertEqual([event.splitlines()[0] for event in events], ["event: tool_call", "event: tool_result", "event: text_delta", "event: done"])
        streamed_call = json.loads(events[0].split("data: ", 1)[1])
        streamed_result = json.loads(events[1].split("data: ", 1)[1])
        self.assertEqual(streamed_call["arguments"], "{}")
        self.assertEqual(streamed_call["call_id"], streamed_result["call_id"])
        self.assertEqual(streamed_result["output"], saved["output"][1]["output"])

    async def test_multiple_calls_keep_their_own_arguments(self):
        requests, _ = self.model([[tool(1, call_id="call-b"), tool(0, call_id="call-a")], [text("已查询")]])
        await self.run_chat()
        messages = requests[1][0]
        self.assertEqual([call["id"] for call in messages[-3]["tool_calls"]], ["call-a", "call-b"])
        self.assertEqual([message["tool_call_id"] for message in messages[-2:]], ["call-a", "call-b"])

    async def test_only_completed_user_attachments_are_allowed(self):  # 允许范围由后端历史决定，不接受助手或失败消息扩张范围
        self.read.return_value = [SimpleNamespace(role="user", content="问题", status="completed", attachments=[SimpleNamespace(file_id="f1", filename="资料.txt", size=1)]), SimpleNamespace(role="user", content="失败问题", status="failed", attachments=[SimpleNamespace(file_id="failed-file")]), SimpleNamespace(role="assistant", content="回答", status="completed", attachments=[SimpleNamespace(file_id="assistant-file")])]
        requests, _ = self.model([[tool()], [text("回答")]])
        with patch.object(service, "execute_tool", return_value="{}") as execute:
            await self.run_chat()
        self.assertEqual(execute.await_args.args[3], {"f1"})
        self.assertIn("read_uploaded_file", [definition["function"]["name"] for definition in requests[0][1]])

    async def test_next_turn_restores_saved_tool_history(self):  # 使用上一轮实际保存的 output 构造下一轮输入
        self.model([[tool()], [text("当前是北京时间。")]])
        await self.run_chat()
        saved = copy.deepcopy(self.update.call_args.kwargs)
        self.read.return_value = [SimpleNamespace(role="user", content="现在几点？", status="completed"), SimpleNamespace(role="assistant", content=saved["content"], output=saved["output"], status="completed"), SimpleNamespace(role="user", content="刚才查到什么时区？", status="completed")]
        requests, _ = self.model([[text("刚才查询的是上海时区。")]])
        with patch.object(service, "execute_tool") as execute:
            await self.run_chat()
        execute.assert_not_called()
        messages = requests[0][0]
        self.assertEqual([item["role"] for item in messages], ["system", "user", "assistant", "tool", "assistant", "user"])
        self.assertEqual(messages[2]["tool_calls"][0]["id"], messages[3]["tool_call_id"])
        self.assertEqual(json.loads(messages[3]["content"])["timezone"], "Asia/Shanghai")
        self.assertEqual(messages[-1]["content"], "刚才查到什么时区？")

    async def test_tool_errors_return_to_model(self):
        for name, arguments in [("unknown", "{}"), ("get_current_time", "{")]:
            with self.subTest(name=name, arguments=arguments):
                requests, _ = self.model([[tool(name=name, arguments=arguments)], [text("无法查询时间")]])
                await self.run_chat()
                self.assertIn("error", json.loads(requests[1][0][-1]["content"]))
                self.assertEqual(self.update.call_args.kwargs["status"], "completed")

    async def test_deleted_file_error_is_returned_to_model(self):
        requests, _ = self.model([[tool(name="read_uploaded_file", arguments='{"file_id":"gone"}')], [text("原附件已删除，无法再次读取。")]])
        with patch.object(service, "execute_tool", side_effect=ValueError("文件已删除或不存在，无法再次读取正文")):
            await self.run_chat()
        self.assertIn("已删除", json.loads(requests[1][0][-1]["content"])["error"])

    async def test_last_request_has_no_tools(self):
        requests, _ = self.model([[tool(call_id=f"call-{index}")] for index in range(4)] + [[text("最终回答")]])
        await self.run_chat()
        self.assertEqual(len(requests), 5)
        self.assertIsNone(requests[-1][1])
        self.assertEqual(self.update.call_args.kwargs["status"], "completed")

    async def test_repeated_calls_stop_at_limit(self):
        requests, _ = self.model([[tool(call_id=f"call-{index}")] for index in range(5)])
        with patch.object(service, "execute_tool", return_value="{}") as execute:
            events = await self.run_chat()
        self.assertEqual(len(requests), 5)
        self.assertEqual(execute.call_count, 4)
        self.assertEqual(self.update.call_args.kwargs["status"], "failed")
        self.assertTrue(events[-1].startswith("event: error"))
        self.assertFalse(any(event.startswith("event: done") for event in events))

    async def test_missing_call_id_does_not_execute(self):
        self.model([[tool(call_id="")]])
        with patch.object(service, "execute_tool") as execute:
            await self.run_chat()
        execute.assert_not_called()
        self.assertEqual(self.update.call_args.kwargs["status"], "failed")

    async def test_cancellation_closes_stream_and_saves_partial_text(self):
        _, closed = self.model([[text("部分正文"), asyncio.CancelledError()]])
        cancel = AsyncMock()
        session = AsyncMock()
        session.__aenter__.return_value = object()
        with patch.object(service, "SessionLocal", return_value=session), patch.object(service, "cancel_message", cancel):
            with self.assertRaises(asyncio.CancelledError):
                await self.run_chat()
        self.assertEqual(closed, [0])
        self.assertEqual(cancel.call_args.args[1:], ("assistant-1", "部分正文"))
        self.assertEqual(cancel.call_args.kwargs["output"][0]["content"][0]["text"], "部分正文")
        self.update.assert_not_called()

    async def test_failure_keeps_tool_records_and_partial_text(self):  # 工具已执行，下一次模型请求输出一半后报错
        self.model([[text("先查时间"), tool()], [text("现在"), text("是"), RuntimeError("模拟模型断线")]])
        events = await self.run_chat()
        saved = self.update.call_args.kwargs
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["content"], "先查时间现在是")
        self.assertEqual([item["type"] for item in saved["output"]], ["message", "function_call", "function_call_output", "message"])
        self.assertEqual(saved["output"][-1]["content"][0]["text"], "现在是")
        self.assertEqual(json.loads(saved["output"][2]["output"])["timezone"], "Asia/Shanghai")
        self.assertTrue(events[-1].startswith("event: error"))

    async def test_cancel_after_tool_keeps_tool_records_and_partial_text(self):
        self.model([[tool()], [text("现在"), asyncio.CancelledError()]])
        cancel = AsyncMock()
        with patch.object(service, "SessionLocal", return_value=AsyncMock()), patch.object(service, "cancel_message", cancel):
            with self.assertRaises(asyncio.CancelledError):
                await self.run_chat()
        saved = cancel.call_args.kwargs["output"]
        self.assertEqual([item["type"] for item in saved], ["function_call", "function_call_output", "message"])
        self.assertEqual(saved[-1]["content"][0]["text"], "现在")
        self.assertEqual(saved[0]["call_id"], saved[1]["call_id"])
        self.update.assert_not_called()

    async def test_closing_frontend_stream_closes_model(self):
        _, closed = self.model([[text("部分正文"), text("未消费正文")]])
        session = AsyncMock()
        cancel = AsyncMock()
        with patch.object(service, "SessionLocal", return_value=session), patch.object(service, "cancel_message", cancel):
            async with aclosing(service.stream_chat_history_events(None, "chat-1")) as stream:
                await anext(stream)
        self.assertEqual(closed, [0])
        self.assertEqual(cancel.call_args.kwargs["output"][0]["content"][0]["text"], "部分正文")
        self.update.assert_not_called()

    async def test_stop_after_tool_notification_does_not_execute(self):  # 收到调用事件后关闭连接，不能继续执行工具
        self.model([[tool()]])
        cancel = AsyncMock()
        with patch.object(service, "SessionLocal", return_value=AsyncMock()), patch.object(service, "cancel_message", cancel), patch.object(service, "execute_tool") as execute:
            async with aclosing(service.stream_chat_history_events(None, "chat-1")) as stream:
                event = await anext(stream)
                self.assertTrue(event.startswith("event: tool_call"))
        execute.assert_not_called()
        self.assertEqual([item["type"] for item in cancel.call_args.kwargs["output"]], ["function_call"])

if __name__ == "__main__":
    unittest.main()
