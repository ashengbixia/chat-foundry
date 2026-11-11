from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.models.knowledge import KnowledgeBase, KnowledgeBaseFile, ChatKnowledgeBase
from backend.app.models.chat import Chat
from backend.app.models.file import File


async def list_knowledge_bases(session: AsyncSession, user_id: str, chat_id: str | None = None) -> list[dict]:
    statement = select(KnowledgeBase, func.count(File.id)).outerjoin(KnowledgeBaseFile, KnowledgeBaseFile.knowledge_base_id == KnowledgeBase.id).outerjoin(File, (File.id == KnowledgeBaseFile.file_id) & (File.user_id == user_id)).where(KnowledgeBase.user_id == user_id)
    if chat_id is not None:
        statement = statement.join(ChatKnowledgeBase, ChatKnowledgeBase.knowledge_base_id == KnowledgeBase.id).join(Chat, Chat.id == ChatKnowledgeBase.chat_id).where(Chat.id == chat_id, Chat.user_id == user_id)
    rows = (await session.execute(statement.group_by(KnowledgeBase.id).order_by(KnowledgeBase.updated_at.desc(), KnowledgeBase.id))).all()
    return [{"id": item.id, "name": item.name, "description": item.description, "file_count": count, "created_at": item.created_at, "updated_at": item.updated_at} for item, count in rows]


async def resolve_knowledge_scope(session: AsyncSession, chat_id: str) -> tuple[list[dict], set[str]]:  # 权限从会话所有者推导，绝不接受模型或前端给定的扩大范围
    owner = (await session.execute(select(Chat.user_id).where(Chat.id == chat_id))).scalar_one_or_none()
    if owner is None:
        return [], set()
    bases = await list_knowledge_bases(session, owner, chat_id)
    if not bases:
        return [], set()
    statement = select(File.id).join(KnowledgeBaseFile, KnowledgeBaseFile.file_id == File.id).join(KnowledgeBase, KnowledgeBase.id == KnowledgeBaseFile.knowledge_base_id).where(KnowledgeBase.id.in_([item["id"] for item in bases]), KnowledgeBase.user_id == owner, File.user_id == owner)
    return bases, set((await session.execute(statement)).scalars())
