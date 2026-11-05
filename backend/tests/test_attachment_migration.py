import importlib.util
import unittest
from pathlib import Path
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, inspect, text

path = Path(__file__).resolve().parents[1] / "migrations" / "versions" / "b7e2a4c9d610_keep_deleted_attachment_snapshots.py"
spec = importlib.util.spec_from_file_location("attachment_snapshot_migration", path)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

class AttachmentMigrationTests(unittest.TestCase):
    def setUp(self):  # 独立旧结构数据库，不接触真实 app.db
        self.engine = create_engine("sqlite:///:memory:")
        @event.listens_for(self.engine, "connect")
        def enable_fk(connection, record):
            connection.execute("PRAGMA foreign_keys=ON")
        self.connection = self.engine.connect()
        self.connection.execute(text("CREATE TABLE messages (id VARCHAR(36) PRIMARY KEY)"))
        self.connection.execute(text("CREATE TABLE files (id VARCHAR(36) PRIMARY KEY)"))
        self.connection.execute(text("INSERT INTO messages VALUES ('m1')"))
        self.connection.execute(text("INSERT INTO files VALUES ('f1')"))
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.engine.dispose()

    def old_table(self):
        self.connection.execute(text("CREATE TABLE message_files (message_id VARCHAR(36) REFERENCES messages(id), file_id VARCHAR(36) REFERENCES files(id), filename VARCHAR(255) NOT NULL, size INTEGER NOT NULL, position INTEGER NOT NULL, PRIMARY KEY(message_id,file_id))"))
        self.connection.execute(text("CREATE INDEX ix_message_files_file_id ON message_files(file_id)"))
        self.connection.execute(text("INSERT INTO message_files VALUES ('m1','f1','旧资料.txt',123,0)"))
        self.connection.commit()

    def run_migration(self, action):
        with Operations.context(MigrationContext.configure(self.connection)):
            action()
        self.connection.commit()

    def test_old_rows_are_preserved_and_link_is_backfilled(self):
        self.old_table()
        self.run_migration(migration.upgrade)
        row = self.connection.execute(text("SELECT message_id,file_id,linked_file_id,filename,size,position FROM message_files")).one()
        self.assertEqual(tuple(row), ("m1", "f1", "f1", "旧资料.txt", 123, 0))
        keys = inspect(self.connection).get_foreign_keys("message_files")
        self.assertFalse(any(key["constrained_columns"] == ["file_id"] for key in keys))
        self.assertTrue(any(key["constrained_columns"] == ["linked_file_id"] and key["options"].get("ondelete") == "SET NULL" for key in keys))
        self.assertEqual(self.connection.execute(text("PRAGMA foreign_key_check")).all(), [])

    def test_file_delete_nulls_live_link_and_keeps_snapshot(self):
        self.old_table()
        self.run_migration(migration.upgrade)
        self.connection.execute(text("DELETE FROM files WHERE id='f1'"))
        self.connection.commit()
        row = self.connection.execute(text("SELECT file_id,linked_file_id,filename,size FROM message_files")).one()
        self.assertEqual(tuple(row), ("f1", None, "旧资料.txt", 123))
        with self.assertRaisesRegex(RuntimeError, "无法无损降级"):
            self.run_migration(migration.downgrade)

    def test_upgrade_is_repeatable_and_does_not_revive_deleted_links(self):
        self.old_table()
        self.run_migration(migration.upgrade)
        self.connection.execute(text("DELETE FROM files WHERE id='f1'"))
        self.connection.execute(text("INSERT INTO files VALUES ('f1')"))
        self.connection.commit()
        self.run_migration(migration.upgrade)
        self.assertIsNone(self.connection.execute(text("SELECT linked_file_id FROM message_files")).scalar_one())

    def test_missing_table_can_be_created(self):
        self.run_migration(migration.upgrade)
        columns = {column["name"] for column in inspect(self.connection).get_columns("message_files")}
        self.assertIn("linked_file_id", columns)
        self.connection.execute(text("INSERT INTO message_files VALUES ('m1','f1','f1','资料.txt',1,0)"))
        self.connection.commit()
        self.assertEqual(self.connection.execute(text("PRAGMA foreign_key_check")).all(), [])

    def test_downgrade_without_deleted_files_keeps_old_data(self):
        self.old_table()
        self.run_migration(migration.upgrade)
        self.run_migration(migration.downgrade)
        self.assertNotIn("linked_file_id", {column["name"] for column in inspect(self.connection).get_columns("message_files")})
        self.assertEqual(tuple(self.connection.execute(text("SELECT * FROM message_files")).one()), ("m1", "f1", "旧资料.txt", 123, 0))
