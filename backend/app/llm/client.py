from backend.app.llm.http import model_client
import httpx
from backend.app.core.config import settings
import json
from collections.abc import AsyncIterator
from typing import Any  # 模型消息和事件可以包含不同类型的数据
from contextlib import aclosing  # 外层停止读取时，及时关闭内部模型流
from backend.app.llm.budget import ensure_input_budget
from backend.app.llm.connection import ModelConnection
from backend.app.llm.messages import complete_tool_reasoning
from backend.app.llm.capabilities import apply_model_capabilities
from backend.app.llm.output import request_limits, apply_output_limit
from backend.app.llm.protocol import ToolMarkupGuard, UnexpectedToolMarkup

class ModelStreamError(RuntimeError):
    """模型流未完整结束，或回答被服务端截断；说明可直接展示给用户。"""


class OutputLimitError(ModelStreamError):
    """供应商明确以length结束，已返回的正文仍保留。"""


class ModelProtocolError(ModelStreamError):
    """工具协议泄漏到正文，不能作为成功回答或可执行指令。"""


def checked_text(guard, text, *, final=False):
    try:
        return guard.feed(text, final=final)
    except UnexpectedToolMarkup as error:
        raise ModelProtocolError(str(error)) from None


def model_usage(value):
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in ('prompt_tokens', 'input_tokens', 'prompt_eval_count',
            'completion_tokens', 'output_tokens', 'eval_count', 'total_tokens')
            if type(value.get(key)) is int and value[key] >= 0}


def output_limit_error(limit):
    setting = f'当前请求设置为{limit} Token' if limit is not None else '当前使用模型服务默认输出额度'
    return OutputLimitError(f'模型输出达到长度限制，回答尚未完成（{setting}）。请在模型设置中调整最大输出；思考与正文可能共用额度，服务自身仍有上限。')

async def generate_reply(message: str, connection: ModelConnection | None = None) -> str:
    """把用户的问题发送给模型，返回回答正文。"""
    connection = connection or ModelConnection(settings.llm_base_url, settings.llm_model, settings.llm_timeout, settings.llm_api_key)
    api_key = connection.api_key.get_secret_value()  # 仅在后端请求模型时读取密钥，不打印或返回给前端
    # 先检查必要配置，避免发送无效请求。
    if not all((
        connection.base_url,
        api_key or connection.allow_no_key,
        connection.model,
    )):
        raise ValueError("请先配置模型地址、密钥和模型名称")

    # 地址应是模型服务的 API 基础地址。
    url = connection.base_url.rstrip("/") + "/chat/completions"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": connection.model,
        "messages": [
            {"role": "user", "content": message}
        ],
        "stream": False,
    }
    window, reserve, limit = request_limits(settings, connection)
    payload = apply_output_limit(apply_model_capabilities(payload, connection), connection, limit)
    ensure_input_budget(payload["messages"], None, window, reserve, settings.llm_context_margin)

    async with model_client(connection,
        timeout=connection.timeout
    ) as client:
        response = await client.post(
            url,
            headers=headers,
            json=payload,
        )

        # 如果服务返回 401、429、500 等错误，就抛出异常。
        response.raise_for_status()

        data = response.json()
        reason = data["choices"][0].get("finish_reason")  # 单次回答也不能把长度截断当作完整答案
        if reason == "length":
            raise output_limit_error(limit)
        if reason == "content_filter":
            raise ModelStreamError("模型服务中止了本次输出，回答未完整生成")
        content = data["choices"][0]["message"]["content"]

        if not isinstance(content, str):
            raise ValueError("模型没有返回文本回答")

        return checked_text(ToolMarkupGuard(), content, final=True)

async def stream_model_events(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, connection: ModelConnection | None = None, *, max_output_tokens: int | None = None, tool_choice: Any = None) -> AsyncIterator[dict[str, Any]]:
    """流式读取模型的正文和工具调用片段，不执行工具。"""

    connection = connection or ModelConnection(settings.llm_base_url, settings.llm_model, settings.llm_timeout, settings.llm_api_key)
    window, output_tokens, output_limit = request_limits(settings, connection, max_output_tokens)
    api_key = connection.api_key.get_secret_value()  # 流式请求同样只在后端读取真实密钥
    if not all((
        connection.base_url,
        api_key or connection.allow_no_key,
        connection.model,
    )):
        raise ValueError("请先配置模型地址、密钥和模型名称")

    url = connection.base_url.rstrip("/") + "/chat/completions"

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    messages = complete_tool_reasoning(messages)
    payload = {
        "model": connection.model,
        "messages": messages,
        "stream": connection.capabilities.streaming,
    }
    if tools:
        payload["tools"] = tools  # 本次请求允许模型使用的工具
        payload["tool_choice"] = tool_choice or "auto"  # 诊断可明确要求测试工具，正常聊天维持auto
    else:
        payload['tool_choice'] = 'none'  # 显式禁止调用，不能依赖供应商的默认行为。
    payload = apply_output_limit(apply_model_capabilities(payload, connection), connection, output_limit)
    ensure_input_budget(payload['messages'], payload.get('tools'), window, output_tokens, settings.llm_context_margin)
    text_guard = ToolMarkupGuard()

    async with model_client(connection,
        timeout=connection.timeout
    ) as client:
        if not connection.capabilities.streaming:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            body = response.json()
            usage = model_usage(body.get('usage'))
            if usage:
                yield {'type': 'usage', 'usage': usage}
            choice = body['choices'][0]
            message = choice.get('message') or {}
            reasoning = message.get('reasoning_content') or message.get('reasoning') or message.get('thinking')
            if isinstance(reasoning, str) and reasoning:
                yield {'type': 'reasoning_delta', 'text': reasoning}
            content = message.get('content')
            if isinstance(content, str) and content:
                content = checked_text(text_guard, content, final=True)
                if content:
                    yield {'type': 'text_delta', 'text': content}
            calls = message.get('tool_calls') or []
            if calls:
                yield {'type': 'tool_call_delta', 'tool_calls': [dict(call, index=index) for index, call in enumerate(calls)]}
            reason = choice.get('finish_reason')
            if reason == 'length':
                raise output_limit_error(output_limit)
            if reason not in ('stop', 'tool_calls') or (reason == 'tool_calls') != bool(calls):
                raise ModelStreamError('模型返回的工具调用与结束原因不一致或不受支持')
            if not calls and not (isinstance(content, str) and content.strip()):
                raise ModelStreamError('模型没有返回正文或工具调用')
            return
        async with client.stream(
            "POST",
            url,
            headers=headers,
            json=payload,
        ) as response:
            response.raise_for_status()
            received_done = False  # 只有显式结束标记才能确认模型流正常结束
            finish_reason = None  # 正常结束、调用工具、长度截断等结束原因
            received_text = False  # 是否收到有效正文
            received_tool = False  # 是否收到工具调用片段
            async for line in response.aiter_lines():
                # 当前按常见的聊天补全 SSE 格式解析。
                if not line.startswith("data:"):
                    continue

                data = line[5:].strip()#去data:之后

                if not data:
                    continue

                if data == "[DONE]":
                    received_done = True
                    break

                chunk = json.loads(data)#字符串json转json

                if "error" in chunk:
                    raise RuntimeError("模型服务在流式响应中返回了错误")

                usage = model_usage(chunk.get('usage'))
                if usage:
                    yield {'type': 'usage', 'usage': usage}

                choices = chunk.get("choices", [])
                if not choices:
                    continue

                # delta 表示本次增量，不是完整回答。
                choice = choices[0]  # 当前只处理第一个回答
                reason = choice.get("finish_reason")  # 普通增量通常为None，结束片段才提供原因
                if reason is not None:
                    finish_reason = reason
                delta = choice.get("delta") or {}  # 模型本次返回的增量内容
                reasoning = delta.get('reasoning_content') or delta.get('reasoning') or delta.get('thinking')
                if isinstance(reasoning, str) and reasoning:
                    yield {'type': 'reasoning_delta', 'text': reasoning}
                content = delta.get("content")  # 本次正文片段
                if isinstance(content, str) and content:
                    received_text = received_text or bool(content.strip())
                    text = checked_text(text_guard, content)
                    if text:
                        yield {"type": "text_delta", "text": text}
                tool_calls = delta.get("tool_calls")  # 参数可能分多次返回，交给上层拼接
                if tool_calls:
                    received_tool = True
                    yield {"type": "tool_call_delta", "tool_calls": tool_calls}

            tail = checked_text(text_guard, '', final=True)
            if tail:
                yield {'type': 'text_delta', 'text': tail}
            if finish_reason == "length":
                raise output_limit_error(output_limit)
            if finish_reason == "content_filter":
                raise ModelStreamError("模型服务中止了本次输出，回答未完整生成")
            if not received_done:
                raise ModelStreamError("模型连接已结束，但没有收到完成标记")
            if finish_reason not in (None, "stop", "tool_calls"):
                raise ModelStreamError("模型返回了当前不支持的结束原因")
            if finish_reason == "tool_calls" and not received_tool:
                raise ModelStreamError("模型声明调用工具，但没有返回工具调用数据")
            if finish_reason == "stop" and received_tool:
                raise ModelStreamError("模型的工具调用数据与结束原因不一致")
            if not received_text and not received_tool:
                raise ModelStreamError("模型没有返回正文或工具调用")

async def stream_reply(messages: list[dict[str, str]], connection: ModelConnection | None = None) -> AsyncIterator[str]:
    """兼容现有聊天流程，不传工具定义，仍然只产出正文。"""
    events = stream_model_events(messages, connection=connection) if connection is not None else stream_model_events(messages)
    async with aclosing(events) as stream:
        async for event in stream:
            if event["type"] == "text_delta":
                yield event["text"]
