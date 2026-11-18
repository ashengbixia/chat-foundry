"""分支复制到明确边界，保留原会话和原证据，不截断原消息。"""
import copy
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, or_, case
from backend.app.auth import check_chat_access, get_current_user
from backend.app.core.database import get_db
from backend.app.models.chat import Chat, Message
from backend.app.models.chat_file import ChatFile
from backend.app.models.message_file import MessageFile
from backend.app.models.knowledge import ChatKnowledgeBase
from backend.app.repositories.knowledge import resolve_knowledge_scope
from backend.app.repositories.chat import get_chat
from backend.app.chat.guards import acquire_generation, release_generation
from backend.app.schemas.chat import ChatResponse

router = APIRouter(prefix='/api/chats', tags=['会话分支'], dependencies=[Depends(check_chat_access)])


class BranchDraft(BaseModel):
    message_id: str = Field(min_length=1, max_length=36)
    content: str | None = Field(default=None, min_length=1, max_length=10000)
    regenerate: bool = False


@router.get('/{chat_id}/branches', response_model=list[ChatResponse])
async def branches(chat_id: str, user=Depends(get_current_user), session=Depends(get_db)):
    chat = await get_chat(session, chat_id)
    root = chat.branch_root_id or chat.id
    return list((await session.execute(select(Chat).where(Chat.user_id == user.id, Chat.kind == 'conversation', or_(Chat.id == root, Chat.branch_root_id == root)).order_by(case((Chat.id == root, 0), else_=1), Chat.created_at, Chat.id))).scalars())


@router.post('/{chat_id}/branches', response_model=ChatResponse, status_code=201)
async def fork_branch(chat_id: str, data: BranchDraft, user=Depends(get_current_user), session=Depends(get_db)):
    if not acquire_generation(chat_id, user.id):
        raise HTTPException(409, '请先停止回答，再编辑或创建分支')
    try:
        source = await get_chat(session, chat_id)
        anchor = await session.scalar(select(Message).where(Message.id == data.message_id, Message.chat_id == chat_id))
        if anchor is None: raise HTTPException(404, '消息不存在')
        if data.content is not None and (anchor.role != 'user' or not data.content.strip()):
            raise HTTPException(400, '只能编辑用户消息，正文不能为空')
        cutoff = anchor.sequence
        if data.regenerate:
            if anchor.role != 'assistant': raise HTTPException(400, '请选择助手回答重新生成')
            cutoff = await session.scalar(select(Message.sequence).where(Message.chat_id == chat_id, Message.role == 'user', Message.sequence < anchor.sequence).order_by(Message.sequence.desc()).limit(1))
            if cutoff is None: raise HTTPException(400, '找不到对应用户问题')
        child = Chat(id=str(uuid.uuid4()), user_id=user.id, title=source.title[:190] + ' · 分支', title_status='manual',
            web_search_enabled=source.web_search_enabled, assistant_id=source.assistant_id,
            branch_root_id=source.branch_root_id or source.id, branch_parent_id=source.id, branch_from_sequence=cutoff)
        session.add(child); await session.flush()
        rows = (await session.execute(select(Message).where(Message.chat_id == chat_id, Message.sequence <= cutoff).order_by(Message.sequence))).scalars()
        for row in rows:
            if row.status == 'generating': raise HTTPException(409, '消息尚未保存完成')
            cloned = Message(id=str(uuid.uuid4()), chat_id=child.id, sequence=row.sequence, role=row.role,
                content=data.content.strip() if row.id == anchor.id and data.content is not None else row.content,
                status=row.status, model=row.model, output=copy.deepcopy(row.output), usage=copy.deepcopy(row.usage),
                context_summary=copy.deepcopy(row.context_summary), error=row.error, created_at=row.created_at)
            if row.role == 'assistant':
                cloned.output = [*cloned.output, {'type': 'branch_snapshot', 'chat_id': chat_id, 'message_id': row.id}]
            cloned.attachments = [MessageFile(file_id=item.file_id, linked_file_id=item.linked_file_id, filename=item.filename, size=item.size, position=item.position) for item in row.attachments]
            session.add(cloned)
        earlier_files = select(MessageFile.file_id).join(Message, Message.id == MessageFile.message_id).where(Message.chat_id == chat_id, Message.sequence <= cutoff)
        files = (await session.execute(select(ChatFile).where(ChatFile.chat_id == chat_id, ChatFile.active.is_(True), ChatFile.file_id.in_(earlier_files)))).scalars()
        for item in files:
            session.add(ChatFile(chat_id=child.id, file_id=item.file_id, linked_file_id=item.linked_file_id, filename=item.filename, size=item.size, active=True))
        bases, _ = await resolve_knowledge_scope(session, chat_id)
        session.add_all(ChatKnowledgeBase(chat_id=child.id, knowledge_base_id=item['id']) for item in bases)
        await session.commit()
        await session.refresh(child, ['knowledge_bases', 'files'])
        return await get_chat(session, child.id)
    finally:
        release_generation(chat_id)
