import logging
import math
from typing import Any
import anyio
from sqlalchemy import func, select, or_, case
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.models.file import File
from backend.app.models.file_chunk import FileChunk
from backend.app.rag.embedding import embed_query
from backend.app.rag.vector_store import get_index_key, search_chunk_vectors
from backend.app.rag.ranking import tokens, bm25_scores, exact_identifiers, conflicting_identifier
from backend.app.core.config import settings

logger = logging.getLogger(__name__)
MAX_RESULT_CHARS = 5000  # 限制一次检索交给模型的正文总长度，仍受聊天总输入预算约束
REFERENCE_NOTICE = "这些是最接近查询的候选片段，不代表完整文件或必然相关；distance是余弦距离，越小越接近，不是正确率。需要全文或上下文时调用read_uploaded_file。"

async def _file_states(session: AsyncSession, file_ids: set[str]) -> dict:  # 只查基本状态，不把整份原文加载进内存
    statement = select(File.id, File.filename, File.status, File.index_status, File.index_key, File.index_task_id, File.content_hash, File.indexed_content_hash).where(File.id.in_(sorted(file_ids)))
    return {row.id: row for row in (await session.execute(statement)).all()}

def _unavailable(file_id: str, row: Any, key: str) -> dict | None:  # 状态错误不能伪装成“没有相关资料”
    if row is None:
        reason, message = "missing", "文件已删除或不存在，无法检索"
    elif row.status != "completed":
        reason, message = "text_not_ready", "文件文字尚未处理成功"
    elif row.index_task_id is not None or row.index_status == "indexing":
        reason, message = "indexing", "文件正在等待或建立索引，请稍后刷新状态"
    elif row.index_status == "failed":
        reason, message = "index_failed", "索引建立失败，请重建索引，或使用原文读取工具"
    elif row.index_status != "ready":
        reason, message = "index_pending", "文件尚未建立索引，请先建立索引，或使用原文读取工具"
    elif row.index_key != key:
        reason, message = "model_changed", "向量模型配置已变化，请重新建立索引"
    elif row.content_hash and row.content_hash != row.indexed_content_hash:
        reason, message = "content_changed", "正文已变化，旧向量不能继续使用，请重新建立索引"
    else:
        return None
    return {"file_id": file_id, "filename": row.filename if row is not None else None, "reason": reason, "message": message}

async def retrieve_file_chunks(session: AsyncSession, query: str, file_ids: set[str], limit: int = 5) -> dict:  # 所有附件检索共用的业务流程
    if not isinstance(query, str) or not query.strip() or len(query) > 1000:
        raise ValueError("检索问题必须是1到1000个字符的文字")
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("返回数量必须是1到10之间的整数")
    if not isinstance(file_ids, set) or not file_ids or any(not isinstance(file_id, str) or not file_id.strip() for file_id in file_ids):
        raise ValueError("当前没有有效的附件检索范围")
    query = query.strip()
    key = get_index_key()
    states = await _file_states(session, file_ids)
    unavailable = []
    for file_id in sorted(file_ids):
        item = _unavailable(file_id, states.get(file_id), key)
        if item is not None:
            unavailable.append(item)
    ready_ids = file_ids - {item["file_id"] for item in unavailable}
    result = {"status": "unavailable", "query": query, "chunks": [], "unavailable_files": unavailable, "notice": REFERENCE_NOTICE, "truncated": False}
    if not ready_ids:
        return result  # 不下载模型，也不查询不完整的索引
    terms = list(dict.fromkeys(tokens(query)))[:32]
    lexical = {}
    if terms:
        conditions = [func.instr(func.lower(FileChunk.content), term) > 0 for term in terms]
        hits = sum(case((condition, 1), else_=0) for condition in conditions)
        statement = select(FileChunk.id, FileChunk.content).join(File, File.id == FileChunk.file_id).where(FileChunk.file_id.in_(sorted(ready_ids)), File.status == 'completed', File.index_status == 'ready', File.index_key == key, File.index_task_id.is_(None), or_(*conditions)).order_by(hits.desc(), FileChunk.id).limit(512)
        lexical = {row.id: row.content for row in (await session.execute(statement)).all()}
    keyword_scores = bm25_scores(query, lexical)
    keyword_rank = sorted(keyword_scores, key=lambda item: (-keyword_scores[item], item))[:50]
    semantic = {}
    try:
        vector = await embed_query(query)
        raw = await anyio.to_thread.run_sync(search_chunk_vectors, vector, sorted(ready_ids), min(limit * 4, 50))
        if not isinstance(raw, dict) or not isinstance(raw.get("ids"), list) or not raw["ids"] or not isinstance(raw["ids"][0], list):
            raise RuntimeError("向量库返回格式不合法")
        distances = raw.get("distances")
        if not isinstance(distances, list) or not distances or not isinstance(distances[0], list):
            raise RuntimeError("向量库缺少距离列表")
        for chunk_id, distance in zip(raw["ids"][0], distances[0]):
            if isinstance(chunk_id, str) and chunk_id and type(distance) in (int, float) and math.isfinite(distance):
                if 0 <= distance <= settings.retrieval_max_distance:
                    semantic.setdefault(chunk_id, float(distance))
    except Exception:
        logger.exception("附件向量检索失败")
        if not keyword_rank:
            raise ValueError("附件检索失败，请检查向量模型与索引，或改用原文读取工具") from None
        result['notice'] += ' 向量检索暂不可用，本次仅返回关键词候选。'
    fused = {}
    for index, chunk_id in enumerate(keyword_rank, 1):
        fused[chunk_id] = fused.get(chunk_id, 0) + 1 / (60 + index)
        if exact_identifiers(query, lexical[chunk_id]):
            fused[chunk_id] += 1  # 精确位号优先，但其他相关条款仍参与候选融合
    for index, chunk_id in enumerate(semantic, 1):
        fused[chunk_id] = fused.get(chunk_id, 0) + 1 / (60 + index)
    candidates = sorted(fused, key=lambda item: (-fused[item], item))
    # 模型计算和向量查询期间，文件可能被删除或重建；重新核对SQL状态和切片ID。
    current_states = await _file_states(session, ready_ids)
    for file_id in sorted(ready_ids):
        item = _unavailable(file_id, current_states.get(file_id), key)
        if item is not None:
            unavailable.append(item)
    current_ids = ready_ids - {item["file_id"] for item in unavailable}
    if not current_ids:
        return result
    rows = {}
    if candidates:
        statement = select(FileChunk.id, FileChunk.file_id, File.filename, FileChunk.chunk_index, FileChunk.start_offset, FileChunk.end_offset, func.substr(FileChunk.content, 1, MAX_RESULT_CHARS).label("content")).join(File, File.id == FileChunk.file_id).where(FileChunk.id.in_(candidates), FileChunk.file_id.in_(sorted(current_ids)), File.status == "completed", File.index_status == "ready", File.index_key == key, File.index_task_id.is_(None))
        rows = {row.id: row for row in (await session.execute(statement)).all()}
    remaining = MAX_RESULT_CHARS
    seen_content = set()
    for chunk_id in candidates:
        row = rows.get(chunk_id)
        if row is None or not row.content.strip() or conflicting_identifier(query, row.content) or row.content.strip() in seen_content:
            continue
        seen_content.add(row.content.strip())
        if len(result["chunks"]) >= limit:
            break
        if remaining <= 0:
            result["truncated"] = True
            break
        content = row.content[:remaining]
        end_offset = row.start_offset + len(content)
        truncated = end_offset < row.end_offset
        result["truncated"] = result["truncated"] or truncated
        result["chunks"].append({"file_id": row.file_id, "filename": row.filename, "chunk_id": row.id, "chunk_index": row.chunk_index, "start_offset": row.start_offset, "end_offset": end_offset, "chunk_end_offset": row.end_offset, "content": content, "distance": semantic.get(chunk_id), "truncated": truncated})
        result["chunks"][-1]["match_type"] = 'exact' if exact_identifiers(query, row.content) else 'hybrid' if chunk_id in keyword_scores and chunk_id in semantic else 'keyword' if chunk_id in keyword_scores else 'semantic'
        remaining -= len(content)
    result["status"] = "ok" if result["chunks"] else "no_matches"
    if len(rows) < len(candidates):
        result["notice"] += " 已忽略不存在或不再允许使用的候选片段；结果不足时可以重建索引。"
    return result
