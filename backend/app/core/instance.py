"""跨平台进程锁；生产运行与离线维护共用，锁文件不删除以避免 inode 竞争。"""
import os
from pathlib import Path


class InstanceLock:
    def __init__(self, root: Path):
        self.root = root
        self.handle = None

    def acquire(self):
        self.root.mkdir(parents=True, exist_ok=True)
        handle = (self.root / '.instance.lock').open('a+b')
        try:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError('数据目录正在使用；只支持一个后端进程，维护前请停止后端。') from None
        self.handle = handle
        return self

    def release(self):
        if self.handle is not None:
            if os.name == 'nt':
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            self.handle.close()
            self.handle = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()
