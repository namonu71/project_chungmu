"""Reproducible, isolated 1,000-order benchmark. Never uses live data."""
import json
import tempfile
import time
from pathlib import Path
from .core import Store, workbook_bytes
from .samples import ORDER_HEADERS,demo_settings


def run():
    with tempfile.TemporaryDirectory() as folder:
        store=Store(folder);store.save_settings(demo_settings())
        rows=[['데모사업자','스마트스토어',f'PERF-{i:06d}',f'L{i}','DEMO-200-W','호텔타올','화이트',2,'가상고객','01000000000','대전 데모로 1','','정상'] for i in range(1000)]
        blob=workbook_bytes(ORDER_HEADERS,rows,'주문입력')
        t=time.perf_counter();bid=store.import_orders(blob,'perf.xlsx','데모사업자','2026-11-06','오전')['batch_id'];input_s=time.perf_counter()-t
        t=time.perf_counter();review=store.review_batch(bid,'가상 주문 1000건 수량·재고·배송정보 대조');review_s=time.perf_counter()-t
        t=time.perf_counter();generated=store.generate(bid,'대상 1000건 확인');output_s=time.perf_counter()-t
        t=time.perf_counter();state=store.dashboard();dashboard_s=time.perf_counter()-t
        assert len(state['orders'])==1000 and sum(o['units'] for o in state['orders'])==2000
        assert review['reviewed']==generated['generated']==1000
        result={'orders':1000,'units':2000,'import_seconds':round(input_s,3),'batch_review_seconds':round(review_s,3),'generate_4_files_seconds':round(output_s,3),'dashboard_seconds':round(dashboard_s,3),'sample':'가상 단일품목 1000주문','note':'이 PC의 실측 기록이며 운영 성능 보증값 아님'}
        (Path(__file__).parent/'performance_result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':run()
