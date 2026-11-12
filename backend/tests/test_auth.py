import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from backend.app.api.auth import router as auth_router
from backend.app.api.chats import router as chats_router
from backend.app.api.files import router as files_router
from backend.app.core import database
from backend.app.auth import COOKIE_NAME, PrivateResponseHeaders, _attempts
from backend.app.models.user import User, UserSession
from backend.app.models.chat import Chat
from backend.app.models.file import File
from backend.app.files import service
from backend.tests.index_fixture import mock_file_vectors

class AccountTests(unittest.IsolatedAsyncioTestCase):  # 用真实Cookie和临时库验证关键权限，不调用真实模型
    async def test_legacy_short_password_can_login_but_new_passwords_still_require_eight(self):
        from backend.app.auth import hash_password
        async with self.sessions() as session:
            session.add(User(id='legacy-admin', username='legacyadmin', name='管理员', role='admin', password_hash=hash_password('old123')))
            await session.commit()
        wrong = await self.admin.post('/api/auth/login', json={'username': 'legacyadmin', 'password': 'wrong'})
        self.assertEqual(wrong.status_code, 401)
        logged_in = await self.admin.post('/api/auth/login', json={'username': 'legacyadmin', 'password': 'old123'})
        self.assertEqual(logged_in.status_code, 200)
        self.assertEqual(logged_in.json()['role'], 'admin')
        signup = await self.other.post('/api/auth/signup', json={'username': 'newuser', 'password': 'short'})
        self.assertEqual(signup.status_code, 422)
        changed = await self.admin.post('/api/auth/password', json={'current_password': 'old123', 'new_password': 'short'})
        self.assertEqual(changed.status_code, 422)

    async def asyncSetUp(self):
        _attempts.clear()
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{(self.root / 'test.db').as_posix()}")
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        await database.create_tables(self.engine)
        mock_file_vectors(self, self.sessions)
        current = patch.object(service, "UPLOAD_DIR", self.root / "uploads")
        current.start()
        self.addCleanup(current.stop)
        self.app = FastAPI()
        self.app.add_middleware(PrivateResponseHeaders)
        for router in [auth_router, chats_router, files_router]:
            self.app.include_router(router)
        async def get_db():
            async with self.sessions() as session:
                yield session
        self.app.dependency_overrides[database.get_db] = get_db
        self.admin = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://accounts")
        self.other = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://accounts")
        async with self.sessions() as session:
            session.add_all([Chat(id="legacy", title="历史会话"), File(id="legacy-file", filename="历史.txt", storage_path="legacy.txt", status="completed", text_content="历史原文")])
            await session.commit()

    async def asyncTearDown(self):
        await self.admin.aclose()
        await self.other.aclose()
        await self.engine.dispose()
        self.directory.cleanup()

    async def signup(self, client, first=False, request_admin=False):
        response = await client.post("/api/auth/signup", json={"username": "admin" if first else "testuser", "password": "test-password-123", "setup": first, "request_admin": request_admin, "role": "admin"})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def test_initial_admin_claims_data_and_login_survives_reload_then_logout_revokes(self):
        self.assertEqual((await self.other.get("/api/chats")).status_code, 401)
        account = await self.signup(self.admin, first=True)
        self.assertEqual(account["role"], "admin")
        self.assertEqual(account["name"], account["username"])
        self.assertNotIn("password_hash", account)
        self.assertEqual((await self.admin.get("/api/auth/me")).json()["id"], account["id"])
        self.assertEqual((await self.admin.get("/api/chats")).headers["cache-control"], "no-store")
        self.assertEqual((await self.admin.get("/api/chats")).json()[0]["id"], "legacy")
        self.assertEqual((await self.admin.get("/api/files")).json()[0]["id"], "legacy-file")
        async with self.sessions() as session:
            user = await session.get(User, account["id"])
            self.assertTrue(user.password_hash.startswith("scrypt$"))
            self.assertNotIn("test-password-123", user.password_hash)
        stale = self.admin.cookies.get(COOKIE_NAME)
        self.assertEqual((await self.admin.post("/api/auth/logout")).status_code, 204)
        self.admin.cookies.set(COOKIE_NAME, stale)
        self.assertEqual((await self.admin.get("/api/auth/me")).status_code, 401)
        self.admin.cookies.clear()
        response = await self.admin.post("/api/auth/login", json={"username": " ADMIN ", "password": "test-password-123"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("HttpOnly", response.headers["set-cookie"])

    async def test_admin_application_requires_approval_and_suspension_revokes_sessions(self):
        admin = await self.signup(self.admin, first=True)
        user = await self.signup(self.other, request_admin=True)
        self.assertEqual(user["role"], "user")
        self.assertTrue(user["admin_requested"])
        self.assertEqual((await self.other.get("/api/auth/users")).status_code, 403)
        response = await self.admin.patch(f'/api/auth/users/{user["id"]}', json={"role": "admin"})
        self.assertEqual(response.json()["role"], "admin")
        self.assertEqual((await self.other.get("/api/auth/users")).status_code, 200)
        self.assertEqual((await self.admin.patch(f'/api/auth/users/{admin["id"]}', json={"role": "disabled"})).status_code, 400)
        self.assertEqual((await self.admin.patch(f'/api/auth/users/{user["id"]}', json={"role": "disabled"})).status_code, 200)
        self.assertEqual((await self.other.get("/api/auth/me")).status_code, 401)

    async def test_private_chat_files_attachments_and_all_id_routes_are_isolated(self):
        await self.signup(self.admin, first=True)
        await self.signup(self.other)
        self.assertEqual((await self.other.get("/api/chats")).json(), [])
        self.assertEqual((await self.other.get("/api/files")).json(), [])
        for method, path in [("GET", "/api/chats/legacy"), ("GET", "/api/chats/legacy/messages"), ("DELETE", "/api/chats/legacy"), ("PATCH", "/api/chats/legacy"), ("POST", "/api/chats/legacy/respond"), ("GET", "/api/files/legacy-file"), ("GET", "/api/files/legacy-file/content"), ("GET", "/api/files/legacy-file/chunks"), ("DELETE", "/api/files/legacy-file"), ("POST", "/api/files/legacy-file/index"), ("POST", "/api/files/legacy-file/chunks")]:
            self.assertEqual((await self.other.request(method, path, json={"title": "越权"} if method == "PATCH" else None)).status_code, 404, path)
        chat = (await self.other.post("/api/chats", json={"title": "自己的会话"})).json()
        response = await self.other.post(f'/api/chats/{chat["id"]}/messages', json={"content": "问题", "file_ids": ["legacy-file"]})
        self.assertEqual(response.status_code, 400)
        self.assertEqual((await self.admin.get(f'/api/chats/{chat["id"]}')).status_code, 404)
        upload = await self.other.post("/api/files", files={"file": ("资料.txt", b"private text", "text/plain")})
        self.assertEqual(upload.status_code, 201, upload.text)
        self.assertEqual([item["id"] for item in (await self.admin.get("/api/files")).json()], ["legacy-file"])
        self.assertEqual((await self.other.post("/api/chats", json={"title": "跨站"}, headers={"Origin": "http://evil.example"})).status_code, 403)

    async def test_password_change_and_expiry_invalidate_old_credentials(self):
        await self.signup(self.admin, first=True)
        old = self.admin.cookies.get(COOKIE_NAME)
        response = await self.admin.post("/api/auth/password", json={"current_password": "test-password-123", "new_password": "new-password-456"})
        self.assertEqual(response.status_code, 204, response.text)
        self.other.cookies.set(COOKIE_NAME, old)
        self.assertEqual((await self.other.get("/api/auth/me")).status_code, 401)
        self.assertEqual((await self.admin.get("/api/auth/me")).status_code, 200)
        async with self.sessions() as session:
            await session.execute(update(UserSession).values(expires_at=int(time.time()) - 1))
            await session.commit()
        self.assertEqual((await self.admin.get("/api/auth/me")).status_code, 401)
