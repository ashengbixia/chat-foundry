"""模型出口与实际HTTP请求并发控制。主任务等待子任务时不占推理槽。"""
import asyncio
import ipaddress
import socket
from contextlib import asynccontextmanager

import httpx
from backend.app.core.config import settings

_active: dict[str, int] = {}


class ModelAccessError(httpx.ConnectError):
    """只含可公开的固定策略说明，不包含目标IP、Key或供应商响应。"""


def approved_internal(base_url: str) -> bool:
    return base_url.rstrip('/') in {url.rstrip('/') for url in settings.model_internal_urls}


async def resolve_public(host: str, port: int) -> str:
    try:
        answers = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM), 10)
        addresses = [entry[4][0] for entry in answers]
        if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
            raise ValueError()
        return addresses[0]
    except (ValueError, OSError, asyncio.TimeoutError):
        raise ModelAccessError('模型地址未获授权或未解析到公网；本地模型需由部署管理员加入 MODEL_INTERNAL_URLS。') from None


class ModelTransport(httpx.AsyncHTTPTransport):
    def __init__(self, base_url: str):
        super().__init__(retries=0)
        self.base_url = base_url

    async def handle_async_request(self, request):
        base = httpx.URL(self.base_url)
        # 请求必须保持在选择的服务；不接受凭据、不同主机、协议或端口。
        if request.url.scheme not in ('http', 'https') or request.url.userinfo or (request.url.scheme, request.url.host, request.url.port) != (base.scheme, base.host, base.port):
            raise ModelAccessError('模型请求地址不合法')
        if not approved_internal(self.base_url):
            if request.url.scheme != 'https' or request.url.port not in (None, 443):
                raise ModelAccessError('生产环境的公网模型服务必须使用标准 HTTPS；其他地址需由管理员授权。')
            address = await resolve_public(request.url.host, request.url.port or 443)
            # 连接已验证的IP，同时保留原Host和TLS SNI；避免校验后再次DNS解析。
            headers = request.headers.copy()
            headers['Host'] = request.url.netloc.decode('ascii')
            extensions = {**request.extensions, 'sni_hostname': request.url.host}
            request = httpx.Request(request.method, request.url.copy_with(host=address), headers=headers, stream=request.stream, extensions=extensions)
        return await super().handle_async_request(request)


@asynccontextmanager
async def model_client(connection, **kwargs):
    user = connection.user_id or '__system__'
    loop = asyncio.get_running_loop()
    deadline = loop.time() + settings.model_queue_timeout
    # 同一事件循环内检查和增加之间无await，两个配额原子获取，避免占着全局槽等待个人槽。
    while sum(_active.values()) >= settings.model_global_concurrency or _active.get(user, 0) >= settings.model_user_concurrency:
        if loop.time() >= deadline:
            raise httpx.PoolTimeout('模型服务繁忙，请稍后重试')
        await asyncio.sleep(0.05)
    _active[user] = _active.get(user, 0) + 1
    try:
        kwargs['follow_redirects'] = False
        kwargs['trust_env'] = False
        if settings.app_env == 'production':
            kwargs['transport'] = ModelTransport(connection.base_url)
        async with httpx.AsyncClient(**kwargs) as client:
            yield client
    finally:
        _active[user] -= 1
        if not _active[user]:
            del _active[user]
