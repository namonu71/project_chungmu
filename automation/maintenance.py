"""Explicit backup and restore into a new, empty directory."""
import argparse
import sqlite3
import zipfile
from pathlib import Path
from .core import Store, UserError


def restore(archive,destination):
    destination=Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise UserError('복구 대상은 새 폴더 또는 빈 폴더여야 합니다. 기존 데이터는 덮어쓰지 않습니다.')
    with zipfile.ZipFile(archive) as z:
        names=z.namelist()
        if 'operations.sqlite3' not in names:raise UserError('업무 DB가 없는 백업입니다.')
        for info in z.infolist():
            target=(destination/info.filename).resolve()
            allowed=info.filename=='operations.sqlite3' or (info.filename.startswith(('originals/','artifacts/')) and info.filename.endswith('.xlsx'))
            if not target.is_relative_to(destination) or not allowed or (info.external_attr>>16)&0o170000==0o120000:
                raise UserError('지원하지 않는 백업 경로입니다.')
        destination.mkdir(parents=True,exist_ok=True)
        z.extractall(destination)
    # Rewrite machine-specific absolute paths while retaining immutable artifact IDs.
    store=Store(destination)
    with store.db() as db:
        for row in list(db.execute('SELECT id,path FROM artifacts')):
            db.execute('UPDATE artifacts SET path=? WHERE id=?',(str(destination/'artifacts'/(row['id']+'.xlsx')),row['id']))
        result=db.execute('PRAGMA integrity_check').fetchone()[0]
        if result!='ok':raise UserError('복구 DB 검사에 실패했습니다: '+result)
    return store


def main():
    parser=argparse.ArgumentParser(description='충무 업무 데이터 백업·복구')
    sub=parser.add_subparsers(dest='action',required=True)
    backup=sub.add_parser('backup');backup.add_argument('data_directory')
    recovery=sub.add_parser('restore');recovery.add_argument('archive');recovery.add_argument('destination')
    args=parser.parse_args()
    if args.action=='backup':print(Store(args.data_directory).backup())
    else:
        store=restore(args.archive,args.destination)
        print('복구 완료:',store.root)


if __name__=='__main__':main()
