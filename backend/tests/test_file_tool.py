import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from backend.app.chat import service
from backend.app.core.database import Base
from backend.app.core.database import register_models
from backend.app.models.chat import Chat, Message
from backend.app.models.file import File
from backend.app.repositories.chat import create_message
from backend.app.tools.files import read_uploaded_file
from backend.app.tools.registry import execute_tool, get_available_tools

class FileToolTests(unittest.IsolatedAsyncioTestCase):  # 全部使用内存数据库与模拟模型
    async def asyncSetUp(self):
        register_models()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.sessions() as session:
            session.add(Chat(id="chat", title="附件读取"))
            session.add_all([File(id="f1", filename="资料.txt", storage_path="f1.txt", status="completed", text_content="甲乙😀丙丁戊", size=20), File(id="secret", filename="其他文件.txt", storage_path="secret.txt", status="completed", text_content="不允许读取的内容"), File(id="pending", filename="pending.txt", storage_path="p.txt", status="processing"), File(id="failed", filename="failed.txt", storage_path="f.txt", status="failed")])
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_unicode_pagination_uses_characters(self):
        async with self.sessions() as session:
            first = await read_uploaded_file("f1", session, {"f1"}, max_chars=3)
            second = await read_uploaded_file("f1", session, {"f1"}, offset=first["next_offset"], max_chars=3)
        self.assertEqual(first["text_content"], "甲乙😀")
        self.assertEqual(first["total_chars"], 6)
        self.assertEqual(first["next_offset"], 3)
        self.assertTrue(first["has_more"])
        self.assertEqual(second["text_content"], "丙丁戊")
        self.assertFalse(second["has_more"])
        self.assertIsNone(second["next_offset"])

    async def test_default_limit_is_6000_and_explicit_maximum_is_12000(self):
        async with self.sessions() as session:
            session.add(File(id="long", filename="long.txt", storage_path="l.txt", status="completed", text_content="文" * 12001))
            await session.commit()
            self.assertEqual(len((await read_uploaded_file("long", session, {"long"}))["text_content"]), 6000)
            self.assertEqual(len((await read_uploaded_file("long", session, {"long"}, max_chars=12000))["text_content"]), 12000)

    async def test_offset_at_or_after_end_returns_empty_page(self):
        async with self.sessions() as session:
            for offset in [6, 20]:
                result = await read_uploaded_file("f1", session, {"f1"}, offset=offset)
                self.assertEqual(result["text_content"], "")
                self.assertFalse(result["has_more"])
                self.assertIsNone(result["next_offset"])

    async def test_unattached_file_rejected_before_database_query(self):
        session = AsyncMock()
        for file_id in ["secret", [], None]:
            with self.assertRaises(ValueError):
                await read_uploaded_file(file_id, session, {"f1"})
        session.execute.assert_not_awaited()

    async def test_invalid_offsets_and_limits_are_rejected(self):
        session = AsyncMock()
        for params in [{"offset": -1}, {"offset": True}, {"offset": "0"}, {"offset": 2147483648}, {"max_chars": 0}, {"max_chars": 12001}, {"max_chars": True}, {"max_chars": 1.5}]:
            with self.subTest(params=params), self.assertRaises(ValueError):
                await read_uploaded_file("f1", session, {"f1"}, **params)
        session.execute.assert_not_awaited()

    async def test_missing_or_unprocessed_file_is_not_readable(self):
        async with self.sessions() as session:
            for file_id in ["missing", "pending", "failed"]:
                with self.assertRaises(ValueError):
                    await read_uploaded_file(file_id, session, {file_id})

    async def test_registry_preserves_time_tool_and_controls_available_tools(self):
        names = lambda ids: [definition["function"]["name"] for definition in get_available_tools(ids)]
        self.assertEqual(names(set()), ["get_current_time"])
        self.assertEqual(set(names({"f1"})), {"get_current_time", "read_uploaded_file", "search_uploaded_files"})
        result = json.loads(await execute_tool("get_current_time", "{}", None, set()))
        self.assertEqual(result["timezone"], "Asia/Shanghai")
        async with self.sessions() as session:
            result = json.loads(await execute_tool("read_uploaded_file", '{"file_id":"f1","max_chars":2}', session, {"f1"}))
            self.assertEqual(result["text_content"], "甲乙")

    async def test_model_cannot_override_internal_tool_parameters(self):
        session = AsyncMock()
        for arguments in ['{"file_id":"f1","session":{}}', '{"file_id":"secret","allowed_file_ids":["secret"]}', '[]', '{']:
            with self.assertRaises(ValueError):
                await execute_tool("read_uploaded_file", arguments, session, {"f1"})
        session.execute.assert_not_awaited()

    async def test_query_returns_substring_instead_of_full_body(self):
        statements = []
        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
        try:
            async with self.sessions() as session:
                await read_uploaded_file("f1", session, {"f1"}, max_chars=2)
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
        self.assertEqual(len(statements), 1)
        self.assertIn("substr(files.text_content", statements[0])
        self.assertNotIn("files.storage_path", statements[0])

    async def run_tool_chat(self, target="f1", paginated=False):  # 真实执行查询和数据库保存，只替换模型输出
        async with self.sessions() as session:
            await create_message(session, "chat", "user", "总结附件内容", file_ids=["f1"])
        requests = []
        async def stream(messages, tools):
            turn = len(requests)
            requests.append((copy.deepcopy(messages), copy.deepcopy(tools)))
            if turn == 0 or paginated and turn == 1:
                params = {"file_id": target, "offset": 3 * turn, "max_chars": 3 if paginated else 6000}
                yield {"type": "tool_call_delta", "tool_calls": [{"index": 0, "id": f"read-{turn}", "function": {"name": "read_uploaded_file", "arguments": json.dumps(params)}}]}
            else:
                yield {"type": "text_delta", "text": "基于读取结果的回答"}
        with patch.object(service, "prepare_auto_reference", AsyncMock(return_value=[])), patch.object(service, "stream_model_events", stream), patch.object(service, "settings", SimpleNamespace(llm_model="test", llm_context_window=32768, llm_max_output_tokens=4096, llm_context_margin=1024)), patch.object(service, "logger"):
            async with self.sessions() as session:
                events = [event async for event in service.stream_chat_history_events(session, "chat")]
        async with self.sessions() as session:
            saved = (await session.execute(select(Message).where(Message.role == "assistant"))).scalar_one()
        return requests, events, saved

    async def test_end_to_end_manifest_tool_result_and_persistence(self):
        requests, events, saved = await self.run_tool_chat()
        first = requests[0][0]
        self.assertIn("read_uploaded_file", first[0]["content"])
        self.assertIn('"file_id": "f1"', first[-1]["content"])
        self.assertNotIn("甲乙😀丙丁戊", first[-1]["content"])
        self.assertNotIn("不允许读取的内容", str(first))
        self.assertEqual(json.loads(requests[1][0][-1]["content"])["text_content"], "甲乙😀丙丁戊")
        self.assertEqual([item["type"] for item in saved.output], ["function_call", "function_call_output", "message"])
        self.assertEqual(saved.status, "completed")
        self.assertTrue(events[-1].startswith("event: done"))

    async def test_multiple_pages_are_returned_to_model_in_order(self):
        requests, _, saved = await self.run_tool_chat(paginated=True)
        results = [json.loads(item["output"]) for item in saved.output if item["type"] == "function_call_output"]
        self.assertEqual(len(requests), 3)
        self.assertEqual("".join(item["text_content"] for item in results), "甲乙😀丙丁戊")
        self.assertTrue(results[0]["has_more"])
        self.assertFalse(results[1]["has_more"])

    async def test_unauthorized_model_call_returns_error_without_leaking_body(self):
        requests, _, saved = await self.run_tool_chat(target="secret")
        self.assertIn("error", json.loads(requests[1][0][-1]["content"]))
        self.assertNotIn("不允许读取的内容", str(requests))
        self.assertEqual(saved.status, "completed")

if __name__ == "__main__":
    unittest.main()
