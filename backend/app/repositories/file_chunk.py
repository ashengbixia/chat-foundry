from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.files.splitter import TextChunk, split_text
from backend.app.models.file_chunk import FileChunk

async def replace_file_chunks(session: AsyncSession, file_id: str, text: str) -> int:  # 替换切片，交给调用方统一提交或回滚
    chunks = split_text(text)  # 切片成功后才删除旧数据
    return await replace_chunk_records(session, file_id, chunks)

async def replace_chunk_records(session: AsyncSession, file_id: str, chunks: list[TextChunk]) -> int:  # 保存已准备好的Token切片，不再次按字符切分
    await session.execute(delete(FileChunk).where(FileChunk.file_id == file_id))
    records = [FileChunk(file_id=file_id, **chunk) for chunk in chunks]
    session.add_all(records)
    return len(records)

async def list_file_chunks(session: AsyncSession, file_id: str, offset: int = 0, limit: int = 20) -> tuple[list[FileChunk], int]:  # 按块序号分页读取
    count_statement = select(func.count()).select_from(FileChunk).where(FileChunk.file_id == file_id)
    total = (await session.execute(count_statement)).scalar_one()
    statement = select(FileChunk).where(FileChunk.file_id == file_id).order_by(FileChunk.chunk_index).offset(offset).limit(limit)
    result = await session.execute(statement)
    return list(result.scalars().all()), total
