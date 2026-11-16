"""统一工具消息格式；实际思考原样保留，后端合成消息使用空思考。"""
from typing import Any


def assistant_tool_message(calls: list[dict[str, Any]], content: str | None = None,
                           reasoning_content: str = '') -> dict[str, Any]:
    return {'role': 'assistant', 'content': content, 'tool_calls': calls,
            'reasoning_content': reasoning_content}


def complete_tool_reasoning(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """出口兜底兼容旧记录及其他调用入口，不修改原消息或覆盖已有思考。"""
    return [dict(message, reasoning_content=message.get('reasoning_content') or '')
            if message.get('role') == 'assistant' and message.get('tool_calls') else message
            for message in messages]
