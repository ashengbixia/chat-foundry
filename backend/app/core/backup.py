"""离线全量备份/校验/恢复。恢复只写入尚不存在的新目录，不覆盖现有数据。"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from backend.app.core.instance import InstanceLock


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as source:
        while chunk := source.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def backup(root: Path, output: Path):
    root, output = root.resolve(), output.resolve()
    if root == output or root in output.parents:
        raise ValueError('备份文件必须保存到数据目录之外')
    if output.exists() or not (root / 'app.db').is_file():
        raise ValueError('备份目标已存在，或源目录缺少 app.db')
    output.parent.mkdir(parents=True, exist_ok=True)
    with InstanceLock(root), tempfile.TemporaryDirectory() as temporary:
        staged = Path(temporary)
        for path in root.rglob('*'):
            if path.is_symlink():
                raise ValueError('数据目录包含符号链接，拒绝备份')
            if not path.is_file() or path.name in ('.instance.lock', 'app.db', 'app.db-wal', 'app.db-shm'):
                continue
            target = staged / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        with closing(sqlite3.connect(str(root / 'app.db'))) as source, closing(sqlite3.connect(str(staged / 'app.db'))) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('数据库完整性检查失败')
        files = {path.relative_to(staged).as_posix(): digest(path) for path in staged.rglob('*') if path.is_file()}
        # x模式禁止覆盖，失败删除本次新建的不完整归档。
        created = False
        try:
            with output.open('xb') as handle:
                created = True
                if os.name != 'nt':
                    os.chmod(output, 0o600)
                with zipfile.ZipFile(handle, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr('manifest.json', json.dumps({'version': 1, 'files': files}))
                    for name in files:
                        archive.write(staged / name, name)
        except BaseException:
            if created:
                output.unlink(missing_ok=True)
            raise


def restore(archive_path: Path, destination: Path):
    destination = destination.resolve()
    if destination.exists():
        raise ValueError('恢复目录必须不存在；请使用新目录，验证后再切换 DATA_DIR')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary, zipfile.ZipFile(archive_path) as archive:
        staged = Path(temporary) / 'data'
        staged.mkdir(mode=0o700)
        manifest = json.loads(archive.read('manifest.json'))
        files = manifest['files']
        names = archive.namelist()
        if manifest.get('version') != 1 or len(names) != len(set(names)) or set(names) != set(files) | {'manifest.json'} or 'app.db' not in files:
            raise ValueError('备份清单不合法')
        for name, expected in files.items():
            relative = PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name or not relative.parts or relative.parts[0] in ('.instance.lock', 'manifest.json'):
                raise ValueError('备份包含不安全路径')
            target = staged.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with archive.open(name) as source, target.open('xb') as output:
                shutil.copyfileobj(source, output)
            if digest(target) != expected:
                raise ValueError('备份文件校验失败')
            if os.name != 'nt':
                os.chmod(target, 0o600)
        with closing(sqlite3.connect(str(staged / 'app.db'))) as database:
            if database.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('恢复数据库完整性检查失败')
        staged.rename(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    save = sub.add_parser('backup')
    save.add_argument('--data-dir', type=Path, required=True)
    save.add_argument('--output', type=Path, required=True)
    load = sub.add_parser('restore')
    load.add_argument('--archive', type=Path, required=True)
    load.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'backup':
            backup(args.data_dir, args.output)
        else:
            restore(args.archive, args.destination)
    except (ValueError, RuntimeError, OSError, zipfile.BadZipFile, KeyError):
        parser.exit(1, '操作失败：请检查目录、运行锁和归档完整性；未覆盖原数据。\n')
    print('操作完成。备份包含私人数据与加密主密钥，请妥善保管；环境变量密钥需另行备份。')


if __name__ == '__main__':
    main()
