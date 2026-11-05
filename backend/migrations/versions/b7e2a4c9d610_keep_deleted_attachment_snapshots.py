"""保留已删除文件的历史附件快照。

Revision ID: b7e2a4c9d610
Revises: 5b9b94b2c2ac
"""
from alembic import op
import sqlalchemy as sa

revision = "b7e2a4c9d610"
down_revision = "5b9b94b2c2ac"
branch_labels = None
depends_on = None

def upgrade() -> None:  # file_id保留为历史ID，另设可置空的文件外键
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    if not inspector.has_table("message_files"):
        op.create_table("message_files", sa.Column("message_id", sa.String(36), sa.ForeignKey("messages.id"), primary_key=True), sa.Column("file_id", sa.String(36), primary_key=True), sa.Column("linked_file_id", sa.String(36), sa.ForeignKey("files.id", ondelete="SET NULL"), nullable=True), sa.Column("filename", sa.String(255), nullable=False), sa.Column("size", sa.Integer(), nullable=False), sa.Column("position", sa.Integer(), nullable=False))
        op.create_index("ix_message_files_file_id", "message_files", ["file_id"])
        op.create_index("ix_message_files_linked_file_id", "message_files", ["linked_file_id"])
        return
    columns = {column["name"] for column in inspector.get_columns("message_files")}
    if "linked_file_id" not in columns:
        op.add_column("message_files", sa.Column("linked_file_id", sa.String(36), nullable=True))
        connection.execute(sa.text("UPDATE message_files SET linked_file_id = file_id WHERE file_id IN (SELECT id FROM files)"))
    foreign_keys = inspector.get_foreign_keys("message_files")
    old_key = next((key for key in foreign_keys if key["constrained_columns"] == ["file_id"]), None)
    has_link = any(key["constrained_columns"] == ["linked_file_id"] for key in foreign_keys)
    if old_key or not has_link:
        convention = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}  # 为旧的未命名外键提供可删除名称
        with op.batch_alter_table("message_files", naming_convention=convention) as batch:
            if old_key:
                batch.drop_constraint(old_key["name"] or "fk_message_files_file_id_files", type_="foreignkey")
            if not has_link:
                batch.create_foreign_key("fk_message_files_linked_file_id_files", "files", ["linked_file_id"], ["id"], ondelete="SET NULL")
    indexes = {index["name"] for index in sa.inspect(connection).get_indexes("message_files")}
    if "ix_message_files_linked_file_id" not in indexes:
        op.create_index("ix_message_files_linked_file_id", "message_files", ["linked_file_id"])

def downgrade() -> None:  # 已删除文件的快照不能重新变成非空外键，拒绝会丢数据的降级
    connection = op.get_bind()
    missing = connection.execute(sa.text("SELECT COUNT(*) FROM message_files WHERE file_id NOT IN (SELECT id FROM files)")).scalar_one()
    if missing:
        raise RuntimeError("存在已删除文件的附件快照，无法无损降级，请保留当前版本")
    convention = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}
    with op.batch_alter_table("message_files", naming_convention=convention) as batch:
        batch.drop_constraint("fk_message_files_linked_file_id_files", type_="foreignkey")
        batch.drop_index("ix_message_files_linked_file_id")
        batch.drop_column("linked_file_id")
        batch.create_foreign_key("fk_message_files_file_id_files", "files", ["file_id"], ["id"])
