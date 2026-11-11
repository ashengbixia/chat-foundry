import logging
from threading import Lock
from typing import Any
import anyio
from backend.app.core.config import settings
from backend.app.rag.chunking import split_with_tokenizer

logger = logging.getLogger(__name__)  # 模型准备进度和失败详情写入后端日志
_model: Any = None  # 启动时尝试加载，后续向量计算复用同一份模型
_model_key: tuple[str, str] | None = None  # 区分模型与运行设备，避免意外复用其他配置
_model_lock = Lock()  # 模型加载和计算串行执行，避免重复加载与CPU争抢

def _load_model(name: str, device: str):  # 真正准备模型时才导入重量依赖，不在导入模块时下载
    from sentence_transformers import SentenceTransformer
    try:
        return SentenceTransformer(name, device=device, trust_remote_code=False, local_files_only=True)  # 完整缓存直接加载，不联网检查可选适配器文件
    except OSError:
        logger.info("本地向量模型缓存不完整，正在尝试联网准备：%s", name)
        return SentenceTransformer(name, device=device, trust_remote_code=False, local_files_only=False)

def _get_model_locked():  # 调用方持有模型锁，启动准备和实际计算共用加载逻辑
    global _model, _model_key
    key = (settings.embedding_model, settings.embedding_device)
    if _model is None or _model_key != key:
        model = _load_model(*key)  # 成功后才更新缓存，失败不会阻止后续重试
        _model, _model_key = model, key
    return _model

def _prepare_model_sync():  # 只准备模型，不做无用的向量计算
    with _model_lock:
        _get_model_locked()

async def prepare_embedding_model() -> bool:  # 启动阶段等待模型准备，失败仅降低RAG能力，不阻止普通聊天
    logger.info("正在准备本地向量模型：%s，设备：%s", settings.embedding_model, settings.embedding_device)
    try:
        await anyio.to_thread.run_sync(_prepare_model_sync)
    except Exception:
        logger.exception("启动时准备向量模型失败，后端将继续启动；建立索引或检索时会再次尝试")
        return False
    logger.info("本地向量模型已准备完成：%s", settings.embedding_model)
    return True

def _encode_sync(texts: list[str]) -> list[list[float]]:  # 工作线程执行计算；启动准备失败时仍允许重新加载
    with _model_lock:
        model = _get_model_locked()
        encoded = model.tokenizer(texts, add_special_tokens=True, truncation=False, verbose=False)
        if any(len(ids) > model.max_seq_length for ids in encoded["input_ids"]):
            raise ValueError("文字超过向量模型Token上限，请重新切片或缩短检索问题")  # 禁止静默丢弃片段尾部
        vectors = model.encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False)
        return vectors.tolist()

def _split_sync(text: str):  # 分词和模型加载共用锁，避免影响事件循环
    with _model_lock:
        model = _get_model_locked()
        return split_with_tokenizer(text, model.tokenizer, model.max_seq_length)

async def split_for_embedding(text: str):  # 索引前按当前模型的真实Token窗口生成切片
    return await anyio.to_thread.run_sync(_split_sync, text)

async def embed_texts(texts: list[str]) -> list[list[float]]:  # 一批文本对应一批向量，不阻塞FastAPI事件循环
    if not isinstance(texts, list):
        raise ValueError("向量计算内容必须是文字列表")
    if not texts:
        return []
    if any(not isinstance(text, str) or not text.strip() for text in texts):
        raise ValueError("向量计算内容不能为空")
    return await anyio.to_thread.run_sync(_encode_sync, list(texts))

async def embed_query(query: str) -> list[float]:  # 检索问题单独添加模型需要的查询提示
    if not isinstance(query, str) or not query.strip():
        raise ValueError("检索问题不能为空")
    text = settings.embedding_query_prefix + query.strip()
    vectors = await embed_texts([text])
    return vectors[0]
