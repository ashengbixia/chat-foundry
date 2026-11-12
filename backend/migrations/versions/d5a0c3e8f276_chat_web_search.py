"""会话级联网搜索开关，已有会话默认关闭。"""
from alembic import op
import sqlalchemy as sa

revision = 'd5a0c3e8f276'
down_revision = 'c4f9b2d7e165'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('chats') as batch:
        batch.add_column(sa.Column('web_search_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table('chats') as batch:
        batch.drop_column('web_search_enabled')
