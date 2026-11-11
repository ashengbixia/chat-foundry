import logging
import time
import anyio
from sqlalchemy import select
from backend.app.core.database import SessionLocal, DATA_DIR
from backend.app.models.file import File
from backend.app.models.file_cleanup import FileCleanup
from backend.app.rag.vector_store import delete_all_file_vectors

logger = logging.getLogger(__name__)


async def process_cleanup_job(session, file_id, upload_dir=None, delete_vectors=None):
    job = await session.get(FileCleanup, file_id)
    if job is None:
        return True
    try:
        if await session.get(File, file_id) is not None:  # 不允许误删仍有有效数据库记录的文件
            raise ValueError("文件仍存在，停止清理")
        if job.storage_path:
            root = (upload_dir or DATA_DIR / 'uploads').resolve()
            path = (root / job.storage_path).resolve()
            if path.parent != root or path == root or path.is_dir():
                raise ValueError("文件清理路径不合法")
            await anyio.Path(path).unlink(missing_ok=True)
        await anyio.to_thread.run_sync(delete_vectors or delete_all_file_vectors, file_id)
        await session.delete(job)
        await session.commit()
        return True
    except Exception:
        await session.rollback()
        job = await session.get(FileCleanup, file_id)
        if job is not None:
            job.attempts += 1
            job.next_retry_at = int(time.time()) + min(300, 15 * 2 ** min(job.attempts, 4))
            job.last_error = "文件或向量清理未完成，后台将继续重试"
            await session.commit()
        logger.warning("文件清理暂未完成，已保留重试任务：%s", file_id, exc_info=True)
        return False


async def retry_file_cleanups(sessions=SessionLocal, upload_dir=None):
    async with sessions() as session:
        ids = list((await session.execute(select(FileCleanup.file_id).where(FileCleanup.next_retry_at <= int(time.time())).order_by(FileCleanup.next_retry_at).limit(20))).scalars())
    for file_id in ids:
        async with sessions() as session:
            await process_cleanup_job(session, file_id, upload_dir)


async def cleanup_worker(sessions=None):
    while True:
        try:
            await retry_file_cleanups(sessions or SessionLocal)
        except Exception:
            logger.exception("后台文件清理扫描失败，稍后重试")
        await anyio.sleep(30)
