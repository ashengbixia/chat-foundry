from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import StreamingResponse
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel,Field
from backend.app.llm.client import generate_reply
from backend.app.llm.client import ModelStreamError
from backend.app.llm.budget import InputBudgetError
from backend.app.chat.service import stream_chat_events
from contextlib import asynccontextmanager
import logging  # 记录启动恢复结果

from backend.app.core.database import SessionLocal, engine, init_db  # 数据库会话、引擎和初始化方法
from backend.app.repositories.chat import recover_interrupted_messages  # 恢复上次中断的消息状态
from backend.app.rag.indexing import recover_interrupted_indexes
from backend.app.rag.embedding import prepare_embedding_model
from backend.app.rag.vector_store import prune_empty_collections
from backend.app.auth import PrivateResponseHeaders, get_current_user
from backend.app.files.cleanup import cleanup_worker
from backend.app.chat.titles import recover_interrupted_titles, close_title_tasks
from backend.app.chat.subagents import recover_tasks, close_tasks
import anyio
from backend.app.core.config import settings
from backend.app.core.database import DATA_DIR
from backend.app.core.instance import InstanceLock

logger = logging.getLogger(__name__)  # 当前模块日志

@asynccontextmanager
async def lifespan(app: FastAPI):  # 管理应用启动和关闭
    if settings.app_env == 'production' and not settings.auth_cookie_secure:
        raise RuntimeError('生产环境必须启用 AUTH_COOKIE_SECURE，并通过 HTTPS 访问。')
    instance = InstanceLock(DATA_DIR).acquire()  # 开发reload也只有实际服务进程持锁
    try:
        await init_db()  # 新库初始化，已有库检查版本和结构；需要升级时提示命令
        async with SessionLocal() as session:  # 启动恢复使用独立数据库会话
            recovered_count = await recover_interrupted_messages(session)  # 处理上次遗留的生成中消息
            recovered_indexes = await recover_interrupted_indexes(session)
            await recover_interrupted_titles(session)
            await recover_tasks(session)
        if recovered_count > 0:
            logger.warning("已将 %s 条中断的助手消息标记为失败", recovered_count)
        if recovered_indexes > 0:
            logger.warning("已将 %s 个中断的文件索引标记为失败，可手动重建", recovered_indexes)
        try:
            await anyio.to_thread.run_sync(prune_empty_collections)  # 回收旧空集合及上次因文件占用未删掉的索引目录
        except Exception:
            logger.exception("回收空向量索引失败，后端继续启动")
        await prepare_embedding_model()  # 启动时在线程中尝试下载并加载本地模型，失败会记录日志并继续启动
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(cleanup_worker, SessionLocal)
            try:
                yield  # 清理任务在启动时扫描，并在运行期间持续重试
            finally:
                tasks.cancel_scope.cancel()
    finally:
        try:
            await close_tasks()
            await close_title_tasks()
            await engine.dispose()  # 退出时释放数据库连接
        finally:
            instance.release()

from backend.app.api.chats import router as chats_router
from backend.app.api.files import router as files_router  # 注册文件上传和查询接口
from backend.app.api.auth import router as auth_router
from backend.app.api.model_settings import router as model_settings_router, get_user_model_connection
from backend.app.llm.connection import ModelConnection
app = FastAPI(title=" AI 助手",lifespan=lifespan,)
app.include_router(chats_router)#把接口分组注册到 FastAPI 应用上
app.include_router(files_router)
app.include_router(auth_router)
app.include_router(model_settings_router)
from backend.app.api.knowledge import router as knowledge_router
app.include_router(knowledge_router)

app.add_middleware(PrivateResponseHeaders)  # 防止浏览器缓存私人数据，不改变聊天流的取消逻辑

from backend.app.llm.http import ModelAccessError
from httpx import PoolTimeout

@app.exception_handler(ModelAccessError)
async def model_access_denied(_request, error):
    return JSONResponse(status_code=403, content={'detail': str(error)})

@app.exception_handler(PoolTimeout)
async def model_capacity_exceeded(_request, _error):
    return JSONResponse(status_code=429, content={'detail': '模型请求排队超时，请稍后重试'}, headers={'Retry-After': '10'})

@app.exception_handler(RequestValidationError)
async def invalid_request(_request, error: RequestValidationError):  # 参数错误只返回字段位置和说明，避免回显密码或API Key输入
    return JSONResponse(status_code=422, content={"detail": [{"loc": item["loc"], "msg": item["msg"], "type": item["type"]} for item in error.errors()]})

class ChatRequest(BaseModel):
    """聊天接口接收的数据。"""
    message: str = Field(min_length=1, max_length=10000)
@app.get("/health")
def health_check():
    """检查后端是否正常运行。"""
    return {"status": "ok"}
@app.post('/api/chat', dependencies=[Depends(get_current_user)])
async def chat(request:ChatRequest, connection: ModelConnection = Depends(get_user_model_connection)):
    try:
        reply=await generate_reply(request.message, connection)
    except (InputBudgetError, ModelStreamError) as error:
        raise HTTPException(status_code=400, detail=str(error))
    return {"reply":reply}
@app.post("/api/chat/stream", dependencies=[Depends(get_current_user)])
async def chat_stream(request: ChatRequest, connection: ModelConnection = Depends(get_user_model_connection)):
    """把模型生成的正文片段持续发送给客户端。"""
    return StreamingResponse(
        stream_chat_events(request.message, connection),
        media_type="text/event-stream",   #media_type="text/plain; charset=utf-8",纯正文流式
        headers={"Cache-Control": "no-cache"},

    )
