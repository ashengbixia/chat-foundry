"""原文件只读预览与文档版本；新版本不重写历史消息或证据。"""
import re
import time
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, UploadFile, BackgroundTasks, Query
from fastapi.responses import FileResponse as OriginalResponse
from sqlalchemy import select, update, delete, func
from backend.app.auth import check_file_access, get_current_user
from backend.app.core.database import get_db
from backend.app.models.file import File
from backend.app.models.user import User
from backend.app.models.workspace import DocumentRevision
from backend.app.models.knowledge import KnowledgeBaseFile, KnowledgeBase
from backend.app.files import service
from backend.app.rag.indexing import reserve_file_index, index_file, IndexBusyError
from backend.app.rag.vector_store import get_index_key

router = APIRouter(prefix='/api/files', tags=['文档版本与原文件'], dependencies=[Depends(check_file_access)])


async def original(session, file_id):
    record = await session.get(File, file_id)
    if record is None: raise HTTPException(404, '文件不存在')
    root = service.UPLOAD_DIR.resolve()
    target = (root / record.storage_path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(404, '原文件不存在，提取正文或历史快照可能仍可查看')
    return record, target


@router.get('/{file_id}/original')
async def original_file(file_id: str, download: bool = False, session=Depends(get_db)):
    record, target = await original(session, file_id)
    pdf = target.suffix.lower() == '.pdf'
    mime = 'application/pdf' if pdf else 'application/octet-stream'
    return OriginalResponse(target, media_type=mime, filename=record.filename,
        content_disposition_type='attachment' if download or not pdf else 'inline',
        headers={'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-store'})


@router.get('/{file_id}/preview')
async def preview_info(file_id: str, offset: int = Query(default=0, ge=0), session=Depends(get_db)):
    record, target = await original(session, file_id)
    page = 1
    if target.suffix.lower() == '.pdf':
        markers = list(re.finditer(r'(?m)^\[第(\d+)页\]\n', record.text_content[:offset + 1]))
        if markers: page = min(200, int(markers[-1][1]))
    return {'file_id': file_id, 'filename': record.filename, 'preview_type': 'pdf' if target.suffix.lower() == '.pdf' else 'download',
        'url': f'/api/files/{file_id}/original#page={page}', 'download_url': f'/api/files/{file_id}/original?download=true', 'page': page}


@router.get('/{file_id}/versions')
async def versions(file_id: str, user=Depends(get_current_user), session=Depends(get_db)):
    # 即便是共享读者，也只能查看当前获授权文件的版本元数据；不授予其他原文件的读取权。
    source = await session.scalar(select(DocumentRevision).where(DocumentRevision.file_id == file_id).limit(1))
    if source is None:
        record = await session.get(File, file_id)
        return [{'file_id': file_id, 'filename': record.filename, 'revision': 1, 'created_at': record.created_at}]
    rows = (await session.execute(select(DocumentRevision.file_id, DocumentRevision.revision, DocumentRevision.created_at, File.filename)
        .join(File, File.id == DocumentRevision.file_id).where(DocumentRevision.document_id == source.document_id, DocumentRevision.user_id == user.id).order_by(DocumentRevision.revision))).all()
    return [dict(row._mapping) for row in rows]


@router.post('/{file_id}/versions', status_code=201)
async def upload_version(file_id: str, file: UploadFile, background_tasks: BackgroundTasks, user=Depends(get_current_user), session=Depends(get_db)):
    new = None
    committed = False
    try:
        revision = await session.scalar(select(DocumentRevision).where(DocumentRevision.file_id == file_id).limit(1))
        if revision:
            latest = await session.scalar(select(func.max(DocumentRevision.revision)).where(DocumentRevision.document_id == revision.document_id))
            if latest != revision.revision: raise HTTPException(409, '请从文档最新版本上传更新')
        new = await service.process_text_file(session, file, user.id)
        if new.id == file_id: return {'file_id': new.id, 'unchanged': True, 'replaced_libraries': 0}
        await session.execute(update(User).where(User.id == user.id).values(name=User.name))
        source = await session.get(File, file_id)
        if source is None or source.user_id != user.id: raise HTTPException(404, '原文件不存在')
        revision = await session.scalar(select(DocumentRevision).where(DocumentRevision.file_id == file_id).limit(1))
        root = revision.document_id if revision else file_id
        head = await session.scalar(select(DocumentRevision).where(DocumentRevision.document_id == root).order_by(DocumentRevision.revision.desc()).limit(1))
        if head and head.file_id != file_id: raise HTTPException(409, '请从文档最新版本上传更新')
        if await session.scalar(select(DocumentRevision.id).where(DocumentRevision.file_id == new.id).limit(1)):
            raise HTTPException(409, '相同内容已属于另一条版本记录，请直接引用已有文件')
        if revision is None:
            session.add(DocumentRevision(user_id=user.id, document_id=root, file_id=file_id, revision=1))
        session.add(DocumentRevision(user_id=user.id, document_id=root, file_id=new.id, revision=(head.revision if head else 1) + 1))
        libraries = list((await session.execute(select(KnowledgeBase.id).join(KnowledgeBaseFile).where(KnowledgeBaseFile.file_id == file_id, KnowledgeBase.user_id == user.id))).scalars())
        await session.execute(delete(KnowledgeBaseFile).where(KnowledgeBaseFile.file_id == file_id, KnowledgeBaseFile.knowledge_base_id.in_(libraries)))
        for key in libraries:
            if not await session.get(KnowledgeBaseFile, (key, new.id)):
                session.add(KnowledgeBaseFile(knowledge_base_id=key, file_id=new.id))
        await session.execute(update(KnowledgeBase).where(KnowledgeBase.id.in_(libraries)).values(updated_at=int(time.time())))
        await session.commit()
        committed = True
        if not new.index_busy and not (new.index_status == 'ready' and new.index_key == get_index_key() and new.index_current):
            try:
                new, task_id = await reserve_file_index(session, new.id)
                background_tasks.add_task(index_file, new.id, task_id)
            except IndexBusyError: pass
        return {'file_id': new.id, 'unchanged': False, 'replaced_libraries': len(libraries)}
    except HTTPException:
        if new is not None and not new.reused and not committed:
            new_id = new.id
            await session.rollback()
            await service.delete_uploaded_file(session, new_id)
        raise
    finally:
        await file.close()
