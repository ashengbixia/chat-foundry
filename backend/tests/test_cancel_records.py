import unittest  # 使用内存数据库验证记录实际保存情况
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from backend.app.core.database import create_tables
from backend.app.models.chat import Chat, Message
from backend.app.repositories.chat import cancel_message

class CancelRecordsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")  # 不连接真实 app.db
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        await create_tables(self.engine)
        async with self.sessions() as session:
            session.add(Chat(id="chat", title="取消记录测试", updated_at=1))
            await session.flush()
            session.add(Message(id="reply", chat_id="chat", sequence=1, role="assistant", status="generating", content=""))
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_explicit_output_survives_database_round_trip(self):
        output = [{"type": "function_call", "call_id": "call-1", "name": "get_current_time", "arguments": "{}"}, {"type": "function_call_output", "call_id": "call-1", "output": '{"timezone":"Asia/Shanghai"}'}, {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "部分回答"}]}]
        async with self.sessions() as session:
            self.assertTrue(await cancel_message(session, "reply", "部分回答", output=output))
        async with self.sessions() as session:  # 用新会话重读，确保是数据库内容而非内存对象
            message = await session.get(Message, "reply")
            self.assertEqual(message.status, "cancelled")
            self.assertEqual(message.content, "部分回答")
            self.assertEqual(message.output, output)
            self.assertIsNone(message.error)
            self.assertGreater((await session.get(Chat, "chat")).updated_at, 1)
            self.assertFalse(await cancel_message(session, "reply", "不能覆盖", output=[]))
        async with self.sessions() as session:
            self.assertEqual((await session.get(Message, "reply")).output, output)

    async def test_legacy_call_generates_text_record(self):  # 未传 output 时兼容旧接口
        async with self.sessions() as session:
            self.assertTrue(await cancel_message(session, "reply", "旧调用正文"))
        async with self.sessions() as session:
            self.assertEqual((await session.get(Message, "reply")).output, [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "旧调用正文"}]}])

    async def test_explicit_empty_output_stays_empty(self):  # 空列表不能被当成未传值
        async with self.sessions() as session:
            self.assertTrue(await cancel_message(session, "reply", "", output=[]))
        async with self.sessions() as session:
            self.assertEqual((await session.get(Message, "reply")).output, [])

if __name__ == "__main__":
    unittest.main()
