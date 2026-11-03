import httpx
from backend.app.core.config import settings
import json
from collections.abc import AsyncIterator

async def generate_reply(message: str) -> str:
    """把用户的问题发送给模型，返回回答正文。"""
    api_key = settings.llm_api_key.get_secret_value()  # 仅在后端请求模型时读取密钥，不打印或返回给前端
    # 先检查必要配置，避免发送无效请求。
    if not all((
        settings.llm_base_url,
        api_key,
        settings.llm_model,
    )):
        raise ValueError("请先配置模型地址、密钥和模型名称")

    # 地址应是模型服务的 API 基础地址。
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "user", "content": message}
        ],
        "stream": False,
    }

    async with httpx.AsyncClient(
        timeout=settings.llm_timeout
    ) as client:
        response = await client.post(
            url,
            headers=headers,
            json=payload,
        )

        # 如果服务返回 401、429、500 等错误，就抛出异常。
        response.raise_for_status()

        data = response.json()
        content = data["choices"][0]["message"]["content"]

        if not isinstance(content, str):
            raise ValueError("模型没有返回文本回答")

        return content

async def stream_reply(messages: list[dict[str,str]]) -> AsyncIterator[str]:
    """接收消息列表，流式读取模型回答，每次产出一段正文。"""

    api_key = settings.llm_api_key.get_secret_value()  # 流式请求同样只在后端读取真实密钥
    if not all((
        settings.llm_base_url,
        api_key,
        settings.llm_model,
    )):
        raise ValueError("请先配置模型地址、密钥和模型名称")

    url = settings.llm_base_url.rstrip("/") + "/chat/completions"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": settings.llm_model,
        "messages": messages,
        "stream": True,
    }

    async with httpx.AsyncClient(
        timeout=settings.llm_timeout
    ) as client:
        async with client.stream(
            "POST",
            url,
            headers=headers,
            json=payload,
        ) as response:
            response.raise_for_status()

            async for line in response.aiter_lines():
                # 当前按常见的聊天补全 SSE 格式解析。
                if not line.startswith("data:"):
                    continue

                data = line[5:].strip()#去data:之后

                if not data:
                    continue

                if data == "[DONE]":
                    break

                chunk = json.loads(data)#字符串json转json

                if "error" in chunk:
                    raise RuntimeError("模型服务在流式响应中返回了错误")

                choices = chunk.get("choices", [])
                if not choices:
                    continue

                # delta 表示本次增量，不是完整回答。
                content = choices[0].get("delta", {}).get("content")

                if isinstance(content, str) and content:
                    yield content
