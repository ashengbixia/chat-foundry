import logging
import json  # 将工具自身的参数或文件状态错误反馈给模型
from collections.abc import AsyncIterator,AsyncGenerator
from contextlib import aclosing
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.repositories.chat import create_message, update_message,list_recent_messages
from backend.app.chat.context import build_model_messages
from backend.app.chat.events import format_sse
from backend.app.llm.client import stream_reply, stream_model_events
from backend.app.tools.registry import execute_tool, get_available_tools  # 根据本次附件范围提供工具
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
    assistant_message_id = None  # 当前助手消息的数据库 ID
    text_parts = []  # 所有正文片段
    output = []  # 本次回答的正文、工具调用和工具结果
    try:
        # ① 查询数据库中的最近消息,组装模型输入
        history = await list_recent_messages(
            session=session,
            chat_id=chat_id,
        )
        if not history:
            raise ValueError("没有可以发送给模型的消息")

        allowed_file_ids = set()  # 只允许读取本次已完成用户历史中明确关联的附件
        for message in history:
            if message.role == "user" and message.status == "completed":
                for attachment in getattr(message, "attachments", []):
                    allowed_file_ids.add(attachment.file_id)
        available_tools = get_available_tools(allowed_file_ids)

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

        # ③ 读取正文或工具调用；执行工具后带着结果再次请求模型。
        for request_index in range(5):  # 最多请求模型 5 次，最后一次不再提供工具
            tool_calls = {}  # 按 index 分开拼接本次请求中的工具调用
            round_text_parts = []  # 本次模型请求产生的正文
            round_output = None  # 收到正文时创建记录，停止或失败时也能保存当前片段
            tools = available_tools if request_index < 4 else None  # 最后一次只要求生成回答
            async with aclosing(stream_model_events(messages, tools)) as stream:
                async for event in stream:
                    if event["type"] == "text_delta":
                        text = event["text"]  # 当前正文片段
                        text_parts.append(text)
                        round_text_parts.append(text)
                        if round_output is None:
                            round_output = {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": ""}]}
                            output.append(round_output)
                        round_output["content"][0]["text"] += text  # 同步积累本轮正文，避免中途结束时遗漏
                        yield format_sse("text_delta", {"text": text})
                    elif event["type"] == "tool_call_delta":
                        for fragment in event["tool_calls"]:
                            index = fragment["index"]  # 区分同一次请求里的不同工具调用
                            if index not in tool_calls:
                                tool_calls[index] = {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                            call = tool_calls[index]  # 当前正在拼接的工具调用
                            if fragment.get("id"):
                                call["id"] += fragment["id"]
                            function = fragment.get("function") or {}  # 本次函数信息片段
                            call["function"]["name"] += function.get("name") or ""
                            call["function"]["arguments"] += function.get("arguments") or ""
            round_text = "".join(round_text_parts)  # 本次请求的完整正文
            if not tool_calls:
                break  # 模型没有继续调用工具，结束生成流程
            if request_index == 4:
                raise RuntimeError("模型超过工具调用次数限制")
            calls = [tool_calls[index] for index in sorted(tool_calls)]  # 完整工具调用列表
            for call in calls:
                if not call["id"] or not call["function"]["name"]:
                    raise ValueError("模型返回的工具调用缺少 ID 或名称")
            messages.append({"role": "assistant", "content": round_text or None, "tool_calls": calls})
            for call in calls:
                await asyncio.sleep(0)  # 多工具顺序执行时也允许响应取消
                name = call["function"]["name"]  # 要执行的工具名称
                arguments = call["function"]["arguments"]  # 已拼接完整的参数字符串
                output.append({"type": "function_call", "call_id": call["id"], "name": name, "arguments": arguments})
                yield format_sse("tool_call", {"call_id": call["id"], "name": name, "arguments": arguments})
                await asyncio.sleep(0)  # 已发送调用通知后，允许取消发生在实际执行之前
                try:
                    result = await execute_tool(name, arguments, session, allowed_file_ids)  # 文件工具可异步查询数据库
                except ValueError as error:
                    result = json.dumps({"error": str(error)}, ensure_ascii=False)  # 已删除文件等可预期错误，便于模型正确说明原因
                except Exception:
                    logger.exception("工具执行失败：%s", name)
                    result = '{"error":"工具执行失败，请检查工具名称和参数，或告知用户无法完成。"}'
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
                output.append({"type": "function_call_output", "call_id": call["id"], "output": result})
                yield format_sse("tool_result", {"call_id": call["id"], "output": result})
        # ④ 模型正常结束，保存完整回答。
        full_text = "".join(text_parts)

        await update_message(
            session=session,
            message_id=assistant_message_id,
            content=full_text,
            status="completed",
            output=output,
        )
    except (asyncio.CancelledError, GeneratorExit):  # 任务被取消，或生成器被关闭
        if assistant_message_id is not None:
            with anyio.move_on_after(5, shield=True) as cleanup_scope:  # 最多用 5 秒保存取消状态
                try:
                    async with SessionLocal() as cancel_session:  # 不复用可能正被取消的数据库会话
                        await cancel_message(cancel_session, assistant_message_id, "".join(text_parts), output=output)
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
                    output=output,  # 保留失败前已有的正文和工具记录
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

