from backend.tests.auth_fixture import allow_legacy_test_user
import unittest  # 使用 Python 标准库运行异步测试
from fastapi import FastAPI  # 创建不执行真实应用启动流程的测试应用
from httpx import ASGITransport, AsyncClient  # 仅在内存中请求测试接口
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # 使用内存 SQLite
from backend.app.api.chats import router  # 待测试接口
from backend.app.core.database import Base, get_db  # 表结构与待替换的数据库依赖
from backend.app.models.chat import Chat, Message  # 测试数据模型
from backend.app.repositories.chat import list_messages_page, list_recent_messages  # 页面分页和模型历史查询


class MessagePaginationTests(unittest.IsolatedAsyncioTestCase):  # 所有数据仅存在于各自测试的内存数据库
    async def asyncSetUp(self):  # 创建测试数据库和样本
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")  # 不连接真实 app.db
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)  # 测试会话工厂
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.sessions() as session:
            session.add_all([Chat(id="main", title="分页测试"), Chat(id="empty", title="空会话"), Chat(id="gaps", title="不连续序号"), Chat(id="other", title="其他会话")])
            await session.flush()
            for sequence in range(1, 201):
                status = {198: "failed", 199: "cancelled", 200: "generating"}.get(sequence, "completed")  # 页面分页不应过滤消息状态
                session.add(Message(id=f"main-{sequence}", chat_id="main", sequence=sequence, role="assistant" if sequence % 2 == 0 or sequence >= 198 else "user", content=f"测试消息{sequence}", status=status))
            for sequence in [2, 5, 10]:
                session.add(Message(id=f"gap-{sequence}", chat_id="gaps", sequence=sequence, role="user", content="间隔测试"))
            session.add(Message(id="other-1", chat_id="other", sequence=999, role="user", content="不能混入其他会话"))
            await session.commit()
        self.app = FastAPI()
        allow_legacy_test_user(self.app)  # 不使用 main.py，不触发真实数据库初始化或恢复
        self.app.include_router(router)

        async def override_db():  # 将请求数据库替换为测试数据库
            async with self.sessions() as session:
                yield session

        self.app.dependency_overrides[get_db] = override_db
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://pagination-test")  # 不访问外部网络

    async def asyncTearDown(self):  # 释放测试资源
        await self.client.aclose()
        await self.engine.dispose()

    async def test_all_chat_summaries(self):  # 超过 100 个会话也全部返回，不包含消息正文
        async with self.sessions() as session:
            session.add_all([Chat(id=f"extra-{index:03d}", title=f"测试会话{index}", created_at=1, updated_at=1) for index in range(121)])
            session.add_all([Chat(id="tie-a", title="同时间A", created_at=1, updated_at=9999999999), Chat(id="tie-b", title="同时间B", created_at=1, updated_at=9999999999)])
            await session.commit()
        response = await self.client.get("/api/chats")
        self.assertEqual(response.status_code, 200)
        chats = response.json()  # 列表只返回基本信息，不能混入会话消息
        self.assertEqual(len(chats), 127)
        self.assertEqual(len({chat["id"] for chat in chats}), 127)
        self.assertEqual([chat["id"] for chat in chats[:2]], ["tie-b", "tie-a"])
        self.assertEqual([chat["id"] for chat in chats[-121:]], [f"extra-{index:03d}" for index in reversed(range(121))])
        self.assertTrue(all(set(chat) == {"id", "title", "created_at", "updated_at"} for chat in chats))

    async def test_latest_page(self):  # 首次返回最近 8 条，并恢复正序
        async with self.sessions() as session:
            items, has_more, cursor = await list_messages_page(session, "main")
            self.assertEqual([item.sequence for item in items], list(range(193, 201)))
            self.assertTrue(has_more)
            self.assertEqual(cursor, 193)
            self.assertEqual([item.status for item in items[-3:]], ["failed", "cancelled", "generating"])

    async def test_all_pages_without_gaps_or_duplicates(self):  # 完整翻页不漏掉多查询的那条消息
        collected = []  # 前插后得到的完整消息序号
        cursor = None  # 首次查询不提供边界
        async with self.sessions() as session:
            for _ in range(25):  # 200 条消息，每批 8 条，共 25 批
                items, has_more, cursor = await list_messages_page(session, "main", cursor)
                collected = [item.sequence for item in items] + collected
            self.assertEqual(collected, list(range(1, 201)))
            self.assertFalse(has_more)
            self.assertIsNone(cursor)

    async def test_new_message_does_not_move_old_cursor(self):  # 新消息不改变已经取得的旧消息边界
        async with self.sessions() as session:
            _, _, cursor = await list_messages_page(session, "main")
            session.add(Message(id="main-201", chat_id="main", sequence=201, role="user", content="翻页期间新增"))
            await session.commit()
            items, _, next_cursor = await list_messages_page(session, "main", cursor)
            self.assertEqual([item.sequence for item in items], list(range(185, 193)))
            self.assertEqual(next_cursor, 185)

    async def test_non_contiguous_sequences(self):  # 边界使用实际序号，不假定序号连续
        async with self.sessions() as session:
            items, has_more, cursor = await list_messages_page(session, "gaps", limit=2)
            self.assertEqual([item.sequence for item in items], [5, 10])
            self.assertTrue(has_more)
            self.assertEqual(cursor, 5)
            items, has_more, cursor = await list_messages_page(session, "gaps", cursor, limit=2)
            self.assertEqual([item.sequence for item in items], [2])
            self.assertFalse(has_more)
            self.assertIsNone(cursor)

    async def test_empty_page_and_before_first_message(self):  # 空会话与已经没有更早消息的边界
        async with self.sessions() as session:
            self.assertEqual(await list_messages_page(session, "empty"), ([], False, None))
            self.assertEqual(await list_messages_page(session, "main", before_sequence=1), ([], False, None))

    async def test_endpoint_response_and_next_page(self):  # 验证真实路由的分页结构和参数传递
        response = await self.client.get("/api/chats/main/messages")  # 查询最近一批
        self.assertEqual(response.status_code, 200)
        page = response.json()  # 序列化后的分页结果
        self.assertEqual(set(page), {"items", "has_more", "next_before_sequence"})
        self.assertEqual([item["sequence"] for item in page["items"]], list(range(193, 201)))
        self.assertEqual(page["next_before_sequence"], 193)
        response = await self.client.get("/api/chats/main/messages", params={"before_sequence": 193})
        self.assertEqual([item["sequence"] for item in response.json()["items"]], list(range(185, 193)))

    async def test_missing_and_empty_chat(self):  # 会话不存在与会话没有消息需要区分
        response = await self.client.get("/api/chats/missing/messages")
        self.assertEqual(response.status_code, 404)
        response = await self.client.get("/api/chats/empty/messages")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"items": [], "has_more": False, "next_before_sequence": None})

    async def test_invalid_parameters(self):  # 参数校验拒绝无效数量和边界
        for params in [{"limit": 0}, {"limit": 101}, {"before_sequence": 0}, {"before_sequence": -1}, {"before_sequence": "invalid"}]:
            with self.subTest(params=params):
                response = await self.client.get("/api/chats/main/messages", params=params)
                self.assertEqual(response.status_code, 422)

    async def test_model_history_query_unchanged(self):  # 给模型读取的历史仍然只包含已完成消息
        async with self.sessions() as session:
            items = await list_recent_messages(session, "main")
            self.assertEqual(len(items), 50)
            self.assertTrue(all(item.status == "completed" for item in items))
            self.assertEqual(items[-1].sequence, 197)


if __name__ == "__main__":
    unittest.main()
