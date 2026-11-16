from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from backend.app.auth import get_current_user
from backend.app.core.database import get_db
from backend.app.models.memory import UserToolSetting
from backend.app.models.chat import Chat
from backend.app.models.user import User
from backend.app.tools.account import load_features, find_memories, write_memory, remove_memory
from backend.app.tools.runtime import ToolContext, registry
from backend.app.tools import registry as loaded_registry

router = APIRouter(prefix='/api', tags=['长期记忆与工具'])


class FeatureInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    memory_enabled: bool = Field(strict=True)
    history_enabled: bool = Field(strict=True)
    knowledge_enabled: bool = Field(strict=True)
    follow_ups_enabled: bool | None = Field(default=None, strict=True)


class MemoryInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    content: str = Field(min_length=1, max_length=8000)
    path: str = Field(default='/', max_length=300)
    type: Literal['user', 'context'] = 'user'


class MemoryUpdate(MemoryInput):
    revision: int = Field(ge=1)


@router.get('/settings/tools')
async def get_features(user: User = Depends(get_current_user), session=Depends(get_db)):
    return await load_features(session, user.id)


@router.put('/settings/tools')
async def set_features(data: FeatureInput, user: User = Depends(get_current_user), session=Depends(get_db)):
    from sqlalchemy import update
    await session.execute(update(User).where(User.id == user.id).values(name=User.name))
    row = await session.get(UserToolSetting, user.id)
    if row is None:
        row = UserToolSetting(user_id=user.id)
        session.add(row)
    for key, value in data.model_dump(exclude_none=True).items():
        setattr(row, key, value)
    await session.commit()
    return await load_features(session, user.id)


@router.get('/memories')
async def list_memories(query: str = Query(default='', max_length=200), offset: int = Query(default=0, ge=0), count: int = Query(default=20, ge=1, le=50), user: User = Depends(get_current_user), session=Depends(get_db)):
    return await find_memories(session, user.id, query=query, offset=offset, count=count)


@router.post('/memories', status_code=201)
async def add_memory(data: MemoryInput, user: User = Depends(get_current_user), session=Depends(get_db)):
    try:
        return await write_memory(session, user.id, **data.model_dump())
    except ValueError as error:
        raise HTTPException(400, str(error)) from None


@router.put('/memories/{memory_id}')
async def update_memory(memory_id: str, data: MemoryUpdate, user: User = Depends(get_current_user), session=Depends(get_db)):
    try:
        return await write_memory(session, user.id, memory_id=memory_id, **data.model_dump())
    except ValueError as error:
        raise HTTPException(409, str(error)) from None


@router.delete('/memories/{memory_id}', status_code=204)
async def delete_memory(memory_id: str, revision: int = Query(ge=1), user: User = Depends(get_current_user), session=Depends(get_db)):
    try:
        await remove_memory(session, user.id, memory_id, revision)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    return Response(status_code=204)


@router.get('/tools')
async def tool_catalog(chat_id: str | None = None, user: User = Depends(get_current_user), session=Depends(get_db)):
    from backend.app.repositories.resources import resolve_chat_files
    from backend.app.repositories.knowledge import resolve_knowledge_scope
    files = set()
    if chat_id:
        chat = await session.scalar(select(Chat).where(Chat.id == chat_id, Chat.user_id == user.id, Chat.kind == 'conversation'))
        if chat is None:
            raise HTTPException(404, '会话不存在')
        _, knowledge_files = await resolve_knowledge_scope(session, chat_id)
        files = await resolve_chat_files(session, chat_id) | knowledge_files
    features = await load_features(session, user.id)
    context = ToolContext(session=session, chat_id=chat_id, account_id=user.id, account_features=features, allowed_file_ids=files, resource_tools=True, web_search_enabled=chat.web_search_enabled if chat_id else False)
    return {'tools': [{'name': spec.name, 'label': spec.label, 'description': spec.description, 'scope': spec.scope,
                       'available': spec.is_available(context), 'on_demand': spec.offer_when is not None,
                       'parameters': spec.arguments.model_json_schema()} for spec in registry._tools.values()], 'features': features,
            'notice': '按需工具经search_tools发现后提供完整参数；可用状态仍受当前会话资料、联网开关及执行时权限约束。'}
