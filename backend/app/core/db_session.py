"""数据库会话显式归属；网络等待前结束已完成步骤的事务。"""
from contextlib import asynccontextmanager
import anyio
from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def managed_session(factory):
    session = factory()
    try:
        yield session
    finally:
        # 浏览器断开、子任务取消时也必须等待连接归还完成。
        with anyio.CancelScope(shield=True):
            await session.close()


async def release_connection(session):
    """只在业务步骤完成处调用；提交后保留 ORM 快照，不跨网络等待占用连接。

    运行会话使用 expire_on_commit=False，不能在一个原子写入步骤的中间调用。
    """
    if isinstance(session, AsyncSession) and session.in_transaction():
        await session.commit()
