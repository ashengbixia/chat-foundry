import time
import hmac
import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Literal
from backend.app.auth import COOKIE_NAME, check_origin, get_admin, get_current_user, hash_password, issue_session, limit_auth_attempts, token_hash, verify_password
from backend.app.core.database import get_db
from backend.app.models.user import User, UserSession
from backend.app.models.chat import Chat
from backend.app.models.file import File
from backend.app.core.config import settings

router = APIRouter(prefix="/api/auth", tags=["账号管理"])

class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    name: str
    username: str
    role: str
    admin_requested: bool
    created_at: int

class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=254, pattern=r"^\S+$")  # 登录兼容旧账号的原登录名，不要求邮箱格式
    password: SecretStr = Field(min_length=1, max_length=128)  # 登录只验证已有密码，兼容历史短密码。

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

class Signup(Credentials):
    username: str = Field(min_length=1, max_length=80, pattern=r"^\S+$")  # 新用户名不可包含空白，不区分大小写
    password: SecretStr = Field(min_length=8, max_length=128)
    setup: bool = False  # 首次初始化单独确认，普通注册不能意外变成初始管理员
    request_admin: bool = False  # 只提交申请，不直接授予权限
    bootstrap_token: SecretStr = Field(default_factory=lambda: SecretStr(''), max_length=256)

class Profile(BaseModel):
    name: str = Field(min_length=1, max_length=80)

class PasswordChange(BaseModel):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=8, max_length=128)

class RoleChange(BaseModel):
    role: Literal["user", "admin", "disabled"]

@router.get("/config")
async def auth_config(session: AsyncSession = Depends(get_db)):  # 登录页只需要知道是否尚未初始化
    count = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    return {"setup_required": count == 0, "registration_enabled": settings.registration_enabled, "bootstrap_required": settings.app_env == 'production' and count == 0}

@router.post("/signup", response_model=UserResponse, status_code=201, dependencies=[Depends(limit_auth_attempts)])
async def signup(data: Signup, response: Response, session: AsyncSession = Depends(get_db)):
    password_hash = await anyio.to_thread.run_sync(hash_password, data.password.get_secret_value())
    await session.execute(text("BEGIN IMMEDIATE"))  # 串行核对第一个账号，避免并发注册产生多个初始管理员
    count = (await session.execute(select(func.count()).select_from(User))).scalar_one()
    if count and not settings.registration_enabled:
        raise HTTPException(403, '公开注册已关闭，请联系管理员创建账号')
    if not count and settings.app_env == 'production':
        expected = settings.bootstrap_token.get_secret_value()
        if not expected or not hmac.compare_digest(expected.encode(), data.bootstrap_token.get_secret_value().encode()):
            raise HTTPException(403, '请填写部署管理员设置的初始化口令')
    if (count == 0) != data.setup:
        raise HTTPException(409, "初始化状态已变化，请刷新页面后重试")
    if (await session.execute(select(User.id).where(User.username == data.username))).scalar_one_or_none():
        raise HTTPException(409, "该用户名已注册")
    user = User(username=data.username, name=data.username, password_hash=password_hash, role="admin" if count == 0 else "user", admin_requested=data.request_admin and count != 0)  # 显示名称默认使用用户名，之后可在账号面板修改
    session.add(user)
    await session.flush()
    if count == 0:
        await session.execute(update(Chat).where(Chat.user_id.is_(None)).values(user_id=user.id))
        await session.execute(update(File).where(File.user_id.is_(None)).values(user_id=user.id))
    await issue_session(session, user, response)
    await session.commit()
    return user

@router.post("/login", response_model=UserResponse, dependencies=[Depends(limit_auth_attempts)])
async def login(data: Credentials, response: Response, session: AsyncSession = Depends(get_db)):
    user = (await session.execute(select(User).where(User.username == data.username))).scalar_one_or_none()
    # 不存在的账号也执行密码计算，避免通过明显的耗时差异枚举账号。
    stored = user.password_hash if user else "scrypt$00000000000000000000000000000000$" + "0" * 64
    correct = await anyio.to_thread.run_sync(verify_password, data.password.get_secret_value(), stored)
    if not correct or user is None or user.role == "disabled":
        raise HTTPException(401, "用户名或密码错误，或账号已停用")
    await session.execute(delete(UserSession).where(UserSession.expires_at <= int(time.time())))
    await issue_session(session, user, response)
    await session.commit()
    return user

@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return user

@router.post("/logout", status_code=204, dependencies=[Depends(check_origin)])
async def logout(request: Request, session: AsyncSession = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME, "")
    if token:
        await session.execute(delete(UserSession).where(UserSession.token_hash == token_hash(token)))
        await session.commit()
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    return response

@router.patch("/profile", response_model=UserResponse)
async def change_profile(data: Profile, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    if not data.name.strip():
        raise HTTPException(422, "名称不能为空")
    user.name = data.name.strip()
    await session.commit()
    return user

@router.post("/password", status_code=204, dependencies=[Depends(limit_auth_attempts)])
async def change_password(data: PasswordChange, response: Response, user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    if not await anyio.to_thread.run_sync(verify_password, data.current_password.get_secret_value(), user.password_hash):
        raise HTTPException(400, "当前密码不正确")
    user.password_hash = await anyio.to_thread.run_sync(hash_password, data.new_password.get_secret_value())
    await session.execute(delete(UserSession).where(UserSession.user_id == user.id))  # 旧设备上的登录同时失效
    await issue_session(session, user, response)
    await session.commit()

@router.post("/request-admin", response_model=UserResponse)
async def request_admin(user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db)):
    if user.role == "user":
        user.admin_requested = True
        await session.commit()
    return user

@router.get("/users", response_model=list[UserResponse], dependencies=[Depends(get_admin)])
async def list_users(session: AsyncSession = Depends(get_db)):
    return (await session.execute(select(User).order_by(User.created_at, User.id))).scalars().all()

@router.post('/users', response_model=UserResponse, status_code=201)
async def create_user(data: Signup, admin: User = Depends(get_admin), session: AsyncSession = Depends(get_db)):
    password_hash = await anyio.to_thread.run_sync(hash_password, data.password.get_secret_value())
    locked = await session.execute(update(User).where(User.id == admin.id, User.role == 'admin').values(name=User.name))
    if not locked.rowcount:
        raise HTTPException(403, '管理员权限已变化')
    if (await session.execute(select(User.id).where(User.username == data.username))).scalar_one_or_none():
        raise HTTPException(409, '该用户名已注册')
    user = User(username=data.username, name=data.username, password_hash=password_hash, role='user', admin_requested=False)
    session.add(user)
    await session.commit()
    return user

@router.patch("/users/{user_id}", response_model=UserResponse)
async def set_role(user_id: str, data: RoleChange, admin: User = Depends(get_admin), session: AsyncSession = Depends(get_db)):
    locked = await session.execute(update(User).where(User.id == admin.id, User.role == "admin").values(role=User.role))  # 加锁后重新核对操作者，防止两个管理员同时互相降权
    if locked.rowcount == 0:
        raise HTTPException(403, "管理员权限已变化，请刷新后重试")
    if user_id == admin.id:
        raise HTTPException(400, "不能在这里修改自己的权限")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(404, "账号不存在")
    if data.role == "admin" and user.role != "admin" and not user.admin_requested:
        raise HTTPException(409, "该账号尚未申请管理员权限")
    user.role = data.role
    user.admin_requested = False
    if data.role == "disabled":
        await session.execute(delete(UserSession).where(UserSession.user_id == user.id))
    await session.commit()
    return user
