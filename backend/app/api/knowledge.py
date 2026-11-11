import time
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.auth import get_current_user, require_chat_owner
from backend.app.core.database import get_db
from backend.app.models.user import User
from backend.app.models.chat import Chat
from backend.app.models.file import File
from backend.app.models.knowledge import KnowledgeBase, KnowledgeBaseFile, ChatKnowledgeBase
from backend.app.repositories.knowledge import list_knowledge_bases
from backend.app.repositories.file import list_files
from backend.app.schemas.file import FileResponse
from backend.app.chat.guards import acquire_generation, release_generation
from backend.app.rag.indexing import IndexBusyError, index_file, reserve_file_index
from backend.app.rag.vector_store import get_index_key

router = APIRouter(prefix="/api", tags=["个人知识库"], dependencies=[Depends(get_current_user)])


class KnowledgeInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)

    @field_validator("name", "description")
    @classmethod
    def trim_text(cls, value, info):
        value = value.strip()
        if info.field_name == "name" and not value:
            raise ValueError("知识库名称不能为空")
        return value


class KnowledgeResponse(KnowledgeInput):
    model_config = ConfigDict(from_attributes=True)
    id: str
    file_count: int = 0
    created_at: int
    updated_at: int


class FileSelection(BaseModel):
    file_ids: list[str] = Field(max_length=200)


class KnowledgeSelection(BaseModel):
    knowledge_base_ids: list[str] = Field(max_length=5)


async def owned_base(session, knowledge_id, user_id, lock=False):
    if lock:  # 先取得SQLite写锁，文件归属校验和关联写入处于同一事务
        result = await session.execute(update(KnowledgeBase).where(KnowledgeBase.id == knowledge_id, KnowledgeBase.user_id == user_id).values(updated_at=int(time.time())))
        if not result.rowcount:
            raise HTTPException(404, "知识库不存在")
    item = (await session.execute(select(KnowledgeBase).where(KnowledgeBase.id == knowledge_id, KnowledgeBase.user_id == user_id))).scalar_one_or_none()
    if item is None:
        raise HTTPException(404, "知识库不存在")
    return item


@router.get("/knowledge", response_model=list[KnowledgeResponse])
async def list_bases(session: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await list_knowledge_bases(session, user.id)


@router.post("/knowledge", response_model=KnowledgeResponse, status_code=201)
async def create_base(body: KnowledgeInput, session: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    item = KnowledgeBase(user_id=user.id, **body.model_dump())
    session.add(item)
    await session.commit()
    return item


@router.patch("/knowledge/{knowledge_id}", response_model=KnowledgeResponse)
async def rename_base(knowledge_id: str, body: KnowledgeInput, session: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    item = await owned_base(session, knowledge_id, user.id, lock=True)
    item.name, item.description = body.name, body.description
    await session.commit()
    return next(base for base in await list_knowledge_bases(session, user.id) if base["id"] == item.id)


@router.delete("/knowledge/{knowledge_id}", status_code=204)
async def delete_base(knowledge_id: str, session: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    item = await owned_base(session, knowledge_id, user.id, lock=True)
    await session.execute(delete(ChatKnowledgeBase).where(ChatKnowledgeBase.knowledge_base_id == knowledge_id))
    await session.execute(delete(KnowledgeBaseFile).where(KnowledgeBaseFile.knowledge_base_id == knowledge_id))
    await session.delete(item)  # 原文件、切片和向量索引保持可复用
    await session.commit()
    return Response(status_code=204)


@router.get("/knowledge/{knowledge_id}/files", response_model=list[FileResponse])
async def base_files(knowledge_id: str, session: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await owned_base(session, knowledge_id, user.id)
    ids = set((await session.execute(select(KnowledgeBaseFile.file_id).where(KnowledgeBaseFile.knowledge_base_id == knowledge_id))).scalars())
    return [file for file in await list_files(session, user.id) if file.id in ids]


@router.post("/knowledge/{knowledge_id}/files", status_code=204)
async def add_files(knowledge_id: str, body: FileSelection, session: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await owned_base(session, knowledge_id, user.id, lock=True)
    ids = set(body.file_ids)
    valid = set((await session.execute(select(File.id).where(File.id.in_(ids), File.user_id == user.id))).scalars())
    if valid != ids:
        raise HTTPException(400, "部分文件不存在或不属于当前账号")
    current = set((await session.execute(select(KnowledgeBaseFile.file_id).where(KnowledgeBaseFile.knowledge_base_id == knowledge_id))).scalars())
    if len(current | ids) > 200:
        raise HTTPException(400, "每个知识库最多加入200份文件")
    session.add_all(KnowledgeBaseFile(knowledge_base_id=knowledge_id, file_id=file_id) for file_id in ids - current)
    await session.commit()
    return Response(status_code=204)


@router.post('/knowledge/{knowledge_id}/index', status_code=202)
async def update_base_index(knowledge_id: str, background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await owned_base(session, knowledge_id, user.id)
    ids = set((await session.execute(select(KnowledgeBaseFile.file_id).where(KnowledgeBaseFile.knowledge_base_id == knowledge_id))).scalars())
    files = [dict(id=item.id, status=item.status, busy=item.index_busy, ready=item.index_status == 'ready' and item.index_key == get_index_key() and item.index_current) for item in await list_files(session, user.id) if item.id in ids]
    counts = dict(queued=0, ready=0, busy=0, failed=0)
    for item in files:
        if item['status'] != 'completed':
            counts['failed'] += 1
        elif item['busy']:
            counts['busy'] += 1
        elif item['ready']:
            counts['ready'] += 1
        else:
            try:
                record, task_id = await reserve_file_index(session, item['id'])
            except (IndexBusyError, FileNotFoundError):
                counts['busy'] += 1
                continue
            background_tasks.add_task(index_file, record.id, task_id)
            counts['queued'] += 1
    return counts


@router.delete("/knowledge/{knowledge_id}/files/{file_id}", status_code=204)
async def remove_file(knowledge_id: str, file_id: str, session: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await owned_base(session, knowledge_id, user.id, lock=True)
    await session.execute(delete(KnowledgeBaseFile).where(KnowledgeBaseFile.knowledge_base_id == knowledge_id, KnowledgeBaseFile.file_id == file_id))
    await session.commit()
    return Response(status_code=204)


@router.get("/chats/{chat_id}/knowledge", response_model=list[KnowledgeResponse], dependencies=[Depends(require_chat_owner)])
async def chat_bases(chat_id: str, session: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return await list_knowledge_bases(session, user.id, chat_id)


@router.put("/chats/{chat_id}/knowledge", response_model=list[KnowledgeResponse], dependencies=[Depends(require_chat_owner)])
async def set_chat_bases(chat_id: str, body: KnowledgeSelection, session: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    if not acquire_generation(chat_id):
        raise HTTPException(409, "会话正在回答，请结束后再调整知识库")
    try:
        result = await session.execute(update(Chat).where(Chat.id == chat_id, Chat.user_id == user.id).values(updated_at=int(time.time())))
        if not result.rowcount:
            raise HTTPException(404, "会话不存在")
        ids = set(body.knowledge_base_ids)
        valid = set((await session.execute(select(KnowledgeBase.id).where(KnowledgeBase.id.in_(ids), KnowledgeBase.user_id == user.id))).scalars())
        if valid != ids:
            raise HTTPException(400, "部分知识库不存在或不属于当前账号")
        await session.execute(delete(ChatKnowledgeBase).where(ChatKnowledgeBase.chat_id == chat_id))
        session.add_all(ChatKnowledgeBase(chat_id=chat_id, knowledge_base_id=item) for item in ids)
        await session.commit()
        return await list_knowledge_bases(session, user.id, chat_id)
    finally:
        release_generation(chat_id)
