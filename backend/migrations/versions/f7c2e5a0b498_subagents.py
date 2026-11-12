"""子会话及委派任务。"""
from alembic import op
import sqlalchemy as sa

revision = 'f7c2e5a0b498'
down_revision = 'e6b1d4f9a387'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('chats') as batch:
        batch.add_column(sa.Column('kind', sa.String(16), nullable=False, server_default='conversation'))
    op.create_table('subagent_tasks',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('parent_chat_id', sa.String(36), sa.ForeignKey('chats.id', ondelete='CASCADE'), nullable=False),
        sa.Column('parent_message_id', sa.String(36), sa.ForeignKey('messages.id', ondelete='CASCADE'), nullable=False),
        sa.Column('child_chat_id', sa.String(36), sa.ForeignKey('chats.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('call_id', sa.String(200), nullable=False),
        sa.Column('request_key', sa.String(64), nullable=False),
        sa.Column('task', sa.Text(), nullable=False),
        sa.Column('background', sa.Boolean(), nullable=False),
        sa.Column('scope', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('result_message_id', sa.String(36), nullable=True),
        sa.Column('delivery_status', sa.String(20), nullable=False),
        sa.Column('created_at', sa.Integer(), nullable=False),
        sa.Column('started_at', sa.Integer(), nullable=True),
        sa.Column('finished_at', sa.Integer(), nullable=True),
        sa.UniqueConstraint('parent_message_id', 'request_key', name='uq_subagent_request'))
    op.create_index('ix_subagent_tasks_user_id', 'subagent_tasks', ['user_id'])
    op.create_index('ix_subagent_tasks_parent_chat_id', 'subagent_tasks', ['parent_chat_id'])


def downgrade():
    op.drop_table('subagent_tasks')
    with op.batch_alter_table('chats') as batch:
        batch.drop_column('kind')
