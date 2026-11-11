"""账号改用用户名登录，保留已有登录名和账号关联。"""
from alembic import op
import sqlalchemy as sa

revision = "e0b5d8f3a721"
down_revision = "d9a4c7e2f610"
branch_labels = None
depends_on = None

def upgrade() -> None:  # 只重命名字段，不重建账号、不改密码或数据归属
    with op.batch_alter_table("users") as batch:
        batch.alter_column("email", new_column_name="username", existing_type=sa.String(254), existing_nullable=False)

def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column("username", new_column_name="email", existing_type=sa.String(254), existing_nullable=False)
