"""账号工作空间：保存连接与助手，所有凭据仅以既有主密钥加密保存。"""
import asyncio
import uuid
from dataclasses import replace
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field, ConfigDict, field_validator
from sqlalchemy import select, update, delete, func
from backend.app.auth import get_current_user
from backend.app.core.database import get_db
from backend.app.models.user import User
from backend.app.models.model_setting import UserModelSetting, ModelServiceConnection
from backend.app.models.workspace import AssistantProfile, KnowledgeGrant
from backend.app.models.knowledge import KnowledgeBase
from backend.app.models.chat import Chat
from backend.app.repositories.knowledge_access import accessible_knowledge
from backend.app.api.model_settings import ModelServiceInput, public_config, resolve_connection, discover_models
from backend.app.core.model_secrets import encrypt_api_key

router = APIRouter(prefix='/api/workspace', tags=['工作空间'])
FIELDS = ('provider', 'base_url', 'model', 'timeout', 'key_source', 'encrypted_api_key', 'model_profiles')


def connection_data(row):
    return ModelServiceInput(provider=row.provider, base_url=row.base_url, model=row.model, timeout=row.timeout, key_source=row.key_source)


async def owned_connection(session, user_id, connection_id):
    row = await session.scalar(select(ModelServiceConnection).where(ModelServiceConnection.id == connection_id, ModelServiceConnection.user_id == user_id))
    if row is None:
        raise HTTPException(404, '模型连接不存在')
    return row


async def sync_connection(session, current):
    row = await owned_connection(session, current.user_id, current.active_connection_id) if current.active_connection_id else None
    if row is None:
        if (await session.scalar(select(func.count()).select_from(ModelServiceConnection).where(ModelServiceConnection.user_id == current.user_id))) >= 20:
            raise HTTPException(400, '最多保存20个模型连接，请先删除不需要的连接')
        row = ModelServiceConnection(id=str(uuid.uuid4()), user_id=current.user_id, name='模型服务')
        session.add(row)
        current.active_connection_id = row.id
    for field in FIELDS:
        setattr(row, field, getattr(current, field))


async def activate(session, row, model=None):
    current = await session.get(UserModelSetting, row.user_id)
    if current is None:
        current = UserModelSetting(user_id=row.user_id)
        session.add(current)
    if model is not None:
        row.model = model
    for field in FIELDS:
        setattr(current, field, getattr(row, field))
    current.active_connection_id = row.id
    await session.commit()
    return {'config': public_config(current), 'inherited': False}


class ConnectionDraft(ModelServiceInput):
    name: str = Field(min_length=1, max_length=80)


@router.get('/connections')
async def connections(user=Depends(get_current_user), session=Depends(get_db)):
    current = await session.get(UserModelSetting, user.id)
    rows = (await session.execute(select(ModelServiceConnection).where(ModelServiceConnection.user_id == user.id).order_by(ModelServiceConnection.name, ModelServiceConnection.id))).scalars()
    return [{'id': row.id, 'name': row.name, **public_config(row), 'active': bool(current and current.active_connection_id == row.id)} for row in rows]


@router.post('/connections', status_code=201)
async def save_connection(data: ConnectionDraft, user=Depends(get_current_user), session=Depends(get_db)):
    await session.execute(update(User).where(User.id == user.id).values(name=User.name))
    if (await session.scalar(select(func.count()).select_from(ModelServiceConnection).where(ModelServiceConnection.user_id == user.id))) >= 20:
        raise HTTPException(400, '最多保存20个模型连接')
    if not data.model or not data.name.strip(): raise HTTPException(400, '请填写连接名称和模型 ID')
    current = await session.get(UserModelSetting, user.id)
    resolved = resolve_connection(data, current, user.id)
    row = ModelServiceConnection(id=str(uuid.uuid4()), user_id=user.id, name=data.name.strip(), provider=data.provider,
        base_url=data.base_url, model=data.model, timeout=data.timeout, key_source=data.key_source,
        encrypted_api_key=encrypt_api_key(resolved.api_key.get_secret_value()) if data.key_source == 'personal' else None,
        model_profiles={data.model: resolved.capabilities.model_dump()})
    session.add(row)
    await session.flush()
    if current is None:
        await activate(session, row)
    else:
        await session.commit()
    return {'id': row.id, 'name': row.name, **public_config(row)}


class ModelChoice(BaseModel):
    @field_validator('model')
    @classmethod
    def trim_model(cls, value):
        if value is not None and not value.strip(): raise ValueError('模型 ID 不能为空')
        return value.strip() if value is not None else None

    model: str | None = Field(default=None, min_length=1, max_length=200)


@router.post('/connections/{connection_id}/activate')
async def select_connection(connection_id: str, data: ModelChoice, user=Depends(get_current_user), session=Depends(get_db)):
    await session.execute(update(User).where(User.id == user.id).values(name=User.name))
    return await activate(session, await owned_connection(session, user.id, connection_id), data.model)


@router.delete('/connections/{connection_id}', status_code=204)
async def delete_connection(connection_id: str, user=Depends(get_current_user), session=Depends(get_db)):
    await session.execute(update(User).where(User.id == user.id).values(name=User.name))
    row = await owned_connection(session, user.id, connection_id)
    if await session.scalar(select(AssistantProfile.id).where(AssistantProfile.connection_id == row.id).limit(1)):
        raise HTTPException(409, '该连接正在被助手使用，请先调整助手配置')
    await session.execute(delete(UserModelSetting).where(UserModelSetting.user_id == user.id, UserModelSetting.active_connection_id == row.id))
    await session.delete(row)
    await session.commit()
    return Response(status_code=204)


@router.post('/models')
async def aggregate_models(user=Depends(get_current_user), session=Depends(get_db)):
    rows = list((await session.execute(select(ModelServiceConnection).where(ModelServiceConnection.user_id == user.id))).scalars())
    async def discover(row):
        try:
            catalog = await discover_models(connection_data(row), user, _ConnectionSession(row))
            return {'connection_id': row.id, 'connection_name': row.name, **catalog}
        except HTTPException as error:
            return {'connection_id': row.id, 'connection_name': row.name, 'models': [], 'notice': str(error.detail)}
        except Exception:
            return {'connection_id': row.id, 'connection_name': row.name, 'models': [], 'notice': '获取该连接模型失败，请检查连接配置'}
    return {'connections': await asyncio.gather(*(discover(row) for row in rows))}


class _ConnectionSession:
    """发现接口只读取当前连接，不与并行HTTP任务共享AsyncSession。"""
    def __init__(self, row): self.row = row
    async def get(self, *_args): return self.row


class AssistantDraft(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    system_prompt: str = Field(default='', max_length=8000)
    connection_id: str | None = None
    model: str = Field(default='', max_length=200)
    tool_names: list[str] | None = Field(default=None, max_length=100)
    knowledge_ids: list[str] = Field(default_factory=list, max_length=5)


def assistant_view(row):
    return {key: getattr(row, key) for key in ('id', 'name', 'system_prompt', 'connection_id', 'model', 'tool_names', 'knowledge_ids', 'updated_at')}


async def validate_assistant(data, user, session):
    if data.connection_id:
        await owned_connection(session, user.id, data.connection_id)
    from backend.app.tools.runtime import registry
    if data.tool_names is not None and not set(data.tool_names) <= set(registry._tools):
        raise HTTPException(400, '包含不存在的工具')
    valid = set((await session.execute(select(KnowledgeBase.id).where(KnowledgeBase.id.in_(data.knowledge_ids), accessible_knowledge(user.id)))).scalars())
    if valid != set(data.knowledge_ids):
        raise HTTPException(400, '知识库不存在或无权访问')


@router.get('/assistants')
async def assistants(user=Depends(get_current_user), session=Depends(get_db)):
    return [assistant_view(row) for row in (await session.execute(select(AssistantProfile).where(AssistantProfile.user_id == user.id).order_by(AssistantProfile.name))).scalars()]


@router.post('/assistants', status_code=201)
async def create_assistant(data: AssistantDraft, user=Depends(get_current_user), session=Depends(get_db)):
    await session.execute(update(User).where(User.id == user.id).values(name=User.name))
    await validate_assistant(data, user, session)
    if (await session.scalar(select(func.count()).select_from(AssistantProfile).where(AssistantProfile.user_id == user.id))) >= 100:
        raise HTTPException(400, '最多保存100个助手')
    row = AssistantProfile(user_id=user.id, **data.model_dump())
    session.add(row); await session.commit()
    return assistant_view(row)


@router.put('/assistants/{assistant_id}')
async def update_assistant(assistant_id: str, data: AssistantDraft, user=Depends(get_current_user), session=Depends(get_db)):
    row = await session.scalar(select(AssistantProfile).where(AssistantProfile.id == assistant_id, AssistantProfile.user_id == user.id))
    if row is None: raise HTTPException(404, '助手不存在')
    await validate_assistant(data, user, session)
    for key, value in data.model_dump().items(): setattr(row, key, value)
    await session.commit()
    return assistant_view(row)


@router.delete('/assistants/{assistant_id}', status_code=204)
async def delete_assistant(assistant_id: str, user=Depends(get_current_user), session=Depends(get_db)):
    row = await session.scalar(select(AssistantProfile).where(AssistantProfile.id == assistant_id, AssistantProfile.user_id == user.id))
    if row is None: raise HTTPException(404, '助手不存在')
    await session.execute(update(Chat).where(Chat.assistant_id == row.id, Chat.user_id == user.id).values(assistant_id=None))
    await session.delete(row); await session.commit()
    return Response(status_code=204)


async def chat_connection(session, chat, user, fallback):
    if not chat.assistant_id: return fallback
    row = await session.scalar(select(AssistantProfile).where(AssistantProfile.id == chat.assistant_id, AssistantProfile.user_id == user.id))
    if row is None: raise HTTPException(409, '助手配置已不可用，请重新选择')
    connection = fallback
    if row.connection_id:
        saved = await owned_connection(session, user.id, row.connection_id)
        data = connection_data(saved)
        if row.model: data.model = row.model
        connection = resolve_connection(data, saved, user.id)
    elif row.model:
        current = await session.get(UserModelSetting, user.id)
        if current is not None:
            data = connection_data(current); data.model = row.model
            connection = resolve_connection(data, current, user.id)
    return replace(connection, model=row.model or connection.model, system_prompt=row.system_prompt,
        tool_names=frozenset(row.tool_names) if row.tool_names is not None else None, assistant_name=row.name)


class ShareDraft(BaseModel):
    username: str = Field(min_length=1, max_length=254)


@router.get('/knowledge/{knowledge_id}/shares')
async def shares(knowledge_id: str, user=Depends(get_current_user), session=Depends(get_db)):
    from backend.app.api.knowledge import owned_base
    await owned_base(session, knowledge_id, user.id)
    rows = (await session.execute(select(User.id, User.username).join(KnowledgeGrant, KnowledgeGrant.user_id == User.id).where(KnowledgeGrant.knowledge_base_id == knowledge_id))).all()
    return [dict(row._mapping) for row in rows]


@router.post('/knowledge/{knowledge_id}/shares')
async def add_share(knowledge_id: str, data: ShareDraft, user=Depends(get_current_user), session=Depends(get_db)):
    from backend.app.api.knowledge import owned_base
    await owned_base(session, knowledge_id, user.id, lock=True)
    target = await session.scalar(select(User).where(User.username == data.username.strip().lower(), User.role != 'disabled'))
    if target is None: raise HTTPException(404, '账号不存在')
    if target.id == user.id: raise HTTPException(400, '无需共享给自己')
    if not await session.get(KnowledgeGrant, (knowledge_id, target.id)):
        session.add(KnowledgeGrant(knowledge_base_id=knowledge_id, user_id=target.id))
    await session.commit()
    return {'id': target.id, 'username': target.username}


@router.delete('/knowledge/{knowledge_id}/shares/{user_id}', status_code=204)
async def remove_share(knowledge_id: str, user_id: str, user=Depends(get_current_user), session=Depends(get_db)):
    from backend.app.api.knowledge import owned_base
    await owned_base(session, knowledge_id, user.id, lock=True)
    from backend.app.models.knowledge import ChatKnowledgeBase
    await session.execute(delete(ChatKnowledgeBase).where(ChatKnowledgeBase.knowledge_base_id == knowledge_id, ChatKnowledgeBase.chat_id.in_(select(Chat.id).where(Chat.user_id == user_id))))
    await session.execute(delete(KnowledgeGrant).where(KnowledgeGrant.knowledge_base_id == knowledge_id, KnowledgeGrant.user_id == user_id))
    await session.commit()
    return Response(status_code=204)
