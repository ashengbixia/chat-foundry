import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import httpx
from pydantic import SecretStr
from backend.app.api import model_settings as api
from backend.app.core import model_secrets
from backend.app.llm import client as llm
from backend.app.models.model_setting import UserModelSetting
from backend.tests import test_auth as account_tests

class ModelSettingsTests(unittest.IsolatedAsyncioTestCase):  # 只验证密钥、隔离和真实聊天接线，不请求外部模型
    async def asyncSetUp(self):
        await account_tests.AccountTests.asyncSetUp(self)
        self.app.include_router(api.router)
        self.admin_user = await account_tests.AccountTests.signup(self, self.admin, first=True)
        self.other_user = await account_tests.AccountTests.signup(self, self.other)
        model_secrets.get_cipher.cache_clear()
        for current in [patch.object(model_secrets, "KEY_FILE", self.root / "private" / "model.key"), patch.object(model_secrets, "settings", SimpleNamespace(model_config_encryption_key=SecretStr(""))), patch.object(api, "settings", SimpleNamespace(llm_base_url="https://default.test/v1", llm_model="default-model", llm_timeout=60, llm_api_key=SecretStr("shared-server-key")))]:
            current.start()
            self.addCleanup(current.stop)
        self.url = "/api/settings/model-service"
        self.draft = {"provider": "custom", "base_url": "https://personal.test/v1", "model": "my-model", "timeout": 30, "key_source": "personal", "api_key": "personal-test-key"}

    async def asyncTearDown(self):
        model_secrets.get_cipher.cache_clear()
        await account_tests.AccountTests.asyncTearDown(self)

    async def test_key_encrypted_and_never_returned_reused_on_same_address_and_reset(self):
        response = await self.admin.put(self.url, json=self.draft)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(self.draft["api_key"], response.text)
        async with self.sessions() as session:
            row = await session.get(UserModelSetting, self.admin_user["id"])
            encrypted_before = row.encrypted_api_key
            self.assertNotIn(self.draft["api_key"], row.encrypted_api_key)
            model_secrets.get_cipher.cache_clear()  # 模拟重启后重新从独立主密钥文件读取
            self.assertEqual(model_secrets.decrypt_api_key(row.encrypted_api_key), self.draft["api_key"])
        response = await self.admin.patch(self.url + "/model", json={"model": "quick-model", "base_url": self.draft["base_url"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["config"]["model"], "quick-model")
        async with self.sessions() as session:
            self.assertEqual((await session.get(UserModelSetting, self.admin_user["id"])).encrypted_api_key, encrypted_before)
        self.assertEqual((await self.admin.patch(self.url + "/model", json={"model": "wrong", "base_url": "https://other.test/v1"})).status_code, 409)
        self.assertTrue((await self.other.get(self.url)).json()["inherited"])
        unchanged = {**self.draft, "api_key": None, "model": "another-model"}
        self.assertEqual((await self.admin.put(self.url, json=unchanged)).status_code, 200)
        self.assertEqual((await self.admin.put(self.url, json={**unchanged, "base_url": "https://changed.test/v1"})).status_code, 400)
        self.assertEqual((await self.admin.delete(self.url)).status_code, 204)
        self.assertTrue((await self.admin.get(self.url)).json()["inherited"])

    async def test_shared_key_cannot_be_sent_to_custom_address_or_overwrite_other_user(self):
        response = await self.other.put(self.url, json={**self.draft, "provider": "server", "key_source": "environment"})
        self.assertEqual(response.status_code, 400)
        response = await self.other.put(self.url, json={**self.draft, "user_id": self.admin_user["id"]})
        self.assertEqual(response.status_code, 422)
        self.assertTrue((await self.admin.get(self.url)).json()["inherited"])

    async def test_thinking_only_at_test_limit_is_reported_separately_from_text_or_invalid_response(self):
        draft = {**self.draft, "key_source": "none", "api_key": None}
        cases = [({"content": "", "reasoning": "internal reasoning"}, "length", 200, "limited"), ({"content": None, "reasoning_content": "internal reasoning"}, "length", 200, "limited"), ({"content": "OK"}, "stop", 200, "ok"), ({"content": ""}, "stop", 400, None)]
        for message, reason, expected, status in cases:
            with self.subTest(status=status, reason=reason), patch.object(api, "request_provider", AsyncMock(return_value={"choices": [{"message": message, "finish_reason": reason}]})):
                response = await self.admin.post(self.url + "/test", json=draft)
                self.assertEqual(response.status_code, expected, response.text)
                if status:
                    self.assertEqual(response.json()["status"], status)

    async def test_two_users_chat_with_own_models_and_keys_and_draft_checks_do_not_save(self):
        first = await self.admin.put(self.url, json=self.draft)
        self.assertEqual(first.status_code, 200, first.text)
        second = {**self.draft, "base_url": "https://second.test/v1", "model": "second-model", "api_key": "second-test-key"}
        self.assertEqual((await self.other.put(self.url, json=second)).status_code, 200)
        requests = []
        def handler(request):
            requests.append((str(request.url), request.headers.get("Authorization"), json.loads(request.content) if request.content else None))
            if request.method == "GET":
                return httpx.Response(200, json={"data": [{"id": "listed-model"}]})
            payload = json.loads(request.content)
            choice = {"message": {"content": "OK"}, "finish_reason": "stop"}
            if payload.get("stream"):
                data = json.dumps({"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}]})
                return httpx.Response(200, text=f"data: {data}\n\ndata: [DONE]\n\n")
            return httpx.Response(200, json={"choices": [choice]})
        real_client = httpx.AsyncClient
        with patch.object(llm.httpx, "AsyncClient", side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)):
            check = await self.admin.post(self.url + "/models", json={**self.draft, "api_key": None})
            self.assertEqual(check.json(), {"models": ["listed-model"]})
            check = await self.admin.post(self.url + "/test", json={**self.draft, "model": "unsaved-model", "api_key": None})
            self.assertEqual(check.status_code, 200, check.text)
            self.assertEqual((await self.admin.get(self.url)).json()["config"]["model"], "my-model")
            async def chat(client):
                record = (await client.post("/api/chats", json={"title": "配置验证"})).json()
                await client.post(f'/api/chats/{record["id"]}/messages', json={"content": "测试"})
                response = await client.post(f'/api/chats/{record["id"]}/respond')
                self.assertIn("event: done", response.text)
                messages = (await client.get(f'/api/chats/{record["id"]}/messages')).json()["items"]
                return messages[-1]["model"]
            self.assertEqual(await asyncio.gather(chat(self.admin), chat(self.other)), ["my-model", "second-model"])
        self.assertTrue(any(url == "https://personal.test/v1/chat/completions" and key == "Bearer personal-test-key" and body["model"] == "my-model" for url, key, body in requests))
        self.assertTrue(any(url == "https://second.test/v1/chat/completions" and key == "Bearer second-test-key" and body["model"] == "second-model" for url, key, body in requests))
