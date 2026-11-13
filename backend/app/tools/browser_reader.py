"""隔离的动态网页读取；浏览器网络全部由固定公网IP的HTTP客户端转发。"""
import asyncio
import time
import anyio
import httpx
from backend.app.core.config import settings
from backend.app.tools.local.read_webpage import bounded_body, clean_text, normalize_url, resolve_public_ip, extract_page, poor_body, page_links, navigation_document

# 专用线程运行Proactor事件循环，兼容Windows uvicorn --reload的Selector循环。
_slots = anyio.CapacityLimiter(2)
MAX_RESOURCE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024


async def proxy_request(route, client, budget):
    request = route.request
    try:
        if request.resource_type in {'image', 'media', 'font'} or request.method not in {'GET', 'HEAD', 'POST', 'OPTIONS'}:
            await route.abort()
            return
        if request.resource_type == 'document':
            if request.frame.parent_frame is not None:
                await route.abort()
                return
            budget['navigations'] = budget.get('navigations', 0) + 1
            if budget['navigations'] > 6:
                raise ValueError('浏览器页面跳转过多')
        budget['requests'] += 1
        if budget['requests'] > 100:
            raise ValueError('浏览器页面请求过多')
        if budget['bytes'] >= MAX_TOTAL_BYTES:
            raise ValueError('浏览器页面总下载量过大')
        url = normalize_url(request.url)
        ip = await resolve_public_ip(url)
        payload = request.post_data_buffer
        if payload and len(payload) > 65536:
            raise ValueError('浏览器请求内容过大')
        headers = {key: value for key, value in request.headers.items()
                   if key.lower() in {'accept', 'accept-language', 'content-type', 'origin', 'referer', 'user-agent'}}
        headers.update({'Host': url.netloc.decode('ascii'), 'Accept-Encoding': 'identity'})
        async with client.stream(request.method, url.copy_with(host=ip), headers=headers,
                                 content=payload, extensions={'sni_hostname': url.host}) as response:
            body = await bounded_body(response, max_bytes=MAX_RESOURCE_BYTES)
            budget['bytes'] += len(body)
            if budget['bytes'] > MAX_TOTAL_BYTES:
                raise ValueError('浏览器页面总下载量过大')
            # 不转发Cookie/认证挑战，不让浏览器继承服务器或其他用户身份。
            forwarded = {key: value for key, value in response.headers.items()
                         if key.lower() not in {'set-cookie', 'www-authenticate', 'proxy-authenticate',
                                                'content-encoding', 'content-length', 'transfer-encoding',
                                                'connection', 'content-security-policy'}}
            policy = response.headers.get('content-security-policy', '')
            forwarded['content-security-policy'] = (policy + ', ' if policy else '') + "worker-src 'none'"
            if request.is_navigation_request() and request.frame.parent_frame is None and response.status_code >= 400:
                budget['navigation_error'] = f'网页返回HTTP {response.status_code}'
            await route.fulfill(status=response.status_code, headers=forwarded, body=body)
    except (ValueError, httpx.HTTPError) as error:
        if request.is_navigation_request() and request.frame.parent_frame is None:
            budget['navigation_error'] = str(error)
        await route.abort()


async def _render(url):
    try:
        from playwright.async_api import async_playwright, Error
    except ImportError:
        raise ValueError('该网页需要浏览器加载；请安装后端依赖，并执行 python -m playwright install chromium') from None
    async with async_playwright() as driver:
        browser = None
        try:
            browser = await driver.chromium.launch(headless=True, args=[
                '--disable-background-networking', '--disable-quic',
                '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
                '--host-resolver-rules=MAP * ~NOTFOUND',
            ])
            context = await browser.new_context(service_workers='block', accept_downloads=False)
            await context.route_web_socket('**/*', lambda socket: socket.close())
            budget = {'requests': 0, 'bytes': 0}
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False,
                                         timeout=httpx.Timeout(10, connect=5)) as client:
                network_slots = asyncio.Semaphore(4)
                async def forward(route):
                    async with network_slots:
                        await proxy_request(route, client, budget)
                await context.route('**/*', forward)
                page = await context.new_page()
                await page.goto(url, wait_until='domcontentloaded', timeout=20000)
                previous, stable = '', 0
                # 不等待networkidle：有些站点会持续轮询。正文稳定后结束，最多等待12秒。
                for _ in range(24):
                    sample = await page.evaluate("""() => {
                        const root = document.querySelector('article, main, [role="main"]') || document.body;
                        if (!root) return {text: '', password: false};
                        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
                        const parts = []; let node, count = 0, size = 0;
                        while ((node = walker.nextNode()) && ++count < 50000 && size < 1048576) {
                            const parent = node.parentElement;
                            if (!parent || parent.closest('script,style,noscript,nav,header,footer,aside,form,[hidden],[aria-hidden="true"]')) continue;
                            if (!parent.getClientRects().length || getComputedStyle(parent).visibility === 'hidden') continue;
                            parts.push(node.textContent); size += node.textContent.length;
                        }
                        const text = parts.join('\\n');
                        return {text, tooLarge: count >= 50000 || size >= 1048576, password: !!document.querySelector('input[type=password]')};
                    }""")
                    if sample['tooLarge']:
                        raise ValueError('浏览器正文过大或结构过于复杂，请提供更具体的文章页面')
                    text = clean_text(sample['text'])
                    if text and text == previous:
                        stable += 1
                    else:
                        stable = 0
                    previous = text
                    if len(text) >= 80 and not poor_body(text) and stable >= 3:
                        break
                    await asyncio.sleep(0.5)
                if budget.get('navigation_error'):
                    raise ValueError(budget['navigation_error'])
                if sample['password']:
                    raise ValueError('该页面需要登录，浏览器读取不会使用登录凭据')
                # 从渲染后的DOM中移除CSS不可见节点，再使用与静态页面相同的正文提取器。
                rendered = await page.evaluate("""() => {
                    const original = document.body;
                    const clone = original.cloneNode(true);
                    const nodes = original.querySelectorAll('*'), copies = clone.querySelectorAll('*');
                    for (let i = 0; i < nodes.length && i < 50000; i++) {
                        if (!nodes[i].getClientRects().length || getComputedStyle(nodes[i]).visibility === 'hidden') copies[i].remove();
                    }
                    return clone.outerHTML.slice(0, 8388608);
                }""")
                _, extracted = extract_page(rendered.encode('utf-8'), 'text/html', 'utf-8')
                final_url = normalize_url(page.url)
                await resolve_public_ip(final_url)
                links = page_links(rendered, str(final_url))
                if poor_body(extracted) or (len(extracted) < 80 and len(links) >= 5):
                    if links:
                        return navigation_document(str(final_url), await page.title(), links, '浏览器加载后正文仍不足。')
                    raise ValueError('页面仅取得菜单、加载提示或无效正文，无法据此确认内容')
                return {'url': str(final_url), 'title': (await page.title())[:200] or final_url.host,
                        'text': extracted, 'links': links, 'retrieved_at': int(time.time())}
        except Error:
            raise ValueError('浏览器读取失败：请确认已执行 python -m playwright install chromium；页面也可能加载超时或拒绝访问') from None
        finally:
            if browser is not None:
                await browser.close()


def _run(url):
    async def bounded():
        try:
            return await asyncio.wait_for(_render(url), timeout=40)
        except asyncio.TimeoutError:
            raise ValueError('浏览器读取超时，请稍后重试或提供公开文本页面') from None
    return asyncio.run(bounded())


async def render_page(url):
    from backend.app.tools.local.read_webpage import loader_engine
    if loader_engine() == 'safe_web':
        raise ValueError('浏览器读取未开启，请配置WEB_LOADER_ENGINE=playwright或auto；兼容旧配置WEB_BROWSER_ENABLED')
    return await anyio.to_thread.run_sync(_run, str(normalize_url(url)), limiter=_slots)
