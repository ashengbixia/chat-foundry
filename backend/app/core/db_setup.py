import argparse
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import URL
from backend.app.core.database import Base, DATABASE_FILE, register_models

UPGRADE_COMMAND = "python -m backend.app.core.db_setup --upgrade"  # 在项目根目录执行
BACKEND_DIR = Path(__file__).resolve().parents[2]  # 配置路径不依赖当前工作目录

def make_config(path: Path) -> Config:  # 为当前数据库创建独立迁移配置
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations").replace("%", "%%"))
    config.attributes["database_url"] = URL.create("sqlite+aiosqlite", database=str(path))
    config.attributes["configure_logger"] = False  # 不覆盖应用的日志配置
    return config

def backup_database(path: Path) -> Path:  # 使用 SQLite 备份接口，包含已提交到 WAL 的内容
    suffix = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    backup = path.with_name(f"{path.stem}-before-upgrade-{suffix}{path.suffix}")
    with closing(sqlite3.connect(str(path))) as source, closing(sqlite3.connect(str(backup))) as target:
        source.backup(target)
    return backup

def verify_schema(engine):  # 版本号正确之外，也检查实际表结构和外键数据
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, Base.metadata)
        if differences:
            raise RuntimeError("数据库结构与当前代码不一致，请保留数据库并检查迁移脚本，不要直接修改版本号。")
        if connection.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
            raise RuntimeError("数据库存在无效外键关联，请保留数据库并检查数据。")

def prepare_database(database_file: Path | None = None, upgrade: bool = False) -> dict:  # 返回初始化或检查结果
    register_models()
    path = Path(database_file if database_file is not None else DATABASE_FILE).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    config = make_config(path)
    scripts = ScriptDirectory.from_config(config)
    head = scripts.get_current_head()
    if head is None:
        raise RuntimeError("未找到数据库迁移版本，请检查迁移文件是否完整。")
    engine = create_engine(URL.create("sqlite", database=str(path)))
    backup = None  # 只有确认旧版本并明确要求升级后才备份和修改
    try:
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            revisions = MigrationContext.configure(connection).get_current_heads()
        if not tables and not revisions:
            Base.metadata.create_all(engine)  # 旧初始迁移只能修改已有表，新库直接创建当前结构
            command.stamp(config, head)
            verify_schema(engine)
            return {"status": "created", "revision": head}
        if len(revisions) != 1:
            raise RuntimeError("已有数据库缺少明确的迁移版本，请先备份并人工确认结构；不能自动标记为最新版本。")
        current = revisions[0]
        if current == head:
            verify_schema(engine)
            return {"status": "ready", "revision": head}
        known_revisions = {revision.revision for revision in scripts.walk_revisions()}
        if current not in known_revisions:
            raise RuntimeError(f"数据库版本 {current} 不在当前迁移链中，请检查代码与数据库是否匹配。")
        if not upgrade:
            raise RuntimeError(f"数据库需要升级（{current} → {head}）。请先停止后端，然后在项目根目录执行：\n{UPGRADE_COMMAND}\n升级完成后重新启动后端。")
        backup = backup_database(path)
        if current == "5b9b94b2c2ac":  # 这些历史表曾依赖启动建表，先补齐后再运行附件迁移
            Base.metadata.tables["files"].create(engine, checkfirst=True)
            Base.metadata.tables["file_chunks"].create(engine, checkfirst=True)
        command.upgrade(config, head)
        verify_schema(engine)
        return {"status": "upgraded", "revision": head, "backup": str(backup)}
    except Exception as error:
        if backup is not None:
            raise RuntimeError(f"数据库升级未完成：{error}\n升级前备份：{backup}\n请停止使用并检查原因，未自动覆盖当前库。") from error
        raise
    finally:
        engine.dispose()

def main():  # 直接运行模块时提供升级入口
    parser = argparse.ArgumentParser(description="初始化、检查或升级项目数据库")
    parser.add_argument("--upgrade", action="store_true", help="停止后端后使用；先备份，再升级旧数据库")
    arguments = parser.parse_args()
    try:
        result = prepare_database(upgrade=arguments.upgrade)
    except Exception as error:
        parser.exit(1, f"{error}\n")
    print(f"数据库检查完成：{result['status']}，版本：{result['revision']}")
    if result.get("backup"):
        print(f"升级前备份：{result['backup']}")

if __name__ == "__main__":
    main()
