from backend.app.core.config import settings
import logging
import hashlib
import uuid
from pathlib import Path, PurePosixPath
import anyio  # 异步读写磁盘文件，取消时保护清理过程
from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func
from backend.app.core.database import DATA_DIR
from backend.app.models.file import File
from backend.app.repositories.file import create_file, delete_file_rows, prepare_file_deletion, update_file
from backend.app.rag.vector_store import delete_all_file_vectors
from backend.app.models.file_cleanup import FileCleanup
from backend.app.files.cleanup import process_cleanup_job

logger = logging.getLogger(__name__)  # 只在后端记录详细异常
UPLOAD_DIR = DATA_DIR / "uploads"  # 所有原文件统一保存的位置
MAX_FILE_SIZE = 5 * 1024 * 1024  # 每个文件最多 5MB
READ_CHUNK_SIZE = 64 * 1024  # 每次读取 64KB，累计检查大小

async def delete_uploaded_file(session: AsyncSession, file_id: str) -> bool:  # SQL删除和清理待办同一事务提交；提交前不动原文件
    with anyio.CancelScope(shield=True):
        try:
            record = await prepare_file_deletion(session, file_id)
            if record is None:
                await session.rollback()
                return False
            root = UPLOAD_DIR.resolve()  # 上传目录的真实绝对路径
            path = (root / record.storage_path).resolve()
            if path.parent != root or path == root:
                raise ValueError("文件保存路径不合法")
            if await anyio.Path(path).exists() and not await anyio.Path(path).is_file():
                raise ValueError("文件保存位置不是普通文件")
            session.add(FileCleanup(file_id=file_id, storage_path=record.storage_path))
            await delete_file_rows(session, file_id)
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
        try:
            await process_cleanup_job(session, file_id, UPLOAD_DIR, delete_all_file_vectors)
        except Exception:
            logger.exception("即时清理未完成，待办将在后台重试，文件ID：%s", file_id)
        return True


async def finalize_upload(session, file_id, text, size):  # SQLite写锁内确认同账号内容，避免并发上传各自创建一套索引
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    try:
        await session.execute(update(File).where(File.id == file_id).values(updated_at=File.updated_at))
        current = await session.get(File, file_id, populate_existing=True)
        existing = (await session.execute(select(File).where(File.user_id == current.user_id, File.content_hash == digest, File.status == 'completed', File.id != file_id))).scalar_one_or_none()
        if existing is not None:
            session.add(FileCleanup(file_id=file_id, storage_path=current.storage_path))
            await session.execute(delete(File).where(File.id == file_id))  # 此时尚无切片、附件和知识库关联
            await session.commit()
            existing._upload_reused = True
            try:
                await process_cleanup_job(session, file_id, UPLOAD_DIR, delete_all_file_vectors)
            except Exception:
                logger.exception("重复上传的临时文件清理将由后台重试：%s", file_id)
            await session.refresh(existing)  # 清理失败会回滚事务，返回前重新加载被过期的字段
            return existing
        used, count = (await session.execute(select(func.coalesce(func.sum(File.size), 0), func.count()).where(File.user_id == current.user_id, File.status == 'completed'))).one()
        total = (await session.execute(select(func.coalesce(func.sum(File.size), 0)).where(File.status == 'completed'))).scalar_one()
        if used + size > settings.user_storage_bytes or count >= settings.user_file_count:
            raise HTTPException(413, '个人文件存储额度已用完，请删除不需要的文件')
        if total + size > settings.total_storage_bytes:
            raise HTTPException(507, '服务器文件存储额度已用完，请联系管理员')
        current.content_hash = digest
        await update_file(session, file_id, size, text_content=text, commit=False)
        await session.commit()
        await session.refresh(current)
        return current
    except BaseException:
        await session.rollback()
        raise

async def process_text_file(session: AsyncSession, upload: UploadFile, user_id: str | None = None) -> File:  # 校验 TXT、保存原文件、提取正文并入库
    filename = PurePosixPath((upload.filename or "").replace("\\", "/")).name  # 去掉客户端路径，仅保留展示名称
    if not filename or len(filename) > 255 or "\x00" in filename:
        raise HTTPException(status_code=400, detail="文件名为空或不合法，最长 255 个字符")
    if Path(filename).suffix.lower() != ".txt":
        raise HTTPException(status_code=400, detail="当前仅支持 UTF-8 编码的 .txt 文件")
    file_id = str(uuid.uuid4())  # 提前生成 ID，让数据库记录与磁盘文件对应
    storage_path = f"{file_id}.txt"  # 原始名称不参与磁盘路径拼接
    path = UPLOAD_DIR / storage_path  # 本次唯一的保存位置
    await create_file(session, file_id, filename, storage_path, upload.content_type, user_id)
    content = bytearray()  # 累计最多 5MB，用于完成后的 UTF-8 解码
    size = 0  # 当前已读取的字节数
    created = False  # 仅清理本次确实创建的文件
    try:
        await anyio.Path(UPLOAD_DIR).mkdir(parents=True, exist_ok=True)
        async with await anyio.open_file(path, "xb") as target:  # 独占创建，避免意外覆盖已有文件
            created = True
            while True:
                chunk = await upload.read(READ_CHUNK_SIZE)  # 不信任请求声明的大小
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILE_SIZE:
                    raise HTTPException(status_code=413, detail="文件大小不能超过 5MB")
                await target.write(chunk)
                content.extend(chunk)
        if not content:
            raise HTTPException(status_code=400, detail="不能上传空文件")
        try:
            text = content.decode("utf-8-sig")  # 兼容带 BOM 的 UTF-8 文本
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="文件不是有效的 UTF-8 文本，请转换编码后上传") from None
        if "\x00" in text or not text.strip():
            raise HTTPException(status_code=400, detail="文件必须包含有效文字，不能是二进制或空白内容")
        return await finalize_upload(session, file_id, text, size)
    except BaseException as error:  # 普通失败和请求取消都要清理本次未完成文件
        detail = error.detail if isinstance(error, HTTPException) else "文件处理失败或上传中断，请重新上传"
        with anyio.move_on_after(5, shield=True):
            if created:
                try:
                    await anyio.Path(path).unlink(missing_ok=True)
                except OSError:
                    logger.exception("清理上传文件失败，文件 ID：%s", file_id)
            try:
                await session.rollback()  # 清除可能被取消或失败的数据库事务
                await update_file(session, file_id, upload.size if upload.size is not None else size, error=detail)
            except Exception:
                logger.exception("保存文件失败状态失败，文件 ID：%s", file_id)
        if isinstance(error, HTTPException) or not isinstance(error, Exception):
            raise
        logger.exception("处理上传文件失败，文件 ID：%s", file_id)
        raise HTTPException(status_code=500, detail="文件处理失败，请查看后端日志") from None
