"""会话资料持续使用，保留已删除文件快照。"""
from alembic import op
import sqlalchemy as sa

revision = "b3e8a1c6d054"
down_revision = "a2d7f0b5c943"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("chat_files", sa.Column("chat_id", sa.String(36), sa.ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True), sa.Column("file_id", sa.String(36), primary_key=True), sa.Column("linked_file_id", sa.String(36), sa.ForeignKey("files.id", ondelete="SET NULL"), nullable=True), sa.Column("filename", sa.String(255), nullable=False), sa.Column("size", sa.Integer(), nullable=False), sa.Column("active", sa.Boolean(), nullable=False), sa.Column("created_at", sa.Integer(), nullable=False))
    op.create_index("ix_chat_files_linked_file_id", "chat_files", ["linked_file_id"])
    op.execute(sa.text("""INSERT INTO chat_files (chat_id, file_id, linked_file_id, filename, size, active, created_at)
        SELECT m.chat_id, mf.file_id, MAX(f.id), MAX(mf.filename), MAX(mf.size), 1, MIN(m.created_at)
        FROM message_files mf JOIN messages m ON m.id = mf.message_id JOIN chats c ON c.id = m.chat_id
        LEFT JOIN files f ON f.id = mf.linked_file_id AND (f.user_id = c.user_id OR (f.user_id IS NULL AND c.user_id IS NULL))
        WHERE m.role = 'user' GROUP BY m.chat_id, mf.file_id"""))


def downgrade():
    op.drop_table("chat_files")
