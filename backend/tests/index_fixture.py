from backend.app.files.splitter import split_text
from unittest.mock import AsyncMock, Mock, patch
from backend.app.rag import indexing
from backend.app.files import service

def mock_file_vectors(case, sessions):  # 旧文件测试运行真实索引调度，但只计算假向量，不碰真实向量库和模型
    async def encode(texts):
        return [[1.0, 0.0] for text in texts]
    patches = [patch.object(indexing, "split_for_embedding", AsyncMock(side_effect=lambda text: split_text(text))), patch.object(indexing, "SessionLocal", sessions), patch.object(indexing, "embed_texts", AsyncMock(side_effect=encode)), patch.object(indexing, "save_chunk_vectors", Mock()), patch.object(indexing, "delete_file_vectors", Mock()), patch.object(service, "delete_all_file_vectors", Mock())]
    for current in patches:
        current.start()
        case.addCleanup(current.stop)
