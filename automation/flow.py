"""Flow(flow.team) 연동: 연결 테스트와 프로젝트 게시글 등록.

공식 문서(https://api.flow.team/docs) 기준: 인증은 x-flow-api-key 헤더, 응답은 {"response":{success,data,error}} 형태.

API 키는 서버 안에서만 사용한다. 화면·로그·응답에는 키를 내보내지 않는다.
키 위치: 프로젝트 폴더의 `.env`(없으면 `.env.txt`) 또는 환경변수 FLOW_API_KEY.
"""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from .core import UserError

ROOT=Path(__file__).resolve().parent.parent
ENV_FILES=[".env",".env.txt"]
DEFAULT_BASE="https://api.flow.team"
DEFAULT_PROJECT_NAME="충무타올"
MAX_LISTED=50
MAX_PAGES=6


def read_env(root=ROOT):
    """(값 사전, 읽은 파일 이름). 환경변수가 파일보다 우선한다."""
    values={};source=None
    for name in ENV_FILES:
        path=Path(root)/name
        if not path.is_file():continue
        for line in path.read_text(encoding="utf-8-sig",errors="replace").splitlines():
            line=line.strip()
            if not line or line.startswith("#") or "=" not in line:continue
            key,_,value=line.partition("=")
            key=key.strip().removeprefix("export ").strip();value=value.strip()
            if len(value)>=2 and value[0]==value[-1] and value[0] in "\"'":value=value[1:-1]
            values.setdefault(key,value)
        source=source or name
    for key in ["FLOW_API_KEY","FLOW_API_BASE","FLOW_REGISTER_ID","FLOW_PROJECT_NAME","FLOW_API_MODE"]:
        if os.environ.get(key):values[key]=os.environ[key];source=source or "환경변수"
    return values,source


def status(root=ROOT):
    values,source=read_env(root)
    return {"configured":bool(values.get("FLOW_API_KEY")),"source":source,"base":values.get("FLOW_API_BASE") or DEFAULT_BASE,"register_id":bool(values.get("FLOW_REGISTER_ID")),"project_name":values.get("FLOW_PROJECT_NAME") or DEFAULT_PROJECT_NAME}


def unwrap(data):
    """공식 응답은 {"response":{...}}로 한 번 감싸여 있다."""
    if isinstance(data,dict) and isinstance(data.get("response"),dict):data=data["response"]
    return data if isinstance(data,dict) else {}


def _projects(data):
    rows=[]
    for item in (data or [])[:500]:
        if isinstance(item,dict) and item.get("projectId") not in [None,""]:
            rows.append({"id":str(item["projectId"]),"name":str(item.get("title") or "(이름 없음)"),"url":str(item.get("projectUrl") or "")})
    return rows


class FlowClient:
    def __init__(self,root=ROOT,timeout=15):
        values,_=read_env(root)
        self.key=values.get("FLOW_API_KEY","")
        if not self.key:raise UserError("Flow API 키가 없습니다. 프로젝트 폴더의 .env(또는 .env.txt)에 FLOW_API_KEY=... 를 넣고 서버를 다시 시작하세요.")
        self.base=(values.get("FLOW_API_BASE") or DEFAULT_BASE).rstrip("/")
        if not self.base.startswith("https://") and not self.base.startswith("http://127.0.0.1"):
            raise UserError("Flow 주소는 https로 시작해야 합니다.")
        self.register_id=values.get("FLOW_REGISTER_ID","")
        self.mode=(values.get("FLOW_API_MODE") or "auto").lower()
        self.project_name=values.get("FLOW_PROJECT_NAME") or DEFAULT_PROJECT_NAME
        self.timeout=timeout

    def call(self,method,path,body=None):
        data=json.dumps(body).encode("utf-8") if body is not None else None
        request=urllib.request.Request(self.base+path,data=data,method=method,headers={"x-flow-api-key":self.key,"Content-Type":"application/json","Accept":"application/json"})
        try:
            with urllib.request.urlopen(request,timeout=self.timeout) as response:
                raw=response.read(2*1024*1024)
        except urllib.error.HTTPError as exc:
            detail=""
            try:
                error=unwrap(json.loads(exc.read(2000))).get("error") or {}
                detail=str(error.get("message") or error.get("code") or "")
            except Exception:pass
            detail=detail.replace(self.key,"***")[:200]
            if exc.code==401:raise UserError("Flow가 API 키를 거부했습니다(401). %s 키가 맞는지, 삭제·만료되지 않았는지 확인하세요."%detail) from None
            if exc.code==403:raise UserError("Flow 접근이 거부됐습니다(403). %s 플랜·권한을 확인하세요."%detail) from None
            raise UserError("Flow 요청이 거부됐습니다(%d). %s"%(exc.code,detail)) from None
        except (urllib.error.URLError,TimeoutError,OSError):
            raise UserError("Flow 서버에 연결하지 못했습니다. 인터넷 연결과 주소를 확인하세요.") from None
        try:body=unwrap(json.loads(raw)) if raw.strip() else {}
        except ValueError:raise UserError("Flow 응답을 읽지 못했습니다.") from None
        if body.get("success") is False:
            error=body.get("error") or {}
            raise UserError("Flow가 요청을 처리하지 못했습니다: %s"%str(error.get("message") or body.get("message") or "")[:200])
        return body.get("data") or {}

    # 키 종류에 따라 경로가 다르다: 개인 키(내 API 키)는 /user/…, 이용기관 관리자 키는 /v1/…
    def projects(self):
        errors=[]
        for prefix in ([self.mode] if self.mode in ["user","v1"] else ["user","v1"]):
            try:
                rows=self.projects_from(prefix)
                self.mode=prefix
                return rows
            except UserError as exc:errors.append(exc)
        raise errors[0]

    def projects_from(self,prefix):
        rows=[];cursor=0
        base="/user/projects" if prefix=="user" else "/v1/projects"
        for _ in range(MAX_PAGES):
            data=self.call("GET","%s?cursor=%s"%(base,cursor))
            rows+=_projects(data.get("projects"))
            if not data.get("hasNext") or data.get("lastCursor") in [None,cursor]:break
            cursor=data["lastCursor"]
        return rows

    def test(self):
        rows=self.projects()
        target=next((p for p in rows if self.project_name and self.project_name in p["name"]),None)
        return {"ok":True,"projects":rows[:MAX_LISTED],"project_count":len(rows),"target":target,"target_name":self.project_name,"key_type":"개인용 키" if self.mode=="user" else "이용기관 관리자 키"}

    def post(self,project_id,title,content):
        project_id=str(project_id or "").strip()
        if not project_id.isdigit():raise UserError("게시할 Flow 프로젝트를 선택하세요.")
        if self.mode not in ["user","v1"]:self.projects()  # 키 종류 판별
        body={"title":title[:200],"contents":content[:10000]}
        if self.mode=="user":path="/user/posts/projects/"+project_id
        else:
            if not self.register_id:raise UserError("작성자 ID가 없습니다. .env에 FLOW_REGISTER_ID=작성자 이메일(Flow 로그인 ID)을 넣으세요.")
            path="/v1/posts/projects/"+project_id;body["registerId"]=self.register_id
        self.call("POST",path,body)
        return {"ok":True}


def is_hold(order):
    return bool(order.get("pending_change") or order.get("manual_hold") or (not order.get("shipment") and order.get("issues")))


def compose(state,workspace="live",now=None):
    """개인정보(수취인·연락처·주소) 없이 주문번호와 사유만 담은 현황 메시지."""
    now=now or datetime.now()
    orders=state["orders"]
    hold=[o for o in orders if is_hold(o)]
    ready=[o for o in orders if o.get("stage")==1 and not is_hold(o)]
    done=[o for o in orders if o.get("status")=="처리 완료"]
    label="가상 데이터(데모)" if workspace=="demo" else "실제 업무"
    stamp=now.strftime("%Y-%m-%d %H:%M")
    lines=[f"[충무 주문 자동화 · {label}] {stamp} 현황",f"전체 {len(orders)}건 · 확인 필요 {len(hold)}건 · 생성 준비 {len(ready)}건 · 처리 완료 {len(done)}건"]
    if hold:
        lines.append("")
        lines.append("확인 필요한 주문")
        for o in hold[:MAX_LISTED]:lines.append(f"- {o['number']}: {o.get('next_action') or '확인 필요'}")
        if len(hold)>MAX_LISTED:lines.append(f"- 외 {len(hold)-MAX_LISTED}건")
    return f"충무 주문 현황 {stamp}","\n".join(lines)
