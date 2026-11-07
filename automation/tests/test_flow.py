import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from automation import flow
from automation.server import make_server

KEY="dd9dcb4c-1f79-4eb8-9522-940fdefe6c1e"  # 관리자 키: /v1 만 허용
USER_KEY="202611070000-personal-key-example"  # 개인 키: /user 만 허용
RECEIVED=[]
PROJECTS=[{"projectId":"123000","title":"[문의] 고객문의","projectUrl":"https://flow.team/main.act?projectId=123000"},{"projectId":"123001","title":"충무타올 영업","projectUrl":"https://flow.team/main.act?projectId=123001"}]


class FakeFlow(BaseHTTPRequestHandler):
    """공식 문서의 응답 형식({"response":{...}})을 흉내 낸다."""
    def log_message(self,*a):pass
    def send(self,code,response):
        body=json.dumps({"response":response}).encode();self.send_response(code);self.send_header("Content-Type","application/json");self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
    def denied(self):
        key=self.headers.get("x-flow-api-key")
        if (key==KEY and self.path.startswith("/v1/")) or (key==USER_KEY and self.path.startswith("/user/")):return False
        self.send(401,{"success":False,"code":401,"message":"error","error":{"code":"UNAUTHORIZED_ERROR","message":"API Key 정보가 올바르지 않습니다."}});return True
    def do_GET(self):
        if self.denied():return
        if self.path.startswith("/v1/projects") or self.path.startswith("/user/projects"):
            cursor=self.path.partition("cursor=")[2] or "0"
            if cursor=="0":return self.send(200,{"success":True,"code":200,"message":"success","data":{"hasNext":True,"lastCursor":1,"projects":PROJECTS[:1]}})
            return self.send(200,{"success":True,"code":200,"message":"success","data":{"hasNext":False,"lastCursor":2,"projects":PROJECTS[1:]}})
        self.send(404,{"success":False,"error":{"code":"NOT_FOUND_ERROR","message":"없음"}})
    def do_POST(self):
        if self.denied():return
        RECEIVED.append((self.path,{k.lower():v for k,v in self.headers.items()},json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
        self.send(200,{"success":True,"code":200,"message":"success","data":{"postId":"1"}})


class FlowTests(unittest.TestCase):
    def setUp(self):
        RECEIVED.clear()
        self.fake=HTTPServer(("127.0.0.1",0),FakeFlow);threading.Thread(target=self.fake.serve_forever,daemon=True).start()
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.write_env(KEY)

    def write_env(self,key,name=".env.txt",extra=""):
        (self.root/name).write_text(f'# memo\nFLOW_API_KEY="{key}"\nFLOW_REGISTER_ID=me@example.com\nFLOW_API_BASE=http://127.0.0.1:{self.fake.server_port}\n{extra}',encoding="utf-8")

    def tearDown(self):
        self.fake.shutdown();self.fake.server_close();self.temp.cleanup()

    def test_env_files_are_read_and_status_hides_key(self):
        st=flow.status(self.root)
        self.assertTrue(st["configured"]);self.assertEqual(st["source"],".env.txt");self.assertNotIn(KEY,json.dumps(st))
        self.assertFalse(flow.status(Path(self.temp.name)/"none")["configured"])
        self.write_env("other-key",".env");self.assertEqual(flow.status(self.root)["source"],".env")

    def test_projects_paging_and_target(self):
        r=flow.FlowClient(self.root).test()
        self.assertEqual(r["project_count"],2);self.assertEqual(r["target"]["id"],"123001");self.assertEqual(r["target"]["name"],"충무타올 영업")

    def test_post_to_project_matches_official_format(self):
        flow.FlowClient(self.root).post("123001","제목","내용")
        path,headers,body=RECEIVED[0]
        self.assertEqual(path,"/v1/posts/projects/123001");self.assertEqual(headers["x-flow-api-key"],KEY)
        self.assertEqual(body,{"registerId":"me@example.com","title":"제목","contents":"내용"})

    def test_personal_key_uses_user_api_without_register_id(self):
        (self.root/".env.txt").write_text(f'FLOW_API_KEY={USER_KEY}\nFLOW_API_BASE=http://127.0.0.1:{self.fake.server_port}\n',encoding="utf-8")
        client=flow.FlowClient(self.root);r=client.test()
        self.assertEqual(r["key_type"],"개인용 키");self.assertEqual(r["target"]["id"],"123001")
        client.post("123001","제목","내용")
        path,headers,body=RECEIVED[0]
        self.assertEqual(path,"/user/posts/projects/123001");self.assertEqual(body,{"title":"제목","contents":"내용"})

    def test_post_requires_register_id_and_project(self):
        (self.root/".env.txt").write_text(f'FLOW_API_KEY={KEY}\nFLOW_API_BASE=http://127.0.0.1:{self.fake.server_port}\n',encoding="utf-8")
        with self.assertRaises(Exception) as caught:flow.FlowClient(self.root).post("123001","t","c")
        self.assertIn("FLOW_REGISTER_ID",str(caught.exception));self.assertEqual(RECEIVED,[])
        self.write_env(KEY)
        with self.assertRaises(Exception):flow.FlowClient(self.root).post("","t","c")

    def test_rejected_key_error_does_not_leak_key(self):
        self.write_env("wrong-key-XYZ")
        with self.assertRaises(Exception) as caught:flow.FlowClient(self.root).test()
        self.assertIn("401",str(caught.exception));self.assertNotIn("wrong-key-XYZ",str(caught.exception))
        (self.root/".env.txt").unlink()
        with self.assertRaises(Exception) as caught:flow.FlowClient(self.root)
        self.assertIn("FLOW_API_KEY",str(caught.exception))

    def test_message_has_no_personal_data(self):
        state={"orders":[{"number":"A-1","status":"보류","stage":0,"issues":["주소 누락"],"shipment":None,"next_action":"주소 누락","lines":[{"recipient":"홍길동","phone":"010-1234-5678"}]},{"number":"A-2","status":"검수 확정","stage":1,"issues":[],"shipment":None}]}
        title,text=flow.compose(state,"live")
        self.assertIn("A-1: 주소 누락",text);self.assertIn("확인 필요 1건",text)
        for secret in ["홍길동","010-1234-5678"]:self.assertNotIn(secret,text)

    def test_http_endpoints(self):
        server=make_server(self.temp.name+"/data",0,flow_root=self.root);threading.Thread(target=server.serve_forever,daemon=True).start()
        base="http://127.0.0.1:%d"%server.server_port
        try:
            token=re.search(r'name="local-token" content="([^"]+)"',urllib.request.urlopen(base).read().decode()).group(1)
            def call(path,body=None,tok=True):
                h={"Content-Type":"application/json","X-Workspace":"demo"}
                if tok:h["X-Local-Token"]=token
                return json.load(urllib.request.urlopen(urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=h)))
            st=call("/api/flow/status");self.assertTrue(st["configured"]);self.assertNotIn(KEY,json.dumps(st))
            self.assertEqual(call("/api/flow/test",{})["target"]["id"],"123001")
            call("/api/demo",{})
            pre=call("/api/flow/post",{"preview":True,"project_id":"123001"});self.assertIn("DEMO-0012",pre["content"]);self.assertEqual(RECEIVED,[])
            with self.assertRaises(urllib.error.HTTPError):call("/api/flow/post",{"project_id":"123001"})
            self.assertEqual(RECEIVED,[])
            call("/api/flow/post",{"project_id":"123001","confirm":True});self.assertEqual(len(RECEIVED),1)
            with self.assertRaises(urllib.error.HTTPError) as caught:call("/api/flow/test",{},tok=False)
            self.assertEqual(caught.exception.code,403)
        finally:
            server.shutdown();server.server_close()


if __name__=="__main__":unittest.main()
