from sqlalchemy.ext.asyncio import AsyncSession
import hashlib
from sqlalchemy import delete, select, update  # 构建文件查询和删除语句
from sqlalchemy.orm import load_only  # 列表不加载文件正文和磁盘路径
from backend.app.models.file import File
from backend.app.models.file_chunk import FileChunk
from backend.app.models.message_file import MessageFile
from backend.app.models.knowledge import KnowledgeBaseFile
from backend.app.models.chat_file import ChatFile

class FileDeletionError(ValueError):  # 仍在处理的文件暂时不允许删除
    pass

async def prepare_file_deletion(session: AsyncSession, file_id: str):  # 先取得SQLite写锁，避免与发送附件并发写入
    result = await session.execute(update(File).where(File.id == file_id).values(updated_at=File.updated_at))
    if result.rowcount == 0:
        return None
    record = (await session.execute(select(File.id, File.storage_path, File.status, File.index_task_id, File.index_status).where(File.id == file_id))).one()
    if record.status == "processing":
        raise FileDeletionError("文件正在处理中，请稍后再删除")
    if record.index_task_id is not None or record.index_status == "indexing":
        raise FileDeletionError("文件正在等待或建立索引，请稍后再删除")
    return record

async def delete_file_rows(session: AsyncSession, file_id: str):  # 保留历史快照，切断外键后再删除切片及文件
    await session.execute(update(ChatFile).where(ChatFile.linked_file_id == file_id).values(linked_file_id=None))
    await session.execute(delete(KnowledgeBaseFile).where(KnowledgeBaseFile.file_id == file_id))
    await session.execute(update(MessageFile).where(MessageFile.linked_file_id == file_id).values(linked_file_id=None))
    await session.execute(delete(FileChunk).where(FileChunk.file_id == file_id))
    await session.execute(delete(File).where(File.id == file_id))

async def list_files(session: AsyncSession, user_id: str | None = None) -> list[File]:  # 查询当前账号的基本信息，最近更新的排在前面
    statement = select(File).options(load_only(File.id, File.filename, File.content_type, File.size, File.status, File.error, File.created_at, File.updated_at, File.index_status, File.index_error, File.index_key, File.index_task_id, File.content_hash, File.indexed_content_hash)).order_by(File.updated_at.desc(), File.id.desc())
    statement = statement.where(File.user_id == user_id)
    result = await session.execute(statement)
    return list(result.scalars().all())

async def create_file(session: AsyncSession, file_id: str, filename: str, storage_path: str, content_type: str | None, user_id: str | None = None) -> File:  # 创建正在处理的文件记录
    record = File(id=file_id, filename=filename, storage_path=storage_path, content_type=content_type, user_id=user_id)
    try:
        session.add(record)
        await session.commit()
        await session.refresh(record)
        return record
    except Exception:
        await session.rollback()
        raise

async def get_file(session: AsyncSession, file_id: str) -> File | None:  # 查询文件，不存在时返回 None
    return await session.get(File, file_id)

async def update_file(session: AsyncSession, file_id: str, size: int, text_content: str = "", error: str | None = None, commit: bool = True) -> File:
    try:
        record = await get_file(session, file_id)
        if record is None:
            raise ValueError("文件记录不存在")
        if record.index_busy:
            raise ValueError("文件正在等待或建立索引，不能替换正文和切片")
        record.size = size
        record.text_content = text_content
        record.content_hash = hashlib.sha256(text_content.encode('utf-8')).hexdigest() if error is None else None
        record.status = "failed" if error is not None else "completed"
        record.error = error
        record.index_status = "pending"
        record.index_error = None
        record.index_key = None
        record.chunk_key = None
        record.indexed_content_hash = None
        await session.execute(delete(FileChunk).where(FileChunk.file_id == file_id))  # 原文变化即撤销旧切片，新的Token切片由后台统一生成
        if commit:
            await session.commit()
            await session.refresh(record)
        else:
            await session.flush()
        return record
    except Exception:
        await session.rollback()
        raise
