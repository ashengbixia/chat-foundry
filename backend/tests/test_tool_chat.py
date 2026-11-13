import asyncio  # 模拟读取流时取消
import copy  # 保存每次模型请求时的消息快照
import json
import unittest
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from backend.app.chat import service
from backend.app.llm.client import ModelStreamError
from backend.app.tools.registry import get_available_tools
from backend.app.chat.context import build_model_messages
from backend.app.tools.runtime import ToolArguments, ToolSpec, registry
from backend.app.tools.local import read_webpage as webpage
from backend.app.tools.local import search_web as web_search
from backend.app.llm.budget import estimate_input_tokens

def text(value: str) -> dict:  # 构造正文事件
    return {"type": "text_delta", "text": value}

def tool(index: int = 0, name: str = "get_current_time", arguments: str = "{}", call_id: str = "call-1") -> dict:  # 构造工具调用事件
    return {"type": "tool_call_delta", "tool_calls": [{"index": index, "id": call_id, "function": {"name": name, "arguments": arguments}}]}

class ToolChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):  # 只模拟仓库与模型，不读写真实数据库或请求真实模型
        for name, value in {"resolve_chat_files": AsyncMock(return_value=set()), "describe_resources": AsyncMock(return_value=[]), "prepare_auto_reference": AsyncMock(return_value=[])}.items():
            mock = patch.object(service, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        history = [SimpleNamespace(role="user", content="现在几点？", status="completed")]
        self.read = AsyncMock(return_value=history)
        self.create = AsyncMock(return_value=SimpleNamespace(id="assistant-1"))
        self.update = AsyncMock()
        self.logger = Mock()
        for name, value in {"resolve_knowledge_scope": AsyncMock(return_value=([], set())), "list_recent_messages": self.read, "create_message": self.create, "update_message": self.update, "logger": self.logger, "settings": SimpleNamespace(llm_model="test-model", llm_context_window=32768, llm_max_output_tokens=4096, llm_context_margin=1024)}.items():
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

    async def run_chat(self, web_search_enabled=False):  # 收集发送到前端的 SSE 事件
        return [event async for event in service.stream_chat_history_events(None, "chat-1", web_search_enabled=web_search_enabled)]

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

    async def test_budget_failure_after_tools_saves_useful_body_and_error_stage(self):
        self.model([[tool()], [text('不应请求第二次模型')]])
        with patch.object(service, 'execute_tool', AsyncMock(return_value=json.dumps({'result': 'x' * 100000}))):
            events = await self.run_chat()
        saved = self.update.call_args.kwargs
        self.assertEqual(saved['status'], 'failed')
        self.assertIn('已保留取得的结果', saved['content'])
        metadata = next(item for item in saved['output'] if item['type'] == 'turn_metadata')
        self.assertEqual(metadata['error_info']['code'], 'input_budget_exceeded')
        self.assertTrue(any('event: text_delta' in event for event in events))
        self.assertTrue(events[-1].startswith('event: error'))

    async def test_final_phase_tool_request_keeps_partial_as_commentary_and_saves_report(self):
        rounds = [[tool(call_id=f'time-{index}')] for index in range(4)]
        rounds.append([text('我尝试打开一个尚未核实的地址（'), tool(call_id='forbidden')])
        requests, _ = self.model(rounds)
        with patch.object(service.settings, 'chat_response_max_tool_call_iterations', 4, create=True):
            await self.run_chat()
        self.assertIsNone(requests[-1][1])
        saved = self.update.call_args.kwargs
        self.assertEqual(saved['status'], 'failed')
        self.assertNotIn('我尝试打开', saved['content'])
        self.assertIn('已保留取得的结果', saved['content'])
        metadata = next(item for item in saved['output'] if item['type'] == 'turn_metadata')
        self.assertEqual(metadata['error_info']['code'], 'model_ignored_final_phase')

    async def test_new_async_tool_works_without_chat_specific_branch(self):
        class EchoArguments(ToolArguments):
            text: str
        async def echo(text):
            return {'echo': text}
        spec = ToolSpec('test_echo', '测试回显', '返回指定文字', EchoArguments, echo)
        requests, _ = self.model([[tool(name='test_echo', arguments='{"text":"接入成功"}')], [text('已完成')]])
        with patch.dict(registry._tools, {'test_echo': spec}):
            events = await self.run_chat()
        self.assertIn('test_echo', [item['function']['name'] for item in requests[0][1]])
        self.assertEqual(json.loads(requests[1][0][-1]['content']), {'echo': '接入成功'})
        saved = self.update.call_args.kwargs['output']
        self.assertEqual(next(item for item in saved if item['type'] == 'function_call')['display_name'], '测试回显')
        self.assertTrue(any('测试回显' in event for event in events))

    async def test_webpage_tool_paginates_and_saves_real_web_citations(self):
        self.read.return_value = [SimpleNamespace(role='user', content='读取 https://example.com/article', status='completed')]
        requests, _ = self.model([[tool(name='read_webpage', arguments='{"url":"https://example.com/article","max_chars":3}', call_id='web1')], [tool(name='read_webpage', arguments='{"url":"https://example.com/article","offset":3}', call_id='web2')], [text('网页正文[1][2]')]])
        document = {'url': 'https://example.com/article', 'title': '网页文章', 'text': '第一段第二段', 'retrieved_at': 100}
        with patch.object(webpage, 'fetch_page', AsyncMock(return_value=document)) as fetch:
            events = await self.run_chat()
        fetch.assert_awaited_once()
        first, second = [json.loads(requests[index][0][-1]['content']) for index in (1, 2)]
        self.assertEqual(first['chunks'][0]['citation_id'], 1)
        self.assertEqual(second['chunks'][0]['citation_id'], 2)
        self.assertEqual(second['chunks'][0]['content'], '第二段')
        self.assertEqual(self.update.call_args.kwargs['status'], 'completed')
        saved = [json.loads(item['output']) for item in self.update.call_args.kwargs['output'] if item['type'] == 'function_call_output']
        self.assertEqual(saved[0]['chunks'][0]['url'], document['url'])
        self.assertTrue(any('读取网页正文' in event for event in events))

    async def test_search_then_read_only_cites_fetched_page(self):
        self.read.return_value = [SimpleNamespace(role='user', content='请联网搜索Python官方文档', status='completed')]
        requests, _ = self.model([[tool(name='search_web', arguments='{"query":"Python official documentation"}', call_id='s')], [tool(name='read_webpage', arguments='{"url":"https://example.com/docs"}', call_id='r')], [text('官方文档内容[1]')]])
        found = [{'title': 'Docs', 'url': 'https://example.com/docs', 'snippet': 'Candidate'}]
        document = {'url': found[0]['url'], 'title': 'Docs', 'text': 'Verified page text', 'retrieved_at': 100}
        with patch.object(web_search, 'search_provider', AsyncMock(return_value=found)), patch.object(webpage, 'fetch_page', AsyncMock(return_value=document)):
            await self.run_chat(web_search_enabled=True)
        self.assertNotIn('citation_id', requests[1][0][-1]['content'])
        self.assertEqual(json.loads(requests[2][0][-1]['content'])['chunks'][0]['citation_id'], 1)
        self.assertEqual(self.update.call_args.kwargs['status'], 'completed')

    async def test_search_switch_is_enforced_without_rewriting_user_message(self):
        self.read.return_value = [SimpleNamespace(role='user', content='python', status='completed')]
        requests, _ = self.model([[tool(name='search_web', arguments='{"query":"python"}')], [text('请先开启联网搜索')]])
        with patch.object(web_search, 'search_provider', AsyncMock()) as provider:
            await self.run_chat(web_search_enabled=False)
        provider.assert_not_awaited()
        self.assertNotIn('search_web', [item['function']['name'] for item in requests[0][1]])
        self.assertIn('权限', json.loads(requests[1][0][-1]['content'])['error'])
        self.assertEqual(next(item['content'] for item in requests[0][0] if item['role'] == 'user'), 'python')
        requests, _ = self.model([[text('你好，想了解Python的哪方面？')]])
        with patch.object(web_search, 'search_provider', AsyncMock()) as provider:
            await self.run_chat(web_search_enabled=True)
        provider.assert_not_awaited()
        self.assertIn('search_web', [item['function']['name'] for item in requests[0][1]])

    async def test_repeated_search_results_only_compact_model_input(self):
        service.resolve_chat_files.return_value = {"f1"}
        self.read.return_value = [SimpleNamespace(role="user", content="FT-204是什么", status="completed", attachments=[SimpleNamespace(file_id="f1", filename="资料.txt", size=1)])]
        chunk = {"file_id": "f1", "filename": "资料.txt", "chunk_id": "c1", "start_offset": 0, "end_offset": 1000, "content": "资料" * 500}
        original = json.dumps({"status": "ok", "chunks": [chunk]}, ensure_ascii=False)
        requests, _ = self.model([[tool(name="search_uploaded_files", call_id="a")], [tool(name="search_uploaded_files", call_id="b")], [text("FT-204是泵出口流量。")]])
        with patch.object(service, "execute_tool", AsyncMock(return_value=original)):
            events = await self.run_chat()
        self.assertEqual(len(requests), 3)
        result = json.loads(requests[-1][0][-1]["content"])
        self.assertEqual(result["chunks"], [])
        self.assertEqual(len(result["omitted_duplicate_chunks"]), 1)
        saved = self.update.call_args.kwargs
        self.assertEqual(saved["status"], "completed")
        expected = json.loads(original)
        expected["chunks"][0]["citation_id"] = 1
        snapshots = [json.loads(item["output"]) for item in saved["output"] if item["type"] == "function_call_output"]
        self.assertEqual(snapshots, [expected, expected])  # 完整原文快照及后端编号均保存，重复检索沿用同一编号
        self.assertEqual(json.loads(requests[1][0][-1]["content"])["chunks"][0]["citation_id"], 1)
        streamed = [json.loads(event.split("data: ", 1)[1])["output"] for event in events if event.startswith("event: tool_result")]
        self.assertEqual([json.loads(value) for value in streamed], snapshots)

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
        self.assertEqual([item["type"] for item in saved["output"]], ["function_call", "function_call_output", "message", "turn_metadata"])
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

    async def test_only_persisted_chat_files_are_allowed(self):  # 读取范围由持久关联决定，助手或失败消息不能扩大范围
        service.resolve_chat_files.return_value = {"f1"}
        self.read.return_value = [SimpleNamespace(role="user", content="问题", status="completed", attachments=[SimpleNamespace(file_id="f1", filename="资料.txt", size=1)]), SimpleNamespace(role="user", content="失败问题", status="failed", attachments=[SimpleNamespace(file_id="failed-file")]), SimpleNamespace(role="assistant", content="回答", status="completed", attachments=[SimpleNamespace(file_id="assistant-file")])]
        requests, _ = self.model([[tool()], [text("回答")]])
        with patch.object(service, "execute_tool", return_value="{}") as execute:
            await self.run_chat()
        self.assertEqual(execute.await_args.kwargs['context'].allowed_file_ids, {"f1"})
        self.assertIn("read_uploaded_file", [definition["function"]["name"] for definition in requests[0][1]])

    async def test_budget_trim_removes_attachment_access_and_recalculates_tools(self):
        self.read.return_value = [SimpleNamespace(role="user", content="旧问题" * 2000, status="completed", attachments=[SimpleNamespace(file_id="old-file", filename="旧附件.txt", size=1)]), SimpleNamespace(role="assistant", content="旧回答", status="completed"), SimpleNamespace(role="user", content="最新问题", status="completed")]
        original = build_model_messages(self.read.return_value)
        current = [original[0], original[-1]]
        budget = estimate_input_tokens(current, get_available_tools(set())) + 1000
        requests, _ = self.model([[tool(name="read_uploaded_file", arguments='{"file_id":"old-file"}')], [text("附件已不在当前上下文，请重新添加。")]])
        config = SimpleNamespace(llm_model="test", llm_context_window=budget + 10, llm_max_output_tokens=10, llm_context_margin=0)
        with patch.object(service, "settings", config):
            await self.run_chat()
        self.assertEqual({item["function"]["name"] for item in requests[0][1]}, {"get_current_time", "read_webpage"})
        self.assertEqual(sum(item["role"] == "user" for item in requests[0][0]), 1)
        self.assertIn("权限", json.dumps(requests[1][0], ensure_ascii=False))
        self.assertIsNone(requests[1][1])

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
        self.assertEqual([item["role"] for item in messages], ["system", "system", "user", "assistant", "tool", "assistant", "user"])
        self.assertEqual(messages[3]["tool_calls"][0]["id"], messages[4]["tool_call_id"])
        self.assertEqual(json.loads(messages[4]["content"])["timezone"], "Asia/Shanghai")
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
        with patch.object(service.settings, 'chat_response_max_tool_call_iterations', 4, create=True):
            await self.run_chat()
        self.assertEqual(len(requests), 5)
        self.assertIsNone(requests[-1][1])
        self.assertEqual(self.update.call_args.kwargs["status"], "completed")

    async def test_repeated_calls_stop_at_limit(self):
        requests, _ = self.model([[tool(call_id=f"call-{index}")] for index in range(5)])
        with patch.object(service.settings, 'chat_response_max_tool_call_iterations', 4, create=True), patch.object(service, "execute_tool", return_value="{}") as execute:
            events = await self.run_chat()
        self.assertEqual(len(requests), 5)
        self.assertEqual(execute.call_count, 4)
        self.assertEqual(self.update.call_args.kwargs["status"], "failed")
        self.assertTrue(events[-1].startswith("event: error"))
        self.assertFalse(any(event.startswith("event: done") for event in events))

    async def test_default_loop_can_continue_beyond_five_requests_and_twelve_calls(self):
        rounds = [[tool(index=index, call_id=f'call-0-{index}') for index in range(14)]]
        rounds += [[tool(call_id=f'call-{index}')] for index in range(1, 6)]
        requests, _ = self.model(rounds + [[text('完成所有步骤')]])
        with patch.object(service, 'execute_tool', AsyncMock(return_value='{}')) as execute:
            events = await self.run_chat()
        self.assertEqual(len(requests), 7)
        self.assertEqual(execute.await_count, 19)
        self.assertTrue(events[-1].startswith('event: done'))

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
        self.assertEqual(saved["content"], "现在是")
        self.assertEqual([item["type"] for item in saved["output"]], ["message", "function_call", "function_call_output", "message", "turn_metadata"])
        self.assertEqual(saved["output"][-2]["content"][0]["text"], "现在是")
        self.assertEqual(json.loads(saved["output"][2]["output"])["timezone"], "Asia/Shanghai")
        self.assertTrue(events[-1].startswith("event: error"))

    async def test_truncated_answer_keeps_tool_records_and_public_error(self):
        self.model([[tool()], [text("部分回答"), ModelStreamError("模型输出达到长度限制，本次回答不完整")]])
        events = await self.run_chat()
        saved = self.update.call_args.kwargs
        self.assertEqual(saved["status"], "failed")
        self.assertIn("长度限制", saved["error"])
        self.assertEqual([item["type"] for item in saved["output"]], ["function_call", "function_call_output", "message", "turn_metadata"])
        self.assertIn("长度限制", events[-1])

    async def test_incomplete_tool_stream_is_not_executed(self):
        self.model([[tool(), ModelStreamError("模型连接已结束，但没有收到完成标记")]])
        with patch.object(service, "execute_tool") as execute:
            events = await self.run_chat()
        execute.assert_not_called()
        self.assertEqual(self.update.call_args.kwargs["status"], "failed")
        self.assertIn("完成标记", events[-1])

    async def test_legacy_stream_shows_public_error_without_done(self):
        async def stream(messages):
            yield "部分正文"
            raise ModelStreamError("模型连接已结束，但没有收到完成标记")
        with patch.object(service, "stream_reply", stream):
            events = [event async for event in service.stream_chat_events("问题")]
        self.assertTrue(events[0].startswith("event: text_delta"))
        self.assertIn("完成标记", events[-1])
        self.assertFalse(any(event.startswith("event: done") for event in events))

    async def test_cancel_after_tool_keeps_tool_records_and_partial_text(self):
        self.model([[tool()], [text("现在"), asyncio.CancelledError()]])
        cancel = AsyncMock()
        with patch.object(service, "SessionLocal", return_value=AsyncMock()), patch.object(service, "cancel_message", cancel):
            with self.assertRaises(asyncio.CancelledError):
                await self.run_chat()
        saved = cancel.call_args.kwargs["output"]
        self.assertEqual([item["type"] for item in saved], ["function_call", "function_call_output", "message", "turn_metadata"])
        self.assertEqual(saved[-2]["content"][0]["text"], "现在")
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
        self.assertEqual([item["type"] for item in cancel.call_args.kwargs["output"]], ["function_call", "turn_metadata"])

    async def test_reasoning_commentary_and_answer_persist_separately(self):
        requests, _ = self.model([[{'type': 'reasoning_delta', 'text': '模型返回的思考'}, text('我先查询'), tool()], [text('最终回答')]])
        events = await self.run_chat()
        saved = self.update.call_args.kwargs
        self.assertEqual(saved['content'], '最终回答')
        self.assertTrue(any(item.get('type') == 'reasoning' for item in saved['output']))
        self.assertTrue(any(item.get('phase') == 'commentary' for item in saved['output']))
        meta = next(item for item in saved['output'] if item['type'] == 'turn_metadata')
        self.assertIn('get_current_time', meta['available_tools'])
        self.assertGreaterEqual(meta['duration_ms'], 0)
        self.assertTrue(any('event: commentary' in event for event in events))
        self.assertEqual(next(item for item in requests[1][0] if item.get('tool_calls'))['reasoning_content'], '模型返回的思考')

if __name__ == "__main__":
    unittest.main()
