from sqlalchemy import select
from backend.app.models.chat import Chat
from backend.app.models.chat_file import ChatFile
from backend.app.models.file import File


async def resolve_chat_files(session, chat_id: str) -> set[str]:  # 读取资格独立于消息分页和模型上下文裁剪
    statement = select(File.id).join(ChatFile, ChatFile.linked_file_id == File.id).join(Chat, Chat.id == ChatFile.chat_id).where(Chat.id == chat_id, ChatFile.active.is_(True), File.user_id.is_not_distinct_from(Chat.user_id))
    return set((await session.execute(statement)).scalars())


async def describe_resources(session, allowed_ids: set[str]) -> list[dict]:
    if not allowed_ids:
        return []
    rows = (await session.execute(select(File.id, File.filename, File.size, File.status, File.index_status, File.index_error, File.error).where(File.id.in_(sorted(allowed_ids))).order_by(File.filename, File.id))).all()
    return [{"file_id": row.id, "filename": row.filename, "size": row.size, "status": row.status, "index_status": row.index_status, "error": row.error or row.index_error} for row in rows]
