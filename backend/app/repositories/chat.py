from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, func, select, update
from backend.app.models.chat import Chat, Message
import time
from typing import Literal



async def create_chat(session: AsyncSession,title: str = "新会话",) -> Chat:
    """创建一个会话，保存到数据库并返回。"""
    chat = Chat(title=title)#创建 Python 对象，此时还没写入数据库。
    session.add(chat)#把对象加入当前数据库会话，准备插入。
    await session.commit()#提交事务，真正保存记录。我们定义的 ID、时间默认值也会在插入时生成。
    await session.refresh(chat)#从数据库重新读取这条记录，更新对象里的字段。
    return chat

async def list_chats(session: AsyncSession) -> list[Chat]:
    """查询全部会话的基本信息，最近更新的排在前面。"""

    statement = (
        select(Chat)#找全部会话行
        .order_by(
            Chat.updated_at.desc(),
            Chat.id.desc(),
        )#更新时间降序、id降序
    )

    result = await session.execute(statement)#执行查询

    return list(result.scalars().all())#取查询里的第一个也就是对象并且变成列表

async def get_chat(session: AsyncSession,chat_id: str,) -> Chat | None:
    """根据会话 ID 查询，找不到时返回 None。"""
    return await session.get(Chat, chat_id)

async def get_latest_message(session: AsyncSession, chat_id: str) -> Message | None:  # 查询会话的最新消息，不过滤状态
    statement = select(Message).where(Message.chat_id == chat_id).order_by(Message.sequence.desc()).limit(1)  # 按序号倒序取一条
    result = await session.execute(statement)  # 执行数据库查询
    return result.scalar_one_or_none()

async def create_message(
    session: AsyncSession,
    chat_id: str,
    role: Literal["user", "assistant"],
    content: str,
    status: Literal["generating", "completed", "cancelled", "failed"] = "completed",
    model: str | None = None,) -> Message:
    """保存一条消息，为其分配会话内序号。"""

    try:
        # 先更新会话。对于当前 SQLite，也先取得写锁，
        # 避免两个写入事务同时计算出相同的下一个序号。
        result = await session.execute(
            update(Chat)
            .where(Chat.id == chat_id)
            .values(updated_at=int(time.time()))
        )

        if result.rowcount == 0:
            raise ValueError("会话不存在")

        # 查询这个会话当前最大的消息序号。
        result = await session.execute(
            select(func.max(Message.sequence))
            .where(Message.chat_id == chat_id)
        )

        max_sequence = result.scalar_one()
        next_sequence = (max_sequence or 0) + 1

        message = Message(
            chat_id=chat_id,
            sequence=next_sequence,
            role=role,
            content=content,
            status=status,
            model=model,
        )

        session.add(message)

        # 消息和会话更新时间在同一事务中提交。
        await session.commit()

    except Exception:
        # 失败后撤销本次事务，避免留下半完成的修改。
        await session.rollback()
        raise

    await session.refresh(message)

    return message

async def list_messages(
    session: AsyncSession,
    chat_id: str,
    offset: int = 0,
    limit: int = 100,) -> list[Message]:
    """分页查询指定会话的消息。"""

    statement = (
        select(Message)
        .where(Message.chat_id == chat_id)
        .order_by(Message.sequence.asc())
        .offset(offset)
        .limit(limit)
    )

    result = await session.execute(statement)

    return list(result.scalars().all())

async def list_messages_page(session: AsyncSession, chat_id: str, before_sequence: int | None = None, limit: int = 8) -> tuple[list[Message], bool, int | None]:  # 页面每批默认查询 8 条消息
    statement = select(Message).where(Message.chat_id == chat_id)  # 限定当前会话
    if before_sequence is not None:
        statement = statement.where(Message.sequence < before_sequence)
    statement = statement.order_by(Message.sequence.desc()).limit(limit + 1)  # 多取一条，判断是否还有旧消息
    result = await session.execute(statement)  # 执行查询
    rows = list(result.scalars().all())  # 数据库返回的消息
    has_more = len(rows) > limit  # 多查到一条表示还有更早消息
    items = rows[:limit]  # 只保留本次需要的数量
    items.reverse()  # 恢复页面从旧到新的显示顺序
    next_before_sequence = items[0].sequence if has_more and items else None  # 下次从本批最早消息之前继续查询
    return items, has_more, next_before_sequence

async def list_recent_messages(
    session: AsyncSession,
    chat_id: str,
    limit: int = 50,
) -> list[Message]:
    """获取最近的已完成消息，返回时按序号从小到大排列。"""

    statement = (
        select(Message)
        .where(
            Message.chat_id == chat_id,
            Message.status == "completed",
            Message.role.in_(["user", "assistant"]),
        )
        .order_by(Message.sequence.desc())
        .limit(limit)
    )

    result = await session.execute(statement)
    messages = list(result.scalars().all())

    # 数据库按倒序取最近消息，交给模型前恢复正序。
    messages.reverse()

    return messages

async def update_message(
    session: AsyncSession,
    message_id: str,
    content: str,
    status: Literal["completed", "cancelled", "failed"],
    error: str | None = None,
    output: list | None = None,
) -> Message:
    """更新消息正文、最终状态和结构化输出。"""
    try:
        message = await session.get(Message, message_id)

        if message is None:
            raise ValueError("消息不存在")
        message.content = content
        message.status = status
        message.error = error
        # 没传 output 时，保留原值；传入 [] 则清空。
        if output is not None:
            message.output = output
        # 消息更新后，同时更新所属会话的时间。
        await session.execute(
            update(Chat)
            .where(Chat.id == message.chat_id)
            .values(updated_at=int(time.time()))
        )
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    await session.refresh(message)

    return message

async def delete_chat(session: AsyncSession, chat_id: str) -> bool:  # 删除会话及其全部消息
    try:
        chat = await get_chat(session, chat_id)  # 查询目标会话
        if chat is None:
            return False
        # 先删除子记录，避免留下不属于任何会话的消息。
        await session.execute(
            delete(Message).where(Message.chat_id == chat_id)
        )
        await session.delete(chat)  # 删除会话记录
        await session.commit()  # 在同一个事务中提交两项删除
        return True

    except Exception:
        await session.rollback()  # 删除失败时回滚本次事务
        raise

async def rename_chat(session: AsyncSession, chat_id: str, title: str) -> Chat | None:  # 修改会话标题
    title = title.strip()  # 去掉标题两端的空白
    if not title or len(title) > 200:
        raise ValueError("会话标题必须为 1～200 个字符")
    try:
        chat = await get_chat(session, chat_id)  # 查询目标会话
        if chat is None:
            return None
        chat.title = title
        chat.updated_at = int(time.time())
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    await session.refresh(chat)
    return chat

async def cancel_message(session: AsyncSession, message_id: str, content: str) -> bool:  # 保存部分正文，并取消仍在生成的助手消息
    now = int(time.time())  # 本次更新时间
    output = [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": content}]}]  # 已生成的部分正文
    try:
        result = await session.execute(
            update(Message)
            .where(Message.id == message_id, Message.role == "assistant", Message.status == "generating")
            .values(content=content, output=output, status="cancelled", error=None, updated_at=now)
        )  # 只有状态仍为 generating 才允许修改
        if result.rowcount == 0:
            await session.rollback()
            return False
        chat_id_query = select(Message.chat_id).where(Message.id == message_id).scalar_subquery()  # 查询所属会话 ID
        await session.execute(
            update(Chat).where(Chat.id == chat_id_query).values(updated_at=now)
        )
        await session.commit()
        return True
    except Exception:
        await session.rollback()
        raise

async def recover_interrupted_messages(session: AsyncSession) -> int:  # 将上次遗留的生成中消息标记为失败
    try:
        result = await session.execute(
            update(Message)
            .where(Message.role == "assistant", Message.status == "generating")
            .values(status="failed", error="上次生成因后端退出而中断，请重试", updated_at=int(time.time()))
        )  # 保留已有正文和结构化输出，只更新状态与错误说明
        await session.commit()
        return result.rowcount
    except Exception:
        await session.rollback()
        raise
