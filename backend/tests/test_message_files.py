from backend.tests.auth_fixture import allow_legacy_test_user
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from backend.app.api.chats import router
from backend.app.core import database
from backend.app.models.chat import Chat, Message
from backend.app.models.file import File
from backend.app.models.message_file import MessageFile
from backend.app.repositories.chat import create_message
from backend.app.repositories.message_file import MessageFileError

class MessageFileTests(unittest.IsolatedAsyncioTestCase):  # 不连接真实数据库，不请求模型
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        @event.listens_for(self.engine.sync_engine, "connect")
        def enable_foreign_keys(connection, record):  # 开启外键，确认删除顺序确实正确
            connection.execute("PRAGMA foreign_keys=ON")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        await database.create_tables(self.engine)  # 只创建隔离测试库
        async with self.sessions() as session:
            session.add_all([Chat(id="chat", title="附件测试", updated_at=1), Chat(id="other", title="其他会话", updated_at=1)])
            session.add_all([File(id=f"f{index}", filename=f"资料{index}.txt", storage_path=f"f{index}.txt", size=index, text_content="不应自动发给模型的正文", status=status) for index, status in [(1, "completed"), (2, "completed"), (3, "processing"), (4, "failed"), (5, "completed"), (6, "completed")]])
            await session.commit()
        app = FastAPI()
        allow_legacy_test_user(app)
        app.include_router(router)
        async def override_db():
            async with self.sessions() as session:
                yield session
        app.dependency_overrides[database.get_db] = override_db
        self.client = AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://attachment-test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()

    async def send(self, file_ids=None, chat_id="chat"):
        payload = {"content": "总结资料"}
        if file_ids is not None:
            payload["file_ids"] = file_ids
        return await self.client.post(f"/api/chats/{chat_id}/messages", json=payload)

    async def count(self, model):
        async with self.sessions() as session:
            return (await session.execute(select(func.count()).select_from(model))).scalar_one()

    async def test_text_only_request_stays_compatible(self):
        response = await self.send()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["attachments"], [])
        self.assertEqual(await self.count(MessageFile), 0)

    async def test_multiple_files_and_history_keep_selection_order(self):
        response = await self.send(["f2", "f1"])
        self.assertEqual(response.status_code, 201)
        expected = [{"file_id": "f2", "filename": "资料2.txt", "size": 2, "deleted": False}, {"file_id": "f1", "filename": "资料1.txt", "size": 1, "deleted": False}]
        self.assertEqual(response.json()["attachments"], expected)
        page = (await self.client.get("/api/chats/chat/messages")).json()
        self.assertEqual(page["items"][0]["attachments"], expected)
        self.assertEqual(await self.count(MessageFile), 2)

    async def test_duplicate_file_ids_are_deduplicated(self):
        response = await self.send(["f1", "f2", "f1"])
        self.assertEqual(response.status_code, 201)
        self.assertEqual([item["file_id"] for item in response.json()["attachments"]], ["f1", "f2"])
        self.assertEqual(await self.count(MessageFile), 2)

    async def test_invalid_attachment_rolls_back_message_and_chat_update(self):
        for file_ids in [["missing"], ["f3"], ["f4"], ["f1", "missing"]]:
            with self.subTest(file_ids=file_ids):
                response = await self.send(file_ids)
                self.assertEqual(response.status_code, 400)
                self.assertIn("附件", response.json()["detail"])
        self.assertEqual(await self.count(Message), 0)
        self.assertEqual(await self.count(MessageFile), 0)
        async with self.sessions() as session:
            self.assertEqual((await session.get(Chat, "chat")).updated_at, 1)
        response = await self.send(["f1"])
        self.assertEqual(response.json()["sequence"], 1)  # 失败不消耗消息序号

    async def test_api_rejects_too_many_or_wrong_type_ids(self):
        for value in [["f1", "f2", "f5", "f6"], [None], "f1"]:
            self.assertEqual((await self.send(value)).status_code, 422)
        self.assertEqual(await self.count(Message), 0)

    async def test_repository_enforces_limit_and_user_role(self):
        for role, ids in [("user", ["f1", "f2", "f5", "f6"]), ("assistant", ["f1"])]:
            async with self.sessions() as session:
                with self.assertRaises(MessageFileError):
                    await create_message(session, "chat", role, "测试", file_ids=ids)
        self.assertEqual(await self.count(Message), 0)

    async def test_unknown_chat_is_still_404(self):
        self.assertEqual((await self.send(["f1"], chat_id="missing")).status_code, 404)
        self.assertEqual(await self.count(MessageFile), 0)

    async def test_one_file_can_be_used_by_multiple_messages(self):
        self.assertEqual((await self.send(["f1"])).status_code, 201)
        self.assertEqual((await self.send(["f1"])).status_code, 201)
        self.assertEqual(await self.count(MessageFile), 2)

    async def test_attachment_insert_failure_rolls_back_everything(self):
        def fail_attachment_insert(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("INSERT INTO MESSAGE_FILES"):
                raise RuntimeError("模拟附件记录写入失败")
        event.listen(self.engine.sync_engine, "before_cursor_execute", fail_attachment_insert)
        try:
            self.assertEqual((await self.send(["f1"])).status_code, 500)
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", fail_attachment_insert)
        self.assertEqual(await self.count(Message), 0)
        self.assertEqual(await self.count(MessageFile), 0)
        async with self.sessions() as session:
            self.assertEqual((await session.get(Chat, "chat")).updated_at, 1)

    async def test_delete_chat_cleans_links_but_keeps_files_and_other_chat(self):
        await self.send(["f1", "f2"])
        await self.send(["f1"], chat_id="other")
        self.assertEqual((await self.client.delete("/api/chats/chat")).status_code, 204)
        self.assertEqual(await self.count(Message), 1)
        self.assertEqual(await self.count(MessageFile), 1)
        self.assertEqual(await self.count(File), 6)
        page = (await self.client.get("/api/chats/other/messages")).json()
        self.assertEqual(page["items"][0]["attachments"][0]["file_id"], "f1")

    async def test_attachment_snapshot_is_not_overwritten_by_file_changes(self):
        await self.send(["f1"])
        async with self.sessions() as session:
            file = await session.get(File, "f1")
            file.filename = "修改后的名称.txt"
            file.size = 999
            await session.commit()
        page = (await self.client.get("/api/chats/chat/messages")).json()
        self.assertEqual(page["items"][0]["attachments"], [{"file_id": "f1", "filename": "资料1.txt", "size": 1, "deleted": False}])

    async def test_attachment_queries_do_not_read_file_body(self):
        statements = []
        def capture(connection, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
        try:
            self.assertEqual((await self.send(["f1"])).status_code, 201)
            self.assertEqual((await self.client.get("/api/chats/chat/messages")).status_code, 200)
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
        self.assertTrue(all("files.text_content" not in statement for statement in statements))

if __name__ == "__main__":
    unittest.main()
