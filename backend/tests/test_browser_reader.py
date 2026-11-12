import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx
from backend.app.tools import browser_reader
from backend.app.tools.local import read_webpage as webpage


class BrowserReaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_proxy_blocks_private_targets_and_pins_public_requests(self):
        request = SimpleNamespace(resource_type='fetch', method='GET', url='http://127.0.0.1/private',
                                  post_data_buffer=None, headers={}, is_navigation_request=lambda: False)
        route = SimpleNamespace(request=request, abort=AsyncMock(), fulfill=AsyncMock())
        requests = []
        class EmptyStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b''
        def handler(req):
            requests.append(req)
            return httpx.Response(302, headers={'location': 'http://127.0.0.1/private', 'set-cookie': 'secret=x'}, stream=EmptyStream())
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            budget = {'requests': 0, 'bytes': 0}
            with patch.object(browser_reader, 'resolve_public_ip', AsyncMock(side_effect=ValueError('不允许内网'))):
                await browser_reader.proxy_request(route, client, budget)
            route.abort.assert_awaited_once()
            self.assertFalse(requests)
            request.url = 'https://example.com/script.js'
            request.headers = {'Cookie': 'private=x', 'Authorization': 'secret', 'accept': '*/*'}
            with patch.object(browser_reader, 'resolve_public_ip', AsyncMock(return_value='93.184.216.34')):
                await browser_reader.proxy_request(route, client, budget)
        self.assertEqual(requests[0].url.host, '93.184.216.34')
        self.assertEqual(requests[0].extensions['sni_hostname'], 'example.com')
        self.assertEqual(requests[0].headers['host'], 'example.com')
        self.assertNotIn('cookie', requests[0].headers)
        self.assertNotIn('authorization', requests[0].headers)
        forwarded = route.fulfill.await_args.kwargs
        self.assertEqual(forwarded['headers']['location'], 'http://127.0.0.1/private')
        self.assertNotIn('set-cookie', forwarded['headers'])

    async def test_static_shell_falls_back_but_static_article_does_not(self):
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b'<html><body><div id="root"></div></body></html>'
        client = httpx.AsyncClient
        def factory(**kwargs):
            return client(transport=httpx.MockTransport(lambda request: httpx.Response(
                200, headers={'content-type': 'text/html'}, stream=Stream())), **kwargs)
        document = {'url': 'https://example.com/', 'title': '动态文章', 'text': '加载后的正文', 'retrieved_at': 100}
        with patch.object(webpage, 'resolve_public_ip', AsyncMock(return_value='93.184.216.34')), \
             patch.object(webpage.httpx, 'AsyncClient', factory), \
             patch.object(browser_reader, 'render_page', AsyncMock(return_value=document)) as render:
            self.assertEqual(await webpage.fetch_page('https://example.com/'), document)
        render.assert_awaited_once_with('https://example.com/')

    async def test_disabled_browser_explains_configuration(self):
        with patch.object(browser_reader.settings, 'web_browser_enabled', False):
            with self.assertRaisesRegex(ValueError, 'WEB_BROWSER_ENABLED'):
                await browser_reader.render_page('https://example.com/')
