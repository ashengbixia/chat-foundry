import asyncio
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from alembic import command
from backend.app.core import database, db_setup

class DatabaseSetupTests(unittest.TestCase):  # 全部使用临时磁盘库，避免修改真实聊天数据
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "data" / "test.db"

    def query(self, sql):  # 读取测试库，并确保 Windows 下及时关闭连接
        with closing(sqlite3.connect(str(self.path))) as connection:
            return connection.execute(sql).fetchall()

    def execute(self, sql):
        with closing(sqlite3.connect(str(self.path))) as connection:
            connection.executescript(sql)
            connection.commit()

    def make_old_database(self, with_attachment=True):  # 从当前结构降级，构造真实的旧版表结构
        db_setup.prepare_database(self.path)
        command.downgrade(db_setup.make_config(self.path), "5b9b94b2c2ac")
        self.execute("INSERT INTO chats (id, title, created_at, updated_at) VALUES ('chat', '保留会话', 1, 1); INSERT INTO messages (id, chat_id, sequence, role, content, output, status, created_at, updated_at) VALUES ('msg', 'chat', 1, 'user', '保留正文', '[]', 'completed', 1, 1);")
        if with_attachment:
            self.execute("INSERT INTO files (id, filename, size, storage_path, text_content, status, created_at, updated_at) VALUES ('file', '附件.txt', 6, 'file.txt', '原文', 'completed', 1, 1); INSERT INTO message_files VALUES ('msg', 'file', '附件.txt', 6, 0);")
        else:
            self.execute("DROP TABLE message_files; DROP TABLE file_chunks; DROP TABLE files;")

    def test_fresh_database_records_revision_and_all_tables(self):
        result = db_setup.prepare_database(self.path)
        self.assertEqual(result["status"], "created")
        self.assertEqual(self.query("SELECT version_num FROM alembic_version"), [(result["revision"],)])
        tables = {row[0] for row in self.query("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"chats", "messages", "files", "message_files", "file_chunks", "alembic_version"} <= tables)

    def test_previous_version_upgrade_preserves_file_and_starts_pending(self):
        db_setup.prepare_database(self.path)
        command.downgrade(db_setup.make_config(self.path), "b7e2a4c9d610")
        self.execute("INSERT INTO files (id, filename, size, storage_path, text_content, status, created_at, updated_at) VALUES ('file', '保留.txt', 6, 'file.txt', '已有正文', 'completed', 1, 1)")
        with self.assertRaisesRegex(RuntimeError, "需要升级"):
            db_setup.prepare_database(self.path)
        result = db_setup.prepare_database(self.path, upgrade=True)
        self.assertEqual(result["status"], "upgraded")
        self.assertEqual(self.query("SELECT text_content, status, index_status, index_task_id FROM files"), [("已有正文", "completed", "pending", None)])
        self.assertEqual(result["revision"], "f1c6e9a4b832")

    def test_current_database_does_not_change_data_or_create_backup(self):
        db_setup.prepare_database(self.path)
        self.execute("INSERT INTO chats (id, title, created_at, updated_at) VALUES ('chat', '不变', 1, 1);")
        before = self.path.read_bytes()
        self.assertEqual(db_setup.prepare_database(self.path, upgrade=True)["status"], "ready")
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(list(self.path.parent.glob("*before-upgrade*")), [])

    def test_old_database_startup_refuses_and_prints_upgrade_command(self):
        self.make_old_database()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "python -m backend.app.core.db_setup --upgrade"):
            db_setup.prepare_database(self.path)
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(list(self.path.parent.glob("*before-upgrade*")), [])

    def test_upgrade_preserves_messages_attachments_and_old_backup(self):
        self.make_old_database()
        result = db_setup.prepare_database(self.path, upgrade=True)
        self.assertEqual(result["status"], "upgraded")
        self.assertEqual(self.query("SELECT content, sequence FROM messages"), [("保留正文", 1)])
        self.assertEqual(self.query("SELECT file_id, linked_file_id, filename FROM message_files"), [("file", "file", "附件.txt")])
        with closing(sqlite3.connect(result["backup"])) as connection:
            self.assertEqual(connection.execute("SELECT version_num FROM alembic_version").fetchone()[0], "5b9b94b2c2ac")
            self.assertEqual(connection.execute("SELECT content FROM messages").fetchone()[0], "保留正文")
        self.assertEqual(db_setup.prepare_database(self.path)["status"], "ready")

    def test_old_database_missing_historical_file_tables(self):
        self.make_old_database(with_attachment=False)
        self.assertEqual(db_setup.prepare_database(self.path, upgrade=True)["status"], "upgraded")
        self.assertEqual(self.query("SELECT content FROM messages"), [("保留正文",)])

    def test_unversioned_or_unknown_database_is_not_mutated(self):
        db_setup.prepare_database(self.path)
        for sql in ["UPDATE alembic_version SET version_num='unknown'", "DELETE FROM alembic_version"]:
            self.execute(sql)
            before = self.path.read_bytes()
            with self.assertRaises(RuntimeError):
                db_setup.prepare_database(self.path, upgrade=True)
            self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(list(self.path.parent.glob("*before-upgrade*")), [])

    def test_head_with_schema_drift_refuses_instead_of_repairing(self):
        db_setup.prepare_database(self.path)
        self.execute("ALTER TABLE chats ADD COLUMN unexpected TEXT")
        before = self.path.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "结构与当前代码不一致"):
            db_setup.prepare_database(self.path)
        self.assertEqual(before, self.path.read_bytes())

    def test_invalid_foreign_key_data_refuses(self):
        db_setup.prepare_database(self.path)
        self.execute("INSERT INTO file_chunks VALUES ('chunk', 'missing-file', 0, '正文', 0, 2)")
        with self.assertRaisesRegex(RuntimeError, "无效外键"):
            db_setup.prepare_database(self.path)

    def test_failed_upgrade_keeps_backup_and_reports_path(self):
        self.make_old_database()
        with patch.object(db_setup.command, "upgrade", side_effect=RuntimeError("模拟失败")):
            with self.assertRaisesRegex(RuntimeError, "升级前备份") as error:
                db_setup.prepare_database(self.path, upgrade=True)
        backups = list(self.path.parent.glob("*before-upgrade*"))
        self.assertEqual(len(backups), 1)
        self.assertIn(str(backups[0]), str(error.exception))
        self.assertEqual(self.query("SELECT version_num FROM alembic_version"), [("5b9b94b2c2ac",)])

    def test_setup_is_independent_of_current_working_directory(self):
        previous = Path.cwd()
        try:
            os.chdir(self.directory.name)
            self.assertEqual(db_setup.prepare_database(self.path)["status"], "created")
        finally:
            os.chdir(previous)

    def test_async_startup_uses_worker_thread_for_alembic(self):
        with patch.object(db_setup, "DATABASE_FILE", self.path):
            asyncio.run(database.init_db())
        self.assertEqual(db_setup.prepare_database(self.path)["status"], "ready")

    def test_backup_includes_committed_wal_data(self):
        db_setup.prepare_database(self.path)
        with closing(sqlite3.connect(str(self.path))) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("INSERT INTO chats (id, title, created_at, updated_at) VALUES ('wal', 'WAL中的数据', 1, 1)")
            connection.commit()
            backup = db_setup.backup_database(self.path)
            with closing(sqlite3.connect(str(backup))) as copied:
                self.assertEqual(copied.execute("SELECT title FROM chats").fetchall(), [("WAL中的数据",)])
