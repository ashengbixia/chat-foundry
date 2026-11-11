import hashlib
import math
import logging
import shutil
import sqlite3
import uuid
from contextlib import closing
from functools import lru_cache
from threading import RLock
from typing import Any
from backend.app.core.config import settings
from backend.app.core.database import DATA_DIR
from backend.app.rag.chunking import chunk_policy_key

VECTOR_DIR = DATA_DIR / "vector_db"  # ChromaDB本地持久化目录，不随启动位置变化
_store_lock = RLock()  # 写入、查询和空集合回收互斥，避免检查为空后另一个任务刚好写入
logger = logging.getLogger(__name__)

@lru_cache(maxsize=1)
def _get_client():  # 所有集合共用持久化客户端，未使用时不创建目录
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    return chromadb.PersistentClient(path=str(VECTOR_DIR), settings=ChromaSettings(anonymized_telemetry=False))

def get_index_key() -> str:  # 不打开向量库，仅计算当前配置对应的集合名称
    identity = chunk_policy_key(settings.embedding_model) + "\n" + settings.embedding_query_prefix + "\nnormalize_embeddings=True"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"file-chunks-{digest}"

@lru_cache(maxsize=1)
def get_collection():  # 延后打开集合，更换模型配置后重启进程
    return _get_client().get_or_create_collection(name=get_index_key(), embedding_function=None, metadata={"hnsw:space": "cosine", "embedding_model": settings.embedding_model})

def get_existing_collection():  # 检索不能悄悄创建空索引，把索引丢失误报为没有匹配
    if not VECTOR_DIR.exists():
        raise ValueError("向量索引目录不存在，请重新建立索引")
    return _get_client().get_collection(name=get_index_key(), embedding_function=None)

def _validate_vector(vector: list[float]) -> None:  # 不允许空向量、布尔值或NaN等无效数值进入索引
    if not isinstance(vector, list) or not vector:
        raise ValueError("向量必须是非空数值列表")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
        raise ValueError("向量必须包含有限数值")

def save_chunk_vectors(items: list[dict[str, Any]], vectors: list[list[float]]) -> None:  # 同一批切片与向量必须逐条对应
    if not isinstance(items, list) or not isinstance(vectors, list) or len(items) != len(vectors):
        raise ValueError("切片列表与向量列表数量不一致")
    if not items:
        return
    ids = set()  # 同批重复ID通常意味着调用方组装错误，不能静默覆盖
    for item, vector in zip(items, vectors):
        if not isinstance(item, dict) or any(not isinstance(item.get(key), str) or not item[key].strip() for key in ("id", "file_id", "content")):
            raise ValueError("切片必须包含有效ID、文件ID和正文")
        if type(item.get("chunk_index")) is not int or item["chunk_index"] < 0:
            raise ValueError("切片序号必须是非负整数")
        if item["id"] in ids:
            raise ValueError("同批切片ID不能重复")
        ids.add(item["id"])
        _validate_vector(vector)
        if len(vector) != len(vectors[0]):
            raise ValueError("同批向量维度必须一致")
    with _store_lock:
        collection = get_collection()
        collection.upsert(ids=[item["id"] for item in items], embeddings=vectors, documents=[item["content"] for item in items], metadatas=[{"file_id": item["file_id"], "chunk_index": item["chunk_index"]} for item in items])

def search_chunk_vectors(vector: list[float], file_ids: list[str], limit: int = 5) -> dict:  # 文件范围必须由业务层提供，不能扩大为全库搜索
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError("候选检索数量必须是1到50之间的整数")
    if not isinstance(file_ids, list) or any(not isinstance(file_id, str) or not file_id.strip() for file_id in file_ids):
        raise ValueError("检索范围必须是有效文件ID列表")
    if not file_ids:
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
    _validate_vector(vector)
    with _store_lock:
        collection = get_existing_collection()
        return collection.query(query_embeddings=[vector], n_results=limit, where={"file_id": {"$in": sorted(set(file_ids))}}, include=["documents", "metadatas", "distances"])

def _remove_if_empty(client, collection) -> bool:  # 先通过Chroma删除空集合，物理目录交给后续回收流程
    if collection.count() != 0:
        return False
    client.delete_collection(name=collection.name)
    get_collection.cache_clear()  # 下次上传重新取得集合，不能继续使用已删除的缓存对象
    return True

def _cleanup_orphan_segments() -> int:  # Chroma 1.5.9删除集合后可能保留物理目录，只回收元数据已不再引用的索引
    root = VECTOR_DIR.resolve()
    metadata = root / "chroma.sqlite3"
    if not metadata.is_file():
        return 0
    with closing(sqlite3.connect(metadata.as_uri() + "?mode=ro", uri=True)) as connection:
        active = {row[0] for row in connection.execute("SELECT id FROM segments")}
    removed = 0
    for path in root.iterdir():
        try:
            uuid.UUID(path.name)  # 只识别Chroma的UUID索引目录，保留数据库及其他文件
        except ValueError:
            continue
        if path.name in active or not path.is_dir() or path.is_symlink():
            continue
        target = path.resolve()
        if target.parent != root:  # 包括Windows目录联接在内，实际删除路径必须仍在向量库内
            continue
        try:
            shutil.rmtree(target)
            removed += 1
        except OSError:
            logger.warning("已失效的向量目录仍被占用或无法删除，下次启动重试：%s", path.name, exc_info=True)
    return removed

def prune_empty_collections() -> int:  # 回收此前留下的空集合，不触碰其他应用的集合
    if not VECTOR_DIR.exists():
        return 0
    with _store_lock:
        client = _get_client()
        removed = 0
        for collection in client.list_collections():
            name = collection if isinstance(collection, str) else collection.name
            if name.startswith("file-chunks-"):
                removed += _remove_if_empty(client, client.get_collection(name=name, embedding_function=None))
        _cleanup_orphan_segments()
        return removed

def delete_file_vectors(file_id: str) -> None:  # 删除指定文件在当前向量配置下的索引
    if not isinstance(file_id, str) or not file_id.strip():
        raise ValueError("删除向量时必须指定有效文件ID")
    if not VECTOR_DIR.exists():
        return
    with _store_lock:
        client = _get_client()
        for item in client.list_collections():
            name = item if isinstance(item, str) else item.name
            if name == get_index_key():
                collection = client.get_collection(name=name, embedding_function=None)
                collection.delete(where={"file_id": file_id})
                _remove_if_empty(client, collection)
                _cleanup_orphan_segments()
                break

def delete_all_file_vectors(file_id: str) -> None:  # 删除文件时清理本项目不同模型集合中的相关记录
    if not isinstance(file_id, str) or not file_id.strip():
        raise ValueError("删除向量时必须指定有效文件ID")
    if not VECTOR_DIR.exists():
        return
    with _store_lock:
        client = _get_client()
        for item in client.list_collections():
            name = item if isinstance(item, str) else item.name
            if name.startswith("file-chunks-"):
                collection = client.get_collection(name=name, embedding_function=None)
                collection.delete(where={"file_id": file_id})
                _remove_if_empty(client, collection)
        _cleanup_orphan_segments()
