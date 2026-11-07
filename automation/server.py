from __future__ import annotations
import argparse
import base64
import json
import logging
import secrets
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote

from .core import Store, UserError, dumps
from .samples import template, seed_demo
from . import flow, autocollect

ROOT=Path(__file__).resolve().parent
# OCR for image orders runs in the browser with a pinned tesseract.js build from jsDelivr.
OCR_CDN="https://cdn.jsdelivr.net"


def clean_name(value):
    name=Path(str(value or "")).name.strip()
    if not name or len(name)>180:raise UserError("자료 파일명을 확인하세요.")
    return name
LOG=logging.getLogger("chungmu")


def make_server(data_root, port=8765, flow_root=None, scheduler=None):
    live=Store(Path(data_root)/"live")
    demo=Store(Path(data_root)/"demo")
    token=secrets.token_urlsafe(32)
    flow_root=flow_root or flow.ROOT
    demo_lock=threading.Lock()
    data_root=Path(data_root)
    scheduler=scheduler or autocollect.Scheduler()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self,fmt,*args):
            LOG.info("%s %s",self.command,urlparse(self.path).path)

        def valid_host(self):
            return self.headers.get("Host") in [f"127.0.0.1:{self.server.server_port}",f"localhost:{self.server.server_port}"]

        def reply(self,code,body,content_type="application/json; charset=utf-8",filename=None):
            if isinstance(body,(dict,list)):body=dumps(body).encode("utf-8")
            if isinstance(body,str):body=body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type",content_type)
            self.send_header("Content-Length",str(len(body)))
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Cache-Control","no-store")
            self.send_header("Referrer-Policy","no-referrer")
            self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self' "+OCR_CDN+" 'wasm-unsafe-eval'; worker-src 'self' blob: "+OCR_CDN+"; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self' "+OCR_CDN+"; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if filename:self.send_header("Content-Disposition","attachment; filename*=UTF-8''"+quote(filename))
            self.end_headers()
            self.wfile.write(body)

        def store(self):
            query=parse_qs(urlparse(self.path).query)
            mode=query.get("workspace",[self.headers.get("X-Workspace","live")])[0]
            if mode not in ["live","demo"]:raise UserError("작업 공간을 확인하세요.")
            return demo if mode=="demo" else live

        def do_GET(self):
            try:
                if not self.valid_host():return self.reply(403,{"error":"로컬 주소로 접속하세요."})
                path=urlparse(self.path).path
                if path=="/":
                    html=(ROOT/"static/index.html").read_text(encoding="utf-8").replace("__TOKEN__",token)
                    return self.reply(200,html,"text/html; charset=utf-8")
                if path in ["/app.js","/extract.js","/styles.css"]:
                    return self.reply(200,(ROOT/"static"/path[1:]).read_bytes(),"text/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8")
                if path=="/favicon.ico":return self.reply(204,b"","image/x-icon")
                if path=="/api/state":return self.reply(200,self.store().dashboard())
                if path=="/api/flow/status":return self.reply(200,flow.status(flow_root))
                if path=="/api/auto":return self.reply(200,autocollect.overview(data_root,scheduler))
                if path.startswith("/api/orders/"):
                    return self.reply(200,self.store().detail(path.rsplit("/",1)[1]))
                if path.startswith("/api/files/"):
                    fid=path.rsplit("/",1)[1]
                    if fid.startswith("A-"):
                        file,name,mime=self.store().attachment_file(fid)
                        return self.reply(200,file.read_bytes(),mime,name)
                    file,name=self.store().artifact_file(fid)
                    return self.reply(200,file.read_bytes(),"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",name)
                if path.startswith("/api/templates/"):
                    blob,name=template(path.rsplit("/",1)[1])
                    return self.reply(200,blob,"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",name)
                return self.reply(404,{"error":"주소를 찾을 수 없습니다."})
            except (UserError,ValueError) as exc:self.reply(400,{"error":str(exc)})
            except Exception:
                LOG.exception("GET failed")
                self.reply(500,{"error":"처리 중 오류가 발생했습니다. 서버 로그를 확인하세요."})

        def do_POST(self):
            try:
                if not self.valid_host() or self.headers.get("X-Local-Token")!=token:
                    return self.reply(403,{"error":"화면을 새로고침한 뒤 다시 시도하세요."})
                origin=self.headers.get("Origin")
                if origin and origin not in [f"http://127.0.0.1:{self.server.server_port}",f"http://localhost:{self.server.server_port}"]:
                    return self.reply(403,{"error":"허용되지 않은 요청입니다."})
                if self.headers.get_content_type()!="application/json":raise UserError("JSON 요청이 필요합니다.")
                length=int(self.headers.get("Content-Length","0"))
                if not 0<length<=29*1024*1024:raise UserError("파일 용량을 확인하세요. 최대 20MB입니다.")
                body=json.loads(self.rfile.read(length))
                path=urlparse(self.path).path;store=self.store()
                def fileblob():
                    if not body.get("name","").lower().endswith(".xlsx"):raise UserError("XLSX 파일만 지원합니다.")
                    try:return base64.b64decode(body["file"],validate=True)
                    except Exception as exc:raise UserError("파일 내용을 읽지 못했습니다.") from exc
                if path=="/api/import-orders":
                    result=store.import_orders(fileblob(),body["name"],body.get("account",""),body.get("day",""),body.get("period",""))
                elif path=="/api/import-extracted":
                    result=store.import_extracted(body.get("headers"),body.get("rows"),clean_name(body.get("name")),body.get("account",""),body.get("day",""),body.get("period",""),body.get("source"))
                elif path=="/api/settings":result=store.save_settings(body)
                elif path=="/api/order":result=store.update_order(body["id"],body)
                elif path=="/api/generate":result=store.generate(body["batch_id"],body.get("note",""))
                elif path=="/api/review-batch":result=store.review_batch(body["batch_id"],body.get("note",""))
                elif path=="/api/import-tracking":result=store.import_tracking(fileblob(),body["name"],body["batch_id"])
                elif path=="/api/link-tracking":result=store.link_tracking(body["row_id"],body["shipment_id"],body.get("note",""))
                elif path=="/api/export-tracking":result=store.export_tracking(body["batch_id"],body.get("note",""))
                elif path=="/api/demo":
                    with demo_lock:seed_demo(demo)
                    result={"ok":True,"workspace":"demo"}
                elif path=="/api/flow/test":result=flow.FlowClient(flow_root).test()
                elif path=="/api/flow/post":
                    workspace=self.headers.get("X-Workspace","live")
                    title,content=flow.compose(store.dashboard(),workspace)
                    if body.get("preview"):result={"title":title,"content":content}
                    else:
                        if not body.get("confirm"):raise UserError("보내기 전에 내용을 확인하세요.")
                        result=flow.FlowClient(flow_root).post(body.get("project_id"),title,content)
                        LOG.info("Flow 게시 완료 (%s, 프로젝트 %s)",workspace,body.get("project_id"))
                        result["title"]=title
                elif path.startswith("/api/auto/"):
                    if self.headers.get("X-Workspace","live")!="live":raise UserError("자동 수집은 실제 업무 공간에서만 사용할 수 있습니다.")
                    if path=="/api/auto/settings":result=autocollect.save_settings(data_root,body,scheduler)
                    elif path=="/api/auto/run":result=autocollect.start_background(data_root,"manual")
                    elif path=="/api/auto/approve":
                        if not body.get("confirm"):raise UserError("게시 전에 초안 내용을 확인하세요.")
                        result=autocollect.post_draft(data_root,body.get("run_id"),flow_root);LOG.info("자동 수집 초안 게시 (%s)",body.get("run_id"))
                    elif path=="/api/auto/sync":result={"ok":True,"synced":autocollect.sync_cloud(data_root,live)}
                    elif path=="/api/auto/discard":result=autocollect.discard(data_root,body.get("run_id"))
                    else:return self.reply(404,{"error":"지원하지 않는 작업입니다."})
                elif path=="/api/backup":result={"path":str(store.backup()),"ok":True}
                elif path=="/api/stop":
                    self.reply(200,{"ok":True})
                    threading.Thread(target=self.server.shutdown,daemon=True).start()
                    return
                else:return self.reply(404,{"error":"지원하지 않는 작업입니다."})
                self.reply(200,result)
            except (UserError,ValueError,KeyError,TypeError) as exc:self.reply(400,{"error":str(exc)})
            except Exception:
                LOG.exception("POST failed")
                self.reply(500,{"error":"저장하지 못했습니다. 화면을 새로고침하고 처리 결과를 확인하세요."})

    return ThreadingHTTPServer(("127.0.0.1",port),Handler)


def main():
    parser=argparse.ArgumentParser(description="충무 주문 자동화 로컬 서버")
    parser.add_argument("--port",type=int,default=8765)
    parser.add_argument("--data-dir",default=str(ROOT.parent/"업무데이터"))
    args=parser.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    live=Store(Path(args.data_dir)/"live")
    stamp=datetime.now().strftime('%Y%m%d')
    with live.db() as db:
        has_orders=db.execute('SELECT 1 FROM orders LIMIT 1').fetchone() is not None
    if has_orders and not list((live.root/'backups').glob('충무업무백업_'+stamp+'*.zip')):
        LOG.info('운영일 첫 실행 백업: %s',live.backup())
    server=make_server(args.data_dir,args.port)
    print(f"충무 업무 자동화: http://127.0.0.1:{server.server_port}",flush=True)
    print("실제 데이터와 데모 데이터는 별도 저장됩니다. 종료: Ctrl+C",flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=="__main__":main()
