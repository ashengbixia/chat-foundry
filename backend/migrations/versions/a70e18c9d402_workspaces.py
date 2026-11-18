"""多连接、助手、会话分支与文档版本；原有模型连接完整迁移。"""
from alembic import op
import sqlalchemy as sa
import uuid
revision = 'a70e18c9d402'
down_revision = 'f38b02a9c714'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('model_service_connections',
        sa.Column('id', sa.String(36), primary_key=True), sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('name', sa.String(80), nullable=False), sa.Column('provider', sa.String(20), nullable=False),
        sa.Column('base_url', sa.String(1000), nullable=False), sa.Column('model', sa.String(200), nullable=False),
        sa.Column('timeout', sa.Float(), nullable=False), sa.Column('key_source', sa.String(20), nullable=False),
        sa.Column('encrypted_api_key', sa.Text(), nullable=True), sa.Column('model_profiles', sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('updated_at', sa.Integer(), nullable=False))
    op.create_index('ix_model_service_connections_user_id', 'model_service_connections', ['user_id'])
    op.add_column('user_model_settings', sa.Column('active_connection_id', sa.String(36), nullable=True))
    bind = op.get_bind()
    for row in bind.execute(sa.text('SELECT * FROM user_model_settings')).mappings().all():
        values = dict(row); values['id'] = str(uuid.uuid4()); values['name'] = '已有模型服务'
        bind.execute(sa.text('INSERT INTO model_service_connections (id,user_id,name,provider,base_url,model,timeout,key_source,encrypted_api_key,model_profiles,updated_at) VALUES (:id,:user_id,:name,:provider,:base_url,:model,:timeout,:key_source,:encrypted_api_key,:model_profiles,:updated_at)'), values)
        bind.execute(sa.text('UPDATE user_model_settings SET active_connection_id=:id WHERE user_id=:user_id'), values)
    op.create_table('assistant_profiles',
        sa.Column('id', sa.String(36), primary_key=True), sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('name', sa.String(80), nullable=False), sa.Column('system_prompt', sa.Text(), nullable=False),
        sa.Column('connection_id', sa.String(36), sa.ForeignKey('model_service_connections.id'), nullable=True),
        sa.Column('model', sa.String(200), nullable=False), sa.Column('tool_names', sa.JSON(none_as_null=True), nullable=True),
        sa.Column('knowledge_ids', sa.JSON(), nullable=False), sa.Column('updated_at', sa.Integer(), nullable=False))
    op.create_index('ix_assistant_profiles_user_id', 'assistant_profiles', ['user_id'])
    op.create_table('knowledge_grants',
        sa.Column('knowledge_base_id', sa.String(36), sa.ForeignKey('knowledge_bases.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), primary_key=True))
    op.create_table('document_revisions',
        sa.Column('id', sa.String(36), primary_key=True), sa.Column('user_id', sa.String(36), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('document_id', sa.String(36), nullable=False), sa.Column('file_id', sa.String(36), sa.ForeignKey('files.id', ondelete='CASCADE'), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False), sa.Column('created_at', sa.Integer(), nullable=False),
        sa.UniqueConstraint('document_id', 'revision', name='uq_document_revision'))
    for column in ['user_id', 'document_id', 'file_id']:
        op.create_index('ix_document_revisions_' + column, 'document_revisions', [column])
    for column, kind in [('assistant_id', sa.String(36)), ('branch_root_id', sa.String(36)), ('branch_parent_id', sa.String(36)), ('branch_from_sequence', sa.Integer())]:
        op.add_column('chats', sa.Column(column, kind, nullable=True))
    op.create_index('ix_chats_branch_root_id', 'chats', ['branch_root_id'])


def downgrade():
    op.drop_index('ix_chats_branch_root_id', table_name='chats')
    with op.batch_alter_table('chats') as batch:
        for name in ['assistant_id', 'branch_root_id', 'branch_parent_id', 'branch_from_sequence']:
            batch.drop_column(name)
    op.drop_table('document_revisions')
    op.drop_table('knowledge_grants')
    op.drop_table('assistant_profiles')
    with op.batch_alter_table('user_model_settings') as batch:
        batch.drop_column('active_connection_id')
    op.drop_table('model_service_connections')
