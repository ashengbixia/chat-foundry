from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker,create_async_engine
from sqlalchemy.orm import DeclarativeBase


# 数据目录固定在 backend/data，不随启动目录改变。
DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_FILE = DATA_DIR / "app.db"

DATABASE_URL = f"sqlite+aiosqlite:///{DATABASE_FILE.as_posix()}"


# 数据库引擎：管理底层连接，导入时通常还不会真正连接。
engine = create_async_engine(DATABASE_URL)


# 会话工厂：每次调用它，创建一个独立的数据库操作会话。
SessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """所有数据库表模型共用的基类。"""
    pass


async def get_db():
    """为一次接口请求提供数据库会话，用完后关闭。"""

    async with SessionLocal() as session:
        yield session
def register_models():  # 集中注册模型，供启动、迁移检查和测试共用
    from backend.app.models.user import User, UserSession
    from backend.app.models.model_setting import UserModelSetting
    from backend.app.models.chat import Chat, Message
    from backend.app.models.file import File  # 注册文件表，启动时创建尚不存在的表
    from backend.app.models.message_file import MessageFile  # 注册消息附件关联表
    from backend.app.models.file_chunk import FileChunk  # 注册文件切片表


async def create_tables(target_engine):  # 仅供隔离测试建表，正式启动必须检查数据库版本
    register_models()
    async with target_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

async def init_db():  # 在线程中执行同步初始化，避免 Alembic 与当前事件循环冲突
    from anyio import to_thread
    from backend.app.core.db_setup import prepare_database
    await to_thread.run_sync(prepare_database)
