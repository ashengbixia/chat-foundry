"""推荐追问按账号开关，内容继续保存在消息output中。"""
from alembic import op
import sqlalchemy as sa
revision = 'e06a91c4d873'
down_revision = 'd95e7b320a61'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user_tool_settings', sa.Column('follow_ups_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    with op.batch_alter_table('user_tool_settings') as batch:
        batch.drop_column('follow_ups_enabled')
