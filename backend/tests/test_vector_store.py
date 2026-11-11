import subprocess
import sqlite3
import uuid
from contextlib import closing
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from backend.app.rag import vector_store

def chunk(chunk_id="chunk-a", file_id="file-a", content="住宿上限437元", index=0):  # 生成无真实用户信息的切片
    return {"id": chunk_id, "file_id": file_id, "content": content, "chunk_index": index}

class VectorValidationTests(unittest.TestCase):  # 参数错误必须在打开向量库前拒绝
    def setUp(self):
        self.collection = Mock()
        self.open_patch = patch.object(vector_store, "get_collection", return_value=self.collection)
        self.open_collection = self.open_patch.start()
        self.addCleanup(self.open_patch.stop)
        self.query_patch = patch.object(vector_store, "get_existing_collection", return_value=self.collection)
        self.query_collection = self.query_patch.start()
        self.addCleanup(self.query_patch.stop)

    def test_empty_write_and_empty_scope_do_not_open_database(self):
        vector_store.save_chunk_vectors([], [])
        self.assertEqual(vector_store.search_chunk_vectors([1.0, 0.0], []), {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]})
        self.open_collection.assert_not_called()

    def test_write_checks_count_metadata_duplicates_and_dimensions(self):
        cases = [([chunk()], []), ([chunk(content="")], [[1.0]]), ([chunk(file_id=" ")], [[1.0]]), ([chunk(index=True)], [[1.0]]), ([chunk(index=-1)], [[1.0]]), ([chunk(), chunk()], [[1.0], [1.0]]), ([chunk(), chunk("b")], [[1.0], [1.0, 0.0]])]
        for items, vectors in cases:
            with self.subTest(items=items), self.assertRaises(ValueError):
                vector_store.save_chunk_vectors(items, vectors)
        self.open_collection.assert_not_called()

    def test_empty_nan_infinite_and_boolean_vectors_are_rejected(self):
        for vector in [[], [float("nan")], [float("inf")], [True], ["1"], None]:
            with self.subTest(vector=vector), self.assertRaises(ValueError):
                vector_store.save_chunk_vectors([chunk()], [vector])
            with self.subTest(vector=vector), self.assertRaises(ValueError):
                vector_store.search_chunk_vectors(vector, ["file-a"])
        self.open_collection.assert_not_called()

    def test_search_checks_limit_and_scope(self):
        for limit in [0, 11, True, 1.5]:
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                vector_store.search_chunk_vectors([1.0], ["file-a"], limit)
        for scope in [None, "file-a", [""], [None]]:
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                vector_store.search_chunk_vectors([1.0], scope)
        self.open_collection.assert_not_called()

    def test_valid_write_and_query_forward_explicit_embeddings_and_scope(self):
        vector_store.save_chunk_vectors([chunk()], [[1.0, 0.0]])
        self.collection.upsert.assert_called_once_with(ids=["chunk-a"], embeddings=[[1.0, 0.0]], documents=["住宿上限437元"], metadatas=[{"file_id": "file-a", "chunk_index": 0}])
        vector_store.search_chunk_vectors([1.0, 0.0], ["file-b", "file-a", "file-a"], 3)
        self.collection.query.assert_called_once_with(query_embeddings=[[1.0, 0.0]], n_results=3, where={"file_id": {"$in": ["file-a", "file-b"]}}, include=["documents", "metadatas", "distances"])

    def test_deletion_requires_specific_file(self):
        for file_id in [None, "", " ", 1]:
            with self.subTest(file_id=file_id), self.assertRaises(ValueError):
                vector_store.delete_file_vectors(file_id)
        self.open_collection.assert_not_called()

class CollectionCleanupTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.collection = Mock(name="collection")
        self.collection.name = vector_store.get_index_key()
        self.client.list_collections.return_value = [self.collection]
        self.client.get_collection.return_value = self.collection
        self.path = Mock()
        self.path.exists.return_value = True
        for current in [patch.object(vector_store, "_get_client", return_value=self.client), patch.object(vector_store, "VECTOR_DIR", self.path), patch.object(vector_store, "_cleanup_orphan_segments", return_value=0)]:
            current.start()
            self.addCleanup(current.stop)

    def test_last_file_deletion_removes_collection_and_invalidates_cache(self):
        self.collection.count.return_value = 0
        with patch.object(vector_store.get_collection, "cache_clear") as clear:
            vector_store.delete_all_file_vectors("file-a")
        self.collection.delete.assert_called_once_with(where={"file_id": "file-a"})
        self.client.delete_collection.assert_called_once_with(name=self.collection.name)
        clear.assert_called_once()

    def test_nonempty_collection_is_preserved(self):
        self.collection.count.return_value = 2
        vector_store.delete_file_vectors("file-a")
        self.collection.delete.assert_called_once_with(where={"file_id": "file-a"})
        self.client.delete_collection.assert_not_called()

    def test_cleanup_only_removes_empty_project_collections(self):
        self.collection.count.return_value = 0
        self.client.list_collections.return_value = ["another-app", self.collection]
        self.assertEqual(vector_store.prune_empty_collections(), 1)
        self.client.get_collection.assert_called_once_with(name=self.collection.name, embedding_function=None)

    def test_missing_directory_does_not_create_database(self):
        self.path.exists.return_value = False
        vector_store.delete_file_vectors("file-a")
        vector_store.delete_all_file_vectors("file-a")
        self.assertEqual(vector_store.prune_empty_collections(), 0)
        self.client.list_collections.assert_not_called()

class OrphanDirectoryTests(unittest.TestCase):
    def test_only_unreferenced_uuid_directories_are_removed_and_locked_ones_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            active, orphan = str(uuid.uuid4()), str(uuid.uuid4())
            with closing(sqlite3.connect(root / "chroma.sqlite3")) as database, database:
                database.execute("CREATE TABLE segments (id TEXT)")
                database.execute("INSERT INTO segments VALUES (?)", (active,))
            for name in [active, orphan, "keep-this"]:
                (root / name).mkdir()
                (root / name / "header.bin").write_bytes(b"test")
            with patch.object(vector_store, "VECTOR_DIR", root):
                with patch.object(vector_store.shutil, "rmtree", side_effect=PermissionError("occupied")), patch.object(vector_store, "logger") as logger:
                    self.assertEqual(vector_store._cleanup_orphan_segments(), 0)
                    logger.warning.assert_called_once()
                self.assertTrue((root / orphan).is_dir())
                self.assertEqual(vector_store._cleanup_orphan_segments(), 1)
            self.assertFalse((root / orphan).exists())
            self.assertTrue((root / active / "header.bin").is_file())
            self.assertTrue((root / "keep-this" / "header.bin").is_file())
            self.assertTrue((root / "chroma.sqlite3").is_file())

class PersistentVectorTests(unittest.TestCase):
    def test_query_does_not_create_missing_index(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "not-created"
            with patch.object(vector_store, "VECTOR_DIR", path):
                with self.assertRaisesRegex(ValueError, "不存在"):
                    vector_store.search_chunk_vectors([1.0, 0.0], ["file"])
            self.assertFalse(path.exists())

    def test_real_chroma_scope_upsert_persistence_delete_and_model_isolation(self):  # 子进程退出后释放索引句柄，兼容Windows临时目录清理
        code = r'''
import sys
from pathlib import Path
from types import SimpleNamespace
from backend.app.rag import vector_store as store
store.VECTOR_DIR = Path(sys.argv[1]) / "vector_db"
store.settings = SimpleNamespace(embedding_model="test-model", embedding_query_prefix="test-prefix")
if sys.argv[2] == "write":
    items = [{"id": "a", "file_id": "file-a", "chunk_index": 0, "content": "住宿437元"}, {"id": "b", "file_id": "file-a", "chunk_index": 1, "content": "餐费73元"}, {"id": "c", "file_id": "file-b", "chunk_index": 0, "content": "范围外资料"}]
    store.save_chunk_vectors(items, [[1., 0., 0.], [0., 1., 0.], [1., 0., 0.]])
    result = store.search_chunk_vectors([1., 0., 0.], ["file-a"], 2)
    assert result["ids"] == [["a", "b"]], result
    assert abs(result["distances"][0][0]) < 0.0001, result
    assert result["metadatas"][0][0]["file_id"] == "file-a"
    items[0]["content"] = "更新后的住宿标准"
    store.save_chunk_vectors([items[0]], [[1., 0., 0.]])
    assert store.get_collection().count() == 3
else:
    result = store.search_chunk_vectors([1., 0., 0.], ["file-a"], 1)
    assert result["documents"] == [["更新后的住宿标准"]], result
    original_name = store.get_collection().name
    store.settings.embedding_model = "another-model"
    store.get_collection.cache_clear()
    assert store.get_collection().name != original_name
    assert store.get_collection().count() == 0
    store.settings.embedding_model = "test-model"
    store.get_collection.cache_clear()
    assert store.get_collection().count() == 3
    store.delete_file_vectors("file-a")
    assert store.get_collection().count() == 1
    assert store.search_chunk_vectors([1., 0., 0.], ["file-a"], 1)["ids"] == [[]]
    assert store.search_chunk_vectors([1., 0., 0.], ["file-b"], 1)["ids"] == [["c"]]
    store.settings.embedding_model = "another-model"
    store.get_collection.cache_clear()
    store.save_chunk_vectors([{"id": "d", "file_id": "file-b", "content": "另一模型的资料", "chunk_index": 0}], [[1., 0., 0.]])
    store.delete_all_file_vectors("file-b")
    assert store._get_client().list_collections() == []
    import time
    for attempt in range(50):
        if not any(path.is_dir() for path in store.VECTOR_DIR.iterdir()):
            break
        time.sleep(0.1)
    assert not any(path.is_dir() for path in store.VECTOR_DIR.iterdir()), list(store.VECTOR_DIR.iterdir())
    # 删除缓存集合后再次上传必须成功，不能拿着已删除的集合对象写入。
    store.save_chunk_vectors([{"id": "new", "file_id": "new-file", "content": "新资料", "chunk_index": 0}], [[1., 0., 0.]])
    assert store.search_chunk_vectors([1., 0., 0.], ["new-file"], 1)["ids"] == [["new"]]
    store.delete_all_file_vectors("new-file")
    assert store._get_client().list_collections() == []
print("临时向量库验收通过")
'''
        with tempfile.TemporaryDirectory() as directory:
            for stage in ["write", "read"]:
                result = subprocess.run([sys.executable, "-X", "utf8", "-c", code, directory, stage], cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, encoding="utf-8", timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("临时向量库验收通过", result.stdout)
            self.assertTrue((Path(directory) / "vector_db" / "chroma.sqlite3").is_file())
