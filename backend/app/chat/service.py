import logging
from collections.abc import AsyncIterator,AsyncGenerator
from contextlib import aclosing
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.repositories.chat import create_message, update_message,list_recent_messages
from backend.app.chat.context import build_model_messages
from backend.app.chat.events import format_sse
from backend.app.llm.client import stream_reply
import asyncio  # 识别异步任务取消
import anyio  # 为取消后的清理提供短暂保护
from backend.app.core.database import SessionLocal  # 创建独立数据库会话
from backend.app.repositories.chat import cancel_message  # 保存取消状态

logger = logging.getLogger(__name__)


async def stream_chat_events(message: str) -> AsyncIterator[str]:
    """读取模型正文，转换成前端可以识别的事件。"""
    try:
        messages = [{"role": "user", "content": message}]
        # 离开这个代码块时，确保关闭模型流的生成器。
        async with aclosing(stream_reply(messages)) as stream:
            async for text in stream:
                yield format_sse("text_delta", {"text": text})

    except Exception:
        # 详细异常记录在后端，不直接暴露给客户端。
        logger.exception("流式聊天失败")

        yield format_sse(
            "error",
            {"message": "模型请求失败，请查看后端日志"},
        )

    else:
        # 只有模型流正常结束，才发送完成事件。
        yield format_sse("done", {})

async def stream_chat_history_events(
    session: AsyncSession,
    chat_id: str,
) -> AsyncGenerator[str, None]:
    """读取已保存的会话历史，调用模型并产出事件。"""
    assistant_message_id=None
    text_parts=[]
    try:
        # ① 查询数据库中的最近消息,组装模型输入
        history = await list_recent_messages(
            session=session,
            chat_id=chat_id,
        )
        if not history:
            raise ValueError("没有可以发送给模型的消息")

        # ② 将数据库对象转换成模型输入。
        messages = build_model_messages(history)

        # ② 创建一条正在生成的助手消息。
        assistant_message = await create_message(
            session=session,
            chat_id=chat_id,
            role="assistant",
            content="",
            status="generating",
            model=settings.llm_model,
        )
        assistant_message_id=assistant_message.id

        # ③ 边收集模型正文，边向客户端发送。
        async with aclosing(stream_reply(messages)) as stream:
            async for text in stream:
                text_parts.append(text)
                # ④ 将模型正文包装成事件。
                yield format_sse(
                    "text_delta",
                    {"text": text},
                )
        # ④ 模型正常结束，保存完整回答。
        full_text = "".join(text_parts)

        await update_message(
            session=session,
            message_id=assistant_message_id,
            content=full_text,
            status="completed",
            output=[
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": full_text,
                        }
                    ],
                }
            ],
        )
    except (asyncio.CancelledError, GeneratorExit):  # 任务被取消，或生成器被关闭
        if assistant_message_id is not None:
            with anyio.move_on_after(5, shield=True) as cleanup_scope:  # 最多用 5 秒保存取消状态
                try:
                    async with SessionLocal() as cancel_session:  # 不复用可能正被取消的数据库会话
                        await cancel_message(cancel_session, assistant_message_id, "".join(text_parts))
                except Exception:
                    logger.exception("保存取消状态失败")
            if cleanup_scope.cancel_called:
                logger.warning("保存取消状态超时，消息 ID：%s", assistant_message_id)
        raise  # 继续传播取消，不能当成正常完成
    except Exception:
        logger.exception("历史会话生成回答失败")

        # 已创建助手消息时，尝试保存部分正文及失败状态。
        if assistant_message_id is not None:
            try:
                await update_message(
                    session=session,
                    message_id=assistant_message_id,
                    content="".join(text_parts),
                    status="failed",
                    error="生成或保存回答失败，请查看后端日志",
                )
            except Exception:
                logger.exception("保存失败状态时发生异常")

        yield format_sse(
            "error",
            {"message": "生成或保存回答失败，请查看后端日志"},
        )

    else:
        # 只有回答已成功保存，才发送正常结束事件。
        yield format_sse(
            "done",
            {"message_id": assistant_message_id},
        )

