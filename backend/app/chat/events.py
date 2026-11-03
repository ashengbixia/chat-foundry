import json
from typing import Any

def format_sse(event_type: str, data: dict[str, Any]) -> str:
    """将事件名称和数据编码成一条 SSE 消息。"""

    payload = json.dumps(data, ensure_ascii=False)#python字典转json字符串

    return f"event: {event_type}\ndata: {payload}\n\n"