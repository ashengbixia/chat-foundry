"""按用户和模型保存能力配置，旧配置保持现有行为。"""
from alembic import op
import sqlalchemy as sa

revision = 'a12c8e4f6b90'
down_revision = 'f7c2e5a0b498'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user_model_settings', sa.Column('model_profiles', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))


def downgrade():
    with op.batch_alter_table('user_model_settings') as batch:
        batch.drop_column('model_profiles')
