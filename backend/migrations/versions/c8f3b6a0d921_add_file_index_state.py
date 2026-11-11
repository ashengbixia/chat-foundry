"""增加文件向量索引状态。

Revision ID: c8f3b6a0d921
Revises: b7e2a4c9d610
"""
from alembic import op
import sqlalchemy as sa

revision = "c8f3b6a0d921"
down_revision = "b7e2a4c9d610"
branch_labels = None
depends_on = None

def upgrade() -> None:  # 历史文件保留原文，初始为待建立索引
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("files")}
    columns = [sa.Column("index_status", sa.String(20), nullable=False, server_default="pending"), sa.Column("index_error", sa.Text(), nullable=True), sa.Column("index_key", sa.String(64), nullable=True), sa.Column("index_task_id", sa.String(36), nullable=True)]
    for column in columns:
        if column.name not in existing:  # 兼容升级协调器补建的历史缺失文件表
            op.add_column("files", column)

def downgrade() -> None:
    connection = op.get_bind()
    busy = connection.execute(sa.text("SELECT COUNT(*) FROM files WHERE index_task_id IS NOT NULL OR index_status = 'indexing'")).scalar_one()
    if busy:
        raise RuntimeError("存在未完成的文件索引任务，请停止任务后再降级")
    with op.batch_alter_table("files") as batch:
        for name in ["index_task_id", "index_key", "index_error", "index_status"]:
            batch.drop_column(name)
