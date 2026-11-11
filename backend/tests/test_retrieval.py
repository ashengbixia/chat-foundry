import asyncio
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from backend.app.core import database
from backend.app.models.file import File
from backend.app.models.file_chunk import FileChunk
from backend.app.rag import retrieval
from backend.app.rag.vector_store import get_index_key
from backend.app.tools.files import search_uploaded_files
from backend.app.tools.registry import execute_tool, get_available_tools

class RetrievalTests(unittest.IsolatedAsyncioTestCase):  # 临时SQL库与模拟向量查询，不加载模型
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        await database.create_tables(self.engine)
        self.embed = AsyncMock(return_value=[1.0, 0.0])
        self.search = Mock(return_value={"ids": [["c2", "c1"]], "distances": [[0.1, 0.3]], "documents": [["不能信任的旧正文", "旧正文"]]})
        for current in [patch.object(retrieval, "embed_query", self.embed), patch.object(retrieval, "search_chunk_vectors", self.search), patch.object(retrieval, "logger", Mock())]:
            current.start()
            self.addCleanup(current.stop)
        key = get_index_key()
        async with self.sessions() as session:
            session.add_all([File(id="f1", filename="现行差旅规定.txt", storage_path="f1.txt", status="completed", index_status="ready", index_key=key), File(id="f2", filename="设备规定.txt", storage_path="f2.txt", status="completed", index_status="ready", index_key=key), File(id="secret", filename="其他用户文件.txt", storage_path="secret.txt", status="completed", index_status="ready", index_key=key)])
            await session.flush()
            session.add_all([FileChunk(id="c1", file_id="f1", chunk_index=0, content="住宿每晚437元", start_offset=0, end_offset=9), FileChunk(id="c2", file_id="f1", chunk_index=1, content="餐费每天73元", start_offset=100, end_offset=107), FileChunk(id="c3", file_id="f2", chunk_index=0, content="设备借用4天", start_offset=0, end_offset=6), FileChunk(id="secret-chunk", file_id="secret", chunk_index=0, content="绝不能泄露的私密正文", start_offset=0, end_offset=11)])
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_exact_identifier_wins_in_fusion_and_respects_scope(self):
        async with self.sessions() as session:
            await session.execute(update(FileChunk).where(FileChunk.id == "c1").values(content="FT-204：泵出口流量，单位为立方米/小时。", end_offset=28))
            await session.execute(update(FileChunk).where(FileChunk.id == "c2").values(content="FT-2040不是这个位号"))
            await session.execute(update(FileChunk).where(FileChunk.id == "secret-chunk").values(content="FT-204：秘密内容"))
            await session.commit()
        result = await self.retrieve(query="ft-204什么含义")
        self.assertEqual([chunk["chunk_id"] for chunk in result["chunks"]], ["c1"])
        self.assertEqual(result["chunks"][0]["match_type"], "exact")
        self.assertEqual(result["chunks"][0]["distance"], 0.3)
        self.embed.assert_awaited_once()
        self.search.assert_called_once()

    async def retrieve(self, ids=None, query="差旅标准", limit=5):
        async with self.sessions() as session:
            return await retrieval.retrieve_file_chunks(session, query, {"f1"} if ids is None else ids, limit)

    async def test_results_keep_ranking_but_use_sql_body_and_filename(self):
        result = await self.retrieve()
        self.assertEqual(result["status"], "ok")
        self.assertEqual([item["chunk_id"] for item in result["chunks"]], ["c2", "c1"])
        self.assertEqual(result["chunks"][0]["content"], "餐费每天73元")
        self.assertEqual(result["chunks"][0]["filename"], "现行差旅规定.txt")
        self.assertEqual(result["chunks"][0]["start_offset"], 100)
        self.assertNotIn("旧正文", str(result))
        self.search.assert_called_once_with([1.0, 0.0], ["f1"], 20)

    async def test_keyword_fallback_and_distance_threshold(self):
        self.embed.side_effect = RuntimeError('offline')
        result = await self.retrieve(query='住宿')
        self.assertEqual([item['chunk_id'] for item in result['chunks']], ['c1'])
        self.assertEqual(result['chunks'][0]['match_type'], 'keyword')
        self.assertIn('关键词候选', result['notice'])
        self.embed.side_effect = None
        self.search.return_value = {'ids': [['c1', 'c2']], 'distances': [[0.99, 0.1]]}
        result = await self.retrieve()
        self.assertEqual([item['chunk_id'] for item in result['chunks']], ['c2'])

    async def test_unauthorized_file_rejected_before_sql_and_embedding(self):
        session = AsyncMock()
        for file_id in ["secret", [], 1, ""]:
            with self.subTest(file_id=file_id), self.assertRaises(ValueError):
                await search_uploaded_files("问题", session, {"f1"}, file_id=file_id)
        with self.assertRaises(ValueError):
            await search_uploaded_files("问题", session, set())
        session.execute.assert_not_awaited()
        self.embed.assert_not_awaited()

    async def test_model_cannot_inject_internal_parameters_or_omit_query(self):
        session = AsyncMock()
        for params in [{"query": "问题", "session": {}}, {"query": "问题", "allowed_file_ids": ["secret"]}, {"query": "问题", "file_ids": ["secret"]}, {}, []]:
            with self.subTest(params=params), self.assertRaises(ValueError):
                await execute_tool("search_uploaded_files", json.dumps(params), session, {"f1"})
        session.execute.assert_not_awaited()

    async def test_public_input_validation_runs_before_sql(self):
        for query in [None, 1, "", " \n", "文" * 1001]:
            with self.subTest(query=query), self.assertRaises(ValueError):
                await self.retrieve(query=query)
        for limit in [0, 11, True, "5"]:
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                await self.retrieve(limit=limit)
        self.embed.assert_not_awaited()
        self.search.assert_not_called()

    async def test_each_unavailable_state_is_explicit_and_does_not_compute(self):
        cases = [({"status": "processing"}, "text_not_ready"), ({"index_status": "pending"}, "index_pending"), ({"index_status": "pending", "index_task_id": "queued"}, "indexing"), ({"index_status": "indexing"}, "indexing"), ({"index_status": "failed"}, "index_failed"), ({"index_key": "old-model"}, "model_changed")]
        for changes, reason in cases:
            async with self.sessions() as session:
                await session.execute(update(File).where(File.id == "f1").values(status="completed", index_status="ready", index_key=get_index_key(), index_task_id=None))
                await session.execute(update(File).where(File.id == "f1").values(**changes))
                await session.commit()
            result = await self.retrieve()
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["unavailable_files"][0]["reason"], reason)
        result = await self.retrieve(ids={"deleted"})
        self.assertEqual(result["unavailable_files"][0]["reason"], "missing")
        self.embed.assert_not_awaited()
        self.search.assert_not_called()

    async def test_mixed_scope_queries_only_ready_files_and_reports_others(self):
        async with self.sessions() as session:
            await session.execute(update(File).where(File.id == "f2").values(index_status="failed"))
            await session.commit()
        result = await self.retrieve(ids={"f1", "f2", "deleted"})
        self.assertEqual(result["status"], "ok")
        self.assertEqual({item["reason"] for item in result["unavailable_files"]}, {"index_failed", "missing"})
        self.search.assert_called_once_with([1.0, 0.0], ["f1"], 20)

    async def test_orphan_foreign_duplicate_and_invalid_distances_are_ignored(self):
        self.search.return_value = {"ids": [["old-chunk", "secret-chunk", "c1", "c1", "c2", 1]], "distances": [[0.0, 0.0, 0.2, 0.3, float("nan"), 0.2]]}
        result = await self.retrieve()
        self.assertEqual([item["chunk_id"] for item in result["chunks"]], ["c1"])
        self.assertEqual(result["chunks"][0]["distance"], 0.2)
        self.assertNotIn("私密正文", str(result))
        self.assertNotIn("其他用户", str(result))

    async def test_file_rebuilt_or_deleted_during_vector_query_is_not_returned(self):
        async def change_index(query):
            async with self.sessions() as session:
                await session.execute(update(File).where(File.id == "f1").values(index_status="indexing", index_task_id="new-job"))
                await session.commit()
            return [1.0, 0.0]
        self.embed.side_effect = change_index
        result = await self.retrieve()
        self.assertEqual(result["chunks"], [])
        self.assertEqual(result["unavailable_files"][0]["reason"], "indexing")
        async def delete_file(query):
            async with self.sessions() as session:
                await session.execute(delete(FileChunk).where(FileChunk.file_id == "f1"))
                await session.execute(delete(File).where(File.id == "f1"))
                await session.commit()
            return [1.0, 0.0]
        self.embed.side_effect = delete_file
        async with self.sessions() as session:
            await session.execute(update(File).where(File.id == "f1").values(index_status="ready", index_task_id=None))
            await session.commit()
        result = await self.retrieve()
        self.assertEqual(result["chunks"], [])
        self.assertEqual(result["unavailable_files"][0]["reason"], "missing")

    async def test_empty_candidates_are_distinct_from_unavailable_index(self):
        self.search.return_value = {"ids": [[]], "distances": [[]]}
        result = await self.retrieve()
        self.assertEqual(result["status"], "no_matches")
        self.assertEqual(result["unavailable_files"], [])

    async def test_body_budget_reports_actual_offsets_and_truncation(self):
        with patch.object(retrieval, "MAX_RESULT_CHARS", 10):
            result = await self.retrieve()
        self.assertEqual(sum(len(item["content"]) for item in result["chunks"]), 10)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["chunks"][1]["end_offset"], 3)
        self.assertTrue(result["chunks"][1]["truncated"])

    async def test_dependency_errors_are_safe_but_cancellation_propagates(self):
        self.embed.side_effect = RuntimeError("private-path-and-key")
        with self.assertRaises(ValueError) as error:
            await self.retrieve()
        self.assertNotIn("private", str(error.exception))
        self.embed.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.retrieve()

    async def test_registry_only_offers_attachment_search_when_scope_exists(self):
        self.assertNotIn("search_uploaded_files", [tool["function"]["name"] for tool in get_available_tools(set())])
        async with self.sessions() as session:
            result = json.loads(await execute_tool("search_uploaded_files", '{"query":"住宿","file_id":"f1","limit":1}', session, {"f1", "f2"}))
        self.assertEqual(len(result["chunks"]), 1)
        self.search.assert_called_once_with([1.0, 0.0], ["f1"], 4)

class RetrievalIntegrationTests(unittest.TestCase):
    def test_real_chroma_sql_tool_stream_and_saved_history(self):  # 子进程关闭Chroma句柄，真实存储加模拟模型完成整条线路
        code = r'''
import asyncio, copy, json, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from backend.app.core import database
from backend.app.models.chat import Chat, Message
from backend.app.models.file import File
from backend.app.models.file_chunk import FileChunk
from backend.app.repositories.chat import create_message
from backend.app.chat import service
from backend.app.chat.context import build_model_messages
from backend.app.rag import retrieval, vector_store
root = Path(sys.argv[1])
vector_store.VECTOR_DIR = root / "vector_db"
requests = []
async def stream(messages, tools):
    requests.append(copy.deepcopy(messages))
    if len(requests) == 1:
        assert "search_uploaded_files" in [item["function"]["name"] for item in tools]
        yield {"type":"tool_call_delta", "tool_calls":[{"index":0,"id":"search-1","function":{"name":"search_uploaded_files","arguments":'{"query":"住宿上限"}'}}]}
    else:
        result = json.loads(messages[-1]["content"])
        assert result["chunks"][0]["content"] == "住宿上限437元", result
        assert result["chunks"][0]["filename"] == "验证资料.txt"
        yield {"type":"text_delta", "text":"根据验证资料.txt，住宿上限437元。"}
async def main():
    engine = create_async_engine(f"sqlite+aiosqlite:///{(root / 'test.db').as_posix()}")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    await database.create_tables(engine)
    key = vector_store.get_index_key()
    async with sessions() as session:
        session.add(Chat(id="chat", title="检索验收"))
        session.add(File(id="file", filename="验证资料.txt", storage_path="file.txt", status="completed", index_status="ready", index_key=key))
        await session.flush()
        session.add(FileChunk(id="chunk", file_id="file", chunk_index=0, content="住宿上限437元", start_offset=0, end_offset=9))
        await session.commit()
        await create_message(session, "chat", "user", "附件中住宿上限多少？", file_ids=["file"])
    vector_store.save_chunk_vectors([{"id":"chunk", "file_id":"file", "chunk_index":0, "content":"故意过期的向量库正文"}], [[1.,0.]])
    config = SimpleNamespace(llm_model="mock",llm_context_window=32768,llm_max_output_tokens=4096,llm_context_margin=1024)
    with patch.object(service,"stream_model_events",stream), patch.object(service,"settings",config), patch.object(retrieval,"embed_query",AsyncMock(return_value=[1.,0.])):
        async with sessions() as session:
            events = [item async for item in service.stream_chat_history_events(session,"chat")]
    assert len(requests) == 2
    assert any("event: tool_result" in item for item in events)
    assert events[-1].startswith("event: done")
    async with sessions() as session:
        saved = (await session.execute(select(Message).where(Message.role=="assistant"))).scalar_one()
        assert saved.status == "completed"
        assert any(item.get("type") == "function_call" and item.get("name") == "search_uploaded_files" for item in saved.output)
        restored = build_model_messages([saved])
        restored_results = [json.loads(item["content"]) for item in restored if item["role"] == "tool"]
        assert any(data.get("chunks") and data["chunks"][0]["content"] == "住宿上限437元" for data in restored_results)
    await engine.dispose()
asyncio.run(main())
print("真实存储检索闭环通过")
'''
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-X", "utf8", "-c", code, directory], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, encoding="utf-8", timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("真实存储检索闭环通过", result.stdout)
