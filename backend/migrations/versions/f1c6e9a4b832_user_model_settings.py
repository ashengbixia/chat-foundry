"""保存每个用户的模型服务配置与加密密钥。"""
from alembic import op
import sqlalchemy as sa

revision = "f1c6e9a4b832"
down_revision = "e0b5d8f3a721"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table("user_model_settings", sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), primary_key=True), sa.Column("provider", sa.String(20), nullable=False), sa.Column("base_url", sa.String(1000), nullable=False), sa.Column("model", sa.String(200), nullable=False), sa.Column("timeout", sa.Float(), nullable=False), sa.Column("key_source", sa.String(20), nullable=False), sa.Column("encrypted_api_key", sa.Text(), nullable=True), sa.Column("updated_at", sa.Integer(), nullable=False))

def downgrade() -> None:
    op.drop_table("user_model_settings")
