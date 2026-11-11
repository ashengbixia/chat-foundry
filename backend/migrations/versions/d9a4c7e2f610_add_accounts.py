"""增加账号、登录会话与数据归属。"""
from alembic import op
import sqlalchemy as sa

revision = "d9a4c7e2f610"
down_revision = "c8f3b6a0d921"
branch_labels = None
depends_on = None

def upgrade() -> None:  # 历史数据先保留为空，在首次初始化管理员的同一事务里认领
    op.create_table("users", sa.Column("id", sa.String(36), primary_key=True), sa.Column("email", sa.String(254), nullable=False, unique=True), sa.Column("name", sa.String(80), nullable=False), sa.Column("password_hash", sa.String(255), nullable=False), sa.Column("role", sa.String(20), nullable=False), sa.Column("admin_requested", sa.Boolean(), nullable=False), sa.Column("created_at", sa.Integer(), nullable=False))
    op.create_table("user_sessions", sa.Column("token_hash", sa.String(64), primary_key=True), sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False), sa.Column("expires_at", sa.Integer(), nullable=False))
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])
    for table in ["chats", "files"]:
        existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}
        with op.batch_alter_table(table) as batch:
            if "user_id" not in existing:
                batch.add_column(sa.Column("user_id", sa.String(36), nullable=True))
                batch.create_foreign_key(f"fk_{table}_user_id", "users", ["user_id"], ["id"])
                batch.create_index(f"ix_{table}_user_id", ["user_id"])

def downgrade() -> None:
    for table in ["files", "chats"]:
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_user_id")
            batch.drop_column("user_id")
    op.drop_table("user_sessions")
    op.drop_table("users")
