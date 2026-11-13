"""原生部署入口：先加载独立配置，再导入应用；不使用开发环境的backend/.env。"""
import argparse
import os
from pathlib import Path
import sys


def load_environment(path: Path):
    from dotenv import load_dotenv
    path = path.resolve(strict=True)
    load_dotenv(path, override=True, interpolate=False)
    os.environ['CHATFOUNDRY_ENV_FILE'] = str(path)
    os.environ['APP_ENV'] = 'production'
    # DLL/BLAS线程数由服务器配置控制，避免4核机器上过量创建线程。
    os.environ.setdefault('OMP_NUM_THREADS', '1')
    os.environ.setdefault('MKL_NUM_THREADS', '1')
    os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', type=Path, required=True)
    actions = parser.add_subparsers(dest='action', required=True)
    serve = actions.add_parser('serve')
    serve.add_argument('--port', type=int, default=8000)
    actions.add_parser('check')
    actions.add_parser('upgrade')
    save = actions.add_parser('backup')
    save.add_argument('--output', type=Path, required=True)
    restore = actions.add_parser('restore')
    restore.add_argument('--archive', type=Path, required=True)
    restore.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    os.chdir(root)
    load_environment(args.env_file)
    from backend.app.core.config import settings
    if not settings.auth_cookie_secure:
        parser.error('原生生产部署要求 AUTH_COOKIE_SECURE=true，并通过HTTPS网关访问')
    if not settings.data_dir.is_absolute():
        parser.error('DATA_DIR必须使用独立于代码目录的绝对路径')
    if args.action == 'serve':
        import uvicorn
        uvicorn.run('backend.app.main:app', host='127.0.0.1', port=args.port,
                    workers=1, reload=False, proxy_headers=True,
                    forwarded_allow_ips='127.0.0.1', limit_concurrency=24,
                    backlog=64, timeout_keep_alive=5)
    elif args.action == 'check':
        print('配置检查通过：生产模式、Secure Cookie、本机后端、独立数据目录。未连接数据库。')
    elif args.action == 'upgrade':
        from backend.app.core.instance import InstanceLock
        from backend.app.core.db_setup import prepare_database
        with InstanceLock(settings.data_dir):
            result = prepare_database(upgrade=True)
        print('数据库检查完成：' + result['status'])
    elif args.action == 'backup':
        from backend.app.core.backup import backup
        backup(settings.data_dir, args.output)
        print('全量备份完成，请另行保管环境配置中的密钥。')
    elif args.action == 'restore':
        from backend.app.core.backup import restore
        restore(args.archive, args.destination)
        print('已恢复至新目录；验证后再修改DATA_DIR并重启服务。')


if __name__ == '__main__':
    main()
