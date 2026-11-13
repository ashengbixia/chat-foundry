import unittest
from unittest.mock import AsyncMock, patch
from backend.app.chat.reference_context import prepare_auto_reference
from backend.app.chat.history_sources import read_history_sources
from backend.app.tools.local import read_webpage as webpage
from backend.app.tools.runtime import ToolContext


class ReadingPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_unrelated_history_is_not_automatically_injected_and_sources_share_budget(self):
        sources = {key: {'content': char * 4000, 'start_offset': 0, 'truncated': False}
                   for key, char in [('H1', '甲'), ('H2', '乙')]}
        self.assertEqual(await prepare_auto_reference(None, '帮我找Python学习网站', set(), [], [], None,
                                                     AsyncMock(), history_sources=sources), [])
        result = read_history_sources(['H1', 'H2'], sources)
        self.assertEqual([len(item['content']) for item in result['chunks']], [2500, 2500])
        self.assertEqual(result['pages'][1]['next_offset'], 2500)
        self.assertEqual(len(read_history_sources(['H2'], sources, offset=2500)['chunks'][0]['content']), 1500)

    async def test_duplicate_web_reads_and_failures_do_not_repeat_network(self):
        context = ToolContext()
        doc = {'url': 'https://example.com/', 'title': '文章', 'text': '正文' * 200, 'retrieved_at': 1}
        with patch.object(webpage, 'fetch_page', AsyncMock(return_value=doc)) as fetch:
            await webpage.read_webpage(context, doc['url'])
            repeated = await webpage.read_webpage(context, doc['url'], offset=150)
        self.assertEqual(repeated['status'], 'already_read')
        self.assertEqual(repeated['chunks'], [])
        fetch.assert_awaited_once()
        with patch.object(webpage, 'fetch_page', AsyncMock(side_effect=ValueError('连接失败'))) as fetch:
            for _ in range(2):
                with self.assertRaises(ValueError):
                    await webpage.read_webpage(context, 'https://other.example/')
        fetch.assert_awaited_once()

    async def test_article_navigation_and_shared_conversation_are_distinct(self):
        body = '<html><body><div class="sidebar">' + 'MENU\n' * 80 + '</div><article><h1>Python教程</h1><p>' + '学习Python可以从变量和函数开始。' * 15 + '</p></article></body></html>'
        text = webpage.extract_page(body.encode(), 'text/html', 'utf-8')[1]
        self.assertNotIn('MENU', text)
        self.assertIn('变量和函数', text)
        shared = '<div><div class="ds-message">问题一</div><div class="ds-message"><div class="ds-think-content">外站过程</div><div class="ds-assistant-message-main-content"><p>回答一</p></div></div><div class="ds-message">问题二</div><div class="ds-message"><div class="ds-assistant-message-main-content">回答二</div></div></div>'
        text = webpage.extract_page(shared.encode(), 'text/html', 'utf-8')[1]
        self.assertIn('回答二', text)
        self.assertIn('分享中的助手', text)
        self.assertNotIn('外站过程', text)
