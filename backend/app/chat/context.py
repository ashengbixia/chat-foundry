from backend.app.models.chat import Message


def build_model_messages(
    messages: list[Message],
    system_prompt: str = "你是一个有帮助的中文助手。",
) -> list[dict[str, str]]:
    """将按序排列的数据库消息转换成模型输入。"""

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

        # 当前阶段只处理用户正文和助手正文。
        if message.role not in ("user", "assistant"):
            continue

        if not message.content.strip():
            continue

        result.append({
            "role": message.role,
            "content": message.content,
        })

    return result