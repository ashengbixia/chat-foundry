from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select, update  # 构建文件查询和删除语句
from sqlalchemy.orm import load_only  # 列表不加载文件正文和磁盘路径
from backend.app.models.file import File
from backend.app.repositories.file_chunk import replace_file_chunks
from backend.app.models.file_chunk import FileChunk
from backend.app.models.message_file import MessageFile

class FileDeletionError(ValueError):  # 仍在处理的文件暂时不允许删除
    pass

async def prepare_file_deletion(session: AsyncSession, file_id: str):  # 先取得SQLite写锁，避免与发送附件并发写入
    result = await session.execute(update(File).where(File.id == file_id).values(updated_at=File.updated_at))
    if result.rowcount == 0:
        return None
    record = (await session.execute(select(File.id, File.storage_path, File.status).where(File.id == file_id))).one()
    if record.status == "processing":
        raise FileDeletionError("文件正在处理中，请稍后再删除")
    return record

async def delete_file_rows(session: AsyncSession, file_id: str):  # 保留历史快照，切断外键后再删除切片及文件
    await session.execute(update(MessageFile).where(MessageFile.linked_file_id == file_id).values(linked_file_id=None))
    await session.execute(delete(FileChunk).where(FileChunk.file_id == file_id))
    await session.execute(delete(File).where(File.id == file_id))

async def list_files(session: AsyncSession) -> list[File]:  # 查询全部基本信息，最近更新的排在前面
    statement = select(File).options(load_only(File.id, File.filename, File.content_type, File.size, File.status, File.error, File.created_at, File.updated_at)).order_by(File.updated_at.desc(), File.id.desc())
    result = await session.execute(statement)
    return list(result.scalars().all())

async def create_file(session: AsyncSession, file_id: str, filename: str, storage_path: str, content_type: str | None) -> File:  # 创建正在处理的文件记录
    record = File(id=file_id, filename=filename, storage_path=storage_path, content_type=content_type)
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

async def update_file(session: AsyncSession, file_id: str, size: int, text_content: str = "", error: str | None = None) -> File:  # 保存提取结果或失败说明
    try:
        record = await get_file(session, file_id)
        if record is None:
            raise ValueError("文件记录不存在")
        record.size = size
        record.text_content = text_content
        record.status = "failed" if error is not None else "completed"
        record.error = error
        await replace_file_chunks(session, file_id, text_content if error is None else "")  # 正文、状态、切片同一事务保存，失败时清理未完成切片
        await session.commit()
        await session.refresh(record)
        return record
    except Exception:
        await session.rollback()
        raise
