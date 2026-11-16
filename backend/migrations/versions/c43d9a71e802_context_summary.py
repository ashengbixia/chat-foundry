"""把上下文摘要保存在保留轮次的首条用户消息上。"""
from alembic import op
import sqlalchemy as sa

revision = 'c43d9a71e802'
down_revision = 'a12c8e4f6b90'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('messages', sa.Column('context_summary', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('messages') as batch:
        batch.drop_column('context_summary')
