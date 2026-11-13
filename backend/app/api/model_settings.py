from backend.app.llm.http import model_client, ModelAccessError
import json
import httpx
import anyio
from typing import Literal
from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update
from backend.app.auth import get_current_user
from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.core.model_secrets import decrypt_api_key, encrypt_api_key
from backend.app.llm.connection import ModelConnection
from backend.app.models.model_setting import UserModelSetting
from backend.app.models.user import User

router = APIRouter(prefix="/api/settings/model-service", tags=["模型服务设置"])
PRESETS = [{"id": "deepseek", "name": "DeepSeek", "base_url": "https://api.deepseek.com/v1", "model": ""}, {"id": "openai", "name": "OpenAI", "base_url": "https://api.openai.com/v1", "model": ""}, {"id": "qwen", "name": "通义千问", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": ""}, {"id": "custom", "name": "自定义 · OpenAI兼容", "base_url": "", "model": ""}]
PRESETS.insert(3, {"id": "ollama", "name": "Ollama · 本地模型", "base_url": "http://127.0.0.1:11434/v1", "model": ""})

def normalize_url(value: str) -> str:  # 只接收API基础地址，禁止把密码或Key混入URL
    value = value.strip().rstrip("/")
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname or parts.username is not None or parts.password is not None or parts.query or parts.fragment or any(character.isspace() for character in value):
            raise ValueError()
        parts.port
    except ValueError:
        raise ValueError("请输入有效的HTTP或HTTPS基础地址，不要包含账号、密码、查询参数或片段") from None
    if parts.path.endswith(("/chat/completions", "/models")):
        raise ValueError("请填写API基础地址，不要附加/chat/completions或/models")
    return value

class ModelServiceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    provider: Literal["server", "deepseek", "openai", "qwen", "ollama", "custom"]
    base_url: str = Field(min_length=1, max_length=1000)
    model: str = Field(default="", max_length=200)
    timeout: float = Field(default=60, ge=5, le=600)
    key_source: Literal["personal", "environment", "none"] = "personal"
    api_key: SecretStr | None = Field(default=None, max_length=4096)  # null只在服务地址未变时保留当前账号的旧Key

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
        return normalize_url(value)

    @field_validator("model")
    @classmethod
    def trim_model(cls, value):
        return value.strip()

    @field_validator("api_key")
    @classmethod
    def check_api_key(cls, value):
        if value is not None:
            raw = value.get_secret_value().strip()
            if any(character.isspace() or not character.isascii() or ord(character) < 32 or ord(character) == 127 for character in raw):
                raise ValueError("API Key只能包含可打印的ASCII字符，不能包含内部空白")
        return value

class ModelSelection(BaseModel):  # 快捷选择只更新模型名，不回传或覆盖服务地址与密钥
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=1000)

    @field_validator("model")
    @classmethod
    def check_model(cls, value):
        if not value.strip():
            raise ValueError("请选择模型")
        return value.strip()

def public_config(row: UserModelSetting | None) -> dict:  # 永不返回密钥明文、密文或主密钥
    if row is None:
        return {"provider": "server", "base_url": settings.llm_base_url.rstrip("/"), "model": settings.llm_model, "timeout": settings.llm_timeout, "key_source": "environment", "key_configured": bool(settings.llm_api_key.get_secret_value())}
    configured = bool(row.encrypted_api_key) if row.key_source == "personal" else bool(settings.llm_api_key.get_secret_value()) if row.key_source == "environment" else False
    return {"provider": row.provider, "base_url": row.base_url, "model": row.model, "timeout": row.timeout, "key_source": row.key_source, "key_configured": configured}

def resolve_connection(data: ModelServiceInput, old: UserModelSetting | None, user_id: str | None = None) -> ModelConnection:
    try:
        if data.key_source == "environment":
            if data.provider != "server" or not settings.llm_base_url or data.base_url != normalize_url(settings.llm_base_url):
                raise ValueError("服务器默认Key只能用于服务器配置的地址；其他服务请填写自己的Key")
            key = settings.llm_api_key.get_secret_value()
            if not key:
                raise ValueError("服务器尚未配置默认Key，请填写个人Key或选择无需Key的服务")
        elif data.key_source == "personal":
            key = data.api_key.get_secret_value().strip() if data.api_key is not None else ""
            if not key:
                if old is None or old.key_source != "personal" or old.base_url != data.base_url or not old.encrypted_api_key:
                    raise ValueError("请填写当前服务的个人API Key；更换地址后需要重新填写")
                key = decrypt_api_key(old.encrypted_api_key)
        else:
            key = ""
        return ModelConnection(data.base_url, data.model, data.timeout, SecretStr(key), data.key_source == "none", user_id)
    except ValueError as error:
        raise HTTPException(400, str(error)) from None

async def get_user_model_connection(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)) -> ModelConnection:  # 每次回答开始时读取本人配置，后续工具循环固定使用该快照
    row = await session.get(UserModelSetting, user.id)
    if row is None:
        if not settings.llm_base_url or not settings.llm_model or not settings.llm_api_key.get_secret_value():
            raise HTTPException(400, "请先在设置的模型服务中配置地址、模型和Key")
        return ModelConnection(settings.llm_base_url.rstrip("/"), settings.llm_model, settings.llm_timeout, settings.llm_api_key, user_id=user.id)
    data = ModelServiceInput(provider=row.provider, base_url=row.base_url, model=row.model, timeout=row.timeout, key_source=row.key_source)
    return resolve_connection(data, row, user.id)

@router.get("")
async def get_model_settings(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    row = await session.get(UserModelSetting, user.id)
    defaults = public_config(None)
    server = {"id": "server", "name": "服务器默认", "base_url": defaults["base_url"], "model": defaults["model"]}
    return {"config": public_config(row), "inherited": row is None, "presets": [server, *PRESETS]}

@router.put("")
async def save_model_settings(data: ModelServiceInput, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    if not data.model:
        raise HTTPException(400, "请填写模型名称")
    await session.execute(update(User).where(User.id == user.id).values(name=User.name))  # 保存、恢复和快捷选择共用写锁
    row = await session.get(UserModelSetting, user.id)
    connection = resolve_connection(data, row, user.id)
    encrypted = None
    if data.key_source == "personal":
        try:
            encrypted = await anyio.to_thread.run_sync(encrypt_api_key, connection.api_key.get_secret_value())
        except (ValueError, OSError):
            raise HTTPException(500, "无法安全保存Key，请检查服务器主密钥配置或私密目录权限") from None
    if row is None:
        row = UserModelSetting(user_id=user.id)
        session.add(row)
    row.provider, row.base_url, row.model, row.timeout, row.key_source, row.encrypted_api_key = data.provider, data.base_url, data.model, data.timeout, data.key_source, encrypted
    await session.commit()
    return {"config": public_config(row), "inherited": False}

@router.delete("", status_code=204)
async def reset_model_settings(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    await session.execute(update(User).where(User.id == user.id).values(name=User.name))
    row = await session.get(UserModelSetting, user.id)
    if row is not None:
        await session.delete(row)
        await session.commit()
    return Response(status_code=204)

@router.patch("/model")
async def select_model(data: ModelSelection, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    await session.execute(update(User).where(User.id == user.id).values(name=User.name))  # 与设置保存串行，避免多个标签页首次同时创建配置
    row = await session.get(UserModelSetting, user.id)
    current = public_config(row)
    if current["base_url"] != data.base_url.rstrip("/"):
        raise HTTPException(409, "模型服务地址已变化，请重新打开模型列表")
    if row is None:
        if not settings.llm_base_url or not settings.llm_api_key.get_secret_value():
            raise HTTPException(400, "请先在设置中配置模型服务")
        row = UserModelSetting(user_id=user.id, provider="server", base_url=current["base_url"], model=data.model, timeout=current["timeout"], key_source="environment", encrypted_api_key=None)
        session.add(row)
    else:
        result = await session.execute(update(UserModelSetting).where(UserModelSetting.user_id == user.id, UserModelSetting.base_url == data.base_url.rstrip("/")).values(model=data.model))
        if result.rowcount == 0:
            raise HTTPException(409, "模型服务已变化，请重新打开模型列表")
    await session.commit()
    await session.refresh(row)
    return {"config": public_config(row), "inherited": False}

async def request_provider(connection: ModelConnection, models: bool) -> dict:  # 测试不保存草稿，不跟随重定向，不向页面转发服务错误正文
    headers = {"Accept": "application/json"}
    key = connection.api_key.get_secret_value()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = None if models else {"model": connection.model, "messages": [{"role": "user", "content": "Reply only OK, without explanation. /no_think"}], "max_tokens": 512, "stream": False}  # 给思考型模型预留空间，支持/no_think的模型可直接短答
    try:
        async with model_client(connection, timeout=min(connection.timeout, 20 if models else 120), follow_redirects=False) as client:
            async with client.stream("GET" if models else "POST", connection.base_url + ("/models" if models else "/chat/completions"), headers=headers, json=payload) as response:
                if response.status_code >= 300:
                    messages = {401: "API Key未通过认证，请重新填写有效Key；需要认证的服务不能选择无需Key", 403: "模型服务拒绝访问，请检查账号权限或模型授权", 404: "此服务未提供模型列表接口，可手动填写模型ID" if models else "没有找到聊天接口或所选模型，请检查基础地址与模型ID", 429: "模型服务请求过于频繁或额度不足，请检查配额后稍后重试"}
                    detail = messages.get(response.status_code, "模型服务暂时不可用，请稍后重试" if response.status_code >= 500 else "模型服务拒绝了请求，请检查地址和生成参数")
                    raise HTTPException(400, f"{detail}（HTTP {response.status_code}）")
                body = bytearray()
                async for part in response.aiter_bytes():
                    body.extend(part)
                    if len(body) > 1024 * 1024:
                        raise HTTPException(400, "模型服务响应过大，无法完成检查")
        result = json.loads(body)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except ModelAccessError as error:
        raise HTTPException(403, str(error)) from None
    except httpx.PoolTimeout:
        raise HTTPException(429, '模型请求排队超时，请稍后重试') from None
    except httpx.TimeoutException:
        raise HTTPException(504, "连接检查超时，请检查服务是否可访问") from None
    except httpx.RequestError:
        raise HTTPException(400, "无法连接模型服务，请检查地址、网络与证书") from None
    except (ValueError, UnicodeError):
        raise HTTPException(400, "模型服务没有返回有效的JSON响应") from None

async def get_ollama_capabilities(connection: ModelConnection, models: list[str]) -> dict[str, list[str]]:  # 只查元数据，不加载模型或发起推理
    if not connection.base_url.endswith("/v1"):
        return {}
    url = connection.base_url[:-3] + "/api/show"  # 保留反向代理的路径前缀
    headers = {"Accept": "application/json"}
    key = connection.api_key.get_secret_value()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    capabilities = {}
    semaphore = anyio.Semaphore(4)
    async with model_client(connection, timeout=min(connection.timeout, 3), follow_redirects=False) as client:
        async def inspect(model):
            async with semaphore:
                try:
                    async with client.stream("POST", url, headers=headers, json={"model": model}) as response:
                        if response.status_code != 200:
                            return
                        body = bytearray()
                        async for part in response.aiter_bytes():
                            body.extend(part)
                            if len(body) > 1024 * 1024:
                                return
                    result = json.loads(body)
                    values = result.get("capabilities") if isinstance(result, dict) else None
                    if isinstance(values, list) and values and all(isinstance(value, str) for value in values):
                        capabilities[model] = values[:16]
                except (httpx.RequestError, ValueError, UnicodeError):
                    pass  # 查询失败时保留未知类型模型，不能按名称猜测并误删
        with anyio.move_on_after(3):  # 模型很多或旧版Ollama不支持时，能力检查不能拖住模型列表
            async with anyio.create_task_group() as group:
                for model in models[:100]:
                    group.start_soon(inspect, model)
    return capabilities

@router.post("/models")
async def discover_models(data: ModelServiceInput, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    connection = resolve_connection(data, await session.get(UserModelSetting, user.id), user.id)
    result = await request_provider(connection, True)
    if not isinstance(result.get("data"), list):
        raise HTTPException(400, "此服务未返回兼容的模型列表，请手动填写模型名称")
    models = sorted({item["id"] for item in result["data"] if isinstance(item, dict) and isinstance(item.get("id"), str) and 0 < len(item["id"]) <= 200})[:500]
    capabilities = {item["id"]: item["capabilities"] for item in result["data"] if isinstance(item, dict) and item.get("id") in models and isinstance(item.get("capabilities"), list) and item["capabilities"] and all(isinstance(value, str) for value in item["capabilities"])}
    ollama = data.provider == "ollama" or any(isinstance(item, dict) and item.get("owned_by") == "library" for item in result["data"])
    if ollama:
        capabilities.update(await get_ollama_capabilities(connection, models))
    excluded = [{"id": model, "reason": "向量模型，不能用于聊天"} for model in models if "embedding" in capabilities.get(model, []) and "completion" not in capabilities[model]]
    excluded_ids = {item["id"] for item in excluded}
    notice = f"已隐藏{len(excluded)}个向量模型。" if excluded else ""
    if ollama and any(model not in capabilities for model in models):
        notice += "部分模型的用途暂未确认，已保留在列表中。"
    return {"models": [model for model in models if model not in excluded_ids], "capabilities": capabilities, "excluded_models": excluded, "notice": notice}

@router.post("/test")
async def test_model_service(data: ModelServiceInput, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    if not data.model:
        raise HTTPException(400, "请先填写模型名称")
    result = await request_provider(resolve_connection(data, await session.get(UserModelSetting, user.id), user.id), False)
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict) or not isinstance(choices[0].get("message"), dict):
        raise HTTPException(400, "模型服务未返回有效聊天消息，请检查接口和模型类型")
    choice = choices[0]
    message = choice["message"]
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return {"status": "ok", "message": "连接成功，所选模型可以返回文本回答；工具调用能力仍需实际对话验证"}
    reasoning = message.get("reasoning") or message.get("reasoning_content")
    if choice.get("finish_reason") == "length":
        if isinstance(reasoning, str) and reasoning.strip():
            return {"status": "limited", "message": "已连接，模型返回了思考内容，但达到测试输出上限，尚未验证最终正文；可保存后在实际对话中验证"}
        raise HTTPException(400, "模型请求已达到测试输出上限，尚未获得正文，不能据此判断模型不支持聊天")
    raise HTTPException(400, "模型已响应但没有返回正文，请检查模型生成设置")
