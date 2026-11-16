"""在受内存限制的独立进程中解析文档，退出后归还解析器内存。"""
import ctypes
import json
import logging
import os
from pathlib import Path
import sys

_job = None


def limit_memory():
    global _job
    if os.name != 'nt':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
        return
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64), ('flags', wintypes.DWORD),
                    ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t), ('active', wintypes.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]
    class Counters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
    class Limits(ctypes.Structure):
        _fields_ = [('basic', Basic), ('io', Counters), ('process_memory', ctypes.c_size_t),
                    ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes, kernel.CreateJobObjectW.restype = [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    limits = Limits()
    limits.basic.flags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    limits.process_memory = 512 * 1024 * 1024
    _job = kernel.CreateJobObjectW(None, None)
    if not _job or not kernel.SetInformationJobObject(_job, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(_job, kernel.GetCurrentProcess()):
        raise RuntimeError('无法启用文档解析进程的内存限制')


def main():
    from backend.app.files.extract import ExtractionError, extract_document
    logging.disable(logging.CRITICAL)
    try:
        limit_memory()
        path = Path(sys.argv[1])
        if path.stat().st_size > 5 * 1024 * 1024:
            raise ExtractionError('文件大小不能超过5MB')
        result = {'text': extract_document(path.read_bytes(), path.suffix.lower())}
    except ExtractionError as error:
        result = {'error': str(error)}
    except Exception:
        result = {'error': '文档解析失败：文件可能损坏、格式不匹配或超出解析资源限制，请转换或拆分后上传'}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
