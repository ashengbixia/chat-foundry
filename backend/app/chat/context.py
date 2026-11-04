from backend.app.models.chat import Message
from typing import Any  # 模型消息还可能包含工具调用列表和空正文


def restore_assistant_messages(message: Message) -> list[dict[str, Any]]:  # 将一条助手记录展开为模型消息，不重新执行工具
    result = []  # 恢复后的正文、工具调用和工具结果
    pending_calls = {}  # 尚未配对结果的工具调用，按 call_id 保存
    has_text = False  # 已恢复正文时不再追加 content，避免重复
    output = getattr(message, "output", None)  # 兼容没有结构化输出的旧消息
    if not isinstance(output, list):
        output = []
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "message":
            content = item.get("content")  # 正文由多个内容块组成
            if not isinstance(content, list):
                continue
            text = "".join(part["text"] for part in content if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str))
            if text.strip():
                result.append({"role": "assistant", "content": text})
                has_text = True
        elif item.get("type") == "function_call":
            call_id = item.get("call_id")  # 调用和结果共同使用的关联标识
            name = item.get("name")  # 历史工具名不要求仍在当前注册表中
            arguments = item.get("arguments")  # 保留当时的原始参数，包括曾导致工具报错的参数
            if isinstance(call_id, str) and call_id and isinstance(name, str) and name and isinstance(arguments, str):
                pending_calls[call_id] = {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}
        elif item.get("type") == "function_call_output":
            call_id = item.get("call_id")
            tool_output = item.get("output")  # 本项目保存的工具结果是字符串
            if not isinstance(call_id, str) or not isinstance(tool_output, str):
                continue
            call = pending_calls.pop(call_id, None)  # 只恢复有完整调用记录的结果
            if call is not None:
                result.append({"role": "assistant", "content": None, "tool_calls": [call]})
                result.append({"role": "tool", "tool_call_id": call_id, "content": tool_output})
    if not has_text and message.content.strip():
        result.append({"role": "assistant", "content": message.content})  # 旧数据或没有可用正文记录时回退
    return result


def build_model_messages(
    messages: list[Message],
    system_prompt: str = "你是一个有帮助的中文助手。",
) -> list[dict[str, Any]]:
    """将按序排列的已完成消息转换成模型输入，恢复配对的工具历史。"""

    result = []

    if system_prompt.strip():
        result.append({
            "role": "system",
            "content": system_prompt,
        })

    for message in messages:
        # 暂时只使用正常完成的消息。
        if message.status != "completed":
            continue

        # 只处理用户和助手记录；工具结果从助手 output 中恢复。
        if message.role not in ("user", "assistant"):
            continue

        if message.role == "assistant":
            result.extend(restore_assistant_messages(message))  # 一条数据库记录可能展开成多条模型消息
        elif message.content.strip():
            result.append({"role": "user", "content": message.content})

    return result
