import logging
import anyio
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.core.database import get_db
from backend.app.files.service import delete_uploaded_file, process_text_file
from backend.app.repositories.file import FileDeletionError, get_file, list_files
from backend.app.models.file import File
from backend.app.repositories.file_chunk import list_file_chunks
from backend.app.schemas.file import FileChunkBuildResponse, FileChunkPageResponse, FileContentResponse, FileResponse
from backend.app.rag.indexing import IndexBusyError, index_file, reserve_file_index
from backend.app.auth import check_file_access, get_current_user
from backend.app.models.user import User
from backend.app.rag.vector_store import get_index_key

logger = logging.getLogger(__name__)  # 记录后端异常，不向客户端暴露磁盘路径
router = APIRouter(prefix="/api/files", tags=["文件管理"], dependencies=[Depends(check_file_access)])  # 所有文件路径共用账号与归属检查

@router.get("", response_model=list[FileResponse])
async def list_files_endpoint(session: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):  # 只返回当前账号的文件
    return await list_files(session, user.id)

@router.post("", response_model=FileResponse, status_code=201)
async def upload_file_endpoint(background_tasks: BackgroundTasks, file: UploadFile, session: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):  # 上传响应后再执行索引
    try:
        record = await process_text_file(session, file, user.id)
        reused = record.reused
        if record.index_busy or (record.index_status == 'ready' and record.index_key == get_index_key() and record.index_current):
            return record
        try:
            record, task_id = await reserve_file_index(session, record.id)
        except IndexBusyError:  # 同时上传同一内容时，另一个请求已经预约索引；仍返回复用结果
            await session.refresh(record)
            record._upload_reused = reused
            return record
        record._upload_reused = reused
        background_tasks.add_task(index_file, record.id, task_id)
        return record
    except HTTPException:
        raise
    except Exception:
        logger.exception("上传文件失败")
        raise HTTPException(status_code=500, detail="上传文件失败，请查看后端日志") from None
    finally:
        with anyio.move_on_after(5, shield=True):
            await file.close()  # 成功、失败和取消都关闭框架接收的临时文件

@router.get("/{file_id}", response_model=FileResponse)
async def get_file_endpoint(file_id: str, session: AsyncSession = Depends(get_db)):  # 查询基本信息和处理状态
    record = await get_file(session, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    return record

@router.delete("/{file_id}", status_code=204)
async def delete_file_endpoint(file_id: str, session: AsyncSession = Depends(get_db)):  # 允许删除被引用文件，消息保留附件快照
    try:
        deleted = await delete_uploaded_file(session, file_id)
    except FileDeletionError as error:
        raise HTTPException(status_code=409, detail=str(error))
    except Exception:
        logger.exception("删除文件失败，文件ID：%s", file_id)
        raise HTTPException(status_code=500, detail="删除文件失败，请查看后端日志") from None
    if not deleted:
        raise HTTPException(status_code=404, detail="文件不存在")
    return Response(status_code=204)

@router.get("/{file_id}/content", response_model=FileContentResponse)
async def get_file_content_endpoint(file_id: str, session: AsyncSession = Depends(get_db)):  # 单独读取已提取正文
    record = await get_file(session, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    if record.status != "completed":
        raise HTTPException(status_code=409, detail="文件尚未处理成功，无法读取正文")
    return {"id": record.id, "text_content": record.text_content}

@router.get("/{file_id}/chunks", response_model=FileChunkPageResponse)
async def list_file_chunks_endpoint(file_id: str, offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=100), session: AsyncSession = Depends(get_db)):  # 查询切片，不读取完整正文
    statement = select(File.status).where(File.id == file_id)
    status = (await session.execute(statement)).scalar_one_or_none()
    if status is None:
        raise HTTPException(status_code=404, detail="文件不存在")
    if status != "completed":
        raise HTTPException(status_code=409, detail="文件尚未处理成功")
    items, total = await list_file_chunks(session, file_id, offset, limit)
    return {"items": items, "total": total, "offset": offset, "limit": limit}

@router.post("/{file_id}/chunks", response_model=FileChunkBuildResponse, status_code=202)
async def rebuild_file_chunks_endpoint(file_id: str, background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_db)):  # 替换切片与预约新索引在同一个SQL事务中完成
    try:
        record, task_id = await reserve_file_index(session, file_id, rebuild_chunks=True)
        background_tasks.add_task(index_file, record.id, task_id)
        return {"file_id": file_id, "chunk_count": None, "status": "queued"}
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文件不存在") from None
    except IndexBusyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except Exception:
        await session.rollback()
        logger.exception("重新切片失败，文件ID：%s", file_id)
        raise HTTPException(status_code=500, detail="重新切片失败，请查看后端日志") from None

@router.post("/{file_id}/index", response_model=FileResponse, status_code=202)
async def rebuild_file_index_endpoint(file_id: str, background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_db)):  # 仅重建向量，不修改原文与切片ID
    try:
        record, task_id = await reserve_file_index(session, file_id)
        background_tasks.add_task(index_file, file_id, task_id)
        return record
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文件不存在") from None
    except IndexBusyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except Exception:
        await session.rollback()
        logger.exception("安排文件索引失败，文件ID：%s", file_id)
        raise HTTPException(status_code=500, detail="安排文件索引失败，请查看后端日志") from None
