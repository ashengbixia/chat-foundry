from typing import Any
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.models.file import File
from backend.app.rag.retrieval import retrieve_file_chunks

async def search_uploaded_files(query: str, session: AsyncSession, allowed_file_ids: set[str], file_id: str | None = None, limit: int = 5) -> dict[str, Any]:  # 模型只能缩小后端给定的附件范围
    if file_id is not None:
        if not isinstance(file_id, str) or file_id not in allowed_file_ids:
            raise ValueError("该文件不属于本次对话允许检索的附件")
        target_ids = {file_id}
    else:
        target_ids = set(allowed_file_ids)
    if not target_ids:
        raise ValueError("当前没有允许检索的附件")
    return await retrieve_file_chunks(session, query, target_ids, limit)

async def read_uploaded_file(file_id: str, session: AsyncSession, allowed_file_ids: set[str], offset: int = 0, max_chars: int = 6000) -> dict[str, Any]:  # 只读取本次模型上下文允许使用的附件
    if not isinstance(file_id, str) or file_id not in allowed_file_ids:
        raise ValueError("该文件不属于本次对话可读取的附件")
    if type(offset) is not int or not 0 <= offset <= 2147483647:
        raise ValueError("offset必须是0到2147483647之间的整数")
    if type(max_chars) is not int or not 1 <= max_chars <= 12000:
        raise ValueError("max_chars必须是1到12000之间的整数")
    statement = select(File.id, File.filename, File.status, func.length(File.text_content).label("total_chars"), func.substr(File.text_content, offset + 1, max_chars).label("text_content")).where(File.id == file_id)  # 由数据库返回指定字符片段，不加载全文
    result = await session.execute(statement)
    row = result.one_or_none()
    if row is None:
        raise ValueError("文件已删除或不存在，无法再次读取正文")
    if row.status != "completed":
        raise ValueError("文件尚未处理完成，无法读取正文")
    next_offset = offset + len(row.text_content)  # 按字符计算下一页的位置，不是字节或Token
    has_more = next_offset < row.total_chars
    return {"file_id": row.id, "filename": row.filename, "text_content": row.text_content, "offset": offset, "total_chars": row.total_chars, "has_more": has_more, "next_offset": next_offset if has_more else None}
