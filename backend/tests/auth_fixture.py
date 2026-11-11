from types import SimpleNamespace
from backend.app.auth import get_current_user
from backend.app.api.model_settings import get_user_model_connection

def allow_legacy_test_user(app):  # 旧业务用例只验证自己的行为；账号隔离由test_auth使用真实登录验证
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=None, role="admin")
    app.dependency_overrides[get_user_model_connection] = lambda: None
