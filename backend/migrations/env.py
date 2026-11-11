import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.core.database import Base, DATABASE_URL
from backend.app.models.chat import Chat, Message
from backend.app.models.file import File  # 让迁移比较也能识别文件表
from backend.app.models.message_file import MessageFile  # 识别消息附件关联表
from backend.app.models.file_chunk import FileChunk  # 识别文件切片表
from backend.app.models.user import User, UserSession
from backend.app.models.model_setting import UserModelSetting
from backend.app.models.knowledge import KnowledgeBase, KnowledgeBaseFile, ChatKnowledgeBase
from backend.app.models.chat_file import ChatFile
from backend.app.models.file_cleanup import FileCleanup


# Alembic 的配置对象。
config = context.config

database_url = config.attributes.get("database_url", DATABASE_URL)  # 允许升级协调器指定临时库
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# 导入模型后，Base 中就注册了会话、消息、文件、附件关联和切片表。
target_metadata = Base.metadata


def run_migrations_offline():
    """只生成迁移 SQL，不实际连接数据库。"""

    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    """在数据库连接中执行迁移。"""

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations():
    """创建迁移专用的异步数据库引擎。"""

    engine = create_async_engine(
        database_url,
        poolclass=pool.NullPool,
    )

    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


def run_migrations_online():
    """连接数据库运行迁移。"""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
