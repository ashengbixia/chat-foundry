import os
from functools import lru_cache
from threading import Lock
from cryptography.fernet import Fernet, InvalidToken
from backend.app.core.config import settings
from backend.app.core.database import DATA_DIR

KEY_FILE = DATA_DIR / "secrets" / "model-settings.key"  # 与数据库分开保存，随backend/data一起排除出Git
_key_lock = Lock()

@lru_cache(maxsize=1)
def get_cipher() -> Fernet:  # 同一进程复用主密钥；修改环境变量后需重启后端
    with _key_lock:
        configured = settings.model_config_encryption_key.get_secret_value().strip()
        if configured:
            key = configured.encode()
        else:
            KEY_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                descriptor = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                key = KEY_FILE.read_bytes().strip()
            else:
                key = Fernet.generate_key()
                with os.fdopen(descriptor, "wb") as target:
                    target.write(key)
                    target.flush()
                    os.fsync(target.fileno())
        try:
            return Fernet(key)
        except (ValueError, TypeError):
            raise ValueError("模型配置主密钥无效，请检查服务器环境配置或私密文件") from None

def encrypt_api_key(value: str) -> str:
    return get_cipher().encrypt(value.encode()).decode()

def decrypt_api_key(value: str) -> str:
    try:
        return get_cipher().decrypt(value.encode()).decode()
    except (InvalidToken, UnicodeError):
        raise ValueError("无法解密已保存的模型Key，请恢复原主密钥或重新填写个人Key") from None
