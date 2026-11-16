"""账号长期记忆和工具开关。"""
from alembic import op
import sqlalchemy as sa

revision = 'd95e7b320a61'
down_revision = 'c43d9a71e802'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('user_tool_settings',
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), primary_key=True),
        sa.Column('memory_enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('history_enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('knowledge_enabled', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table('memories',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('content', sa.Text(), nullable=False), sa.Column('path', sa.String(300), nullable=False),
        sa.Column('type', sa.String(16), nullable=False), sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.Integer(), nullable=False), sa.Column('updated_at', sa.Integer(), nullable=False))
    op.create_index('ix_memories_user_id', 'memories', ['user_id'])


def downgrade():
    op.drop_table('memories')
    op.drop_table('user_tool_settings')
