import copy
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from openpyxl import load_workbook
from openpyxl import Workbook
from automation.core import Store, UserError, workbook_bytes, read_xlsx
from automation.samples import demo_settings, demo_orders, ORDER_HEADERS, seed_demo


class OperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(self.tmp.name)
        self.store.save_settings(demo_settings())

    def tearDown(self):self.tmp.cleanup()

    def blob(self,number='ORD-001',quantity=3,**changes):
        values={'사업자':'데모사업자','판매처':'스마트스토어','주문번호':number,'주문행번호':'L1','상품코드':'DEMO-200-W','상품명':'호텔타올','옵션':'화이트','수량':quantity,'수취인':'가상고객','연락처':'01000000000','주소':'대전 샘플로 1','메시지':'','주문상태':'정상'}
        values.update(changes)
        return workbook_bytes(ORDER_HEADERS,[[values[h] for h in ORDER_HEADERS]],'주문입력')

    def enter(self,blob=None,period='오전'):
        return self.store.import_orders(blob or self.blob(),'주문.xlsx','데모사업자','2026-11-06',period)['batch_id']

    def first(self):return self.store.dashboard()['orders'][0]

    def review(self,oid=None):self.store.update_order(oid or self.first()['id'],{'action':'review','note':'테스트 검수'})

    def generate(self,bid=None):
        bid=bid or self.enter();self.review();return bid,self.store.generate(bid,'대상/제외 확인')

    def test_end_to_end_and_stages(self):
        bid=self.enter();self.assertEqual(self.first()['stage'],0)
        self.review();self.assertEqual(self.first()['stage'],1)
        result=self.store.generate(bid,'대조');self.assertEqual(len(result['artifacts']),4)
        o=self.first();self.assertEqual(o['stage'],2)
        self.store.update_order(o['id'],{'action':'accept','note':'접수 확인'})
        tracking=workbook_bytes(['배송ID','택배사','송장번호'],[[o['shipment']['id'],'CJ대한통운','990000000001']])
        self.store.import_tracking(tracking,'송장.xlsx',bid)
        self.assertEqual(self.first()['stage'],4)
        with self.assertRaises(UserError):self.store.update_order(o['id'],{'action':'reflect','note':'파일 없음'})
        result=self.store.export_tracking(bid,'검수');self.assertIn('artifact_id',result)
        self.store.update_order(o['id'],{'action':'reflect','note':'실제 반영 확인'})
        self.assertEqual(self.first()['stage'],5)
        self.assertNotEqual(self.first()['status'],'처리 완료')
        self.store.update_order(o['id'],{'action':'ship','note':'실제 출고 확인'})
        self.assertEqual(self.first()['status'],'처리 완료')

    def test_generated_values_and_formula_injection(self):
        bid=self.enter(self.blob(**{'수취인':'+가상고객'}))
        self.review();result=self.store.generate(bid,'대조')
        state=self.store.dashboard();summary=next(a for a in state['artifacts'] if a['kind']=='summary')
        path,_=self.store.artifact_file(summary['id']);w=load_workbook(path,data_only=True)
        self.assertEqual(w.active['D2'].value,3)
        review=next(a for a in state['artifacts'] if a['kind']=='review')
        path,_=self.store.artifact_file(review['id']);w=load_workbook(path,data_only=False)
        self.assertEqual(w.active['K2'].data_type,'s');self.assertTrue(w.active['K2'].value.startswith("'"))

    def test_duplicate_file_and_overlapping_batch(self):
        blob=self.blob();bid=self.enter(blob);self.review();self.store.generate(bid,'확인')
        self.assertTrue(self.store.import_orders(blob,'same.xlsx','데모사업자','2026-11-06','오후')['duplicate'])
        # Byte-distinct file with same logical order (extra unused column).
        rows,_=read_xlsx(blob);alternate=workbook_bytes(ORDER_HEADERS+['메모'],[['데모사업자','스마트스토어','ORD-001','L1','DEMO-200-W','호텔타올','화이트',3,'가상고객','01000000000','대전 샘플로 1','','정상','중첩']])
        b2=self.enter(alternate,'오후')
        self.assertNotEqual(bid,b2);self.assertEqual(len(self.store.dashboard()['orders']),1)
        with self.assertRaises(UserError):self.store.generate(b2,'중복 차단')
        with self.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM shipments').fetchone()[0],1)
        self.assertTrue(self.store.generate(bid,'재시도')['reused'])

    def test_hold_and_missing_mapping(self):
        self.enter(self.blob(**{'상품코드':'UNKNOWN','주소':''}));o=self.first()
        self.assertTrue(any('미매핑' in s for s in o['issues']))
        self.assertTrue(any('주소 누락' in s for s in o['issues']))
        with self.assertRaises(UserError):self.review()

    def test_processing_and_over_capacity(self):
        self.enter(self.blob(quantity=30,**{'메시지':'자수 부탁'}));o=self.first()
        self.assertTrue(any('가공 의심' in s for s in o['issues']))
        self.assertTrue(any('최대 수량' in s for s in o['issues']))

    def test_whole_order_held_when_one_line_invalid(self):
        normal=['데모사업자','스마트스토어','MIX','L1','DEMO-200-W','호텔타올','화이트',2,'샘플','01000000000','대전 샘플로 1','','정상']
        bad=normal.copy();bad[3]='L2';bad[4]='UNKNOWN'
        bid=self.enter(workbook_bytes(ORDER_HEADERS,[normal,bad]));self.assertEqual(len(self.store.dashboard()['orders']),1)
        with self.assertRaises(UserError):self.store.generate(bid,'확인')

    def test_correction_and_review(self):
        self.enter(self.blob(**{'주소':''}));o=self.first()
        self.store.update_order(o['id'],{'action':'edit','address':'대전 수정로 2','note':'고객 확인'})
        self.assertEqual(self.first()['issues'],[]);self.review()
        self.assertEqual(self.first()['stage'],1)
        s=self.store.settings();s['mappings'][0]['max_units']=10;self.store.save_settings(s)
        self.assertEqual(self.first()['stage'],0)

    def test_change_after_export_requires_resolution(self):
        bid,_=self.generate();o=self.first()
        self.enter(self.blob(quantity=4),'오후');self.assertTrue(self.first()['pending_change'])
        with self.assertRaises(UserError):self.store.update_order(o['id'],{'action':'edit','note':'직접 변경'})
        with self.assertRaises(UserError):self.store.update_order(o['id'],{'action':'resolve_change','note':'미확인'})
        self.store.update_order(o['id'],{'action':'resolve_change','note':'외부 접수 취소 확인','external_resolved':True})
        self.assertEqual(self.first()['revision'],2);self.assertIsNone(self.first()['shipment'])
        self.assertEqual(self.first()['units'],4)
        self.review();self.store.generate(bid,'변경분만 재생성')
        with self.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM shipments WHERE active=1').fetchone()[0],1)

    def test_tracking_without_identifier_needs_manual_link(self):
        bid,_=self.generate();o=self.first()
        result=self.store.import_tracking(workbook_bytes(['수취인','송장번호'],[['가상고객','990000000002']]),'송장.xlsx',bid)
        self.assertEqual(result['linked'],0)
        rid=self.store.dashboard()['tracking_rows'][0]['id']
        self.store.link_tracking(rid,o['shipment']['id'],'원본·수취정보 직접 대조')
        self.assertEqual(self.first()['shipment']['tracking'],'990000000002')

    def test_duplicate_tracking_rows_are_all_blocked(self):
        bid,_=self.generate();o=self.first()
        result=self.store.import_tracking(workbook_bytes(['주문번호','송장번호'],[['ORD-001','990000000003'],['ORD-001','990000000003']]),'송장.xlsx',bid)
        self.assertEqual(result['linked'],0);self.assertIsNone(self.first()['shipment']['tracking'])
        for r in self.store.dashboard()['tracking_rows']:
            with self.assertRaises(UserError):self.store.link_tracking(r['id'],o['shipment']['id'],'중복 시도')

    def test_multiple_tracking_for_one_shipment_all_blocked(self):
        bid,_=self.generate()
        r=self.store.import_tracking(workbook_bytes(['주문번호','송장번호'],[['ORD-001','990000000004'],['ORD-001','990000000005']]),'송장.xlsx',bid)
        self.assertEqual(r['linked'],0)

    def test_tracking_reexport_is_idempotent(self):
        bid,_=self.generate();o=self.first()
        blob=workbook_bytes(['배송ID','송장번호'],[[o['shipment']['id'],'990000000006']])
        self.store.import_tracking(blob,'송장.xlsx',bid)
        self.assertTrue(self.store.import_tracking(blob,'다른이름.xlsx',bid)['duplicate'])
        a=self.store.export_tracking(bid,'확인');b=self.store.export_tracking(bid,'재시도')
        self.assertEqual(a['artifact_id'],b['artifact_id']);self.assertTrue(b['reused'])

    def test_formula_input_and_wrong_account_rejected(self):
        w=Workbook();w.active.append(ORDER_HEADERS)
        w.active.append(['데모사업자','스마트스토어','FORMULA','L1','DEMO-200-W','호텔타올','화이트','=1+1','가상고객','01000000000','대전 샘플로 1','','정상'])
        stream=io.BytesIO();w.save(stream)
        with self.assertRaises(UserError):self.enter(stream.getvalue())
        with self.assertRaises(UserError):self.enter(self.blob(**{'사업자':'다른사업자'}))
        with self.assertRaises(UserError):read_xlsx(b'not-an-xlsx')

    def test_carrier_grouped_header_and_identifier_precision(self):
        w=Workbook();sheet=w.active
        sheet.append(['운송장번호','수하인','수하인','수하인','주문번호'])
        sheet.append(['운송장번호','이름','주소','전화','주문번호'])
        sheet.append(['990000000111','가상수취인','대전 샘플로','01000000000','ORD-001'])
        stream=io.BytesIO();w.save(stream)
        records,metadata=read_xlsx(stream.getvalue(),'tracking')
        self.assertEqual(metadata['header_row'],2)
        self.assertEqual(records[0]['recipient'],'가상수취인')
        self.assertEqual(records[0]['phone'],'01000000000')
        with self.assertRaises(UserError):self.enter(self.blob(number=123456789012345678))

    def test_persistence_and_backup(self):
        bid,_=self.generate();other=Store(self.tmp.name)
        self.assertEqual(len(other.dashboard()['orders']),1)
        backup=self.store.backup()
        with zipfile.ZipFile(backup) as z:
            self.assertIn('operations.sqlite3',z.namelist())
            self.assertTrue(any(n.startswith('artifacts/') for n in z.namelist()))

    def test_full_demo(self):
        with tempfile.TemporaryDirectory() as d:
            s=Store(d);seed_demo(s);state=s.dashboard()
            self.assertEqual(len(state['orders']),12)
            self.assertEqual(len(state['artifacts']),5)
            self.assertEqual(sum(o['status']=='처리 완료' for o in state['orders']),1)
            seed_demo(s);self.assertEqual(len(s.dashboard()['orders']),12)

    def test_batch_review_excludes_holds_and_is_idempotent(self):
        bid=self.enter(demo_orders())
        result=self.store.review_batch(bid,'재고·상품·배송 대조')
        self.assertEqual(result['reviewed'],8);self.assertEqual(result['excluded'],4)
        self.assertEqual(self.store.review_batch(bid,'재확인')['reviewed'],0)
        result=self.store.generate(bid,'대상8건 제외4건 확인')
        self.assertEqual(result['generated'],8);self.assertEqual(result['holds'],4)

    def test_conflicting_reference_and_order_number_not_autolinked(self):
        bid,_=self.generate();o=self.first()
        r=self.store.import_tracking(workbook_bytes(['배송ID','주문번호','송장번호'],[[o['shipment']['id'],'DIFFERENT','990000000123']]),'송장.xlsx',bid)
        self.assertEqual(r['linked'],0)

    def test_exported_rules_are_frozen(self):
        bid,_=self.generate();o=self.first()
        settings=self.store.settings();settings['profile']['carrier']='로젠택배'
        settings['mappings'][0]['multiplier']=5
        self.store.save_settings(settings)
        self.store.import_tracking(workbook_bytes(['배송ID','송장번호'],[[o['shipment']['id'],'990000000007']]),'송장.xlsx',bid)
        result=self.store.export_tracking(bid,'확인')
        path,_=self.store.artifact_file(result['artifact_id']);w=load_workbook(path,data_only=True)
        self.assertEqual(w.active['D2'].value,'CJ대한통운')
        self.assertEqual(self.first()['shipment']['carrier'],'CJ대한통운')
        self.assertEqual(self.first()['units'],3)
        self.assertEqual(self.first()['issues'],[])

    def test_same_order_number_on_two_channels_is_ambiguous(self):
        first=['데모사업자','스마트스토어','SAME','L1','DEMO-200-W','호텔타올','화이트',2,'샘플','01000000000','대전 샘플로 1','','정상']
        second=first.copy();second[1]='쿠팡'
        bid=self.enter(workbook_bytes(ORDER_HEADERS,[first,second]))
        for o in self.store.dashboard()['orders']:self.review(o['id'])
        self.store.generate(bid,'확인')
        r=self.store.import_tracking(workbook_bytes(['주문번호','송장번호'],[['SAME','990000000008']]),'송장.xlsx',bid)
        self.assertEqual(r['linked'],0)

    def test_shipment_tracking_cannot_be_used_for_two_orders(self):
        bid,_=self.generate();o=self.first()
        self.store.import_tracking(workbook_bytes(['주문번호','송장번호'],[['ORD-001','990000000009']]),'송장.xlsx',bid)
        b2=self.enter(self.blob(number='ORD-002'));self.review();self.store.generate(b2,'확인')
        r=self.store.import_tracking(workbook_bytes(['주문번호','송장번호'],[['ORD-002','990000000009']]),'송장.xlsx',b2)
        self.assertEqual(r['linked'],0)

    def test_wrong_carrier_and_manual_hold_block_tracking(self):
        bid,_=self.generate();o=self.first()
        self.store.update_order(o['id'],{'action':'hold','note':'출고 보류'})
        r=self.store.import_tracking(workbook_bytes(['배송ID','송장번호'],[[o['shipment']['id'],'990000000010']]),'송장.xlsx',bid)
        self.assertEqual(r['linked'],0)
        rid=self.store.dashboard()['tracking_rows'][0]['id']
        with self.assertRaises(UserError):self.store.link_tracking(rid,o['shipment']['id'],'강제 연결')

    def test_restore_rewrites_paths_and_refuses_overwrite(self):
        from automation.maintenance import restore
        bid,_=self.generate();backup=self.store.backup()
        with tempfile.TemporaryDirectory() as parent:
            restored=restore(backup,Path(parent)/'restored')
            self.assertEqual(len(restored.dashboard()['orders']),1)
            aid=restored.dashboard()['artifacts'][0]['id']
            self.assertTrue(restored.artifact_file(aid)[0].is_relative_to(restored.root))
            with self.assertRaises(UserError):restore(backup,restored.root)


if __name__=='__main__':unittest.main()
