import unittest
from unittest.mock import patch
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from backend.app.core.config import Settings
from backend.app.api import site_info


class SiteInfoTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_config_is_explicit_and_does_not_expose_secrets(self):
        app = FastAPI()
        app.include_router(site_info.router)
        config = Settings(_env_file=None, site_name=' 示例工作空间 ', icp_record='示例ICP备案号',
                          gongan_record='示例公安备案号', gongan_record_code='00000000000000000000',
                          bootstrap_token='not-public', DEEPSEEK_API_KEY='not-public')
        with patch.object(site_info, 'settings', config):
            async with AsyncClient(transport=ASGITransport(app=app), base_url='http://site') as client:
                response = await client.get('/api/site-info')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(set(data), {'site_name', 'icp_record', 'icp_record_url', 'gongan_record', 'gongan_record_url'})
        self.assertEqual(data['site_name'], '示例工作空间')
        self.assertEqual(data['icp_record_url'], 'https://beian.miit.gov.cn/')
        self.assertEqual(data['gongan_record_url'], 'https://beian.mps.gov.cn/#/query/webSearch?code=00000000000000000000')
        self.assertNotIn('not-public', response.text)

    async def test_defaults_and_unconfigured_records(self):
        config = Settings(_env_file=None, site_name=' ', icp_record=' ', gongan_record=' ')
        with patch.object(site_info, 'settings', config):
            data = site_info.get_site_info()
        self.assertEqual(data.site_name, 'ChatFoundry')
        self.assertEqual((data.icp_record, data.icp_record_url, data.gongan_record, data.gongan_record_url), ('', '', '', ''))
        for code in ['javascript:alert(1)', '123', '1' * 21]:
            with self.assertRaises(ValidationError):
                Settings(_env_file=None, gongan_record_code=code)
