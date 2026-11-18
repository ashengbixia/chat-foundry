"""知识库目录向量缓存：计算范围来自SQL，展示分页不会裁掉召回候选。"""
import hashlib
import anyio
from backend.app.rag import vector_store as store


def collection_name():
    return 'knowledge-meta-' + store.get_index_key().removeprefix('file-chunks-')


def cached_vectors(owner, knowledge_id, fingerprint):
    with store._store_lock:
        collection = store._get_client().get_or_create_collection(name=collection_name(), embedding_function=None)
        rows = collection.get(where={'$and': [{'user_id': owner}, {'knowledge_id': knowledge_id}]}, include=['metadatas', 'embeddings'])
        metadata = rows.get('metadatas') or []
        if not metadata or any(row.get('fingerprint') != fingerprint for row in metadata):
            return None
        if len(metadata) != metadata[0].get('parts'):
            return None
        vectors = rows.get('embeddings')
        return vectors.tolist() if hasattr(vectors, 'tolist') else vectors


def save_vectors(owner, knowledge_id, fingerprint, vectors):
    with store._store_lock:
        collection = store._get_client().get_or_create_collection(name=collection_name(), embedding_function=None)
        collection.delete(where={'$and': [{'user_id': owner}, {'knowledge_id': knowledge_id}]})
        collection.upsert(ids=[f'{owner}:{knowledge_id}:{i}' for i in range(len(vectors))], embeddings=vectors,
            metadatas=[{'user_id': owner, 'knowledge_id': knowledge_id, 'fingerprint': fingerprint, 'parts': len(vectors)} for _ in vectors])


def prune_vectors(owner, valid_ids):
    if not store.VECTOR_DIR.exists():
        return
    with store._store_lock:
        client = store._get_client()
        names = {item if isinstance(item, str) else item.name for item in client.list_collections()}
        if collection_name() not in names:
            return
        collection = client.get_collection(name=collection_name(), embedding_function=None)
        rows = collection.get(where={'user_id': owner}, include=['metadatas'])
        obsolete = [key for key, metadata in zip(rows['ids'], rows['metadatas']) if metadata['knowledge_id'] not in valid_ids]
        if obsolete:
            collection.delete(ids=obsolete)
            store._remove_if_empty(client, collection)
            store._cleanup_orphan_segments()


def delete_knowledge_vectors(knowledge_id):
    if not store.VECTOR_DIR.exists():
        return
    with store._store_lock:
        client = store._get_client()
        for item in client.list_collections():
            name = item if isinstance(item, str) else item.name
            if name.startswith('knowledge-meta-'):
                collection = client.get_collection(name=name, embedding_function=None)
                collection.delete(where={'knowledge_id': knowledge_id})
                store._remove_if_empty(client, collection)
        store._cleanup_orphan_segments()


async def rank_catalog(owner, bases, query):
    from backend.app.rag.embedding import embed_query, embed_texts, split_for_embedding
    if not bases:
        await anyio.to_thread.run_sync(prune_vectors, owner, set())
        return []
    query_vector = await embed_query(query)
    ranked = []
    for row in bases:
        text = row['name'] + '\n' + row['description']
        fingerprint = hashlib.sha256(text.encode('utf-8')).hexdigest()
        vectors = await anyio.to_thread.run_sync(cached_vectors, owner, row['id'], fingerprint)
        if vectors is None:
            parts = await split_for_embedding(text)
            vectors = await embed_texts([part.content if hasattr(part, 'content') else part['content'] for part in parts])
            await anyio.to_thread.run_sync(save_vectors, owner, row['id'], fingerprint, vectors)
        score = max((sum(a * b for a, b in zip(vector, query_vector)) for vector in vectors), default=-1)
        ranked.append(dict(row, similarity=score))
    await anyio.to_thread.run_sync(prune_vectors, owner, {row['id'] for row in bases})
    return sorted(ranked, key=lambda row: (-row['similarity'], row['id']))


def cleanup_knowledge_vectors(knowledge_id):
    try:
        delete_knowledge_vectors(knowledge_id)
    except Exception:
        import logging
        logging.getLogger(__name__).exception('清理知识库目录向量失败，后续发现时重试当前配置的失效记录')
