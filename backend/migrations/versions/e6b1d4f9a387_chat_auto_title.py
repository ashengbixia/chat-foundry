"""保存自动标题状态；旧会话保持原名。"""
from alembic import op
import sqlalchemy as sa

revision = 'e6b1d4f9a387'
down_revision = 'd5a0c3e8f276'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('chats') as batch:
        batch.add_column(sa.Column('title_status', sa.String(16), nullable=False, server_default='manual'))


def downgrade():
    with op.batch_alter_table('chats') as batch:
        batch.drop_column('title_status')
