import json
import tempfile
import unittest
import urllib.request
import threading
from datetime import datetime
from pathlib import Path

from automation import autocollect
from automation.core import Store, UserError
from automation.server import make_server


class FakeScheduler:
    def __init__(self):self.applied=[]
    def apply(self,settings):self.applied.append(settings);return {"registered":[{"name":"x","time":t} for t in settings["times"]] if settings["enabled"] else [],"message":"ok"}
    def status(self,settings):return {"registered":[],"in_sync":not settings["enabled"]}


class FakeFlowClient:
    def __init__(self,fail=False):self.posts=[];self.fail=fail
    def post(self,project_id,title,content):
        if self.fail:raise UserError("작성자 ID가 없습니다.")
        self.posts.append((project_id,title,content));return {"ok":True}


SETTINGS={"enabled":True,"times":["14:00","09:30"],"weekdays_only":True,"rooms":[{"room":"OO상사 주문방","account":"acc1"}],"gmail":{"query":"label:주문","account":"acc2"},"max_messages":50,"flow":{"mode":"approve","project_id":"123","project_name":"충무타올"}}
ORDER={"판매처":"스마트스토어","주문번호":"K-1","상품코드":"SKU1","수량":"2","수취인":"홍길동","연락처":"010-1111-2222","주소":"서울시 어딘가 1"}
COLLECTED={"sources":[
    {"type":"kakao","name":"OO상사 주문방","status":"ok","message_count":12,"orders":[ORDER],"skipped":[{"reason":"주문번호 없음","summary":"수건 3박스 문의"}]},
    {"type":"kakao","name":"없는방","status":"not_found","suggestions":["없는 방"],"orders":[]},
    {"type":"gmail","name":"발주서","status":"ok","message_count":1,"orders":[]}]}


class AutoCollectTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/"live")

    def tearDown(self):self.tmp.cleanup()

    def test_validate_settings(self):
        s=autocollect.validate_settings(SETTINGS)
        self.assertEqual(s["times"],["09:30","14:00"])
        for bad in [{**SETTINGS,"times":["25:00"]},{**SETTINGS,"rooms":[{"room":"a","account":""}]},{**SETTINGS,"gmail":{"query":"x","account":""}},
                    {**SETTINGS,"times":[]},{**SETTINGS,"flow":{"mode":"auto","project_id":""}},{**SETTINGS,"rooms":[],"gmail":{"query":"","account":""}}]:
            with self.assertRaises(UserError):autocollect.validate_settings(bad)
        self.assertFalse(autocollect.validate_settings({})["enabled"])

    def test_save_and_load(self):
        sched=FakeScheduler()
        autocollect.save_settings(self.root,SETTINGS,sched)
        self.assertEqual(autocollect.load_settings(self.root)["rooms"][0]["account"],"acc1")
        self.assertEqual(len(sched.applied),1)

    def test_prompt_mentions_inputs_and_read_only(self):
        p=autocollect.build_prompt(autocollect.validate_settings(SETTINGS),"2026-11-07T09:30",datetime(2026,11,7,14,0))
        self.assertIn("OO상사 주문방",p);self.assertIn("label:주문",p);self.assertIn("2026-11-07T09:30",p);self.assertIn("보내거나",p)

    def test_extract_json(self):
        self.assertEqual(autocollect.extract_json('설명\n```json\n{"sources":[]}\n```')["sources"],[])
        with self.assertRaises(ValueError):autocollect.extract_json("없음")

    def test_execute_imports_and_drafts_without_pii(self):
        autocollect.save_settings(self.root,SETTINGS,FakeScheduler())
        run_id,path=autocollect.new_run(self.root,"manual")
        status=autocollect.execute(self.root,self.store,run_id,path,lambda prompt,log:COLLECTED,now=datetime(2026,11,7,14,5))
        self.assertEqual(status["status"],"draft")
        kakao=status["results"][0];self.assertEqual(kakao["orders"],1);self.assertTrue(kakao["batch_id"])
        self.assertEqual(status["results"][1]["status"],"not_found")
        batch=self.store.dashboard()["batches"][0];self.assertEqual((batch["account"],batch["period"],batch["day"]),("acc1","오후","2026-11-07"))
        content=status["draft"]["content"]
        for pii in ["홍길동","010-1111-2222","서울시"]:self.assertNotIn(pii,content)
        self.assertIn("수건 3박스 문의",content)
        client=FakeFlowClient();autocollect.post_draft(self.root,run_id,client=client)
        self.assertEqual(client.posts[0][0],"123");self.assertEqual(autocollect.read_status(path)["status"],"posted")
        with self.assertRaises(UserError):autocollect.post_draft(self.root,run_id,client=client)

    def test_auto_mode_posts_and_failure_keeps_draft(self):
        autocollect.save_settings(self.root,{**SETTINGS,"flow":{"mode":"auto","project_id":"123"}},FakeScheduler())
        client=FakeFlowClient()
        run_id,path=autocollect.new_run(self.root,"schedule")
        self.assertEqual(autocollect.execute(self.root,self.store,run_id,path,lambda p,l:COLLECTED,lambda:client)["status"],"posted")
        bad=FakeFlowClient(fail=True)
        run_id,path=autocollect.new_run(self.root,"schedule")
        status=autocollect.execute(self.root,self.store,run_id,path,lambda p,l:COLLECTED,lambda:bad)
        self.assertEqual(status["status"],"draft");self.assertIn("자동 게시 실패",status["post_error"])

    def test_failure_is_recorded_and_lock(self):
        autocollect.save_settings(self.root,SETTINGS,FakeScheduler())
        run_id,path=autocollect.new_run(self.root,"manual")
        with self.assertRaises(UserError):autocollect.new_run(self.root,"manual")
        def boom(p,l):raise UserError("카카오톡이 잠겨 있습니다.")
        status=autocollect.execute(self.root,self.store,run_id,path,boom)
        self.assertEqual(status["status"],"failed");self.assertIn("잠겨",status["error"])
        autocollect.new_run(self.root,"manual")  # 실패 후 다시 실행 가능

    def test_discard_and_bad_ids(self):
        with self.assertRaises(UserError):autocollect.discard(self.root,"../x")
        autocollect.save_settings(self.root,SETTINGS,FakeScheduler())
        run_id,path=autocollect.new_run(self.root,"manual")
        autocollect.execute(self.root,self.store,run_id,path,lambda p,l:COLLECTED)
        autocollect.discard(self.root,run_id);self.assertEqual(autocollect.read_status(path)["status"],"discarded")

    def cloud_settings(self,**flow):
        bridge=self.root/"drive"/"충무자동수집";bridge.parent.mkdir()
        return {**SETTINGS,"rooms":[],"cloud":{"enabled":True,"bridge_dir":str(bridge)},"flow":{**SETTINGS["flow"],**flow}},bridge

    def test_cloud_settings_written_to_drive_without_rooms(self):
        settings,bridge=self.cloud_settings()
        autocollect.save_settings(self.root,{**settings,"rooms":[{"room":"비밀방","account":"acc1"}]},FakeScheduler())
        data=json.loads((bridge/autocollect.CLOUD_SETTINGS_NAME).read_text(encoding="utf-8"))
        self.assertTrue(data["enabled"]);self.assertEqual(data["gmail"]["query"],"label:주문");self.assertNotIn("rooms",data)
        self.assertTrue((bridge/autocollect.CLOUD_RUNS_DIR/"처리됨").is_dir())
        with self.assertRaises(UserError):autocollect.validate_settings({**settings,"times":["21:00"]})
        with self.assertRaises(UserError):autocollect.validate_settings({**settings,"gmail":{"query":"","account":""},"rooms":[{"room":"a","account":"b"}]})
        with self.assertRaises(UserError):autocollect.validate_settings({**settings,"cloud":{"enabled":True,"bridge_dir":str(self.root/"없음"/"x")}})
        self.assertNotIn("label:주문",autocollect.build_prompt(autocollect.validate_settings({**settings,"rooms":[{"room":"a","account":"b"}]}),None))

    def test_sync_cloud_imports_mail_orders(self):
        settings,bridge=self.cloud_settings();autocollect.save_settings(self.root,settings,FakeScheduler())
        runs=bridge/autocollect.CLOUD_RUNS_DIR
        (runs/"cloud_20261107_1005.json").write_text(json.dumps({"run_at":"2026-11-07T10:05:00","sources":[{"type":"gmail","name":"발주서","status":"ok","message_count":1,"orders":[ORDER]}],"posted":False}),encoding="utf-8")
        (runs/"cloud_20261107_1105.json").write_text(json.dumps({"run_at":"2026-11-07T11:05:00","sources":[],"posted":True,"draft":{"title":"t","content":"c"}}),encoding="utf-8")
        (runs/"broken.json").write_text("{",encoding="utf-8")
        self.assertEqual(autocollect.sync_cloud(self.root,self.store),2)
        self.assertEqual(sorted(p.name for p in (runs/"처리됨").iterdir()),["cloud_20261107_1005.json","cloud_20261107_1105.json"])
        self.assertTrue((runs/"broken.json").exists())
        records={r["started"]:r for r in autocollect.list_runs(self.root)}
        first=records["2026-11-07T10:05:00"];self.assertEqual((first["status"],first["trigger"]),("draft","cloud"))
        self.assertEqual(first["results"][0]["account"],"acc2");self.assertNotIn("홍길동",first["draft"]["content"])
        self.assertEqual(records["2026-11-07T11:05:00"]["status"],"posted")
        self.assertEqual(self.store.dashboard()["batches"][0]["period"],"오전")
        self.assertEqual(autocollect.sync_cloud(self.root,self.store),0)

    def test_run_with_cloud_and_no_rooms_only_syncs(self):
        settings,bridge=self.cloud_settings();autocollect.save_settings(self.root,settings,FakeScheduler())
        run_id,path=autocollect.new_run(self.root,"schedule")
        def never(p,l):raise AssertionError("카카오 방이 없으면 Claude를 부르지 않는다")
        self.assertEqual(autocollect.execute(self.root,self.store,run_id,path,never)["status"],"imported")

    def test_scheduler_expected_counts(self):
        s=autocollect.Scheduler(self.root)
        self.assertEqual(s.expected(autocollect.validate_settings(SETTINGS)),2)
        settings,_=self.cloud_settings()
        self.assertEqual(s.expected(autocollect.validate_settings(settings)),1)
        self.assertEqual(s.expected(autocollect.validate_settings({**settings,"rooms":[{"room":"a","account":"b"}]})),3)

    def test_http_endpoints(self):
        sched=FakeScheduler();server=make_server(self.root,0,scheduler=sched)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        base=f"http://127.0.0.1:{server.server_port}"
        try:
            html=urllib.request.urlopen(base+"/").read().decode();token=html.split('name="local-token" content="')[1].split('"')[0]
            def post(path,body,ws="live"):
                req=urllib.request.Request(base+path,json.dumps(body).encode(),{"Content-Type":"application/json","X-Local-Token":token,"X-Workspace":ws})
                try:return 200,json.loads(urllib.request.urlopen(req).read())
                except urllib.error.HTTPError as e:return e.code,json.loads(e.read())
            self.assertEqual(post("/api/auto/settings",SETTINGS)[0],200)
            data=json.loads(urllib.request.urlopen(base+"/api/auto").read())
            self.assertEqual(data["settings"]["rooms"][0]["room"],"OO상사 주문방")
            self.assertEqual(post("/api/auto/settings",SETTINGS,"demo")[0],400)
            self.assertEqual(post("/api/auto/approve",{"run_id":"x"})[0],400)
        finally:server.shutdown();server.server_close()


if __name__=="__main__":unittest.main()
