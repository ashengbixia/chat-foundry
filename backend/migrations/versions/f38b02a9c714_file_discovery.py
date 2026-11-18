"""账号上传文件发现开关，与当前会话附件授权分别管理。"""
from alembic import op
import sqlalchemy as sa
revision = 'f38b02a9c714'
down_revision = 'e06a91c4d873'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('user_tool_settings', sa.Column('files_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    with op.batch_alter_table('user_tool_settings') as batch:
        batch.drop_column('files_enabled')
