"""个人知识库、文件成员和会话选择。"""
from alembic import op
import sqlalchemy as sa

revision = "a2d7f0b5c943"
down_revision = "f1c6e9a4b832"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("knowledge_bases", sa.Column("id", sa.String(36), primary_key=True), sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False), sa.Column("name", sa.String(100), nullable=False), sa.Column("description", sa.Text(), nullable=False), sa.Column("created_at", sa.Integer(), nullable=False), sa.Column("updated_at", sa.Integer(), nullable=False))
    op.create_index("ix_knowledge_bases_user_id", "knowledge_bases", ["user_id"])
    op.create_table("knowledge_base_files", sa.Column("knowledge_base_id", sa.String(36), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True), sa.Column("file_id", sa.String(36), sa.ForeignKey("files.id", ondelete="CASCADE"), primary_key=True))
    op.create_index("ix_knowledge_base_files_file_id", "knowledge_base_files", ["file_id"])
    op.create_table("chat_knowledge_bases", sa.Column("chat_id", sa.String(36), sa.ForeignKey("chats.id", ondelete="CASCADE"), primary_key=True), sa.Column("knowledge_base_id", sa.String(36), sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True))
    op.create_index("ix_chat_knowledge_bases_knowledge_base_id", "chat_knowledge_bases", ["knowledge_base_id"])


def downgrade():
    op.drop_table("chat_knowledge_bases")
    op.drop_table("knowledge_base_files")
    op.drop_table("knowledge_bases")
