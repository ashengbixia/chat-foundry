import asyncio
import logging
import uuid
import anyio
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.core.database import SessionLocal
from backend.app.models.file import File
from backend.app.repositories.file_chunk import list_file_chunks, replace_chunk_records, replace_file_chunks
from backend.app.models.file_chunk import FileChunk
from backend.app.rag.embedding import embed_texts, split_for_embedding
from backend.app.rag.vector_store import delete_file_vectors, get_index_key, save_chunk_vectors

logger = logging.getLogger(__name__)  # 详细失败原因仅写后端日志
BATCH_SIZE = 32  # 每批计算切片数量，不一次加载整份文档

class IndexBusyError(ValueError):
    pass

class IndexContentError(ValueError):  # 只包含可以直接向用户展示的固定提示
    pass

async def reserve_file_index(session: AsyncSession, file_id: str, rebuild_chunks: bool = False) -> tuple[File, str]:  # 返回响应前预约任务，防止重复提交
    try:
        result = await session.execute(update(File).where(File.id == file_id).values(updated_at=File.updated_at))  # 先取得SQLite写锁
        if result.rowcount == 0:
            raise FileNotFoundError("文件不存在")
        record = await session.get(File, file_id, populate_existing=True)
        if record.status != "completed":
            raise IndexBusyError("文件文字尚未处理成功，不能建立索引")
        if record.index_busy:
            raise IndexBusyError("文件正在等待或建立索引，请稍后再操作")
        if rebuild_chunks:
            await replace_file_chunks(session, file_id, record.text_content)
        task_id = str(uuid.uuid4())
        record.index_status = "pending"
        record.index_error = None
        record.index_key = None
        record.index_task_id = task_id
        await session.commit()
        await session.refresh(record)
        return record, task_id
    except BaseException:
        await session.rollback()
        raise

async def _finish_index(file_id: str, task_id: str, status: str, error: str | None = None, key: str | None = None):  # 旧任务不能覆盖新任务的状态
    async with SessionLocal() as session:
        await session.execute(update(File).where(File.id == file_id, File.index_task_id == task_id).values(index_status=status, index_error=error, index_key=key, index_task_id=None))
        await session.commit()

async def _owns_task(file_id: str, task_id: str) -> bool:  # 不长时间占用数据库事务
    async with SessionLocal() as session:
        return (await session.execute(select(File.id).where(File.id == file_id, File.index_task_id == task_id))).scalar_one_or_none() is not None

async def build_chunk_index(file_id: str, task_id: str) -> int:  # 分批读取、计算、写入，全部成功才标记ready
    async with SessionLocal() as session:
        text = (await session.execute(select(File.text_content).where(File.id == file_id, File.index_task_id == task_id))).scalar_one()
    prepared = await split_for_embedding(text)
    async with SessionLocal() as session:
        locked = await session.execute(update(File).where(File.id == file_id, File.index_task_id == task_id, File.index_status == "indexing").values(updated_at=File.updated_at))
        if locked.rowcount == 0:
            raise RuntimeError("索引任务已失效")
        previous = (await session.execute(select(FileChunk.content, FileChunk.start_offset, FileChunk.end_offset).where(FileChunk.file_id == file_id).order_by(FileChunk.chunk_index))).all()
        expected = [(chunk["content"], chunk["start_offset"], chunk["end_offset"]) for chunk in prepared]
        if [tuple(row) for row in previous] != expected:
            await replace_chunk_records(session, file_id, prepared)
        await session.commit()  # 原文不变；只有切片策略或内容变化才更换切片ID
    await anyio.to_thread.run_sync(delete_file_vectors, file_id)
    offset = 0
    while True:
        if not await _owns_task(file_id, task_id):
            raise RuntimeError("索引任务已失效")
        async with SessionLocal() as session:
            chunks, total = await list_file_chunks(session, file_id, offset, BATCH_SIZE)
            items = [{"id": chunk.id, "file_id": chunk.file_id, "content": chunk.content, "chunk_index": chunk.chunk_index} for chunk in chunks]
        if not items:
            break
        vectors = await embed_texts([item["content"] for item in items])
        if not await _owns_task(file_id, task_id):
            raise RuntimeError("索引任务已失效")
        await anyio.to_thread.run_sync(save_chunk_vectors, items, vectors)
        offset += len(items)
        if offset >= total:
            break
    if offset == 0:
        raise IndexContentError("文件没有可建立索引的切片，请先重新切片")
    return offset

async def index_file(file_id: str, task_id: str):  # 后台任务自行创建会话，不使用上传请求的数据库会话
    try:
        async with SessionLocal() as session:
            result = await session.execute(update(File).where(File.id == file_id, File.status == "completed", File.index_task_id == task_id, File.index_status == "pending").values(index_status="indexing"))
            if result.rowcount == 0:
                await session.rollback()
                return
            await session.commit()
        key = get_index_key()
        await build_chunk_index(file_id, task_id)
        await _finish_index(file_id, task_id, "ready", key=key)
    except BaseException as error:
        logger.exception("文件索引失败，文件ID：%s", file_id)
        with anyio.CancelScope(shield=True):
            try:
                if await _owns_task(file_id, task_id):
                    try:
                        await anyio.to_thread.run_sync(delete_file_vectors, file_id)
                    except Exception:
                        logger.exception("清理未完成的向量失败，文件ID：%s", file_id)
                    message = str(error) if isinstance(error, IndexContentError) else "索引任务已中断，请重新建立索引" if isinstance(error, asyncio.CancelledError) else "向量索引建立失败，请查看后端日志后重试"
                    await _finish_index(file_id, task_id, "failed", error=message)
            except Exception:
                logger.exception("记录索引失败状态失败，重启后将恢复，文件ID：%s", file_id)
        if not isinstance(error, Exception):
            raise

async def recover_interrupted_indexes(session: AsyncSession) -> int:  # 单后端进程启动时释放未完成任务
    result = await session.execute(update(File).where((File.index_task_id.is_not(None)) | (File.index_status == "indexing")).values(index_status="failed", index_error="上次索引任务因服务重启而中断，请重新建立索引", index_key=None, index_task_id=None))
    await session.commit()
    return result.rowcount
