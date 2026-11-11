"""文件内容指纹、切片版本和持久清理待办。"""
import hashlib
from alembic import op
import sqlalchemy as sa

revision = "c4f9b2d7e165"
down_revision = "b3e8a1c6d054"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {item['name'] for item in inspector.get_columns('files')}
    constraints = {item['name'] for item in inspector.get_unique_constraints('files')}
    with op.batch_alter_table("files") as batch:
        for name in ('content_hash', 'chunk_key', 'indexed_content_hash'):
            if name not in columns:
                batch.add_column(sa.Column(name, sa.String(64), nullable=True))
        if 'uq_files_user_content_hash' not in constraints:
            batch.create_unique_constraint("uq_files_user_content_hash", ["user_id", "content_hash"])
    connection = op.get_bind()
    seen = set()
    for row in connection.execute(sa.text("SELECT id,user_id,text_content,index_status FROM files WHERE status='completed' ORDER BY created_at,id")).mappings():
        digest = hashlib.sha256(row["text_content"].encode('utf-8')).hexdigest()
        identity = (row["user_id"], digest)
        if identity in seen:  # 既有重复文件不合并，历史引用、知识库及会话关系全部保留
            continue
        seen.add(identity)
        connection.execute(sa.text("UPDATE files SET content_hash=:digest,indexed_content_hash=:indexed WHERE id=:id"), {"digest": digest, "indexed": digest if row['index_status'] == 'ready' else None, "id": row['id']})
    op.create_table("file_cleanup_jobs", sa.Column("file_id", sa.String(36), primary_key=True), sa.Column("storage_path", sa.String(255), nullable=True), sa.Column("attempts", sa.Integer(), nullable=False), sa.Column("next_retry_at", sa.Integer(), nullable=False), sa.Column("last_error", sa.Text(), nullable=True))
    op.create_index("ix_file_cleanup_jobs_next_retry_at", "file_cleanup_jobs", ["next_retry_at"])


def downgrade():
    op.drop_table("file_cleanup_jobs")
    with op.batch_alter_table("files") as batch:
        batch.drop_constraint("uq_files_user_content_hash", type_="unique")
        for column in ("indexed_content_hash", "chunk_key", "content_hash"):
            batch.drop_column(column)
