import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import anyio
from backend.app.files.extract import ExtractionError, extract_document

_slots = asyncio.Semaphore(1)


async def parse_uploaded_document(path: Path, content: bytes, extension: str) -> str:
    if extension == '.txt':
        return extract_document(content, extension)
    async with _slots:
        required_environment = {'SYSTEMROOT', 'WINDIR', 'PATH', 'PATHEXT', 'TEMP', 'TMP', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'LANG', 'LC_ALL', 'LD_LIBRARY_PATH'}
        worker_environment = {key: value for key, value in os.environ.items() if key.upper() in required_environment}
        worker_environment.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1')
        # Windows下Uvicorn热重载可使用Selector事件循环，不支持asyncio子进程。
        # 用线程等待原生Popen，同时保留超时、取消清理与隐藏窗口。
        process = await anyio.to_thread.run_sync(lambda: subprocess.Popen(
            [sys.executable, '-m', 'backend.app.files.extract_worker', str(path)],
            cwd=str(Path(__file__).resolve().parents[3]), env=worker_environment,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            **({'creationflags': 0x08000000} if os.name == 'nt' else {})))
        try:
            try:
                output, _ = await anyio.to_thread.run_sync(lambda: process.communicate(timeout=30), abandon_on_cancel=True)
            except subprocess.TimeoutExpired:
                raise ExtractionError('文档解析超过30秒，请拆分或转换后上传') from None
            if process.returncode:
                raise ExtractionError('文档解析进程未完成，请检查文件或缩小文档范围')
            result = json.loads(output)
            if result.get('error'):
                raise ExtractionError(result['error'])
            return result['text']
        finally:
            if process.poll() is None:
                with anyio.CancelScope(shield=True):
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await anyio.to_thread.run_sync(process.wait)
