"""자동 수집 실행기. Windows 작업 스케줄러(대시보드에서 등록)나 대시보드 '지금 실행'이 호출한다.

    python -m automation.autorun run [--trigger schedule|manual] [--run-id ID] [--data-dir 경로]
    python -m automation.autorun sync   # 클라우드 결과 가져오기
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

from . import autocollect
from .core import Store, UserError


def main(argv=None):
    parser=argparse.ArgumentParser(description="충무 자동 수집")
    sub=parser.add_subparsers(dest="command",required=True)
    run=sub.add_parser("run")
    run.add_argument("--trigger",default="manual",choices=["manual","schedule"])
    run.add_argument("--run-id")
    run.add_argument("--data-dir",default=str(autocollect.ROOT/"업무데이터"))
    sync=sub.add_parser("sync",help="클라우드가 Drive에 남긴 메일 주문 가져오기")
    sync.add_argument("--data-dir",default=str(autocollect.ROOT/"업무데이터"))
    args=parser.parse_args(argv)
    data_root=Path(args.data_dir)
    if args.command=="sync":
        count=autocollect.sync_cloud(data_root,Store(data_root/"live"))
        print(f"클라우드 결과 {count}건을 가져왔습니다.");return 0
    if args.command=="run":
        try:
            if args.run_id:path=autocollect._run_dir(data_root,args.run_id);run_id=args.run_id
            else:
                settings=autocollect.load_settings(data_root)
                if args.trigger=="schedule" and not settings["enabled"]:return 0  # 꺼진 뒤 남은 예약은 조용히 끝낸다
                run_id,path=autocollect.new_run(data_root,args.trigger)
        except UserError as exc:
            print(exc,file=sys.stderr);return 1
        status=autocollect.execute(data_root,Store(data_root/"live"),run_id,path,autocollect.run_claude)
        print(f"{run_id}: {autocollect.STATUS_LABEL.get(status.get('status'),status.get('status'))} {status.get('error','')}")
        return 0 if status.get("status")!="failed" else 2
    return 1


if __name__=="__main__":sys.exit(main())
