"""자동 수집: 정해진 시각에 카카오톡·Gmail에서 주문을 가져와 주문 입력 → Flow 초안/게시.

역할 분담
- 대시보드: 설정 입력(방·메일 조건·시각·게시 방식), 지금 실행, 초안 승인·폐기.
- Windows 작업 스케줄러: 설정한 시각에 `자동수집_실행.cmd` 실행(이 PC, 로그인·화면 잠금 해제 상태 필요).
- Claude Code(`claude -p`): 카카오 MCP·Gmail 커넥터로 원문을 읽고 주문 행을 JSON으로 뽑는다. 읽기 도구만 허용한다.
- 이 모듈: 뽑은 행을 기존 주문 흐름(import_extracted)으로 입력하고, 개인정보 없는 Flow 초안을 만든다.
  검수 확정·출고파일 생성은 하지 않는다(사람이 대시보드에서 진행).

저장 위치: 업무데이터/자동수집/ (git 제외). 설정.json, runs/<실행ID>/status.json·collected.json·claude.log
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from .core import UserError
from . import flow

ROOT=Path(__file__).resolve().parent.parent
TASK_PREFIX="Chungmu_AutoCollect_"
SYNC_TASK=TASK_PREFIX+"Sync"
CLOUD_SETTINGS_NAME="충무자동수집_설정.txt"  # Drive 커넥터는 text/* 를 읽는다(내용은 JSON)
CLOUD_RUNS_DIR="클라우드실행"
CLOUD_HOURS=(6,19)  # 클라우드 작업은 KST 07:05~19:05 매시 깨어난다 → 06:00~19:00 시각만 처리 가능
LOCK_STALE=timedelta(minutes=40)
CLAUDE_TIMEOUT=20*60
MAX_ROOMS=20
MAX_TIMES=6
ORDER_FIELDS=["판매처","주문번호","주문행번호","상품코드","상품명","옵션","수량","수취인","연락처","주소","배송메시지"]
TRIGGER_LABEL={"schedule":"예약 실행","manual":"직접 실행","cloud":"클라우드 실행"}
STATUS_LABEL={"running":"실행 중","draft":"승인 대기","posted":"게시 완료","imported":"입력 완료","failed":"실패","discarded":"폐기"}
KAKAO_TOOLS=["mcp__kakao__kakao_health_check","mcp__kakao__kakao_read_messages","mcp__kakao__kakao_suggest_rooms"]
GMAIL_TOOLS=["mcp__claude_ai_Gmail"]  # 커넥터 인증 후 읽기 도구 이름이 확정되면 좁힐 것

DEFAULTS={"enabled":False,"times":[],"weekdays_only":True,"rooms":[],"gmail":{"query":"","account":""},"max_messages":100,"flow":{"mode":"approve","project_id":"","project_name":""},"cloud":{"enabled":False,"bridge_dir":""}}


def default_bridge():
    """Google Drive 데스크톱 앱의 내 드라이브 아래 '충무자동수집'. 계정이 여럿이면 폴더가 이미 있는 드라이브를 고른다."""
    roots=[Path(f"{d}:/{mine}") for d in "GHIJKLMNOPQRSTUVWXYZ" for mine in ["내 드라이브","My Drive"]]
    roots=[r for r in roots if r.is_dir()]
    for root in roots:
        if (root/"충무자동수집").is_dir():return str(root/"충무자동수집")
    return str(roots[0]/"충무자동수집") if roots else ""


def base_dir(data_root):
    path=Path(data_root)/"자동수집";(path/"runs").mkdir(parents=True,exist_ok=True);return path


# ---------- 설정 ----------
def load_settings(data_root):
    path=base_dir(data_root)/"설정.json"
    data=json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    merged=json.loads(json.dumps(DEFAULTS))
    for key,value in data.items():
        if isinstance(merged.get(key),dict) and isinstance(value,dict):merged[key].update(value)
        else:merged[key]=value
    return merged


def _text(value,limit,label):
    value=str(value or "").strip()
    if len(value)>limit:raise UserError(f"{label}은(는) {limit}자 이하로 입력하세요.")
    return value


def validate_settings(body):
    if not isinstance(body,dict):raise UserError("자동 수집 설정 형식을 확인하세요.")
    times=[]
    for t in body.get("times") or []:
        t=str(t).strip()
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d",t):raise UserError(f"실행 시각 '{t}'을(를) HH:MM 형식으로 입력하세요.")
        if t not in times:times.append(t)
    if len(times)>MAX_TIMES:raise UserError(f"실행 시각은 {MAX_TIMES}개까지 등록할 수 있습니다.")
    rooms=[];seen=set()
    for r in body.get("rooms") or []:
        name=_text(r.get("room"),100,"카카오톡 방 이름");account=_text(r.get("account"),100,"사업자")
        if not name and not account:continue
        if not name:raise UserError("카카오톡 방 이름을 입력하세요.")
        if not account:raise UserError(f"'{name}' 방의 사업자를 입력하세요. 주문을 어느 사업자로 입력할지 정해야 합니다.")
        if name in seen:raise UserError(f"'{name}' 방이 중복됐습니다.")
        seen.add(name);rooms.append({"room":name,"account":account})
    if len(rooms)>MAX_ROOMS:raise UserError(f"카카오톡 방은 {MAX_ROOMS}개까지 등록할 수 있습니다.")
    gmail=body.get("gmail") or {}
    query=_text(gmail.get("query"),300,"메일 검색 조건");gaccount=_text(gmail.get("account"),100,"메일 사업자")
    if query and not gaccount:raise UserError("메일로 받은 주문을 입력할 사업자를 입력하세요.")
    try:max_messages=int(body.get("max_messages") or 100)
    except (TypeError,ValueError):raise UserError("방별 최대 메시지 수는 숫자로 입력하세요.")
    if not 10<=max_messages<=500:raise UserError("방별 최대 메시지 수는 10~500 사이로 입력하세요.")
    f=body.get("flow") or {}
    mode=f.get("mode") or "approve"
    if mode not in ["approve","auto"]:raise UserError("Flow 게시 방식을 선택하세요.")
    project_id=str(f.get("project_id") or "").strip()
    if project_id and not project_id.isdigit():raise UserError("Flow 프로젝트를 다시 선택하세요.")
    if mode=="auto" and not project_id:raise UserError("자동 게시하려면 Flow 프로젝트를 선택하세요.")
    enabled=bool(body.get("enabled"))
    if enabled and not times:raise UserError("자동 실행을 켜려면 실행 시각을 하나 이상 입력하세요.")
    if enabled and not rooms and not query:raise UserError("자동 실행을 켜려면 카카오톡 방이나 메일 검색 조건을 하나 이상 입력하세요.")
    c=body.get("cloud") or {}
    cloud={"enabled":bool(c.get("enabled")),"bridge_dir":_text(c.get("bridge_dir"),300,"Drive 연동 폴더")}
    if cloud["enabled"]:
        if not query:raise UserError("클라우드 실행은 메일 수집용입니다. 메일 검색 조건을 입력하세요.")
        if not cloud["bridge_dir"] or not Path(cloud["bridge_dir"]).parent.is_dir():raise UserError("Google Drive 연동 폴더를 찾지 못했습니다. Google Drive 데스크톱 앱이 켜져 있는지 확인하고 경로를 입력하세요.")
        late=[t for t in times if not f"{CLOUD_HOURS[0]:02d}:00"<=t<=f"{CLOUD_HOURS[1]:02d}:00"]
        if late:raise UserError(f"클라우드 실행 시각은 {CLOUD_HOURS[0]:02d}:00~{CLOUD_HOURS[1]:02d}:00 사이여야 합니다: {', '.join(late)}")
    return {"cloud":cloud,"enabled":enabled,"times":sorted(times),"weekdays_only":bool(body.get("weekdays_only",True)),"rooms":rooms,"gmail":{"query":query,"account":gaccount},"max_messages":max_messages,"flow":{"mode":mode,"project_id":project_id,"project_name":_text(f.get("project_name"),200,"프로젝트 이름")}}


def save_settings(data_root,body,scheduler=None):
    settings=validate_settings(body)
    path=base_dir(data_root)/"설정.json"
    path.write_text(json.dumps(settings,ensure_ascii=False,indent=2),encoding="utf-8")
    cloud=write_cloud_settings(settings)
    schedule=(scheduler or Scheduler()).apply(settings)
    if cloud:schedule["message"]+=" 클라우드 설정도 Google Drive에 저장했습니다."
    return {"ok":True,"settings":settings,"schedule":schedule}


def write_cloud_settings(settings):
    """클라우드 작업이 읽을 설정(카카오 방 제외)을 Drive 연동 폴더에 쓴다. 클라우드를 끄면 enabled=false로 남긴다."""
    bridge=settings["cloud"]["bridge_dir"]
    if not bridge or not Path(bridge).parent.is_dir():return False
    folder=Path(bridge);(folder/CLOUD_RUNS_DIR/"처리됨").mkdir(parents=True,exist_ok=True)
    data={"enabled":bool(settings["enabled"] and settings["cloud"]["enabled"]),"times":settings["times"],"weekdays_only":settings["weekdays_only"],"timezone":"Asia/Seoul",
          "gmail":settings["gmail"],"flow":settings["flow"],"updated":datetime.now().isoformat(timespec="seconds")}
    (folder/CLOUD_SETTINGS_NAME).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    return True


# ---------- 작업 스케줄러 ----------
class Scheduler:
    """Windows 작업 스케줄러에 현재 사용자 작업을 등록한다(로그온 상태에서만 실행).
    - Chungmu_AutoCollect_N: 설정 시각에 수집. PC가 꺼져 있어 놓치면 켜진 뒤 바로 실행(StartWhenAvailable).
    - Chungmu_AutoCollect_Sync: 로그온할 때 클라우드가 Drive에 남긴 메일 주문을 가져온다(클라우드 사용 시)."""
    def __init__(self,root=ROOT):
        self.root=Path(root)

    def _run(self,args):
        if os.name!="nt":return subprocess.CompletedProcess(args,1,"","Windows가 아닙니다.")
        flags=getattr(subprocess,"CREATE_NO_WINDOW",0)
        result=subprocess.run(args,capture_output=True,creationflags=flags)
        decode=lambda b:b.decode("cp949",errors="replace") if b else ""
        return subprocess.CompletedProcess(args,result.returncode,decode(result.stdout),decode(result.stderr))

    def _ps(self,script):
        encoded=base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return self._run(["powershell","-NoProfile","-NonInteractive","-EncodedCommand",encoded])

    def tasks(self):
        out=self._run(["schtasks","/Query","/FO","CSV","/NH"])
        names=set()
        for line in out.stdout.splitlines():
            name=line.split('","')[0].strip('"').lstrip("\\")
            if name.startswith(TASK_PREFIX):names.add(name)
        return sorted(names)

    def python(self):
        scripts=self.root/".venv"/"Scripts"
        for name in ["pythonw.exe","python.exe"]:
            if (scripts/name).is_file():return scripts/name
        raise UserError("프로젝트 가상환경(.venv)을 찾지 못했습니다. 실행.cmd로 한 번 실행해 준비하세요.")

    @staticmethod
    def _q(value):
        return "'"+str(value).replace("'","''")+"'"

    def _register(self,name,trigger,arguments):
        script=(f"$a=New-ScheduledTaskAction -Execute {self._q(self.python())} -Argument {self._q(arguments)} -WorkingDirectory {self._q(self.root)};"
                f"$t={trigger};"
                "$s=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 40) -MultipleInstances IgnoreNew;"
                f"Register-ScheduledTask -TaskName {self._q(name)} -Action $a -Trigger $t -Settings $s -Description '충무 자동 수집(대시보드에서 관리)' -Force | Out-Null")
        r=self._ps(script)
        if r.returncode!=0:raise UserError(f"작업 스케줄러 등록 실패({name}): {(r.stderr or r.stdout).strip()[:200]}")

    def apply(self,settings):
        for name in self.tasks():self._run(["schtasks","/Delete","/TN",name,"/F"])
        if not settings["enabled"]:return {"registered":[],"message":"자동 실행이 꺼져 있습니다. 등록된 예약을 모두 지웠습니다."}
        registered=[];cloud=settings.get("cloud",{}).get("enabled")
        local_needed=bool(settings["rooms"]) or not cloud  # 클라우드가 메일을 맡으면 PC는 카카오톡만
        if local_needed:
            for index,t in enumerate(settings["times"],1):
                trig=f"New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At {self._q(t)}" if settings["weekdays_only"] else f"New-ScheduledTaskTrigger -Daily -At {self._q(t)}"
                self._register(f"{TASK_PREFIX}{index}",trig,"-m automation.autorun run --trigger schedule");registered.append({"name":f"{TASK_PREFIX}{index}","time":t})
        if cloud:
            self._register(SYNC_TASK,"New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME","-m automation.autorun sync");registered.append({"name":SYNC_TASK,"time":"로그온"})
        days="평일" if settings["weekdays_only"] else "매일"
        parts=[]
        if local_needed:parts.append(f"이 PC: {days} {', '.join(settings['times'])} (꺼져 있었으면 켜진 뒤 바로 실행)")
        if cloud:parts.append("클라우드: 메일 수집 · 로그온 때 결과 가져오기")
        return {"registered":registered,"message":"예약을 등록했습니다. "+" / ".join(parts)}

    def expected(self,settings):
        if not settings["enabled"]:return 0
        cloud=settings.get("cloud",{}).get("enabled")
        return (len(settings["times"]) if settings["rooms"] or not cloud else 0)+(1 if cloud else 0)

    def status(self,settings):
        names=self.tasks()
        return {"registered":names,"in_sync":len(names)==self.expected(settings)}


# ---------- 실행 기록 ----------
def _run_dir(data_root,run_id):
    if not re.fullmatch(r"R\d{8}-\d{6}-[0-9a-f]{4}",str(run_id or "")):raise UserError("실행 기록을 찾지 못했습니다.")
    path=base_dir(data_root)/"runs"/run_id
    if not path.is_dir():raise UserError("실행 기록을 찾지 못했습니다.")
    return path


def read_status(path):
    try:return json.loads((Path(path)/"status.json").read_text(encoding="utf-8"))
    except (OSError,ValueError):return None


def write_status(path,status):
    status["updated"]=datetime.now().isoformat(timespec="seconds")
    tmp=Path(path)/"status.json.tmp";tmp.write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding="utf-8");tmp.replace(Path(path)/"status.json")


def list_runs(data_root,limit=30):
    rows=[]
    for path in sorted((base_dir(data_root)/"runs").iterdir(),reverse=True)[:limit]:
        status=read_status(path)
        if status:
            if status.get("status")=="running" and _stale(status.get("started")):status["status"]="failed";status["error"]="실행이 중간에 멈췄습니다(시간 초과 또는 PC 종료)."
            status["status_label"]=STATUS_LABEL.get(status.get("status"),status.get("status"));rows.append(status)
    return rows


def _stale(started):
    try:return datetime.now()-datetime.fromisoformat(started)>LOCK_STALE
    except (TypeError,ValueError):return True


def running_run(data_root):
    for run in list_runs(data_root,5):
        if run.get("status")=="running":return run
    return None


def last_success(data_root):
    for run in list_runs(data_root,60):
        if run.get("status") in ["draft","posted","imported"]:return run.get("started")
    return None


def new_run(data_root,trigger):
    if running_run(data_root):raise UserError("이미 자동 수집이 실행 중입니다. 끝난 뒤 다시 시도하세요.")
    run_id=datetime.now().strftime("R%Y%m%d-%H%M%S-")+uuid.uuid4().hex[:4]
    path=base_dir(data_root)/"runs"/run_id;path.mkdir(parents=True)
    write_status(path,{"id":run_id,"trigger":trigger,"status":"running","step":"준비","started":datetime.now().isoformat(timespec="seconds")})
    return run_id,path


def start_background(data_root,trigger="manual"):
    """대시보드 '지금 실행': 별도 프로세스로 autorun을 띄우고 바로 돌아온다."""
    run_id,_=new_run(data_root,trigger)
    flags=getattr(subprocess,"CREATE_NO_WINDOW",0)|getattr(subprocess,"DETACHED_PROCESS",0)
    subprocess.Popen([sys.executable,"-m","automation.autorun","run","--trigger",trigger,"--run-id",run_id,"--data-dir",str(data_root)],cwd=str(ROOT),creationflags=flags,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,close_fds=True)
    return {"ok":True,"run_id":run_id}


# ---------- Claude로 원문 읽기·주문 추출 ----------
def find_claude():
    found=shutil.which("claude")
    if found:return found
    for candidate in [Path(os.environ.get("APPDATA",""))/"npm"/"claude.cmd",Path.home()/".local/bin/claude.exe"]:
        if candidate.is_file():return str(candidate)
    return None


def build_prompt(settings,since,now=None):
    now=now or datetime.now()
    since_text=since or (now-timedelta(days=1)).isoformat(timespec="minutes")
    rooms="\n".join(f"- {r['room']}" for r in settings["rooms"]) or "- (없음)"
    gmail="" if settings.get("cloud",{}).get("enabled") else settings["gmail"]["query"]
    schema={"sources":[{"type":"kakao 또는 gmail","name":"방 이름 또는 메일 제목","status":"ok | not_found | error","error":"실패 사유(한국어)","suggestions":["비슷한 방 이름"],"message_count":0,"orders":[{k:"" for k in ORDER_FIELDS}],"skipped":[{"reason":"주문번호 없음 등","summary":"개인정보 없이 한 줄 요약"}],"notes":"주문이 아닌 내용 한두 줄 요약(개인정보 제외)"}]}
    return f"""충무 주문 자동 수집 작업이다. 지금은 {now:%Y-%m-%d %H:%M}이다. 읽기만 하고, 어떤 메시지·메일도 보내거나 수정하지 마라.

1. kakao_health_check를 호출한다. ready가 아니면 카카오 출처 전부를 status "error", error에 guide 내용을 넣고 2단계는 건너뛴다.
2. 아래 카카오톡 방마다 kakao_read_messages(room_name=방 이름 그대로, max_messages={settings['max_messages']})를 호출한다.
   방 이름이 맞지 않아 suggestions가 오면 다른 방을 대신 읽지 말고 status "not_found"와 suggestions를 기록한다.
{rooms}
3. Gmail 검색 조건: {gmail or '(없음 — 건너뜀)'}
   조건이 있으면 이 조건에 {since_text} 이후 받은 메일만 검색해 본문을 읽는다(첨부 표가 본문에 있으면 포함). 메일 하나가 출처 하나다.
4. {since_text} 이후의 메시지·메일에서 주문만 골라 orders에 한 품목당 한 행으로 적는다. 원문에 없는 값은 지어내지 말고 빈 칸으로 둔다.
   판매처나 주문번호가 원문에 없으면 orders에 넣지 말고 skipped에 사유와 요약을 적는다. 수량은 숫자만 적는다.
5. 마지막 응답은 아래 형식의 JSON 하나만 출력한다(설명·코드블록 표시 없이).
{json.dumps(schema,ensure_ascii=False)}
"""


def extract_json(text):
    text=(text or "").strip()
    fence=re.search(r"```(?:json)?\s*(\{.*\})\s*```",text,re.S)
    if fence:text=fence.group(1)
    start=text.find("{");end=text.rfind("}")
    if start<0 or end<start:raise ValueError("Claude 응답에서 JSON을 찾지 못했습니다.")
    data=json.loads(text[start:end+1])
    if not isinstance(data,dict) or not isinstance(data.get("sources"),list):raise ValueError("Claude 응답 형식이 다릅니다(sources 없음).")
    return data


def run_claude(prompt,log_path,claude=None,timeout=CLAUDE_TIMEOUT):
    claude=claude or find_claude()
    if not claude:raise UserError("Claude Code(claude 명령)를 찾지 못했습니다. 이 PC에 Claude Code를 설치하고 로그인하세요.")
    tools=KAKAO_TOOLS+GMAIL_TOOLS
    args=[claude,"-p","--output-format","json","--allowedTools",",".join(tools)]
    flags=getattr(subprocess,"CREATE_NO_WINDOW",0)
    result=subprocess.run(args,input=prompt.encode("utf-8"),capture_output=True,timeout=timeout,cwd=str(ROOT),creationflags=flags)
    out=result.stdout.decode("utf-8",errors="replace");err=result.stderr.decode("utf-8",errors="replace")
    Path(log_path).write_text(f"exit={result.returncode}\n--- stdout ---\n{out}\n--- stderr ---\n{err}",encoding="utf-8")
    try:envelope=json.loads(out)
    except ValueError:raise UserError(f"Claude 실행 결과를 읽지 못했습니다(종료 코드 {result.returncode}). claude.log를 확인하세요.")
    if envelope.get("is_error"):raise UserError("Claude 실행 오류: "+str(envelope.get("result") or envelope.get("subtype"))[:300])
    return extract_json(envelope.get("result",""))


# ---------- 주문 입력·초안 ----------
def period_for(now):
    return "오전" if now.hour<12 else "오후"


def _rows(orders):
    rows=[]
    for o in orders or []:
        if not isinstance(o,dict):continue
        row=[str(o.get(k) or "").strip()[:500] for k in ORDER_FIELDS]
        if any(row):rows.append(row)
    return rows


def ingest(store,settings,collected,now=None):
    """수집 결과를 출처별 배치로 입력한다. 반환: 출처별 결과 목록(개인정보 없음)."""
    now=now or datetime.now();day=now.strftime("%Y-%m-%d");period=period_for(now)
    accounts={r["room"]:r["account"] for r in settings["rooms"]}
    results=[]
    for src in collected.get("sources",[])[:100]:
        kind="gmail" if src.get("type")=="gmail" else "kakao"
        name=str(src.get("name") or "")[:120]
        item={"type":kind,"name":name,"status":src.get("status") or "ok","error":str(src.get("error") or "")[:300],"suggestions":[str(s)[:100] for s in (src.get("suggestions") or [])][:5],"messages":src.get("message_count") or 0,"orders":0,"skipped":[{"reason":str(s.get("reason") or "")[:100],"summary":str(s.get("summary") or "")[:200]} for s in (src.get("skipped") or []) if isinstance(s,dict)][:30],"notes":str(src.get("notes") or "")[:300]}
        rows=_rows(src.get("orders"))
        account=settings["gmail"]["account"] if kind=="gmail" else accounts.get(name)
        if rows and item["status"]=="ok":
            if not account:item.update(status="error",error="이 출처의 사업자가 설정에 없습니다.")
            else:
                label="카카오" if kind=="kakao" else "메일"
                try:
                    r=store.import_extracted(ORDER_FIELDS,rows,f"자동수집_{label}_{name}_{now:%Y%m%d_%H%M}.txt"[:180],account,day,period)
                    item.update(batch_id=r["batch_id"],duplicate=r.get("duplicate",False),orders=r.get("orders",0),account=account)
                except UserError as exc:item.update(status="error",error=f"주문 입력 실패: {exc}")
        results.append(item)
    return results


def compose_draft(store,results,now=None):
    now=now or datetime.now();stamp=now.strftime("%Y-%m-%d %H:%M")
    imported=sum(r["orders"] for r in results if not r.get("duplicate"))
    lines=[f"[충무 자동 수집] {stamp} ({period_for(now)})",f"출처 {len(results)}곳 · 새로 입력한 주문 {imported}건",""]
    for r in results:
        label="카카오톡" if r["type"]=="kakao" else "메일"
        if r["status"]!="ok":lines.append(f"- {label} {r['name']}: 확인 필요 — {r['error'] or ('방 이름 불일치' if r['status']=='not_found' else r['status'])}")
        elif r.get("batch_id"):lines.append(f"- {label} {r['name']}: 주문 {r['orders']}건 입력{' (이미 입력된 내용)' if r.get('duplicate') else ''} · 배치 {r['batch_id']}")
        else:lines.append(f"- {label} {r['name']}: 새 주문 없음")
        for s in r["skipped"][:5]:lines.append(f"    · 입력 못 함({s['reason']}): {s['summary']}")
    lines+=["","검수 확정·출고파일 생성은 대시보드에서 담당자가 진행합니다.",""]
    _,status_text=flow.compose(store.dashboard(),"live",now)
    lines.append(status_text)
    return f"충무 자동 수집 {stamp}","\n".join(lines)[:9500]


def post_draft(data_root,run_id,flow_root=None,client=None,settings=None):
    path=_run_dir(data_root,run_id);status=read_status(path)
    if not status or status.get("status")!="draft":raise UserError("승인 대기 중인 초안만 게시할 수 있습니다.")
    settings=settings or load_settings(data_root)
    project_id=settings["flow"]["project_id"]
    if not project_id:raise UserError("자동 수집 설정에서 게시할 Flow 프로젝트를 먼저 선택하세요.")
    client=client or flow.FlowClient(flow_root or flow.ROOT)
    client.post(project_id,status["draft"]["title"],status["draft"]["content"])
    status.update(status="posted",posted=datetime.now().isoformat(timespec="seconds"),project_id=project_id,post_error="")
    write_status(path,status)
    return {"ok":True,"run_id":run_id}


def discard(data_root,run_id):
    path=_run_dir(data_root,run_id);status=read_status(path)
    if not status or status.get("status")!="draft":raise UserError("승인 대기 중인 초안만 폐기할 수 있습니다.")
    status["status"]="discarded";write_status(path,status)
    return {"ok":True}


def execute(data_root,store,run_id,path,collect,flow_client_factory=None,now=None):
    """한 번의 실행. collect(prompt, log_path) -> 수집 JSON. 실패해도 status.json에 사유를 남긴다."""
    status=read_status(path);settings=load_settings(data_root);now=now or datetime.now()
    try:
        status.update(step="클라우드 결과 가져오기");write_status(path,status)
        synced=sync_cloud(data_root,store,settings)
        cloud=settings["cloud"]["enabled"]
        if not settings["rooms"] and (cloud or not settings["gmail"]["query"]):
            status.update(status="imported",step="완료",note=f"이 PC에서 읽을 카카오톡 방이 없습니다. 클라우드 결과 {synced}건을 가져왔습니다.");write_status(path,status);return read_status(path)
        status.update(step="원문 읽기·주문 추출");write_status(path,status)
        collected=collect(build_prompt(settings,last_success(data_root),now),path/"claude.log")
        (path/"collected.json").write_text(json.dumps(collected,ensure_ascii=False,indent=2),encoding="utf-8")
        status.update(step="주문 입력");write_status(path,status)
        results=ingest(store,settings,collected,now)
        title,content=compose_draft(store,results,now)
        status.update(results=results,draft={"title":title,"content":content},status="draft",step="완료")
        write_status(path,status)
        if settings["flow"]["mode"]=="auto":
            try:post_draft(data_root,run_id,client=flow_client_factory() if flow_client_factory else None,settings=settings)
            except UserError as exc:
                status=read_status(path);status["post_error"]=f"자동 게시 실패(초안으로 남김): {exc}";write_status(path,status)
    except Exception as exc:  # noqa: BLE001 - 어떤 실패든 기록을 남긴다
        status=read_status(path) or status
        status.update(status="failed",error=str(exc)[:500] or exc.__class__.__name__);write_status(path,status)
    return read_status(path)


def sync_cloud(data_root,store,settings=None):
    """클라우드 작업이 Drive 연동 폴더에 남긴 결과(메일 주문)를 주문 시스템에 넣고 실행 기록으로 남긴다.
    처리한 파일은 '처리됨'으로 옮긴다. 반환: 가져온 파일 수."""
    settings=settings or load_settings(data_root)
    bridge=settings["cloud"]["bridge_dir"]
    if not bridge:return 0
    folder=Path(bridge)/CLOUD_RUNS_DIR
    if not folder.is_dir():return 0
    done=folder/"처리됨";done.mkdir(exist_ok=True);count=0
    for file in sorted(folder.glob("*.json")):
        try:data=json.loads(file.read_text(encoding="utf-8-sig"))
        except (OSError,ValueError):continue  # 아직 동기화 중일 수 있다 → 다음에 다시
        if not isinstance(data,dict) or not isinstance(data.get("sources"),list):continue
        try:at=datetime.fromisoformat(str(data.get("run_at"))[:19])
        except ValueError:at=datetime.now()
        run_id=at.strftime("R%Y%m%d-%H%M%S-")+uuid.uuid4().hex[:4]
        path=base_dir(data_root)/"runs"/run_id;path.mkdir(parents=True)
        status={"id":run_id,"trigger":"cloud","started":at.isoformat(timespec="seconds"),"source_file":file.name}
        try:
            for src in data["sources"]:src["type"]="gmail"
            results=ingest(store,settings,data,at)
            if data.get("posted"):
                draft=data.get("draft") or {"title":"","content":""}
                status.update(status="posted",posted=data.get("posted_at") or at.isoformat(timespec="seconds"),project_id=settings["flow"]["project_id"])
            else:
                title,content=compose_draft(store,results,at);draft={"title":title,"content":content}
                status.update(status="draft",post_error=str(data.get("post_error") or "")[:300])
            status.update(results=results,draft=draft,step="완료",error=str(data.get("error") or "")[:300])
        except Exception as exc:  # noqa: BLE001
            status.update(status="failed",error=f"클라우드 결과를 가져오지 못했습니다: {exc}"[:500])
        write_status(path,status)
        try:file.replace(done/file.name)
        except OSError:pass
        count+=1
    return count


def overview(data_root,scheduler=None):
    settings=load_settings(data_root);scheduler=scheduler or Scheduler()
    try:schedule=scheduler.status(settings)
    except Exception as exc:schedule={"registered":[],"in_sync":False,"error":str(exc)[:200]}
    runs=list_runs(data_root)
    bridge=settings["cloud"]["bridge_dir"]
    pending=len(list((Path(bridge)/CLOUD_RUNS_DIR).glob("*.json"))) if bridge and (Path(bridge)/CLOUD_RUNS_DIR).is_dir() else 0
    cloud={"bridge_found":bool(bridge) and Path(bridge).parent.is_dir(),"pending":pending,"default_bridge":default_bridge()}
    return {"cloud":cloud,"settings":settings,"schedule":schedule,"runs":runs,"running":any(r.get("status")=="running" for r in runs),"claude":bool(find_claude()),"flow":flow.status()}
