import hashlib
import hmac
import secrets
import time
from collections import defaultdict
from threading import Lock
from urllib.parse import urlsplit
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import MutableHeaders
from backend.app.core.config import settings
from backend.app.core.database import get_db
from backend.app.models.user import User, UserSession
from backend.app.models.chat import Chat
from backend.app.models.file import File

COOKIE_NAME = "chatfoundry_session"  # HttpOnly Cookie，不把登录凭证放进localStorage
_attempts = defaultdict(list)  # 单进程限流，与当前单worker运行方式一致
_attempts_lock = Lock()

class PrivateResponseHeaders:  # 直接转发ASGI消息，不包装流式生成器，保留原有停止回答和断开连接处理
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def send_response(message):
            if scope["type"] == "http" and scope.get("path", "").startswith("/api/") and message["type"] == "http.response.start":
                MutableHeaders(scope=message)["Cache-Control"] = "no-store"
            await send(message)
        await self.app(scope, receive, send_response)

def check_origin(request: Request):  # 拒绝跨站写入；同源前端及本机命令行可以正常访问
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (origin and (urlsplit(origin).netloc != request.headers.get("host") or urlsplit(origin).scheme != request.url.scheme)):
        raise HTTPException(403, "不允许跨站操作")

def limit_auth_attempts(request: Request):  # 注册、登录和密码校验限流，不相信客户端提供的转发IP
    check_origin(request)
    key = (request.client.host if request.client else "local", request.url.path)
    now = time.monotonic()
    with _attempts_lock:
        if len(_attempts) > 10000:
            for old in list(_attempts):
                if not _attempts[old] or _attempts[old][-1] < now - 300:
                    del _attempts[old]
            if len(_attempts) > 10000:
                raise HTTPException(429, "请求过于频繁，请稍后重试")
        recent = [stamp for stamp in _attempts[key] if stamp > now - 300]
        if len(recent) >= 15:
            raise HTTPException(429, "尝试次数过多，请5分钟后重试")
        _attempts[key] = recent + [now]

def hash_password(password: str) -> str:  # 随机盐+scrypt，数据库不保存明文或可逆密码
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"

def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, salt, expected = stored.split("$")
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False

def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()

async def issue_session(session: AsyncSession, user: User, response: Response):  # 新登录和修改密码后都生成新凭证
    token = secrets.token_urlsafe(32)
    lifetime = settings.auth_session_days * 86400
    session.add(UserSession(token_hash=token_hash(token), user_id=user.id, expires_at=int(time.time()) + lifetime))
    response.set_cookie(COOKIE_NAME, token, max_age=lifetime, httponly=True, secure=settings.auth_cookie_secure, samesite="strict", path="/")

async def get_session_user(request: Request, session: AsyncSession = Depends(get_db)) -> User:  # 每次请求重新核对会话有效期和账号状态
    check_origin(request)
    token = request.cookies.get(COOKIE_NAME, "")
    user = None
    if token and len(token) <= 128:
        statement = select(User).join(UserSession, UserSession.user_id == User.id).where(UserSession.token_hash == token_hash(token), UserSession.expires_at > int(time.time()))
        user = (await session.execute(statement)).scalar_one_or_none()
    if user is None or user.role == "disabled":
        raise HTTPException(401, "登录已失效，请重新登录")
    return user

async def get_current_user(user: User = Depends(get_session_user)) -> User:
    return user

async def get_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user

async def require_chat_owner(chat_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):  # 管理员也不能直接读取别人的私人聊天
    exists = (await session.execute(select(Chat.id).where(Chat.id == chat_id, Chat.user_id == user.id))).scalar_one_or_none()
    if exists is None:
        raise HTTPException(404, "会话不存在")

async def require_file_owner(file_id: str, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    exists = (await session.execute(select(File.id).where(File.id == file_id, File.user_id == user.id))).scalar_one_or_none()
    if exists is None:
        raise HTTPException(404, "文件不存在")

async def check_chat_access(request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    if "chat_id" in request.path_params:
        await require_chat_owner(request.path_params["chat_id"], user, session)

async def check_file_access(request: Request, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    if "file_id" in request.path_params:
        await require_file_owner(request.path_params["file_id"], user, session)
